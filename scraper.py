"""
scraper.py — Camada de Web Scraping do AgroVision AI

Responsabilidades:
  - fetch_weather()   → previsão do tempo via Open-Meteo (gratuito, sem API key)
  - fetch_agro_news() → scraping de notícias do agronegócio (Notícias Agrícolas)

Boas práticas implementadas:
  - Cache por TTL (10 min) para limitar requisições à fonte
  - Rate limiting independente do cache
  - Timeout em todas as requisições
  - Tratamento de erro: nunca propaga exceção ao chamador
  - Dados sempre retornados em formato JSON-serializável
"""

import time
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup

# ── Cache e rate limit ────────────────────────────────────────
_CACHE: dict = {}
_LAST_REQUEST: dict = {}

CACHE_TTL        = 600   # 10 minutos
MIN_INTERVAL     = 60    # mínimo de 60s entre requisições ao mesmo endpoint
REQUEST_TIMEOUT  = 10    # segundos

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AgroVisionBot/1.0; +https://github.com/agrovision)"
}


def _get_cache(key: str):
    entry = _CACHE.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    return None


def _set_cache(key: str, data):
    _CACHE[key] = {"data": data, "ts": time.time()}
    return data


def _rate_ok(key: str) -> bool:
    """Retorna True se passou tempo suficiente desde a última requisição real."""
    now = time.time()
    if now - _LAST_REQUEST.get(key, 0) < MIN_INTERVAL:
        return False
    _LAST_REQUEST[key] = now
    return True


# ── Clima — Open-Meteo (gratuito, sem API key) ────────────────
def fetch_weather(lat: float = -23.55, lon: float = -46.63) -> dict:
    """
    Busca previsão do tempo atual e para os próximos 3 dias.

    Fonte: Open-Meteo (open-meteo.com) — API pública, sem autenticação.
    Relevância: condições climáticas afetam diretamente operações rurais,
    colheitas, movimentação de máquinas e presença de pessoas no campo.

    Args:
        lat: latitude (padrão: São Paulo -SP)
        lon: longitude (padrão: São Paulo -SP)
    """
    key = f"weather_{lat:.2f}_{lon:.2f}"
    cached = _get_cache(key)
    if cached:
        return cached

    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":  lat,
                "longitude": lon,
                "current":   "temperature_2m,relative_humidity_2m,precipitation,windspeed_10m",
                "daily":     "temperature_2m_max,temperature_2m_min,precipitation_sum",
                "timezone":  "America/Sao_Paulo",
                "forecast_days": 3,
            },
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        d = resp.json()

        current = d.get("current", {})
        daily   = d.get("daily", {})

        result = {
            "current": {
                "temperature":  current.get("temperature_2m"),
                "humidity":     current.get("relative_humidity_2m"),
                "precipitation":current.get("precipitation"),
                "windspeed":    current.get("windspeed_10m"),
            },
            "forecast": [
                {
                    "date": daily["time"][i],
                    "max":  daily["temperature_2m_max"][i],
                    "min":  daily["temperature_2m_min"][i],
                    "rain": daily["precipitation_sum"][i],
                }
                for i in range(len(daily.get("time", [])))
            ],
            "source":    "open-meteo.com",
            "cached_at": time.strftime("%H:%M"),
        }
        return _set_cache(key, result)

    except Exception as exc:
        return {"error": f"Clima indisponível: {exc}"}


# ── Notícias do agronegócio — Notícias Agrícolas ─────────────
def fetch_agro_news(limit: int = 5) -> list:
    """
    Coleta manchetes do agronegócio via RSS do Google News.

    Fonte: Google News RSS (news.google.com) — público, gratuito, sem API key.
    XML/RSS sempre vem em UTF-8 bem formado, evitando problemas de encoding.
    Relevância: contextualiza detecções YOLO com cenário do setor agro
    (ex: surto de pragas, clima extremo, movimentações de safra).

    Args:
        limit: máximo de notícias a retornar
    """
    key = "agro_news"
    cached = _get_cache(key)
    if cached:
        return cached

    if not _rate_ok(key):
        return [{"title": "Aguardando atualização...", "link": "", "source": "cache"}]

    try:
        resp = requests.get(
            "https://news.google.com/rss/search",
            params={
                "q":    "agronegócio safra brasil",
                "hl":   "pt-BR",
                "gl":   "BR",
                "ceid": "BR:pt-419",
            },
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()

        # ElementTree lida automaticamente com o encoding declarado no XML
        root    = ET.fromstring(resp.content)
        channel = root.find("channel")
        if channel is None:
            raise ValueError("Feed RSS inválido.")

        news = []
        for item in channel.findall("item")[:limit]:
            title  = item.findtext("title", "").strip()
            link   = item.findtext("link",  "").strip()
            src_el = item.find("source")
            source = src_el.text.strip() if src_el is not None else "Google News"
            if title:
                news.append({"title": title, "link": link, "source": source})

        if not news:
            raise ValueError("Feed retornou vazio.")

        return _set_cache(key, news)

    except Exception as exc:
        return [{"error": f"Notícias indisponíveis: {exc}", "title": "", "link": ""}]
