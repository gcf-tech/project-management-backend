# Diagnóstico: bloques con recurrencia no se renderizan en grid Weekly

**Fecha:** 2026-04-30  
**Branch:** fix/weekly-recurrence-render  
**Análisis:** estático (sin necesidad de ejecución — hipótesis confirmadas por lectura del código)

---

## Caso de prueba reproducible

- Tipo: personal  
- Semana: 2026-04-27 (lunes)  
- Día: lunes (JS `day_of_week = 1`)  
- Hora: 09:00–10:00  
- Recurrencia: `FREQ=WEEKLY;BYDAY=MO,TU,WE;UNTIL=20260531T000000Z`

---

## Evidencia esperada (consola del navegador con `?debug=weekly`)

Una vez aplicados los fixes, los logs deberían mostrar:

```
[weekly-data] fetchBlocks incoming: 1 | masters: 1 | concrete: 0 | virtual: 3
[weekly-data]   first master to expand: { id: "123", is_master: true, rrule_string: "FREQ=WEEKLY;BYDAY=MO,TU,WE;UNTIL=20260531T000000Z", dtstart: "2026-04-27T09:00:00", ... }
[weekly-data]   first virtual generated: { id: "123:2026-04-27", day: 1, start_time: "09:00", week_start: "2026-04-27" }

[rrule-expander] _expandOne master.id=123
[rrule-expander]   dtstart resolved: 2026-04-27T00:00:00.000Z
[rrule-expander]   utcStart: 2026-04-27T00:00:00.000Z  utcEnd: 2026-05-01T00:00:00.000Z
[rrule-expander]   occurrences.length: 3
[rrule-expander]   first occurrence raw: 2026-04-27T00:00:00.000Z
```

**Antes del fix (UTC-5, Bogotá):**
```
[rrule-expander]   utcStart: 2026-04-20T00:00:00.000Z  utcEnd: 2026-04-24T00:00:00.000Z
[rrule-expander]   occurrences.length: 0
```
→ El rango es la semana ANTERIOR completa porque `"2026-04-27"` se parsea como UTC midnight,
  que en UTC-5 es el domingo 26 de abril a las 19:00, y `startOfDay()` devuelve domingo 26 local.

---

## Hipótesis evaluadas

### H1 — GET /blocks duplica master (concreto + master) ✅ CONFIRMADA

**Evidencia (código):** `app/api/v1/weekly.py` líneas 192–197:

```python
# Query 1 — devuelve el master sin is_master=True
blocks = db.query(WeeklyBlock).filter(
    WeeklyBlock.user_id == user.id,
    WeeklyBlock.week_start == week_start,   # el master creado para esta semana SÍSALE INCLUIDO
).all()
...
result.append(serialize_block(block))  # is_master queda False

# Query 3 — el mismo master reaparece con is_master=True
rrule_masters = db.query(WeeklyBlock).filter(
    WeeklyBlock.rrule_string.isnot(None),
    WeeklyBlock.week_start <= week_end,     # 2026-04-27 <= 2026-05-03 → True
).all()
result.append(serialize_block(master, is_master=True))
```

**Efecto:** el master aparece 2 veces en la respuesta. En el frontend:
- copia `is_master=False` → va a `concrete[]` (bloque concreto duplicado)
- copia `is_master=True` → va a `masters[]` (se expande) — OK

**Severidad:** media. Sólo para la semana de creación produce un bloque duplicado visible el lunes. No causa ausencia total.

---

### H2 — dtstart parseado como local-time crea desfase de semana ✅ CONFIRMADA (causa principal)

**Evidencia (código):** `weekly-data.js` línea 99:

```javascript
const weekDays = getWeekDays(weekStartIsoDate, prefs);
//                           ^^^^^^^^^^^^^^^^ string "2026-04-27"
```

`getWeekDays` hace internamente `new Date("2026-04-27")`. Según la spec ECMAScript, una cadena ISO 8601 de solo-fecha se parsea como **UTC midnight**. En Bogotá (UTC-5):

```
new Date("2026-04-27")          // → April 27 00:00 UTC
                                //   = April 26 19:00 local
startOfDay(April 26 19:00)      // → April 26 00:00 local  (¡domingo!)
getDay(April 26 local)          // → 0  (domingo)
daysBack = (0 - 1 + 7) % 7     // → 6
cur = April 26 - 6 días         // → April 20  (semana ANTERIOR)
```

El rango pasado a `expandBlocks` es **April 20–24** (semana de la creación −7 días). El `dtstart` del master es `2026-04-27T00:00:00 UTC`. Como `dtstart > utcEnd (April 24)`, RRule no genera ninguna ocurrencia.

**Para semanas siguientes (ej. May 4):** el rango se corre a April 28–May 2, generando Tue+Wed virtuales que son de la semana anterior pero aparecen en columnas Tue+Wed de May 4 — datos incorrectos en columnas.

**Severidad:** alta. Es la causa raíz de que no se rendericen los virtuales.

---

### H3 — _resolveDtstart trunca hora a UTC midnight ⚠️ CONFIRMADA (sin impacto actual)

`_resolveDtstart` siempre llama `_utcMidnight` aunque el master tenga `dtstart = "2026-04-27T09:00:00"`. La hora 09:00 se descarta. Para reglas `BYDAY` esto no causa problemas (no hay restricción de hora en el patrón), pero si en el futuro se usan reglas `BYHOUR` fallaría.

**No se corrige en este PR** — se documenta en ADR-004 para implementar en la capa Calendar.

---

### H4 — virtual.day usa getUTCDay() pero columna usa getDay() ❌ NO CONFIRMADA

`occ.getUTCDay()` es correcto porque `_utcMidnight` genera ocurrencias en UTC midnight. Para esas fechas, `getUTCDay()` devuelve el día correcto independientemente de la zona horaria local. Las columnas usan `date.getDay()` sobre objetos Date locales construidos con `getWeekDays(_refDate, prefs)` que usa el constructor de Date objeto (sin problemas de UTC parse). Los días coinciden.

---

### H5 — _blockVisible filtra virtuales incorrectamente ❌ NO CONFIRMADA

`_blockVisible` para bloques personales siempre retorna `true`. Para bloques de task/activity la verificación se hace contra `STATE.tasks` usando `task_id`/`activity_id`, que los bloques virtuales heredan del master. No filtra incorrectamente.

---

## Fixes aplicados

### FIX H1 — `app/api/v1/weekly.py`

```python
# Antes
blocks = db.query(WeeklyBlock).filter(
    WeeklyBlock.user_id == user.id,
    WeeklyBlock.week_start == week_start,
).all()

# Después
blocks = db.query(WeeklyBlock).filter(
    WeeklyBlock.user_id == user.id,
    WeeklyBlock.week_start == week_start,
    WeeklyBlock.rrule_string.is_(None),   # ← excluir masters; van en query 3
).all()
```

### FIX H2 — `src/weekly/weekly-data.js`

```javascript
// Antes
const weekDays = getWeekDays(weekStartIsoDate, prefs);

// Después — parsear como fecha LOCAL para evitar el shift UTC en UTC-N
const [_y, _m, _d] = weekStartIsoDate.split('-').map(Number);
const weekDays = getWeekDays(new Date(_y, _m - 1, _d), prefs);
```

---

## Logs de diagnóstico

Los logs de consola están bajo flag `?debug=weekly` en ambos archivos. **Eliminar antes del merge a main.**

- `src/weekly/weekly-data.js` — función `fetchBlocks`
- `src/calendar/recurrence/rrule-expander.js` — función `_expandOne`

---

## Tests añadidos

| Archivo | Tests | Cobertura |
|---|---|---|
| `src/weekly/__tests__/rrule-expander.test.js` | 16 | expansion BYDAY, FREQ=DAILY, UNTIL, exceptions, formStateToRRule |
| `tests/test_weekly_blocks.py` | 8 | H1 no-dup, rrule_until filtering, _compute_dtstart day mapping |

---

## Checklist E2E (manual — post deploy)

- [ ] C1: MO,TU,WE hasta 31/05 → 3 bloques en cada semana Apr 27–May 25
- [ ] C2: DAILY hasta 5 días → 5 bloques en columnas correspondientes
- [ ] C3: WEEKLY sin UNTIL → aparece en cada semana navegada (probar 4 semanas)
- [ ] C4: MONTHLY día 15 → solo en semana que contiene el 15
- [ ] C5: editar 1 ocurrencia (scope=this) → solo esa cambia
- [ ] C6: eliminar futuras (scope=future) → no aparecen después de esa fecha
- [ ] Network tab: 1 request a /blocks por cambio de semana
- [ ] DevTools console: sin errores de rrule
- [ ] Bloques en columnas correctas según día calendario local (UTC-5)
