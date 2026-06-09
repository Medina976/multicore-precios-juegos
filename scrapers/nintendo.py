"""
scrapers/nintendo.py
Dueño: Brack

Obtiene precio de un juego en Nintendo eShop US.

ESTRATEGIA (3 niveles):
    1) API de Algolia de Nintendo (la misma que usa el buscador oficial):
       Endpoint: https://u3b6gr4ua3-dsn.algolia.net/1/indexes/*/queries
       → Devuelve: salePrice, msrp, nsuid, url (/us/store/products/...)
         salePrice = precio con descuento activo (null si no hay oferta)
         msrp = precio de lista oficial sin descuento

    2) Si Algolia no trae precio → API de precios de Nintendo por NSUID:
       https://api.ec.nintendo.com/v1/price?country=US&lang=en&ids={nsuid}
       (misma API que usa la app oficial, sin login, sin verificación de edad)

    3) Si tampoco hay NSUID → segunda búsqueda en Algolia con título simplificado.

Firma requerida por orquestador_brack.py:
    scraper.obtener_precio(juego: dict) -> dict

Retorna:
    {
        "precio":         float | None,
        "precio_regular": float | None,
        "url_directa":    str | None,
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

# Algolia de Nintendo (app_id y api_key son públicas, usadas por nintendo.com)
_ALGOLIA_URL    = "https://u3b6gr4ua3-dsn.algolia.net/1/indexes/*/queries"
_ALGOLIA_APP_ID = "U3B6GR4UA3"
_ALGOLIA_API_KEY = "c4da8be7fd29f0f5bfa42920b0a99dc7"
_ALGOLIA_INDEX  = "noa_aem_game_en_us"

# API oficial de precios de Nintendo
_PRICE_API = "https://api.ec.nintendo.com/v1/price"


def _limpiar_titulo(titulo: str) -> str:
    """Quita ediciones y subtítulos para mejorar coincidencia en búsqueda."""
    for sufijo in [
        " - standard edition", " standard edition", " deluxe edition",
        " complete edition", " game of the year", " goty", " remastered",
        " definitive edition", " anniversary edition", " digital edition",
    ]:
        titulo = titulo.lower().replace(sufijo, "")
    return titulo.strip()


def _buscar_algolia(titulo: str) -> dict | None:
    """
    Hace POST a la API de Algolia de Nintendo.
    Devuelve el hit más relevante o None.
    """
    try:
        payload = {
            "requests": [{
                "indexName": _ALGOLIA_INDEX,
                "params": (
                    f"query={requests.utils.quote(titulo)}"
                    "&hitsPerPage=5"
                    "&filters=categories%3AGames"  # solo juegos
                    "&attributesToRetrieve=title,salePrice,msrp,nsuid,url,productCode"
                ),
            }]
        }
        resp = requests.post(
            _ALGOLIA_URL,
            json=payload,
            headers={
                "X-Algolia-Application-Id": _ALGOLIA_APP_ID,
                "X-Algolia-API-Key":        _ALGOLIA_API_KEY,
                "User-Agent":               _ua.random,
                "Content-Type":             "application/json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        hits = resp.json()["results"][0].get("hits", [])
        if not hits:
            return None

        # Preferir coincidencia exacta de título
        titulo_lower = titulo.lower()
        for hit in hits:
            if (hit.get("title") or "").lower() == titulo_lower:
                return hit

        # Coincidencia parcial fuerte
        for hit in hits:
            hit_title = (hit.get("title") or "").lower()
            if titulo_lower in hit_title or hit_title in titulo_lower:
                return hit

        return hits[0]

    except (requests.RequestException, KeyError, IndexError) as e:
        log.debug(f"[Nintendo/Algolia] Fallo para '{titulo}': {e}")
        return None


def _precio_por_nsuid(nsuid: str) -> tuple[float | None, float | None]:
    """
    Consulta la API oficial de precios de Nintendo usando el NSUID.
    Retorna (precio_actual, precio_regular).
    """
    try:
        resp = requests.get(
            _PRICE_API,
            params={"country": "US", "lang": "en", "ids": nsuid},
            headers={"User-Agent": _ua.random},
            timeout=10,
        )
        resp.raise_for_status()
        prices_data = resp.json().get("prices", [])
        if not prices_data:
            return None, None

        p = prices_data[0]
        regular_raw    = p.get("regular_price",    {}).get("raw_value")
        discounted_raw = p.get("discount_price",   {}).get("raw_value")

        def _parse(v):
            if v is None:
                return None
            return round(float(str(v).replace(",", "")), 2)

        precio_regular = _parse(regular_raw)
        precio         = _parse(discounted_raw) or precio_regular
        return precio, precio_regular

    except (requests.RequestException, ValueError, KeyError) as e:
        log.debug(f"[Nintendo/PriceAPI] Fallo para nsuid={nsuid}: {e}")
        return None, None


def _extraer_precios_hit(hit: dict) -> tuple[float | None, float | None]:
    """
    Extrae (precio_oferta, precio_regular) del hit de Algolia.
    salePrice puede ser None incluso aunque el juego tenga precio.
    """
    def _parse(v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return round(float(v), 2)
        limpio = re.sub(r"[^\d.]", "", str(v))
        return round(float(limpio), 2) if limpio else None

    sale    = _parse(hit.get("salePrice"))
    regular = _parse(hit.get("msrp"))

    # Si hay salePrice y msrp, salePrice ES el precio con descuento
    # Si solo hay msrp, ese es el precio normal (sin oferta)
    if sale is not None and regular is not None:
        return sale, regular
    if regular is not None:
        return regular, regular
    if sale is not None:
        return sale, sale
    return None, None


class ScraperNintendo:
    """Scraper para precios de Nintendo eShop (US)."""

    def obtener_precio(self, juego: dict) -> dict:
        titulo    = juego.get("titulo", "")
        url_bd    = (juego.get("urls_tiendas") or {}).get("nintendo", "")

        if not titulo:
            raise ValueError(f"Juego {juego.get('id')} sin título")

        espera = _ESPERA_BASE
        for intento in range(1, _MAX_REINTENTOS + 1):
            try:
                # --- Intento 1: título original ---
                hit = _buscar_algolia(titulo)

                # --- Intento 2: título limpio (sin "Deluxe Edition", etc.) ---
                if hit is None:
                    titulo_limpio = _limpiar_titulo(titulo)
                    if titulo_limpio != titulo.lower():
                        hit = _buscar_algolia(titulo_limpio)

                if hit is None:
                    log.warning(f"[Nintendo] Sin resultados en Algolia para: '{titulo}'")
                    return {"precio": None, "precio_regular": None, "url_directa": None}

                precio, precio_regular = _extraer_precios_hit(hit)

                # Si Algolia no trajo precio, intentar con NSUID
                nsuid = str(hit.get("nsuid") or "").strip()
                if precio is None and nsuid:
                    log.debug(f"[Nintendo] Sin precio en Algolia, consultando NSUID={nsuid}")
                    precio, precio_regular = _precio_por_nsuid(nsuid)

                # URL directa del producto
                url_rel     = hit.get("url") or ""
                url_directa = (
                    f"https://www.nintendo.com{url_rel}"
                    if url_rel and not url_rel.startswith("http")
                    else url_rel or None
                )

                log.info(
                    f"[Nintendo] {titulo} → ${precio} "
                    f"(regular: ${precio_regular}) | {url_directa}"
                )
                return {
                    "precio":         precio,
                    "precio_regular": precio_regular,
                    "url_directa":    url_directa,
                }

            except requests.RequestException as e:
                log.warning(
                    f"[Nintendo] Intento {intento}/{_MAX_REINTENTOS} falló "
                    f"para '{titulo}': {e}"
                )
                if intento < _MAX_REINTENTOS:
                    time.sleep(espera)
                    espera *= 2
                else:
                    return {"precio": None, "precio_regular": None, "url_directa": None}

        return {"precio": None, "precio_regular": None, "url_directa": None}
