"""
seeding.py — Llenar la BD con 200+ juegos desde RAWG
Dueña: Anthony

Correr UNA SOLA VEZ:
    python seeding.py

Necesita:
    pip install requests psycopg2-binary python-dotenv
"""
import os
import time
import requests
from dotenv import load_dotenv
import psycopg2

load_dotenv()

RAWG_API_KEY = os.environ["RAWG_API_KEY"]
DATABASE_URL = os.environ["DATABASE_URL"]
BASE_URL = "https://api.rawg.io/api"

# Plataformas que nos interesan (IDs de RAWG)
# 4=PC, 187=PS5, 18=PS4, 1=Xbox One, 186=Xbox Series, 7=Switch
PLATFORMS = "4,187,18,1,186,7"

def obtener_juegos_rawg(total: int = 250) -> list[dict]:
    """Llama a RAWG y trae 'total' juegos populares."""
    juegos = []
    page = 1
    page_size = 40  # máximo que permite RAWG por página

    print(f"Bajando {total} juegos desde RAWG...")

    while len(juegos) < total:
        params = {
            "key": RAWG_API_KEY,
            "page": page,
            "page_size": page_size,
            "platforms": PLATFORMS,
            "ordering": "-rating",       # los mejor calificados primero
            "metacritic": "60,100",      # solo juegos con buen score
        }
        resp = requests.get(f"{BASE_URL}/games", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        resultados = data.get("results", [])
        if not resultados:
            break

        juegos.extend(resultados)
        print(f"  Página {page}: {len(resultados)} juegos (total acumulado: {len(juegos)})")
        page += 1
        time.sleep(0.5)  # respetar rate limit de RAWG

    return juegos[:total]


def mapear_consolas(platforms: list) -> list[str]:
    """Convierte los IDs de plataforma de RAWG a nuestros nombres."""
    mapa = {
        "PC": "PC",
        "PlayStation 5": "PS5",
        "PlayStation 4": "PS4",
        "Xbox One": "Xbox",
        "Xbox Series S/X": "Xbox",
        "Nintendo Switch": "Switch",
    }
    resultado = set()
    for p in platforms:
        nombre = p.get("platform", {}).get("name", "")
        if nombre in mapa:
            resultado.add(mapa[nombre])
    return list(resultado)


def construir_urls_tiendas(slug: str) -> dict:
    """
    Construye URLs aproximadas de Steam y Nintendo para un juego.
    RAWG no da URLs directas, así que usamos la búsqueda de cada tienda.
    Brack va a usar estas URLs como punto de partida para el scraper.
    """
    return {
        "steam": f"https://store.steampowered.com/search/?term={slug.replace('-', '+')}",
        "nintendo": f"https://www.nintendo.com/search/#q={slug.replace('-', '+')}&p=1&cat=gme&sort=df",
    }


def insertar_juegos(juegos_rawg: list[dict]) -> None:
    """Inserta los juegos en la BD. Ignora duplicados."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    insertados = 0
    saltados = 0

    import json

    for j in juegos_rawg:
        titulo = j.get("name", "").strip()
        if not titulo:
            continue

        slug = j.get("slug", "")
        consolas = mapear_consolas(j.get("platforms") or [])
        imagen_url = j.get("background_image")
        fecha_lanzamiento = j.get("released")  # puede ser None
        genero = None
        if j.get("genres"):
            genero = j["genres"][0]["name"]

        # RAWG no da precio sugerido — lo dejamos en NULL
        # Brack lo va a actualizar cuando scrapee cada tienda
        precio_sugerido = None

        urls_tiendas = construir_urls_tiendas(slug)

        # URL de Metacritic si RAWG la tiene
        metacritic_url = None
        if j.get("metacritic_url"):
            metacritic_url = j["metacritic_url"]
        elif slug:
            metacritic_url = f"https://www.metacritic.com/game/{slug}/"

        # URL de HLTB (aproximada por nombre)
        hltb_url = f"https://howlongtobeat.com/?q={slug.replace('-', '+')}"

        try:
            cur.execute("""
                INSERT INTO juegos
                    (titulo, consolas, precio_sugerido, fecha_lanzamiento,
                     genero, imagen_url, urls_tiendas, metacritic_url, hltb_url)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (
                titulo,
                consolas,
                precio_sugerido,
                fecha_lanzamiento,
                genero,
                imagen_url,
                json.dumps(urls_tiendas),
                metacritic_url,
                hltb_url,
            ))
            if cur.rowcount > 0:
                insertados += 1
            else:
                saltados += 1
        except Exception as e:
            print(f"  Error insertando '{titulo}': {e}")
            conn.rollback()
            continue

    conn.commit()
    cur.close()
    conn.close()
    print(f"\nListo: {insertados} juegos insertados, {saltados} ya existían.")


def verificar_bd() -> None:
    """Muestra cuántos juegos quedaron en la BD."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM juegos")
    total = cur.fetchone()[0]
    cur.execute("SELECT titulo, consolas FROM juegos LIMIT 5")
    ejemplos = cur.fetchall()
    cur.close()
    conn.close()

    print(f"\nTotal de juegos en BD: {total}")
    print("Primeros 5 juegos:")
    for titulo, consolas in ejemplos:
        print(f"  - {titulo} ({', '.join(consolas)})")


if __name__ == "__main__":
    print("=== Seeding de la BD ===\n")

    # 1. Bajar juegos de RAWG
    juegos = obtener_juegos_rawg(total=250)
    print(f"\nTotal bajados de RAWG: {len(juegos)}")

    # 2. Insertar en BD
    print("\nInsertando en la BD...")
    insertar_juegos(juegos)

    # 3. Verificar
    verificar_bd()

    print("\n=== Seeding terminado ===")
    print("Avisale a Brack y Felipe que la BD ya tiene juegos.")