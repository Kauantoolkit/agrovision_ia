// ── HLS player ────────────────────────────────────────────────
let hlsInstance = null;

function switchToHLS(url) {
    const img   = document.getElementById("live-feed-img");
    const video = document.getElementById("live-feed-video");
    if (hlsInstance) { hlsInstance.destroy(); hlsInstance = null; }
    img.style.display   = "none";
    video.style.display = "block";
    if (typeof Hls !== "undefined" && Hls.isSupported()) {
        hlsInstance = new Hls();
        hlsInstance.loadSource(url);
        hlsInstance.attachMedia(video);
        hlsInstance.on(Hls.Events.MANIFEST_PARSED, () => video.play());
        hlsInstance.on(Hls.Events.ERROR, (_, data) => {
            if (data.fatal)
                document.getElementById("camera-status").textContent = "Erro HLS: " + data.details;
        });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
        video.src = url;
        video.play();
    } else {
        document.getElementById("camera-status").textContent = "HLS não suportado neste browser.";
    }
}

function switchToMJPEG() {
    const img   = document.getElementById("live-feed-img");
    const video = document.getElementById("live-feed-video");
    if (hlsInstance) { hlsInstance.destroy(); hlsInstance = null; }
    video.style.display = "none";
    video.src           = "";
    img.style.display   = "block";
    img.src             = "/video_feed";
}

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
        switchToMJPEG();
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

// ── Camera status polling ─────────────────────────────────────
let cameraStatusPoller = null;

function startCameraStatusPolling(btn) {
    stopCameraStatusPolling();
    const status = document.getElementById("camera-status");
    cameraStatusPoller = setInterval(async () => {
        try {
            const data = await fetch("/camera/status").then(r => r.json());
            status.textContent = data.message;
            if (data.state === "connected" || data.state === "error") {
                stopCameraStatusPolling();
                if (btn) btn.disabled = false;
            }
        } catch {
            // ignora falhas de rede durante polling
        }
    }, 1500);
}

function stopCameraStatusPolling() {
    if (cameraStatusPoller) {
        clearInterval(cameraStatusPoller);
        cameraStatusPoller = null;
    }
}

async function applyLocalCamera() {
    const select = document.getElementById("camera-select");
    const btn    = document.getElementById("camera-apply-btn");
    const idx    = parseInt(select.value);
    if (isNaN(idx)) return;
    btn.disabled = true;
    document.getElementById("camera-status").textContent = "Trocando câmera...";
    try {
        await fetch("/camera/select", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ source: idx })
        });
        switchToMJPEG();
        startCameraStatusPolling(btn);
    } catch {
        document.getElementById("camera-status").textContent = "Erro ao trocar câmera.";
        btn.disabled = false;
    }
}

// ── Stream by URL ─────────────────────────────────────────────
async function applyStreamUrl() {
    const input  = document.getElementById("stream-url");
    const btn    = document.getElementById("url-apply-btn");
    const url    = input.value.trim();
    if (!url) {
        document.getElementById("camera-status").textContent = "Cole uma URL antes de conectar.";
        return;
    }
    btn.disabled = true;
    const isHLS     = url.includes(".m3u8");
    const isYoutube = url.includes("youtube.com") || url.includes("youtu.be");

    if (isHLS) {
        // Toca direto no browser via hls.js — sem passar pelo backend
        document.getElementById("camera-status").textContent = "Conectando ao stream HLS...";
        switchToHLS(url);
        document.getElementById("camera-status").textContent = "Stream HLS conectado.";
        btn.disabled = false;
    } else if (isYoutube) {
        // Backend resolve a URL, browser toca com hls.js
        document.getElementById("camera-status").textContent = "Resolvendo URL do YouTube (até 30s)...";
        try {
            const resp = await fetch("/stream/resolve", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url })
            });
            const data = await resp.json();
            if (data.error) {
                document.getElementById("camera-status").textContent = "Erro: " + data.error;
            } else {
                switchToHLS(data.resolved_url);
                document.getElementById("camera-status").textContent = "YouTube conectado via HLS.";
            }
        } catch (e) {
            document.getElementById("camera-status").textContent = "Erro: " + e.message;
        }
        btn.disabled = false;
    } else {
        // RTSP / MJPEG — proxy pelo backend + OpenCV
        document.getElementById("camera-status").textContent = "Enviando requisição...";
        try {
            await fetch("/camera/select", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ source: url })
            });
            switchToMJPEG();
            startCameraStatusPolling(btn);
        } catch {
            document.getElementById("camera-status").textContent = "Erro ao conectar ao stream.";
            btn.disabled = false;
        }
    }
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
    document.getElementById("live-feed-img").src = cam.preview + "?t=" + Date.now();
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

    document.getElementById("live-feed-img").src = cam.preview;

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

// ── Scraping: Clima ───────────────────────────────────────────
const WEATHER_ICONS = {
    rain:    "🌧️",
    cloudy:  "🌥️",
    clear:   "☀️",
    cold:    "🥶",
};

function weatherIcon(c) {
    if (c.precipitation > 0)  return WEATHER_ICONS.rain;
    if (c.humidity     > 80)  return WEATHER_ICONS.cloudy;
    if (c.temperature  < 10)  return WEATHER_ICONS.cold;
    return WEATHER_ICONS.clear;
}

function weatherCondition(c) {
    if (c.precipitation > 0) return "Chuva";
    if (c.humidity > 80)     return "Nublado";
    if (c.temperature < 10)  return "Frio";
    return "Tempo limpo";
}

async function loadWeather() {
    try {
        const data = await fetch("/scraping/weather").then(r => r.json());
        if (data.error) {
            document.getElementById("weather-error").textContent = data.error;
            return;
        }
        const c = data.current;

        document.getElementById("weather-icon-big").textContent   = weatherIcon(c);
        document.getElementById("weather-temp-big").textContent   = `${c.temperature}°C`;
        document.getElementById("weather-condition").textContent  = weatherCondition(c);
        document.getElementById("weather-humidity").textContent   = `${c.humidity}%`;
        document.getElementById("weather-wind").textContent       = `${c.windspeed} km/h`;
        document.getElementById("weather-rain").textContent       = `${c.precipitation} mm`;
        document.getElementById("weather-time").textContent       = data.cached_at;
        document.getElementById("weather-badge").textContent      =
            c.precipitation > 0 ? "Chuva" : "Seco";

        const forecastEl = document.getElementById("weather-forecast");
        forecastEl.innerHTML = "";
        (data.forecast || []).forEach(day => {
            const div = document.createElement("div");
            div.className = "forecast-day";
            const rainText = day.rain > 0 ? `💧 ${day.rain}mm` : "–";
            div.innerHTML =
                `<div class="forecast-date">${day.date.slice(5)}</div>
                 <div class="forecast-temps">${day.max}° / ${day.min}°</div>
                 <div class="forecast-rain">${rainText}</div>`;
            forecastEl.appendChild(div);
        });
    } catch (e) {
        document.getElementById("weather-error").textContent = "Erro: " + e.message;
    }
}

// ── Scraping: Notícias ────────────────────────────────────────
async function loadAgroNews() {
    const list = document.getElementById("agro-news");
    list.innerHTML = `<li class="news-item"><span class="news-num">—</span>
        <span class="news-body" style="color:#94a3b8">Carregando notícias...</span></li>`;
    try {
        const data = await fetch("/scraping/news?limit=5").then(r => r.json());
        list.innerHTML = "";

        if (!Array.isArray(data) || data.length === 0) {
            list.innerHTML = `<li style="color:#94a3b8;font-size:13px;padding:8px 0">Nenhuma notícia encontrada.</li>`;
            return;
        }

        data.forEach((item, i) => {
            if (item.error) {
                document.getElementById("news-error").textContent = item.error;
                return;
            }
            const li = document.createElement("li");
            li.className = "news-item";

            const titleEl = item.link
                ? `<a href="${item.link}" target="_blank" rel="noopener">${item.title}</a>`
                : `<span>${item.title}</span>`;

            li.innerHTML =
                `<span class="news-num">${i + 1}</span>
                 <span class="news-body">
                     ${titleEl}
                     <span class="news-src">${item.source || ""}</span>
                 </span>`;
            list.appendChild(li);
        });
    } catch (e) {
        list.innerHTML = "";
        document.getElementById("news-error").textContent = "Erro: " + e.message;
    }
}

loadWeather();
loadAgroNews();

// ── Chat ──────────────────────────────────────────────────────
let chatHistory = [];

async function loadModels() {
    try {
        const data = await fetch("/models").then(r => r.json());
        const select = document.getElementById("model-select");
        select.innerHTML = "";
        data.models.forEach(m => {
            const opt = document.createElement("option");
            opt.value = m;
            opt.textContent = m;
            if (m === data.active) opt.selected = true;
            select.appendChild(opt);
        });
    } catch {
        document.getElementById("model-select").innerHTML = "<option>Erro ao carregar</option>";
    }
}

async function selectModel(model) {
    if (!model) return;
    try {
        await fetch("/model/select", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ model })
        });
        setStatus("Modelo trocado para " + model);
    } catch {
        setStatus("Erro ao trocar modelo.");
    }
}

loadModels();

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
    let   firstTokenAt = null;

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
                    if (firstTokenAt === null) {
                        firstTokenAt = Date.now();
                        const ttft = ((firstTokenAt - startedAt) / 1000).toFixed(1);
                        setStatus("Primeira palavra em " + ttft + "s...");
                    }
                    fullAnswer += chunk.delta;
                    aiBubble.textContent = fullAnswer;
                    document.getElementById("chat-history").scrollTop =
                        document.getElementById("chat-history").scrollHeight;
                }

                if (chunk.done) {
                    aiBubble.classList.remove("streaming");
                    if (chunk.history) chatHistory = chunk.history;
                    const elapsed = ((Date.now() - startedAt) / 1000).toFixed(1);
                    const ttft = firstTokenAt ? ((firstTokenAt - startedAt) / 1000).toFixed(1) : "?";
                    setStatus("Primeira palavra: " + ttft + "s · Total: " + elapsed + "s · " + (chatHistory.length / 2 | 0) + " trocas");
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
