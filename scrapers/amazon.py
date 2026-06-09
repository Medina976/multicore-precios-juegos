"""
scrapers/amazon.py
Dueño: Brack

Obtiene el precio actual de un juego en Amazon.com usando la API
pública de búsqueda de productos de Amazon (sin API key).

ESTRATEGIA:
    1) Consultar la API de sugerencias/búsqueda de Amazon:
         https://completion.amazon.com/api/2017/suggestions
       Para obtener el ASIN (identificador único de Amazon) del producto.
    2) Con el ASIN construir la URL directa del producto:
         https://www.amazon.com/dp/{ASIN}
    3) Consultar el precio con la API de ofertas de Amazon:
         https://api.amazon.com/paapi5/getItems  (Product Advertising API)
       NOTA: La PA API requiere credenciales. Como alternativa pública
       hacemos un GET a la página del producto con headers de bot-bypass
       y extraemos el precio con BeautifulSoup del JSON-LD incrustado.

Por qué JSON-LD y no scraping del HTML crudo:
    - Amazon incrusta los datos de precio en un bloque <script type="application/ld+json">
      que es más estable que los selectores CSS del HTML (que cambian con A/B tests).
    - El JSON-LD sigue el estándar schema.org/Product y siempre tiene 'offers.price'.

Firma requerida por orquestador_brack.py:
    scraper.obtener_precio(juego: dict) -> dict

Retorna:
    {
        "precio":        float | None,
        "precio_regular": float | None,
        "url_directa":   str | None,   <- https://www.amazon.com/dp/{ASIN}
    }
"""

import re
import json
import time
import logging
import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

log = logging.getLogger(__name__)

_ua = UserAgent()
_MAX_REINTENTOS = 3
_ESPERA_BASE    = 2

_AMAZON_SUGGEST_URL = "https://completion.amazon.com/api/2017/suggestions"
_AMAZON_BASE        = "https://www.amazon.com"

# Headers que imitan un navegador real para evitar el bloqueo de Amazon
def _headers_browser() -> dict:
    return {
        "User-Agent":      _ua.random,
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
        "Referer":         "https://www.amazon.com/",
        "DNT":             "1",
    }


def _buscar_asin(titulo: str) -> tuple[str | None, str | None]:
    """
    Usa la API de autocompletado de Amazon para encontrar el ASIN
    del videojuego más relevante.
    Retorna (asin, titulo_encontrado) o (None, None).
    """
    try:
        resp = requests.get(
            _AMAZON_SUGGEST_URL,
            params={
                "mid":        "ATVPDKIKX0DER",  # Marketplace US
                "alias":      "videogames",       # categoría videojuegos
                "prefix":     titulo,
                "event":      "onkeypress",
                "limit":      6,
                "b2b":        0,
                "fresh":      0,
                "ks":         3,
                "lop":        1,
                "fb":         1,
                "suggestion-type": ["KEYWORD", "WIDGET"],
            },
            headers={"User-Agent": _ua.random, "Accept": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        data        = resp.json()
        sugerencias = data.get("suggestions", [])

        titulo_lower = titulo.lower()
        for s in sugerencias:
            valor = s.get("value") or ""
            # Las sugerencias con ASIN tienen el formato "title [ASIN:B0XXXXX]"
            m = re.search(r"\[ASIN:([A-Z0-9]{10})\]", valor)
            if m:
                nombre = re.sub(r"\[ASIN:[^]]+\]", "", valor).strip()
                if titulo_lower in nombre.lower() or nombre.lower() in titulo_lower:
                    return m.group(1), nombre

        # Sin ASIN en sugerencias: buscar por título directo en la URL de búsqueda
        return None, None

    except requests.RequestException as e:
        log.debug(f"[Amazon/Suggest] Fallo para '{titulo}': {e}")
        return None, None


def _buscar_asin_via_search(titulo: str) -> tuple[str | None, str | None]:
    """
    Fallback: hacer GET a la página de resultados de Amazon y extraer
    el primer ASIN del HTML usando un patrón robusto.
    """
    try:
        url  = f"{_AMAZON_BASE}/s"
        resp = requests.get(
            url,
            params={"k": titulo, "i": "videogames"},
            headers=_headers_browser(),
            timeout=12,
        )
        resp.raise_for_status()

        # Amazon incrusta los ASINs en data-asin="B0XXXXX" en los resultados
        m = re.search(r'data-asin="([A-Z0-9]{10})"', resp.text)
        if m:
            asin = m.group(1)
            # Extraer el título del primer resultado
            soup  = BeautifulSoup(resp.text, "html.parser")
            h2    = soup.find("h2", {"class": re.compile(r"a-size")})
            nombre = h2.get_text(strip=True) if h2 else titulo
            return asin, nombre

    except requests.RequestException as e:
        log.debug(f"[Amazon/Search] Fallo para '{titulo}': {e}")

    return None, None


def _obtener_precio_pagina(asin: str) -> tuple[float | None, float | None]:
    """
    Hace GET a amazon.com/dp/{ASIN} y extrae el precio del bloque
    JSON-LD (schema.org/Product) o de los spans de precio.
    Retorna (precio_actual, precio_regular).
    """
    url = f"{_AMAZON_BASE}/dp/{asin}"
    try:
        resp = requests.get(url, headers=_headers_browser(), timeout=12)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # --- Método 1: JSON-LD (más estable) ---
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "{}")
                # Puede ser un dict o una lista
                if isinstance(data, list):
                    data = next((d for d in data if d.get("@type") == "Product"), {})
                offers = data.get("offers") or {}
                precio_str = offers.get("price") or offers.get("lowPrice")
                if precio_str:
                    precio = round(float(str(precio_str).replace(",", "")), 2)
                    return precio, precio  # JSON-LD no distingue regular/oferta
            except (json.JSONDecodeError, ValueError):
                continue

        # --- Método 2: span de precio (menos estable, pero buen fallback) ---
        # Precio actual: #corePriceDisplay_desktop_feature_div .a-price .a-offscreen
        precio_span = soup.select_one(
            "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen"
        )
        precio_raw = precio_span.get_text(strip=True) if precio_span else None

        # Precio tachado (regular antes del descuento)
        regular_span = soup.select_one(
            ".basisPrice .a-price .a-offscreen, "
            ".a-price.a-text-price .a-offscreen"
        )
        regular_raw = regular_span.get_text(strip=True) if regular_span else None

        def _parse(s):
            if not s:
                return None
            limpio = re.sub(r"[^\d.]", "", s)
            return round(float(limpio), 2) if limpio else None

        precio         = _parse(precio_raw)
        precio_regular = _parse(regular_raw) or precio
        if precio:
            return precio, precio_regular

    except requests.RequestException as e:
        log.debug(f"[Amazon/Page] Fallo para ASIN={asin}: {e}")

    return None, None


class ScraperAmazon:
    """Scraper para precios de Amazon.com (videojuegos, US)."""

    def obtener_precio(self, juego: dict) -> dict:
        """
        Parámetros
        ----------
        juego : dict
            Debe contener 'titulo' y opcionalmente 'urls_tiendas.amazon'.

        Retorna
        -------
        dict con 'precio', 'precio_regular' (float o None)
        y 'url_directa' (str o None).
        """
        titulo  = juego.get("titulo", "")
        url_bd  = (juego.get("urls_tiendas") or {}).get("amazon", "")

        if not titulo:
            raise ValueError(f"Juego {juego.get('id')} sin título")

        # Si ya tenemos /dp/ASIN en la BD, saltamos la búsqueda
        m_asin = re.search(r"/dp/([A-Z0-9]{10})", url_bd or "")
        asin   = m_asin.group(1) if m_asin else None

        espera = _ESPERA_BASE
        for intento in range(1, _MAX_REINTENTOS + 1):
            try:
                # Si no tenemos ASIN, buscarlo
                if not asin:
                    asin, _ = _buscar_asin(titulo)
                if not asin:
                    asin, _ = _buscar_asin_via_search(titulo)

                if not asin:
                    log.warning(f"[Amazon] No se encontró ASIN para '{titulo}'")
                    return {"precio": None, "precio_regular": None, "url_directa": None}

                url_directa = f"{_AMAZON_BASE}/dp/{asin}"
                precio, precio_regular = _obtener_precio_pagina(asin)

                log.info(
                    f"[Amazon] {titulo} (ASIN={asin}) → ${precio} "
                    f"(regular: ${precio_regular})"
                )
                return {
                    "precio":         precio,
                    "precio_regular": precio_regular,
                    "url_directa":    url_directa,
                }

            except Exception as e:
                log.warning(
                    f"[Amazon] Intento {intento}/{_MAX_REINTENTOS} falló "
                    f"para '{titulo}': {e}"
                )
                if intento < _MAX_REINTENTOS:
                    time.sleep(espera)
                    espera *= 2
                    asin = None  # forzar nueva búsqueda si falló la página
                else:
                    return {"precio": None, "precio_regular": None, "url_directa": None}

        return {"precio": None, "precio_regular": None, "url_directa": None}
