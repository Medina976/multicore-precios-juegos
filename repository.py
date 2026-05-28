"""
repository.py — Capa de acceso a datos
Dueño: Anthony

Brack y Felipe NO escriben SQL. Solo importan estas funciones:

    from repository import obtener_todos_los_juegos, actualizar_precio, ...

Esto desacopla a los tres y permite trabajar en paralelo.
"""
from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

# =====================================================================
# Conexión: pool de conexiones (thread-safe — clave para el paralelismo de Brack)
# =====================================================================
# Usar las credenciales de Supabase (vienen del .env, no hardcoded)
import os
from dotenv import load_dotenv

load_dotenv()

_pool: Optional[ThreadedConnectionPool] = None


def init_pool(minconn: int = 2, maxconn: int = 20) -> None:
    """Se llama UNA vez al inicio del programa."""
    global _pool
    _pool = ThreadedConnectionPool(
        minconn,
        maxconn,
        dsn=os.environ["DATABASE_URL"],  # de Supabase
    )


@contextmanager
def _conn():
    """Saca una conexión del pool, la devuelve al terminar."""
    if _pool is None:
        init_pool()
    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


# =====================================================================
# LECTURAS (las usa Brack para saber qué scrapear; las usa Felipe para la API)
# =====================================================================
def obtener_todos_los_juegos() -> list[dict]:
    """Devuelve todos los juegos con sus URLs de tiendas. Brack itera sobre esto."""
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT id, titulo, consolas, precio_sugerido,
                   urls_tiendas, metacritic_url, hltb_url, imagen_url
            FROM juegos
            ORDER BY id
        """)
        return [dict(row) for row in cur.fetchall()]


def obtener_juego_por_id(juego_id: int) -> Optional[dict]:
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM juegos WHERE id = %s", (juego_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def obtener_precios_por_juego(juego_id: int) -> list[dict]:
    """Para la vista de detalle. Felipe la llama."""
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT tienda, precio, precio_regular, en_oferta,
                   porcentaje_descuento, fecha_scraping
            FROM precios_actuales
            WHERE juego_id = %s
            ORDER BY precio ASC NULLS LAST
        """, (juego_id,))
        return [dict(row) for row in cur.fetchall()]


def obtener_scores_por_juego(juego_id: int) -> list[dict]:
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT consola, score_metacritic, fecha
            FROM scores
            WHERE juego_id = %s
        """, (juego_id,))
        return [dict(row) for row in cur.fetchall()]


def obtener_hltb_por_juego(juego_id: int) -> Optional[dict]:
    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT horas_main, horas_main_extra, horas_completionist, fecha
            FROM hltb
            WHERE juego_id = %s
        """, (juego_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def ultima_actualizacion() -> Optional[str]:
    """Felipe la usa para mostrar 'Última actualización: hace X min'."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT MAX(fecha_scraping) FROM precios_actuales")
        return cur.fetchone()[0]


# =====================================================================
# ESCRITURAS (las usa Brack desde los scrapers, en paralelo)
# Todas hacen UPSERT — no falla si ya existía
# =====================================================================
def actualizar_precio(
    juego_id: int,
    tienda: str,
    precio: float,
    precio_regular: float | None = None,
) -> None:
    """Brack la llama cada vez que un scraper de tienda termina con éxito."""
    # Detectar oferta comparando con el MSRP guardado en la tabla juegos
    en_oferta = False
    descuento = None

    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT precio_sugerido FROM juegos WHERE id = %s", (juego_id,))
        row = cur.fetchone()
        sugerido = row[0] if row else None

        if sugerido and precio < float(sugerido):
            en_oferta = True
            descuento = round((float(sugerido) - precio) / float(sugerido) * 100, 2)

        cur.execute("""
            INSERT INTO precios_actuales
                (juego_id, tienda, precio, precio_regular, en_oferta,
                 porcentaje_descuento, fecha_scraping)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (juego_id, tienda) DO UPDATE SET
                precio = EXCLUDED.precio,
                precio_regular = EXCLUDED.precio_regular,
                en_oferta = EXCLUDED.en_oferta,
                porcentaje_descuento = EXCLUDED.porcentaje_descuento,
                fecha_scraping = NOW()
        """, (juego_id, tienda, precio, precio_regular, en_oferta, descuento))


def guardar_score(juego_id: int, consola: str, score: int) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO scores (juego_id, consola, score_metacritic, fecha)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (juego_id, consola) DO UPDATE SET
                score_metacritic = EXCLUDED.score_metacritic,
                fecha = NOW()
        """, (juego_id, consola, score))


def guardar_hltb(
    juego_id: int,
    horas_main: float | None,
    horas_main_extra: float | None,
    horas_completionist: float | None,
) -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO hltb (juego_id, horas_main, horas_main_extra,
                              horas_completionist, fecha)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (juego_id) DO UPDATE SET
                horas_main = EXCLUDED.horas_main,
                horas_main_extra = EXCLUDED.horas_main_extra,
                horas_completionist = EXCLUDED.horas_completionist,
                fecha = NOW()
        """, (juego_id, horas_main, horas_main_extra, horas_completionist))


def loggear_fallo(juego_id: int | None, fuente: str, mensaje: str) -> None:
    """Brack la llama cuando un scraper falla, para que quede registro."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO log_scraping (juego_id, fuente, nivel, mensaje)
            VALUES (%s, %s, 'ERROR', %s)
        """, (juego_id, fuente, mensaje))
