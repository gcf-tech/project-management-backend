"""Agente "Planificador" del Deck.

Es la adaptación de los agentes de Visión (AGENTE_0), Backlog (AGENTE_1) y
Estimación (AGENTE_3) de AIASE a la unidad de trabajo del Deck: en vez de épicas e
historias de usuario produce una tarjeta padre (proyecto) y sus subtareas, y para
cada subtarea sugiere la persona idónea comparando con el historial real del
equipo (estimación por analogía: Shepperd & Schofield, 1997).

Principios que se conservan de AI-StorySense:
- Prompt de sistema FIJO entre proyectos; lo único que varía es el contexto.
- Salida JSON con esquema cerrado y límites de tamaño.
- Validación programática ANTES de presentar al humano (4.4.3 del documento):
  ids únicos, asignados existentes, horas numéricas, textos acotados.
- Nunca escribe en el tablero: solo produce la propuesta que el humano aprueba.
"""
from typing import Any, Dict, List, Optional, Tuple

from app.services import ai_svc
from app.services.ai_context import summarize_context_for_prompt

MAX_TASKS = 12
MIN_TASKS = 2
CONFIDENCE = ("alta", "media", "baja")

# Títulos que describen una FASE del ciclo de vida y no una pieza funcional. El
# equipo ya modela las fases como columnas del tablero; si el modelo las propone
# como subtareas se registra un aviso (métrica de calidad, no se descarta).
import re as _re
_PHASE_TITLE = _re.compile(
    r"^\s*(definir|levantar|reunir|documentar|recopilar)\s+(los\s+)?requisitos"
    r"|^\s*(realizar|ejecutar|hacer)\s+(las\s+)?pruebas"
    r"|^\s*(pruebas|testing)\s+(internas?|externas?|del|de)\b"
    r"|^\s*documentar\b"
    r"|^\s*(lanzar|desplegar|despliegue|lanzamiento|poner en producci[oó]n)\b"
    r"|^\s*dise[ñn]ar\s+(la\s+)?(interfaz|arquitectura)\b",
    _re.IGNORECASE,
)
_GENERIC_REASON = _re.compile(
    r"tiene experiencia en (desarrollo|documentaci[oó]n|pruebas|dise[ñn]o|configuraci[oó]n)\b"
    r"|ha (trabajado|completado) (en )?(tareas|proyectos) similares",
    _re.IGNORECASE,
)

SYSTEM_PROMPT = """Actúas como Tech Lead y Project Manager senior de una empresa del sector financiero. Tu único trabajo es transformar la descripción de un proyecto en una propuesta de tarjeta padre y subtareas para el tablero de trabajo del equipo, asignando a cada subtarea la persona más idónea según el contexto real del equipo que se te entrega.

Sigue este proceso en orden.

PASO 1, el proyecto. Resume la descripción en un título corto y una descripción de máximo 300 caracteres. No inventes requisitos que no estén en la descripción ni en el contexto.

PASO 2, las subtareas. Cada subtarea es una PIEZA FUNCIONAL del producto que se puede entregar y verificar por sí sola (una pantalla, una regla de negocio, una integración, un reporte, una configuración), exactamente como hacen los PROYECTOS ANTERIORES del contexto: fíjate en cómo están redactadas sus subtareas e imita su estilo, número y tamaño. Las fases del ciclo de vida (levantar requisitos, diseñar, desarrollar, probar, documentar, desplegar, lanzar) NO son subtareas: ya son las ETAPAS DEL FLUJO por las que cada tarjeta avanza, así que está prohibido crear subtareas con esos nombres salvo que la descripción pida explícitamente un entregable de ese tipo (por ejemplo un manual de usuario). Si hay historial, cita en reference_project_id el proyecto anterior más parecido al proyecto completo. Si no hay historial comparable usa entre 4 y 8 subtareas.

PASO 3, la persona idónea. Para cada subtarea elige UNA persona de MIEMBROS DISPONIBLES usando exactamente su id numérico. La asignación es por EVIDENCIA, no por reparto equitativo: es correcto que una misma persona reciba la mayoría o todas las subtareas si el historial lo respalda. Orden de prioridad: primero quien ya completó tareas del mismo producto o funcionalidad según "ha hecho" o según los PROYECTOS ANTERIORES; después quien tenga el cargo más afín; solo entre candidatos realmente equivalentes, el que tenga menos tareas abiertas. Prefiere miembros del mismo equipo del tablero. En assignee_reasoning escribe primero, entre comillas, el título textual de una tarea o proyecto del contexto que ESA persona hizo (o su cargo si no hay tarea citable) y después, en pocas palabras, por qué esa experiencia aplica a la subtarea; frases genéricas como "tiene experiencia en desarrollo" están prohibidas, y citar tareas de otra persona o de un producto sin relación cuenta como no tener evidencia. Si nadie es idóneo deja suggested_assignee_id en null y explica por qué.

PASO 4, estimación y confianza. Estima las horas de cada subtarea por su complejidad, comparando con tareas similares del historial. confidence es alta SOLO si citas en assignee_reasoning una tarea o proyecto anterior del mismo producto o funcionalidad; media si la analogía es por cargo o por un producto distinto; baja si no hay historial comparable. No todas las subtareas pueden ser alta salvo que cada una tenga su cita.

Devuelve exclusivamente JSON válido sin markdown ni texto adicional con esta estructura: un objeto project con title y description. Un array tasks donde cada elemento tiene id con el formato T1, T2..., title, description, suggested_assignee_id como número o null, assignee_reasoning, estimated_hours como número, confidence con valor alta, media o baja, reference_project_id como número o null, y depends_on como array de ids de tareas. Un array assumptions con supuestos que tomaste por falta de información. Un array risks con riesgos relevantes.

REGLA CRÍTICA: mínimo 2 y máximo 12 subtareas. title máximo 90 caracteres, description máximo 200 caracteres, assignee_reasoning máximo 200 caracteres. Solo usa ids de miembros que aparezcan en la lista. JSON compacto sin comentarios."""


def build_user_prompt(context: Dict[str, Any], project_text: str,
                      feedback: Optional[str] = None,
                      previous: Optional[Dict[str, Any]] = None) -> str:
    parts = [
        "CONTEXTO ORGANIZACIONAL",
        summarize_context_for_prompt(context),
        "",
        "DESCRIPCIÓN DEL PROYECTO (aportada por el usuario):",
        project_text.strip(),
    ]
    if feedback:
        # Iteración quirúrgica (4.3.3): se regenera con el feedback del revisor y la
        # propuesta previa, sin reiniciar desde cero.
        parts += [
            "",
            "PROPUESTA ANTERIOR (fue rechazada por el revisor):",
            _compact_previous(previous or {}),
            "",
            "FEEDBACK DEL REVISOR (aplícalo y conserva lo que no se cuestiona):",
            feedback.strip(),
        ]
    parts += ["", "Genera la propuesta siguiendo las reglas del sistema."]
    return "\n".join(parts)


def _compact_previous(p: Dict[str, Any]) -> str:
    import json
    slim = {
        "project": p.get("project"),
        "tasks": [{k: t.get(k) for k in ("id", "title", "suggested_assignee_id", "estimated_hours")}
                  for t in (p.get("tasks") or [])],
    }
    return json.dumps(slim, ensure_ascii=False)


def _norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return _re.sub(r"\s+", " ", s.lower().replace("…", "").replace("...", "")).strip()


def _evidence_index(context: Dict[str, Any]) -> List[Tuple[str, set]]:
    """Lista de (texto_normalizado, {ids de personas a las que pertenece}) con todo
    lo citable del contexto: títulos recientes y cargo de cada miembro, títulos de
    proyectos anteriores y de sus subtareas (con sus asignados)."""
    idx: List[Tuple[str, set]] = []
    for m in context.get("members", []):
        for t in m.get("recent_completed_titles", []) or []:
            idx.append((_norm(t), {m["id"]}))
        if m.get("job_title"):
            idx.append((_norm(m["job_title"]), {m["id"]}))
    for p in context.get("history_projects", []):
        owners = set()
        for st in p.get("subtasks", []):
            ids = set(st.get("assignee_ids") or [])
            owners |= ids
            idx.append((_norm(st.get("title", "")), ids))
        idx.append((_norm(p.get("title", "")), owners))
    return [(t, ids) for t, ids in idx if len(t) >= 12]


def _find_evidence(reasoning: str, idx: List[Tuple[str, set]]) -> Tuple[bool, set]:
    """¿La justificación contiene algún texto del contexto? Devuelve (hay_cita,
    personas a las que pertenece la cita). Se compara por el inicio del título
    (25 caracteres) para tolerar recortes."""
    r = _norm(reasoning)
    if not r:
        return False, set()
    owners: set = set()
    found = False
    for text, ids in idx:
        key = text[:25]
        if key and key in r:
            found = True
            owners |= ids
    return found, owners


def validate_and_normalize(data: Dict[str, Any], context: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Validación programática complementaria. Devuelve (propuesta_normalizada,
    avisos). Corrige lo corregible (ids, longitudes, tipos) y registra en
    `warnings` lo que tuvo que ajustar, para trazabilidad."""
    warnings: List[str] = []
    valid_members = {m["id"] for m in context.get("members", [])}
    valid_projects = {p["id"] for p in context.get("history_projects", [])}
    evidence = _evidence_index(context)
    has_history = bool(context.get("history_projects"))

    project = data.get("project") or {}
    if not isinstance(project, dict):
        project = {}
    title = str(project.get("title") or "").strip()[:255]
    if not title:
        warnings.append("project.title vacío; se usó un título genérico")
        title = "Proyecto sin título"
    description = str(project.get("description") or "").strip()[:1000]

    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list):
        raw_tasks = []
    tasks: List[Dict[str, Any]] = []
    seen_ids = set()
    for i, t in enumerate(raw_tasks[:MAX_TASKS]):
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id") or f"T{i + 1}").strip()
        if tid in seen_ids:
            new_id = f"T{len(tasks) + 1}"
            warnings.append(f"id duplicado {tid} → {new_id}")
            tid = new_id
        seen_ids.add(tid)

        ttitle = str(t.get("title") or "").strip()[:255]
        if not ttitle:
            warnings.append(f"{tid}: título vacío, tarea descartada")
            continue

        aid = t.get("suggested_assignee_id")
        try:
            aid = int(aid) if aid is not None else None
        except (TypeError, ValueError):
            aid = None
        if aid is not None and aid not in valid_members:
            warnings.append(f"{tid}: asignado {aid} no existe en el contexto, se dejó sin asignar")
            aid = None

        hours = t.get("estimated_hours")
        try:
            hours = float(hours) if hours is not None else None
            if hours is not None and (hours <= 0 or hours > 2000):
                raise ValueError
        except (TypeError, ValueError):
            if hours is not None:
                warnings.append(f"{tid}: estimated_hours inválido ({t.get('estimated_hours')}), se omitió")
            hours = None

        conf = str(t.get("confidence") or "").lower().strip()
        if conf not in CONFIDENCE:
            warnings.append(f"{tid}: confidence '{conf}' fuera del dominio, se fijó en baja")
            conf = "baja"

        ref = t.get("reference_project_id")
        try:
            ref = int(ref) if ref is not None else None
        except (TypeError, ValueError):
            ref = None
        if ref is not None and ref not in valid_projects:
            warnings.append(f"{tid}: reference_project_id {ref} no está en el historial, se omitió")
            ref = None

        deps = t.get("depends_on") if isinstance(t.get("depends_on"), list) else []
        reasoning = str(t.get("assignee_reasoning") or "").strip()[:400]
        if _PHASE_TITLE.search(ttitle):
            warnings.append(f"{tid}: '{ttitle}' parece una fase del ciclo de vida, no una pieza funcional")
        if aid is not None and has_history:
            cited, owners = _find_evidence(reasoning, evidence)
            generic = bool(_GENERIC_REASON.search(reasoning))
            if not cited:
                warnings.append(f"{tid}: la justificación no cita ninguna tarea, proyecto o cargo del contexto")
            elif owners and aid not in owners:
                warnings.append(f"{tid}: la evidencia citada pertenece a otra persona, no al asignado {aid}")
                cited = False
            if generic:
                warnings.append(f"{tid}: justificación genérica ('{reasoning[:60]}…')")
            if conf == "alta" and (not cited or generic):
                conf = "media"
                warnings.append(f"{tid}: confidence rebajada a media por falta de evidencia verificable")
        tasks.append({
            "id": tid,
            "title": ttitle,
            "description": str(t.get("description") or "").strip()[:1000],
            "suggested_assignee_id": aid,
            "assignee_reasoning": reasoning,
            "estimated_hours": hours,
            "confidence": conf,
            "reference_project_id": ref,
            "depends_on": [str(d) for d in deps if str(d) != tid],
        })

    # Cita repetida mecánicamente: la misma evidencia en 3+ subtareas distintas.
    from collections import Counter
    cites = Counter(_norm(t["assignee_reasoning"])[:40] for t in tasks if t["assignee_reasoning"])
    for key, n in cites.items():
        if n >= 3 and key:
            affected = [t for t in tasks if _norm(t["assignee_reasoning"])[:40] == key]
            warnings.append(f"la misma cita se repite en {n} subtareas ({', '.join(t['id'] for t in affected)}): copia mecánica, no analogía por tarea")
            for t in affected:
                if t["confidence"] == "alta":
                    t["confidence"] = "media"

    # Dependencias que apuntan a tareas inexistentes se limpian.
    ids = {t["id"] for t in tasks}
    for t in tasks:
        bad = [d for d in t["depends_on"] if d not in ids]
        if bad:
            warnings.append(f"{t['id']}: dependencias inexistentes {bad} eliminadas")
            t["depends_on"] = [d for d in t["depends_on"] if d in ids]

    if len(tasks) < MIN_TASKS:
        warnings.append(f"solo {len(tasks)} tarea(s) válidas (mínimo {MIN_TASKS})")

    def _str_list(key):
        v = data.get(key)
        return [str(x)[:300] for x in v][:10] if isinstance(v, list) else []

    normalized = {
        "project": {"title": title, "description": description},
        "tasks": tasks,
        "assumptions": _str_list("assumptions"),
        "risks": _str_list("risks"),
    }
    return normalized, warnings


async def generate_plan(context: Dict[str, Any], project_text: str, *,
                        feedback: Optional[str] = None,
                        previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Llama al modelo y devuelve {'proposal', 'warnings', 'model', 'usage', 'prompt'}."""
    user_prompt = build_user_prompt(context, project_text, feedback=feedback, previous=previous)
    result = await ai_svc.complete_json(SYSTEM_PROMPT, user_prompt)
    proposal, warnings = validate_and_normalize(result.data, context)
    return {
        "proposal": proposal,
        "warnings": warnings,
        "model": result.model,
        "usage": {"prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens},
        "prompt": user_prompt,
    }


def count_manual_edits(original: Dict[str, Any], edited: Dict[str, Any]) -> Dict[str, int]:
    """Cuenta las ediciones humanas entre la propuesta de la IA y la versión final
    (equivale a `manualEditsCount` de AIASE, desglosado por campo para el análisis)."""
    counts = {"project": 0, "task_title": 0, "task_description": 0, "assignee": 0,
              "hours": 0, "tasks_added": 0, "tasks_removed": 0}
    po, pe = original.get("project") or {}, edited.get("project") or {}
    for k in ("title", "description"):
        if (po.get(k) or "").strip() != (pe.get(k) or "").strip():
            counts["project"] += 1
    to = {t["id"]: t for t in original.get("tasks") or []}
    te = {t.get("id"): t for t in edited.get("tasks") or []}
    for tid, t in te.items():
        o = to.get(tid)
        if o is None:
            counts["tasks_added"] += 1
            continue
        if (o.get("title") or "").strip() != (t.get("title") or "").strip():
            counts["task_title"] += 1
        if (o.get("description") or "").strip() != (t.get("description") or "").strip():
            counts["task_description"] += 1
        if o.get("suggested_assignee_id") != t.get("suggested_assignee_id"):
            counts["assignee"] += 1
        if (o.get("estimated_hours") or None) != (t.get("estimated_hours") or None):
            counts["hours"] += 1
    counts["tasks_removed"] = len([tid for tid in to if tid not in te])
    counts["total"] = sum(counts.values())
    return counts
