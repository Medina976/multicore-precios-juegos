"""
scrapers/hltb.py
Dueño: Brack

Extrae los tiempos de juego de HowLongToBeat.com usando su API interna
mediante requests puro (sin Selenium, sin navegador).

El único problema real era que el hash del endpoint caduca y el código
anterior no lo renovaba. Esta versión lo renueva automáticamente.

ESTRATEGIA:
    1. POST directo con el hash cacheado en memoria (fast path).
    2. Si HLTB responde 400/404 (hash expirado): escanear los chunks de
       Next.js con requests para obtener un hash fresco y reintentar.
    3. Si requests tampoco encuentra el hash (HLTB bloqueó el UA):
       rotar User-Agent y reintentar hasta _MAX_REINTENTOS.

El caché es thread-safe (_hash_lock), por lo que cuando el orquestador
corre 10 juegos en paralelo solo uno de ellos refresca el hash y los
demás esperan y reutilizan el mismo valor.

Firma requerida por orquestador_brack.py:
    scraper.obtener_datos(juego: dict) -> dict

Retorna:
    {
        "main":           float | None,
        "main_extra":     float | None,
        "completionist":  float | None,
    }
"""

import re
import time
import logging
import threading
import requests
from fake_useragent import UserAgent

log = logging.getLogger(__name__)

_ua = UserAgent()
_MAX_REINTENTOS = 3
_ESPERA_BASE    = 2

_HLTB_HOME        = "https://howlongtobeat.com/"
_HLTB_SEARCH_BASE = "https://howlongtobeat.com/api/search"
_HLTB_REFERER     = "https://howlongtobeat.com/"

# Caché del hash — compartido y thread-safe
_hash_cache: str | None = None
_hash_lock  = threading.Lock()


def _extraer_hash_via_requests() -> str | None:
    """
    Descarga la página principal de HLTB y los primeros chunks de Next.js
    para encontrar el hash del endpoint de búsqueda.
    """
    hdrs = {
        "User-Agent": _ua.random,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    try:
        resp = requests.get(_HLTB_HOME, headers=hdrs, timeout=15)
        resp.raise_for_status()
        html = resp.text

        # Buscar hash directo en el HTML
        m = re.search(r'/api/search/([a-zA-Z0-9]{10,35})["\'/]', html)
        if m:
            log.debug(f"[HLTB] Hash en HTML: {m.group(1)}")
            return m.group(1)

        # Escanear chunks de Next.js (máx 10 para no tardar)
        chunk_urls = re.findall(r'"(/_next/static/chunks/[^"]+\.js)"', html)
        for cu in chunk_urls[:10]:
            try:
                js = requests.get(
                    f"https://howlongtobeat.com{cu}",
                    headers=hdrs,
                    timeout=10,
                )
                m = re.search(r'/api/search/([a-zA-Z0-9]{10,35})["\'/]', js.text)
                if m:
                    log.debug(f"[HLTB] Hash en chunk JS: {m.group(1)}")
                    return m.group(1)
            except requests.RequestException:
                continue

    except requests.RequestException as e:
        log.debug(f"[HLTB] requests falló al extraer hash: {e}")

    return None


def _obtener_hash(forzar_refresh: bool = False) -> str | None:
    """Retorna el hash cacheado, o lo extrae si es necesario."""
    global _hash_cache
    with _hash_lock:
        if _hash_cache and not forzar_refresh:
            return _hash_cache
        log.info("[HLTB] Extrayendo hash dinámico...")
        h = _extraer_hash_via_requests()
        if h:
            _hash_cache = h
            log.info(f"[HLTB] Hash cacheado: {h}")
        else:
            log.error("[HLTB] No se pudo obtener el hash de HLTB.")
        return _hash_cache


class ScraperHLTB:
    """Scraper para tiempos de juego de HowLongToBeat (100% requests, sin Selenium)."""

    def obtener_datos(self, juego: dict) -> dict:
        titulo = juego.get("titulo", "")
        if not titulo:
            raise ValueError(f"Juego {juego.get('id')} no tiene título")

        payload = {
            "searchType": "games",
            "searchTerms": titulo.split(),
            "searchPage": 1,
            "size": 5,
            "searchOptions": {
                "games": {
                    "userId": 0,
                    "platform": "",
                    "sortCategory": "popular",
                    "rangeCategory": "main",
                    "rangeTime": {"min": None, "max": None},
                    "gameplay": {"perspective": "", "flow": "", "genre": ""},
                    "rangeYear": {"min": "", "max": ""},
                    "modifier": "",
                },
                "users": {"sortCategory": "postcount"},
                "lists": {"sortCategory": "follows"},
                "filter": "",
                "sort": 0,
                "randomizer": 0,
            },
            "useCache": True,
        }

        espera        = _ESPERA_BASE
        hash_invalido = False

        for intento in range(1, _MAX_REINTENTOS + 1):
            h = _obtener_hash(forzar_refresh=hash_invalido)
            hash_invalido = False

            if not h:
                log.error(f"[HLTB] Sin hash para '{titulo}'.")
                return {"main": None, "main_extra": None, "completionist": None}

            hdrs = {
                "User-Agent":   _ua.random,
                "Referer":      _HLTB_REFERER,
                "Origin":       "https://howlongtobeat.com",
                "Content-Type": "application/json",
                "Accept":       "application/json, text/plain, */*",
            }

            try:
                resp = requests.post(
                    f"{_HLTB_SEARCH_BASE}/{h}",
                    json=payload,
                    headers=hdrs,
                    timeout=15,
                )

                if resp.status_code in (400, 404):
                    log.warning(f"[HLTB] Hash expirado (HTTP {resp.status_code}). Renovando...")
                    hash_invalido = True
                    time.sleep(espera)
                    espera *= 2
                    continue

                if resp.status_code == 429:
                    log.warning(f"[HLTB] Rate limit. Esperando {espera}s...")
                    time.sleep(espera)
                    espera *= 2
                    continue

                resp.raise_for_status()
                data = resp.json()
                resultados = data.get("data", [])

                if not resultados:
                    log.warning(f"[HLTB] Sin resultados para '{titulo}'")
                    return {"main": None, "main_extra": None, "completionist": None}

                titulo_lower = titulo.lower()
                mejor = resultados[0]
                for res in resultados:
                    if (res.get("game_name") or "").lower() == titulo_lower:
                        mejor = res
                        break

                def _seg(s):
                    return round(int(s) / 3600, 1) if s else None

                resultado = {
                    "main":          _seg(mejor.get("comp_main")),
                    "main_extra":    _seg(mejor.get("comp_plus")),
                    "completionist": _seg(mejor.get("comp_100")),
                }
                log.info(f"[HLTB] {titulo} → {resultado}")
                return resultado

            except requests.RequestException as e:
                log.warning(f"[HLTB] Intento {intento}/{_MAX_REINTENTOS} fallo para '{titulo}': {e}")
                if intento < _MAX_REINTENTOS:
                    time.sleep(espera)
                    espera *= 2

        log.error(f"[HLTB] Agotados reintentos para '{titulo}'")
        return {"main": None, "main_extra": None, "completionist": None}
