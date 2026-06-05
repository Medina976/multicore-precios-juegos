"""
medir_speedup.py — Mide y compara el rendimiento secuencial vs paralelo
Dueño: Brack

Esta es la pieza CLAVE para el informe: demuestra que los 3 niveles de
paralelismo del orquestador generan un speedup medible y reproducible.

Uso típico:
    python medir_speedup.py                       # 30 juegos, ambos modos
    python medir_speedup.py --juegos 50           # 50 juegos
    python medir_speedup.py --juegos 200          # todos (puede tardar mucho)
    python medir_speedup.py --solo paralelo       # solo paralelo (mas rapido)
    python medir_speedup.py --solo secuencial     # solo secuencial
    python medir_speedup.py --salida informe/     # carpeta donde se guardan los resultados

Salidas:
    informe/resultado_secuencial_YYYYMMDD_HHMMSS.json
    informe/resultado_paralelo_YYYYMMDD_HHMMSS.json
    informe/reporte_speedup_YYYYMMDD_HHMMSS.md

NOTAS:
- Por defecto procesa solo 30 juegos (de los 200) para que la prueba
  sea razonable en tiempo. Con 200 juegos la secuencial puede tardar 30+ min.
- Los resultados se guardan en BD (UPSERT) — corrida tras corrida actualiza
  los mismos registros, no duplica.
- El "modo paralelo" usa exactamente la misma logica que el orquestador
  en produccion (3 niveles anidados).
"""

import argparse
import json
import logging
import os
import platform
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Optional

from dotenv import load_dotenv

load_dotenv(override=True)

# Re-utilizamos lo que ya hay para que la medicion sea fiel al sistema real
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

# ============================================================
# Configuracion (igual que en el orquestador)
# ============================================================
WORKERS_JUEGOS = 10      # Nivel 1
WORKERS_FUENTES = 6      # Niveles 2 y 3
TIMEOUT_SEGUNDOS = 10

FUENTES_TIENDAS = [
    ("steam", ScraperSteam()),
    ("nintendo", ScraperNintendo()),
]
FUENTES_AUXILIARES = [
    ("metacritic", ScraperMetacritic()),
    ("hltb", ScraperHLTB()),
]
TODAS_LAS_FUENTES = ["steam", "nintendo", "metacritic", "hltb"]

logging.basicConfig(
    level=logging.WARNING,  # WARNING para no llenar la pantalla con info de cada juego
    format="%(asctime)s [%(threadName)s] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


# ============================================================
# Helpers para correr UNA fuente y reportar exito/fallo
# ============================================================
def _correr_fuente(
    fuente: str,
    scraper,
    juego: dict,
    metodo: str,
) -> tuple[bool, Optional[str], float]:
    """
    Corre una fuente y devuelve (exito, mensaje_error, segundos_tomados).
    Tambien escribe el resultado en la BD si tuvo exito.
    """
    t0 = time.perf_counter()
    try:
        r = getattr(scraper, metodo)(juego)
        # Despachar a la funcion correcta de repository.py
        if fuente in ("steam", "nintendo"):
            actualizar_precio(
                juego["id"],
                fuente,
                r.get("precio"),
                r.get("precio_regular"),
            )
        elif fuente == "metacritic":
            for consola, score in (r.get("scores") or {}).items():
                if score is not None:
                    guardar_score(juego["id"], consola, score)
        elif fuente == "hltb":
            guardar_hltb(
                juego["id"],
                r.get("main"),
                r.get("main_extra"),
                r.get("completionist"),
            )
        return True, None, time.perf_counter() - t0
    except Exception as e:
        # Guardamos el fallo en la BD para el log
        try:
            loggear_fallo(juego["id"], fuente, str(e))
        except Exception:
            pass  # si tambien falla el log no nos vamos a quedar pegados
        return False, str(e), time.perf_counter() - t0


# ============================================================
# MODO SECUENCIAL
# ============================================================
def medir_secuencial(juegos: list[dict]) -> dict:
    """Procesa los juegos uno por uno, sin paralelismo. Es la base del speedup."""
    print(f"\n{'='*60}")
    print(f"MODO SECUENCIAL — {len(juegos)} juegos")
    print(f"{'='*60}")

    stats = {
        "modo": "secuencial",
        "juegos_intentados": len(juegos),
        "juegos_con_al_menos_un_exito": 0,
        "juegos_con_fallo_total": 0,
        "exitos_por_fuente": defaultdict(int),
        "fallos_por_fuente": defaultdict(int),
        "tiempo_total_por_fuente": defaultdict(float),
        "workers_nivel_1": 1,
        "workers_nivel_2_3": 1,
    }

    inicio = time.perf_counter()

    for i, juego in enumerate(juegos, 1):
        if i % 5 == 0 or i == len(juegos):
            transcurrido = time.perf_counter() - inicio
            print(f"  [{i}/{len(juegos)}] {juego['titulo'][:50]}  ({transcurrido:.1f}s)")

        algun_exito = False

        # Tiendas (Nivel 3) — secuencialmente
        for fuente, scraper in FUENTES_TIENDAS:
            url = (juego.get("urls_tiendas") or {}).get(fuente)
            if not url:
                continue
            ok, err, dt = _correr_fuente(fuente, scraper, juego, "obtener_precio")
            stats["tiempo_total_por_fuente"][fuente] += dt
            if ok:
                stats["exitos_por_fuente"][fuente] += 1
                algun_exito = True
            else:
                stats["fallos_por_fuente"][fuente] += 1

        # Auxiliares (Nivel 2) — secuencialmente
        for fuente, scraper in FUENTES_AUXILIARES:
            ok, err, dt = _correr_fuente(fuente, scraper, juego, "obtener_datos")
            stats["tiempo_total_por_fuente"][fuente] += dt
            if ok:
                stats["exitos_por_fuente"][fuente] += 1
                algun_exito = True
            else:
                stats["fallos_por_fuente"][fuente] += 1

        if algun_exito:
            stats["juegos_con_al_menos_un_exito"] += 1
        else:
            stats["juegos_con_fallo_total"] += 1

    stats["duracion_segundos"] = round(time.perf_counter() - inicio, 2)
    stats["tiempo_promedio_por_juego"] = round(
        stats["duracion_segundos"] / max(1, len(juegos)), 3
    )

    # convertir defaultdicts a dict normales para que JSON los serialize
    stats["exitos_por_fuente"] = dict(stats["exitos_por_fuente"])
    stats["fallos_por_fuente"] = dict(stats["fallos_por_fuente"])
    stats["tiempo_total_por_fuente"] = {
        k: round(v, 2) for k, v in stats["tiempo_total_por_fuente"].items()
    }

    print(f"\n  ✔ Secuencial termino en {stats['duracion_segundos']}s")
    return stats


# ============================================================
# MODO PARALELO (los 3 niveles)
# ============================================================
def medir_paralelo(juegos: list[dict]) -> dict:
    """Mismo trabajo, pero usando los 3 niveles de paralelismo anidados."""
    print(f"\n{'='*60}")
    print(f"MODO PARALELO — {len(juegos)} juegos")
    print(f"  Nivel 1: {WORKERS_JUEGOS} juegos simultaneos")
    print(f"  Niveles 2-3: {WORKERS_FUENTES} fuentes en paralelo por juego")
    print(f"{'='*60}")

    stats = {
        "modo": "paralelo",
        "juegos_intentados": len(juegos),
        "juegos_con_al_menos_un_exito": 0,
        "juegos_con_fallo_total": 0,
        "exitos_por_fuente": defaultdict(int),
        "fallos_por_fuente": defaultdict(int),
        "tiempo_total_por_fuente": defaultdict(float),
        "workers_nivel_1": WORKERS_JUEGOS,
        "workers_nivel_2_3": WORKERS_FUENTES,
    }
    lock = Lock()  # para escribir stats desde varios hilos sin pisarse

    def procesar_un_juego(juego: dict) -> bool:
        """Nivel 2 + 3: lanza las 4 fuentes de UN juego en paralelo."""
        algun_exito_local = False
        resultados_locales = []

        with ThreadPoolExecutor(
            max_workers=WORKERS_FUENTES,
            thread_name_prefix=f"j{juego['id']}",
        ) as ex:
            futuros = {}

            for fuente, scraper in FUENTES_TIENDAS:
                url = (juego.get("urls_tiendas") or {}).get(fuente)
                if url:
                    fut = ex.submit(_correr_fuente, fuente, scraper, juego, "obtener_precio")
                    futuros[fut] = fuente

            for fuente, scraper in FUENTES_AUXILIARES:
                fut = ex.submit(_correr_fuente, fuente, scraper, juego, "obtener_datos")
                futuros[fut] = fuente

            for fut in as_completed(futuros, timeout=TIMEOUT_SEGUNDOS * 4):
                fuente = futuros[fut]
                try:
                    ok, err, dt = fut.result(timeout=TIMEOUT_SEGUNDOS)
                except Exception as e:
                    ok, err, dt = False, str(e), 0.0
                resultados_locales.append((fuente, ok, dt))
                if ok:
                    algun_exito_local = True

        # Actualizar stats compartidos en una sola seccion critica corta
        with lock:
            for fuente, ok, dt in resultados_locales:
                stats["tiempo_total_por_fuente"][fuente] += dt
                if ok:
                    stats["exitos_por_fuente"][fuente] += 1
                else:
                    stats["fallos_por_fuente"][fuente] += 1
            if algun_exito_local:
                stats["juegos_con_al_menos_un_exito"] += 1
            else:
                stats["juegos_con_fallo_total"] += 1

        return algun_exito_local

    inicio = time.perf_counter()

    # Nivel 1: pool de juegos
    procesados = 0
    with ThreadPoolExecutor(
        max_workers=WORKERS_JUEGOS,
        thread_name_prefix="juego",
    ) as ex:
        futuros = {ex.submit(procesar_un_juego, j): j["id"] for j in juegos}
        for fut in as_completed(futuros):
            procesados += 1
            if procesados % 10 == 0 or procesados == len(juegos):
                transcurrido = time.perf_counter() - inicio
                print(f"  [{procesados}/{len(juegos)}] procesados  ({transcurrido:.1f}s)")
            try:
                fut.result()
            except Exception as e:
                log.error(f"Juego {futuros[fut]} fallo completamente: {e}")

    stats["duracion_segundos"] = round(time.perf_counter() - inicio, 2)
    stats["tiempo_promedio_por_juego"] = round(
        stats["duracion_segundos"] / max(1, len(juegos)), 3
    )

    stats["exitos_por_fuente"] = dict(stats["exitos_por_fuente"])
    stats["fallos_por_fuente"] = dict(stats["fallos_por_fuente"])
    stats["tiempo_total_por_fuente"] = {
        k: round(v, 2) for k, v in stats["tiempo_total_por_fuente"].items()
    }

    print(f"\n  ✔ Paralelo termino en {stats['duracion_segundos']}s")
    return stats


# ============================================================
# Generador del reporte markdown
# ============================================================
def generar_reporte_md(
    stats_seq: Optional[dict],
    stats_par: Optional[dict],
    n_juegos: int,
    timestamp: str,
) -> str:
    """Arma el reporte markdown que se pega al informe del profe."""
    lineas = []
    lineas.append(f"# Reporte de Speedup — Proyecto Multicore\n")
    lineas.append(f"_Generado: {timestamp}_\n")
    lineas.append("## Configuracion del experimento\n")
    lineas.append(f"- **Juegos procesados:** {n_juegos}")
    lineas.append(f"- **Workers Nivel 1 (juegos):** {WORKERS_JUEGOS}")
    lineas.append(f"- **Workers Niveles 2-3 (fuentes):** {WORKERS_FUENTES}")
    lineas.append(f"- **Fuentes scrapeadas:** {', '.join(TODAS_LAS_FUENTES)}")
    lineas.append(f"- **Maquina:** {platform.system()} {platform.release()} — {platform.processor() or 'CPU desconocido'}")
    lineas.append(f"- **Python:** {sys.version.split()[0]}")
    lineas.append("")

    # Tabla resumen
    lineas.append("## Resultados resumidos\n")
    lineas.append("| Modo | Duracion | Juegos OK | Juegos con todos fallos | Tiempo prom. por juego |")
    lineas.append("|---|---:|---:|---:|---:|")
    if stats_seq:
        lineas.append(
            f"| Secuencial | {stats_seq['duracion_segundos']}s "
            f"| {stats_seq['juegos_con_al_menos_un_exito']} "
            f"| {stats_seq['juegos_con_fallo_total']} "
            f"| {stats_seq['tiempo_promedio_por_juego']}s |"
        )
    if stats_par:
        lineas.append(
            f"| Paralelo | {stats_par['duracion_segundos']}s "
            f"| {stats_par['juegos_con_al_menos_un_exito']} "
            f"| {stats_par['juegos_con_fallo_total']} "
            f"| {stats_par['tiempo_promedio_por_juego']}s |"
        )
    lineas.append("")

    # Metricas de speedup (solo si tenemos ambos)
    if stats_seq and stats_par:
        speedup = stats_seq["duracion_segundos"] / max(0.001, stats_par["duracion_segundos"])
        eficiencia = speedup / WORKERS_JUEGOS
        lineas.append("## Metricas de paralelismo\n")
        lineas.append(f"- **Speedup** = T_secuencial / T_paralelo = "
                      f"{stats_seq['duracion_segundos']} / {stats_par['duracion_segundos']} = "
                      f"**{speedup:.2f}x**")
        lineas.append(f"- **Eficiencia (respecto al Nivel 1)** = Speedup / N_workers = "
                      f"{speedup:.2f} / {WORKERS_JUEGOS} = "
                      f"**{eficiencia*100:.1f}%**")
        lineas.append(f"- **Tiempo ahorrado:** "
                      f"{stats_seq['duracion_segundos'] - stats_par['duracion_segundos']:.1f}s "
                      f"({(1 - 1/speedup)*100:.1f}% menos)")
        lineas.append("")

    # Por fuente
    lineas.append("## Exitos y fallos por fuente\n")
    lineas.append("| Fuente | Modo | Exitos | Fallos | Tiempo acumulado |")
    lineas.append("|---|---|---:|---:|---:|")
    for fuente in TODAS_LAS_FUENTES:
        if stats_seq:
            lineas.append(
                f"| {fuente} | secuencial "
                f"| {stats_seq['exitos_por_fuente'].get(fuente, 0)} "
                f"| {stats_seq['fallos_por_fuente'].get(fuente, 0)} "
                f"| {stats_seq['tiempo_total_por_fuente'].get(fuente, 0)}s |"
            )
        if stats_par:
            lineas.append(
                f"| {fuente} | paralelo "
                f"| {stats_par['exitos_por_fuente'].get(fuente, 0)} "
                f"| {stats_par['fallos_por_fuente'].get(fuente, 0)} "
                f"| {stats_par['tiempo_total_por_fuente'].get(fuente, 0)}s |"
            )
    lineas.append("")

    # Analisis (texto fijo para el informe)
    if stats_seq and stats_par:
        speedup = stats_seq["duracion_segundos"] / max(0.001, stats_par["duracion_segundos"])
        lineas.append("## Analisis\n")
        lineas.append(
            f"El paralelismo implementado en `orquestador_brack.py` arroja un "
            f"speedup de **{speedup:.2f}x** sobre la version secuencial. "
            f"Como las tareas son predominantemente I/O-bound (esperar respuestas HTTP "
            f"de Steam, Nintendo, Metacritic y HLTB), el GIL de Python NO es un "
            f"problema: los hilos liberan el GIL durante las llamadas de red, lo que "
            f"permite que el `ThreadPoolExecutor` solape varias esperas simultaneamente.\n"
        )
        lineas.append(
            f"Los 3 niveles de paralelismo (10 juegos en Nivel 1 x 4 fuentes en "
            f"Niveles 2-3) generan, en teoria, hasta {WORKERS_JUEGOS} x "
            f"{min(WORKERS_FUENTES, 4)} = {WORKERS_JUEGOS * min(WORKERS_FUENTES, 4)} "
            f"requests HTTP en vuelo en cualquier momento. La eficiencia "
            f"observada ({(speedup/WORKERS_JUEGOS)*100:.1f}%) es razonable "
            f"considerando overheads de sincronizacion, locks de la BD y la "
            f"variabilidad de latencia entre las APIs externas.\n"
        )

    return "\n".join(lineas)


# ============================================================
# Main
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="Mide el speedup del orquestador paralelo vs version secuencial.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--juegos", type=int, default=30,
        help="Cuantos juegos procesar (default: 30). Usa 200 para correr todos.",
    )
    parser.add_argument(
        "--solo", choices=["paralelo", "secuencial"], default=None,
        help="Correr solo uno de los dos modos.",
    )
    parser.add_argument(
        "--salida", default="informe",
        help="Carpeta donde guardar reportes (default: informe/).",
    )
    args = parser.parse_args()

    # Crear carpeta de salida
    salida = Path(args.salida)
    salida.mkdir(exist_ok=True)

    # Inicializar pool con suficientes conexiones para el modo paralelo
    init_pool(minconn=4, maxconn=30)

    print(f"Trayendo juegos de la BD...")
    todos = obtener_todos_los_juegos()
    if not todos:
        print("ERROR: la BD no tiene juegos. Corre primero: python seeding.py")
        sys.exit(1)

    n = min(args.juegos, len(todos))
    juegos = todos[:n]
    print(f"Procesare {n} de {len(todos)} juegos disponibles.\n")

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")

    stats_seq = None
    stats_par = None

    # Correr secuencial primero (el paralelo "calienta" caches de DNS, etc.)
    if args.solo != "paralelo":
        stats_seq = medir_secuencial(juegos)
        ruta_seq = salida / f"resultado_secuencial_{timestamp_str}.json"
        ruta_seq.write_text(json.dumps(stats_seq, indent=2, ensure_ascii=False))
        print(f"  → {ruta_seq}")

    if args.solo != "secuencial":
        stats_par = medir_paralelo(juegos)
        ruta_par = salida / f"resultado_paralelo_{timestamp_str}.json"
        ruta_par.write_text(json.dumps(stats_par, indent=2, ensure_ascii=False))
        print(f"  → {ruta_par}")

    # Reporte markdown
    reporte = generar_reporte_md(stats_seq, stats_par, n, timestamp_str)
    ruta_reporte = salida / f"reporte_speedup_{timestamp_str}.md"
    ruta_reporte.write_text(reporte)

    print(f"\n{'='*60}")
    print(f"REPORTE GENERADO")
    print(f"{'='*60}")
    print(f"  {ruta_reporte}")
    if stats_seq and stats_par:
        speedup = stats_seq["duracion_segundos"] / max(0.001, stats_par["duracion_segundos"])
        print(f"\n  >>> SPEEDUP: {speedup:.2f}x <<<")
    print()


if __name__ == "__main__":
    main()
