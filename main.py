"""
main.py — Backend FastAPI del proyecto Multicore Precios Juegos
Dueño: Felipe

Endpoints:
    GET /api/juegos          → lista de juegos con mejor precio y filtros
    GET /api/juegos/{id}     → detalle completo: precios, scores, HLTB
    GET /api/status          → última actualización y total de juegos

Docs automáticas (Swagger): http://localhost:8000/docs

Correr localmente:
    python main.py

Para Render (producción):
    uvicorn main:app --host 0.0.0.0 --port $PORT
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import repository as repo

FRONTEND_DIR = Path(__file__).parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    repo.init_pool(minconn=2, maxconn=10)
    yield


app = FastAPI(
    title="Multicore Precios Juegos",
    description="Precios de 200+ videojuegos en Steam y Nintendo eShop, con scores de Metacritic y tiempos de HowLongToBeat.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS: necesario para que el frontend pueda llamar desde otro dominio en Render
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────
# ENDPOINT 1: Lista de juegos
# ─────────────────────────────────────────────────────────

@app.get("/api/juegos", summary="Lista de juegos con mejor precio")
def get_juegos(
    plataforma: Optional[str] = Query(
        None, description="Switch | PS5 | PS4 | Xbox | PC"
    ),
    tienda: Optional[str] = Query(
        None, description="steam | nintendo"
    ),
    en_oferta: Optional[bool] = Query(
        None, description="true → solo ofertas"
    ),
    orden: str = Query(
        "nombre", description="nombre | precio | descuento | score"
    ),
    limit: int = Query(
        50, ge=1, le=200, description="Cantidad de resultados (máx 200)"
    ),
):
    juegos = repo.obtener_lista_para_api(
        plataforma=plataforma,
        tienda=tienda,
        en_oferta=en_oferta,
        orden=orden,
        limit=limit,
    )
    ultima = repo.ultima_actualizacion()
    return {
        "juegos": juegos,
        "total": len(juegos),
        "ultima_actualizacion": ultima.isoformat() if ultima else None,
    }


# ─────────────────────────────────────────────────────────
# ENDPOINT 2: Detalle de un juego
# ─────────────────────────────────────────────────────────

@app.get("/api/juegos/{juego_id}", summary="Detalle completo de un juego")
def get_juego(juego_id: int):
    juego = repo.obtener_juego_por_id(juego_id)
    if not juego:
        raise HTTPException(status_code=404, detail="Juego no encontrado")

    precios  = repo.obtener_precios_por_juego(juego_id)
    scores   = repo.obtener_scores_por_juego(juego_id)
    hltb     = repo.obtener_hltb_por_juego(juego_id)

    urls_tiendas = juego.get("urls_tiendas") or {}

    # Adjuntar URL de la tienda a cada precio
    precios_out = []
    for p in precios:
        d = dict(p)
        d["url"] = urls_tiendas.get(d["tienda"])
        if d.get("fecha_scraping"):
            d["fecha_scraping"] = d["fecha_scraping"].isoformat()
        # Convertir Decimal a float
        for k in ("precio", "precio_regular", "porcentaje_descuento"):
            if d.get(k) is not None:
                d[k] = float(d[k])
        precios_out.append(d)

    scores_out = []
    for s in scores:
        d = dict(s)
        if d.get("fecha"):
            d["fecha"] = d["fecha"].isoformat()
        scores_out.append(d)

    hltb_out = None
    if hltb:
        hltb_out = {
            k: float(v) if v is not None else None
            for k, v in dict(hltb).items()
            if k != "fecha"
        }

    return {
        "id":              juego["id"],
        "titulo":          juego["titulo"],
        "imagen_url":      juego["imagen_url"],
        "consolas":        juego["consolas"],
        "precio_sugerido": float(juego["precio_sugerido"]) if juego.get("precio_sugerido") else None,
        "precios":         precios_out,
        "scores":          scores_out,
        "hltb":            hltb_out,
    }


# ─────────────────────────────────────────────────────────
# ENDPOINT 3: Estado del sistema
# ─────────────────────────────────────────────────────────

@app.get("/api/status", summary="Estado del sistema")
def get_status():
    ultima = repo.ultima_actualizacion()
    total  = repo.contar_juegos()
    return {
        "ultima_actualizacion": ultima.isoformat() if ultima else None,
        "total_juegos":         total,
        "scraping_en_curso":    False,
    }


# ─────────────────────────────────────────────────────────
# Servir el frontend estático
# ─────────────────────────────────────────────────────────

if FRONTEND_DIR.exists():
    app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/", include_in_schema=False)
def root():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"status": "API activa", "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
