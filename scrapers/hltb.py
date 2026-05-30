"""
scrapers/hltb.py
Dueño: Brack

Extrae los tiempos de juego de HowLongToBeat.com usando su API
internal de búsqueda (JSON POST), sin necesidad de Selenium.

Firma requerida por orquestador_brack.py:
    scraper.obtener_datos(juego: dict) -> dict

Retorna:
    {
        "main":           float | None,   # historia principal (horas)
        "main_extra":     float | None,   # historia + extras
        "completionist":  float | None,   # 100% completionista
    }
"""

import time
import logging
import requests
from fake_useragent import UserAgent

log = logging.getLogger(__name__)

_ua = UserAgent()
_MAX_REINTENTOS = 3
_ESPERA_BASE = 2

_HLTB_SEARCH_URL = "https://howlongtobeat.com/api/search"
_HLTB_REFERER = "https://howlongtobeat.com/"

# El hash del payload cambia de vez en cuando; este fue verificado en 2026-05
_SEARCH_HASH = "dfh4bhy6ol2cru08ry4"


class ScraperHLTB:
    """Scraper para tiempos de juego de HowLongToBeat."""

    def obtener_datos(self, juego: dict) -> dict:
        """
        Parámetros
        ----------
        juego : dict
            Debe contener 'titulo'.

        Retorna
        -------
        dict con claves 'main', 'main_extra', 'completionist' (float o None).
        """
        titulo = juego.get("titulo", "")
        if not titulo:
            raise ValueError(f"Juego {juego.get('id')} no tiene título")

        headers = {
            "User-Agent": _ua.random,
            "Referer": _HLTB_REFERER,
            "Origin": "https://howlongtobeat.com",
            "Content-Type": "application/json",
        }

        payload = {
            "searchType": "games",
            "searchTerms": titulo.split(),
            "searchPage": 1,
            "size": 1,
            "searchOptions": {
                "games": {
                    "userId": 0,
                    "platform": "",
                    "sortCategory": "popular",
                    "rangeCategory": "main",
                    "rangeTime": {"min": None, "max": None},
                    "gameplay": {"perspective": "", "flow": "", "genre": ""},
                    "rangeYear": {"min": "", "max": ""},
                    "modifier": "",
                },
                "users": {"sortCategory": "postcount"},
                "lists": {"sortCategory": "follows"},
                "filter": "",
                "sort": 0,
                "randomizer": 0,
            },
            "useCache": True,
        }

        search_url = f"{_HLTB_SEARCH_URL}/{_SEARCH_HASH}"

        espera = _ESPERA_BASE
        for intento in range(1, _MAX_REINTENTOS + 1):
            try:
                resp = requests.post(
                    search_url, json=payload, headers=headers, timeout=12
                )
                resp.raise_for_status()
                data = resp.json()

                resultados = data.get("data", [])
                if not resultados:
                    log.warning(f"[HLTB] Sin resultados para: {titulo}")
                    return {"main": None, "main_extra": None, "completionist": None}

                r = resultados[0]

                def _horas(segundos) -> float | None:
                    """HLTB devuelve segundos; convertimos a horas con 1 decimal."""
                    if not segundos:
                        return None
                    return round(segundos / 3600, 1)

                resultado = {
                    "main":          _horas(r.get("comp_main")),
                    "main_extra":    _horas(r.get("comp_plus")),
                    "completionist": _horas(r.get("comp_100")),
                }

                log.info(f"[HLTB] {titulo} — {resultado}")
                return resultado

            except requests.RequestException as e:
                log.warning(
                    f"[HLTB] Intento {intento}/{_MAX_REINTENTOS} falló "
                    f"para juego {juego.get('id')}: {e}"
                )
                if intento < _MAX_REINTENTOS:
                    time.sleep(espera)
                    espera *= 2
                else:
                    raise
