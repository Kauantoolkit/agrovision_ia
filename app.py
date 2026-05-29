import os
from dotenv import load_dotenv
load_dotenv()
import cv2
import time
import uuid
import json
import asyncio
import sqlite3
import threading
import requests
import numpy as np
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime
from collections import defaultdict, Counter
from typing import List, Union

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from ultralytics import YOLO
import scraper

# =========================
# CONFIGURAÇÕES
# =========================
CAMERA_SOURCE = 0
# Se quiser testar com vídeo depois, troque por:
# CAMERA_SOURCE = "teste.mp4"

MODEL_PATH = "yolo11n.pt"
CONFIDENCE_THRESHOLD = 0.45
SAVE_DIR = "static/captures"
DB_PATH = "detections.db"

TARGET_CLASSES = {"person", "car", "motorcycle", "truck", "bus"}

MIN_CONSECUTIVE_FRAMES = 3
ALERT_COOLDOWN_SECONDS = 20

# =========================
# OLLAMA
# =========================
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
MODEL_NAME = os.getenv("OLLAMA_MODEL", "llama3.2:latest")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")

# "idle" → "warming" → "ready" | "failed"
warmup_status = "idle"

# =========================
# WINDY WEBCAMS
# =========================
WINDY_API_KEY = os.getenv("WINDY_API_KEY", "")
WINDY_API_URL = "https://api.windy.com/webcams/api/v3/webcams"
WINDY_CACHE: dict = {}   # chave: (offset, country) → {data, ts}
WINDY_CACHE_TTL = 300    # 5 minutos


def fetch_windy_cameras(offset: int = 0, limit: int = 20) -> dict:
    limit = min(limit, 50)
    cache_key = (offset, limit)
    now = time.time()
    cached = WINDY_CACHE.get(cache_key)
    if cached and (now - cached["ts"]) < WINDY_CACHE_TTL:
        return cached["data"]

    collected: list = []
    api_offset = offset  # paginação direta na API
    api_total = 0

    try:
        for _ in range(1):  # uma única requisição por página
            resp = requests.get(
                WINDY_API_URL,
                params={
                    "limit": 50,
                    "offset": api_offset,
                    "include": "location,images",
                },
                headers={"x-windy-api-key": WINDY_API_KEY},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            api_total = data.get("total", 0)

            for cam in data.get("webcams", []):
                preview = cam.get("images", {}).get("current", {}).get("preview", "")
                if not preview:
                    continue
                loc = cam.get("location", {})
                collected.append({
                    "id":           str(cam.get("webcamId", "")),
                    "title":        cam.get("title", "Sem nome"),
                    "city":         loc.get("city", ""),
                    "country":      loc.get("country", ""),
                    "country_code": loc.get("country_code", ""),
                    "preview":      preview,
                })

    except Exception as exc:
        return {"error": str(exc), "webcams": [], "total": 0}

    result = {
        "webcams": collected,
        "total":   api_total,
        "offset":  offset,
        "limit":   limit,
    }
    WINDY_CACHE[cache_key] = {"data": result, "ts": now}
    return result

# =========================
# RESOLUÇÃO DE STREAM
# =========================
def _yt_dlp_extract(source: str) -> str:
    import yt_dlp
    ydl_opts = {
        "format": "best[protocol^=m3u8]/best[protocol^=https]/best",
        "quiet": True,
        "noplaylist": True,
        "socket_timeout": 15,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(source, download=False)
        direct_url = info.get("url")
        if not direct_url:
            formats = info.get("formats", [])
            m3u8 = [f for f in formats if "m3u8" in (f.get("protocol") or "")]
            direct_url = (m3u8 or formats)[-1]["url"]
        return direct_url


def resolve_stream_url(source):
    """Converte URLs do YouTube em links diretos usando yt-dlp (se instalado).
    Para RTSP, MJPEG ou HLS direto, retorna como está."""
    if not isinstance(source, str):
        return source
    if "youtube.com" in source or "youtu.be" in source:
        set_camera_status("resolving", "Resolvendo URL do YouTube via yt-dlp...")
        try:
            import yt_dlp  # noqa: F401 — checa se está instalado
        except ImportError:
            msg = "yt-dlp não instalado. Instale com: pip install yt-dlp"
            print(f"[Camera] {msg}")
            set_camera_status("error", msg)
            return source
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(_yt_dlp_extract, source)
                direct_url = future.result(timeout=30)
            print("[Camera] URL do YouTube resolvida via yt-dlp.")
            return direct_url
        except FuturesTimeout:
            msg = "Timeout ao resolver YouTube URL (>30s). Tente outra URL."
            print(f"[Camera] {msg}")
            set_camera_status("error", msg)
        except Exception as exc:
            msg = f"Falha ao resolver YouTube URL: {exc}"
            print(f"[Camera] {msg}")
            set_camera_status("error", msg)
    return source

# =========================
# APP
# =========================
app = FastAPI(title="AgroVision AI")

os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs(SAVE_DIR, exist_ok=True)
os.makedirs("uploads", exist_ok=True)
os.makedirs("runs", exist_ok=True)
os.makedirs("models", exist_ok=True)
os.makedirs("dataset_agro/images/train", exist_ok=True)
os.makedirs("dataset_agro/images/val", exist_ok=True)
os.makedirs("dataset_agro/labels/train", exist_ok=True)
os.makedirs("dataset_agro/labels/val", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

model = YOLO(MODEL_PATH)
cv2.setLogLevel(0)  # suprime warnings do OpenCV no console

last_frame = None
last_frame_lock = threading.Lock()

# Estado da câmera visível ao frontend via /camera/status
camera_status = {"state": "connecting", "message": "Iniciando..."}
camera_status_lock = threading.Lock()

def set_camera_status(state: str, message: str):
    with camera_status_lock:
        camera_status["state"] = state
        camera_status["message"] = message

detection_state = defaultdict(int)
last_alert_time = defaultdict(lambda: 0.0)

active_camera_source = CAMERA_SOURCE
camera_source_lock = threading.Lock()


def list_cameras() -> list:
    with camera_source_lock:
        active = active_camera_source
    available = []
    for i in range(10):
        if i == active:
            available.append(i)  # câmera ativa já está aberta, não testa
            continue
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            available.append(i)
            cap.release()
    return available

# =========================
# BANCO DE DADOS
# =========================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            event_time TEXT,
            label TEXT,
            confidence REAL,
            image_path TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_event(event_id: str, label: str, confidence: float, image_path: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO events (id, event_time, label, confidence, image_path)
        VALUES (?, ?, ?, ?, ?)
    """, (
        event_id,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        label,
        confidence,
        image_path
    ))
    conn.commit()
    conn.close()


def list_events(limit: int = 50):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, event_time, label, confidence, image_path
        FROM events
        ORDER BY event_time DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()

    return [
        {
            "id": r[0],
            "event_time": r[1],
            "label": r[2],
            "confidence": r[3],
            "image_path": r[4]
        }
        for r in rows
    ]


def get_last_event():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, event_time, label, confidence, image_path
        FROM events
        ORDER BY event_time DESC
        LIMIT 1
    """)
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "id": row[0],
        "event_time": row[1],
        "label": row[2],
        "confidence": row[3],
        "image_path": row[4]
    }

# =========================
# FUNÇÕES DE DETECÇÃO
# =========================
def draw_box(frame, x1, y1, x2, y2, label, conf):
    text = f"{label} {conf:.2f}"
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
    cv2.putText(
        frame,
        text,
        (x1, max(20, y1 - 10)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 0),
        2
    )


def should_alert(label: str):
    now = time.time()
    return (now - last_alert_time[label]) > ALERT_COOLDOWN_SECONDS


def process_stream():
    global last_frame, active_camera_source

    current_source = None
    cap = None

    while True:
        # Verifica se houve troca de câmera ou ainda não abriu nenhuma
        with camera_source_lock:
            requested = active_camera_source

        if requested != current_source:
            if cap is not None:
                cap.release()
            current_source = requested
            set_camera_status("connecting", f"Conectando a {current_source}...")
            resolved = resolve_stream_url(current_source)
            cap = cv2.VideoCapture(resolved)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                set_camera_status("connected", f"Câmera {current_source} conectada.")
                print(f"[Camera] Câmera {current_source} aberta.")
            else:
                msg = f"Câmera {current_source} não disponível."
                set_camera_status("error", msg)
                print(f"[Camera] {msg} Tentando novamente em 3s...")
                cap.release()
                cap = None
                time.sleep(3)
                continue

        if cap is None or not cap.isOpened():
            time.sleep(3)
            continue

        ok, frame = cap.read()
        if not ok:
            print(f"[Camera] Falha ao ler frame. Reconectando em 3s...")
            cap.release()
            cap = None
            time.sleep(3)
            continue

        results = model(frame, conf=CONFIDENCE_THRESHOLD, verbose=False)

        found_labels_in_frame = set()
        best_conf_by_label = {}

        for result in results:
            boxes = result.boxes
            if boxes is None:
                continue

            for box in boxes:
                cls_id = int(box.cls[0].item())
                conf = float(box.conf[0].item())
                label = model.names[cls_id]

                if label not in TARGET_CLASSES:
                    continue

                found_labels_in_frame.add(label)

                if label not in best_conf_by_label or conf > best_conf_by_label[label]:
                    best_conf_by_label[label] = conf

                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                draw_box(frame, x1, y1, x2, y2, label, conf)

        for label in TARGET_CLASSES:
            if label in found_labels_in_frame:
                detection_state[label] += 1
            else:
                detection_state[label] = 0

        for label in found_labels_in_frame:
            if detection_state[label] >= MIN_CONSECUTIVE_FRAMES and should_alert(label):
                event_id = str(uuid.uuid4())[:8]
                filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{label}_{event_id}.jpg"
                filepath = os.path.join(SAVE_DIR, filename)

                cv2.imwrite(filepath, frame)
                image_path = f"/static/captures/{filename}"

                confidence = best_conf_by_label.get(label, 0.0)
                save_event(event_id, label, confidence, image_path)

                last_alert_time[label] = time.time()
                print(f"[ALERTA] {label} detectado. Evidência salva em {filepath}")

        with last_frame_lock:
            last_frame = frame.copy()

        time.sleep(0.05)

# =========================
# EVENTO DE INICIALIZAÇÃO
# =========================
@app.on_event("startup")
def startup_event():
    init_db()
    thread = threading.Thread(target=process_stream, daemon=True)
    thread.start()
    warmup_thread = threading.Thread(target=warmup_ollama, daemon=True)
    warmup_thread.start()

# =========================
# ROTAS
# =========================
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    events = list_events(20)
    return templates.TemplateResponse("index.html", {"request": request, "events": events})


@app.get("/health")
def health():
    return {"status": "ok", "service": "AgroVision AI", "ollama": get_ollama_status()}


@app.get("/cameras")
def get_cameras():
    with camera_source_lock:
        current = active_camera_source
    return JSONResponse(content={"cameras": list_cameras(), "active": current})


class CameraSelectRequest(BaseModel):
    source: Union[int, str]

@app.post("/camera/select")
def select_camera(req: CameraSelectRequest):
    global active_camera_source
    with camera_source_lock:
        active_camera_source = req.source
    set_camera_status("connecting", f"Conectando a {req.source}...")
    return JSONResponse(content={"selected": req.source})


@app.get("/camera/status")
def get_camera_status():
    with camera_status_lock:
        return JSONResponse(content=dict(camera_status))


class ResolveRequest(BaseModel):
    url: str

@app.post("/stream/resolve")
def stream_resolve(req: ResolveRequest):
    """Resolve YouTube URL para HLS direto — browser toca com hls.js."""
    try:
        resolved = resolve_stream_url(req.url)
        if resolved == req.url:
            return JSONResponse(status_code=400, content={"error": "Não foi possível resolver a URL."})
        return JSONResponse(content={"resolved_url": resolved})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


# =========================
# SCRAPING
# =========================
@app.get("/scraping/weather")
def get_weather(lat: float = -23.55, lon: float = -46.63):
    return JSONResponse(content=scraper.fetch_weather(lat, lon))


@app.get("/scraping/news")
def get_news(limit: int = 5):
    return JSONResponse(content=scraper.fetch_agro_news(limit))


@app.get("/cameras/public")
def get_public_cameras(offset: int = 0, limit: int = 20):
    if not WINDY_API_KEY:
        return JSONResponse(
            status_code=503,
            content={
                "error": "WINDY_API_KEY não configurada. Defina a variável de ambiente antes de iniciar.",
                "webcams": [],
                "total": 0,
            }
        )
    return JSONResponse(content=fetch_windy_cameras(offset, limit))


@app.get("/models")
def get_models():
    try:
        base_url = OLLAMA_URL.rsplit("/api/", 1)[0]
        resp = requests.get(f"{base_url}/api/tags", timeout=5)
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", [])]
        return JSONResponse(content={"models": models, "active": MODEL_NAME})
    except Exception as exc:
        return JSONResponse(status_code=503, content={"error": str(exc), "models": [], "active": MODEL_NAME})


class ModelSelectRequest(BaseModel):
    model: str


@app.post("/model/select")
def select_model(req: ModelSelectRequest):
    global MODEL_NAME
    MODEL_NAME = req.model
    return JSONResponse(content={"selected": MODEL_NAME})


@app.get("/events")
def get_events():
    return JSONResponse(content=list_events(50))


async def generate_mjpeg():
    while True:
        with last_frame_lock:
            frame = last_frame.copy() if last_frame is not None else None

        if frame is None:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + _placeholder_frame()
                + b"\r\n"
            )
            await asyncio.sleep(1.0)
            continue

        success, buffer = cv2.imencode(".jpg", frame)
        if not success:
            await asyncio.sleep(0.1)
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + buffer.tobytes()
            + b"\r\n"
        )
        await asyncio.sleep(0.05)


@app.get("/video_feed")
async def video_feed():
    return StreamingResponse(
        generate_mjpeg(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


def _placeholder_frame() -> bytes:
    img = np.zeros((360, 640, 3), dtype=np.uint8)
    cv2.putText(img, "Aguardando camera...", (120, 180),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (180, 180, 180), 2)
    _, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()


@app.get("/frame")
def get_frame():
    global last_frame

    with last_frame_lock:
        frame_data = last_frame

    if frame_data is None:
        return Response(content=_placeholder_frame(), media_type="image/jpeg")

    success, buffer = cv2.imencode(".jpg", frame_data)
    if not success:
        return Response(content=_placeholder_frame(), media_type="image/jpeg")

    return Response(content=buffer.tobytes(), media_type="image/jpeg")


# =========================
# CHAT COM OLLAMA / LLAMA
# =========================
class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: List[Message] = []


class ChatResponse(BaseModel):
    answer: str
    history: List[Message]


def get_ollama_status() -> str:
    try:
        base_url = OLLAMA_URL.rsplit("/api/", 1)[0]
        resp = requests.get(f"{base_url}/api/tags", timeout=3)
        if resp.ok:
            return "online"
        return "erro"
    except Exception:
        return "offline"


def build_chat_messages(message: str, history: List[Message]) -> list:
    system_msg = {
        "role": "system",
        "content": (
            "Você é um assistente do sistema AgroVision AI, especializado em monitoramento rural. "
            "Responda em português, de forma clara, objetiva e útil."
        )
    }
    messages = [system_msg]

    last_event = get_last_event()
    if last_event:
        event_ctx = (
            f"Último evento detectado:\n"
            f"- ID: {last_event['id']}\n"
            f"- Horário: {last_event['event_time']}\n"
            f"- Objeto: {last_event['label']}\n"
            f"- Confiança: {last_event['confidence']:.2f}\n"
            f"- Imagem: {last_event['image_path']}\n"
        )
        messages.append({"role": "system", "content": event_ctx})
    else:
        messages.append({"role": "system", "content": "Nenhum evento registrado no banco ainda."})

    for h in history:
        messages.append(h.dict())

    messages.append({"role": "user", "content": message})
    return messages


def ask_ollama(message: str, history: List[Message]):
    payload = {
        "model": MODEL_NAME,
        "messages": build_chat_messages(message, history),
        "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
    }
    response = requests.post(OLLAMA_URL, json=payload, timeout=(10, OLLAMA_TIMEOUT))
    response.raise_for_status()
    return response.json()["message"]["content"]


def stream_ollama(message: str, history: List[Message]):
    payload = {
        "model": MODEL_NAME,
        "messages": build_chat_messages(message, history),
        "stream": True,
        "keep_alive": OLLAMA_KEEP_ALIVE,
    }
    full_response = ""
    try:
        with requests.post(OLLAMA_URL, json=payload, stream=True, timeout=(10, OLLAMA_TIMEOUT)) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                delta = chunk.get("message", {}).get("content", "")
                if delta:
                    full_response += delta
                    yield json.dumps({"delta": delta}) + "\n"
                if chunk.get("done"):
                    new_history = list(history) + [
                        Message(role="user", content=message),
                        Message(role="assistant", content=full_response),
                    ]
                    yield json.dumps({"done": True, "history": [h.dict() for h in new_history]}) + "\n"
    except Exception as exc:
        yield json.dumps({"error": str(exc)}) + "\n"


def warmup_ollama():
    global warmup_status
    warmup_status = "warming"
    try:
        ask_ollama("Responda apenas: pronto", [])
        warmup_status = "ready"
        print(f"[Ollama] Modelo {MODEL_NAME} aquecido.")
    except Exception as exc:
        warmup_status = "failed"
        print(f"[Ollama] Warmup falhou: {exc}")


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    try:
        answer = ask_ollama(req.message, req.history)
        new_history = list(req.history) + [
            Message(role="user", content=req.message),
            Message(role="assistant", content=answer),
        ]
        return ChatResponse(answer=answer, history=new_history)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/chat/stream")
def chat_stream(req: ChatRequest):
    return StreamingResponse(
        stream_ollama(req.message, req.history),
        media_type="application/x-ndjson",
    )
