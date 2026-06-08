"""
orquestador_brack.py — Orquestador del scraping (3 niveles de paralelismo anidados)
Dueño: Brack

ESQUEMA VISUAL:
    Nivel 1: ThreadPoolExecutor(10) sobre la lista de 200 juegos
        └─ Para cada juego:
            Nivel 2 + 3: ThreadPoolExecutor que lanza TODAS las fuentes a la vez
                ├─ Tarea: Metacritic    (Nivel 2)
                ├─ Tarea: HowLongToBeat (Nivel 2)
                ├─ Tarea: Steam         (Nivel 3)
                └─ Tarea: Nintendo eShop (Nivel 3)
            Esperar a que todas terminen, guardar en BD, siguiente juego.

Uso desde linea de comandos:
    python orquestador_brack.py                  # paralelo (default)
    python orquestador_brack.py --modo secuencial  # secuencial
    python orquestador_brack.py --modo comparar    # corre ambos y muestra speedup
    python orquestador_brack.py --limite 30      # solo 30 juegos (para pruebas)

Para mediciones DETALLADAS con reporte para el informe, ver:
    python medir_speedup.py
"""
import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
load_dotenv(override=True)

from repository import (
    obtener_todos_los_juegos,
    actualizar_precio,
    actualizar_url_tienda,
    guardar_score,
    guardar_hltb,
    loggear_fallo,
    init_pool,
)
from scrapers.steam import ScraperSteam
from scrapers.nintendo import ScraperNintendo
from scrapers.metacritic import ScraperMetacritic
from scrapers.hltb import ScraperHLTB


# Configuracion
WORKERS_JUEGOS  = 10
WORKERS_FUENTES = 6
TIMEOUT_SEGUNDOS = 10

FUENTES_TIENDAS = [
    ("steam",    ScraperSteam()),
    ("nintendo", ScraperNintendo()),
]
FUENTES_AUXILIARES = [
    ("metacritic", ScraperMetacritic()),
    ("hltb",       ScraperHLTB()),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def _guardar_resultado(juego_id: int, fuente: str, resultado: dict) -> None:
    if fuente in ("steam", "nintendo", "psn"):
        actualizar_precio(
            juego_id,
            fuente,
            resultado.get("precio"),
            resultado.get("precio_regular"),
        )
        # Si el scraper de Steam nos dio la URL directa, actualizarla en BD
        if fuente == "steam" and resultado.get("url_directa"):
            actualizar_url_tienda(juego_id, "steam", resultado["url_directa"])

    elif fuente == "metacritic":
        for consola, score in (resultado.get("scores") or {}).items():
            if score is not None:
                guardar_score(juego_id, consola, score)

    elif fuente == "hltb":
        guardar_hltb(
            juego_id,
            resultado.get("main"),
            resultado.get("main_extra"),
            resultado.get("completionist"),
        )


def procesar_juego(juego: dict) -> None:
    juego_id = juego["id"]
    log.info(f"→ Juego {juego_id} ({juego['titulo']})")

    with ThreadPoolExecutor(
        max_workers=WORKERS_FUENTES,
        thread_name_prefix=f"j{juego_id}",
    ) as executor:
        futuros = {}

        for nombre, scraper in FUENTES_TIENDAS:
            url = (juego.get("urls_tiendas") or {}).get(nombre)
            if url:
                futuros[executor.submit(scraper.obtener_precio, juego)] = nombre

        for nombre, scraper in FUENTES_AUXILIARES:
            futuros[executor.submit(scraper.obtener_datos, juego)] = nombre

        for fut in as_completed(futuros, timeout=TIMEOUT_SEGUNDOS * 4):
            nombre = futuros[fut]
            try:
                resultado = fut.result(timeout=TIMEOUT_SEGUNDOS)
                _guardar_resultado(juego_id, nombre, resultado)
            except Exception as e:
                log.warning(f"  ✗ {nombre} fallo para juego {juego_id}: {e}")
                loggear_fallo(juego_id, nombre, str(e))


def ejecutar_scraping_completo(juegos: list[dict] | None = None) -> dict:
    """Punto de entrada del modo PARALELO."""
    if juegos is None:
        juegos = obtener_todos_los_juegos()
    log.info(f"Iniciando scraping de {len(juegos)} juegos con {WORKERS_JUEGOS} workers")

    inicio = time.perf_counter()
    exitosos = 0
    fallidos  = 0

    with ThreadPoolExecutor(
        max_workers=WORKERS_JUEGOS,
        thread_name_prefix="juego",
    ) as executor:
        futuros = {executor.submit(procesar_juego, j): j["id"] for j in juegos}
        for fut in as_completed(futuros):
            juego_id = futuros[fut]
            try:
                fut.result()
                exitosos += 1
            except Exception as e:
                fallidos += 1
                log.error(f"Juego {juego_id} fallo completamente: {e}")

    duracion = time.perf_counter() - inicio
    log.info(f"=== PARALELO Terminado: {exitosos} OK, {fallidos} fallos en {duracion:.1f}s ===")
    return {
        "modo":               "paralelo",
        "juegos_procesados":  exitosos,
        "fallos":             fallidos,
        "duracion_segundos":  round(duracion, 1),
    }


def ejecutar_scraping_secuencial(juegos: list[dict] | None = None) -> dict:
    """Modo SECUENCIAL para comparar el speedup con el paralelo."""
    if juegos is None:
        juegos = obtener_todos_los_juegos()
    log.info(f"Iniciando scraping SECUENCIAL de {len(juegos)} juegos")

    inicio = time.perf_counter()
    exitosos = 0
    fallidos  = 0

    for juego in juegos:
        try:
            for nombre, scraper in FUENTES_TIENDAS:
                url = (juego.get("urls_tiendas") or {}).get(nombre)
                if url:
                    try:
                        r = scraper.obtener_precio(juego)
                        _guardar_resultado(juego["id"], nombre, r)
                    except Exception as e:
                        loggear_fallo(juego["id"], nombre, str(e))

            for nombre, scraper in FUENTES_AUXILIARES:
                try:
                    r = scraper.obtener_datos(juego)
                    _guardar_resultado(juego["id"], nombre, r)
                except Exception as e:
                    loggear_fallo(juego["id"], nombre, str(e))

            exitosos += 1
        except Exception as e:
            fallidos += 1
            log.error(f"Juego {juego['id']} fallo completamente: {e}")

    duracion = time.perf_counter() - inicio
    log.info(f"=== SECUENCIAL Terminado: {exitosos} OK, {fallidos} fallos en {duracion:.1f}s ===")
    return {
        "modo":               "secuencial",
        "juegos_procesados":  exitosos,
        "fallos":             fallidos,
        "duracion_segundos":  round(duracion, 1),
    }


# =====================================================================
# CLI
# =====================================================================

def main():
    init_pool()
    parser = argparse.ArgumentParser(description="Orquestador de scraping")
    parser.add_argument(
        "--modo",
        choices=["paralelo", "secuencial", "comparar"],
        default="paralelo",
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=None,
        help="Procesar solo los primeros N juegos (para pruebas)",
    )
    args = parser.parse_args()

    juegos = obtener_todos_los_juegos()
    if args.limite:
        juegos = juegos[: args.limite]

    if args.modo == "paralelo":
        ejecutar_scraping_completo(juegos)

    elif args.modo == "secuencial":
        ejecutar_scraping_secuencial(juegos)

    elif args.modo == "comparar":
        log.info("=== COMPARANDO PARALELO vs SECUENCIAL ===")
        r_seq = ejecutar_scraping_secuencial(juegos)
        r_par = ejecutar_scraping_completo(juegos)
        if r_par["duracion_segundos"] > 0:
            speedup = r_seq["duracion_segundos"] / r_par["duracion_segundos"]
            log.info(f"SPEEDUP: {speedup:.2f}x más rápido en modo paralelo")
            print(f"\nSEQUENCIAL: {r_seq['duracion_segundos']}s")
            print(f"PARALELO:   {r_par['duracion_segundos']}s")
            print(f"SPEEDUP:    {speedup:.2f}x")


if __name__ == "__main__":
    main()
