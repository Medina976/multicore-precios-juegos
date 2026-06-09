"""
scrapers/amazon.py
Dueño: Brack

Obtiene precio de un juego en Amazon.com.

ESTRATEGIA (3 pasos):
    1) Si el juego ya tiene URL /dp/ASIN en BD → usarla directamente.

    2) Si no → buscar el ASIN via la API de autocompletado de Amazon:
         https://completion.amazon.com/api/2017/suggestions
         ?mid=ATVPDKIKX0DER&alias=videogames&prefix=<titulo>&limit=6
       Las sugerencias con ASIN traen la forma: "título [ASIN:B0XXXXX]"

    3) Si la API de autocompletado no da ASIN → GET a la página de búsqueda
       de Amazon y extraer data-asin del primer resultado.

    4) Con el ASIN construir https://www.amazon.com/dp/{ASIN} y extraer
       el precio en este orden de preferencia (más estable primero):
         a) JSON-LD <script type="application/ld+json"> (schema.org/Product)
         b) span#apex_desktop .a-price .a-offscreen  (precio nuevo)
         c) span#corePriceDisplay_desktop_feature_div .a-price .a-offscreen

POR QUÉ AMAZON ES DIFÍCIL:
    - La estructura del HTML cambia constantemente con A/B tests.
    - El JSON-LD es la señal más estable (es el mismo bloque que usan
      Google y los buscadores para indexar el precio).
    - Si falla el JSON-LD, usamos un selector CSS estable conocido.

Firma requerida:
    scraper.obtener_precio(juego: dict) -> dict
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
_ESPERA_BASE    = 3   # Amazon es más agresivo bloqueando, empezamos en 3s

_AMAZON_SUGGEST_URL = "https://completion.amazon.com/api/2017/suggestions"
_AMAZON_BASE        = "https://www.amazon.com"


def _headers_browser() -> dict:
    return {
        "User-Agent":      _ua.random,
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
        "Referer":         "https://www.amazon.com/",
        "DNT":             "1",
        # Cookie mínima para evitar el redirect de región
        "Cookie":          "i18n-prefs=USD; sp-cdn=\"L5Z9:MX\"",
    }


def _buscar_asin_sugerencias(titulo: str) -> str | None:
    """Busca ASIN via la API de autocompletado de Amazon."""
    try:
        resp = requests.get(
            _AMAZON_SUGGEST_URL,
            params={
                "mid":    "ATVPDKIKX0DER",
                "alias":  "videogames",
                "prefix": titulo,
                "event":  "onkeypress",
                "limit":  8,
                "b2b":    0,
                "fresh":  0,
            },
            headers={"User-Agent": _ua.random, "Accept": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        sugerencias = resp.json().get("suggestions", [])
        titulo_lower = titulo.lower()

        for s in sugerencias:
            valor = s.get("value") or ""
            m = re.search(r"\[ASIN:([A-Z0-9]{10})\]", valor)
            if m:
                nombre = re.sub(r"\[ASIN:[^]]+\]", "", valor).strip().lower()
                # Verificar que el título coincide razonablemente
                if any(
                    palabra in nombre
                    for palabra in titulo_lower.split()
                    if len(palabra) > 3
                ):
                    return m.group(1)
    except requests.RequestException as e:
        log.debug(f"[Amazon/Suggest] Error para '{titulo}': {e}")
    return None


def _buscar_asin_search(titulo: str) -> str | None:
    """Fallback: extrae el ASIN del primer resultado de la búsqueda de Amazon."""
    try:
        resp = requests.get(
            f"{_AMAZON_BASE}/s",
            params={"k": f"{titulo} game", "i": "videogames", "rh": "n:468642"},
            headers=_headers_browser(),
            timeout=14,
        )
        resp.raise_for_status()

        # Amazon incrusta data-asin en los divs de cada resultado
        # Buscamos el primero que tenga precio (excluir anuncios sin precio)
        soup  = BeautifulSoup(resp.text, "html.parser")
        items = soup.find_all("div", {"data-asin": re.compile(r"^[A-Z0-9]{10}$")})
        for item in items:
            # Solo tomar items que tengan span de precio
            if item.find("span", class_="a-price"):
                return item["data-asin"]

    except requests.RequestException as e:
        log.debug(f"[Amazon/Search] Error para '{titulo}': {e}")
    return None


def _obtener_precio_pagina(asin: str) -> tuple[float | None, float | None]:
    """
    Extrae el precio de la página del producto en Amazon.
    Retorna (precio_actual, precio_regular).
    """
    url = f"{_AMAZON_BASE}/dp/{asin}"
    try:
        resp = requests.get(
            url,
            headers=_headers_browser(),
            timeout=14,
            allow_redirects=True,
        )
        resp.raise_for_status()

        # Verificar que no nos redirigió a una página de error o captcha
        if "robot" in resp.url.lower() or "captcha" in resp.text[:500].lower():
            log.warning(f"[Amazon] CAPTCHA detectado para ASIN={asin}")
            return None, None

        soup = BeautifulSoup(resp.text, "html.parser")

        # --- Método 1: JSON-LD (schema.org/Product) ---
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "{}")
                if isinstance(data, list):
                    data = next(
                        (d for d in data if d.get("@type") == "Product"), {}
                    )
                if data.get("@type") != "Product":
                    continue
                offers = data.get("offers") or {}
                # Puede ser un solo objeto o una lista
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                precio_str = (
                    offers.get("price")
                    or offers.get("lowPrice")
                    or offers.get("highPrice")
                )
                if precio_str is not None:
                    precio = round(float(str(precio_str).replace(",", "")), 2)
                    if precio > 0:
                        return precio, precio
            except (json.JSONDecodeError, ValueError, TypeError):
                continue

        # --- Método 2: Selector CSS conocido (price box principal) ---
        def _parse_span(selector: str) -> float | None:
            el = soup.select_one(selector)
            if not el:
                return None
            txt = el.get_text(strip=True)
            limpio = re.sub(r"[^\d.]", "", txt)
            try:
                val = float(limpio)
                return round(val, 2) if val > 0 else None
            except ValueError:
                return None

        # Precio actual (nuevo)
        precio = (
            _parse_span("#apex_desktop .a-price .a-offscreen")
            or _parse_span("#corePriceDisplay_desktop_feature_div .a-price .a-offscreen")
            or _parse_span(".a-price.priceToPay .a-offscreen")
            or _parse_span(".a-price .a-offscreen")
        )

        # Precio tachado (era)
        precio_regular = (
            _parse_span(".basisPrice .a-offscreen")
            or _parse_span(".a-price.a-text-price .a-offscreen")
            or precio
        )

        if precio:
            return precio, precio_regular

    except requests.RequestException as e:
        log.debug(f"[Amazon/Page] Error para ASIN={asin}: {e}")

    return None, None


class ScraperAmazon:
    """Scraper para precios de Amazon.com (videojuegos US)."""

    def obtener_precio(self, juego: dict) -> dict:
        titulo = juego.get("titulo", "")
        url_bd = (juego.get("urls_tiendas") or {}).get("amazon", "")

        if not titulo:
            raise ValueError(f"Juego {juego.get('id')} sin título")

        # Reutilizar ASIN si ya lo tenemos en BD
        m_asin = re.search(r"/dp/([A-Z0-9]{10})", url_bd or "")
        asin   = m_asin.group(1) if m_asin else None

        espera = _ESPERA_BASE
        for intento in range(1, _MAX_REINTENTOS + 1):
            try:
                # Paso 1: conseguir ASIN
                if not asin:
                    asin = _buscar_asin_sugerencias(titulo)
                if not asin:
                    asin = _buscar_asin_search(titulo)

                if not asin:
                    log.warning(f"[Amazon] No se encontró ASIN para '{titulo}'")
                    return {"precio": None, "precio_regular": None, "url_directa": None}

                url_directa = f"{_AMAZON_BASE}/dp/{asin}"

                # Paso 2: extraer precio de la página del producto
                precio, precio_regular = _obtener_precio_pagina(asin)

                if precio is None:
                    log.warning(
                        f"[Amazon] ASIN={asin} encontrado pero no se pudo "
                        f"extraer precio para '{titulo}'"
                    )
                    # Devolvemos url_directa aunque no haya precio
                    # (el usuario puede ir al producto y ver el precio)
                    return {
                        "precio":         None,
                        "precio_regular": None,
                        "url_directa":    url_directa,
                    }

                log.info(
                    f"[Amazon] {titulo} (ASIN={asin}) → "
                    f"${precio} (regular: ${precio_regular})"
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
                    asin = None  # reintentar búsqueda
                else:
                    return {"precio": None, "precio_regular": None, "url_directa": None}

        return {"precio": None, "precio_regular": None, "url_directa": None}
