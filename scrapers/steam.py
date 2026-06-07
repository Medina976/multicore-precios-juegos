"""
scrapers/steam.py
Dueño: Brack

Obtiene el precio actual de un juego en Steam usando la Storefront API
publica de Steam (no requiere API key).

Endpoint principal:
    https://store.steampowered.com/api/appdetails?appids={appid}&cc=us&filters=price_overview

Endpoint de busqueda (fallback cuando no tenemos AppID):
    https://store.steampowered.com/api/storesearch/?term={query}&cc=us&l=en

Firma requerida por orquestador_brack.py:
    scraper.obtener_precio(juego: dict) -> dict

Retorna:
    {
        "precio": float | None,
        "precio_regular": float | None,
    }

NOTA: El seeding.py genera URLs del tipo
    https://store.steampowered.com/search/?term=elden+ring
en vez de URLs directas con AppID. Por eso este scraper:
  1) Intenta extraer AppID de la URL si la tiene (formato /app/{id}/...).
  2) Si no, busca el juego por titulo en la API de busqueda de Steam
     y toma el primer resultado.

Mejora: se agrega throttling suave entre peticiones consecutivas
  para evitar bloqueos por rate-limit de Steam (HTTP 429).
"""

import re
import time
import logging
import requests
from fake_useragent import UserAgent

log = logging.getLogger(__name__)

_ua = UserAgent()

_MAX_REINTENTOS = 3
_ESPERA_BASE = 2       # segundos, se duplica en cada reintento
_THROTTLE_DELAY = 0.5  # pausa entre peticiones exitosas para evitar 429

_STEAM_SEARCH_API    = "https://store.steampowered.com/api/storesearch/"
_STEAM_APPDETAILS_API = "https://store.steampowered.com/api/appdetails"


def _extraer_appid(url: str) -> str | None:
    """Extrae el AppID de Steam desde una URL del tipo:
    https://store.steampowered.com/app/1245620/Elden_Ring/
    """
    match = re.search(r"/app/(\d+)", url or "")
    return match.group(1) if match else None


def _buscar_appid_por_titulo(titulo: str, timeout: int = 8) -> str | None:
    """
    Llama a la API de busqueda de Steam y devuelve el AppID del primer
    resultado cuyo nombre coincida aproximadamente con el titulo,
    o None si no hay resultados.
    """
    if not titulo:
        return None
    try:
        headers = {"User-Agent": _ua.random}
        resp = requests.get(
            _STEAM_SEARCH_API,
            params={"term": titulo, "cc": "us", "l": "en"},
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        if not items:
            return None

        # Preferir coincidencia exacta de nombre antes de tomar el primero
        titulo_lower = titulo.lower()
        for item in items:
            nombre = (item.get("name") or "").lower()
            if nombre == titulo_lower:
                return str(item.get("id"))

        # Si no hay coincidencia exacta, tomar el primero
        return str(items[0].get("id"))

    except requests.RequestException as e:
        log.debug(f"[Steam] Busqueda por titulo fallo para '{titulo}': {e}")
        return None


class ScraperSteam:
    """Scraper para precios de Steam."""

    def obtener_precio(self, juego: dict) -> dict:
        """
        Parametros
        ----------
        juego : dict
            Debe contener al menos 'titulo' y opcionalmente 'urls_tiendas.steam'.

        Retorna
        -------
        dict con claves 'precio' y 'precio_regular' (float o None).
        Lanza Exception si no se puede obtener el precio tras reintentos.
        """
        url_tienda = (juego.get("urls_tiendas") or {}).get("steam")
        titulo = juego.get("titulo", "")

        # 1) Intentar sacar AppID de la URL directa
        appid = _extraer_appid(url_tienda) if url_tienda else None

        # 2) Si no se pudo, buscar por titulo (este es el caso del seeding actual)
        if not appid:
            appid = _buscar_appid_por_titulo(titulo)
            if appid:
                log.debug(f"[Steam] AppID '{appid}' encontrado por busqueda de titulo '{titulo}'")

        if not appid:
            log.warning(f"[Steam] No se pudo identificar AppID para '{titulo}' (url={url_tienda})")
            return {"precio": None, "precio_regular": None}

        # 3) Consultar precio con el AppID
        espera = _ESPERA_BASE
        for intento in range(1, _MAX_REINTENTOS + 1):
            try:
                headers = {"User-Agent": _ua.random}
                resp = requests.get(
                    _STEAM_APPDETAILS_API,
                    params={
                        "appids": appid,
                        "cc": "us",
                        "filters": "price_overview",
                    },
                    headers=headers,
                    timeout=10,
                )

                # Steam devuelve 429 cuando hay demasiadas peticiones
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", espera * 2))
                    log.warning(f"[Steam] Rate limit (429), esperando {retry_after}s...")
                    time.sleep(retry_after)
                    continue

                resp.raise_for_status()
                data = resp.json()

                info = data.get(str(appid), {})
                if not info.get("success"):
                    # El juego no esta disponible en la tienda US
                    return {"precio": None, "precio_regular": None}

                precio_overview = info.get("data", {}).get("price_overview")
                if precio_overview is None:
                    # Juego gratuito o sin precio definido
                    return {"precio": 0.0, "precio_regular": 0.0}

                # Steam devuelve los precios en centavos (USD)
                precio = precio_overview["final"] / 100
                precio_regular = precio_overview["initial"] / 100

                log.info(
                    f"[Steam] {titulo} (id={appid}) — ${precio:.2f} "
                    f"(regular: ${precio_regular:.2f})"
                )

                # Throttling suave para no saturar la API de Steam
                time.sleep(_THROTTLE_DELAY)

                return {"precio": precio, "precio_regular": precio_regular}

            except requests.RequestException as e:
                log.warning(
                    f"[Steam] Intento {intento}/{_MAX_REINTENTOS} fallo "
                    f"para juego {juego.get('id')}: {e}"
                )
                if intento < _MAX_REINTENTOS:
                    time.sleep(espera)
                    espera *= 2
                else:
                    raise
