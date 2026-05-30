-- =====================================================================
-- Proyecto Multicore — Esquema de BD
-- Para correr en el SQL Editor de Supabase
-- Dueño: Anthony
--Hace falta conectar la bd a la API de RAWG para llenar la tabla de juegos (script de seeding)
--Anthony debe de instalar la extensión de sql para ver errores y corregir algunas cosas.
-- =====================================================================

-- Tabla principal: catálogo de juegos
-- Se llena UNA VEZ con el script de seeding desde la API de RAWG
CREATE TABLE juegos (
    id              SERIAL PRIMARY KEY,
    titulo          TEXT NOT NULL,
    consolas        TEXT[] NOT NULL,           -- ['PS5','Switch','PC']
    precio_sugerido NUMERIC(7,2),              -- MSRP en USD
    fecha_lanzamiento DATE,
    genero          TEXT,
    imagen_url      TEXT,
    urls_tiendas    JSONB NOT NULL DEFAULT '{}',  -- {"steam":"...","nintendo":"..."}
    metacritic_url  TEXT,
    hltb_url        TEXT,
    creado_en       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_juegos_titulo ON juegos (titulo);

-- Tabla de precios actuales (la escribe el orquestador de Brack en cada corrida)
CREATE TABLE precios_actuales (
    id                    SERIAL PRIMARY KEY,
    juego_id              INT NOT NULL REFERENCES juegos(id) ON DELETE CASCADE,
    tienda                TEXT NOT NULL,       -- 'steam', 'nintendo', 'psn'
    precio                NUMERIC(7,2),
    precio_regular        NUMERIC(7,2),
    en_oferta             BOOLEAN DEFAULT FALSE,
    porcentaje_descuento  NUMERIC(5,2),
    fecha_scraping        TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (juego_id, tienda)                  -- upsert: un precio actual por tienda
);

CREATE INDEX idx_precios_juego ON precios_actuales (juego_id);

-- Scores de Metacritic
CREATE TABLE scores (
    id                SERIAL PRIMARY KEY,
    juego_id          INT NOT NULL REFERENCES juegos(id) ON DELETE CASCADE,
    consola           TEXT NOT NULL,
    score_metacritic  INT,                     -- 0..100
    fecha             TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (juego_id, consola)
);

-- Tiempos de HowLongToBeat
CREATE TABLE hltb (
    id                     SERIAL PRIMARY KEY,
    juego_id               INT NOT NULL REFERENCES juegos(id) ON DELETE CASCADE,
    horas_main             NUMERIC(5,1),
    horas_main_extra       NUMERIC(5,1),
    horas_completionist    NUMERIC(5,1),
    fecha                  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (juego_id)
);

-- Log opcional de fallos (para el informe — "manejamos errores")
CREATE TABLE log_scraping (
    id              SERIAL PRIMARY KEY,
    juego_id        INT REFERENCES juegos(id) ON DELETE CASCADE,
    fuente          TEXT NOT NULL,             -- 'steam', 'metacritic', etc.
    nivel           TEXT NOT NULL,             -- 'INFO', 'WARN', 'ERROR'
    mensaje         TEXT,
    duracion_ms     INT,
    fecha           TIMESTAMPTZ DEFAULT NOW()
);

-- =====================================================================
-- Vista útil para Felipe (API): juego con su mejor precio actual
-- =====================================================================
CREATE OR REPLACE VIEW v_juegos_con_mejor_precio AS
SELECT
    j.id,
    j.titulo,
    j.consolas,
    j.imagen_url,
    j.precio_sugerido,
    (
        SELECT MIN(p.precio)
        FROM precios_actuales p
        WHERE p.juego_id = j.id
    ) AS mejor_precio,
    (
        SELECT MAX(s.score_metacritic)
        FROM scores s
        WHERE s.juego_id = j.id
    ) AS mejor_score
FROM juegos j;
