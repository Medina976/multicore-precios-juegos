"""
scrapers/hltb.py
Dueño: Brack

Obtiene los tiempos de completado de un juego desde HowLongToBeat.

ESTRATEGIA:
    HowLongToBeat no tiene API pública, pero expone un endpoint de búsqueda
    que usa la propia web:

    POST https://howlongtobeat.com/api/search/{token}
    Body (JSON):
      {
        "searchType": "games",
        "searchTerms": ["doom", "eternal"],
        "searchPage": 1,
        "size": 5,
        "searchOptions": { ... }
      }

    El token se extrae del HTML/JS de la página principal (cambia con deploys).
    Respuesta: lista de juegos con comp_main, comp_plus, comp_100 en segundos.

Firma requerida:
    scraper.obtener_datos(juego: dict) -> dict

Retorna:
    {
        "main":          float | None,   (horas, redondeado 1 decimal)
        "main_extra":    float | None,
        "completionist": float | None,
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
_ESPERA_BASE    = 2

_HLTB_BASE   = "https://howlongtobeat.com"
_HLTB_SEARCH = "{base}/api/search/{token}"

# Cache del token (se renueva si la respuesta da 404/403)
_token_cache: str | None = None


def _obtener_token() -> str:
    """
    Extrae el token de busqueda del JS de la pagina de HLTB.
    El token es un hash que aparece en el bundle JS de Next.js:
      /api/search/<hash>
    """
    global _token_cache

    resp = requests.get(
        _HLTB_BASE,
        headers={
            "User-Agent": _ua.random,
            "Accept": "text/html,application/xhtml+xml,*/*",
        },
        timeout=12,
    )
    resp.raise_for_status()

    # Buscar el src del bundle JS principal de Next.js
    js_srcs = re.findall(r'src="(/_next/static/chunks/[^"]+\.js)"', resp.text)

    # Revisar los primeros archivos JS hasta encontrar el token
    for src in js_srcs[:8]:
        try:
            js_resp = requests.get(
                f"{_HLTB_BASE}{src}",
                headers={"User-Agent": _ua.random},
                timeout=10,
            )
            # El token aparece como: "/api/search/" + "<hash>"
            m = re.search(
                r'["\x60]/api/search/([a-zA-Z0-9]{8,})["\x60/]',
                js_resp.text,
            )
            if m:
                _token_cache = m.group(1)
                log.debug(f"[HLTB] Token encontrado: {_token_cache}")
                return _token_cache
        except requests.RequestException:
            continue

    raise RuntimeError("[HLTB] No se pudo extraer el token de busqueda")


def _buscar_hltb(titulo: str, token: str) -> dict | None:
    """
    Hace POST a la API de busqueda de HLTB.
    Retorna el juego mas relevante o None.
    """
    palabras = titulo.split()
    payload = {
        "searchType":  "games",
        "searchTerms": palabras,
        "searchPage":  1,
        "size":        5,
        "searchOptions": {
            "games": {
                "userId":        0,
                "platform":      "",
                "sortCategory":  "popular",
                "rangeCategory": "main",
                "rangeTime":     {"min": None, "max": None},
                "gameplay":      {"perspective": "", "flow": "", "genre": "", "subGenre": ""},
                "rangeYear":     {"min": "", "max": ""},
                "modifier":      "",
            },
            "users":  {"sortCategory": "postcount"},
            "lists":  {"sortCategory": "follows"},
            "filter": "",
            "sort":   0,
            "randomizer": 0,
        },
    }
    resp = requests.post(
        _HLTB_SEARCH.format(base=_HLTB_BASE, token=token),
        json=payload,
        headers={
            "User-Agent":   _ua.random,
            "Content-Type": "application/json",
            "Origin":       _HLTB_BASE,
            "Referer":      f"{_HLTB_BASE}/",
        },
        timeout=12,
    )
    resp.raise_for_status()

    data    = resp.json()
    results = data.get("data") or []
    if not results:
        return None

    # Preferir coincidencia exacta de nombre
    titulo_lower = titulo.lower()
    for r in results:
        if (r.get("game_name") or "").lower() == titulo_lower:
            return r

    # Coincidencia parcial fuerte
    for r in results:
        nombre = (r.get("game_name") or "").lower()
        if titulo_lower in nombre or nombre in titulo_lower:
            return r

    return results[0]


def _segundos_a_horas(segundos) -> float | None:
    """Convierte segundos (int) a horas con 1 decimal. 0 -> None."""
    if not segundos or not isinstance(segundos, (int, float)):
        return None
    horas = segundos / 3600
    return round(horas, 1) if horas > 0 else None


class ScraperHLTB:
    """Scraper para tiempos de completado de HowLongToBeat."""

    def obtener_datos(self, juego: dict) -> dict:
        titulo = juego.get("titulo", "")

        if not titulo:
            raise ValueError(f"Juego {juego.get('id')} sin titulo")

        global _token_cache
        vacio  = {"main": None, "main_extra": None, "completionist": None}
        espera = _ESPERA_BASE

        for intento in range(1, _MAX_REINTENTOS + 1):
            try:
                # Obtener o reutilizar el token
                if _token_cache is None:
                    _token_cache = _obtener_token()

                resultado = _buscar_hltb(titulo, _token_cache)

                # Reintentar con titulo corto si no hay resultados
                if resultado is None:
                    titulo_corto = titulo.split(":")[0].split(" - ")[0].strip()
                    if titulo_corto != titulo:
                        resultado = _buscar_hltb(titulo_corto, _token_cache)

                if resultado is None:
                    log.warning(f"[HLTB] Sin resultados para: '{titulo}'")
                    return vacio

                main          = _segundos_a_horas(resultado.get("comp_main"))
                main_extra    = _segundos_a_horas(resultado.get("comp_plus"))
                completionist = _segundos_a_horas(resultado.get("comp_100"))

                log.info(
                    f"[HLTB] {titulo} -> "
                    f"Main: {main}h | Main+Extra: {main_extra}h | 100%: {completionist}h"
                )
                return {
                    "main":          main,
                    "main_extra":    main_extra,
                    "completionist": completionist,
                }

            except requests.HTTPError as e:
                # Si el token expiro, renovarlo en el siguiente intento
                if e.response is not None and e.response.status_code in (403, 404):
                    log.warning("[HLTB] Token expirado, renovando...")
                    _token_cache = None
                    time.sleep(espera)
                    espera *= 2
                else:
                    if intento < _MAX_REINTENTOS:
                        time.sleep(espera)
                        espera *= 2
                    else:
                        return vacio

            except Exception as e:
                log.warning(
                    f"[HLTB] Intento {intento}/{_MAX_REINTENTOS} fallo "
                    f"para '{titulo}': {e}"
                )
                if intento < _MAX_REINTENTOS:
                    time.sleep(espera)
                    espera *= 2
                else:
                    return vacio

        return vacio
