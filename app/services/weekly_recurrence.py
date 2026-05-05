from datetime import date, datetime, time as dt_time
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.db.models import WeeklyBlock
from app.core.datetime_utils import to_rfc3339_z


def serialize_block(
    block: WeeklyBlock,
    *,
    is_virtual: bool = False,
    virtual_week_start: Optional[date] = None,
    is_master: bool = False,
) -> dict:
    week_start = virtual_week_start if is_virtual else block.week_start
    resolved_title = block.title or ""
    priority = None
    column_status = None
    item_type = None

    if block.block_type == "task" and block.task:
        resolved_title = block.task.title
        priority = block.task.priority
        column_status = block.task.column_status
    elif block.block_type == "activity" and block.activity:
        resolved_title = block.activity.title
        item_type = block.activity.type

    # Virtual blocks (old system) use compound id: "<series_id>:<week_start_iso>"
    block_id = f"{block.series_id}:{week_start.isoformat()}" if is_virtual else block.id

    payload = {
        "id": block_id,
        "week_start": week_start,
        "day_of_week": block.day_of_week,
        "block_type": block.block_type,
        "task_id": block.task_id,
        "activity_id": block.activity_id,
        "title": resolved_title,
        "color": block.color,
        "start_time": block.start_time,
        "end_time": block.end_time,
        "notes": block.notes,
        "priority": priority,
        "column_status": column_status,
        "item_type": item_type,
        "is_virtual": is_virtual,
        "is_master": is_master,
        "series_id": block.series_id,
        "recurrence": block.recurrence,
        "recurrence_until": block.recurrence_until,
        # RRule fields
        "rrule_string": block.rrule_string,
        "dtstart": to_rfc3339_z(block.dtstart),
        "rrule_until": to_rfc3339_z(block.rrule_until),
        "parent_block_id": block.parent_block_id,
        "exception_dates": block.exception_dates or [],
    }

    # Payload trim (Fase 1, Tarea 3) — drop fields that the JS _normalizeBlock
    # already defaults. Keeps the wire payload compact without touching client code.
    if payload["notes"] is None or payload["notes"] == "":
        payload.pop("notes")
    if not payload["exception_dates"]:
        payload.pop("exception_dates")

    return payload


def get_virtual_projections(db: Session, user_id: int, week_start: date) -> list[dict]:
    """Return virtual projections for all recurring series with no concrete occurrence on week_start."""
    concrete_series = {
        b.series_id
        for b in db.query(WeeklyBlock).filter(
            WeeklyBlock.user_id == user_id,
            WeeklyBlock.week_start == week_start,
            WeeklyBlock.series_id.isnot(None),
        ).all()
    }

    candidates = db.query(WeeklyBlock).filter(
        WeeklyBlock.user_id == user_id,
        WeeklyBlock.recurrence == "weekly",
        WeeklyBlock.week_start < week_start,
        or_(WeeklyBlock.recurrence_until.is_(None), WeeklyBlock.recurrence_until >= week_start),
    ).all()

    return [
        serialize_block(s, is_virtual=True, virtual_week_start=week_start)
        for s in candidates
        if s.series_id not in concrete_series
    ]


def materialize(db: Session, series_block: WeeklyBlock, week_start: date) -> WeeklyBlock:
    """Create a concrete occurrence of a recurring series for the given week."""
    concrete = WeeklyBlock(
        user_id=series_block.user_id,
        week_start=week_start,
        day_of_week=series_block.day_of_week,
        block_type=series_block.block_type,
        task_id=series_block.task_id,
        activity_id=series_block.activity_id,
        title=series_block.title,
        color=series_block.color,
        start_time=series_block.start_time,
        end_time=series_block.end_time,
        notes=series_block.notes,
        recurrence="none",
        series_id=series_block.series_id,
    )
    db.add(concrete)
    db.flush()
    return concrete


def get_series_origin(db: Session, series_id: str, user_id: int) -> Optional[WeeklyBlock]:
    return db.query(WeeklyBlock).filter(
        WeeklyBlock.series_id == series_id,
        WeeklyBlock.user_id == user_id,
        WeeklyBlock.recurrence == "weekly",
    ).first()


def delete_materializations_from(db: Session, series_id: str, user_id: int, from_week: date) -> None:
    """Delete concrete occurrences (non-origin) with week_start >= from_week."""
    db.query(WeeklyBlock).filter(
        WeeklyBlock.series_id == series_id,
        WeeklyBlock.user_id == user_id,
        WeeklyBlock.recurrence == "none",
        WeeklyBlock.week_start >= from_week,
    ).delete(synchronize_session=False)


def delete_materializations_after(db: Session, series_id: str, user_id: int, after_week: date) -> None:
    """Delete concrete occurrences (non-origin) with week_start > after_week."""
    db.query(WeeklyBlock).filter(
        WeeklyBlock.series_id == series_id,
        WeeklyBlock.user_id == user_id,
        WeeklyBlock.recurrence == "none",
        WeeklyBlock.week_start > after_week,
    ).delete(synchronize_session=False)


def delete_all_materializations(db: Session, series_id: str, user_id: int) -> None:
    """Delete all concrete occurrences (non-origin) of a series."""
    db.query(WeeklyBlock).filter(
        WeeklyBlock.series_id == series_id,
        WeeklyBlock.user_id == user_id,
        WeeklyBlock.recurrence == "none",
    ).delete(synchronize_session=False)


def is_virtual_id(block_id: str) -> bool:
    return ":" in block_id


def parse_virtual_id(block_id: str) -> tuple[str, date]:
    series_id, week_iso = block_id.rsplit(":", 1)
    return series_id, date.fromisoformat(week_iso)
