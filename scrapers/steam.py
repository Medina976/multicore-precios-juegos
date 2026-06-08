"""
scrapers/steam.py
Dueño: Brack

Obtiene el precio actual de un juego en Steam usando la Storefront API
pública de Steam (no requiere API key).

MEJORA CLAVE: cuando el scraper encuentra el AppID (ya sea desde la URL
o buscando por título), actualiza la URL de la tienda en la BD con el
formato directo /app/{appid}/ para que el frontend muestre el link correcto.

Firma requerida por orquestador_brack.py:
    scraper.obtener_precio(juego: dict) -> dict

Retorna:
    {
        "precio":        float | None,
        "precio_regular": float | None,
        "url_directa":   str | None,   <- URL /app/{id}/... para guardar en BD
    }
"""

import re
import time
import logging
import requests
from fake_useragent import UserAgent

log = logging.getLogger(__name__)
_ua = UserAgent()

_MAX_REINTENTOS  = 3
_ESPERA_BASE     = 2
_THROTTLE_DELAY  = 0.5

_STEAM_SEARCH_API     = "https://store.steampowered.com/api/storesearch/"
_STEAM_APPDETAILS_API = "https://store.steampowered.com/api/appdetails"


def _extraer_appid(url: str) -> str | None:
    m = re.search(r"/app/(\d+)", url or "")
    return m.group(1) if m else None


def _buscar_appid_por_titulo(titulo: str, timeout: int = 8) -> tuple[str | None, str | None]:
    """
    Busca el juego en Steam y retorna (appid, nombre_encontrado).
    Con el nombre encontrado podemos construir la URL directa.
    """
    if not titulo:
        return None, None
    try:
        headers = {"User-Agent": _ua.random}
        resp = requests.get(
            _STEAM_SEARCH_API,
            params={"term": titulo, "cc": "us", "l": "en"},
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            return None, None

        titulo_lower = titulo.lower()
        for item in items:
            if (item.get("name") or "").lower() == titulo_lower:
                nombre = item["name"].replace(" ", "_")
                return str(item["id"]), nombre

        # Sin coincidencia exacta → primer resultado
        item = items[0]
        nombre = (item.get("name") or titulo).replace(" ", "_")
        return str(item["id"]), nombre

    except requests.RequestException as e:
        log.debug(f"[Steam] Búsqueda falló para '{titulo}': {e}")
        return None, None


class ScraperSteam:
    """Scraper para precios de Steam."""

    def obtener_precio(self, juego: dict) -> dict:
        url_tienda = (juego.get("urls_tiendas") or {}).get("steam")
        titulo     = juego.get("titulo", "")

        appid      = _extraer_appid(url_tienda) if url_tienda else None
        url_directa = None

        if not appid:
            appid, nombre_steam = _buscar_appid_por_titulo(titulo)
            if appid:
                # Construir URL directa para guardar en BD
                url_directa = f"https://store.steampowered.com/app/{appid}/{nombre_steam or titulo.replace(' ', '_')}/"
                log.info(f"[Steam] AppID '{appid}' por búsqueda. URL directa: {url_directa}")
        else:
            # Ya teníamos URL directa en BD
            url_directa = url_tienda

        if not appid:
            log.warning(f"[Steam] No se pudo identificar AppID para '{titulo}'")
            return {"precio": None, "precio_regular": None, "url_directa": None}

        espera = _ESPERA_BASE
        for intento in range(1, _MAX_REINTENTOS + 1):
            try:
                resp = requests.get(
                    _STEAM_APPDETAILS_API,
                    params={"appids": appid, "cc": "us", "filters": "price_overview"},
                    headers={"User-Agent": _ua.random},
                    timeout=10,
                )

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", espera * 2))
                    log.warning(f"[Steam] Rate limit (429), esperando {retry_after}s...")
                    time.sleep(retry_after)
                    continue

                resp.raise_for_status()
                data = resp.json()
                info = data.get(str(appid), {})

                if not info.get("success"):
                    return {"precio": None, "precio_regular": None, "url_directa": url_directa}

                po = info.get("data", {}).get("price_overview")
                if po is None:
                    return {"precio": 0.0, "precio_regular": 0.0, "url_directa": url_directa}

                precio         = po["final"]   / 100
                precio_regular = po["initial"] / 100

                log.info(f"[Steam] {titulo} (id={appid}) — ${precio:.2f} (reg ${precio_regular:.2f})")
                time.sleep(_THROTTLE_DELAY)

                return {
                    "precio":        precio,
                    "precio_regular": precio_regular,
                    "url_directa":   url_directa,
                }

            except requests.RequestException as e:
                log.warning(f"[Steam] Intento {intento}/{_MAX_REINTENTOS} falló para '{titulo}': {e}")
                if intento < _MAX_REINTENTOS:
                    time.sleep(espera)
                    espera *= 2
                else:
                    raise

        return {"precio": None, "precio_regular": None, "url_directa": None}
