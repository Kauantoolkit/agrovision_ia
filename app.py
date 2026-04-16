import os
import cv2
import time
import uuid
import sqlite3
import threading
from datetime import datetime
from collections import defaultdict

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from ultralytics import YOLO

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
# APP
# =========================
app = FastAPI(title="AgroVision AI")

os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs(SAVE_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

model = YOLO(MODEL_PATH)

last_frame = None
last_frame_lock = threading.Lock()

detection_state = defaultdict(int)
last_alert_time = defaultdict(lambda: 0.0)

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
    global last_frame

    cap = cv2.VideoCapture(CAMERA_SOURCE)

    if not cap.isOpened():
        print("Erro ao abrir câmera.")
        return

    print("Câmera iniciada com sucesso.")

    while True:
        ok, frame = cap.read()
        if not ok:
            time.sleep(1)
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

# =========================
# ROTAS
# =========================
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    events = list_events(20)
    return templates.TemplateResponse("index.html", {"request": request, "events": events})


@app.get("/health")
def health():
    return {"status": "ok", "service": "AgroVision AI"}


@app.get("/events")
def get_events():
    return JSONResponse(content=list_events(50))


@app.get("/frame")
def get_frame():
    global last_frame

    with last_frame_lock:
        if last_frame is None:
            return JSONResponse(
                content={"message": "Ainda sem frame disponível."},
                status_code=503
            )

        success, buffer = cv2.imencode(".jpg", last_frame)
        if not success:
            return JSONResponse(
                content={"message": "Erro ao converter frame."},
                status_code=500
            )

        return Response(content=buffer.tobytes(), media_type="image/jpeg")


# =========================
# CHAT LOCAL (baseado em regras)
# =========================
class ChatRequest(BaseModel):
    message: str


LABEL_TRADUCAO = {
    "person": "pessoa",
    "car": "carro",
    "motorcycle": "motocicleta",
    "truck": "caminhão",
    "bus": "ônibus",
}

PALAVRAS_CHAVE = {
    "último": "ultimo_evento",
    "ultimo": "ultimo_evento",
    "recente": "ultimo_evento",
    "detectado": "ultimo_evento",
    "detecção": "ultimo_evento",
    "deteccao": "ultimo_evento",
    "quando": "ultimo_evento",
    "horário": "ultimo_evento",
    "horario": "ultimo_evento",
    "quantos": "contagem",
    "quantidade": "contagem",
    "total": "contagem",
    "eventos": "contagem",
    "imagem": "imagem",
    "foto": "imagem",
    "evidência": "imagem",
    "evidencia": "imagem",
    "câmera": "camera",
    "camera": "camera",
    "frame": "camera",
    "status": "camera",
    "ajuda": "ajuda",
    "help": "ajuda",
    "comandos": "ajuda",
    "o que": "ajuda",
}


def interpretar_pergunta(message: str) -> str:
    msg = message.lower()
    for palavra, intencao in PALAVRAS_CHAVE.items():
        if palavra in msg:
            return intencao
    return "desconhecido"


@app.post("/chat")
def chat(req: ChatRequest):
    last_event = get_last_event()
    todos_eventos = list_events(50)
    intencao = interpretar_pergunta(req.message)

    if intencao == "ultimo_evento":
        if not last_event:
            answer = "Nenhum evento foi registrado pelo sistema até o momento."
        else:
            label_pt = LABEL_TRADUCAO.get(last_event["label"], last_event["label"])
            answer = (
                f"O último evento detectado foi:\n"
                f"- Tipo: {label_pt}\n"
                f"- Data/Hora: {last_event['event_time']}\n"
                f"- Confiança: {last_event['confidence']:.2%}\n"
                f"- ID do registro: {last_event['id']}"
            )

    elif intencao == "contagem":
        if not todos_eventos:
            answer = "Nenhum evento registrado até o momento."
        else:
            contagem = {}
            for ev in todos_eventos:
                label_pt = LABEL_TRADUCAO.get(ev["label"], ev["label"])
                contagem[label_pt] = contagem.get(label_pt, 0) + 1
            linhas = "\n".join(f"- {k}: {v}" for k, v in contagem.items())
            answer = f"Total de {len(todos_eventos)} eventos registrados:\n{linhas}"

    elif intencao == "imagem":
        if not last_event:
            answer = "Nenhum evento registrado. Ainda não há imagens de evidência."
        else:
            answer = (
                f"A imagem do último evento está disponível em:\n"
                f"{last_event['image_path']}\n\n"
                f"Abra o link na tabela de eventos para visualizá-la."
            )

    elif intencao == "camera":
        with last_frame_lock:
            tem_frame = last_frame is not None
        status = "ativa e transmitindo" if tem_frame else "sem sinal no momento"
        answer = f"A câmera está {status}. Acesse /frame para ver o último frame capturado."

    elif intencao == "ajuda":
        answer = (
            "Sou o assistente do AgroVision AI. Você pode me perguntar:\n"
            "- 'O que foi detectado no último evento?'\n"
            "- 'Quantos eventos foram registrados?'\n"
            "- 'Onde está a imagem do último evento?'\n"
            "- 'Qual é o status da câmera?'"
        )

    else:
        if last_event:
            label_pt = LABEL_TRADUCAO.get(last_event["label"], last_event["label"])
            answer = (
                f"Não entendi exatamente sua pergunta, mas posso informar que o último evento "
                f"registrado foi a detecção de {label_pt} em {last_event['event_time']}.\n\n"
                f"Tente perguntar sobre: último evento, quantidade de eventos, imagem ou câmera."
            )
        else:
            answer = (
                "Não entendi sua pergunta. Tente perguntar sobre:\n"
                "- último evento detectado\n"
                "- quantidade de eventos\n"
                "- status da câmera"
            )

    return JSONResponse(content={"answer": answer})
