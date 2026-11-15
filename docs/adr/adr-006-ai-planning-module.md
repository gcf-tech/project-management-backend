# ADR-006: Módulo de planificación asistida por IA en el Deck

**Estado:** Aceptado  
**Fecha:** 2026-11-11  
**Contexto:** Aplicación del trabajo de grado *AI-StorySense* (Universidad del Valle) sobre el tablero de gestión de proyectos de la empresa.

---

## Contexto

Crear un proyecto en el Deck hoy exige crear la tarjeta padre, inventar las subtareas una a una, decidir quién hace cada una y fijar fechas a criterio. El trabajo de grado AI-StorySense (y su implementación de referencia AIASE) demostró en un escenario controlado que un pipeline de agentes LLM con supervisión humana obligatoria y contexto organizacional produce artefactos de planificación con mayor confianza y trazabilidad. Se quiere validar ese principio en un dominio real (fintech) con datos reales del equipo, con el menor cambio posible sobre el producto en producción.

## Decisión

1. **La IA solo propone; el humano aprueba.** Se introduce la entidad `deck_ai_proposals` con ciclo de vida `draft → approved | discarded`. Nada se escribe en `deck_cards` hasta `approve`, que reutiliza la lógica existente de creación de tarjeta y subtareas. Cada intervención humana (edición, regeneración con feedback, aprobación, descarte) se registra en `deck_ai_proposal_events` con usuario, timestamp y detalle (trazabilidad HITL).

2. **El LLM vive en el backend.** AIASE llamaba a Gemini/Claude desde el navegador con la clave en el cliente; aquí la clave está únicamente en variables de entorno del servidor (`OPENAI_API_KEY`) y el frontend solo habla con `/api/decks/ai/*`. Proveedor OpenAI (contratado por la empresa) detrás de una función `complete_json()` agnóstica, para poder sustituir el modelo sin tocar el planificador, tal como el documento establece que la arquitectura es independiente del modelo.

3. **El contexto organizacional se construye desde la base de datos**, no desde un JSON manual: cargo, equipo e historial real de cada miembro (tarjetas completadas, títulos recientes, horas registradas, carga abierta) y los proyectos anteriores del tablero con sus subtareas y asignados. Se persiste como `context_snapshot` en cada propuesta para que todo resultado sea reproducible. Existe un modo `context_mode=none` que replica el experimento "sin contexto" del estudio empírico.

4. **Un solo agente "Planificador"** con prompt de sistema fijo, derivado de los agentes de Visión, Backlog y Estimación de AIASE, adaptado a tareas de proyecto (no historias de usuario). Salida JSON con esquema cerrado y **validación programática** antes de presentarla al humano (ids únicos, asignados existentes, horas numéricas, textos acotados); los ajustes se guardan en `validation_warnings`.

5. **Router separado (`deck_ai.py`)** y feature flag por tablero (`AI_PLANNING_BOARD_IDS`) para probar en una URL distinta compartiendo base de datos sin exponer el módulo al resto de la empresa. `deck.py` no se modifica.

## Alternativas descartadas

- *Llamar al LLM desde el frontend (como AIASE):* expone la clave y no deja trazabilidad centralizada.
- *Crear tarjetas directamente y permitir deshacer:* rompe el principio HITL y contamina el historial/actividad del tablero con propuestas descartadas.
- *Pipeline de cinco agentes como en AIASE:* innecesario para la unidad de trabajo del Deck (tareas, no historias de usuario); un agente con validación programática cubre el objetivo con menos latencia y costo.

## Consecuencias

- Dos tablas nuevas; ninguna tabla existente cambia (migración `a8b9c0d1e2f3`).
- Métricas de evaluación disponibles en `/api/decks/ai/metrics`: Tasa de Aprobación de Artefactos (AAR), ediciones manuales promedio por campo, regeneraciones, comparación con/sin contexto.
- Criterio de evaluación complementario (*backtest*): tomar proyectos históricos, ocultar sus subtareas y asignados, generar la propuesta solo con la descripción, y comparar estructura y asignación contra lo que el equipo hizo realmente.
- Costo por propuesta acotado: prompt compacto (límites en `ai_context.py`) y modelo `gpt-4o-mini` por defecto.
