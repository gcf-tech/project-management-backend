# ADR-001: Arquitectura de expansión de recurrencias — Opción C (client-side expansion)

**Estado:** Aceptado  
**Fecha:** 2026-04-30  
**Contexto:** Diseño del módulo Calendar y refactorización del Weekly Tracker

---

## Contexto

El Weekly Tracker necesita mostrar ocurrencias de bloques recurrentes. Se evaluaron tres estrategias para materializar las ocurrencias.

---

## Opciones evaluadas

### Opción A — Materialización completa en servidor (server-side eager)

El backend genera y almacena en BD todas las ocurrencias futuras al momento de crear la regla.

**Pros:**
- GET /blocks es una lectura plana, sin cómputo.
- Sin lógica RRule en el cliente.

**Contras:**
- Explosión de filas para reglas sin UNTIL (potencialmente infinitas).
- Editar la regla (scope=all) requiere borrar y regenerar miles de filas.
- Complejidad en paginación y rangos de tiempo amplios (vista semestral/anual).

### Opción B — Expansión on-demand en servidor (server-side lazy)

El backend expande en memoria para el rango solicitado en cada GET /blocks.

**Pros:**
- Sin estado materializado en BD.
- El servidor controla la zona horaria.

**Contras:**
- CPU en el servidor por cada request de weekly view.
- Más difícil de cachear (el rango varía por usuario y semana).
- Añade latencia al GET /blocks que hoy es < 50 ms.

### Opción C — Expansión client-side con masters persistidos (elegida) ✅

El servidor almacena solo el bloque master (`is_master=true`, `rrule_string`). El cliente recibe los masters y expande con `rrule.js` para el rango visible.

**Pros:**
- BD liviana: 1 fila por regla (no por ocurrencia).
- Expansión en el cliente es instantánea (< 500 reglas/usuario).
- Editar scope=all solo actualiza 1 fila.
- Compatible con offline-first / cache del cliente.

**Contras:**
- Lógica de RRule duplicada (server para validate RRULE_STRING, client para expand).
- Requiere cuidado con zonas horarias al parsear fechas ISO en el cliente.
- Los virtuales no existen en BD → no se pueden indexar ni agregar en el servidor directamente.

---

## Decisión

Se elige **Opción C**. El tamaño del dataset (< 500 reglas/usuario) hace que la expansión client-side sea despreciable en tiempo. El beneficio en simplicidad de BD y edición de series es determinante.

---

## Consecuencias

1. Las fechas ISO string en el cliente DEBEN parsearse con el constructor de fecha local (`new Date(y, m-1, d)`) y NO con `new Date("YYYY-MM-DD")` (que es UTC midnight per spec ECMAScript).
2. El backend solo materializa ocurrencias cuando el usuario edita una sola ocurrencia (scope=this) — ver `weekly_recurrence.materialize`.
3. La vista Calendar (semester, quarter) recibirá masters via GET /blocks y expandirá en el rango completo del view. El rendimiento debe validarse con usuarios que tengan > 200 reglas activas.
4. ADR-004 documenta la estrategia de timezone para `dtstart` una vez que se implemente la vista Calendar.

---

## Referencias

- `src/calendar/recurrence/rrule-expander.js` — implementación de expansión
- `app/api/v1/weekly.py` — endpoint GET /blocks y query de rrule_masters
- `docs/diagnostics/recurrence-render-2026-04-30.md` — análisis de bugs en la implementación inicial
