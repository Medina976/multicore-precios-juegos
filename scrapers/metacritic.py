"""
scrapers/metacritic.py
Dueño: Brack

Extrae el Metascore de Metacritic usando su API interna JSON
(no requiere scraping HTML, evita bloqueos de Cloudflare).

Endpoint de búsqueda:
    GET https://internal-prod.apigee.io/v2/apps/mcm/v4/search/title
        ?apiKey=1MOZgmNFxvmljaQR1X9KAij9Mo4xAY3u
        &title={titulo}&platform=&componentName=search&componentDisplayName=Search
        &componentType=SearchResults

Firma requerida por orquestador_brack.py:
    scraper.obtener_datos(juego: dict) -> dict

Retorna:
    {
        "scores": {
            "Switch": 92,
            "PC":     89,
            ...  # solo las plataformas con score disponible
        }
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

# API interna de Metacritic (JSON, sin Cloudflare)
_MC_SEARCH_URL = "https://internal-prod.apigee.io/v2/apps/mcm/v4/search/title"
_MC_API_KEY    = "1MOZgmNFxvmljaQR1X9KAij9Mo4xAY3u"

# Mapa de slugs de plataforma Metacritic → nombre canónico del proyecto
_PLATAFORMA_MAP = {
    "nintendo-switch": "Switch",
    "switch":           "Switch",
    "pc":               "PC",
    "playstation-5":    "PS5",
    "ps5":              "PS5",
    "playstation-4":    "PS4",
    "ps4":              "PS4",
    "xbox-series-x":    "Xbox",
    "xbox-series-xboxone": "Xbox",
    "xbox-one":         "Xbox",
    "ios":              "iOS",
    "android":          "Android",
}


def _plataforma_canon(plat_raw: str) -> str:
    """Convierte el slug de Metacritic al nombre canónico del proyecto."""
    slug = plat_raw.lower().replace(" ", "-")
    return _PLATAFORMA_MAP.get(slug, plat_raw)


class ScraperMetacritic:
    """Scraper para scores de Metacritic (vía API JSON interna)."""

    def obtener_datos(self, juego: dict) -> dict:
        """
        Parámetros
        ----------
        juego : dict
            Debe contener 'titulo'. 'metacritic_url' se usa solo como
            referencia de plataforma si está disponible.

        Retorna
        -------
        dict con clave 'scores': {consola: score_int}.
        """
        titulo = juego.get("titulo", "")
        if not titulo:
            log.warning(f"[Metacritic] Juego {juego.get('id')} sin título")
            return {"scores": {}}

        consolas_juego = [c.lower() for c in (juego.get("consolas") or [])]

        espera = _ESPERA_BASE
        for intento in range(1, _MAX_REINTENTOS + 1):
            try:
                headers = {
                    "User-Agent": _ua.random,
                    "Accept": "application/json",
                    "Referer": "https://www.metacritic.com/",
                }
                params = {
                    "apiKey": _MC_API_KEY,
                    "title": titulo,
                    "platform": "",
                    "componentName": "search",
                    "componentDisplayName": "Search",
                    "componentType": "SearchResults",
                }
                resp = requests.get(
                    _MC_SEARCH_URL,
                    params=params,
                    headers=headers,
                    timeout=12,
                )
                resp.raise_for_status()
                data = resp.json()

                # La respuesta tiene una lista de componentes; buscamos
                # los que contengan datos de juegos con scores
                scores = {}
                componentes = data.get("components", [])
                for comp in componentes:
                    items = comp.get("data", {}).get("items", [])
                    for item in items:
                        # Verificamos que el título coincida aproximadamente
                        item_title = (item.get("title") or "").lower()
                        if not any(w in item_title for w in titulo.lower().split()[:3]):
                            continue

                        # Extraer score y plataforma
                        score_raw = (
                            item.get("criticScoreSummary", {}).get("score")
                            or item.get("score")
                        )
                        plat_raw = (
                            item.get("platform", {}).get("name", "")
                            if isinstance(item.get("platform"), dict)
                            else item.get("platform", "")
                        )

                        if score_raw is None:
                            continue

                        try:
                            score_val = int(score_raw)
                        except (ValueError, TypeError):
                            continue

                        plat_canon = _plataforma_canon(plat_raw)

                        # Solo guardamos plataformas que el juego tenga declaradas,
                        # o todas si el juego no tiene lista de consolas
                        if not consolas_juego or plat_canon.lower() in consolas_juego:
                            scores[plat_canon] = score_val

                # Si la búsqueda no devolvió nada útil, intentamos
                # asignar el score a todas las consolas del juego
                if not scores and consolas_juego:
                    # Buscar cualquier score en los resultados y asignar a todas
                    for comp in componentes:
                        for item in comp.get("data", {}).get("items", []):
                            score_raw = (
                                item.get("criticScoreSummary", {}).get("score")
                                or item.get("score")
                            )
                            if score_raw is not None:
                                try:
                                    score_val = int(score_raw)
                                    for c in consolas_juego:
                                        scores[_plataforma_canon(c)] = score_val
                                    break
                                except (ValueError, TypeError):
                                    pass
                        if scores:
                            break

                log.info(f"[Metacritic] {titulo} — scores: {scores}")
                return {"scores": scores}

            except requests.RequestException as e:
                log.warning(
                    f"[Metacritic] Intento {intento}/{_MAX_REINTENTOS} falló "
                    f"para '{titulo}': {e}"
                )
                if intento < _MAX_REINTENTOS:
                    time.sleep(espera)
                    espera *= 2
                else:
                    return {"scores": {}}
