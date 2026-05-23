# Día 1 — Lo que hacemos HOY (los 3)

Esto es lo concreto de las primeras 2-3 horas. Si esto sale bien, el resto del proyecto fluye.

## Parte 1 — Juntos (45 minutos)

### 1. Crear cuentas y herramientas
- [ ] Crear repo en GitHub: `multicore-precios-juegos`. Los 3 con permisos de escritura.
- [ ] Crear proyecto en **Supabase** (gratis, no requiere tarjeta): https://supabase.com
  - Lo creo yo. Les paso a Brack y Felipe la `DATABASE_URL` y el `SUPABASE_URL` por privado.
- [ ] Crear cuenta en **RAWG**: https://rawg.io/apidocs — sacar API key gratis. La uso yo para el seeding.
- [ ] Crear cuenta en **Render**: https://render.com — la usaremos el Día 6 para desplegar.

### 2. Estructura del repo

```
multicore-precios-juegos/
├── README.md
├── .env.example              # variables de entorno (sin secretos)
├── requirements.txt
├── data/
│   ├── seeding.py            # yo: carga 200+ juegos desde RAWG
│   ├── repository.py         # yo: capa de acceso a datos
│   └── esquema.sql           # yo: CREATE TABLE de todo
├── scrapers/                 # Brack
│   ├── __init__.py
│   ├── base.py               # interfaz común
│   ├── steam.py
│   ├── nintendo.py
│   ├── metacritic.py
│   └── hltb.py
├── orquestador.py            # Brack: los 3 niveles de paralelismo
├── backend/                  # Felipe
│   ├── main.py               # FastAPI app
│   └── routes.py
├── frontend/                 # Felipe
│   ├── index.html
│   ├── style.css
│   └── app.js
└── informe/
    └── informe.md            # el informe final, lo escribimos el día 7
```

### 3. requirements.txt inicial

```
psycopg2-binary==2.9.9
python-dotenv==1.0.1
requests==2.32.3
beautifulsoup4==4.12.3
fake-useragent==1.5.1
fastapi==0.115.0
uvicorn==0.30.6
apscheduler==3.10.4
```

Cada uno corre `pip install -r requirements.txt` en su máquina.

## Parte 2 — Acuerdos clave (30 minutos)

Hay que dejar TRES cosas decididas y por escrito en el README antes de que cada uno se vaya a su parte:

### A. Tiendas que vamos a scrapear
- **Decidido: Steam + Nintendo eShop**. Punto. Si sobra tiempo el día 5, sumamos PSN, pero NO arrancamos pensando en eso.

### B. Formato del diccionario `juego` que recibe Brack
Yo (Persona A) le garantizo a Brack que `obtener_todos_los_juegos()` le va a devolver una lista de diccionarios así:

```python
{
    "id": 123,
    "titulo": "Hollow Knight",
    "consolas": ["Switch", "PC", "PS4"],
    "precio_sugerido": 14.99,
    "urls_tiendas": {
        "steam": "https://store.steampowered.com/app/367520",
        "nintendo": "https://www.nintendo.com/store/products/hollow-knight-switch/"
    },
    "metacritic_url": "https://www.metacritic.com/game/hollow-knight",
    "hltb_url": "https://howlongtobeat.com/game/26723",
    "imagen_url": "https://..."
}
```

### C. Formato que cada scraper de Brack DEBE devolver
Para que mi `repository.py` los acepte sin sorpresas:

```python
# Scraper de tienda:
{"precio": 9.99, "precio_regular": 14.99, "url": "..."}

# Scraper de Metacritic:
{"scores": {"Switch": 88, "PC": 90, "PS4": 87}}

# Scraper de HLTB:
{"main": 26.5, "main_extra": 40.0, "completionist": 65.0}
```

Si alguno no se puede obtener, el scraper devuelve esa clave como `None` o lanza excepción (la atrapa el orquestador).

## Parte 3 — Cada uno a su parte (resto del día)

### Yo (Persona A)
- [ ] Correr `esquema.sql` en Supabase (10 min).
- [ ] Escribir `repository.py` con todas las funciones (1-2 h). Ya tengo la plantilla, solo adaptar.
- [ ] Arrancar `seeding.py` que llama a RAWG y mete 200+ juegos (2 h). Al terminar, avisarles a los otros 2 que la BD ya tiene datos.

### Brack
- [ ] Crear `scrapers/base.py` con la clase base de la interfaz acordada.
- [ ] Escribir `scrapers/steam.py` para UN juego de prueba. Que funcione devolviendo precio.
- [ ] Si sobra tiempo: empezar `scrapers/metacritic.py`.

### Felipe
- [ ] Levantar FastAPI con un endpoint `/api/status` que devuelva data hardcodeada.
- [ ] Maquetar `index.html` con CSS — 3 cards de prueba con datos falsos, ya con el diseño tipo "Hottest Deals".

## Checkpoint del día 1 (final de jornada)

A las 9 PM nos juntamos 15 min por video y cada uno reporta:
- ¿Mi parte arrancó?
- ¿Algo me bloqueó?
- ¿Necesito algo del otro mañana?

Si alguien no pudo arrancar, lo ayudamos en ese momento. **No esperar al día 2 para destrabar bloqueos.**
