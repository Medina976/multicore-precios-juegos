"""
scrapers/nintendo.py
Dueño: Brack

Obtiene el precio actual de un juego en Nintendo eShop (región US)
usando la API pública de Algolia que usa el sitio de Nintendo.

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

# Credenciales públicas de Algolia que usa nintendo.com
_ALGOLIA_APP_ID = "U3B6GR4UA3"
_ALGOLIA_API_KEY = "9a20c93440cf63cf1a7008d75f7438bf"
_ALGOLIA_INDEX = "noa_aem_game_en_us"
_ALGOLIA_URL = (
    f"https://{_ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{_ALGOLIA_INDEX}/query"
)


def _extraer_nsuid(url: str) -> str | None:
    """Intenta extraer el nsuid del path de la URL de Nintendo.
    Ejemplo: https://www.nintendo.com/us/store/products/zelda-tears-of-the-kingdom-switch/
    Si no se puede, retorna None y se buscará por título.
    """
    match = re.search(r"/products/([\w-]+)/?", url or "")
    return match.group(1) if match else None


class ScraperNintendo:
    """Scraper para precios de Nintendo eShop (US)."""

    def obtener_precio(self, juego: dict) -> dict:
        """
        Parámetros
        ----------
        juego : dict
            Debe contener 'titulo' y 'urls_tiendas' con sub-campo 'nintendo'.

        Retorna
        -------
        dict con claves 'precio' y 'precio_regular' (float o None).
        """
        url_tienda = (juego.get("urls_tiendas") or {}).get("nintendo")
        titulo = juego.get("titulo", "")

        if not url_tienda and not titulo:
            raise ValueError(f"Juego {juego.get('id')} no tiene URL de Nintendo ni título")

        slug = _extraer_nsuid(url_tienda) if url_tienda else None
        # Usamos el slug o el título para buscar en Algolia
        query = slug.replace("-", " ") if slug else titulo

        headers = {
            "User-Agent": _ua.random,
            "X-Algolia-Application-Id": _ALGOLIA_APP_ID,
            "X-Algolia-API-Key": _ALGOLIA_API_KEY,
            "Content-Type": "application/json",
        }

        payload = {
            "params": f"query={requests.utils.quote(query)}&hitsPerPage=1&attributesToRetrieve=title,salePrice,msrp,percentOff,nsuid",
        }

        espera = _ESPERA_BASE
        for intento in range(1, _MAX_REINTENTOS + 1):
            try:
                resp = requests.post(
                    _ALGOLIA_URL, json=payload, headers=headers, timeout=10
                )
                resp.raise_for_status()
                data = resp.json()

                hits = data.get("hits", [])
                if not hits:
                    log.warning(f"[Nintendo] Sin resultados para: {query}")
                    return {"precio": None, "precio_regular": None}

                hit = hits[0]
                precio = hit.get("salePrice")          # precio actual (puede ser con descuento)
                precio_regular = hit.get("msrp")        # precio sugerido de fábrica

                # Convertir a float si vienen como string ("$59.99" → 59.99)
                if isinstance(precio, str):
                    precio = float(re.sub(r"[^\d.]", "", precio) or 0)
                if isinstance(precio_regular, str):
                    precio_regular = float(re.sub(r"[^\d.]", "", precio_regular) or 0)

                log.info(
                    f"[Nintendo] {juego.get('titulo')} — ${precio} "
                    f"(regular: ${precio_regular})"
                )
                return {"precio": precio, "precio_regular": precio_regular}

            except requests.RequestException as e:
                log.warning(
                    f"[Nintendo] Intento {intento}/{_MAX_REINTENTOS} falló "
                    f"para juego {juego.get('id')}: {e}"
                )
                if intento < _MAX_REINTENTOS:
                    time.sleep(espera)
                    espera *= 2
                else:
                    raise
