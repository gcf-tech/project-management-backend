import logging
from datetime import datetime, date, timezone
from typing import Optional, Annotated, List
from fastapi import APIRouter, HTTPException, Header, Depends
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError, DataError

logger = logging.getLogger(__name__)

from app.api.dependencies import get_db, get_current_user
from app.db.models import Task, Activity, Subtask, TimeLog, Observation, User
from app.schemas.task_schemas import (
    TaskCreate, TaskPatch, ActivityCreate, ActivityPatch,
    TimeRecord, ColumnUpdate, TimeLogCreate, TimeLogPatch
)
from app.services.task_svc import (
    serialize_task, serialize_activity,
    record_time_on_task, record_time_on_activity,
    _recalc_time_spent
)
from app.services.nextcloud_svc import parse_date
from app.core.datetime_utils import utc_now, ensure_aware_utc, to_rfc3339_z

router = APIRouter()


def _gen_task_id() -> str:
    return f"task-{int(utc_now().timestamp() * 1000)}"


def _gen_activity_id() -> str:
    return f"activity-{int(utc_now().timestamp() * 1000)}"


def _gen_subtask_id(index: int) -> str:
    return f"sub-{int(utc_now().timestamp() * 1000)}-{index}"


# ── Tasks ─────────────────────────────────────────────────────────────────────

@router.get("/tareas")
async def get_tasks(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        return []
    user = await get_current_user(authorization, db)
    if not user:
        return []
    tasks = db.query(Task).filter(
        and_(Task.deleted_at.is_(None),
             or_(Task.owner_id == user.id, Task.assigned_to == user.id))
    ).all()
    return [serialize_task(t) for t in tasks]


@router.get("/tareas/{task_id}")
async def get_task(
    task_id: str,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id, Task.deleted_at.is_(None)).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return serialize_task(task)


@router.post("/tareas")
async def create_task(
    data: TaskCreate,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    task_id = _gen_task_id()
    assigned_user = None
    if data.assignedTo:
        assigned_user = db.query(User).filter(User.nc_user_id == data.assignedTo).first()

    # Retroactive tasks assigned to another user require admin or leader role.
    if data.is_retroactive and assigned_user and assigned_user.id != user.id and user.role not in ("admin", "leader"):
        raise HTTPException(status_code=403, detail="Only admins or leaders can create retroactive tasks for other users")

    task = Task(
        id=task_id, title=data.title, description=data.description,
        owner_id=user.id, assigned_to=assigned_user.id if assigned_user else None,
        column_status="completed" if data.is_retroactive else data.column,
        type=data.type, priority=data.priority,
        start_date=parse_date(data.startDate), deadline=parse_date(data.deadline),
        difficulty=data.difficulty, difficulty_reason=data.difficultyReason,
        was_difficult=data.wasDifficult, deck_card_id=data.deckCardId,
        progress=100 if data.is_retroactive else 0,
        completed_at=ensure_aware_utc(data.completed_at) if (data.is_retroactive and data.completed_at) else None,
    )
    db.add(task)

    for idx, sub in enumerate(data.subtasks):
        db.add(Subtask(
            id=sub.get("id", _gen_subtask_id(idx)),
            task_id=task_id, text=sub.get("text", ""),
            completed=True if data.is_retroactive else sub.get("completed", False),
            time_spent=sub.get("timeSpent", 0),
        ))

    try:
        db.flush()

        if data.is_retroactive and data.time_logs:
            total_seconds = 0
            for entry in data.time_logs:
                seconds = int(round(entry.hours * 3600))
                db.add(TimeLog(
                    user_id=user.id,
                    task_id=task_id,
                    log_date=entry.log_date,
                    seconds=seconds,
                    client_op_id=f"retro-{task_id}-{entry.log_date.isoformat()}",
                ))
                total_seconds += seconds
            db.flush()
            task.time_spent = total_seconds

        db.commit()
    except (IntegrityError, DataError) as exc:
        db.rollback()
        logger.error(
            "DB error creating task | user_id=%s payload=%s error=%s",
            user.id, data.model_dump(), str(exc.orig),
        )
        raise HTTPException(status_code=422, detail=f"Invalid task data: {exc.orig}")

    db.refresh(task)
    return {"success": True, "task": serialize_task(task)}


@router.patch("/tareas/{task_id}")
async def patch_task(
    task_id: str,
    data: TaskPatch,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id, Task.deleted_at.is_(None)).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if authorization:
        user = await get_current_user(authorization, db)
        if user and task.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")

    for field, value in data.model_dump(exclude_unset=True).items():
        if field == "column":
            task.column_status = value
        elif field == "startDate":
            task.start_date = parse_date(value)
        elif field == "deadline":
            task.deadline = parse_date(value)
        elif field == "timeSpent":
            task.time_spent = value
        elif field == "progress":
            task.progress = value
        elif field == "wasDifficult":
            task.was_difficult = value
        elif field == "difficultyReason":
            task.difficulty_reason = value
        elif field == "assignedTo" and value:
            u = db.query(User).filter(User.nc_user_id == value).first()
            task.assigned_to = u.id if u else None
        elif field == "subtasks" and value is not None:
            db.query(Subtask).filter(Subtask.task_id == task_id).delete()
            for idx, sub in enumerate(value):
                db.add(Subtask(
                    id=sub.get("id", _gen_subtask_id(idx)),
                    task_id=task_id, text=sub.get("text", ""),
                    completed=sub.get("completed", False),
                    time_spent=sub.get("timeSpent", 0),
                ))
        elif field == "observations" and value is not None:
            db.query(Observation).filter(Observation.task_id == task_id).delete()
            for obs in value:
                db.add(Observation(task_id=task_id, user_id=task.owner_id, text=obs.get("text", "")))
        elif field == "timeLog" and value is not None:
            db.query(TimeLog).filter(TimeLog.task_id == task_id).delete()
            for entry in value:
                db.add(TimeLog(
                    user_id=task.owner_id, task_id=task_id,
                    log_date=parse_date(entry.get("date")),
                    seconds=entry.get("seconds", 0),
                ))
        elif hasattr(task, field):
            setattr(task, field, value)

    task.updated_at = utc_now()
    db.commit()
    db.refresh(task)
    return {"success": True, "task": serialize_task(task)}


@router.put("/tareas/{task_id}")
async def update_task(
    task_id: str,
    data: TaskCreate,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id, Task.deleted_at.is_(None)).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    if authorization:
        user = await get_current_user(authorization, db)
        if user and task.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")

    task.title = data.title
    task.description = data.description
    task.column_status = data.column
    task.type = data.type
    task.priority = data.priority
    task.start_date = parse_date(data.startDate)
    task.deadline = parse_date(data.deadline)
    task.difficulty = data.difficulty
    task.difficulty_reason = data.difficultyReason
    task.was_difficult = data.wasDifficult
    task.updated_at = utc_now()

    db.query(Subtask).filter(Subtask.task_id == task_id).delete()
    for idx, sub in enumerate(data.subtasks):
        db.add(Subtask(
            id=sub.get("id", _gen_subtask_id(idx)),
            task_id=task_id, text=sub.get("text", ""),
            completed=sub.get("completed", False),
            time_spent=sub.get("timeSpent", 0),
        ))

    db.commit()
    db.refresh(task)
    return {"success": True, "task": serialize_task(task)}


@router.delete("/tareas/{task_id}")
async def delete_task(
    task_id: str,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id, Task.deleted_at.is_(None)).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    user = None
    if authorization:
        user = await get_current_user(authorization, db)
        if user and task.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")
    task.deleted_at = utc_now()
    task.deleted_by = user.id if user else None
    db.commit()
    return {"success": True}


@router.post("/tareas/{task_id}/time")
async def record_task_time(
    task_id: str,
    time_data: TimeRecord,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id, Task.deleted_at.is_(None)).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    user = None
    if authorization:
        user = await get_current_user(authorization, db)
        if user and task.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")
    user_id = user.id if user else task.owner_id
    task = record_time_on_task(
        db, task, user_id,
        time_data.timeSpent, time_data.absoluteTime,
        time_data.subtaskId, time_data.feedback,
        time_data.startAt,
    )
    return {"success": True, "task": serialize_task(task)}


@router.patch("/tareas/{task_id}/columna")
async def update_task_column(
    task_id: str,
    data: ColumnUpdate,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id, Task.deleted_at.is_(None)).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    valid = ["actively-working", "working-now", "completed"]
    if data.column not in valid:
        raise HTTPException(status_code=400, detail="Invalid column")

    user = None
    if authorization:
        user = await get_current_user(authorization, db)
        if user and task.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")

    if data.column == "working-now" and user:
        existing = db.query(Task).filter(
            Task.column_status == "working-now",
            Task.id != task_id,
            Task.owner_id == user.id,
            Task.deleted_at.is_(None),
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Only one task can be in 'Working Right Now'")

    task.column_status = data.column
    if data.column == "completed":
        task.completed_at = utc_now()
        task.progress = 100
    task.updated_at = utc_now()
    db.commit()
    db.refresh(task)
    return {"success": True, "task": serialize_task(task)}


@router.post("/tareas/{task_id}/finalizar")
async def finalize_task(
    task_id: str,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id, Task.deleted_at.is_(None)).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    if authorization:
        user = await get_current_user(authorization, db)
        if user and task.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")
    task.progress = 100
    task.column_status = "completed"
    task.completed_at = utc_now()
    db.query(Subtask).filter(Subtask.task_id == task_id).update({"completed": True})
    task.updated_at = utc_now()
    db.commit()
    db.refresh(task)
    return {"success": True, "task": serialize_task(task)}


@router.post("/tareas/{task_id}/reabrir")
async def reabrir_task(
    task_id: str,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id, Task.deleted_at.is_(None)).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    if authorization:
        user = await get_current_user(authorization, db)
        if user and task.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")
    task.column_status = "actively-working"
    task.completed_at = None
    task.updated_at = utc_now()
    db.commit()
    db.refresh(task)
    return {"success": True, "task": serialize_task(task)}


# ── Activities ───────────────────────────────────────────────────────────────

@router.get("/activities")
async def get_activities(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        return []
    user = await get_current_user(authorization, db)
    if not user:
        return []
    activities = db.query(Activity).filter(
        and_(Activity.deleted_at.is_(None),
             or_(Activity.owner_id == user.id, Activity.assigned_to == user.id))
    ).all()
    return [serialize_activity(a) for a in activities]


@router.post("/activities")
async def create_activity(
    data: ActivityCreate,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    assigned_user = None
    if data.assignedTo:
        assigned_user = db.query(User).filter(User.nc_user_id == data.assignedTo).first()

    activity_id = _gen_activity_id()
    activity = Activity(
        id=activity_id, title=data.title, description=data.description,
        owner_id=user.id, assigned_to=assigned_user.id if assigned_user else None,
        type=data.type, priority=data.priority,
        start_date=parse_date(data.startDate), deadline=parse_date(data.deadline),
        progress=100 if data.is_retroactive else 0,
        completed_at=ensure_aware_utc(data.completed_at) if (data.is_retroactive and data.completed_at) else None,
    )
    try:
        db.add(activity)
        db.flush()

        if data.is_retroactive and data.time_logs:
            total_seconds = 0
            for entry in data.time_logs:
                seconds = int(round(entry.hours * 3600))
                db.add(TimeLog(
                    user_id=user.id,
                    activity_id=activity_id,
                    log_date=entry.log_date,
                    seconds=seconds,
                    client_op_id=f"retro-act-{activity_id}-{entry.log_date.isoformat()}",
                ))
                total_seconds += seconds
            db.flush()
            activity.time_spent = total_seconds

        db.commit()
        db.refresh(activity)
    except (IntegrityError, DataError) as exc:
        db.rollback()
        logger.error(
            "DB error creating activity | user_id=%s payload=%s error=%s",
            user.id, data.model_dump(), str(exc.orig),
        )
        raise HTTPException(status_code=422, detail=f"Invalid activity data: {exc.orig}")
    return {"success": True, "activity": serialize_activity(activity)}


@router.patch("/activities/{activity_id}")
async def patch_activity(
    activity_id: str,
    data: ActivityPatch,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    activity = db.query(Activity).filter(
        Activity.id == activity_id, Activity.deleted_at.is_(None)
    ).first()
    if not activity:
        raise HTTPException(status_code=404, detail=f"Activity {activity_id} not found")

    if authorization:
        user = await get_current_user(authorization, db)
        if user and activity.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")

    prev_progress = activity.progress

    for field, value in data.model_dump(exclude_unset=True).items():
        if field == "startDate":
            activity.start_date = parse_date(value)
        elif field == "deadline":
            activity.deadline = parse_date(value)
        elif field == "timeSpent":
            activity.time_spent = value
        elif field == "progress":
            if value == 100 and prev_progress != 100:
                activity.completed_at = datetime.utcnow()
            elif value < 100 and prev_progress == 100:
                activity.completed_at = None
            activity.progress = value
        elif field == "assignedTo" and value:
            u = db.query(User).filter(User.nc_user_id == value).first()
            activity.assigned_to = u.id if u else None
        elif field == "timeLog" and value is not None:
            db.query(TimeLog).filter(TimeLog.activity_id == activity_id).delete()
            for entry in value:
                db.add(TimeLog(
                    user_id=activity.owner_id, activity_id=activity_id,
                    log_date=parse_date(entry.get("date")),
                    seconds=entry.get("seconds", 0),
                ))
        elif hasattr(activity, field):
            setattr(activity, field, value)

    activity.updated_at = utc_now()
    try:
        db.commit()
        db.refresh(activity)
    except (IntegrityError, DataError) as exc:
        db.rollback()
        logger.error(
            "DB error patching activity | activity_id=%s payload=%s error=%s",
            activity_id, data.model_dump(exclude_unset=True), str(exc.orig),
        )
        raise HTTPException(status_code=422, detail=f"Invalid activity data: {exc.orig}")
    return {"success": True, "activity": serialize_activity(activity)}


@router.delete("/activities/{activity_id}")
async def delete_activity(
    activity_id: str,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    activity = db.query(Activity).filter(
        Activity.id == activity_id, Activity.deleted_at.is_(None)
    ).first()
    if not activity:
        raise HTTPException(status_code=404, detail=f"Activity {activity_id} not found")
    user = None
    if authorization:
        user = await get_current_user(authorization, db)
        if user and activity.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")
    activity.deleted_at = utc_now()
    activity.deleted_by = user.id if user else None
    db.commit()
    return {"success": True}


@router.post("/activities/{activity_id}/time")
async def record_activity_time(
    activity_id: str,
    time_data: TimeRecord,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    activity = db.query(Activity).filter(
        Activity.id == activity_id, Activity.deleted_at.is_(None)
    ).first()
    if not activity:
        raise HTTPException(status_code=404, detail=f"Activity {activity_id} not found")
    user = None
    if authorization:
        user = await get_current_user(authorization, db)
        if user and activity.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")
    user_id = user.id if user else activity.owner_id
    activity = record_time_on_activity(
        db, activity, user_id,
        time_data.timeSpent, time_data.absoluteTime,
        time_data.feedback,
        time_data.startAt,
    )
    return {"success": True, "activity": serialize_activity(activity)}


@router.post("/activities/{activity_id}/reabrir")
async def reabrir_activity(
    activity_id: str,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    activity = db.query(Activity).filter(
        Activity.id == activity_id, Activity.deleted_at.is_(None)
    ).first()
    if not activity:
        raise HTTPException(status_code=404, detail=f"Activity {activity_id} not found")
    if authorization:
        user = await get_current_user(authorization, db)
        if user and activity.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")
    activity.completed_at = None
    activity.progress = 0
    activity.updated_at = utc_now()
    db.commit()
    db.refresh(activity)
    return {"success": True, "activity": serialize_activity(activity)}

# ── Time Logs ───────────────────────────────────────────────────────────────

@router.post("/tareas/{task_id}/time-logs")
async def create_task_time_log(
    task_id: str,
    data: TimeLogCreate,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if data.seconds <= 0 or data.seconds > 86400:
        raise HTTPException(status_code=400, detail="Invalid seconds")

    log_date = parse_date(data.logDate)
    if log_date > datetime.now(timezone.utc).date():
        raise HTTPException(status_code=400, detail="Future dates not allowed")

    if data.clientOpId:
        existing_op = db.query(TimeLog).filter(TimeLog.client_op_id == data.clientOpId).first()
        if existing_op:
            return {"success": True, "task": serialize_task(task)}

    existing_log = db.query(TimeLog).filter(
        TimeLog.task_id == task_id,
        TimeLog.user_id == user.id,
        TimeLog.log_date == log_date
    ).first()

    if existing_log:
        raise HTTPException(status_code=409, detail="Time log for this date already exists")

    new_log = TimeLog(
        user_id=user.id,
        task_id=task_id,
        log_date=log_date,
        seconds=data.seconds,
        client_op_id=data.clientOpId,
        start_at=ensure_aware_utc(data.startAt) if data.startAt is not None else None,
    )
    db.add(new_log)
    db.flush()
    task = _recalc_time_spent(db, task_id=task_id)
    db.commit()
    db.refresh(task)
    return {"success": True, "task": serialize_task(task)}

@router.post("/activities/{activity_id}/time-logs")
async def create_activity_time_log(
    activity_id: str,
    data: TimeLogCreate,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    if data.seconds <= 0 or data.seconds > 86400:
        raise HTTPException(status_code=400, detail="Invalid seconds")

    log_date = parse_date(data.logDate)
    if log_date > datetime.now(timezone.utc).date():
        raise HTTPException(status_code=400, detail="Future dates not allowed")

    if data.clientOpId:
        existing_op = db.query(TimeLog).filter(TimeLog.client_op_id == data.clientOpId).first()
        if existing_op:
            return {"success": True, "activity": serialize_activity(activity)}

    existing_log = db.query(TimeLog).filter(
        TimeLog.activity_id == activity_id,
        TimeLog.user_id == user.id,
        TimeLog.log_date == log_date
    ).first()

    if existing_log:
        raise HTTPException(status_code=409, detail="Time log for this date already exists")

    new_log = TimeLog(
        user_id=user.id,
        activity_id=activity_id,
        log_date=log_date,
        seconds=data.seconds,
        client_op_id=data.clientOpId,
        start_at=ensure_aware_utc(data.startAt) if data.startAt is not None else None,
    )
    db.add(new_log)
    db.flush()
    activity = _recalc_time_spent(db, activity_id=activity_id)
    db.commit()
    db.refresh(activity)
    return {"success": True, "activity": serialize_activity(activity)}

@router.get("/tareas/{task_id}/time-logs")
async def get_task_time_logs(
    task_id: str,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    logs = db.query(TimeLog).filter(TimeLog.task_id == task_id).order_by(TimeLog.log_date.desc()).all()
    return [{"id": l.id, "logDate": l.log_date.isoformat(), "seconds": l.seconds, "userId": l.user_id, "updatedAt": to_rfc3339_z(l.updated_at)} for l in logs]

@router.get("/activities/{activity_id}/time-logs")
async def get_activity_time_logs(
    activity_id: str,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    logs = db.query(TimeLog).filter(TimeLog.activity_id == activity_id).order_by(TimeLog.log_date.desc()).all()
    return [{"id": l.id, "logDate": l.log_date.isoformat(), "seconds": l.seconds, "userId": l.user_id, "updatedAt": to_rfc3339_z(l.updated_at)} for l in logs]

@router.patch("/time-logs/{log_id}")
async def patch_time_log(
    log_id: int,
    data: TimeLogPatch,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    time_log = db.query(TimeLog).filter(TimeLog.id == log_id).first()
    if not time_log:
        # Idempotent: row already deleted by a prior seconds=0 call with the same clientOpId
        if data.seconds == 0 and data.clientOpId:
            return {"success": True}
        raise HTTPException(status_code=404, detail="Time log not found")

    if time_log.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied")

    task_id = time_log.task_id
    activity_id = time_log.activity_id

    if data.seconds == 0:
        db.delete(time_log)
    else:
        if data.seconds < 0 or data.seconds > 86400:
            raise HTTPException(status_code=400, detail="Invalid seconds")
        time_log.seconds = data.seconds
        time_log.client_op_id = data.clientOpId
        if data.startAt is not None:
            time_log.start_at = ensure_aware_utc(data.startAt)

    db.flush()
    if task_id:
        obj = _recalc_time_spent(db, task_id=task_id)
        db.commit()
        db.refresh(obj)
        return {"success": True, "task": serialize_task(obj)}
    else:
        obj = _recalc_time_spent(db, activity_id=activity_id)
        db.commit()
        db.refresh(obj)
        return {"success": True, "activity": serialize_activity(obj)}

@router.delete("/time-logs/{log_id}")
async def delete_time_log(
    log_id: int,
    clientOpId: Optional[str] = None,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    time_log = db.query(TimeLog).filter(TimeLog.id == log_id).first()
    if not time_log:
        if clientOpId:
            return {"success": True} 
        raise HTTPException(status_code=404, detail="Time log not found")

    if time_log.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied")

    task_id = time_log.task_id
    activity_id = time_log.activity_id

    db.delete(time_log)
    db.flush()
    if task_id:
        obj = _recalc_time_spent(db, task_id=task_id)
        db.commit()
        db.refresh(obj)
        return {"success": True, "task": serialize_task(obj)}
    else:
        obj = _recalc_time_spent(db, activity_id=activity_id)
        db.commit()
        db.refresh(obj)
        return {"success": True, "activity": serialize_activity(obj)}

@router.post("/admin/time-logs/reconcile")
async def reconcile_time_logs(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user or user.role not in ["admin"]:
        raise HTTPException(status_code=403, detail="Access denied")

    tasks = db.query(Task).all()
    for t in tasks:
        _recalc_time_spent(db, task_id=t.id)
    
    activities = db.query(Activity).all()
    for a in activities:
        _recalc_time_spent(db, activity_id=a.id)

    db.commit()
    return {"success": True, "message": f"Reconciled {len(tasks)} tasks and {len(activities)} activities."}

@router.get("/health")
async def health(db: Session = Depends(get_db)):
    return {
        "status": "healthy",
        "service": "Activity Tracker API",
        "version": "4.0.0",
        "database": "MySQL/Railway",
        "tasks_count": db.query(Task).filter(Task.deleted_at.is_(None)).count(),
        "users_count": db.query(User).count(),
    }