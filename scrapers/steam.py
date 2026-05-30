"""
scrapers/steam.py
Dueño: Brack

Obtiene el precio actual de un juego en Steam usando la Storefront API
pública de Steam (no requiere API key).

Endpoint: https://store.steampowered.com/api/appdetails?appids={appid}&cc=us&filters=price_overview

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

# Número de reintentos ante errores de red
_MAX_REINTENTOS = 3
_ESPERA_BASE = 2  # segundos, se duplica en cada reintento


def _extraer_appid(url: str) -> str | None:
    """Extrae el AppID de Steam desde una URL del tipo:
    https://store.steampowered.com/app/1245620/Elden_Ring/
    """
    match = re.search(r"/app/(\d+)/", url or "")
    return match.group(1) if match else None


class ScraperSteam:
    """Scraper para precios de Steam."""

    def obtener_precio(self, juego: dict) -> dict:
        """
        Parámetros
        ----------
        juego : dict
            Debe contener la clave 'urls_tiendas' con un sub-campo 'steam'.
            Ejemplo: {'id': 1, 'titulo': 'Elden Ring',
                      'urls_tiendas': {'steam': 'https://store.steampowered.com/app/1245620/...'}}

        Retorna
        -------
        dict con claves 'precio' y 'precio_regular' (ambos float o None).
        Lanza Exception si no se puede obtener el precio tras los reintentos.
        """
        url_tienda = (juego.get("urls_tiendas") or {}).get("steam")
        if not url_tienda:
            raise ValueError(f"Juego {juego.get('id')} no tiene URL de Steam")

        appid = _extraer_appid(url_tienda)
        if not appid:
            raise ValueError(f"No se pudo extraer AppID de: {url_tienda}")

        api_url = (
            f"https://store.steampowered.com/api/appdetails"
            f"?appids={appid}&cc=us&filters=price_overview"
        )

        espera = _ESPERA_BASE
        for intento in range(1, _MAX_REINTENTOS + 1):
            try:
                headers = {"User-Agent": _ua.random}
                resp = requests.get(api_url, headers=headers, timeout=10)
                resp.raise_for_status()
                data = resp.json()

                info = data.get(str(appid), {})
                if not info.get("success"):
                    # El juego no está disponible en la tienda US
                    return {"precio": None, "precio_regular": None}

                precio_overview = info.get("data", {}).get("price_overview")
                if precio_overview is None:
                    # Juego gratuito o sin precio definido
                    return {"precio": 0.0, "precio_regular": 0.0}

                # Steam devuelve los precios en centavos (USD)
                precio = precio_overview["final"] / 100
                precio_regular = precio_overview["initial"] / 100

                log.info(
                    f"[Steam] {juego.get('titulo')} — ${precio:.2f} "
                    f"(regular: ${precio_regular:.2f})"
                )
                return {"precio": precio, "precio_regular": precio_regular}

            except requests.RequestException as e:
                log.warning(
                    f"[Steam] Intento {intento}/{_MAX_REINTENTOS} falló "
                    f"para juego {juego.get('id')}: {e}"
                )
                if intento < _MAX_REINTENTOS:
                    time.sleep(espera)
                    espera *= 2
                else:
                    raise
