"""
repository.py — Capa de acceso a datos
Dueño: Anthony

Brack y Felipe NO escriben SQL. Solo importan estas funciones:

    from repository import obtener_todos_los_juegos, actualizar_precio, ...

Esto desacopla a los tres y permite trabajar en paralelo.
"""
from contextlib import contextmanager
from typing import Optional
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

# =====================================================================
# Conexión: pool de conexiones (thread-safe — clave para el paralelismo de Brack)
# =====================================================================
# Usar las credenciales de Supabase (vienen del .env, no hardcoded)
import os
from dotenv import load_dotenv

# Carga el .env desde la misma carpeta donde está este archivo (repository.py)
# Sin esto, load_dotenv() puede buscar en el directorio de trabajo incorrecto.
_ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

_pool: Optional[ThreadedConnectionPool] = None


def init_pool(minconn: int = 2, maxconn: int = 20) -> None:
    """Se llama UNA vez al inicio del programa."""
    global _pool
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise EnvironmentError(
            "No se encontró DATABASE_URL.\n"
            f"Verifica que exista el archivo .env en: {_ENV_PATH}\n"
            "Contenido esperado: DATABASE_URL=postgresql://..."
        )
    _pool = ThreadedConnectionPool(
        minconn,
        maxconn,
        dsn=db_url,
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


# =====================================================================
# PARA LA API DE FELIPE
# =====================================================================
def obtener_lista_para_api(
    plataforma: Optional[str] = None,
    tienda: Optional[str] = None,
    en_oferta: Optional[bool] = None,
    orden: str = "nombre",
    limit: int = 50,
) -> list[dict]:
    """
    Para el endpoint GET /api/juegos.
    Devuelve los juegos con su mejor precio y score, con filtros y ordenamiento.
    Felipe llama a esta función desde FastAPI — no escribe SQL.
    """
    conditions = []
    params: list = []

    if plataforma:
        conditions.append("%s = ANY(j.consolas)")
        params.append(plataforma)

    if tienda:
        conditions.append(
            "EXISTS(SELECT 1 FROM precios_actuales p2 "
            "WHERE p2.juego_id = j.id AND p2.tienda = %s AND p2.precio IS NOT NULL)"
        )
        params.append(tienda)

    if en_oferta is True:
        conditions.append(
            "EXISTS(SELECT 1 FROM precios_actuales p2 "
            "WHERE p2.juego_id = j.id AND p2.en_oferta = true)"
        )
    elif en_oferta is False:
        conditions.append(
            "NOT EXISTS(SELECT 1 FROM precios_actuales p2 "
            "WHERE p2.juego_id = j.id AND p2.en_oferta = true)"
        )

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    order_map = {
        "nombre":    "j.titulo ASC",
        "precio":    "mejor_precio ASC NULLS LAST",
        "descuento": "porcentaje_descuento DESC NULLS LAST",
        "score":     "mejor_score DESC NULLS LAST",
    }
    order_by = order_map.get(orden, "j.titulo ASC")
    params.append(limit)

    query = f"""
        SELECT
            j.id,
            j.titulo,
            j.consolas,
            j.imagen_url,
            j.precio_sugerido::float                                      AS precio_sugerido,
            MIN(p.precio)::float                                           AS mejor_precio,
            MAX(s.score_metacritic)                                        AS mejor_score,
            BOOL_OR(p.en_oferta)                                           AS en_oferta,
            CASE
                WHEN j.precio_sugerido > 0
                     AND MIN(p.precio) IS NOT NULL
                     AND MIN(p.precio) < j.precio_sugerido
                THEN ROUND(
                    (j.precio_sugerido - MIN(p.precio)) / j.precio_sugerido * 100, 2
                )::float
                ELSE NULL
            END AS porcentaje_descuento
        FROM juegos j
        LEFT JOIN precios_actuales p ON p.juego_id = j.id
        LEFT JOIN scores           s ON s.juego_id = j.id
        {where_clause}
        GROUP BY j.id, j.titulo, j.consolas, j.imagen_url, j.precio_sugerido
        ORDER BY {order_by}
        LIMIT %s
    """

    with _conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, params)
        return [dict(row) for row in cur.fetchall()]


def contar_juegos() -> int:
    """Total de juegos en la BD. Para el endpoint /api/status."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM juegos")
        return cur.fetchone()[0]
