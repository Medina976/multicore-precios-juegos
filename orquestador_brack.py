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
WORKERS_JUEGOS = 10       # Nivel 1: cuantos juegos en paralelo
WORKERS_FUENTES = 6       # Niveles 2 y 3: cuantas fuentes a la vez por juego
TIMEOUT_SEGUNDOS = 10

FUENTES_TIENDAS = [
    ("steam", ScraperSteam()),
    ("nintendo", ScraperNintendo()),
]
FUENTES_AUXILIARES = [
    ("metacritic", ScraperMetacritic()),
    ("hltb", ScraperHLTB()),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


# =====================================================================
# Helpers: ejecutar UNA fuente y guardar resultado
# =====================================================================
def _ejecutar_fuente(fuente: str, scraper, juego: dict, metodo: str) -> None:
    """Corre una fuente, guarda el resultado o loggea el fallo."""
    try:
        resultado = getattr(scraper, metodo)(juego)
        _guardar_resultado(juego["id"], fuente, resultado)
    except Exception as e:
        log.warning(f"  ✗ {fuente} fallo para juego {juego['id']}: {e}")
        loggear_fallo(juego["id"], fuente, str(e))


def _guardar_resultado(juego_id: int, fuente: str, resultado: dict) -> None:
    """Despacha el resultado a la funcion correcta de repository.py."""
    if fuente in ("steam", "nintendo", "psn"):
        actualizar_precio(
            juego_id,
            fuente,
            resultado.get("precio"),
            resultado.get("precio_regular"),
        )
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


# =====================================================================
# Nivel 2 + 3: procesar TODAS las fuentes de UN juego en paralelo
# =====================================================================
def procesar_juego(juego: dict) -> None:
    """Lanza todas las fuentes de un juego en paralelo y guarda los resultados."""
    juego_id = juego["id"]
    log.info(f"→ Juego {juego_id} ({juego['titulo']})")

    with ThreadPoolExecutor(
        max_workers=WORKERS_FUENTES,
        thread_name_prefix=f"j{juego_id}",
    ) as executor:
        futuros = {}

        # Tiendas (Nivel 3) — solo si hay URL configurada
        for nombre, scraper in FUENTES_TIENDAS:
            url = (juego.get("urls_tiendas") or {}).get(nombre)
            if url:
                futuros[executor.submit(scraper.obtener_precio, juego)] = nombre

        # Fuentes auxiliares (Nivel 2) — siempre se intentan
        for nombre, scraper in FUENTES_AUXILIARES:
            futuros[executor.submit(scraper.obtener_datos, juego)] = nombre

        # Recoger resultados a medida que llegan
        for fut in as_completed(futuros, timeout=TIMEOUT_SEGUNDOS * 4):
            nombre = futuros[fut]
            try:
                resultado = fut.result(timeout=TIMEOUT_SEGUNDOS)
                _guardar_resultado(juego_id, nombre, resultado)
            except Exception as e:
                log.warning(f"  ✗ {nombre} fallo para juego {juego_id}: {e}")
                loggear_fallo(juego_id, nombre, str(e))
                # Si una fuente falla, las otras siguen. Nunca "todo o nada".


# =====================================================================
# Nivel 1: pool de juegos
# =====================================================================
def ejecutar_scraping_completo(juegos: list[dict] | None = None) -> dict:
    """Punto de entrada del modo PARALELO."""
    if juegos is None:
        juegos = obtener_todos_los_juegos()
    log.info(f"Iniciando scraping de {len(juegos)} juegos con {WORKERS_JUEGOS} workers")

    inicio = time.perf_counter()
    exitosos = 0
    fallidos = 0

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
        "modo": "paralelo",
        "juegos_procesados": exitosos,
        "fallos": fallidos,
        "duracion_segundos": round(duracion, 1),
    }


# =====================================================================
# Version SECUENCIAL para la comparativa del informe
# (esto es lo que demuestra que el paralelismo sirve)
# =====================================================================
def ejecutar_secuencial(juegos: list[dict] | None = None) -> dict:
    """Misma logica, sin paralelismo. Para medir el speedup."""
    if juegos is None:
        juegos = obtener_todos_los_juegos()
    log.info(f"Iniciando scraping SECUENCIAL de {len(juegos)} juegos")

    inicio = time.perf_counter()
    exitosos = 0
    fallidos = 0

    for j in juegos:
        try:
            algun_exito = False

            # Tiendas — chequeamos URL igual que el modo paralelo (consistencia)
            for nombre, scraper in FUENTES_TIENDAS:
                url = (j.get("urls_tiendas") or {}).get(nombre)
                if not url:
                    continue
                try:
                    r = scraper.obtener_precio(j)
                    _guardar_resultado(j["id"], nombre, r)
                    algun_exito = True
                except Exception as e:
                    log.warning(f"  ✗ {nombre} fallo para juego {j['id']}: {e}")
                    loggear_fallo(j["id"], nombre, str(e))

            # Auxiliares
            for nombre, scraper in FUENTES_AUXILIARES:
                try:
                    r = scraper.obtener_datos(j)
                    _guardar_resultado(j["id"], nombre, r)
                    algun_exito = True
                except Exception as e:
                    log.warning(f"  ✗ {nombre} fallo para juego {j['id']}: {e}")
                    loggear_fallo(j["id"], nombre, str(e))

            if algun_exito:
                exitosos += 1
            else:
                fallidos += 1
        except Exception as e:
            fallidos += 1
            log.error(f"Juego {j['id']} fallo completamente: {e}")

    duracion = time.perf_counter() - inicio
    log.info(f"=== SECUENCIAL Terminado: {exitosos} OK, {fallidos} fallos en {duracion:.1f}s ===")
    return {
        "modo": "secuencial",
        "juegos_procesados": exitosos,
        "fallos": fallidos,
        "duracion_segundos": round(duracion, 1),
    }


# =====================================================================
# Main con CLI
# =====================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Orquestador de scraping con 3 niveles de paralelismo.",
    )
    parser.add_argument(
        "--modo", choices=["paralelo", "secuencial", "comparar"],
        default="paralelo",
        help="paralelo (default), secuencial, o comparar (corre ambos y muestra speedup)",
    )
    parser.add_argument(
        "--limite", type=int, default=None,
        help="Limitar a N juegos (para pruebas rapidas). Default: todos.",
    )
    args = parser.parse_args()

    init_pool(minconn=4, maxconn=30)

    juegos = obtener_todos_los_juegos()
    if args.limite:
        juegos = juegos[:args.limite]
        log.info(f"Limitando a {len(juegos)} juegos (modo prueba)")

    if args.modo == "paralelo":
        ejecutar_scraping_completo(juegos)

    elif args.modo == "secuencial":
        ejecutar_secuencial(juegos)

    elif args.modo == "comparar":
        # Corremos secuencial primero (el paralelo se beneficia del calentamiento de DNS)
        r_seq = ejecutar_secuencial(juegos)
        r_par = ejecutar_scraping_completo(juegos)
        speedup = r_seq["duracion_segundos"] / max(0.001, r_par["duracion_segundos"])
        print(f"\n{'='*50}")
        print(f"COMPARATIVA ({len(juegos)} juegos)")
        print(f"{'='*50}")
        print(f"  Secuencial : {r_seq['duracion_segundos']}s")
        print(f"  Paralelo   : {r_par['duracion_segundos']}s")
        print(f"  Speedup    : {speedup:.2f}x")
        print(f"  Eficiencia : {(speedup/WORKERS_JUEGOS)*100:.1f}% (sobre Nivel 1)")
        print()


if __name__ == "__main__":
    main()
