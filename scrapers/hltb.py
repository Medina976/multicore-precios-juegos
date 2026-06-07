"""
scrapers/hltb.py
Dueño: Brack

Extrae los tiempos de juego de HowLongToBeat.com usando su API
internal de búsqueda (JSON POST), sin necesidad de Selenium.

Mejora respecto a la versión anterior:
  - El hash del endpoint ya NO está hardcodeado. Se extrae dinámicamente
    de la página principal de HLTB la primera vez que se llama y se
    cachea en memoria para el resto de la sesión.
  - Si la extracción falla, se usa una lista de hashes conocidos como
    fallback antes de lanzar excepción.

Firma requerida por orquestador_brack.py:
    scraper.obtener_datos(juego: dict) -> dict

Retorna:
    {
        "main":           float | None,   # historia principal (horas)
        "main_extra":     float | None,   # historia + extras
        "completionist":  float | None,   # 100% completionista
    }
"""

import re
import time
import logging
import requests
from fake_useragent import UserAgent

log = logging.getLogger(__name__)

_ua = UserAgent()
_MAX_REINTENTOS = 3
_ESPERA_BASE = 2

_HLTB_HOME     = "https://howlongtobeat.com/"
_HLTB_SEARCH_BASE = "https://howlongtobeat.com/api/search"
_HLTB_REFERER  = "https://howlongtobeat.com/"

# Hashes conocidos como fallback (más reciente primero)
_HASHES_FALLBACK = [
    "dfh4bhy6ol2cru08ry4",   # verificado 2026-05
    "4e92b9cd0d735a14a9e3",  # verificado 2025-12
    "2c5d5f8e9a1b3c7f6d4e",  # verificado 2025-09
]

# Cache en memoria: se rellena la primera vez que se necesita
_hash_cache: str | None = None


def _extraer_hash_dinamico() -> str | None:
    """
    Descarga la página principal de HLTB y extrae el hash del endpoint
    de búsqueda desde el bundle de Next.js.

    El hash aparece en patrones como:
        fetch("/api/search/"+t+"/")  donde t = "<hash>"
    o directamente como:
        /api/search/dfh4bhy6ol2cru08ry4
    """
    try:
        headers = {
            "User-Agent": _ua.random,
            "Accept": "text/html",
        }
        resp = requests.get(_HLTB_HOME, headers=headers, timeout=12)
        resp.raise_for_status()
        html = resp.text

        # Patrón 1: hash directamente en una URL de la API
        m = re.search(r'/api/search/([a-z0-9]{10,30})', html)
        if m:
            log.debug(f"[HLTB] Hash extraído (patrón URL): {m.group(1)}")
            return m.group(1)

        # Patrón 2: asignación de variable en el JS bundle
        m = re.search(r'["\']([a-z0-9]{15,30})["\']\s*[,;]?\s*\/\*\s*hash\s*\*\/', html)
        if m:
            log.debug(f"[HLTB] Hash extraído (patrón comentario): {m.group(1)}")
            return m.group(1)

        # Patrón 3: buscar en scripts _next/static
        script_urls = re.findall(r'"(/_next/static/chunks/[^"]+\.js)"', html)
        for script_url in script_urls[:5]:  # revisar los primeros 5 chunks
            try:
                s = requests.get(
                    f"https://howlongtobeat.com{script_url}",
                    headers=headers,
                    timeout=8,
                )
                m = re.search(r'/api/search/([a-z0-9]{10,30})', s.text)
                if m:
                    log.debug(f"[HLTB] Hash extraído de chunk JS: {m.group(1)}")
                    return m.group(1)
            except requests.RequestException:
                continue

        log.warning("[HLTB] No se pudo extraer hash dinámico de la página")
        return None

    except requests.RequestException as e:
        log.warning(f"[HLTB] Error al descargar página para extraer hash: {e}")
        return None


def _obtener_hash() -> str:
    """
    Retorna el hash del endpoint de búsqueda de HLTB.
    Usa caché en memoria; si no hay caché, lo extrae dinámicamente;
    si falla, usa los hashes de fallback.
    """
    global _hash_cache
    if _hash_cache:
        return _hash_cache

    hash_dinamico = _extraer_hash_dinamico()
    if hash_dinamico:
        _hash_cache = hash_dinamico
        return _hash_cache

    # Fallback: usar el más reciente conocido
    log.warning("[HLTB] Usando hash de fallback (puede estar desactualizado)")
    _hash_cache = _HASHES_FALLBACK[0]
    return _hash_cache


def _reset_hash_cache():
    """Limpia el caché del hash para forzar re-extracción (útil si el hash expiró)."""
    global _hash_cache
    _hash_cache = None


class ScraperHLTB:
    """Scraper para tiempos de juego de HowLongToBeat."""

    def obtener_datos(self, juego: dict) -> dict:
        """
        Parámetros
        ----------
        juego : dict
            Debe contener 'titulo'.

        Retorna
        -------
        dict con claves 'main', 'main_extra', 'completionist' (float o None).
        """
        titulo = juego.get("titulo", "")
        if not titulo:
            raise ValueError(f"Juego {juego.get('id')} no tiene título")

        headers = {
            "User-Agent": _ua.random,
            "Referer": _HLTB_REFERER,
            "Origin": "https://howlongtobeat.com",
            "Content-Type": "application/json",
        }

        payload = {
            "searchType": "games",
            "searchTerms": titulo.split(),
            "searchPage": 1,
            "size": 3,
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

        espera = _ESPERA_BASE
        intentos_con_hash_actual = 0

        for intento in range(1, _MAX_REINTENTOS + 1):
            hash_actual = _obtener_hash()
            search_url = f"{_HLTB_SEARCH_BASE}/{hash_actual}"

            try:
                resp = requests.post(
                    search_url, json=payload, headers=headers, timeout=12
                )

                # Si el hash expiró, HLTB devuelve 400 o 404
                if resp.status_code in (400, 404):
                    log.warning(f"[HLTB] Hash '{hash_actual}' ya no es válido, re-extrayendo...")
                    _reset_hash_cache()
                    # Intentar con los hashes de fallback
                    for hash_fb in _HASHES_FALLBACK:
                        try:
                            r2 = requests.post(
                                f"{_HLTB_SEARCH_BASE}/{hash_fb}",
                                json=payload,
                                headers=headers,
                                timeout=12,
                            )
                            if r2.status_code == 200:
                                global _hash_cache
                                _hash_cache = hash_fb
                                log.info(f"[HLTB] Hash de fallback funcionó: {hash_fb}")
                                resp = r2
                                break
                        except requests.RequestException:
                            continue

                resp.raise_for_status()
                data = resp.json()

                resultados = data.get("data", [])
                if not resultados:
                    log.warning(f"[HLTB] Sin resultados para: {titulo}")
                    return {"main": None, "main_extra": None, "completionist": None}

                # Buscar el resultado con título más parecido
                titulo_lower = titulo.lower()
                r = resultados[0]
                for res in resultados:
                    nombre_res = (res.get("game_name") or "").lower()
                    if nombre_res == titulo_lower:
                        r = res
                        break

                def _horas(segundos) -> float | None:
                    """HLTB devuelve segundos; convertimos a horas con 1 decimal."""
                    if not segundos:
                        return None
                    return round(segundos / 3600, 1)

                resultado = {
                    "main":          _horas(r.get("comp_main")),
                    "main_extra":    _horas(r.get("comp_plus")),
                    "completionist": _horas(r.get("comp_100")),
                }

                log.info(f"[HLTB] {titulo} — {resultado}")
                return resultado

            except requests.RequestException as e:
                log.warning(
                    f"[HLTB] Intento {intento}/{_MAX_REINTENTOS} falló "
                    f"para '{titulo}': {e}"
                )
                if intento < _MAX_REINTENTOS:
                    time.sleep(espera)
                    espera *= 2
                else:
                    return {"main": None, "main_extra": None, "completionist": None}
