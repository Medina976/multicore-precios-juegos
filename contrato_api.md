# Contrato de la API REST

**Dueño: Felipe.** Brack y yo necesitamos este contrato para saber qué expone Felipe; el frontend lo consume.

Base URL en producción: `https://nuestro-proyecto.onrender.com`

---

## `GET /api/juegos`

Lista todos los juegos con su mejor precio actual. Es lo que alimenta las cards de la página principal.

**Query params (todos opcionales):**

| Param | Valores | Qué hace |
|---|---|---|
| `plataforma` | `Switch`, `PS5`, `PS4`, `Xbox`, `PC` | Solo juegos disponibles en esa plataforma |
| `tienda` | `steam`, `nintendo` | Solo juegos con precio en esa tienda |
| `en_oferta` | `true`, `false` | Solo juegos en oferta |
| `orden` | `precio`, `nombre`, `descuento`, `score` | Cómo ordenar |
| `limit` | número | Cuántos devolver (default 50) |

**Respuesta 200:**

```json
{
  "juegos": [
    {
      "id": 123,
      "titulo": "The Legend of Zelda: Tears of the Kingdom",
      "imagen_url": "https://...",
      "consolas": ["Switch"],
      "mejor_precio": 49.99,
      "precio_sugerido": 69.99,
      "porcentaje_descuento": 28.57,
      "en_oferta": true,
      "mejor_score": 96
    }
  ],
  "total": 200,
  "ultima_actualizacion": "2026-05-22T14:30:00Z"
}
```

---

## `GET /api/juegos/{id}`

Detalle completo para el modal/página de detalle.

**Respuesta 200:**

```json
{
  "id": 123,
  "titulo": "The Legend of Zelda: Tears of the Kingdom",
  "imagen_url": "https://...",
  "consolas": ["Switch"],
  "precio_sugerido": 69.99,
  "precios": [
    {
      "tienda": "nintendo",
      "precio": 49.99,
      "precio_regular": 69.99,
      "en_oferta": true,
      "porcentaje_descuento": 28.57,
      "url": "https://nintendo.com/games/...",
      "fecha_scraping": "2026-05-22T14:30:00Z"
    },
    {
      "tienda": "steam",
      "precio": null,
      "url": null
    }
  ],
  "scores": [
    { "consola": "Switch", "score_metacritic": 96 }
  ],
  "hltb": {
    "horas_main": 60.5,
    "horas_main_extra": 105.0,
    "horas_completionist": 240.0
  }
}
```

**Respuesta 404:** juego no existe.

---

## `GET /api/status`

Para el "Última actualización: hace X minutos" del frontend. El frontend hace polling a este endpoint cada minuto.

**Respuesta 200:**

```json
{
  "ultima_actualizacion": "2026-05-22T14:30:00Z",
  "total_juegos": 200,
  "scraping_en_curso": false
}
```

---

## Notas de implementación para Felipe

- Usa FastAPI — autodocumenta en `/docs` con Swagger gratis. El profesor lo va a ver y suma puntos.
- Internamente, llama a las funciones de `repository.py` que yo (Persona A) ya escribí. **No escribir SQL aquí**.
- CORS: habilitar `*` para que el frontend pueda llamar desde otro dominio (Render sirve backend y frontend en URLs distintas).
- Si Brack todavía no terminó de poblar la BD con precios, los campos de precios pueden venir `null`. El frontend debe manejarlo (mostrar "—" o "Precio no disponible").
