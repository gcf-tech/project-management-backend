"""Deck · Planificación asistida por IA (módulo del trabajo de grado AI-StorySense).

Flujo (todo con supervisión humana, nada se escribe en el tablero sin aprobar):

  POST /boards/{id}/proposals        describe el proyecto → propuesta en borrador
  GET  /proposals/{id}               ver propuesta (ai + final + eventos)
  PATCH /proposals/{id}              guardar edición humana (payload_final)
  POST /proposals/{id}/regenerate    rechazar con feedback → iteración quirúrgica
  POST /proposals/{id}/approve       crea tarjeta padre + subtareas asignadas
  POST /proposals/{id}/discard       descartar
  GET  /boards/{id}/proposals        historial del tablero
  GET  /boards/{id}/context          contexto que vería la IA (para auditoría/doc)
  GET  /metrics                      AAR, ediciones promedio, etc. (admin/leader)

Router aparte de deck.py para no tocar el archivo principal; reutiliza sus
helpers de autenticación, contexto de permisos, actividad y recálculo de fechas.
"""
from typing import Annotated, Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.core import config
from app.core.datetime_utils import utc_now
from app.db.models import (
    User, DeckBoard, DeckColumn, DeckCard, DeckCardAssignee, DeckCardFollower,
    DeckCardTeam, DeckAiProposal, DeckAiProposalEvent,
)
from app.api.v1.deck import (
    _get_current_user, _build_deck_context, _get_board_or_404, _require_see_board,
    _log_activity, _next_position, _parse_dt, _recompute_parent_due, _serialize_card,
    _serialize_subtask, _user_brief,
)
from app.services import ai_planner
from app.services.ai_context import build_board_context
from app.services.ai_svc import AIError

router = APIRouter()


# ============================================================
# SCHEMAS
# ============================================================

class ProposalIn(BaseModel):
    text: str = Field(min_length=20, max_length=8000)
    contextMode: str = "full"       # full | none (none = experimento sin contexto)
    inputKind: str = "text"         # text | audio (reservado)


class ProposalPatch(BaseModel):
    payload: Dict[str, Any]


class RegenerateIn(BaseModel):
    feedback: str = Field(min_length=3, max_length=2000)


class ApproveIn(BaseModel):
    columnId: Optional[int] = None
    startDate: Optional[str] = None
    dueDate: Optional[str] = None
    priority: Optional[str] = None


class DiscardIn(BaseModel):
    reason: Optional[str] = None


# ============================================================
# HELPERS
# ============================================================

def _feature_allowed(board_id: int) -> bool:
    return not config.AI_PLANNING_BOARD_IDS or board_id in config.AI_PLANNING_BOARD_IDS


def _require_feature(board: DeckBoard):
    if not config.AI_ENABLED:
        raise HTTPException(status_code=503, detail="El servicio de IA no está configurado en este entorno")
    if not _feature_allowed(board.id):
        raise HTTPException(status_code=403, detail="La planificación con IA no está habilitada en este tablero")


def _require_can_plan(ctx, board: DeckBoard):
    """Puede planificar quien puede crear tarjetas en el tablero (mismo criterio
    que create_card): equipo dueño o admin."""
    _require_see_board(ctx, board)
    if not ctx.is_owner_team(board.team_id):
        raise HTTPException(status_code=403, detail="No puedes crear tarjetas en este tablero")


def _get_proposal_or_404(db: Session, proposal_id: int) -> DeckAiProposal:
    p = db.query(DeckAiProposal).filter(DeckAiProposal.id == proposal_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Propuesta no encontrada")
    return p


def _event(db: Session, p: DeckAiProposal, user: Optional[User], event: str, *,
           from_status=None, to_status=None, feedback=None, detail=None):
    db.add(DeckAiProposalEvent(
        proposal_id=p.id, user_id=user.id if user else None, event=event,
        from_status=from_status, to_status=to_status, feedback=feedback,
        detail=detail, created_at=utc_now(),
    ))


def _serialize_event(e: DeckAiProposalEvent) -> Dict[str, Any]:
    return {
        "id": e.id, "event": e.event, "fromStatus": e.from_status, "toStatus": e.to_status,
        "feedback": e.feedback, "detail": e.detail,
        "user": _user_brief(e.user) if e.user else None,
        "createdAt": e.created_at.isoformat() if e.created_at else None,
    }


def _serialize_proposal(p: DeckAiProposal, *, full: bool = True) -> Dict[str, Any]:
    out = {
        "id": p.id, "boardId": p.board_id, "status": p.status,
        "inputText": p.input_text, "inputKind": p.input_kind, "contextMode": p.context_mode,
        "model": p.model, "regenerations": p.regenerations,
        "manualEdits": p.manual_edits, "parentCardId": p.parent_card_id,
        "createdBy": _user_brief(p.creator) if p.creator else None,
        "createdAt": p.created_at.isoformat() if p.created_at else None,
        "updatedAt": p.updated_at.isoformat() if p.updated_at else None,
        "approvedAt": p.approved_at.isoformat() if p.approved_at else None,
        "proposal": p.payload_final or p.payload_ai,
    }
    if full:
        out["proposalAi"] = p.payload_ai
        out["warnings"] = p.validation_warnings or []
        out["usage"] = {"promptTokens": p.prompt_tokens, "completionTokens": p.completion_tokens}
        out["events"] = [_serialize_event(e) for e in p.events]
        # Miembros del contexto: el front los usa para el selector de asignado.
        snap = p.context_snapshot or {}
        out["members"] = [{"id": m["id"], "name": m["name"], "jobTitle": m.get("job_title", ""),
                           "team": m.get("team", ""), "sameTeam": m.get("same_team", False)}
                          for m in snap.get("members", [])]
        out["columns"] = snap.get("columns", [])
    return out


# ============================================================
# ENDPOINTS
# ============================================================

@router.get("/boards/{board_id}/context")
async def board_ai_context(
    board_id: int,
    authorization: Annotated[str, Header()],
    mode: str = "full",
    db: Session = Depends(get_db),
):
    """Contexto que recibiría la IA para este tablero. Útil para auditar qué sabe
    el modelo y para documentar el 'RAG organizacional' en el trabajo de grado."""
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    board = _get_board_or_404(db, board_id)
    _require_can_plan(ctx, board)
    return {"enabled": config.AI_ENABLED and _feature_allowed(board.id),
            "context": build_board_context(db, board, include_history=(mode != "none"))}


@router.post("/boards/{board_id}/proposals")
async def create_proposal(
    board_id: int,
    body: ProposalIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    board = _get_board_or_404(db, board_id)
    _require_can_plan(ctx, board)
    _require_feature(board)

    mode = "none" if body.contextMode == "none" else "full"
    context = build_board_context(db, board, include_history=(mode == "full"))
    try:
        gen = await ai_planner.generate_plan(context, body.text)
    except AIError as e:
        raise HTTPException(status_code=502, detail=str(e))

    p = DeckAiProposal(
        board_id=board.id, created_by=user.id, input_text=body.text.strip(),
        input_kind="audio" if body.inputKind == "audio" else "text",
        context_mode=mode, model=gen["model"], status="draft",
        payload_ai=gen["proposal"], payload_final=None,
        context_snapshot=context, validation_warnings=gen["warnings"],
        prompt_tokens=gen["usage"]["prompt_tokens"], completion_tokens=gen["usage"]["completion_tokens"],
        created_at=utc_now(), updated_at=utc_now(),
    )
    db.add(p)
    db.flush()
    _event(db, p, user, "generated", to_status="draft",
           detail={"warnings": gen["warnings"], "tasks": len(gen["proposal"]["tasks"])})
    db.commit()
    db.refresh(p)
    return _serialize_proposal(p)


@router.get("/boards/{board_id}/proposals")
async def list_proposals(
    board_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    board = _get_board_or_404(db, board_id)
    _require_see_board(ctx, board)
    rows = (db.query(DeckAiProposal).filter(DeckAiProposal.board_id == board.id)
            .order_by(DeckAiProposal.created_at.desc()).limit(50).all())
    return {"proposals": [_serialize_proposal(p, full=False) for p in rows]}


@router.get("/proposals/{proposal_id}")
async def get_proposal(
    proposal_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    p = _get_proposal_or_404(db, proposal_id)
    _require_see_board(ctx, p.board)
    return _serialize_proposal(p)


@router.patch("/proposals/{proposal_id}")
async def edit_proposal(
    proposal_id: int,
    body: ProposalPatch,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Guarda la versión editada por el humano. Se re-valida con la misma capa
    programática (ids de miembros, longitudes) y se recalcula el conteo de
    ediciones respecto a la salida original de la IA."""
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    p = _get_proposal_or_404(db, proposal_id)
    _require_can_plan(ctx, p.board)
    if p.status != "draft":
        raise HTTPException(status_code=409, detail="La propuesta ya no es editable")

    normalized, warnings = ai_planner.validate_and_normalize(body.payload, p.context_snapshot or {})
    if len(normalized["tasks"]) < 1:
        raise HTTPException(status_code=400, detail="La propuesta debe tener al menos una tarea")
    edits = ai_planner.count_manual_edits(p.payload_ai, normalized)
    p.payload_final = normalized
    p.manual_edits = edits
    p.updated_at = utc_now()
    _event(db, p, user, "edited", from_status="draft", to_status="draft",
           detail={"edits": edits, "warnings": warnings})
    db.commit()
    db.refresh(p)
    return _serialize_proposal(p)


@router.post("/proposals/{proposal_id}/regenerate")
async def regenerate_proposal(
    proposal_id: int,
    body: RegenerateIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Rechazo con feedback → iteración quirúrgica (4.3.3): se vuelve a llamar al
    modelo con la propuesta anterior y el feedback; la salida reemplaza payload_ai
    (la original queda en el evento) y se descarta la edición parcial."""
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    p = _get_proposal_or_404(db, proposal_id)
    _require_can_plan(ctx, p.board)
    _require_feature(p.board)
    if p.status != "draft":
        raise HTTPException(status_code=409, detail="La propuesta ya no es editable")
    if p.regenerations >= 5:
        raise HTTPException(status_code=429, detail="Máximo 5 regeneraciones por propuesta")

    context = p.context_snapshot or build_board_context(db, p.board, include_history=(p.context_mode == "full"))
    previous = p.payload_final or p.payload_ai
    try:
        gen = await ai_planner.generate_plan(context, p.input_text, feedback=body.feedback, previous=previous)
    except AIError as e:
        raise HTTPException(status_code=502, detail=str(e))

    _event(db, p, user, "regenerated", from_status="draft", to_status="draft",
           feedback=body.feedback,
           detail={"previous": previous, "warnings": gen["warnings"]})
    p.payload_ai = gen["proposal"]
    p.payload_final = None
    p.manual_edits = None
    p.validation_warnings = gen["warnings"]
    p.model = gen["model"]
    p.regenerations = (p.regenerations or 0) + 1
    p.prompt_tokens = (p.prompt_tokens or 0) + gen["usage"]["prompt_tokens"]
    p.completion_tokens = (p.completion_tokens or 0) + gen["usage"]["completion_tokens"]
    p.updated_at = utc_now()
    db.commit()
    db.refresh(p)
    return _serialize_proposal(p)


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: int,
    body: ApproveIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Único punto donde la propuesta toca el tablero: crea la tarjeta padre y una
    subtarea por tarea, con el asignado final. Replica la lógica de create_card y
    create_subtasks_bulk de deck.py (owner team, followers, actividad, fechas)."""
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    p = _get_proposal_or_404(db, proposal_id)
    board = p.board
    _require_can_plan(ctx, board)
    if p.status != "draft":
        raise HTTPException(status_code=409, detail="La propuesta ya fue procesada")

    final = p.payload_final or p.payload_ai
    tasks = final.get("tasks") or []
    if not tasks:
        raise HTTPException(status_code=400, detail="La propuesta no tiene tareas")

    # Columna destino: la indicada o la primera del tablero.
    col = None
    if body.columnId:
        col = db.query(DeckColumn).filter(DeckColumn.id == body.columnId, DeckColumn.board_id == board.id).first()
    if not col:
        col = db.query(DeckColumn).filter(DeckColumn.board_id == board.id).order_by(DeckColumn.position).first()
    col_id = col.id if col else None

    valid_users = {u.id for u in db.query(User.id).filter(User.is_active.is_(True)).all()}
    start_dt = _parse_dt(body.startDate)
    due_dt = _parse_dt(body.dueDate)
    priority = body.priority if body.priority in ("low", "medium", "high", "urgent") else None
    now = utc_now()

    # ── Tarjeta padre ──
    proj = final.get("project") or {}
    parent = DeckCard(
        board_id=board.id, column_id=col_id, owner_team_id=board.team_id,
        title=(proj.get("title") or "Proyecto")[:255], description=proj.get("description") or None,
        priority=priority, start_date=start_dt, due_date=due_dt,
        position=_next_position(db, DeckCard, column_id=col_id) if col_id else 0,
        created_by=user.id, created_at=now, updated_at=now,
    )
    db.add(parent)
    db.flush()
    db.add(DeckCardTeam(card_id=parent.id, team_id=board.team_id, is_owner=True, shared_by=user.id, created_at=now))
    parent_followers = {user.id}

    # ── Subtareas ──
    created: List[DeckCard] = []
    pos = _next_position(db, DeckCard, column_id=col_id) if col_id else 0
    for t in tasks:
        sub = DeckCard(
            board_id=board.id, column_id=col_id, owner_team_id=board.team_id,
            parent_card_id=parent.id, title=(t.get("title") or "Tarea")[:255],
            description=_subtask_description(t), priority=priority,
            start_date=start_dt, due_date=due_dt,
            position=pos, created_by=user.id, created_at=now, updated_at=now,
        )
        pos += 1
        db.add(sub)
        db.flush()
        db.add(DeckCardTeam(card_id=sub.id, team_id=board.team_id, is_owner=True, shared_by=user.id, created_at=now))
        aid = t.get("suggested_assignee_id")
        followers = {user.id}
        if aid in valid_users:
            db.add(DeckCardAssignee(card_id=sub.id, user_id=aid, assigned_by=user.id, created_at=now))
            followers.add(aid)
            parent_followers.add(aid)
        for uid in followers:
            db.add(DeckCardFollower(card_id=sub.id, user_id=uid, created_at=now))
        db.flush()
        _log_activity(db, sub, user, "created",
                      message=f"{user.display_name} creó esta subtarea desde la propuesta IA #{p.id}",
                      payload={"aiProposalId": p.id, "taskId": t.get("id")}, notify=False)
        created.append(sub)

    for uid in parent_followers:
        db.add(DeckCardFollower(card_id=parent.id, user_id=uid, created_at=now))
    db.flush()
    _log_activity(db, parent, user, "created",
                  message=f"{user.display_name} creó este proyecto desde la propuesta IA #{p.id}",
                  payload={"aiProposalId": p.id, "subtasks": len(created)}, notify=False)
    _recompute_parent_due(db, parent.id)

    edits = p.manual_edits or ai_planner.count_manual_edits(p.payload_ai, final)
    p.payload_final = final
    p.manual_edits = edits
    p.status = "approved"
    p.parent_card_id = parent.id
    p.approved_at = now
    p.updated_at = now
    _event(db, p, user, "approved", from_status="draft", to_status="approved",
           detail={"parentCardId": parent.id, "subtasks": len(created), "edits": edits,
                   "approvedWithoutEdits": (edits.get("total", 0) == 0 and p.regenerations == 0)})
    db.commit()
    db.refresh(parent)
    for c in created:
        db.refresh(c)
    return {
        "proposal": _serialize_proposal(p, full=False),
        "card": _serialize_card(parent, full=True),
        "subtasks": [_serialize_subtask(c) for c in created],
    }


def _subtask_description(t: Dict[str, Any]) -> Optional[str]:
    """Descripción de la subtarea: la de la IA + una nota de trazabilidad con el
    razonamiento de asignación y la estimación, para que quede visible en la tarjeta."""
    parts = []
    if t.get("description"):
        parts.append(str(t["description"]))
    meta = []
    if t.get("estimated_hours"):
        meta.append(f"Estimación IA: {t['estimated_hours']} h (confianza {t.get('confidence', 'n/d')})")
    if t.get("assignee_reasoning"):
        meta.append(f"Asignación sugerida: {t['assignee_reasoning']}")
    if meta:
        parts.append("<p><em>" + " · ".join(meta) + "</em></p>")
    return "\n".join(parts) or None


@router.post("/proposals/{proposal_id}/discard")
async def discard_proposal(
    proposal_id: int,
    body: DiscardIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    p = _get_proposal_or_404(db, proposal_id)
    _require_can_plan(ctx, p.board)
    if p.status != "draft":
        raise HTTPException(status_code=409, detail="La propuesta ya fue procesada")
    _event(db, p, user, "discarded", from_status="draft", to_status="discarded", feedback=body.reason)
    p.status = "discarded"
    p.updated_at = utc_now()
    db.commit()
    return {"ok": True, "status": p.status}


@router.get("/metrics")
async def ai_metrics(
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Métricas de evaluación (Cuadro 5.1 del documento, adaptadas):
    - AAR: % de propuestas aprobadas sin edición ni regeneración.
    - Ediciones promedio por propuesta aprobada, desglosadas por campo.
    - Comparación con/sin contexto (context_mode)."""
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    if not ctx.can_see_analytics():
        raise HTTPException(status_code=403, detail="Solo admin o leader")
    q = db.query(DeckAiProposal)
    if ctx.role != "admin":
        board_ids = [b.id for b in db.query(DeckBoard.id).filter(DeckBoard.team_id.in_(ctx.team_ids)).all()]
        q = q.filter(DeckAiProposal.board_id.in_(board_ids or [-1]))
    rows = q.all()

    def _bucket(items):
        total = len(items)
        approved = [p for p in items if p.status == "approved"]
        clean = [p for p in approved if (p.manual_edits or {}).get("total", 0) == 0 and (p.regenerations or 0) == 0]
        fields = ["project", "task_title", "task_description", "assignee", "hours", "tasks_added", "tasks_removed", "total"]
        avg = {f: (round(sum((p.manual_edits or {}).get(f, 0) for p in approved) / len(approved), 2) if approved else 0)
               for f in fields}
        return {
            "proposals": total,
            "approved": len(approved),
            "discarded": len([p for p in items if p.status == "discarded"]),
            "draft": len([p for p in items if p.status == "draft"]),
            "approvedWithoutEdits": len(clean),
            "aar": round(100 * len(clean) / len(approved), 1) if approved else None,
            "avgRegenerations": round(sum(p.regenerations or 0 for p in items) / total, 2) if total else 0,
            "avgEdits": avg,
            "avgTasks": round(sum(len((p.payload_final or p.payload_ai).get("tasks", [])) for p in items) / total, 1) if total else 0,
        }

    return {
        "all": _bucket(rows),
        "byContext": {
            "full": _bucket([p for p in rows if p.context_mode == "full"]),
            "none": _bucket([p for p in rows if p.context_mode == "none"]),
        },
    }
