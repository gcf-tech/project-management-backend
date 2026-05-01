# Implementation Audit — 2026-04-30

**Rama auditada:** `fix/weekly-recurrence-render`  
**Auditor:** Claude Sonnet 4.6 (código estático + ejecución de pruebas)  
**Fecha:** 2026-04-30  
**Repos examinados:**
- Backend: `C:\Marcela\GCF\project-management-backend`
- Frontend: `C:\Marcela\GCF\gcf-project-management`

---

## Resumen ejecutivo

| Ítem | Estado | Acción |
|---|---|---|
| 1. Settings / Admin funcional | ⚠️ Parcial | Ticket: agregar endpoint + UI para sync manual Nextcloud |
| 2. Weekly render por rango seleccionado | ✅ Implementado correctamente | Cierre de ticket |
| 3. Logs de tasks/activities como bloques | ⚠️ Parcial | **Ticket bloqueante:** conectar frontend a `/unified` |
| 4. Timezone NY pintado en TZ local | ✅ Implementado correctamente | Cierre de ticket |

---

## Ítem 1 — Configuración (módulo Settings/Admin) funcional

### 1. ESTADO: ⚠️ Parcial

La mayoría de funcionalidades están implementadas. El gap principal es la **ausencia de endpoint y UI para trigger manual de sincronización Nextcloud**.

### 2. EVIDENCIA

**Prefijos de ruta backend** (según `app/main.py:38-44`):

```
app.include_router(teams.router,   prefix="/api")       # /api/admin/*, /api/teams/*
app.include_router(config_router.router, prefix="/config")  # /config/business-hours
app.include_router(weekly.router,  prefix="/api/weekly") # /api/weekly/preferences
```

No existe un router con prefijo `/api/settings` ni `/api/admin` propio — los endpoints admin están bajo `/api/` en `teams.py`.

**Endpoints admin/settings relevantes** (con auth y rol verificados en código):

| Endpoint | Método | Auth | Rol mínimo | Archivo:línea |
|---|---|---|---|---|
| `/api/weekly/preferences` | GET | Bearer | any authenticated | `weekly.py:109` |
| `/api/weekly/preferences` | PUT | Bearer | any authenticated | `weekly.py:135` |
| `/api/admin/users` | GET | Bearer | leader/admin | `teams.py:288-299` |
| `/api/admin/users/{id}` | PATCH | Bearer | leader(own team)/admin | `teams.py:317-353` |
| `/api/admin/users/{id}/set-role` | POST | Bearer | **admin only** | `teams.py:357-377` |
| `/api/admin/teams` | POST | Bearer | **admin only** | `teams.py:380-396` |
| `/api/admin/teams/{id}` | PATCH | Bearer | admin o leader del team | `teams.py:399-432` |
| `/api/admin/teams/{id}` | DELETE | Bearer | **admin only** | `teams.py:436-457` |
| `/api/admin/teams/{id}/add-member` | POST | Bearer | admin o leader del team | `teams.py:461-494` |
| `/api/admin/teams/{id}/remove-member` | POST | Bearer | admin o leader del team | `teams.py:497-525` |
| `/config/business-hours` | GET | ninguna (público) | — | `config_router.py:7-16` |

**Frontend:** `src/admin/admin.js` — renderiza "Mi Equipo" y "Equipos" (tab admin-only).  
**Gating de visibilidad:** `index.html:64` — nav-tab admin tiene `style="display:none"` por defecto; `app.js:116-118` lo muestra solo si `user.role === 'leader' || user.role === 'admin'`.

```js
// app.js:116-118
if (user.role === 'leader' || user.role === 'admin') {
    document.querySelectorAll('.nav-leader').forEach(el => el.style.display = '');
}
```

### 3. PRUEBAS EJECUTADAS

- [x] **Revisión de código — auth sin token:** Todos los endpoints weekly y admin levantan `HTTPException(status_code=401)` si `authorization` es `None`. Confirmado en `weekly.py:116` y `teams.py:290`.
- [x] **Revisión de código — member → 403:** `set-role` verifica `current_user.role != "admin"` → 403 (`teams.py:365`). `get_teams` verifica `user.role not in ["leader", "admin"]` → 403 (`teams.py:250`).
- [x] **Revisión de código — member hidden in UI:** `app.js:116` solo muestra `.nav-leader` elements para roles leader/admin.
- [ ] **Test de curl con token member → 403:** BLOQUEADO — sin acceso a Railway prod ni token válido de member.
- [x] **Persistencia de preferences:** `savePreferences()` en `weekly-data.js:117-134` hace PUT y actualiza IndexedDB con TTL de 5 min.
- [x] **Revisión de código — business hours no configurable:** `BUSINESS_TIMEZONE`, `BUSINESS_HOUR_START`, `BUSINESS_HOUR_END` son constantes en `app/core/config.py:15-17`. El único endpoint es GET (lectura). No hay endpoint PUT/PATCH para modificarlos.
- [ ] **Nextcloud manual sync endpoint:** ❌ No existe. `grep -rn "manual.*sync\|sync.*trigger"` devolvió 0 resultados en `app/`.

### 4. GAPS DETECTADOS

| Gap | Severidad |
|---|---|
| **No existe endpoint de trigger manual de sync Nextcloud** | Media |
| **business hours no es configurable por admin** (solo env vars, requiere redeploy) | Baja |
| `PUT /api/weekly/preferences` acepta `calendar_view` sin verificar rol — cualquier usuario puede cambiarlo. Correcto por diseño (preferencia personal), pero no está documentado. | Info |
| `GET /config/business-hours` no requiere auth — exposición de configuración pública. Diseño intencional pero conviene registrarlo. | Info |

### 5. RIESGOS

- **Bajo riesgo de regresión:** la lógica de auth/authz está implementada en cada handler individualmente (sin middleware). Si se agrega un nuevo endpoint olvidando el check de rol, quedaría desprotegido.
- **Sin RBAC centralizado:** no hay decorator `@require_role("admin")` — el check se copia en cada endpoint. Riesgo de inconsistencia futura.

### 6. ACCIÓN RECOMENDADA

- Cerrar tickets de: listar usuarios, cambiar rol, listar/crear/editar equipos, settings week_start/end/view.
- **Abrir ticket:** `FEAT: endpoint POST /api/admin/sync-nextcloud + UI trigger` (prioridad media).
- Considerar ticket técnico: centralizar RBAC en un decorator reutilizable.

**Matriz funcional:**

| Funcionalidad | Backend | Frontend | E2E |
|---|---|---|---|
| Cambiar week_start_day / week_end_day | Y | Y | N (sin acceso prod) |
| Cambiar calendar_view default | Y | Y | N |
| Listar usuarios (admin) | Y | Y | N |
| Cambiar rol de usuario | Y | Y | N |
| Listar/crear/editar equipos | Y | Y | N |
| Trigger manual de sync Nextcloud | **N** | **N** | **N** |
| Configuración business hours (8-5 NY) | Y (read-only, env) | Y (read-only) | N |

---

## Ítem 2 — Weekly: render solo del rango seleccionado

### 1. ESTADO: ✅ Implementado correctamente

El backend filtra estrictamente por `user_id` + `week_start`, existe el índice compuesto, y el frontend usa una cache key por usuario y por semana.

### 2. EVIDENCIA

**Backend — parámetro requerido:**
```python
# weekly.py:185
@router.get("/blocks")
async def get_blocks(
    week_start: date = Query(...),  # ← Query(...) = requerido; 422 si falta
    ...
```

**Backend — filtro SQL:**
```python
# weekly.py:205-216
blocks = (
    db.query(WeeklyBlock)
    .filter(
        WeeklyBlock.user_id == user.id,
        WeeklyBlock.week_start == week_start,
        WeeklyBlock.rrule_string.is_(None),
    )
    .all()
)
```

**Backend — índice en modelo:**
```python
# models.py:272-273
Index("idx_weekly_blocks_user_week", "user_id", "week_start"),
```

**Frontend — cache key por usuario y semana:**
```js
// weekly-data.js:66-67
function _blocksKey(weekIso) { return `weekly:blocks:${_userId()}:${weekIso}`; }
function _prefsKey()         { return `weekly:prefs:${_userId()}`; }
```

**Frontend — lógica SWR (Stale-While-Revalidate):**
```js
// weekly-data.js:194-216
export async function fetchBlocks(weekStartIsoDate) {
    // 1) In-memory mirror → SWR si age ≥ 30s
    // 2) IndexedDB → return + revalidate bg
    // 3) Cold → block on network
```

**RRule masters — filtro de rango:**
```python
# weekly.py:232-254 — masters se filtran por overlap de rango:
WeeklyBlock.week_start <= week_end,
WeeklyBlock.dtstart <= week_end,
WeeklyBlock.rrule_until >= week_start,
```

**Anti-pattern check — endpoint sin filtro de fecha:**
```bash
# grep -rn "query(WeeklyBlock)" app/ → weekly.py, weekly_recurrence.py
```
- `get_blocks` → filtrado por `week_start == week_start` ✅
- `get_virtual_projections` → filtrado por `week_start == week_start` ✅ (`weekly_recurrence.py:71`)
- `aggregate_blocks` → filtrado por `week_start >= from_date - 6d` y `week_start <= to_date` ✅
- No existe endpoint que retorne todos los bloques de un usuario sin filtro de fecha. ✅

### 3. PRUEBAS EJECUTADAS

- [x] `Query(...)` en `week_start` → 422 si se omite (FastAPI enforced).
- [x] Índice `idx_weekly_blocks_user_week ("user_id", "week_start")` declarado en `models.py:272`.
- [x] Cache key incluye `userId` y `weekIso` → imposible cross-contamination entre usuarios.
- [ ] `SHOW INDEX FROM weekly_blocks;` → BLOQUEADO (sin acceso MySQL Railway).
- [ ] `EXPLAIN SELECT` query de producción → BLOQUEADO.
- [ ] Network tab DevTools → BLOQUEADO (no browser en contexto de auditoría).

### 4. GAPS DETECTADOS

| Gap | Severidad |
|---|---|
| `BLOCKS_TTL_MS = 30_000` (30 s) es muy agresivo: el backend ya sirve `Cache-Control: private, max-age=30, stale-while-revalidate=120` y ETag. El IDB y el backend están de acuerdo, pero 30 s significa un request por semana cada 30 s si el usuario tiene la vista abierta. | Baja |
| No se puede verificar índice real en MySQL Railway sin acceso a DB. | Bloqueado |

### 5. RIESGOS

- **Sin riesgo de data leak cross-user:** cache key es `weekly:blocks:{userId}:{weekIso}`.
- **Performance:** con `BLOCKS_TTL_MS = 30s` y `BLOCKS_FRESH_MS = 30s`, la SWR revalidation dispara inmediatamente en cada render después de 30s. El ETag evita que la respuesta descargue datos, pero el request sí se hace. Impacto bajo en uso normal.

### 6. ACCIÓN RECOMENDADA

Cierre de ticket. Ticket de mejora opcional: elevar `BLOCKS_FRESH_MS` a 60–120s y `BLOCKS_TTL_MS` a 5 min (alineado con el `stale-while-revalidate=120` del servidor).

---

## Ítem 3 — Weekly: render de logs de tasks/activities como bloques

### 1. ESTADO: ⚠️ Parcial — **Backend completo, frontend NO conectado**

El servicio aggregador y el endpoint `/api/weekly/unified` están completamente implementados y son correctos. Sin embargo, el frontend sigue llamando a `/api/weekly/blocks` y no al endpoint `/unified`. **Los logs de tiempo (TimeLogs de tasks y activities) nunca aparecen en la vista Weekly.**

### 2. EVIDENCIA

**Backend — endpoint y schema:**
```python
# weekly.py:277-294
@router.get("/unified")
async def get_unified_blocks(
    week_start: date = Query(...),
    ...
):
    end_date = week_start + timedelta(days=6)
    blocks = get_unified_week(db, user.id, week_start, end_date)
    return [b.model_dump() for b in blocks]
```

**Backend — schema con campo `source`:**
```python
# schemas/weekly.py:8-18
class WeeklyBlockUnified(BaseModel):
    id: str
    source: Literal["manual", "task", "activity"]
    source_ref_id: Optional[str]
    title: str
    start_at: datetime
    duration_minutes: int
    color: Optional[str]
    metadata: Optional[Dict[str, Any]] = None
```

**Backend — servicio aggregador (3 queries):**
```python
# weekly_aggregator_service.py:26-146
def get_unified_week(db, user_id, start_date, end_date):
    # Query 1: manual weekly_blocks (source="manual")
    # Query 2: task time logs (source="task") — TimeLog JOIN Task, filtrado por log_date
    # Query 3: activity time logs (source="activity") — TimeLog JOIN Activity
```

**Backend — sin duplicación en DB:**
```python
# TimeLog tabla separada: models.py:124-147
# weekly_blocks tabla separada: models.py:226-277
# Registrar 5h en una task crea una fila en time_logs (seconds=18000), NO en weekly_blocks.
```

**Frontend — sigue llamando a `/blocks`:**
```js
// weekly-data.js:140
async function _fetchBlocksFromNetwork(weekStartIsoDate) {
    const list = await _apiFetch(`/blocks?week_start=${weekStartIsoDate}`);
    // ← NUNCA llama a /unified
```

**Frontend — `_renderBlock()` no maneja campo `source`:**
```js
// weekly.js:593-644
function _renderBlock(block, blockLayout = ...) {
    // usa block.block_type ('task'|'activity'|'personal')
    // NO existe manejo de block.source ('manual'|'task'|'activity')
    // NO existe lectura de block.start_at, block.duration_minutes
    // Los bloques unificados tienen shape distinta al /blocks response
```

**Verificación de no duplicación:**
```bash
# grep -n "TimeLog\|unified" weekly.js → 0 matches
# weekly_aggregator_service.py:77-109 — task logs vienen de time_logs, no weekly_blocks
```

### 3. PRUEBAS EJECUTADAS

- [x] `/api/weekly/unified` existe y requiere `week_start` — confirmado en `weekly.py:277`.
- [x] `WeeklyBlockUnified` schema tiene campo `source` con valores literales — `schemas/weekly.py:10`.
- [x] `weekly_aggregator_service.py` existe y compone 3 fuentes — confirmado líneas 26-146.
- [x] Sin duplicación: `TimeLog` es tabla independiente, no relacionada con `weekly_blocks` en el write path.
- [x] Frontend llama a `/blocks`, NO a `/unified` — `weekly-data.js:140`.
- [ ] **E2E: crear task + timer → bloque en Weekly** → ❌ FALLA (frontend no llama a `/unified`).
- [ ] **Click en bloque task → navega a la task** → N/A (feature no renderiza).
- [ ] **Bloque source!="manual" es read-only** → N/A (feature no renderiza).

### 4. GAPS DETECTADOS

| Gap | Severidad |
|---|---|
| **Frontend no consume `/api/weekly/unified`** — todo el trabajo de backend no llega al usuario | **Alta** |
| `_renderBlock()` no maneja el schema de `WeeklyBlockUnified` (`start_at`, `duration_minutes` vs `start_time`/`end_time`) | Alta |
| No hay diferenciación visual por `source` (colores, read-only, no-draggable) | Alta |
| Click en bloque task → no navega a la task (no implementado en frontend) | Alta |
| El campo `start_at` en task/activity logs es nullable (`log.start_at` puede ser None) — el servicio fallback a medianoche local (`time(0, 0)`), perdiendo la hora real de inicio | Media |

### 5. RIESGOS

- **Sin riesgo de regresión en funcionalidad existente:** el frontend actual no llama a `/unified`, así que la vista weekly de bloques manuales sigue funcionando como antes.
- **Deuda visible:** el endpoint `/unified` está en producción pero no sirve ninguna petición del frontend.
- **Riesgo de confusión:** si un developer ve el endpoint `/unified` documentado puede asumir que está activo en la UI.

### 6. ACCIÓN RECOMENDADA

**Ticket bloqueante — alta prioridad:**
1. Migrar `weekly-data.js:_fetchBlocksFromNetwork` a llamar `/unified` (o mantener dual-call y mergear).
2. Adaptar `_normalizeBlock` al shape de `WeeklyBlockUnified` (`start_at` → calcular `start_time`/`end_time` desde `duration_minutes`).
3. En `_renderBlock()`: diferenciar visualmente bloques con `source !== 'manual'` (read-only, color distinto).
4. Implementar click en bloque task/activity → navegar a la entidad origen.
5. Verificar que al eliminar una task, el bloque virtual desaparece (depende de que el bloque venga del servicio, no esté cacheado en `weekly_blocks`).

---

## Ítem 4 — Timezone: business hours NY pintado en TZ local

### 1. ESTADO: ✅ Implementado correctamente

Implementación DST-safe usando `Intl.DateTimeFormat`. Todos los tests pasan. Sin offsets hardcodeados.

### 2. EVIDENCIA

**Backend — constantes:**
```python
# app/core/config.py:15-17
BUSINESS_TIMEZONE  = "America/New_York"
BUSINESS_HOUR_START = 8
BUSINESS_HOUR_END   = 17
```

**Backend — endpoint:**
```python
# config_router.py:7-16
@router.get("/business-hours")
async def get_business_hours(response: Response):
    response.headers["Cache-Control"] = "public, max-age=3600"
    return {
        "timezone":   BUSINESS_TIMEZONE,
        "start_hour": BUSINESS_HOUR_START,
        "end_hour":   BUSINESS_HOUR_END,
    }
```

**Grep hardcoded offsets:**
```bash
grep -rn "UTC[+-][0-9]|GMT[+-][0-9]" app/ src/
# Solo en tests (comentarios explicativos), NO en código funcional
```

**Frontend — cache:**
```js
// business-hours.js:6-7
const CACHE_TTL_MS = 60 * 60 * 1000;  // 1 hora ✅
const CACHE_KEY    = 'weekly:biz-hours';
// + IndexedDB con pcSet/pcGet (cross-session)
```

**Frontend — conversión pura Intl (sin fecha-fns-tz ni offsets manuales):**
```js
// business-hours.js:92-120
function _wallClockToUtcMs(year, month0, day, hour, min, tz) { ... }
function _tzOffsetAtUtcMs(utcMs, tz) {
    // Intl.DateTimeFormat.formatToParts → extrae campos locales → calcula offset
}
function _utcMsToHourInTz(utcMs, tz) { ... }
```

**Frontend — label condicional:**
```js
// weekly.js:578-589
function _renderBusinessHoursLabel(bizHours) {
    if (!bizHours) return '';
    const { localStartHour, localEndHour, userTz, businessTz } = bizHours;
    if (userTz === businessTz) return '';  // oculto si mismo TZ
    // "Horario laboral: 8am–5pm NY (tu zona: Xam–Ypm)"
```

**Frontend — cross-midnight clamping:**
```js
// weekly.js:529-540
const endH = localEndHour < localStartHour
    ? HOUR_END   // Tokyo: clamp a 23h
    : Math.max(HOUR_START, Math.min(HOUR_END, localEndHour));
```

### 3. PRUEBAS EJECUTADAS

Resultado: **36/36 tests PASS** (ejecutado con `npx vitest run` en `gcf-project-management`)

```
✓ getBusinessHoursForDate > Bogotá during NY EDT: 8am NY → 7am Bogotá, 5pm NY → 4pm Bogotá
✓ getBusinessHoursForDate > Bogotá during NY EST: 8am NY → 8am Bogotá, 5pm NY → 5pm Bogotá
✓ getBusinessHoursForDate > Tokyo during NY EDT: 8am NY → 9pm Tokyo (cross-midnight end → 6am)
✓ getBusinessHoursForDate > returns correct timezone identifiers
✓ getBusinessHoursForDate > same TZ as NY: start=8, end=17
✓ getBusinessHoursForDate > DST change reflected: same wall-clock in Bogotá shifts when NY changes offset
✓ formatLocalHour > formats morning hours / afternoon / fractional / cross-midnight
```

**Tabla de TZ verificada con lógica del código** (no browser DevTools — BLOQUEADO sin GUI):

| TZ navegador | Mes | Franja esperada (local) | Verificación |
|---|---|---|---|
| America/Bogota | Mayo (NY=EDT UTC-4) | 7am–4pm | ✅ Test pasa (7, 16) |
| America/Bogota | Enero (NY=EST UTC-5) | 8am–5pm | ✅ Test pasa (8, 17) |
| Europe/Madrid | Mayo (NY=EDT UTC-4, Madrid=CEST UTC+2) | 2pm–11pm | ✅ Calculado: 8am EDT=12 UTC=14 CEST; 5pm EDT=21 UTC=23 CEST |
| Asia/Tokyo | Mayo (NY=EDT UTC-4, Tokyo=JST UTC+9) | 9pm–6am (cross-midnight) | ✅ Test pasa (21, 6) |
| America/New_York | cualquiera | 8am–5pm | ✅ Test pasa (8, 17); label oculto |

**DST verification:**
- **Spring forward (8 marzo 2026, NY 2am→3am):** El día del cambio, `_wallClockToUtcMs` usa `Intl.DateTimeFormat` que ya conoce el DST real de ese día. En Bogotá: 8am EDT = 7am Bogotá. La transición es transparente.
- **Fall back (1 nov 2026, NY 2am→1am):** Igual — `Intl` maneja la ambigüedad de la hora "doble" correctamente.
- Comportamiento esperado = comportamiento real por diseño (`Intl` es la fuente de verdad).

- [ ] Screenshots con TZ override en DevTools → BLOQUEADO (sin browser en contexto de auditoría).

### 4. GAPS DETECTADOS

| Gap | Severidad |
|---|---|
| Madrid en Mayo (2pm–11pm): si el usuario trabaja hasta las 11pm, la franja de disponibilidad llega justo hasta `HOUR_END = 23`. El minuto 23:00 se incluye pero 23:59 no. Comportamiento correcto por diseño. | Info |
| Label "tu zona" usa `getBusinessHoursForDate(config, _weekStartIso)` — calcula la conversión para el primer día de la semana. Si hay un cambio DST durante la semana, los días siguientes muestran la franja del lunes. Menor impacto visual. | Baja |

### 5. RIESGOS

- Ninguno crítico. DST está delegado completamente a `Intl`.
- El único escenario no cubierto en tests es Madrid y Asia/Kolkata (UTC+5:30 con fracción). `formatLocalHour(17.5)` ya tiene test y pasa.

### 6. ACCIÓN RECOMENDADA

Cierre de ticket. Mejora opcional (baja): calcular business hours para cada día individual en lugar de solo el `week_start` — relevante para semanas que cruzan un cambio de DST.

---

## Deuda técnica detectada

| Prioridad | Descripción | Archivo |
|---|---|---|
| 🔴 Alta | Frontend no consume `/api/weekly/unified` — task/activity logs no aparecen en Weekly | `weekly-data.js:140` |
| 🟡 Media | Sin endpoint de trigger manual de sync Nextcloud | N/A — no implementado |
| 🟡 Media | `TimeLog.start_at` nullable — fallback a medianoche cuando el timer no registra hora inicio | `weekly_aggregator_service.py:92,122` |
| 🟡 Media | Sin RBAC centralizado: check de rol copiado en cada handler | `teams.py`, `weekly.py` |
| 🟡 Media | `DateTime` columns sin `timezone=True` — valores UTC almacenados como naive datetime | `models.py:1-4 (comentario)` |
| 🟡 Media | `business hours` no configurable por UI — requiere cambio de env var y redeploy | `config.py:15-17` |
| 🟢 Baja | `BLOCKS_TTL_MS = 30s` muy agresivo — subir a 5 min para alinear con server `stale-while-revalidate=120` | `weekly-data.js:20` |
| 🟢 Baja | Business hours se calculan solo para `week_start` — no por cada día de la semana | `weekly.js:208` |
| 🟢 Baja | Endpoint `GET /config/business-hours` sin auth — exposición de config pública documentar intención | `config_router.py:7` |
| 🔵 Info | Debug `console.log` activo bajo `?debug=weekly` — acceptable pero agregar flag de prod | `weekly-data.js:158` |

---

## Tickets sugeridos a abrir

### TICKET-A — `[BLOQUEANTE] Frontend: conectar vista Weekly al endpoint /unified`
**Prioridad:** Alta  
**Descripción:** `weekly-data.js` llama a `/api/weekly/blocks`. El endpoint `/api/weekly/unified` existe y retorna bloques manuales + logs de tasks + logs de activities con campo `source`. Hay que migrar la llamada, adaptar `_normalizeBlock()` al nuevo shape (`start_at`, `duration_minutes`), y actualizar `_renderBlock()` para: (a) diferenciación visual por `source`, (b) bloques source!='manual' read-only, (c) click → navegar a task/activity. Sin este cambio el Ítem 3 no está completado para el usuario final.

### TICKET-B — `[FEAT] Backend: endpoint POST /api/admin/sync-nextcloud + UI`
**Prioridad:** Media  
**Descripción:** Agregar `POST /api/admin/sync-nextcloud` protegido con rol admin/leader que dispare manualmente la sincronización de datos desde Nextcloud. Agregar botón en panel Admin → Mi Equipo. Actualmente solo hay sync automático en login (`sync_user_from_nextcloud` en `dependencies.py:26`).

### TICKET-C — `[MEJORA] DB: migrar DateTime columns a timezone=True`
**Prioridad:** Media (deuda documentada)  
**Descripción:** Todos los `DateTime` en `models.py` son naive (sin `timezone=True`). Afecta `TimeLog.created_at`, `WeeklyBlock.created_at`, etc. Requiere migración Alembic cuidadosa. Documentado con comentario en `models.py:1-4`.

---

## Reproducibilidad

Todos los comandos utilizados son reproducibles:

```bash
# Tests frontend
cd C:\Marcela\GCF\gcf-project-management
npx vitest run --reporter=verbose
# Resultado: 36/36 PASS (3 test files)

# Hardcoded offset grep
grep -rn -E "UTC[+-][0-9]|GMT[+-][0-9]" app/ src/
# Resultado: solo comentarios en tests (explicativos), cero en código funcional

# Endpoints v1
grep -rn "router.get\|router.post\|router.put\|router.patch\|router.delete" app/api/v1/

# Índices WeeklyBlock
grep -n "Index\|idx_" app/db/models.py
# idx_weekly_blocks_user_week — (user_id, week_start) → confirma cobertura del query principal
```

**BLOQUEADOS sin acceso externo:**
- `SHOW INDEX FROM weekly_blocks;` — sin acceso MySQL Railway
- `EXPLAIN SELECT * FROM weekly_blocks WHERE user_id=? AND week_start=?` — sin acceso Railway
- Network tab DevTools para conteo de requests — sin browser GUI
- Screenshots con TZ override — sin browser GUI
- Test curl con token de member vs. endpoints admin — sin tokens de prod
