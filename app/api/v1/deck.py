"""
Deck (Teamwork Kanban) API endpoints.

A Trello-like board per team. Each Nextcloud-group-backed Team owns one board
("deck") with reorderable columns (task lists) and cards. A card has multiple
assignees, tags, due/start dates, a description, comments and an immutable
activity log. Access is governed by users.deck_role with a fallback to
users.role:

  - admin  → sees & manages every team's board
  - member → sees their own team's board (+ cards shared with their team, F3)

Team membership is NOT managed here: it lives in users.team_id, synced from
Nextcloud groups (see app/services/nextcloud_svc.py).
"""
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Header, UploadFile, File, Form
from fastapi.responses import Response
from sqlalchemy.orm import Session
import os
from sqlalchemy import and_, func, case
from typing import Annotated, List, Optional, Dict, Any
from datetime import datetime, date, timedelta, timezone
from pydantic import BaseModel, Field

from app.api.dependencies import get_db
from app.core.security import get_nc_user_info
from app.services.nextcloud_svc import push_nc_notification
from app.services.email_svc import send_email, build_notification_email
from app.core.datetime_utils import utc_now
from app.db.models import (
    User,
    Team,
    DeckBoard,
    DeckColumn,
    DeckProject,
    DeckCard,
    DeckCardAssignee,
    DeckCardFollower,
    DeckCardTeam,
    DeckCardFavorite,
    DeckTag,
    DeckCardTag,
    DeckComment,
    DeckActivity,
    DeckNotification,
    DeckAttachment,
    DeckTimeLog,
    DeckStageNote,
)

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024  # 15 MB

router = APIRouter()

# Canonical workflow pipeline seeded on every new board (name, color).
# Flow: Creación → Prototipado → Revisión → Desarrollo → Testing interno →
#       Testing externo → Documentación → Lanzado (moves back and forth allowed).
DEFAULT_COLUMNS = [
    ("Creación", "#8a93a3", 0),
    ("Prototipado", "#3b82f6", 180),
    ("Revisión", "#e0a11f", 120),
    ("Desarrollo", "#F37022", 480),
    ("Testing interno", "#8b5cf6", 120),
    ("Testing externo", "#14b8a6", 120),
    ("Documentación", "#6366f1", 60),
    ("Lanzado", "#1f7a44", 0),
]


# ============================================================
# SCHEMAS
# ============================================================

class BoardIn(BaseModel):
    teamId: int
    title: str
    description: Optional[str] = None
    color: Optional[str] = None


class BoardPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    archived: Optional[bool] = None


class ColumnIn(BaseModel):
    title: str
    color: Optional[str] = None
    wipLimit: Optional[int] = None
    defaultMinutes: Optional[int] = None


class ColumnPatch(BaseModel):
    title: Optional[str] = None
    color: Optional[str] = None
    wipLimit: Optional[int] = None
    defaultMinutes: Optional[int] = None


class ColumnMove(BaseModel):
    position: int


class CardIn(BaseModel):
    title: str
    description: Optional[str] = None
    columnId: Optional[int] = None
    projectId: Optional[int] = None
    priority: Optional[str] = None
    startDate: Optional[str] = None       # ISO yyyy-mm-dd
    dueDate: Optional[str] = None         # ISO datetime
    assigneeIds: List[int] = Field(default_factory=list)
    tagIds: List[int] = Field(default_factory=list)
    clientOpId: Optional[str] = None


class CardPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    projectId: Optional[int] = None
    priority: Optional[str] = None
    startDate: Optional[str] = None
    dueDate: Optional[str] = None
    prototypeUrl: Optional[str] = None


class TimeLogIn(BaseModel):
    minutes: int
    description: Optional[str] = None
    date: Optional[str] = None
    billable: bool = False


class StageNoteIn(BaseModel):
    body: str


class CardMove(BaseModel):
    columnId: int
    position: int


class ListOrderIn(BaseModel):
    orderedIds: List[int]


class SubtaskIn(BaseModel):
    title: str
    boardId: Optional[int] = None   # board destino; por defecto el del equipo del usuario
    description: Optional[str] = None
    priority: Optional[str] = None
    startDate: Optional[str] = None
    dueDate: Optional[str] = None
    columnId: Optional[int] = None
    assigneeIds: List[int] = Field(default_factory=list)


class AssigneeIn(BaseModel):
    userId: int


class TagIn(BaseModel):
    name: str
    color: Optional[str] = None


class TagAttach(BaseModel):
    tagId: Optional[int] = None
    name: Optional[str] = None
    color: Optional[str] = None


class CommentIn(BaseModel):
    body: str
    parentId: Optional[int] = None
    mentions: List[int] = Field(default_factory=list)
    attachmentIds: List[int] = Field(default_factory=list)


class CommentPatch(BaseModel):
    body: str


# ============================================================
# AUTH / CONTEXT HELPERS
# ============================================================

async def _get_current_user(authorization: str, db: Session) -> User:
    nc_data = await get_nc_user_info(authorization)
    user = db.query(User).filter(User.nc_user_id == nc_data["id"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


class DeckContext:
    """Resolved Deck access context for the current user."""
    def __init__(self, user: User, role: str, team_ids: set, visible_board_ids):
        self.user = user
        self.role = role                       # "admin" | "member"
        self.team_ids = team_ids               # set[int] — user's team(s)
        self.visible_board_ids = visible_board_ids  # set[int] or None (= all)

    def is_admin(self) -> bool:
        return self.role == "admin"

    def can_see_board(self, board_id: int) -> bool:
        return self.visible_board_ids is None or board_id in self.visible_board_ids

    def can_see_card(self, card: DeckCard) -> bool:
        if self.is_admin():
            return True
        if card.owner_team_id in self.team_ids:
            return True
        return any(st.team_id in self.team_ids for st in card.shared_teams)

    def can_write_card(self, card: DeckCard) -> bool:
        return self.is_admin() or self.can_see_card(card)

    def is_owner_team(self, team_id: int) -> bool:
        return self.is_admin() or team_id in self.team_ids


def _build_deck_context(db: Session, user: User) -> DeckContext:
    # Deck-specific override mirrors assessment_role precedence.
    role = user.deck_role
    if not role:
        role = "admin" if user.role == "admin" else "member"

    # users.team_id is the single Nextcloud-synced team. Modeled as a set to
    # stay future-proof for multi-team membership.
    team_ids = {user.team_id} if user.team_id else set()

    if role == "admin":
        return DeckContext(user, "admin", team_ids, None)  # all boards

    own = set()
    shared = set()
    if team_ids:
        own = {b.id for b in db.query(DeckBoard.id).filter(DeckBoard.team_id.in_(team_ids)).all()}
        shared = {
            row[0] for row in db.query(DeckCard.board_id)
            .join(DeckCardTeam, DeckCardTeam.card_id == DeckCard.id)
            .filter(DeckCardTeam.team_id.in_(team_ids)).distinct().all()
        }
    return DeckContext(user, "member", team_ids, own | shared)


def _get_board_or_404(db: Session, board_id: int) -> DeckBoard:
    board = db.query(DeckBoard).filter(DeckBoard.id == board_id).first()
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")
    return board


def _get_card_or_404(db: Session, card_id: int) -> DeckCard:
    card = db.query(DeckCard).filter(DeckCard.id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    return card


def _require_see_board(ctx: DeckContext, board: DeckBoard):
    if not ctx.can_see_board(board.id):
        raise HTTPException(status_code=403, detail="No access to this board")


def _require_see_card(ctx: DeckContext, card: DeckCard):
    if not ctx.can_see_card(card):
        raise HTTPException(status_code=403, detail="No access to this card")


def _require_see_card_or_parent(db: Session, ctx: DeckContext, card: DeckCard):
    """Visible si el usuario puede ver la card, o si es una subtarea cuyo padre
    puede ver (para que el equipo del proyecto vea las subtareas de otros)."""
    if ctx.can_see_card(card):
        return
    if card.parent_card_id:
        parent = db.query(DeckCard).filter(DeckCard.id == card.parent_card_id).first()
        if parent and ctx.can_see_card(parent):
            return
    raise HTTPException(status_code=403, detail="No access to this card")


def _require_write_card(ctx: DeckContext, card: DeckCard):
    if not ctx.can_write_card(card):
        raise HTTPException(status_code=403, detail="Not allowed to edit this card")


def _require_owner_team(ctx: DeckContext, team_id: int):
    if not ctx.is_owner_team(team_id):
        raise HTTPException(status_code=403, detail="Owner-team or admin only")


# ============================================================
# DATE PARSING
# ============================================================

def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    return date.fromisoformat(value[:10])


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    v = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        # plain date → end of day not assumed; midnight UTC
        return datetime.fromisoformat(value[:10] + "T00:00:00+00:00")


EDIT_WINDOW = timedelta(minutes=5)

def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """MySQL returns naive datetimes; treat them as UTC for comparison."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

def _within_edit_window(created_at: Optional[datetime]) -> bool:
    aware = _as_utc(created_at)
    return aware is not None and (utc_now() - aware) <= EDIT_WINDOW

# Ventana para el indicador "actualizada recientemente" (punto gris en la tarea).
RECENT_WINDOW = timedelta(hours=48)

# DEV: si está activo, NO se excluye al actor de sus propias notificaciones, así
# tus propios cambios encienden el punto naranja (útil para previsualizar en
# pruebas). Activar con DECK_DEV_SELF_NOTIFY=true en el entorno. NO usar en prod.
DEV_SELF_NOTIFY = os.getenv("DECK_DEV_SELF_NOTIFY", "").strip().lower() in ("1", "true", "yes", "on")

def _is_recent(updated_at: Optional[datetime]) -> bool:
    aware = _as_utc(updated_at)
    return aware is not None and (utc_now() - aware) <= RECENT_WINDOW


# ============================================================
# SERIALIZATION
# ============================================================

def _user_brief(user: Optional[User]) -> Optional[Dict[str, Any]]:
    if not user:
        return None
    return {
        "userId": user.id,
        "ncUserId": user.nc_user_id,
        "displayName": user.display_name,
        "email": user.email,
    }


def _serialize_tag(tag: DeckTag) -> Dict[str, Any]:
    return {"id": tag.id, "boardId": tag.board_id, "name": tag.name, "color": tag.color}


def _serialize_column(col: DeckColumn) -> Dict[str, Any]:
    return {
        "id": col.id,
        "boardId": col.board_id,
        "title": col.title,
        "position": col.position,
        "color": col.color,
        "isDefault": bool(col.is_default),
        "wipLimit": col.wip_limit,
        "defaultMinutes": col.default_minutes or 0,
    }


def _serialize_board(board: DeckBoard, *, with_columns=False) -> Dict[str, Any]:
    out = {
        "id": board.id,
        "teamId": board.team_id,
        "teamName": board.team.name if board.team else None,
        "title": board.title,
        "description": board.description,
        "color": board.color,
        "archived": bool(board.archived),
    }
    if with_columns:
        out["columns"] = [_serialize_column(c) for c in sorted(board.columns, key=lambda c: c.position)]
    return out


def _serialize_card(card: DeckCard, *, full=False, sub=None) -> Dict[str, Any]:
    out = {
        "id": card.id,
        "boardId": card.board_id,
        "columnId": card.column_id,
        "ownerTeamId": card.owner_team_id,
        "projectId": card.project_id,
        "title": card.title,
        "description": card.description,
        "prototypeUrl": card.prototype_url,
        "parentCardId": card.parent_card_id,
        "listOrder": card.list_order,
        "position": card.position,
        "priority": card.priority,
        "startDate": card.start_date.isoformat() if card.start_date else None,
        "dueDate": card.due_date.isoformat() if card.due_date else None,
        "completedAt": card.completed_at.isoformat() if card.completed_at else None,
        "archived": bool(card.archived),
        "assignees": [_user_brief(a.user) for a in card.assignees],
        "tags": [_serialize_tag(ct.tag) for ct in card.tags if ct.tag],
        "createdAt": card.created_at.isoformat() if card.created_at else None,
        "updatedAt": card.updated_at.isoformat() if card.updated_at else None,
        # Actividad reciente (indicador gris a nivel de tarea).
        "recentlyUpdated": _is_recent(card.updated_at),
    }
    if full:
        out["followers"] = [_user_brief(f.user) for f in card.followers if f.user]
        out["sharedTeams"] = [
            {"teamId": st.team_id, "name": st.team.name if st.team else None, "isOwner": bool(st.is_owner)}
            for st in card.shared_teams
        ]
        out["commentCount"] = sum(1 for c in card.comments if not c.deleted_at)
        out["activityCount"] = len(card.activity)
    else:
        out["commentCount"] = sum(1 for c in card.comments if not c.deleted_at)
    if sub is not None:
        out["subtaskCount"] = sub.get("count", 0)
        out["subtaskDone"] = sub.get("done", 0)
    return out


def _augment_user_flags(db: Session, user: User, dicts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Marca por-usuario cada card serializada: `isFavorite` y `hasUnread`
    (tiene notificaciones sin leer → algo cambió recientemente en la tarea)."""
    ids = [d["id"] for d in dicts]
    if not ids:
        return dicts
    favs = {r[0] for r in db.query(DeckCardFavorite.card_id).filter(
        and_(DeckCardFavorite.user_id == user.id, DeckCardFavorite.card_id.in_(ids))).all()}
    unread = {r[0] for r in db.query(DeckNotification.card_id).filter(
        and_(DeckNotification.user_id == user.id, DeckNotification.is_read.is_(False),
             DeckNotification.card_id.in_(ids))).all()}
    for d in dicts:
        d["isFavorite"] = d["id"] in favs
        d["hasUnread"] = d["id"] in unread
    return dicts


def _subtask_rollup(db: Session, card_ids: List[int]) -> Dict[int, Dict[str, int]]:
    """Conteo de subtareas (total y completadas) por card padre, en un solo query."""
    if not card_ids:
        return {}
    rows = db.query(
        DeckCard.parent_card_id,
        func.count(DeckCard.id),
        func.sum(case((DeckCard.completed_at.isnot(None), 1), else_=0)),
    ).filter(
        and_(DeckCard.parent_card_id.in_(card_ids), DeckCard.archived.is_(False))
    ).group_by(DeckCard.parent_card_id).all()
    return {pid: {"count": int(cnt or 0), "done": int(done or 0)} for pid, cnt, done in rows}


def _serialize_attachment(a: DeckAttachment) -> Dict[str, Any]:
    return {
        "id": a.id,
        "filename": a.filename,
        "contentType": a.content_type,
        "size": a.size,
        "isImage": bool(a.content_type and a.content_type.startswith("image/")),
        "url": f"/api/decks/attachments/{a.id}",
    }


def _serialize_comment(c: DeckComment, attachments: Optional[List[DeckAttachment]] = None) -> Dict[str, Any]:
    return {
        "id": c.id,
        "cardId": c.card_id,
        "parentId": c.parent_id,
        "author": _user_brief(c.user),
        "body": "" if c.deleted_at else c.body,
        "mentions": c.mentions or [],
        "edited": c.edited_at is not None,
        "deleted": c.deleted_at is not None,
        "editable": _within_edit_window(c.created_at) and c.deleted_at is None,
        "attachments": [_serialize_attachment(a) for a in (attachments or [])],
        "createdAt": c.created_at.isoformat() if c.created_at else None,
    }


def _serialize_activity(a: DeckActivity) -> Dict[str, Any]:
    return {
        "id": a.id,
        "cardId": a.card_id,
        "eventType": a.event_type,
        "actor": _user_brief(a.actor),
        "payload": a.payload or {},
        "message": a.message,
        "createdAt": a.created_at.isoformat() if a.created_at else None,
    }


def _serialize_notification(n: DeckNotification, card_title: Optional[str]) -> Dict[str, Any]:
    return {
        "id": n.id,
        "type": n.type,
        "cardId": n.card_id,
        "cardTitle": card_title,
        "actor": _user_brief(n.actor),
        "message": n.message,
        "isRead": bool(n.is_read),
        "createdAt": n.created_at.isoformat() if n.created_at else None,
    }


# ============================================================
# ACTIVITY + NOTIFICATION FAN-OUT
# ============================================================

# event_type -> default notification type (None = no notification)
_NOTIF_FOR_EVENT = {
    "assigned": "assigned",
    "moved": "moved",
    "commented": "comment",
    "due_changed": "card_updated",
    "updated": "card_updated",
    "completed": "card_updated",
    "shared_team": "shared",
}


def _log_activity(db: Session, card: DeckCard, actor: Optional[User], event_type: str, *,
                  payload=None, message=None, extra_recipients=None, notify=True) -> DeckActivity:
    """Append an immutable activity row and (optionally) fan out notifications
    to followers ∪ assignees ∪ explicit recipients (minus the actor). Only
    stages rows; the caller owns the commit (same transaction discipline as
    assessment.save_evaluation)."""
    act = DeckActivity(
        card_id=card.id, board_id=card.board_id,
        actor_id=actor.id if actor else None,
        event_type=event_type, payload=payload, message=message,
        created_at=utc_now(),
    )
    db.add(act)
    db.flush()  # need act.id for notification linkage

    if not notify:
        return act
    ntype = _NOTIF_FOR_EVENT.get(event_type)
    if not ntype:
        return act

    recipients = {f.user_id for f in card.followers}
    recipients |= {a.user_id for a in card.assignees}
    if extra_recipients:
        recipients |= set(extra_recipients)
    if actor:
        if DEV_SELF_NOTIFY:
            recipients.add(actor.id)      # dev: notifícate a ti mismo para previsualizar
        else:
            recipients.discard(actor.id)

    for uid in recipients:
        db.add(DeckNotification(
            user_id=uid, actor_id=actor.id if actor else None,
            card_id=card.id, activity_id=act.id,
            type=ntype, message=message, is_read=False, created_at=utc_now(),
        ))
    return act


async def _dispatch_external(db: Session, authorization: str, activity_id: int) -> None:
    """Best-effort: para las notificaciones recién creadas de una actividad,
    (1) las refleja en la campana de Nextcloud y (2) envía correo a cada
    destinatario. Cualquier fallo se ignora; el canal in-app es la fuente de
    verdad."""
    rows = db.query(DeckNotification, User).join(
        User, DeckNotification.user_id == User.id
    ).filter(DeckNotification.activity_id == activity_id).all()
    if not rows:
        return

    # Título de la card (para el correo).
    card_id = rows[0][0].card_id
    card_title = None
    if card_id:
        r = db.query(DeckCard.title).filter(DeckCard.id == card_id).first()
        card_title = r[0] if r else None

    changed = False
    emails = []
    for notif, recipient in rows:
        # (1) Nextcloud bell (requiere token admin; suele fallar silenciosamente).
        if not notif.nc_pushed:
            ok = await push_nc_notification(
                authorization, recipient.nc_user_id,
                subject=notif.message or "Deck", message="",
            )
            if ok:
                notif.nc_pushed = True
                changed = True
        # (2) Correo (no-reply) — se prepara aquí y se envía en segundo plano.
        if recipient.email:
            subject, html, text = build_notification_email(
                recipient.display_name, notif.type, notif.message or "Tienes una actualización en Deck.", card_title,
            )
            emails.append((recipient.email, subject, html, text))
    if changed:
        db.commit()
    # Enviar correos sin bloquear la respuesta del usuario.
    for to, subject, html, text in emails:
        asyncio.create_task(send_email(to, subject, html, text))


def _next_position(db: Session, model, **filters) -> int:
    q = db.query(func.max(model.position))
    for k, v in filters.items():
        q = q.filter(getattr(model, k) == v)
    current = q.scalar()
    return (current + 1) if current is not None else 0


# ============================================================
# BOOTSTRAP / BOARDS
# ============================================================

@router.get("/bootstrap")
async def bootstrap(
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)

    boards = _visible_boards(db, ctx)
    return {
        "session": {
            "userId": user.id,
            "ncUserId": user.nc_user_id,
            "displayName": user.display_name,
            "deckRole": ctx.role,
            "isAdmin": ctx.is_admin(),
            "teamIds": list(ctx.team_ids),
        },
        "boards": [_serialize_board(b) for b in boards],
    }


def _visible_boards(db: Session, ctx: DeckContext) -> List[DeckBoard]:
    q = db.query(DeckBoard).filter(DeckBoard.archived.is_(False))
    if not ctx.is_admin():
        ids = ctx.visible_board_ids or set()
        if not ids:
            return []
        q = q.filter(DeckBoard.id.in_(ids))
    return q.all()


@router.get("/boards")
async def list_boards(
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    return {"boards": [_serialize_board(b) for b in _visible_boards(db, ctx)]}


@router.get("/boards/{board_id}/members")
async def board_members(
    board_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """All active users in the company, so anyone can be assigned/followed.
    Members of the board's team (and shared teams) are flagged `sameTeam` and
    returned first so the picker shows the local team on top."""
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    board = _get_board_or_404(db, board_id)
    _require_see_board(ctx, board)

    users = db.query(User).filter(User.is_active.is_(True)).order_by(User.display_name).all()
    out = []
    for m in users:
        brief = _user_brief(m)
        # "Mismo equipo" = SOLO el equipo dueño del board (no los equipos con los
        # que se comparten cards; si no, quienes reciben una card compartida
        # aparecerían como del equipo del board).
        brief["sameTeam"] = (m.team_id == board.team_id)
        brief["teamId"] = m.team_id
        out.append(brief)
    # Same-team first, then alphabetical (already alpha from the query).
    out.sort(key=lambda b: (not b["sameTeam"],))
    return {"members": out}


@router.get("/boards/{board_id}")
async def get_board(
    board_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    board = _get_board_or_404(db, board_id)
    _require_see_board(ctx, board)
    out = _serialize_board(board, with_columns=True)
    tags = db.query(DeckTag).filter(DeckTag.board_id == board_id).order_by(DeckTag.name).all()
    out["tags"] = [_serialize_tag(t) for t in tags]
    return out


@router.post("/boards")
async def create_board(
    body: BoardIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    _require_owner_team(ctx, body.teamId)

    if not db.query(Team).filter(Team.id == body.teamId).first():
        raise HTTPException(status_code=404, detail="Team not found")
    if db.query(DeckBoard).filter(DeckBoard.team_id == body.teamId).first():
        raise HTTPException(status_code=409, detail="Team already has a board")

    board = DeckBoard(
        team_id=body.teamId, title=body.title, description=body.description,
        color=body.color, created_by=user.id, created_at=utc_now(), updated_at=utc_now(),
    )
    db.add(board)
    db.flush()
    for pos, (name, color, mins) in enumerate(DEFAULT_COLUMNS):
        db.add(DeckColumn(board_id=board.id, title=name, color=color, position=pos, is_default=True,
                          default_minutes=mins, created_at=utc_now(), updated_at=utc_now()))
    db.commit()
    db.refresh(board)
    return _serialize_board(board, with_columns=True)


@router.patch("/boards/{board_id}")
async def patch_board(
    board_id: int,
    body: BoardPatch,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    board = _get_board_or_404(db, board_id)
    _require_owner_team(ctx, board.team_id)

    if body.title is not None:
        board.title = body.title
    if body.description is not None:
        board.description = body.description
    if body.color is not None:
        board.color = body.color
    if body.archived is not None:
        board.archived = body.archived
    board.updated_at = utc_now()
    db.commit()
    db.refresh(board)
    return _serialize_board(board, with_columns=True)


# ============================================================
# COLUMNS
# ============================================================

@router.get("/boards/{board_id}/columns")
async def list_columns(
    board_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    board = _get_board_or_404(db, board_id)
    _require_see_board(ctx, board)
    cols = db.query(DeckColumn).filter(DeckColumn.board_id == board_id).order_by(DeckColumn.position).all()
    return {"columns": [_serialize_column(c) for c in cols]}


@router.post("/boards/{board_id}/columns")
async def create_column(
    board_id: int,
    body: ColumnIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    board = _get_board_or_404(db, board_id)
    _require_owner_team(ctx, board.team_id)

    col = DeckColumn(
        board_id=board_id, title=body.title, color=body.color, wip_limit=body.wipLimit,
        default_minutes=body.defaultMinutes or 0,
        position=_next_position(db, DeckColumn, board_id=board_id),
        created_at=utc_now(), updated_at=utc_now(),
    )
    db.add(col)
    db.commit()
    db.refresh(col)
    return _serialize_column(col)


@router.patch("/columns/{column_id}")
async def patch_column(
    column_id: int,
    body: ColumnPatch,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    col = db.query(DeckColumn).filter(DeckColumn.id == column_id).first()
    if not col:
        raise HTTPException(status_code=404, detail="Column not found")
    _require_owner_team(ctx, col.board.team_id)

    if body.title is not None:
        col.title = body.title
    if body.color is not None:
        col.color = body.color
    if body.wipLimit is not None:
        col.wip_limit = body.wipLimit
    if body.defaultMinutes is not None:
        col.default_minutes = max(0, body.defaultMinutes)
    col.updated_at = utc_now()
    db.commit()
    db.refresh(col)
    return _serialize_column(col)


@router.post("/columns/{column_id}/move")
async def move_column(
    column_id: int,
    body: ColumnMove,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    col = db.query(DeckColumn).filter(DeckColumn.id == column_id).first()
    if not col:
        raise HTTPException(status_code=404, detail="Column not found")
    _require_owner_team(ctx, col.board.team_id)

    siblings = db.query(DeckColumn).filter(
        and_(DeckColumn.board_id == col.board_id, DeckColumn.id != column_id)
    ).order_by(DeckColumn.position).all()
    target = max(0, min(body.position, len(siblings)))
    siblings.insert(target, col)
    for pos, c in enumerate(siblings):
        c.position = pos
        c.updated_at = utc_now()
    db.commit()
    return {"success": True}


@router.delete("/columns/{column_id}")
async def delete_column(
    column_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    col = db.query(DeckColumn).filter(DeckColumn.id == column_id).first()
    if not col:
        raise HTTPException(status_code=404, detail="Column not found")
    _require_owner_team(ctx, col.board.team_id)
    # Cards keep existing but lose their column (ondelete SET NULL).
    db.delete(col)
    db.commit()
    return {"success": True}


# ============================================================
# CARDS
# ============================================================

def _load_board_cards(db: Session, ctx: DeckContext, board: DeckBoard) -> List[DeckCard]:
    cards = db.query(DeckCard).filter(
        and_(DeckCard.board_id == board.id, DeckCard.archived.is_(False))
    ).order_by(DeckCard.position).all()
    return cards


@router.get("/boards/{board_id}/cards")
async def list_cards(
    board_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    board = _get_board_or_404(db, board_id)
    _require_see_board(ctx, board)
    cards = _load_board_cards(db, ctx, board)
    roll = _subtask_rollup(db, [c.id for c in cards])
    dicts = [_serialize_card(c, sub=roll.get(c.id)) for c in cards]
    _augment_user_flags(db, user, dicts)
    return {"cards": dicts}


@router.post("/boards/{board_id}/list-order")
async def set_list_order(
    board_id: int,
    body: ListOrderIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Guarda el orden manual de la lista 'En curso': list_order = índice."""
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    board = _get_board_or_404(db, board_id)
    _require_owner_team(ctx, board.team_id)
    pos = {cid: i for i, cid in enumerate(body.orderedIds)}
    rows = db.query(DeckCard).filter(
        and_(DeckCard.id.in_(body.orderedIds or [0]), DeckCard.board_id == board_id)
    ).all()
    for c in rows:
        c.list_order = pos.get(c.id)
    db.commit()
    return {"success": True}


@router.get("/cards/{card_id}")
async def get_card(
    card_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_see_card_or_parent(db, ctx, card)
    roll = _subtask_rollup(db, [card.id]).get(card.id)
    out = _serialize_card(card, full=True, sub=roll)
    # Abrir la card cuenta como "visto": marca sus notificaciones como leídas
    # (limpia el indicador de la tarea y baja el contador de la campana).
    db.query(DeckNotification).filter(and_(
        DeckNotification.user_id == user.id,
        DeckNotification.card_id == card.id,
        DeckNotification.is_read.is_(False),
    )).update({DeckNotification.is_read: True, DeckNotification.read_at: utc_now()}, synchronize_session=False)
    db.commit()
    out["isFavorite"] = db.query(DeckCardFavorite).filter_by(user_id=user.id, card_id=card.id).first() is not None
    out["hasUnread"] = False
    return out


@router.post("/cards/{card_id}/favorite")
async def add_favorite(
    card_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_see_card_or_parent(db, ctx, card)
    if not db.query(DeckCardFavorite).filter_by(user_id=user.id, card_id=card.id).first():
        db.add(DeckCardFavorite(user_id=user.id, card_id=card.id, created_at=utc_now()))
        db.commit()
    return {"success": True, "isFavorite": True}


@router.delete("/cards/{card_id}/favorite")
async def remove_favorite(
    card_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    db.query(DeckCardFavorite).filter_by(user_id=user.id, card_id=card_id).delete()
    db.commit()
    return {"success": True, "isFavorite": False}


@router.get("/notifications/unread-cards")
async def unread_cards(
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Ids de las cards con notificaciones SIN LEER para el usuario actual.
    Se sondea desde la lista para mantener vivo el indicador naranja de novedad
    sin recargar todo el tablero."""
    user = await _get_current_user(authorization, db)
    rows = db.query(DeckNotification.card_id).filter(and_(
        DeckNotification.user_id == user.id,
        DeckNotification.is_read.is_(False),
        DeckNotification.card_id.isnot(None),
    )).distinct().all()
    return {"cardIds": [r[0] for r in rows]}


# ============================================================
# SUBTASKS (subtareas: cards hijas que pueden vivir en otro board)
# ============================================================

def _serialize_subtask(card: DeckCard) -> Dict[str, Any]:
    out = _serialize_card(card)
    out["columnTitle"] = card.column.title if card.column else None
    out["columnColor"] = card.column.color if card.column else None
    out["boardTitle"] = card.board.title if card.board else None
    out["teamName"] = card.owner_team.name if card.owner_team else None
    return out


@router.get("/cards/{card_id}/subtasks")
async def list_subtasks(
    card_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    parent = _get_card_or_404(db, card_id)
    _require_see_card_or_parent(db, ctx, parent)
    rows = db.query(DeckCard).filter(
        and_(DeckCard.parent_card_id == card_id, DeckCard.archived.is_(False))
    ).order_by(DeckCard.created_at).all()
    return {"subtasks": [_serialize_subtask(c) for c in rows]}


def _resolve_subtask_board(db: Session, ctx: DeckContext, user: User, parent: DeckCard, board_id: Optional[int]) -> DeckBoard:
    """Board destino de una subtarea: el indicado, si no el del equipo del
    usuario (adopta su flujo), y si no, el del padre."""
    board = None
    if board_id:
        board = _get_board_or_404(db, board_id)
    elif user.team_id:
        board = db.query(DeckBoard).filter(DeckBoard.team_id == user.team_id).first()
    if not board:
        board = db.query(DeckBoard).filter(DeckBoard.id == parent.board_id).first()
    if not board:
        raise HTTPException(status_code=404, detail="No hay board destino")
    if not ctx.is_owner_team(board.team_id):
        raise HTTPException(status_code=403, detail="No puedes crear subtareas en ese board")
    return board


@router.get("/cards/{card_id}/subtask-context")
async def subtask_context(
    card_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Board destino (columnas + miembros) para el wizard de nueva subtarea."""
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    parent = _get_card_or_404(db, card_id)
    _require_see_card_or_parent(db, ctx, parent)
    board = _resolve_subtask_board(db, ctx, user, parent, None)
    cols = db.query(DeckColumn).filter(DeckColumn.board_id == board.id).order_by(DeckColumn.position).all()
    users = db.query(User).filter(User.is_active.is_(True)).order_by(User.display_name).all()
    members = []
    for m in users:
        b = _user_brief(m); b["sameTeam"] = (m.team_id == board.team_id); b["teamId"] = m.team_id
        members.append(b)
    members.sort(key=lambda b: (not b["sameTeam"],))
    return {
        "boardId": board.id, "boardTitle": board.title, "teamName": board.team.name if board.team else None,
        "columns": [_serialize_column(c) for c in cols], "members": members,
    }


@router.post("/cards/{card_id}/subtasks")
async def create_subtask(
    card_id: int,
    body: SubtaskIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Crea una subtarea de una card (con los campos completos del wizard)."""
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    parent = _get_card_or_404(db, card_id)
    _require_see_card_or_parent(db, ctx, parent)
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="Título requerido")

    board = _resolve_subtask_board(db, ctx, user, parent, body.boardId)

    # Columna: la indicada si pertenece al board, si no la primera.
    col = None
    if body.columnId:
        col = db.query(DeckColumn).filter(and_(DeckColumn.id == body.columnId, DeckColumn.board_id == board.id)).first()
    if not col:
        col = db.query(DeckColumn).filter(DeckColumn.board_id == board.id).order_by(DeckColumn.position).first()

    card = DeckCard(
        board_id=board.id, column_id=col.id if col else None,
        owner_team_id=board.team_id, parent_card_id=parent.id,
        title=body.title.strip(), description=body.description, priority=body.priority,
        start_date=_parse_dt(body.startDate), due_date=_parse_dt(body.dueDate),
        position=_next_position(db, DeckCard, column_id=col.id) if col else 0,
        created_by=user.id, created_at=utc_now(), updated_at=utc_now(),
    )
    db.add(card)
    db.flush()
    db.add(DeckCardTeam(card_id=card.id, team_id=board.team_id, is_owner=True, shared_by=user.id, created_at=utc_now()))
    for uid in dict.fromkeys(body.assigneeIds):
        db.add(DeckCardAssignee(card_id=card.id, user_id=uid, assigned_by=user.id, created_at=utc_now()))
    for uid in dict.fromkeys([user.id, *body.assigneeIds]):
        db.add(DeckCardFollower(card_id=card.id, user_id=uid, created_at=utc_now()))
    db.flush()
    db.refresh(card)
    _log_activity(db, card, user, "created", message=f"{user.display_name} creó esta subtarea", notify=False)
    db.commit()
    db.refresh(card)
    return _serialize_subtask(card)


@router.post("/boards/{board_id}/cards")
async def create_card(
    board_id: int,
    body: CardIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    board = _get_board_or_404(db, board_id)
    _require_see_board(ctx, board)
    if not ctx.is_owner_team(board.team_id):
        raise HTTPException(status_code=403, detail="Not allowed to add cards to this board")

    # Idempotency: a retried POST returns the original card.
    if body.clientOpId:
        existing = db.query(DeckCard).filter(DeckCard.client_op_id == body.clientOpId).first()
        if existing:
            return _serialize_card(existing, full=True)

    # Default column: first column on the board if none provided.
    column_id = body.columnId
    if column_id is None:
        first_col = db.query(DeckColumn).filter(DeckColumn.board_id == board_id)\
            .order_by(DeckColumn.position).first()
        column_id = first_col.id if first_col else None

    card = DeckCard(
        board_id=board_id, column_id=column_id, owner_team_id=board.team_id,
        project_id=body.projectId, title=body.title, description=body.description,
        priority=body.priority, start_date=_parse_dt(body.startDate),
        due_date=_parse_dt(body.dueDate),
        position=_next_position(db, DeckCard, column_id=column_id) if column_id else 0,
        created_by=user.id, client_op_id=body.clientOpId,
        created_at=utc_now(), updated_at=utc_now(),
    )
    db.add(card)
    db.flush()

    # Owner team M2M (denormalized owner_team_id already set).
    db.add(DeckCardTeam(card_id=card.id, team_id=board.team_id, is_owner=True,
                        shared_by=user.id, created_at=utc_now()))
    # Assignees
    for uid in dict.fromkeys(body.assigneeIds):
        db.add(DeckCardAssignee(card_id=card.id, user_id=uid, assigned_by=user.id, created_at=utc_now()))
    # Tags
    for tid in dict.fromkeys(body.tagIds):
        if db.query(DeckTag).filter(and_(DeckTag.id == tid, DeckTag.board_id == board_id)).first():
            db.add(DeckCardTag(card_id=card.id, tag_id=tid, created_at=utc_now()))
    # Default followers = creator + assignees (all get notified of changes).
    for uid in dict.fromkeys([user.id, *body.assigneeIds]):
        db.add(DeckCardFollower(card_id=card.id, user_id=uid, created_at=utc_now()))

    db.flush()
    db.refresh(card)
    _log_activity(db, card, user, "created", message=f"{user.display_name} created this card", notify=False)
    db.commit()
    db.refresh(card)
    return _serialize_card(card, full=True)


@router.patch("/cards/{card_id}")
async def patch_card(
    card_id: int,
    body: CardPatch,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_write_card(ctx, card)

    if body.title is not None:
        card.title = body.title
    if body.description is not None:
        card.description = body.description
    if body.projectId is not None:
        card.project_id = body.projectId
    if body.priority is not None:
        card.priority = body.priority
    if body.prototypeUrl is not None:
        card.prototype_url = body.prototypeUrl or None
    if body.startDate is not None:
        card.start_date = _parse_dt(body.startDate)
    if body.dueDate is not None:
        old_due = card.due_date
        card.due_date = _parse_dt(body.dueDate)
        if old_due != card.due_date:
            _log_activity(db, card, user, "due_changed",
                          payload={"to": card.due_date.isoformat() if card.due_date else None},
                          message=f"{user.display_name} changed the due date")
    card.updated_at = utc_now()
    _log_activity(db, card, user, "updated", message=f"{user.display_name} updated this card")
    db.commit()
    db.refresh(card)
    return _serialize_card(card, full=True)


@router.post("/cards/{card_id}/move")
async def move_card(
    card_id: int,
    body: CardMove,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_write_card(ctx, card)

    target_col = db.query(DeckColumn).filter(DeckColumn.id == body.columnId).first()
    if not target_col or target_col.board_id != card.board_id:
        raise HTTPException(status_code=400, detail="Target column not on this board")

    from_col = card.column_id
    # Re-sequence target column with the card inserted at the requested index.
    siblings = db.query(DeckCard).filter(
        and_(DeckCard.column_id == body.columnId, DeckCard.id != card_id,
             DeckCard.archived.is_(False))
    ).order_by(DeckCard.position).all()
    target = max(0, min(body.position, len(siblings)))
    siblings.insert(target, card)
    card.column_id = body.columnId
    for pos, c in enumerate(siblings):
        c.position = pos
        c.updated_at = utc_now()

    if from_col != body.columnId:
        _log_activity(db, card, user, "moved",
                      payload={"from": from_col, "to": body.columnId},
                      message=f"{user.display_name} moved this card to {target_col.title}")
    db.commit()
    return {"success": True}


@router.post("/cards/{card_id}/complete")
async def complete_card(
    card_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_write_card(ctx, card)
    card.completed_at = utc_now()
    card.updated_at = utc_now()
    _log_activity(db, card, user, "completed", message=f"{user.display_name} completed this card")
    # Si es una subtarea, avisar a los followers del padre.
    parent_act = None
    if card.parent_card_id:
        parent = db.query(DeckCard).filter(DeckCard.id == card.parent_card_id).first()
        if parent:
            parent_act = _log_activity(
                db, parent, user, "updated",
                message=f"Subtarea completada: “{card.title}” ({card.owner_team.name if card.owner_team else 'equipo'})",
            )
    db.commit()
    if parent_act is not None:
        await _dispatch_external(db, authorization, parent_act.id)
    db.refresh(card)
    roll = _subtask_rollup(db, [card.id]).get(card.id)
    return _serialize_card(card, full=True, sub=roll)


@router.post("/cards/{card_id}/reopen")
async def reopen_card(
    card_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_write_card(ctx, card)
    card.completed_at = None
    card.updated_at = utc_now()
    _log_activity(db, card, user, "reopened", message=f"{user.display_name} reopened this card", notify=False)
    db.commit()
    db.refresh(card)
    return _serialize_card(card, full=True)


@router.post("/cards/{card_id}/archive")
async def archive_card(
    card_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_write_card(ctx, card)
    card.archived = True
    card.updated_at = utc_now()
    _log_activity(db, card, user, "archived", message=f"{user.display_name} archived this card", notify=False)
    db.commit()
    return {"success": True}


@router.post("/cards/{card_id}/restore")
async def restore_card(
    card_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_write_card(ctx, card)
    card.archived = False
    card.updated_at = utc_now()
    _log_activity(db, card, user, "restored", message=f"{user.display_name} restored this card", notify=False)
    db.commit()
    db.refresh(card)
    return _serialize_card(card, full=True)


@router.delete("/cards/{card_id}")
async def delete_card(
    card_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_owner_team(ctx, card.owner_team_id)
    db.delete(card)
    db.commit()
    return {"success": True}


# ============================================================
# ASSIGNEES
# ============================================================

@router.post("/cards/{card_id}/assignees")
async def add_assignee(
    card_id: int,
    body: AssigneeIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_write_card(ctx, card)

    target = db.query(User).filter(User.id == body.userId).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    exists = db.query(DeckCardAssignee).filter(
        and_(DeckCardAssignee.card_id == card_id, DeckCardAssignee.user_id == body.userId)
    ).first()
    act = None
    if not exists:
        db.add(DeckCardAssignee(card_id=card_id, user_id=body.userId,
                                assigned_by=user.id, created_at=utc_now()))
        # An assignee automatically follows the card.
        if not db.query(DeckCardFollower).filter(
            and_(DeckCardFollower.card_id == card_id, DeckCardFollower.user_id == body.userId)
        ).first():
            db.add(DeckCardFollower(card_id=card_id, user_id=body.userId, created_at=utc_now()))
        db.flush()
        db.refresh(card)
        act = _log_activity(db, card, user, "assigned",
                            payload={"targetUserId": body.userId},
                            message=f"{user.display_name} assigned {target.display_name}",
                            extra_recipients={body.userId})
    db.commit()
    if act is not None:
        await _dispatch_external(db, authorization, act.id)
    db.refresh(card)
    return _serialize_card(card, full=True)


@router.delete("/cards/{card_id}/assignees/{user_id}")
async def remove_assignee(
    card_id: int,
    user_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_write_card(ctx, card)
    row = db.query(DeckCardAssignee).filter(
        and_(DeckCardAssignee.card_id == card_id, DeckCardAssignee.user_id == user_id)
    ).first()
    if row:
        db.delete(row)
        db.flush()
        db.refresh(card)
        _log_activity(db, card, user, "unassigned", payload={"targetUserId": user_id},
                      message=f"{user.display_name} unassigned a member", notify=False)
    db.commit()
    db.refresh(card)
    return _serialize_card(card, full=True)


# ============================================================
# TAGS
# ============================================================

@router.get("/boards/{board_id}/tags")
async def list_tags(
    board_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    board = _get_board_or_404(db, board_id)
    _require_see_board(ctx, board)
    tags = db.query(DeckTag).filter(DeckTag.board_id == board_id).order_by(DeckTag.name).all()
    return {"tags": [_serialize_tag(t) for t in tags]}


@router.post("/boards/{board_id}/tags")
async def create_tag(
    board_id: int,
    body: TagIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    board = _get_board_or_404(db, board_id)
    _require_owner_team(ctx, board.team_id)
    existing = db.query(DeckTag).filter(
        and_(DeckTag.board_id == board_id, DeckTag.name == body.name)
    ).first()
    if existing:
        return _serialize_tag(existing)
    tag = DeckTag(board_id=board_id, name=body.name, color=body.color, created_at=utc_now())
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return _serialize_tag(tag)


@router.post("/cards/{card_id}/tags")
async def attach_tag(
    card_id: int,
    body: TagAttach,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_write_card(ctx, card)

    tag = None
    if body.tagId:
        tag = db.query(DeckTag).filter(
            and_(DeckTag.id == body.tagId, DeckTag.board_id == card.board_id)
        ).first()
    elif body.name:
        tag = db.query(DeckTag).filter(
            and_(DeckTag.board_id == card.board_id, DeckTag.name == body.name)
        ).first()
        if not tag:
            tag = DeckTag(board_id=card.board_id, name=body.name, color=body.color, created_at=utc_now())
            db.add(tag)
            db.flush()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")

    exists = db.query(DeckCardTag).filter(
        and_(DeckCardTag.card_id == card_id, DeckCardTag.tag_id == tag.id)
    ).first()
    if not exists:
        db.add(DeckCardTag(card_id=card_id, tag_id=tag.id, created_at=utc_now()))
        db.flush()
        db.refresh(card)
        _log_activity(db, card, user, "tagged", payload={"tag": tag.name},
                      message=f"{user.display_name} added tag “{tag.name}”", notify=False)
    db.commit()
    db.refresh(card)
    return _serialize_card(card, full=True)


@router.delete("/cards/{card_id}/tags/{tag_id}")
async def detach_tag(
    card_id: int,
    tag_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_write_card(ctx, card)
    row = db.query(DeckCardTag).filter(
        and_(DeckCardTag.card_id == card_id, DeckCardTag.tag_id == tag_id)
    ).first()
    if row:
        db.delete(row)
        db.flush()
        db.refresh(card)
        _log_activity(db, card, user, "untagged", payload={"tagId": tag_id},
                      message=f"{user.display_name} removed a tag", notify=False)
    db.commit()
    db.refresh(card)
    return _serialize_card(card, full=True)


# ============================================================
# COMMENTS
# ============================================================

@router.get("/cards/{card_id}/comments")
async def list_comments(
    card_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_see_card(ctx, card)
    rows = db.query(DeckComment).filter(DeckComment.card_id == card_id)\
        .order_by(DeckComment.created_at).all()
    # Batch-load attachments for these comments.
    atts = db.query(DeckAttachment).filter(DeckAttachment.card_id == card_id).all()
    by_comment: Dict[int, List[DeckAttachment]] = {}
    for a in atts:
        if a.comment_id:
            by_comment.setdefault(a.comment_id, []).append(a)
    return {"comments": [_serialize_comment(c, by_comment.get(c.id)) for c in rows]}


@router.post("/cards/{card_id}/comments")
async def add_comment(
    card_id: int,
    body: CommentIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_write_card(ctx, card)
    # A comment must have text or at least one attachment.
    if not body.body.strip() and not body.attachmentIds:
        raise HTTPException(status_code=400, detail="Empty comment")

    comment = DeckComment(
        card_id=card_id, user_id=user.id, parent_id=body.parentId,
        body=body.body, mentions=body.mentions or None, created_at=utc_now(),
    )
    db.add(comment)
    db.flush()

    # Link any pre-uploaded attachments belonging to this card.
    linked = []
    if body.attachmentIds:
        linked = db.query(DeckAttachment).filter(
            and_(DeckAttachment.id.in_(body.attachmentIds), DeckAttachment.card_id == card_id)
        ).all()
        for a in linked:
            a.comment_id = comment.id

    # Users notified via a comment also start following the card.
    for uid in set(body.mentions or []):
        exists = db.query(DeckCardFollower).filter(
            and_(DeckCardFollower.card_id == card_id, DeckCardFollower.user_id == uid)
        ).first()
        if not exists:
            db.add(DeckCardFollower(card_id=card_id, user_id=uid, created_at=utc_now()))

    db.refresh(card)
    act = _log_activity(db, card, user, "commented",
                        payload={"commentId": comment.id},
                        message=f"{user.display_name} commented",
                        extra_recipients=set(body.mentions or []))
    db.commit()
    await _dispatch_external(db, authorization, act.id)
    db.refresh(comment)
    return _serialize_comment(comment, linked)


@router.patch("/comments/{comment_id}")
async def edit_comment(
    comment_id: int,
    body: CommentPatch,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    comment = db.query(DeckComment).filter(DeckComment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if comment.user_id != user.id and not ctx.is_admin():
        raise HTTPException(status_code=403, detail="Can only edit your own comment")
    if not ctx.is_admin() and not _within_edit_window(comment.created_at):
        raise HTTPException(status_code=403, detail="El comentario solo se puede editar los primeros 5 minutos")
    comment.body = body.body
    comment.edited_at = utc_now()
    db.commit()
    db.refresh(comment)
    return _serialize_comment(comment)


@router.delete("/comments/{comment_id}")
async def delete_comment(
    comment_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    comment = db.query(DeckComment).filter(DeckComment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if comment.user_id != user.id and not ctx.is_admin():
        raise HTTPException(status_code=403, detail="Can only delete your own comment")
    if not ctx.is_admin() and not _within_edit_window(comment.created_at):
        raise HTTPException(status_code=403, detail="El comentario solo se puede eliminar los primeros 5 minutos")
    comment.deleted_at = utc_now()
    db.commit()
    return {"success": True}


# ============================================================
# ATTACHMENTS
# ============================================================

@router.post("/cards/{card_id}/attachments")
async def upload_attachment(
    card_id: int,
    authorization: Annotated[str, Header()],
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload a file to a card. Returned id can be passed as attachmentIds when
    creating a comment. Binary stored in-DB (LONGBLOB), capped at 15 MB."""
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_write_card(ctx, card)

    data = await file.read()
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 15 MB)")
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    att = DeckAttachment(
        card_id=card_id, uploaded_by=user.id,
        filename=(file.filename or "archivo")[:255],
        content_type=file.content_type, size=len(data), data=data, created_at=utc_now(),
    )
    db.add(att)
    db.commit()
    db.refresh(att)
    return _serialize_attachment(att)


@router.get("/attachments/{attachment_id}")
async def download_attachment(
    attachment_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    att = db.query(DeckAttachment).filter(DeckAttachment.id == attachment_id).first()
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")
    card = _get_card_or_404(db, att.card_id)
    _require_see_card(ctx, card)
    return Response(
        content=att.data,
        media_type=att.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{att.filename}"'},
    )


# ============================================================
# TIME LOGS
# ============================================================

def _serialize_timelog(t: DeckTimeLog) -> Dict[str, Any]:
    return {
        "id": t.id,
        "minutes": t.minutes,
        "description": t.description or "",
        "date": t.log_date.isoformat() if t.log_date else None,
        "billable": bool(t.billable),
        "user": _user_brief(t.user),
        "createdAt": t.created_at.isoformat() if t.created_at else None,
    }


@router.get("/cards/{card_id}/timelogs")
async def list_timelogs(
    card_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_see_card(ctx, card)
    rows = db.query(DeckTimeLog).filter(DeckTimeLog.card_id == card_id)\
        .order_by(DeckTimeLog.log_date.desc(), DeckTimeLog.created_at.desc()).all()
    total = sum(t.minutes for t in rows)
    return {"timelogs": [_serialize_timelog(t) for t in rows], "totalMinutes": total}


@router.post("/cards/{card_id}/timelogs")
async def add_timelog(
    card_id: int,
    body: TimeLogIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_write_card(ctx, card)
    if body.minutes <= 0:
        raise HTTPException(status_code=400, detail="El tiempo debe ser mayor a 0")
    tl = DeckTimeLog(
        card_id=card_id, user_id=user.id, minutes=body.minutes,
        description=body.description, log_date=_parse_date(body.date) or date.today(),
        billable=body.billable, created_at=utc_now(),
    )
    db.add(tl)
    db.commit()
    db.refresh(tl)
    return _serialize_timelog(tl)


@router.delete("/timelogs/{timelog_id}")
async def delete_timelog(
    timelog_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    tl = db.query(DeckTimeLog).filter(DeckTimeLog.id == timelog_id).first()
    if not tl:
        raise HTTPException(status_code=404, detail="Time log not found")
    if tl.user_id != user.id and not ctx.is_admin():
        raise HTTPException(status_code=403, detail="Solo puedes borrar tu propio registro")
    db.delete(tl)
    db.commit()
    return {"success": True}


# ============================================================
# STAGE NOTES (comentarios internos por etapa)
# ============================================================

def _serialize_stage_note(n: DeckStageNote) -> Dict[str, Any]:
    return {
        "id": n.id,
        "columnId": n.column_id,
        "author": _user_brief(n.user),
        "body": n.body,
        "createdAt": n.created_at.isoformat() if n.created_at else None,
    }


@router.get("/cards/{card_id}/stage-notes")
async def list_stage_notes(
    card_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_see_card(ctx, card)
    rows = db.query(DeckStageNote).filter(DeckStageNote.card_id == card_id)\
        .order_by(DeckStageNote.created_at).all()
    return {"notes": [_serialize_stage_note(n) for n in rows]}


@router.post("/cards/{card_id}/stages/{column_id}/notes")
async def add_stage_note(
    card_id: int,
    column_id: int,
    body: StageNoteIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_write_card(ctx, card)
    if not body.body.strip():
        raise HTTPException(status_code=400, detail="Nota vacía")
    if not db.query(DeckColumn).filter(and_(DeckColumn.id == column_id, DeckColumn.board_id == card.board_id)).first():
        raise HTTPException(status_code=400, detail="Etapa no pertenece al board")
    note = DeckStageNote(card_id=card_id, column_id=column_id, user_id=user.id, body=body.body, created_at=utc_now())
    db.add(note)
    db.commit()
    db.refresh(note)
    return _serialize_stage_note(note)


@router.delete("/stage-notes/{note_id}")
async def delete_stage_note(
    note_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    note = db.query(DeckStageNote).filter(DeckStageNote.id == note_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    if note.user_id != user.id and not ctx.is_admin():
        raise HTTPException(status_code=403, detail="Solo puedes borrar tu propia nota")
    db.delete(note)
    db.commit()
    return {"success": True}


# ============================================================
# ACTIVITY
# ============================================================

@router.get("/cards/{card_id}/activity")
async def card_activity(
    card_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_see_card(ctx, card)
    rows = db.query(DeckActivity).filter(DeckActivity.card_id == card_id)\
        .order_by(DeckActivity.created_at).all()
    return {"activity": [_serialize_activity(a) for a in rows]}


@router.delete("/activity/{activity_id}")
async def delete_activity(
    activity_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Elimina un registro del historial/flujo (afecta diagrama e historial).
    Solo admins, para curar datos de prueba o errores."""
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    if not ctx.is_admin():
        raise HTTPException(status_code=403, detail="Solo un admin puede borrar registros del historial")
    act = db.query(DeckActivity).filter(DeckActivity.id == activity_id).first()
    if not act:
        raise HTTPException(status_code=404, detail="Registro no encontrado")
    card_id = act.card_id
    was_move = act.event_type == "moved"
    db.query(DeckNotification).filter(DeckNotification.activity_id == activity_id)\
        .update({DeckNotification.activity_id: None}, synchronize_session=False)
    db.delete(act)
    db.flush()

    # Si se borró un movimiento, recomputar la etapa actual con el último 'moved'
    # que quede (para que la card vuelva al estado previo, no solo el diagrama).
    if was_move:
        card = db.query(DeckCard).filter(DeckCard.id == card_id).first()
        if card:
            last = db.query(DeckActivity).filter(
                and_(DeckActivity.card_id == card_id, DeckActivity.event_type == "moved")
            ).order_by(DeckActivity.created_at.desc()).first()
            cols = db.query(DeckColumn).filter(DeckColumn.board_id == card.board_id)\
                .order_by(DeckColumn.position).all()
            new_col = None
            if last and isinstance(last.payload, dict) and last.payload.get("to"):
                new_col = last.payload["to"]
            elif cols:
                new_col = cols[0].id
            if new_col and any(c.id == new_col for c in cols):
                card.column_id = new_col
                # Si el nuevo estado no es la etapa final, la card deja de estar completada.
                if cols and new_col != cols[-1].id:
                    card.completed_at = None
                card.updated_at = utc_now()

    db.commit()
    return {"success": True}


# ============================================================
# FOLLOWERS
# ============================================================

@router.post("/cards/{card_id}/followers")
async def add_follower(
    card_id: int,
    body: AssigneeIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_see_card(ctx, card)
    # A user can add themselves; adding others requires write access.
    if body.userId != user.id:
        _require_write_card(ctx, card)
    exists = db.query(DeckCardFollower).filter(
        and_(DeckCardFollower.card_id == card_id, DeckCardFollower.user_id == body.userId)
    ).first()
    if not exists:
        db.add(DeckCardFollower(card_id=card_id, user_id=body.userId, created_at=utc_now()))
    db.commit()
    db.refresh(card)
    return _serialize_card(card, full=True)


@router.delete("/cards/{card_id}/followers/{user_id}")
async def remove_follower(
    card_id: int,
    user_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_see_card(ctx, card)
    if user_id != user.id:
        _require_write_card(ctx, card)
    row = db.query(DeckCardFollower).filter(
        and_(DeckCardFollower.card_id == card_id, DeckCardFollower.user_id == user_id)
    ).first()
    if row:
        db.delete(row)
    db.commit()
    db.refresh(card)
    return _serialize_card(card, full=True)


# ============================================================
# NOTIFICATIONS
# ============================================================

def _ensure_due_soon_notifications(db: Session, user: User) -> None:
    """Lazily create 'due_soon' notifications for the user's assigned/followed
    cards whose due_date falls within the next 48h and aren't completed. Dedups
    against an existing unread due_soon for the same card+user."""
    horizon = utc_now() + timedelta(hours=48)
    card_ids = set()
    card_ids |= {r[0] for r in db.query(DeckCardAssignee.card_id)
                 .filter(DeckCardAssignee.user_id == user.id).all()}
    card_ids |= {r[0] for r in db.query(DeckCardFollower.card_id)
                 .filter(DeckCardFollower.user_id == user.id).all()}
    if not card_ids:
        return
    cards = db.query(DeckCard).filter(
        and_(
            DeckCard.id.in_(card_ids),
            DeckCard.completed_at.is_(None),
            DeckCard.archived.is_(False),
            DeckCard.due_date.isnot(None),
            DeckCard.due_date <= horizon,
            DeckCard.due_date >= utc_now() - timedelta(hours=12),
        )
    ).all()
    changed = False
    for card in cards:
        # Dedup against ANY existing due_soon for this card+user (read or unread)
        # so a dismissed reminder is not regenerated on every poll.
        dup = db.query(DeckNotification).filter(
            and_(
                DeckNotification.user_id == user.id,
                DeckNotification.card_id == card.id,
                DeckNotification.type == "due_soon",
            )
        ).first()
        if dup:
            continue
        db.add(DeckNotification(
            user_id=user.id, actor_id=None, card_id=card.id,
            type="due_soon", message=f"“{card.title}” vence pronto",
            is_read=False, created_at=utc_now(),
        ))
        changed = True
    if changed:
        db.commit()


@router.get("/notifications")
async def list_notifications(
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
    unread: bool = False,
    limit: int = 50,
):
    user = await _get_current_user(authorization, db)
    _ensure_due_soon_notifications(db, user)

    q = db.query(DeckNotification).filter(DeckNotification.user_id == user.id)
    if unread:
        q = q.filter(DeckNotification.is_read.is_(False))
    rows = q.order_by(DeckNotification.created_at.desc()).limit(min(limit, 100)).all()

    title_by_card = {}
    card_ids = {n.card_id for n in rows if n.card_id}
    if card_ids:
        for cid, title in db.query(DeckCard.id, DeckCard.title).filter(DeckCard.id.in_(card_ids)).all():
            title_by_card[cid] = title

    unread_count = db.query(func.count(DeckNotification.id)).filter(
        and_(DeckNotification.user_id == user.id, DeckNotification.is_read.is_(False))
    ).scalar() or 0

    return {
        "notifications": [_serialize_notification(n, title_by_card.get(n.card_id)) for n in rows],
        "unreadCount": unread_count,
    }


@router.get("/notifications/unread-count")
async def unread_count(
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    _ensure_due_soon_notifications(db, user)
    count = db.query(func.count(DeckNotification.id)).filter(
        and_(DeckNotification.user_id == user.id, DeckNotification.is_read.is_(False))
    ).scalar() or 0
    return {"count": count}


def _own_notification(db: Session, user: User, notif_id: int) -> DeckNotification:
    n = db.query(DeckNotification).filter(
        and_(DeckNotification.id == notif_id, DeckNotification.user_id == user.id)
    ).first()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    return n


@router.post("/notifications/{notif_id}/read")
async def mark_read(
    notif_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    n = _own_notification(db, user, notif_id)
    n.is_read = True
    n.read_at = utc_now()
    db.commit()
    return {"success": True}


@router.post("/notifications/{notif_id}/unread")
async def mark_unread(
    notif_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    n = _own_notification(db, user, notif_id)
    n.is_read = False
    n.read_at = None
    db.commit()
    return {"success": True}


@router.post("/notifications/read-all")
async def mark_all_read(
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    db.query(DeckNotification).filter(
        and_(DeckNotification.user_id == user.id, DeckNotification.is_read.is_(False))
    ).update({DeckNotification.is_read: True, DeckNotification.read_at: utc_now()})
    db.commit()
    return {"success": True}


# ============================================================
# TIMELINE  (derived from card start/due dates — no new tables)
# ============================================================

@router.get("/boards/{board_id}/timeline")
async def board_timeline(
    board_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    board = _get_board_or_404(db, board_id)
    _require_see_board(ctx, board)

    cards = db.query(DeckCard).filter(
        and_(DeckCard.board_id == board_id, DeckCard.archived.is_(False))
    ).order_by(DeckCard.due_date).all()

    out = []
    for c in cards:
        if not c.start_date and not c.due_date:
            continue
        out.append({
            "id": c.id,
            "title": c.title,
            "start": c.start_date.isoformat() if c.start_date else None,
            "end": c.due_date.isoformat() if c.due_date else None,
            "completedAt": c.completed_at.isoformat() if c.completed_at else None,
            "priority": c.priority,
            "columnId": c.column_id,
            "assignees": [_user_brief(a.user) for a in c.assignees],
        })
    return {"cards": out}


# ============================================================
# TEAMS + CROSS-TEAM SHARING (Phase 3)
# ============================================================

class ShareIn(BaseModel):
    teamId: int


@router.get("/teams")
async def list_teams(
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """All teams — used as targets when sharing a card across teams."""
    await _get_current_user(authorization, db)
    teams = db.query(Team).order_by(Team.name).all()
    return {"teams": [{"id": t.id, "name": t.name} for t in teams]}


@router.get("/boards/{board_id}/shared-cards")
async def board_shared_cards(
    board_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Cards from OTHER boards shared with this board's owner team."""
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    board = _get_board_or_404(db, board_id)
    _require_see_board(ctx, board)

    cards = db.query(DeckCard).join(
        DeckCardTeam, DeckCardTeam.card_id == DeckCard.id
    ).filter(
        and_(
            DeckCardTeam.team_id == board.team_id,
            DeckCardTeam.is_owner.is_(False),
            DeckCard.archived.is_(False),
        )
    ).order_by(DeckCard.updated_at.desc()).all()
    dicts = [_serialize_card(c, full=True) for c in cards]
    _augment_user_flags(db, user, dicts)
    return {"cards": dicts}


@router.post("/cards/{card_id}/share")
async def share_card(
    card_id: int,
    body: ShareIn,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    """Share a card with another team (owner-team member or admin)."""
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_owner_team(ctx, card.owner_team_id)

    team = db.query(Team).filter(Team.id == body.teamId).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if body.teamId == card.owner_team_id:
        raise HTTPException(status_code=400, detail="Card already owned by this team")

    exists = db.query(DeckCardTeam).filter(
        and_(DeckCardTeam.card_id == card_id, DeckCardTeam.team_id == body.teamId)
    ).first()
    act = None
    if not exists:
        db.add(DeckCardTeam(card_id=card_id, team_id=body.teamId, is_owner=False,
                            shared_by=user.id, created_at=utc_now()))
        db.flush()
        db.refresh(card)
        # Notify the target team's members.
        member_ids = {
            r[0] for r in db.query(User.id).filter(
                and_(User.team_id == body.teamId, User.is_active.is_(True))
            ).all()
        }
        act = _log_activity(db, card, user, "shared_team",
                            payload={"teamId": body.teamId, "teamName": team.name},
                            message=f"{user.display_name} compartió esta tarjeta con {team.name}",
                            extra_recipients=member_ids)
    db.commit()
    if act is not None:
        await _dispatch_external(db, authorization, act.id)
    db.refresh(card)
    return _serialize_card(card, full=True)


@router.delete("/cards/{card_id}/share/{team_id}")
async def unshare_card(
    card_id: int,
    team_id: int,
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    user = await _get_current_user(authorization, db)
    ctx = _build_deck_context(db, user)
    card = _get_card_or_404(db, card_id)
    _require_owner_team(ctx, card.owner_team_id)

    row = db.query(DeckCardTeam).filter(
        and_(DeckCardTeam.card_id == card_id, DeckCardTeam.team_id == team_id)
    ).first()
    if row and row.is_owner:
        raise HTTPException(status_code=400, detail="Cannot remove the owner team")
    if row:
        db.delete(row)
        db.flush()
        db.refresh(card)
        _log_activity(db, card, user, "unshared_team", payload={"teamId": team_id},
                      message=f"{user.display_name} dejó de compartir con un equipo", notify=False)
    db.commit()
    db.refresh(card)
    return _serialize_card(card, full=True)
