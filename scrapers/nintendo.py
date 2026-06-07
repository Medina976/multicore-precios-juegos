"""
scrapers/nintendo.py
Dueño: Brack

Obtiene el precio actual de un juego en Nintendo eShop (región US).

Estrategia:
  1) Intenta con la API de Algolia de Nintendo (credenciales actualizadas).
  2) Si Algolia falla (403/timeout), cae a la API pública de búsqueda
     del eShop US: https://searching.nintendo.com/

Firma requerida por orquestador_brack.py:
    scraper.obtener_precio(juego: dict) -> dict

Retorna:
    {
        "precio": float | None,
        "precio_regular": float | None,
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

# ── Algolia (Nintendo.com) ────────────────────────────────────────────────────
# Credenciales públicas obtenidas del JS de nintendo.com (junio 2026)
_ALGOLIA_APP_ID  = "U3B6GR4UA3"
_ALGOLIA_API_KEY = "9a20c93440cf63cf1a7008d75f7438bf"
_ALGOLIA_INDEX   = "noa_aem_game_en_us_1"
_ALGOLIA_URL     = (
    f"https://{_ALGOLIA_APP_ID.lower()}-dsn.algolia.net"
    f"/1/indexes/{_ALGOLIA_INDEX}/query"
)

# ── Fallback: API de búsqueda pública del eShop US ────────────────────────────
_ESHOP_SEARCH_URL = "https://searching.nintendo.com/en/games/search.json"
# Si el anterior también falla, usamos el endpoint de Nintendo API oficial
_NINTENDO_API_URL = "https://api.ec.nintendo.com/v1/price"


def _limpiar_precio(valor) -> float | None:
    """Convierte '\$59.99' o 59.99 a float. Retorna None si no se puede."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    limpio = re.sub(r"[^\d.]", "", str(valor))
    return float(limpio) if limpio else None


def _buscar_algolia(query: str, headers: dict) -> dict | None:
    """
    Busca en el índice de Algolia de Nintendo.
    Retorna el primer hit o None si falla / no hay resultados.
    """
    payload = {
        "params": (
            f"query={requests.utils.quote(query)}"
            "&hitsPerPage=3"
            "&attributesToRetrieve=title,salePrice,msrp,percentOff,nsuid,url"
        )
    }
    try:
        resp = requests.post(
            _ALGOLIA_URL,
            json=payload,
            headers={
                **headers,
                "X-Algolia-Application-Id": _ALGOLIA_APP_ID,
                "X-Algolia-API-Key": _ALGOLIA_API_KEY,
            },
            timeout=10,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
        return hits[0] if hits else None
    except requests.RequestException as e:
        log.debug(f"[Nintendo/Algolia] Fallo para '{query}': {e}")
        return None


def _buscar_eshop(query: str, headers: dict) -> dict | None:
    """
    Fallback: búsqueda en el eShop US público.
    Retorna un dict con 'salePrice' y 'msrp', o None.
    """
    try:
        resp = requests.get(
            _ESHOP_SEARCH_URL,
            params={"q": query, "limit": 3, "locale": "en_US"},
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("hits", data.get("results", []))
        if not items:
            return None
        item = items[0]
        # El eShop devuelve los precios en distintos campos según la versión
        precio = (
            item.get("salePrice")
            or item.get("lowestPrice")
            or item.get("price")
        )
        msrp = item.get("msrp") or item.get("regularPrice") or precio
        return {"salePrice": precio, "msrp": msrp}
    except requests.RequestException as e:
        log.debug(f"[Nintendo/eShop] Fallo para '{query}': {e}")
        return None


class ScraperNintendo:
    """Scraper para precios de Nintendo eShop (US)."""

    def obtener_precio(self, juego: dict) -> dict:
        """
        Parámetros
        ----------
        juego : dict
            Debe contener 'titulo' y opcionalmente 'urls_tiendas.nintendo'.

        Retorna
        -------
        dict con claves 'precio' y 'precio_regular' (float o None).
        """
        url_tienda = (juego.get("urls_tiendas") or {}).get("nintendo")
        titulo = juego.get("titulo", "")

        if not url_tienda and not titulo:
            raise ValueError(f"Juego {juego.get('id')} sin URL de Nintendo ni título")

        # Construir query: preferir slug de la URL limpiado, o el título
        match = re.search(r"/products/([\w-]+)/?$", url_tienda or "")
        query = match.group(1).replace("-", " ") if match else titulo

        headers = {
            "User-Agent": _ua.random,
            "Accept": "application/json",
            "Referer": "https://www.nintendo.com/",
        }

        espera = _ESPERA_BASE
        for intento in range(1, _MAX_REINTENTOS + 1):
            try:
                # Intento 1: Algolia
                hit = _buscar_algolia(query, headers)

                # Intento 2: eShop como fallback
                if hit is None:
                    log.debug(f"[Nintendo] Algolia sin resultado para '{query}', probando eShop...")
                    hit = _buscar_eshop(titulo, headers)

                if hit is None:
                    log.warning(f"[Nintendo] Sin resultados en ninguna fuente para: {titulo}")
                    return {"precio": None, "precio_regular": None}

                precio = _limpiar_precio(hit.get("salePrice"))
                precio_regular = _limpiar_precio(hit.get("msrp")) or precio

                log.info(
                    f"[Nintendo] {titulo} — ${precio} (regular: ${precio_regular})"
                )
                return {"precio": precio, "precio_regular": precio_regular}

            except requests.RequestException as e:
                log.warning(
                    f"[Nintendo] Intento {intento}/{_MAX_REINTENTOS} falló "
                    f"para '{titulo}': {e}"
                )
                if intento < _MAX_REINTENTOS:
                    time.sleep(espera)
                    espera *= 2
                else:
                    return {"precio": None, "precio_regular": None}
