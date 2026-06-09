"""
scrapers/psn.py
Dueño: Brack

Obtiene precio de un juego en PlayStation Store (región US).

ESTRATEGIA (2 niveles):
    1) API de búsqueda pública de PlayStation:
       https://search.playstation.com/playstation/search/v1/universalSearch
       → Devuelve: name, conceptId, price (discountedPrice + basePrice), url

    2) Si la búsqueda falla → intentar con el conceptId guardado en BD
       consultando directamente:
       https://store.playstation.com/en-us/product/{conceptId}

NOTA sobre la estructura de precios de PSN:
    - basePrice      = precio normal de lista (siempre presente si el juego tiene precio)
    - discountedPrice = precio con descuento activo (null si no hay oferta)
    - Si ambos son null → el juego puede ser gratuito (price=0) o sin precio US.

Firma requerida:
    scraper.obtener_precio(juego: dict) -> dict
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

_PSN_SEARCH_URL = (
    "https://search.playstation.com/playstation/search/v1/universalSearch"
)
_PSN_BASE = "https://store.playstation.com"


def _limpiar_titulo(titulo: str) -> str:
    """Normaliza el título para mejorar el hit en la búsqueda de PSN."""
    for sufijo in [
        " - standard edition", " standard edition", " deluxe edition",
        " complete edition", " game of the year", " goty", " remastered",
        " definitive edition", " anniversary edition",
    ]:
        titulo = titulo.lower().replace(sufijo, "")
    return titulo.strip()


def _buscar_psn(titulo: str, headers: dict) -> dict | None:
    """
    Llama a la API de búsqueda de PlayStation.
    Retorna el hit más relevante o None.
    """
    try:
        resp = requests.get(
            _PSN_SEARCH_URL,
            params={
                "query":       titulo,
                "age":         99,
                "country":     "US",
                "language":    "en",
                "pageSize":    5,
                "pageOffset":  0,
                "domainCodes": "MFGames",
            },
            headers={
                **headers,
                "Origin":  "https://store.playstation.com",
                "Referer": "https://store.playstation.com/",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        hits = []
        for d in data.get("domainResponses", []):
            hits.extend(d.get("hits", []))

        if not hits:
            return None

        # Preferir coincidencia exacta de nombre
        titulo_lower = titulo.lower()
        for hit in hits:
            if (hit.get("name") or "").lower() == titulo_lower:
                return hit

        # Coincidencia parcial fuerte
        for hit in hits:
            hit_name = (hit.get("name") or "").lower()
            if titulo_lower in hit_name or hit_name in titulo_lower:
                return hit

        return hits[0]

    except requests.RequestException as e:
        log.debug(f"[PSN/Search] Fallo para '{titulo}': {e}")
        return None


def _extraer_precios_hit(hit: dict) -> tuple[float | None, float | None]:
    """
    Extrae (precio_actual, precio_regular) de un hit de la API de PSN.

    La API puede devolver el precio en distintas estructuras según la versión:
      Estructura A: hit.price.discountedPrice / hit.price.basePrice
      Estructura B: hit.prices[0].discountedPrice / hit.prices[0].basePrice
      Estructura C: hit.defaultSku.prices[0]...
    Probamos todas para ser robustos.
    """
    def _parse(v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            # PSN a veces devuelve el precio en centavos (e.g., 5999 = $59.99)
            val = float(v)
            if val > 999:  # claramente en centavos
                val /= 100
            return round(val, 2)
        # String como "$59.99" o "59.99"
        limpio = re.sub(r"[^\d.]", "", str(v))
        return round(float(limpio), 2) if limpio else None

    # Intentar estructura A
    price_obj = hit.get("price") or {}
    raw_discount = price_obj.get("discountedPrice")
    raw_regular  = price_obj.get("basePrice") or price_obj.get("amount") or price_obj.get("price")

    # Intentar estructura B si A no funcionó
    if raw_regular is None:
        prices_list = hit.get("prices") or []
        if prices_list:
            price_obj    = prices_list[0]
            raw_discount = price_obj.get("discountedPrice")
            raw_regular  = price_obj.get("basePrice") or price_obj.get("regularPrice")

    # Intentar campo directo "basePrice" en el hit raíz
    if raw_regular is None:
        raw_regular = hit.get("basePrice") or hit.get("lowestPrice")

    precio_regular = _parse(raw_regular)
    precio         = _parse(raw_discount) or precio_regular

    return precio, precio_regular


class ScraperPSN:
    """Scraper para precios de PlayStation Store (US)."""

    def obtener_precio(self, juego: dict) -> dict:
        titulo = juego.get("titulo", "")

        if not titulo:
            raise ValueError(f"Juego {juego.get('id')} sin título")

        headers = {
            "User-Agent": _ua.random,
            "Accept":     "application/json",
        }

        espera = _ESPERA_BASE
        for intento in range(1, _MAX_REINTENTOS + 1):
            try:
                # --- Intento 1: título original ---
                hit = _buscar_psn(titulo, headers)

                # --- Intento 2: título limpio ---
                if hit is None:
                    titulo_limpio = _limpiar_titulo(titulo)
                    if titulo_limpio != titulo.lower():
                        hit = _buscar_psn(titulo_limpio, headers)

                if hit is None:
                    log.warning(f"[PSN] Sin resultados para: '{titulo}'")
                    return {"precio": None, "precio_regular": None, "url_directa": None}

                precio, precio_regular = _extraer_precios_hit(hit)

                # URL directa
                url_rel     = hit.get("url") or ""
                url_directa = (
                    f"{_PSN_BASE}{url_rel}"
                    if url_rel and not url_rel.startswith("http")
                    else url_rel or None
                )

                log.info(
                    f"[PSN] {titulo} → ${precio} "
                    f"(regular: ${precio_regular}) | {url_directa}"
                )
                return {
                    "precio":         precio,
                    "precio_regular": precio_regular,
                    "url_directa":    url_directa,
                }

            except requests.RequestException as e:
                log.warning(
                    f"[PSN] Intento {intento}/{_MAX_REINTENTOS} falló "
                    f"para '{titulo}': {e}"
                )
                if intento < _MAX_REINTENTOS:
                    time.sleep(espera)
                    espera *= 2
                else:
                    return {"precio": None, "precio_regular": None, "url_directa": None}

        return {"precio": None, "precio_regular": None, "url_directa": None}
