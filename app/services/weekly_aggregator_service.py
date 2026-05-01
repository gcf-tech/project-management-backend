from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import List

from sqlalchemy.orm import Session, joinedload

from app.db.models import Activity, Task, TimeLog, WeeklyBlock
from app.schemas.weekly import WeeklyBlockUnified

_JS_TO_PY_WEEKDAY: dict[int, int] = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}


def _block_actual_date(block: WeeklyBlock) -> date:
    py_wd = _JS_TO_PY_WEEKDAY.get(block.day_of_week, 0)
    offset = (py_wd - block.week_start.weekday() + 7) % 7
    return block.week_start + timedelta(days=offset)


def _duration_minutes(start: time, end: time) -> int:
    start_dt = datetime.combine(date.min, start)
    end_dt = datetime.combine(date.min, end)
    return max(1, int((end_dt - start_dt).total_seconds() // 60))


def get_unified_week(
    db: Session,
    user_id: int,
    start_date: date,
    end_date: date,
) -> List[WeeklyBlockUnified]:
    result: List[WeeklyBlockUnified] = []

    # ── Query 1: manual weekly_blocks ────────────────────────────────────────
    manual_blocks = (
        db.query(WeeklyBlock)
        .filter(
            WeeklyBlock.user_id == user_id,
            WeeklyBlock.week_start >= start_date - timedelta(days=6),
            WeeklyBlock.week_start <= end_date,
            WeeklyBlock.rrule_string.is_(None),
        )
        .options(joinedload(WeeklyBlock.task), joinedload(WeeklyBlock.activity))
        .all()
    )

    for block in manual_blocks:
        actual_date = _block_actual_date(block)
        if not (start_date <= actual_date <= end_date):
            continue
        start_at = datetime.combine(actual_date, block.start_time)
        duration = _duration_minutes(block.start_time, block.end_time)

        if block.title:
            title = block.title
        elif block.block_type == "task" and block.task:
            title = block.task.title
        elif block.block_type == "activity" and block.activity:
            title = block.activity.title
        else:
            title = "Sin título"

        source_ref = block.task_id or block.activity_id

        result.append(WeeklyBlockUnified(
            id=f"manual-{block.id}",
            source="manual",
            source_ref_id=source_ref,
            title=title,
            start_at=start_at,
            duration_minutes=duration,
            color=block.color,
            metadata=None,
        ))

    # ── Query 2: task time logs ───────────────────────────────────────────────
    task_logs = (
        db.query(TimeLog)
        .join(Task, TimeLog.task_id == Task.id)
        .filter(
            TimeLog.user_id == user_id,
            TimeLog.task_id.isnot(None),
            TimeLog.log_date >= start_date,
            TimeLog.log_date <= end_date,
            Task.deleted_at.is_(None),
        )
        .options(joinedload(TimeLog.task))
        .all()
    )

    for log in task_logs:
        if log.start_at is not None:
            start_at = log.start_at
        else:
            start_at = datetime.combine(log.log_date, time(0, 0))

        result.append(WeeklyBlockUnified(
            id=f"task-log-{log.id}",
            source="task",
            source_ref_id=log.task_id,
            title=log.task.title if log.task else "Tarea eliminada",
            start_at=start_at,
            duration_minutes=max(1, log.seconds // 60),
            color=None,
            metadata={
                "priority": log.task.priority if log.task else None,
                "column_status": log.task.column_status if log.task else None,
            },
        ))

    # ── Query 3: activity time logs ───────────────────────────────────────────
    activity_logs = (
        db.query(TimeLog)
        .join(Activity, TimeLog.activity_id == Activity.id)
        .filter(
            TimeLog.user_id == user_id,
            TimeLog.activity_id.isnot(None),
            TimeLog.log_date >= start_date,
            TimeLog.log_date <= end_date,
            Activity.deleted_at.is_(None),
        )
        .options(joinedload(TimeLog.activity))
        .all()
    )

    for log in activity_logs:
        if log.start_at is not None:
            start_at = log.start_at
        else:
            start_at = datetime.combine(log.log_date, time(0, 0))

        result.append(WeeklyBlockUnified(
            id=f"activity-log-{log.id}",
            source="activity",
            source_ref_id=log.activity_id,
            title=log.activity.title if log.activity else "Actividad eliminada",
            start_at=start_at,
            duration_minutes=max(1, log.seconds // 60),
            color=None,
            metadata={
                "activity_type": log.activity.type if log.activity else None,
            },
        ))

    result.sort(key=lambda b: b.start_at)
    return result
