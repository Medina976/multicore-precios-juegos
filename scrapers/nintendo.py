"""
scrapers/nintendo.py
Dueño: Brack

Obtiene el precio actual de un juego en Nintendo eShop (región US).

ESTRATEGIA:
  1) Algolia (API interna de Nintendo): busca por título, devuelve precio
     Y la URL directa del producto (/us/store/products/slug-del-juego/).
     Si encuentra la URL directa, la guarda en BD (igual que Steam hace
     con el AppID), para que el link de la tarjeta apunte al juego exacto.
  2) Si Algolia no tiene resultados: API pública de precios de Nintendo
     por NSUID (https://api.ec.nintendo.com/v1/price).

MEJORA CLAVE vs versión anterior:
  - Retorna 'url_directa' con la URL real del producto.
  - El orquestador llama actualizar_url_tienda() igual que con Steam.
  - Lee 'msrp' (precio regular) aunque 'salePrice' sea null, porque
    muchos juegos sin oferta activa solo tienen msrp.
  - Elimina el fallback a searching.nintendo.com (deprecado, devuelve 404).

Firma requerida por orquestador_brack.py:
    scraper.obtener_precio(juego: dict) -> dict

Retorna:
    {
        "precio":        float | None,
        "precio_regular": float | None,
        "url_directa":   str | None,   <- URL /us/store/products/slug/ para BD
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

# ── Algolia (Nintendo.com) ────────────────────────────────────────────────────
# Credenciales públicas del JS de nintendo.com (junio 2026)
_ALGOLIA_APP_ID  = "U3B6GR4UA3"
_ALGOLIA_API_KEY = "9a20c93440cf63cf1a7008d75f7438bf"
_ALGOLIA_INDEX   = "noa_aem_game_en_us_1"
_ALGOLIA_URL     = (
    f"https://{_ALGOLIA_APP_ID.lower()}-dsn.algolia.net"
    f"/1/indexes/{_ALGOLIA_INDEX}/query"
)

_NINTENDO_BASE   = "https://www.nintendo.com"
# API oficial de precios por NSUID (no tiene verificación de edad)
_NINTENDO_PRICE_API = "https://api.ec.nintendo.com/v1/price"


def _limpiar_precio(valor) -> float | None:
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    limpio = re.sub(r"[^\d.]", "", str(valor))
    return float(limpio) if limpio else None


def _slug_desde_url(url: str) -> str | None:
    """Extrae el slug de una URL tipo /us/store/products/doom-eternal-switch/"""
    m = re.search(r"/products/([\w-]+)/?", url or "")
    return m.group(1) if m else None


def _buscar_algolia(query: str, headers: dict) -> dict | None:
    """
    Busca en Algolia y retorna el hit más relevante con:
      - title, salePrice, msrp, nsuid, url (path relativo del producto)
    Retorna None si falla o no hay resultados.
    """
    payload = {
        "params": (
            f"query={requests.utils.quote(query)}"
            "&hitsPerPage=5"
            "&attributesToRetrieve=title,salePrice,msrp,percentOff,nsuid,url,slug"
        )
    }
    try:
        resp = requests.post(
            _ALGOLIA_URL,
            json=payload,
            headers={
                **headers,
                "X-Algolia-Application-Id": _ALGOLIA_APP_ID,
                "X-Algolia-API-Key":        _ALGOLIA_API_KEY,
            },
            timeout=10,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
        if not hits:
            return None

        # Preferir coincidencia exacta de título
        query_lower = query.lower()
        for hit in hits:
            if (hit.get("title") or "").lower() == query_lower:
                return hit

        # Si no hay exacta, tomar el primero
        return hits[0]

    except requests.RequestException as e:
        log.debug(f"[Nintendo/Algolia] Fallo para '{query}': {e}")
        return None


def _precio_por_nsuid(nsuid: str, headers: dict) -> dict | None:
    """
    API oficial de Nintendo para precios por NSUID.
    No requiere login ni verifica edad — es la misma que usa la app de Nintendo.
    Retorna dict con 'regular_price' y 'discount_price', o None.
    """
    try:
        resp = requests.get(
            _NINTENDO_PRICE_API,
            params={"ids": nsuid, "country": "US", "lang": "en"},
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        prices = resp.json().get("prices", [])
        if not prices:
            return None
        p = prices[0]
        regular  = p.get("regular_price", {}).get("raw_value")
        discount = p.get("discount_price", {}).get("raw_value")
        return {
            "regular":  float(regular)  if regular  else None,
            "discount": float(discount) if discount else None,
        }
    except requests.RequestException as e:
        log.debug(f"[Nintendo/PriceAPI] Fallo para nsuid={nsuid}: {e}")
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
        dict con claves 'precio', 'precio_regular' (float o None)
        y 'url_directa' (str o None) para actualizar la BD.
        """
        url_tienda = (juego.get("urls_tiendas") or {}).get("nintendo", "")
        titulo     = juego.get("titulo", "")

        if not titulo:
            raise ValueError(f"Juego {juego.get('id')} sin título")

        # Si ya tenemos una URL directa en BD, extraer el slug para búsqueda
        # más precisa (ej: "doom-eternal-switch" → mejor que solo "Doom Eternal")
        slug_bd = _slug_desde_url(url_tienda)
        query   = slug_bd.replace("-", " ") if slug_bd else titulo

        headers = {
            "User-Agent": _ua.random,
            "Accept":     "application/json",
            "Referer":    "https://www.nintendo.com/",
        }

        espera = _ESPERA_BASE
        for intento in range(1, _MAX_REINTENTOS + 1):
            try:
                hit = _buscar_algolia(query, headers)

                # Si buscamos por slug y no encontramos, reintentar con título
                if hit is None and slug_bd:
                    log.debug(f"[Nintendo] Sin resultado por slug, reintentando con título: '{titulo}'")
                    hit = _buscar_algolia(titulo, headers)

                if hit is None:
                    log.warning(f"[Nintendo] Sin resultados en Algolia para: '{titulo}'")
                    return {"precio": None, "precio_regular": None, "url_directa": None}

                # ── Extraer URL directa del producto ──────────────────────────
                # Algolia devuelve 'url' como path relativo: /us/store/products/slug/
                url_relativa = hit.get("url") or hit.get("slug") or ""
                if url_relativa and not url_relativa.startswith("http"):
                    url_directa = f"{_NINTENDO_BASE}{url_relativa}"
                elif url_relativa.startswith("http"):
                    url_directa = url_relativa
                else:
                    url_directa = None

                # ── Precio: primero desde Algolia, luego desde API de precios ─
                precio          = _limpiar_precio(hit.get("salePrice"))
                precio_regular  = _limpiar_precio(hit.get("msrp"))

                # Si Algolia no trajo precio, intentar con la API oficial por NSUID
                nsuid = hit.get("nsuid")
                if precio is None and nsuid:
                    log.debug(f"[Nintendo] Precio nulo en Algolia, consultando API por nsuid={nsuid}")
                    precios_api = _precio_por_nsuid(str(nsuid), headers)
                    if precios_api:
                        precio         = precios_api.get("discount") or precios_api.get("regular")
                        precio_regular = precios_api.get("regular") or precio

                # Si msrp también es None pero tenemos precio de venta, usar ese
                if precio_regular is None:
                    precio_regular = precio

                log.info(
                    f"[Nintendo] {titulo} → ${precio} "
                    f"(regular: ${precio_regular}) | url: {url_directa}"
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
