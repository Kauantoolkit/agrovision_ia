// ── Camera tabs ───────────────────────────────────────────────
let activeCamTab = "local";

function switchCamTab(tab) {
    activeCamTab = tab;
    ["local", "url", "windy"].forEach(t => {
        document.getElementById("cam-panel-" + t).classList.toggle("active", t === tab);
        document.getElementById("tab-btn-" + t).classList.toggle("active", t === tab);
    });
    if (tab !== "windy") {
        clearInterval(windyRefreshTimer);
        windyRefreshTimer = null;
        document.getElementById("live-feed").src = "/video_feed";
    }
    if (tab === "windy" && windyCameras.length === 0) {
        loadWindyCameras(0);
    }
}

const EXAMPLES = {
    rtsp:    "rtsp://usuario:senha@192.168.1.100:554/stream1",
    mjpeg:   "http://webcam.exemplo.com/video.mjpg",
    youtube: "https://www.youtube.com/watch?v=COLE_O_ID_AQUI"
};

function fillExample(type) {
    document.getElementById("stream-url").value = EXAMPLES[type];
    document.getElementById("stream-url").focus();
}

// ── Local cameras ─────────────────────────────────────────────
async function loadCameras() {
    const select = document.getElementById("camera-select");
    const status = document.getElementById("camera-status");
    status.textContent = "Detectando câmeras locais...";
    try {
        const data = await fetch("/cameras").then(r => r.json());
        select.innerHTML = "";
        if (data.cameras.length === 0) {
            select.innerHTML = "<option value=''>Nenhuma câmera encontrada</option>";
            status.textContent = "Nenhuma câmera local disponível.";
            return;
        }
        data.cameras.forEach(idx => {
            const opt = document.createElement("option");
            opt.value = idx;
            opt.textContent = `Câmera ${idx}`;
            if (idx === data.active) opt.selected = true;
            select.appendChild(opt);
        });
        const activeLabel = typeof data.active === "string"
            ? "URL externa"
            : `Câmera ${data.active}`;
        status.textContent = `${data.cameras.length} câmera(s) local(is). Ativa agora: ${activeLabel}.`;
    } catch {
        status.textContent = "Erro ao listar câmeras.";
    }
}

async function applyLocalCamera() {
    const select = document.getElementById("camera-select");
    const status = document.getElementById("camera-status");
    const btn    = document.getElementById("camera-apply-btn");
    const idx    = parseInt(select.value);
    if (isNaN(idx)) return;
    btn.disabled = true;
    status.textContent = "Trocando para câmera local...";
    try {
        await fetch("/camera/select", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ source: idx })
        });
        status.textContent = `Câmera ${idx} ativada.`;
    } catch {
        status.textContent = "Erro ao trocar câmera.";
    }
    btn.disabled = false;
}

// ── Stream by URL ─────────────────────────────────────────────
async function applyStreamUrl() {
    const input  = document.getElementById("stream-url");
    const status = document.getElementById("camera-status");
    const btn    = document.getElementById("url-apply-btn");
    const url    = input.value.trim();
    if (!url) {
        status.textContent = "Cole uma URL antes de conectar.";
        return;
    }
    btn.disabled = true;
    const isYoutube = url.includes("youtube.com") || url.includes("youtu.be");
    status.textContent = isYoutube
        ? "Resolvendo stream do YouTube (pode demorar alguns segundos)..."
        : "Conectando ao stream...";
    try {
        await fetch("/camera/select", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ source: url })
        });
        status.textContent = `Stream conectado: ${url.length > 60 ? url.slice(0, 57) + "..." : url}`;
    } catch {
        status.textContent = "Erro ao conectar ao stream.";
    }
    btn.disabled = false;
}

loadCameras();

// ── Windy public cameras ──────────────────────────────────────
let windyCameras    = [];
let windyIndex      = 0;
let windyOffset     = 0;
let windyTotal      = 0;
let windyRefreshTimer = null;
const WINDY_PAGE    = 50;
const WINDY_REFRESH = 8000;

async function loadWindyCameras(offset) {
    const status  = document.getElementById("camera-status");
    const prevBtn = document.getElementById("windy-prev");
    const nextBtn = document.getElementById("windy-next");

    prevBtn.disabled = true;
    nextBtn.disabled = true;
    document.getElementById("windy-title").textContent    = "Carregando...";
    document.getElementById("windy-location").textContent = "";
    document.getElementById("windy-counter").textContent  = "";
    status.textContent = "Buscando câmeras públicas...";

    try {
        const params = new URLSearchParams({ offset, limit: WINDY_PAGE });
        const resp   = await fetch("/cameras/public?" + params);
        const data   = await resp.json();

        if (data.error) {
            document.getElementById("windy-title").textContent    = "⚠️ " + data.error;
            document.getElementById("windy-location").textContent = "";
            status.textContent = "";
            return;
        }

        windyCameras = data.webcams || [];
        windyOffset  = offset;
        windyTotal   = data.total  || 0;
        windyIndex   = 0;

        if (windyCameras.length === 0) {
            document.getElementById("windy-title").textContent    = "Nenhuma câmera encontrada";
            document.getElementById("windy-location").textContent = "";
            status.textContent = "";
            return;
        }

        showWindyCamera(0);
        status.textContent = `${windyTotal.toLocaleString()} câmeras públicas disponíveis.`;
    } catch (err) {
        document.getElementById("windy-title").textContent    = "Erro de conexão";
        document.getElementById("windy-location").textContent = err.message;
        status.textContent = "";
    }
}

function windyRefreshSnapshot() {
    if (activeCamTab !== "windy" || windyCameras.length === 0) return;
    const cam = windyCameras[windyIndex];
    document.getElementById("live-feed").src = cam.preview + "?t=" + Date.now();
}

function showWindyCamera(idx) {
    const cam     = windyCameras[idx];
    const prevBtn = document.getElementById("windy-prev");
    const nextBtn = document.getElementById("windy-next");

    document.getElementById("windy-title").textContent =
        cam.title || "Sem nome";

    const parts = [cam.city, cam.country].filter(Boolean);
    document.getElementById("windy-location").textContent =
        parts.length ? parts.join(", ") : "Localização desconhecida";

    const globalIndex = windyOffset + idx + 1;
    document.getElementById("windy-counter").textContent =
        `${globalIndex.toLocaleString()} / ${windyTotal.toLocaleString()}`;

    document.getElementById("live-feed").src = cam.preview;

    clearInterval(windyRefreshTimer);
    windyRefreshTimer = setInterval(windyRefreshSnapshot, WINDY_REFRESH);

    prevBtn.disabled = (windyOffset === 0 && idx === 0);
    nextBtn.disabled = (windyOffset + idx + 1 >= windyTotal);
}

async function windyNavigate(dir) {
    const newIdx = windyIndex + dir;

    if (newIdx >= 0 && newIdx < windyCameras.length) {
        windyIndex = newIdx;
        showWindyCamera(windyIndex);
        return;
    }

    if (dir === 1 && windyOffset + WINDY_PAGE < windyTotal) {
        await loadWindyCameras(windyOffset + WINDY_PAGE);
    } else if (dir === -1 && windyOffset > 0) {
        await loadWindyCameras(windyOffset - WINDY_PAGE);
        windyIndex = windyCameras.length - 1;
        showWindyCamera(windyIndex);
    }
}

// ── Chat ──────────────────────────────────────────────────────
let chatHistory = [];

function handleKey(event) {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        sendQuestion();
    }
}

function addBubble(role, text, streaming = false) {
    const history = document.getElementById("chat-history");
    const bubble  = document.createElement("div");
    bubble.className = "chat-bubble " + role + (streaming ? " streaming" : "");
    bubble.textContent = text;
    history.appendChild(bubble);
    history.scrollTop = history.scrollHeight;
    return bubble;
}

function setStatus(text) {
    document.getElementById("chat-status").textContent = text;
}

function setSendDisabled(disabled) {
    document.getElementById("chat-send-btn").disabled = disabled;
}

async function sendQuestion() {
    const questionField = document.getElementById("chat-question");
    const message = questionField.value.trim();
    if (!message) return;

    questionField.value = "";
    setSendDisabled(true);
    setStatus("Consultando o modelo local...");

    addBubble("user", message);
    const aiBubble = addBubble("assistant", "▍", true);

    const startedAt  = Date.now();
    let   fullAnswer = "";

    try {
        const response = await fetch("/chat/stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message, history: chatHistory })
        });

        if (!response.ok) throw new Error("Erro HTTP " + response.status);

        const reader  = response.body.getReader();
        const decoder = new TextDecoder();

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const lines = decoder.decode(value, { stream: true }).split("\n").filter(l => l.trim());
            for (const line of lines) {
                const chunk = JSON.parse(line);

                if (chunk.error) {
                    aiBubble.textContent = "Erro: " + chunk.error;
                    aiBubble.classList.remove("streaming");
                    setStatus("Erro ao consultar o Ollama. Verifique se ele está rodando.");
                    setSendDisabled(false);
                    return;
                }

                if (chunk.delta) {
                    fullAnswer += chunk.delta;
                    aiBubble.textContent = fullAnswer;
                    document.getElementById("chat-history").scrollTop =
                        document.getElementById("chat-history").scrollHeight;
                }

                if (chunk.done) {
                    aiBubble.classList.remove("streaming");
                    if (chunk.history) chatHistory = chunk.history;
                    const elapsed = ((Date.now() - startedAt) / 1000).toFixed(1);
                    setStatus(`Resposta em ${elapsed}s · ${chatHistory.length / 2 | 0} trocas no histórico`);
                }
            }
        }

    } catch (err) {
        setStatus("Streaming falhou, tentando modo normal...");
        try {
            const resp = await fetch("/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message, history: chatHistory })
            });
            const data = await resp.json();
            if (data.error) throw new Error(data.error);
            aiBubble.textContent = data.answer;
            aiBubble.classList.remove("streaming");
            if (data.history) chatHistory = data.history;
            const elapsed = ((Date.now() - startedAt) / 1000).toFixed(1);
            setStatus(`Resposta em ${elapsed}s.`);
        } catch (fallbackErr) {
            aiBubble.textContent = "Não foi possível obter resposta. Verifique se o Ollama está rodando.";
            aiBubble.classList.remove("streaming");
            setStatus("Erro: " + fallbackErr.message);
        }
    }

    setSendDisabled(false);
}

function clearHistory() {
    chatHistory = [];
    const historyDiv = document.getElementById("chat-history");
    historyDiv.innerHTML = "";
    addBubble("assistant", "Histórico limpo. Como posso ajudar?");
    setStatus("Histórico limpo.");
}
