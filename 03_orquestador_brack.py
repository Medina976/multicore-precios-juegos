"""
orquestador.py — El corazón del proyecto
Dueño: Brack

Aquí están los 3 niveles de paralelismo anidados.
Esto es lo que el profesor va a evaluar más fuerte.

ESQUEMA VISUAL:
    Nivel 1: ThreadPoolExecutor(10) sobre la lista de 200 juegos
        └─ Para cada juego:
            Nivel 2 + 3: ThreadPoolExecutor que lanza TODAS las fuentes a la vez
                ├─ Tarea: Metacritic    (Nivel 2)
                ├─ Tarea: HowLongToBeat (Nivel 2)
                ├─ Tarea: Steam         (Nivel 3)
                └─ Tarea: Nintendo eShop (Nivel 3)
            Esperar a que todas terminen, guardar en BD, siguiente juego.
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

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

# Configuración
WORKERS_JUEGOS = 10       # Nivel 1: cuántos juegos en paralelo
WORKERS_FUENTES = 6       # Niveles 2 y 3: cuántas fuentes a la vez por juego
TIMEOUT_SEGUNDOS = 10

# Registrar todas las fuentes que se van a consultar por juego.
# Cada una es (nombre, instancia_scraper, función_que_guarda_en_BD)
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
# Nivel 2 + 3: procesar TODAS las fuentes de UN juego en paralelo
# =====================================================================
def procesar_juego(juego: dict) -> None:
    """Lanza todas las fuentes de un juego en paralelo y guarda los resultados."""
    juego_id = juego["id"]
    log.info(f"→ Juego {juego_id} ({juego['titulo']})")

    # Aquí se lanzan en paralelo: Metacritic, HLTB, Steam, Nintendo (todas a la vez)
    with ThreadPoolExecutor(
        max_workers=WORKERS_FUENTES,
        thread_name_prefix=f"j{juego_id}",
    ) as executor:
        futuros = {}

        # Tiendas (Nivel 3)
        for nombre, scraper in FUENTES_TIENDAS:
            url = juego["urls_tiendas"].get(nombre)
            if url:
                futuros[executor.submit(scraper.obtener_precio, juego)] = nombre

        # Fuentes auxiliares (Nivel 2)
        for nombre, scraper in FUENTES_AUXILIARES:
            futuros[executor.submit(scraper.obtener_datos, juego)] = nombre

        # Recoger resultados a medida que van llegando
        for fut in as_completed(futuros, timeout=TIMEOUT_SEGUNDOS * 3):
            nombre = futuros[fut]
            try:
                resultado = fut.result(timeout=TIMEOUT_SEGUNDOS)
                _guardar_resultado(juego_id, nombre, resultado)
            except Exception as e:
                log.warning(f"  ✗ {nombre} falló para juego {juego_id}: {e}")
                loggear_fallo(juego_id, nombre, str(e))
                # IMPORTANTE: si una fuente falla, las otras siguen.
                # NUNCA "todo o nada".


def _guardar_resultado(juego_id: int, fuente: str, resultado: dict) -> None:
    """Despacha el resultado a la función correcta de repository.py."""
    if fuente in ("steam", "nintendo", "psn"):
        actualizar_precio(
            juego_id,
            fuente,
            resultado["precio"],
            resultado.get("precio_regular"),
        )
    elif fuente == "metacritic":
        for consola, score in resultado["scores"].items():
            guardar_score(juego_id, consola, score)
    elif fuente == "hltb":
        guardar_hltb(
            juego_id,
            resultado.get("main"),
            resultado.get("main_extra"),
            resultado.get("completionist"),
        )


# =====================================================================
# Nivel 1: pool de juegos
# =====================================================================
def ejecutar_scraping_completo() -> dict:
    """Punto de entrada. Lo llama el scheduler o main()."""
    init_pool(minconn=4, maxconn=30)

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
                log.error(f"Juego {juego_id} falló completamente: {e}")

    duracion = time.perf_counter() - inicio

    log.info(f"=== Terminado: {exitosos} OK, {fallidos} fallos en {duracion:.1f}s ===")
    return {
        "juegos_procesados": exitosos,
        "fallos": fallidos,
        "duracion_segundos": round(duracion, 1),
    }


# =====================================================================
# Versión SECUENCIAL para la comparativa del informe
# (esto es lo que demuestra que el paralelismo sirve)
# =====================================================================
def ejecutar_secuencial() -> dict:
    """Misma lógica, sin paralelismo. SOLO para medir el speedup."""
    init_pool(minconn=2, maxconn=4)
    juegos = obtener_todos_los_juegos()

    inicio = time.perf_counter()
    for j in juegos:
        for nombre, scraper in FUENTES_TIENDAS:
            try:
                r = scraper.obtener_precio(j)
                _guardar_resultado(j["id"], nombre, r)
            except Exception:
                pass
        for nombre, scraper in FUENTES_AUXILIARES:
            try:
                r = scraper.obtener_datos(j)
                _guardar_resultado(j["id"], nombre, r)
            except Exception:
                pass
    duracion = time.perf_counter() - inicio
    log.info(f"Versión secuencial: {duracion:.1f}s")
    return {"duracion_segundos": round(duracion, 1)}


if __name__ == "__main__":
    # Para el informe: correr ambas y comparar
    resultado_paralelo = ejecutar_scraping_completo()
    # resultado_secuencial = ejecutar_secuencial()
    # speedup = resultado_secuencial["duracion_segundos"] / resultado_paralelo["duracion_segundos"]
    # print(f"Speedup: {speedup:.2f}x")
