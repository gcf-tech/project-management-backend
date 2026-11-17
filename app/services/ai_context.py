"""Constructor del contexto organizacional para el planificador con IA.

Equivale al "RAG organizacional" de AI-StorySense (perfil del equipo + historial),
pero en vez de un JSON cargado a mano se arma desde la base de datos del Deck:

- Miembros: cargo, equipo, rol, y su historial real (tarjetas completadas en este
  tablero y en total, títulos recientes, carga abierta, horas si el equipo las usa).
- Proyectos históricos del tablero: tarjetas padre con sus subtareas y asignados.
  Son la referencia de "cómo organiza su trabajo este equipo" y el estándar de
  calidad contra el que se evalúa cada propuesta (ver criterio de evaluación).
- Flujo del área: columnas del tablero.

Decisiones tomadas tras revisar el contexto real del Tech Board (2026-11-15):
- `users.job_title` está vacío en producción → cadena de respaldo
  assessment_employees.cargo/area → workspace_profiles.departamento.
- Nadie registra horas → se omite la métrica cuando es 0 en todo el equipo.
- Miembros de otros equipos solo entran si ya han tenido tarjetas en este tablero.
- Proyectos históricos: se priorizan los que tienen asignados (señal de analogía)
  y se evita que un solo producto/persona acapare la lista.

Solo lectura. Devuelve un dict serializable que se persiste como `context_snapshot`
en la propuesta, para que cualquier resultado sea reproducible.
"""
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    User, Team, DeckBoard, DeckColumn, DeckCard, DeckCardAssignee, DeckTimeLog,
    AssessmentEmployee, WorkspaceProfile,
)

# Límites para mantener el prompt acotado (misma filosofía que AIASE: JSON compacto).
MAX_MEMBERS = 30
MAX_RECENT_TITLES = 6
MAX_HISTORY_PROJECTS = 10
MAX_SUBTASKS_PER_PROJECT = 10
MAX_SAME_PRODUCT_PROJECTS = 3   # evita que un mismo producto/persona acapare el historial
MAX_TEXT = 140


def _clip(s: Optional[str], n: int = MAX_TEXT) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[:n - 1] + "…"


def _strip_html(s: Optional[str]) -> str:
    return re.sub(r"<[^>]+>", " ", s or "")


def _is_done(card: DeckCard) -> bool:
    return card.status == "done" or card.completed_at is not None


def _resolve_job_titles(db: Session, user_ids: List[int]) -> Dict[int, str]:
    """users.job_title → assessment_employees.cargo (+área) → workspace_profiles.departamento."""
    out: Dict[int, str] = {}
    if not user_ids:
        return out
    for uid, jt in db.query(User.id, User.job_title).filter(User.id.in_(user_ids)).all():
        if jt and jt.strip():
            out[uid] = jt.strip()
    missing = [u for u in user_ids if u not in out]
    if missing:
        for uid, cargo, area in (db.query(AssessmentEmployee.user_id, AssessmentEmployee.cargo, AssessmentEmployee.area)
                                 .filter(AssessmentEmployee.user_id.in_(missing)).all()):
            label = " / ".join(x.strip() for x in (cargo, area) if x and x.strip())
            if label:
                out[uid] = label
    missing = [u for u in user_ids if u not in out]
    if missing:
        for uid, dep in (db.query(WorkspaceProfile.user_id, WorkspaceProfile.departamento)
                         .filter(WorkspaceProfile.user_id.in_(missing)).all()):
            if dep and dep.strip():
                out[uid] = dep.strip()
    return out


def build_board_context(db: Session, board: DeckBoard, *, include_history: bool = True,
                        exclude_project_id: Optional[int] = None) -> Dict[str, Any]:
    """Arma el contexto para un tablero. `include_history=False` produce el
    modo "sin contexto" (equivalente a E1/E3 del estudio empírico): solo la lista
    de miembros con su cargo, sin historial ni proyectos previos.

    `exclude_project_id` (backtest, leave-one-out): esa tarjeta padre y sus
    subtareas se omiten del historial y de las estadísticas de los miembros, para
    que la IA no vea la respuesta que se le va a pedir reconstruir."""
    excluded: set = set()
    if exclude_project_id is not None:
        excluded = {exclude_project_id} | {r[0] for r in db.query(DeckCard.id)
                                            .filter(DeckCard.parent_card_id == exclude_project_id).all()}
    team: Optional[Team] = board.team
    columns = (db.query(DeckColumn).filter(DeckColumn.board_id == board.id)
               .order_by(DeckColumn.position).all())
    team_names = dict(db.query(Team.id, Team.name).all())

    # ── Universo de miembros ────────────────────────────────────────────────
    all_users: List[User] = (db.query(User).filter(User.is_active.is_(True))
                             .order_by(User.display_name).all())
    # Quién ha tenido tarjetas en ESTE tablero (para filtrar gente de otros equipos).
    on_board = {r[0] for r in (db.query(DeckCardAssignee.user_id)
                               .join(DeckCard, DeckCard.id == DeckCardAssignee.card_id)
                               .filter(DeckCard.board_id == board.id).distinct().all())}
    users = [u for u in all_users if u.team_id == board.team_id or u.id in on_board]
    users.sort(key=lambda u: (u.team_id != board.team_id, u.display_name or ""))
    users = users[:MAX_MEMBERS]
    uids = [u.id for u in users]
    job_titles = _resolve_job_titles(db, uids)

    # ── Historial por miembro ──────────────────────────────────────────────
    stats: Dict[int, Dict[str, Any]] = {}
    team_logs_hours = False
    if include_history and uids:
        rows = (db.query(DeckCardAssignee.user_id, DeckCard)
                .join(DeckCard, DeckCard.id == DeckCardAssignee.card_id)
                .filter(DeckCardAssignee.user_id.in_(uids), DeckCard.archived.is_(False))
                .order_by(DeckCard.id.desc()).all())
        per_user: Dict[int, List[DeckCard]] = defaultdict(list)
        for uid, card in rows:
            if card.id in excluded:
                continue
            per_user[uid].append(card)
        minutes = dict(db.query(DeckTimeLog.user_id, func.coalesce(func.sum(DeckTimeLog.minutes), 0))
                       .filter(DeckTimeLog.user_id.in_(uids)).group_by(DeckTimeLog.user_id).all())
        team_logs_hours = any((m or 0) > 0 for m in minutes.values())
        parent_title = {}
        parent_ids_needed = {c.parent_card_id for cs in per_user.values() for c in cs if c.parent_card_id}
        if parent_ids_needed:
            parent_title = dict(db.query(DeckCard.id, DeckCard.title).filter(DeckCard.id.in_(parent_ids_needed)).all())
        for uid in uids:
            cards = per_user.get(uid, [])
            done = [c for c in cards if _is_done(c)]
            # Productos/proyectos en los que ha participado (padres de sus tarjetas), más recientes primero.
            seen, projects = set(), []
            for c in sorted(cards, key=lambda c: -c.id):
                pid = c.parent_card_id
                if pid and pid not in excluded and pid in parent_title and pid not in seen:
                    seen.add(pid); projects.append(_clip(parent_title[pid], 70))
                if len(projects) >= 6:
                    break
            open_ = [c for c in cards if not _is_done(c) and c.status != "cancelled"]
            # Títulos recientes: primero los de este tablero, luego el resto.
            done.sort(key=lambda c: (c.board_id != board.id, -c.id))
            stats[uid] = {
                "completed_count": len(done),
                "completed_on_board": len([c for c in done if c.board_id == board.id]),
                "open_count": len(open_),
                "hours_logged": round((minutes.get(uid, 0) or 0) / 60, 1),
                "recent_completed_titles": [_clip(c.title, 80) for c in done[:MAX_RECENT_TITLES]],
                "projects_worked": projects,
            }

    members: List[Dict[str, Any]] = []
    for u in users:
        m = {
            "id": u.id,
            "name": u.display_name,
            "job_title": job_titles.get(u.id, ""),
            "team": team_names.get(u.team_id, "") if u.team_id else "",
            "same_team": u.team_id == board.team_id,
            "role": u.deck_role or u.role or "member",
        }
        if include_history:
            m.update(stats.get(u.id, {}))
        members.append(m)

    # ── Proyectos históricos del tablero (tarjetas padre con subtareas) ─────
    history: List[Dict[str, Any]] = []
    if include_history:
        parent_ids = [r[0] for r in db.query(DeckCard.parent_card_id)
                      .filter(DeckCard.parent_card_id.isnot(None)).distinct().all()]
        parents = (db.query(DeckCard)
                   .filter(DeckCard.id.in_(parent_ids), DeckCard.board_id == board.id,
                           DeckCard.archived.is_(False))
                   .order_by(DeckCard.id.desc()).all()) if parent_ids else []
        name_of = {u.id: u.display_name for u in all_users}
        candidates = []
        for p in parents:
            if p.id in excluded:
                continue
            subs = (db.query(DeckCard).filter(DeckCard.parent_card_id == p.id)
                    .order_by(DeckCard.created_at).all())
            if not subs:
                continue
            assigned = sum(1 for s in subs if s.assignees)
            assignee_set = frozenset(a.user_id for s in subs for a in s.assignees)
            candidates.append((p, subs, assigned, assignee_set))
        # Prioridad: proyectos con asignados (señal de analogía) y más recientes;
        # se limita cuántos proyectos "del mismo dueño único" entran para no sesgar.
        candidates.sort(key=lambda c: (c[2] == 0, -c[0].id))
        single_owner_count: Dict[frozenset, int] = defaultdict(int)
        for p, subs, assigned, assignee_set in candidates:
            if len(history) >= MAX_HISTORY_PROJECTS:
                break
            if len(assignee_set) == 1:
                single_owner_count[assignee_set] += 1
                if single_owner_count[assignee_set] > MAX_SAME_PRODUCT_PROJECTS:
                    continue
            history.append({
                "id": p.id,
                "title": _clip(p.title, 100),
                "description": _clip(_strip_html(p.description), 220),
                "created_at": p.created_at.date().isoformat() if p.created_at else None,
                "completed": _is_done(p),
                "duration_days": ((p.completed_at - p.start_date).days
                                  if p.completed_at and p.start_date else None),
                "subtask_count": len(subs),
                "subtasks": [{
                    "title": _clip(s.title, 90),
                    "assignees": [name_of.get(a.user_id, f"#{a.user_id}") for a in s.assignees],
                    "assignee_ids": [a.user_id for a in s.assignees],
                    "completed": _is_done(s),
                } for s in subs[:MAX_SUBTASKS_PER_PROJECT]],
            })
        # Orden final por recencia para que el modelo vea primero lo más actual.
        history.sort(key=lambda h: -h["id"])

    return {
        "board": {"id": board.id, "title": board.title, "team": team.name if team else ""},
        "columns": [{"id": c.id, "title": c.title, "position": c.position} for c in columns],
        "members": members,
        "history_projects": history,
        "context_mode": "full" if include_history else "none",
        "team_logs_hours": team_logs_hours,
    }


def summarize_context_for_prompt(ctx: Dict[str, Any]) -> str:
    """Render compacto del contexto para el prompt. Se mantiene legible para que
    el mismo texto pueda citarse en el documento del trabajo de grado."""
    lines: List[str] = []
    b = ctx["board"]
    full = ctx["context_mode"] == "full"
    lines.append(f"TABLERO: {b['title']} (equipo: {b['team'] or 'n/d'})")
    lines.append("ETAPAS DEL FLUJO: " + " → ".join(c["title"] for c in ctx["columns"]))
    lines.append("")
    lines.append("MIEMBROS DISPONIBLES (usa exactamente estos ids en suggested_assignee_id):")
    for m in ctx["members"]:
        base = f"- id={m['id']} | {m['name']} | cargo: {m['job_title'] or 'n/d'} | equipo: {m['team'] or 'n/d'}"
        if not m["same_team"]:
            base += " (otro equipo, ha colaborado en este tablero)"
        if full:
            base += (f" | completadas: {m.get('completed_count', 0)}"
                     f" ({m.get('completed_on_board', 0)} en este tablero)"
                     f" | abiertas: {m.get('open_count', 0)}")
            if ctx.get("team_logs_hours") and m.get("hours_logged"):
                base += f" | horas registradas: {m['hours_logged']}"
            if m.get("projects_worked"):
                base += " | proyectos en los que ha trabajado: " + "; ".join(m["projects_worked"])
            if m.get("recent_completed_titles"):
                base += " | tareas que ha hecho: " + "; ".join(m["recent_completed_titles"])
        lines.append(base)
    if full:
        lines.append("")
        if ctx["history_projects"]:
            lines.append("PROYECTOS ANTERIORES DE ESTE TABLERO (referencia de cómo se organiza el equipo; cita el id en reference_project_id):")
            for p in ctx["history_projects"]:
                extra = f" | {p['subtask_count']} subtareas"
                if p.get("duration_days"):
                    extra += f" | duró {p['duration_days']} días"
                lines.append(f"- proyecto id={p['id']} | {p['title']}{extra} | {p['description'] or ''}")
                for s in p["subtasks"]:
                    who = ", ".join(s["assignees"]) or "sin asignar"
                    lines.append(f"    · {s['title']} → {who}")
                if p["subtask_count"] > len(p["subtasks"]):
                    lines.append(f"    · (+{p['subtask_count'] - len(p['subtasks'])} subtareas más)")
        else:
            lines.append("PROYECTOS ANTERIORES: no hay historial en este tablero (declara confidence baja).")
    return "\n".join(lines)
