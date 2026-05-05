# ADR-005: Calendar integration — BFF + Adapter + Cache-Aside (no DB sync)

**Estado:** Aceptado
**Fecha:** 2026-04-30
**Supersede:** ADR-002 (parcialmente — ver "Diferencias")

---

## Contexto

La vista Calendar (day / week / month / quarter / semester) y la vista Weekly
necesitan mostrar eventos del calendario Nextcloud del usuario (reuniones,
entregas, fechas asignadas). Los eventos:

- Pertenecen a otro sistema de verdad (Nextcloud Calendar / CalDAV).
- Cambian con frecuencia y no requieren write-back desde Activity Tracker.
- Tienen una restricción multi-tenant crítica: cada usuario ve **solo** sus
  eventos (calendarios propios + compartidos a los que tiene acceso).

ADR-002 había propuesto sincronizar los eventos CalDAV a `WeeklyBlock` como
"fuente de verdad local". Tras revisar el caso de uso, esa decisión se
revierte: los eventos no son del usuario en este aplicativo, por lo tanto
no deben vivir en su DB local.

---

## Decisión

Se adopta una arquitectura **BFF + Adapter + Cache-Aside con Stale-While-
Revalidate**, sin sincronización a base de datos.

```
┌──────────┐   GET /api/calendar/events   ┌──────────┐   CalDAV PROPFIND/REPORT   ┌──────────┐
│ Frontend │ ────────────────────────────►│ FastAPI  │ ──────────────────────────►│ Nextcloud│
│ (5 vistas)│  ◄ JSON unificado + ETag    │ BFF      │  ◄ iCal expandido           │ CalDAV   │
└──────────┘                              └────┬─────┘                            └──────────┘
                                               │
                                          ┌────▼────────┐
                                          │ Cache-aside │ Redis (prod) ó in-memory (dev)
                                          │  TTL/SWR    │ Clave: cal:{user}:{view}:{from}:{to}:{cals}
                                          └─────────────┘
```

**Componentes:**

| Capa | Módulo | Responsabilidad |
|---|---|---|
| Adapter | `app/integrations/calendar/{base,nextcloud}.py` | Hablar CalDAV, expandir RRULE, traducir a `CalendarEvent` |
| Cache | `app/core/cache.py` | Interfaz `Cache` con `RedisCache` y `InMemoryTTLCache` |
| Service | `app/services/calendar_service.py` | `EventRepository` con SWR + prefetch de ventana adyacente |
| Schema | `app/schemas/calendar.py` | `CalendarEventOut` (contrato HTTP) |
| API | `app/api/v1/calendar.py` | GET `/events`, POST `/cache/invalidate`, ETag, 304, gzip |

**Selección de cache backend** (automática en startup):
1. Si `REDIS_URL` está definido → `RedisCache` (compartido entre workers,
   sobrevive a reinicios).
2. Caso contrario → `InMemoryTTLCache` (un proceso, sin persistencia; OK para
   dev y tests).

**TTLs por vista** (segundos, configurables vía env):
| Vista | TTL | Stale threshold (× TTL) |
|---|---|---|
| day | 300 | 0.7 → 210s |
| week | 300 | 0.7 → 210s |
| month | 600 | 0.7 → 420s |
| quarter | 900 | 0.7 → 630s |
| semester | 1800 | 0.7 → 1260s |

**Flujo de lectura (`EventRepository.get_events`):**
1. Build cache key: `cal:{nc_user_id}:{view}:{start}:{end}:{cals}`.
2. Cache hit fresh (age < threshold) → return inmediato.
3. Cache hit stale (threshold ≤ age < TTL) → return inmediato + dispatch
   refresh background (`asyncio.create_task`).
4. Cache miss (age ≥ TTL o no existe) → block on CalDAV fetch.

**Multi-tenant guarantee** (no debatible):
- El cache key SIEMPRE incluye `nc_user_id` como segundo segmento.
- El adapter se construye con `(nc_user_id, access_token)` en cada request
  y NO se cachea entre requests.
- `POST /api/calendar/cache/invalidate` solo borra entradas con prefijo
  `cal:{caller.nc_user_id}:`. No existe path de admin para borrar caches
  de otros usuarios desde este endpoint.
- Tests `test_calendar_cache.py::test_two_users_get_only_their_own_events`
  pinea esta invariante.

---

## Auth CalDAV

Decisión activa: **Bearer token OAuth2** (Opción B del ADR-002).

- El frontend envía `Authorization: Bearer <access_token>` a la API normal.
- El backend toma el token de ese header y lo reusa contra CalDAV
  (`caldav.DAVClient(headers={"Authorization": "Bearer ..."})`).
- Variable env `CALDAV_AUTH_MODE=bearer` (default).

**Requisito en Nextcloud:** `allow_oauth2_in_caldav = true` en
`config.php` o equivalente (ver
`docs/calendar-auth-strategy.md`).

**Plan de fallback** (NO implementado por defecto, código preparado):
Si Bearer no funciona en producción, cambiar `CALDAV_AUTH_MODE=app_password`.
Eso requiere:
1. Añadir columna cifrada `users.nc_caldav_token_encrypted` (AES-GCM).
2. UI de onboarding para que el usuario genere App Password en Nextcloud
   y lo pegue una vez.
3. El adapter ya soporta `username/password`; solo cambia el
   `_build_client()`.

---

## Diferencias vs ADR-002

| Tema | ADR-002 (descartado) | ADR-005 (aceptado) |
|---|---|---|
| Almacén de eventos | Sync a `WeeklyBlock` (fuente local) | Cache-aside, no DB |
| Conflictos | "last-write-wins" en BD | N/A — backend siempre lee de Nextcloud |
| Cache | No definido | Redis o in-memory, TTL por vista |
| Editing | CRUD completo a CalDAV | Read-only en este iteración (deep-link a Nextcloud) |
| Adapter | `app/services/caldav_client.py` | `app/integrations/calendar/{base,nextcloud}.py` (DI) |

ADR-002 sigue válido para la decisión "proxy via backend" (no llamar CalDAV
desde el cliente — CORS, refresh, etc.). Eso se mantiene.

---

## Performance hardening (Fase 5 del ticket)

- **gzip** global vía `GZipMiddleware(minimum_size=1024)` en `main.py`.
- **ETag** por respuesta (`SHA256(canonical_json)[:16]`) en `/events`.
- **304** cuando el cliente envía `If-None-Match` que coincide.
- **Cache-Control** `private, max-age=(TTL - 30s)` para que el navegador
  reutilice la respuesta sin pedirla mientras el backend la considera fresca.
- **Vary: Authorization** para que caches intermedios distingan por usuario.
- **`X-Cache: fresh|stale|miss`** header diagnóstico (lo lee el frontend si
  quiere mostrar un badge "actualizando…").
- **Logs estructurados** en `EventRepository._fetch_and_store` con
  `user`, `view`, `range`, `events_count`, `elapsed_ms`.

---

## Consecuencias

1. Los eventos NUNCA están en MySQL — desconectar Nextcloud rompe la vista
   gracefully (degradación: el frontend recibe `events: []` y muestra
   solo bloques propios; el log lo registra como provider error).
2. La invalidación es responsabilidad del cliente: cuando el usuario hace
   "refresh manual", el frontend llama `POST /cache/invalidate` antes del
   siguiente GET.
3. Recurrencias se expanden server-side con `recurring-ical-events` para
   no exponer al frontend a la complejidad de RRULE/EXDATE.
4. La adopción de Redis es un cambio de env var (`REDIS_URL`) sin
   redeploy de código — una migración de single-worker a multi-worker
   solo requiere ese flag.

---

## Referencias

- `app/integrations/calendar/`
- `app/services/calendar_service.py`
- `app/api/v1/calendar.py`
- `app/core/cache.py`
- `tests/test_calendar_cache.py` — invariante multi-tenant pineada
- `docs/calendar-auth-strategy.md` — desglose de la decisión de auth
- `docs/adr/adr-002-caldav-auth-strategy.md` — predecesor (parcialmente vigente)
