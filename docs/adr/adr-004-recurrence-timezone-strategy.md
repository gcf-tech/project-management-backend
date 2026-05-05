# ADR-004: Estrategia de timezone para fechas de recurrencia (dtstart)

**Estado:** Aceptado (correctivo — aplicado en fix/weekly-recurrence-render)  
**Fecha:** 2026-04-30  
**Contexto:** Bug H2 en recurrence-render-2026-04-30 — expansión RRule en timezone UTC-5

---

## Contexto

El bug H2 reveló una ambigüedad de timezone en el pipeline de fechas de recurrencia:

1. El backend serializa `dtstart` como `"2026-04-27T09:00:00"` (sin offset, naive datetime).
2. El frontend parsea la string del parámetro `weekStartIsoDate` con `new Date("2026-04-27")` que la spec ECMAScript trata como UTC midnight.
3. En Bogotá (UTC-5) esto desplaza la semana calculada un día atrás, causando que el rango de expansión RRule no cubra la semana correcta.

---

## Decisión aplicada (sin comparar alternativas — ya corregido)

### Regla 1: fechas de navegación (semana visible)

**En el cliente**, toda fecha ISO solo-fecha (`"YYYY-MM-DD"`) proveniente del servidor o de la URL DEBE parsearse con el constructor local:

```javascript
// Correcto
const [y, m, d] = isoDate.split('-').map(Number);
const localDate = new Date(y, m - 1, d);   // midnight local

// Incorrecto — UTC midnight en todos los navegadores
const date = new Date("2026-04-27");        // April 26 19:00 local en UTC-5
```

Esta regla ya se aplica en `fetchBlocks` (fix H2).

### Regla 2: dtstart del master

**Estado actual (Weekly):** `dtstart` se serializa como datetime naive (`"2026-04-27T09:00:00"`) y el cliente lo trunca a UTC midnight via `_utcMidnight`. Esto es correcto para reglas `BYDAY` donde la hora no importa para la generación de ocurrencias.

**Para Calendar:** cuando se implemente soporte de eventos con hora de inicio específica (e.g. `BYHOUR`) o cuando se necesite que la ocurrencia lleve la hora correcta en la vista Calendar, el pipeline completo debe ser:

1. **Backend:** persistir y serializar `dtstart` con offset UTC explícito: `"2026-04-27T09:00:00+00:00"`.
2. **Frontend `_resolveDtstart`:** si `master.dtstart` incluye offset, usar `new Date(master.dtstart)` directamente (sin truncar a midnight).
3. **Frontend `_expandOne`:** mapear cada ocurrencia a hora local del usuario usando `Intl.DateTimeFormat` (no `getUTCDay()`).

### Regla 3: rangeEnd para set.between

El rango pasado a `set.between(utcStart, utcEnd, inclusive)` debe cubrir el día completo del último día visible. Con dtstart a midnight UTC, `utcEnd = _utcMidnight(lastDay)` con `inclusive=true` es suficiente. Si dtstart tiene hora distinta de midnight, `utcEnd` debe ser `lastDay 23:59:59 UTC`.

---

## Consecuencias

1. `weekStartIsoDate` NUNCA se pasa a `new Date()` directamente — siempre usar el constructor `new Date(y, m-1, d)`.
2. `_resolveDtstart` preservará el comportamiento actual (truncar a midnight) para masters legacy. Para masters nuevos con offset explícito en `dtstart`, no truncar.
3. Documentar en `rrule-expander.js` la invariante: "all dtstart values passed to RRule are UTC midnight; occurrence day is read via getUTCDay()".
4. Al implementar Calendar, revisar `_expandOne` para convertir ocurrencias de UTC midnight a hora local usando `Intl.DateTimeFormat(timezone, {...})`.

---

## Referencias

- `docs/diagnostics/recurrence-render-2026-04-30.md` — análisis completo del bug
- `src/weekly/weekly-data.js` — FIX H2 aplicado
- `src/calendar/recurrence/rrule-expander.js` — `_resolveDtstart` y `_expandOne`
- ECMAScript spec: [Date.parse ISO 8601](https://tc39.es/ecma262/#sec-date.parse) — date-only strings are UTC
