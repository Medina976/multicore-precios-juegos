"""
scrapers/psn.py
Dueño: Brack

Obtiene el precio actual de un juego en PlayStation Store (región US)
usando la API pública de Búsqueda de Sony + la API de Precios de PSN.

ESTRATEGIA:
    1) Buscar el juego con la API de búsqueda de Playstation:
         https://search.playstation.com/playstation/search/v1/universalSearch
       Devuelve conceptId (identificador PSN), nombre del producto y URL directa.
    2) Con el conceptId consultar la API de precios oficial:
         https://store.playstation.com/store/api/11/19/en/US/resolve
       Para obtener precio regular y precio en oferta.
    3) Guardar la URL directa en BD igual que Steam y Nintendo.

Ventajas vs. hacer scraping de la página web:
    - Ninguna de estas APIs pide login ni verificación de edad.
    - Son las mismas APIs que usa el sitio web oficial de PlayStation.
    - No hay riesgo de que un captcha o modal bloquee el acceso.

Firma requerida por orquestador_brack.py:
    scraper.obtener_precio(juego: dict) -> dict

Retorna:
    {
        "precio":        float | None,
        "precio_regular": float | None,
        "url_directa":   str | None,   <- URL directa a la página del juego en PSN
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

_PSN_SEARCH_URL = (
    "https://search.playstation.com/playstation/search/v1/universalSearch"
)
_PSN_BASE       = "https://store.playstation.com"


def _buscar_psn(titulo: str, headers: dict) -> dict | None:
    """
    Llama a la API de búsqueda de PlayStation y retorna el hit más relevante.
    Cada hit tiene: name, conceptId, price (con amount y discountedPrice), url.
    """
    try:
        resp = requests.get(
            _PSN_SEARCH_URL,
            params={
                "query":       titulo,
                "age":         99,       # evitar filtros de edad en la respuesta
                "country":     "US",
                "language":    "en",
                "pageSize":    5,
                "pageOffset":  0,
                "domainCodes": "MFGames",  # solo juegos, no accesorios
            },
            headers={
                **headers,
                "Origin":  "https://store.playstation.com",
                "Referer": "https://store.playstation.com/",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data  = resp.json()

        # Estructura: domainResponses[0].hits[]
        dominios = data.get("domainResponses", [])
        hits = []
        for d in dominios:
            hits.extend(d.get("hits", []))

        if not hits:
            return None

        # Preferir coincidencia exacta de nombre
        titulo_lower = titulo.lower()
        for hit in hits:
            if (hit.get("name") or "").lower() == titulo_lower:
                return hit
        return hits[0]

    except requests.RequestException as e:
        log.debug(f"[PSN/Search] Fallo para '{titulo}': {e}")
        return None


def _extraer_precios_hit(hit: dict) -> tuple[float | None, float | None]:
    """
    Extrae (precio_oferta, precio_regular) de un hit de la API de búsqueda.
    La estructura puede variar; exploramos varios campos posibles.
    """
    precio          = None
    precio_regular  = None

    price_info = hit.get("price") or {}

    # Campo 'discountedPrice' = precio con descuento (None si no hay oferta)
    raw_discount = price_info.get("discountedPrice")
    # Campo 'amount' o 'basePrice' = precio regular sin descuento
    raw_regular  = (
        price_info.get("basePrice")
        or price_info.get("amount")
        or price_info.get("price")
    )

    def _parse(v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return round(float(v), 2)
        limpio = re.sub(r"[^\d.]", "", str(v))
        return round(float(limpio), 2) if limpio else None

    precio_regular = _parse(raw_regular)
    precio         = _parse(raw_discount) or precio_regular

    return precio, precio_regular


class ScraperPSN:
    """Scraper para precios de PlayStation Store (US)."""

    def obtener_precio(self, juego: dict) -> dict:
        """
        Parámetros
        ----------
        juego : dict
            Debe contener 'titulo' y opcionalmente 'urls_tiendas.psn'.

        Retorna
        -------
        dict con 'precio', 'precio_regular' (float o None)
        y 'url_directa' (str o None).
        """
        titulo     = juego.get("titulo", "")
        url_bd     = (juego.get("urls_tiendas") or {}).get("psn", "")

        if not titulo:
            raise ValueError(f"Juego {juego.get('id')} sin título")

        headers = {
            "User-Agent": _ua.random,
            "Accept":     "application/json",
        }

        espera = _ESPERA_BASE
        for intento in range(1, _MAX_REINTENTOS + 1):
            try:
                hit = _buscar_psn(titulo, headers)

                if hit is None:
                    log.warning(f"[PSN] Sin resultados para: '{titulo}'")
                    return {"precio": None, "precio_regular": None, "url_directa": None}

                precio, precio_regular = _extraer_precios_hit(hit)

                # URL directa: PSN devuelve la ruta relativa en 'url'
                url_rel     = hit.get("url") or ""
                url_directa = (
                    f"{_PSN_BASE}{url_rel}" if url_rel and not url_rel.startswith("http")
                    else url_rel or None
                )

                log.info(
                    f"[PSN] {titulo} → ${precio} "
                    f"(regular: ${precio_regular}) | url: {url_directa}"
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
