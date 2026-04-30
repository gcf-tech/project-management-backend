from datetime import datetime, date, time, timedelta
from typing import Optional, Annotated, Literal, List
from uuid import uuid4
import hashlib
import json as _json
from fastapi import APIRouter, HTTPException, Header, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import or_

from app.api.dependencies import get_db, get_current_user
from app.db.models import Task, Activity, UserPreferences, WeeklyBlock
from app.schemas.weekly import WeeklyBlockUnified
from app.services.weekly_aggregator_service import get_unified_week
from app.services.weekly_recurrence import (
    serialize_block,
    get_virtual_projections,
    materialize,
    get_series_origin,
    delete_materializations_from,
    delete_materializations_after,
    delete_all_materializations,
    is_virtual_id,
    parse_virtual_id,
)

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class PreferencesOut(BaseModel):
    week_start_day: int
    week_end_day: int
    calendar_view: str = "week"


class PreferencesIn(BaseModel):
    week_start_day: int
    week_end_day: int
    calendar_view: str = "week"


class WeeklyBlockCreate(BaseModel):
    week_start: date
    day_of_week: int
    block_type: Literal["task", "activity", "personal"]
    task_id: Optional[str] = None
    activity_id: Optional[str] = None
    title: Optional[str] = None
    color: Optional[str] = None
    start_time: time
    end_time: time
    notes: Optional[str] = None
    recurrence: Literal["none", "weekly"] = "none"
    recurrence_until: Optional[date] = None
    # RRule fields
    rrule_string: Optional[str] = None


class WeeklyBlockPatch(BaseModel):
    day_of_week: Optional[int] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    notes: Optional[str] = None
    title: Optional[str] = None
    color: Optional[str] = None
    recurrence: Optional[Literal["none", "weekly"]] = None
    recurrence_until: Optional[date] = None
    scope: Optional[Literal["this", "future", "all"]] = "this"
    # RRule fields
    rrule_string: Optional[str] = None
    exception_dates: Optional[List[str]] = None


class WeeklyBlockOut(BaseModel):
    id: str | int
    week_start: date
    day_of_week: int
    block_type: str
    task_id: Optional[str]
    activity_id: Optional[str]
    title: str
    color: Optional[str]
    start_time: time
    end_time: time
    notes: Optional[str]
    priority: Optional[str]
    column_status: Optional[str]
    item_type: Optional[str]
    is_virtual: bool = False
    is_master: bool = False
    series_id: Optional[str] = None
    recurrence: str = "none"
    recurrence_until: Optional[date] = None
    # RRule fields
    rrule_string: Optional[str] = None
    dtstart: Optional[str] = None
    rrule_until: Optional[str] = None
    parent_block_id: Optional[int] = None
    exception_dates: Optional[List[str]] = None

    class Config:
        from_attributes = True


# ── Preferences ───────────────────────────────────────────────────────────────

@router.get("/preferences")
async def get_preferences(
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    # 5 min — same as PREFS_TTL_MS in the JS persistent cache.
    response.headers["Cache-Control"] = "private, max-age=300"
    response.headers["Vary"] = "Authorization"

    prefs = db.query(UserPreferences).filter(UserPreferences.user_id == user.id).first()
    if not prefs:
        return {"week_start_day": 1, "week_end_day": 5, "calendar_view": "week"}
    return {
        "week_start_day": prefs.week_start_day,
        "week_end_day": prefs.week_end_day,
        "calendar_view": prefs.calendar_view or "week",
    }


@router.put("/preferences")
async def update_preferences(
    data: PreferencesIn,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    if data.week_start_day not in (0, 1):
        raise HTTPException(status_code=400, detail="week_start_day must be 0 (Sun) or 1 (Mon)")
    if data.week_end_day not in (4, 5, 6):
        raise HTTPException(status_code=400, detail="week_end_day must be 4 (Thu), 5 (Fri), or 6 (Sun)")
    if data.week_start_day != 0 and data.week_end_day <= data.week_start_day:
        raise HTTPException(status_code=400, detail="week_end_day must be greater than week_start_day")

    prefs = db.query(UserPreferences).filter(UserPreferences.user_id == user.id).first()
    VALID_VIEWS = {"day", "week", "month", "quarter", "semester", "annual"}
    calendar_view = data.calendar_view if data.calendar_view in VALID_VIEWS else "week"

    if prefs:
        prefs.week_start_day = data.week_start_day
        prefs.week_end_day = data.week_end_day
        prefs.calendar_view = calendar_view
        prefs.updated_at = datetime.utcnow()
    else:
        prefs = UserPreferences(
            user_id=user.id,
            week_start_day=data.week_start_day,
            week_end_day=data.week_end_day,
            calendar_view=calendar_view,
        )
        db.add(prefs)

    db.commit()
    db.refresh(prefs)
    return {
        "week_start_day": prefs.week_start_day,
        "week_end_day": prefs.week_end_day,
        "calendar_view": prefs.calendar_view or "week",
    }


# ── Blocks ────────────────────────────────────────────────────────────────────

@router.get("/blocks")
async def get_blocks(
    week_start: date = Query(...),
    authorization: Annotated[str | None, Header()] = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    if week_start > date.today() + timedelta(days=365):
        raise HTTPException(status_code=400, detail="week_start no puede ser mayor a un año en el futuro")

    # Exclude rrule masters here — they are returned separately below as is_master=True.
    # Including them in this query would add a second entry (is_master=False) that the
    # client treats as a concrete block, causing a duplicate on the creation week.
    # selectinload pre-fetches the related Task/Activity rows in a single batched
    # query, eliminating the N+1 caused by `block.task` access in serialize_block.
    blocks = (
        db.query(WeeklyBlock)
        .options(
            selectinload(WeeklyBlock.task),
            selectinload(WeeklyBlock.activity),
        )
        .filter(
            WeeklyBlock.user_id == user.id,
            WeeklyBlock.week_start == week_start,
            WeeklyBlock.rrule_string.is_(None),
        )
        .all()
    )

    result = []
    for block in blocks:
        if block.block_type == "task":
            if not block.task:
                continue
            if block.task.deleted_at is not None:
                continue
            if block.task.column_status == "completed":
                continue
        result.append(serialize_block(block))

    # Old-style virtual projections (recurrence="weekly" system)
    result.extend(get_virtual_projections(db, user.id, week_start))

    # RRule master blocks: returned for client-side expansion.
    # Include masters whose date range overlaps the requested week.
    week_end = week_start + timedelta(days=6)
    rrule_masters = (
        db.query(WeeklyBlock)
        .options(
            selectinload(WeeklyBlock.task),
            selectinload(WeeklyBlock.activity),
        )
        .filter(
            WeeklyBlock.user_id == user.id,
            WeeklyBlock.rrule_string.isnot(None),
            WeeklyBlock.week_start <= week_end,
            or_(
                WeeklyBlock.dtstart.is_(None),
                WeeklyBlock.dtstart <= datetime.combine(week_end, time.max),
            ),
            or_(
                WeeklyBlock.rrule_until.is_(None),
                WeeklyBlock.rrule_until >= datetime.combine(week_start, time.min),
            ),
        )
        .all()
    )
    for master in rrule_masters:
        result.append(serialize_block(master, is_master=True))

    # ── HTTP caching: ETag + Cache-Control ───────────────────────────────────
    # Serialize deterministically so the same payload always hashes identically.
    # `default=str` matches FastAPI's date/time encoding (date.isoformat / time.isoformat).
    payload_bytes = _json.dumps(result, sort_keys=True, default=str).encode("utf-8")
    etag         = f'"{hashlib.sha256(payload_bytes).hexdigest()[:16]}"'

    headers = {
        "Cache-Control": "private, max-age=30, stale-while-revalidate=120",
        "ETag":          etag,
        "Vary":          "Authorization",
    }

    if if_none_match and if_none_match == etag:
        return Response(status_code=304, headers=headers)

    return Response(content=payload_bytes, media_type="application/json", headers=headers)


@router.get("/unified")
async def get_unified_blocks(
    week_start: date = Query(...),
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    if week_start > date.today() + timedelta(days=365):
        raise HTTPException(status_code=400, detail="week_start no puede ser mayor a un año en el futuro")

    end_date = week_start + timedelta(days=6)
    blocks = get_unified_week(db, user.id, week_start, end_date)
    return [b.model_dump() for b in blocks]


@router.post("/blocks", status_code=201)
async def create_block(
    data: WeeklyBlockCreate,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    if data.end_time <= data.start_time:
        raise HTTPException(status_code=400, detail="end_time must be after start_time")

    if data.recurrence_until and data.recurrence_until < data.week_start:
        raise HTTPException(status_code=400, detail="recurrence_until must be >= week_start")

    if data.block_type == "task":
        if not data.task_id:
            raise HTTPException(status_code=400, detail="task_id is required for task blocks")
        task = db.query(Task).filter(
            Task.id == data.task_id,
            Task.deleted_at.is_(None),
            or_(Task.owner_id == user.id, Task.assigned_to == user.id),
        ).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found or access denied")
        if task.column_status == "completed":
            raise HTTPException(status_code=400, detail="Cannot schedule a completed task")

    elif data.block_type == "activity":
        if not data.activity_id:
            raise HTTPException(status_code=400, detail="activity_id is required for activity blocks")
        activity = db.query(Activity).filter(
            Activity.id == data.activity_id,
            Activity.deleted_at.is_(None),
            or_(Activity.owner_id == user.id, Activity.assigned_to == user.id),
        ).first()
        if not activity:
            raise HTTPException(status_code=404, detail="Activity not found or access denied")

    elif data.block_type == "personal":
        if not data.title:
            raise HTTPException(status_code=400, detail="title is required for personal blocks")

    series_id = str(uuid4()) if (data.recurrence == "weekly" or data.rrule_string) else None

    # Compute dtstart for rrule master blocks (UTC midnight of first occurrence)
    dtstart = None
    rrule_until = None
    if data.rrule_string:
        dtstart = _compute_dtstart(data.week_start, data.day_of_week, data.start_time)
        rrule_until = _extract_rrule_until(data.rrule_string)

    block = WeeklyBlock(
        user_id=user.id,
        week_start=data.week_start,
        day_of_week=data.day_of_week,
        block_type=data.block_type,
        task_id=data.task_id,
        activity_id=data.activity_id,
        title=data.title,
        color=data.color,
        start_time=data.start_time,
        end_time=data.end_time,
        notes=data.notes,
        recurrence=data.recurrence,
        recurrence_until=data.recurrence_until,
        series_id=series_id,
        rrule_string=data.rrule_string,
        dtstart=dtstart,
        rrule_until=rrule_until,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return serialize_block(block, is_master=bool(data.rrule_string))


@router.patch("/blocks/{block_id}")
async def patch_block(
    block_id: str,
    data: WeeklyBlockPatch,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    updates = data.model_dump(exclude_unset=True)
    scope = updates.pop("scope", "this")

    def apply_updates(block: WeeklyBlock) -> None:
        new_start = updates.get("start_time", block.start_time)
        new_end = updates.get("end_time", block.end_time)
        if new_end <= new_start:
            raise HTTPException(status_code=400, detail="end_time must be after start_time")
        for field, value in updates.items():
            if field in ("title", "color") and block.block_type != "personal":
                continue
            setattr(block, field, value)
        block.updated_at = datetime.utcnow()

    if is_virtual_id(block_id):
        series_id, week_start = parse_virtual_id(block_id)
        origin = get_series_origin(db, series_id, user.id)
        if not origin:
            raise HTTPException(status_code=404, detail="Series not found")

        if scope == "this":
            # Materialize this week and apply updates only to that concrete occurrence
            block = materialize(db, origin, week_start)
            apply_updates(block)
            db.commit()
            db.refresh(block)
            return serialize_block(block)

        # scope='future' or 'all': update origin pattern, rebuild projections
        apply_updates(origin)
        if scope == "future":
            delete_materializations_after(db, series_id, user.id, week_start)
        else:  # all
            delete_all_materializations(db, series_id, user.id)
        db.commit()
        db.refresh(origin)
        return serialize_block(origin, is_virtual=True, virtual_week_start=week_start)

    # Concrete block
    try:
        numeric_id = int(block_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Block not found")

    block = db.query(WeeklyBlock).filter(WeeklyBlock.id == numeric_id).first()
    if not block:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    if block.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    apply_updates(block)

    if block.series_id and scope in ("future", "all"):
        origin = get_series_origin(db, block.series_id, user.id)
        if origin and origin.id != block.id:
            apply_updates(origin)
        if scope == "future":
            delete_materializations_after(db, block.series_id, user.id, block.week_start)
        else:  # all
            delete_all_materializations(db, block.series_id, user.id)

    db.commit()
    db.refresh(block)
    return serialize_block(block)


@router.delete("/blocks/{block_id}")
async def delete_block(
    block_id: str,
    scope: Literal["this", "future", "all"] = Query("this"),
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    if is_virtual_id(block_id):
        series_id, week_start = parse_virtual_id(block_id)
        origin = get_series_origin(db, series_id, user.id)
        if not origin:
            raise HTTPException(status_code=404, detail="Series not found")

        if scope == "this":
            raise HTTPException(
                status_code=400,
                detail="Cannot delete a single virtual occurrence. Use scope='future' or scope='all'.",
            )
        elif scope == "future":
            # Cut the series just before this week; remove this week's materializations onward
            origin.recurrence_until = week_start - timedelta(days=1)
            delete_materializations_from(db, series_id, user.id, week_start)
        else:  # all
            delete_all_materializations(db, series_id, user.id)
            db.delete(origin)

        db.commit()
        return {"message": "Bloque eliminado"}

    # Concrete block
    try:
        numeric_id = int(block_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Block not found")

    block = db.query(WeeklyBlock).filter(WeeklyBlock.id == numeric_id).first()
    if not block:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    if block.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    if not block.series_id:
        db.delete(block)
    elif block.recurrence == "weekly":
        # Deleting the origin directly → clean up the entire series
        delete_all_materializations(db, block.series_id, user.id)
        db.delete(block)
    elif scope == "this":
        db.delete(block)
    elif scope == "future":
        origin = get_series_origin(db, block.series_id, user.id)
        if origin:
            origin.recurrence_until = block.week_start - timedelta(days=1)
        delete_materializations_from(db, block.series_id, user.id, block.week_start)
    else:  # all
        origin = get_series_origin(db, block.series_id, user.id)
        delete_all_materializations(db, block.series_id, user.id)
        if origin:
            db.delete(origin)

    db.commit()
    return {"message": "Bloque eliminado"}


# ── Available items ───────────────────────────────────────────────────────────

@router.get("/available-items")
async def get_available_items(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    tasks = db.query(Task).filter(
        Task.deleted_at.is_(None),
        Task.column_status.in_(["actively-working", "working-now"]),
        or_(Task.owner_id == user.id, Task.assigned_to == user.id),
    ).all()

    activities = db.query(Activity).filter(
        Activity.deleted_at.is_(None),
        or_(Activity.owner_id == user.id, Activity.assigned_to == user.id),
    ).all()

    return {
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "priority": t.priority,
                "column_status": t.column_status,
            }
            for t in tasks
        ],
        "activities": [
            {
                "id": a.id,
                "title": a.title,
                "type": a.type,
            }
            for a in activities
        ],
    }


# ── Aggregate ─────────────────────────────────────────────────────────────────

@router.get("/aggregate")
async def aggregate_blocks(
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
    granularity: str = Query("day"),
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Return block load metrics per day in [from_date, to_date]."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    if (to_date - from_date).days > 366:
        raise HTTPException(status_code=400, detail="El rango from/to no puede superar 366 días")

    # Fetch blocks whose week_start falls in a range that could contain the interval
    blocks = db.query(WeeklyBlock).filter(
        WeeklyBlock.user_id == user.id,
        WeeklyBlock.week_start >= from_date - timedelta(days=6),
        WeeklyBlock.week_start <= to_date,
        WeeklyBlock.rrule_string.is_(None),   # masters are virtual-expanded client-side
    ).all()

    from collections import defaultdict
    by_day: dict[date, list] = defaultdict(list)

    for block in blocks:
        ws = block.week_start
        js_dow = block.day_of_week
        py_wd = _JS_TO_PY_WEEKDAY.get(js_dow, 0)
        offset = (py_wd - ws.weekday() + 7) % 7
        actual_date = ws + timedelta(days=offset)
        if from_date <= actual_date <= to_date:
            by_day[actual_date].append(block)

    result = []
    for d in sorted(by_day):
        day_blocks = by_day[d]
        total_mins = sum(
            (
                datetime.combine(d, b.end_time) - datetime.combine(d, b.start_time)
            ).seconds // 60
            for b in day_blocks
        )
        result.append({
            "date": d.isoformat(),
            "taskCount": len(day_blocks),
            "totalHours": round(total_mins / 60, 1),
            "completionRate": 0.0,
        })
    return result


# ── RRule helpers ─────────────────────────────────────────────────────────────

# JS day_of_week (0=Sun … 6=Sat) → Python weekday (0=Mon … 6=Sun)
_JS_TO_PY_WEEKDAY = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}


def _compute_dtstart(week_start: date, day_of_week: int, start_t: time) -> datetime:
    """Return the datetime of the first occurrence of a recurring block."""
    py_wd = _JS_TO_PY_WEEKDAY.get(day_of_week, 0)
    offset = (py_wd - week_start.weekday() + 7) % 7
    occurrence_date = week_start + timedelta(days=offset)
    return datetime.combine(occurrence_date, start_t)


def _extract_rrule_until(rrule_string: str) -> Optional[datetime]:
    """Parse UNTIL=YYYYMMDDTHHMMSSz from an RRULE string, return as datetime or None."""
    for part in rrule_string.upper().split(";"):
        if part.startswith("UNTIL="):
            raw = part[6:].rstrip("Z")
            try:
                if "T" in raw:
                    return datetime.strptime(raw, "%Y%m%dT%H%M%S")
                return datetime.strptime(raw, "%Y%m%d")
            except ValueError:
                return None
    return None
