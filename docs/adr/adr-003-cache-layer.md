# ADR-003: Capa de caché — Redis vs in-memory

**Estado:** Propuesto (pre-Calendar)  
**Fecha:** 2026-04-30  
**Contexto:** La vista Calendar con expansión de múltiples semanas necesitará caché de masters y preferencias

---

## Contexto

Hoy el Weekly Tracker tiene caché in-memory en el cliente (`_blocksCache` con TTL 30 s en `weekly-data.js`). Con la vista Calendar (month, quarter, semester) el cliente consultará múltiples semanas simultáneamente y los masters se expandirán en rangos mayores. Se evalúa si añadir caché en el servidor.

---

## Opciones evaluadas

### Opción A — Solo caché client-side (status quo)

Mantener el `_blocksCache` Map en JS, ampliar TTL a 5 minutos, y gestionar invalidación por semana.

**Pros:**
- Cero infraestructura adicional.
- Ya existe y funciona.

**Contras:**
- Perdida al recargar página o entre pestañas.
- Cada instancia del cliente mantiene su propia copia — sin beneficio multiventana.
- La vista semester requeriría cachear ~26 semanas simultáneamente; un Map de JS no tiene TTL por entrada fácil.

### Opción B — Caché in-memory en el servidor (Python dict / lru_cache)

Usar `functools.lru_cache` o un dict global en el proceso FastAPI para cachear los masters por `user_id`.

**Pros:**
- Sin dependencia de Redis.
- Acceso O(1) dentro del mismo proceso.

**Contras:**
- No funciona en despliegue multi-worker (Gunicorn + multiple workers tendrían cachés independientes).
- Sin TTL nativo — requiere implementación manual.
- Pérdida del caché en cada restart.
- No aplica a Vercel serverless (cada invocación es stateless).

### Opción C — Redis (elegida recomendada para producción) ✅

Añadir Redis (o Upstash Redis para Vercel) como caché de masters. Clave: `blocks:masters:{user_id}`, TTL 60 s.

**Pros:**
- Compartido entre workers y despliegues serverless.
- TTL nativo con eviction automática.
- Upstash Redis tiene free tier y driver compatible con serverless.

**Contras:**
- Infraestructura adicional (Redis / Upstash).
- Añade latencia de red al servidor (~1–3 ms para Upstash).
- Invalidación explícita necesaria al crear/editar/eliminar un master.

### Opción D — No caché servidor + prefetch optimistic en cliente

El cliente prefetcha semanas adyacentes (current ±1) en background usando `requestIdleCallback`.

**Pros:**
- Sin infraestructura.
- UX percibida rápida (siguiente semana ya cargada antes de navegar).

**Contras:**
- No reduce carga del servidor.
- Para vista semester/quarter el prefetch no aplica.

---

## Decisión

**Fase actual (Weekly):** mantener Opción A (client-side Map). Es suficiente para 1 semana visible.

**Fase Calendar (semester/quarter):** implementar **Opción D** (prefetch optimistic) como primer paso y **Opción C** (Redis/Upstash) si el tiempo de GET /blocks supera 200 ms en p95 con usuarios con >100 masters.

---

## Consecuencias

1. El `_blocksCache` actual debe extenderse a un `Map<weekIso, ...>` con invalidación por prefijo de usuario al mutar cualquier bloque.
2. Añadir `requestIdleCallback` en `weekly.js` para prefetchar `weekStart ± 7 días` después del render.
3. Si se adopta Redis: crear `app/services/cache.py` con `get_masters(user_id)` / `invalidate_masters(user_id)` abstrayendo el driver (memoria para tests, Redis para prod).
4. La decisión Redis se revisará con métricas reales post-lanzamiento de Calendar.

---

## Referencias

- `src/weekly/weekly-data.js` — `_blocksCache` implementation  
- Upstash Redis serverless: https://upstash.com/redis  
- `app/api/v1/weekly.py` — GET /blocks endpoint
