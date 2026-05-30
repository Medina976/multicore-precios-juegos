"""
scrapers/metacritic.py
Dueño: Brack

Extrae el score de Metacritic para un juego usando BeautifulSoup.
Metacritic tiene protección anti-bot, así que usamos fake-useragent
y un pequeño delay entre intentos.

Firma requerida por orquestador_brack.py:
    scraper.obtener_datos(juego: dict) -> dict

Retorna:
    {
        "scores": {
            "Switch": 92,
            "PC": 89,
            ...  # solo las consolas que aparezcan en la página
        }
    }
"""

import time
import logging
import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

log = logging.getLogger(__name__)

_ua = UserAgent()
_MAX_REINTENTOS = 3
_ESPERA_BASE = 3  # Metacritic es más estricto, esperamos más

# Mapa de nombres de plataforma en Metacritic → nombre canónico del proyecto
_PLATAFORMA_MAP = {
    "nintendo-switch": "Switch",
    "switch": "Switch",
    "pc": "PC",
    "playstation-5": "PS5",
    "ps5": "PS5",
    "playstation-4": "PS4",
    "ps4": "PS4",
    "xbox-series-x": "Xbox",
    "xbox-one": "Xbox",
}


class ScraperMetacritic:
    """Scraper para scores de Metacritic."""

    def obtener_datos(self, juego: dict) -> dict:
        """
        Parámetros
        ----------
        juego : dict
            Debe contener 'metacritic_url' y/o 'consolas'.

        Retorna
        -------
        dict con clave 'scores': {consola: score_int}.
        """
        url = juego.get("metacritic_url")
        if not url:
            log.warning(f"[Metacritic] Juego {juego.get('id')} sin URL de Metacritic")
            return {"scores": {}}

        consolas_juego = [c.lower() for c in (juego.get("consolas") or [])]
        scores = {}

        espera = _ESPERA_BASE
        for intento in range(1, _MAX_REINTENTOS + 1):
            try:
                headers = {
                    "User-Agent": _ua.random,
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://www.metacritic.com/",
                }
                resp = requests.get(url, headers=headers, timeout=12)
                resp.raise_for_status()

                soup = BeautifulSoup(resp.text, "html.parser")

                # Metacritic muestra el metascore en un elemento con
                # data-testid="score-details-metascore" o clase "c-siteReviewScore"
                # Intentamos ambos selectores por compatibilidad
                score_tag = (
                    soup.find(attrs={"data-testid": "score-details-metascore"})
                    or soup.find(class_="c-siteReviewScore")
                    or soup.find(class_="metascore_w")
                )

                if score_tag:
                    try:
                        score_val = int(score_tag.get_text(strip=True))
                    except ValueError:
                        score_val = None

                    # Asignamos el score a todas las consolas del juego
                    for consola_raw in consolas_juego:
                        consola_canon = _PLATAFORMA_MAP.get(consola_raw, consola_raw.upper())
                        scores[consola_canon] = score_val

                log.info(f"[Metacritic] {juego.get('titulo')} — scores: {scores}")
                return {"scores": scores}

            except requests.RequestException as e:
                log.warning(
                    f"[Metacritic] Intento {intento}/{_MAX_REINTENTOS} falló "
                    f"para juego {juego.get('id')}: {e}"
                )
                if intento < _MAX_REINTENTOS:
                    time.sleep(espera)
                    espera *= 2
                else:
                    raise
