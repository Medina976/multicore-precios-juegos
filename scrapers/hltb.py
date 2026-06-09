"""
scrapers/hltb.py
Dueño: Brack

Obtiene los tiempos de completado de un juego desde HowLongToBeat
usando la librería oficial `howlongtobeatpy`.

https://pypi.org/project/howlongtobeatpy/

Ventajas sobre scraping manual:
    - Maneja automáticamente el token dinámico de la API de HLTB
      (que cambia con cada deploy de la página).
    - Devuelve los tiempos ya en HORAS (no en segundos).
    - Mantenida activamente; si HLTB cambia su API, se actualiza la librería.

Firma requerida por orquestador_brack.py:
    scraper.obtener_datos(juego: dict) -> dict

Retorna:
    {
        "main":          float | None,   (horas, 1 decimal)
        "main_extra":    float | None,
        "completionist": float | None,
    }
"""

import asyncio
import logging
import time
from howlongtobeatpy import HowLongToBeat

log = logging.getLogger(__name__)

_MAX_REINTENTOS = 3
_ESPERA_BASE    = 2


def _redondear(valor) -> float | None:
    """Redondea a 1 decimal; retorna None si el valor es 0 o nulo."""
    if valor is None or not isinstance(valor, (int, float)) or valor <= 0:
        return None
    return round(float(valor), 1)


def _buscar_sincrono(titulo: str):
    """
    howlongtobeatpy es async internamente; lo envolvemos en asyncio.run()
    para que el orquestador (que usa threads, no async) pueda llamarlo.
    """
    return asyncio.run(HowLongToBeat().async_search(titulo))


def _seleccionar_resultado(resultados: list, titulo: str) -> object | None:
    """
    Elige el resultado más relevante de la lista que devuelve HLTB.
    La librería ya los ordena por similitud, pero verificamos
    coincidencia exacta primero.
    """
    if not resultados:
        return None

    titulo_lower = titulo.lower()

    # 1) Coincidencia exacta de nombre
    for r in resultados:
        if (r.game_name or "").lower() == titulo_lower:
            return r

    # 2) El mejor score de similaridad que devuelve la librería
    #    (similarity es float 0-1; ya vienen ordenados de mayor a menor)
    if hasattr(resultados[0], "similarity") and resultados[0].similarity >= 0.7:
        return resultados[0]

    # 3) Coincidencia parcial fuerte
    for r in resultados:
        nombre = (r.game_name or "").lower()
        if titulo_lower in nombre or nombre in titulo_lower:
            return r

    return resultados[0]


class ScraperHLTB:
    """Scraper para tiempos de completado de HowLongToBeat."""

    def obtener_datos(self, juego: dict) -> dict:
        titulo = juego.get("titulo", "")

        if not titulo:
            raise ValueError(f"Juego {juego.get('id')} sin título")

        vacio = {"main": None, "main_extra": None, "completionist": None}
        espera = _ESPERA_BASE

        for intento in range(1, _MAX_REINTENTOS + 1):
            try:
                resultados = _buscar_sincrono(titulo)

                if not resultados:
                    # Reintentar con título simplificado (sin subtítulos)
                    titulo_corto = titulo.split(":")[0].split(" - ")[0].strip()
                    if titulo_corto != titulo:
                        resultados = _buscar_sincrono(titulo_corto)

                if not resultados:
                    log.warning(f"[HLTB] Sin resultados para: '{titulo}'")
                    return vacio

                juego_hltb = _seleccionar_resultado(resultados, titulo)
                if juego_hltb is None:
                    return vacio

                main          = _redondear(juego_hltb.main_story)
                main_extra    = _redondear(juego_hltb.main_extra)
                completionist = _redondear(juego_hltb.completionist)

                log.info(
                    f"[HLTB] {titulo} ({juego_hltb.game_name}) → "
                    f"Main: {main}h | Main+Extra: {main_extra}h | 100%%: {completionist}h"
                )
                return {
                    "main":          main,
                    "main_extra":    main_extra,
                    "completionist": completionist,
                }

            except Exception as e:
                log.warning(
                    f"[HLTB] Intento {intento}/{_MAX_REINTENTOS} falló "
                    f"para '{titulo}': {e}"
                )
                if intento < _MAX_REINTENTOS:
                    time.sleep(espera)
                    espera *= 2
                else:
                    return vacio

        return vacio
