from datetime import datetime, date, time
from typing import Optional, Annotated, Literal, List
from fastapi import APIRouter, HTTPException, Header, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.api.dependencies import get_db, get_current_user
from app.db.models import Task, Activity, UserPreferences, WeeklyBlock

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class PreferencesOut(BaseModel):
    week_start_day: int
    week_end_day: int


class PreferencesIn(BaseModel):
    week_start_day: int
    week_end_day: int


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


class WeeklyBlockPatch(BaseModel):
    day_of_week: Optional[int] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    notes: Optional[str] = None
    title: Optional[str] = None
    color: Optional[str] = None


class WeeklyBlockOut(BaseModel):
    id: int
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

    class Config:
        from_attributes = True


# ── Helpers ───────────────────────────────────────────────────────────────────

def _serialize_block(block: WeeklyBlock) -> dict:
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

    return {
        "id": block.id,
        "week_start": block.week_start,
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
    }


# ── Preferences ───────────────────────────────────────────────────────────────

@router.get("/preferences")
async def get_preferences(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    prefs = db.query(UserPreferences).filter(UserPreferences.user_id == user.id).first()
    if not prefs:
        return {"week_start_day": 1, "week_end_day": 5}
    return {"week_start_day": prefs.week_start_day, "week_end_day": prefs.week_end_day}


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
    if prefs:
        prefs.week_start_day = data.week_start_day
        prefs.week_end_day = data.week_end_day
        prefs.updated_at = datetime.utcnow()
    else:
        prefs = UserPreferences(
            user_id=user.id,
            week_start_day=data.week_start_day,
            week_end_day=data.week_end_day,
        )
        db.add(prefs)

    db.commit()
    db.refresh(prefs)
    return {"week_start_day": prefs.week_start_day, "week_end_day": prefs.week_end_day}


# ── Blocks ────────────────────────────────────────────────────────────────────

@router.get("/blocks")
async def get_blocks(
    week_start: date = Query(...),
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    blocks = db.query(WeeklyBlock).filter(
        WeeklyBlock.user_id == user.id,
        WeeklyBlock.week_start == week_start,
    ).all()

    result = []
    for block in blocks:
        if block.block_type == "task":
            if not block.task:
                continue
            if block.task.deleted_at is not None:
                continue
            if block.task.column_status == "completed":
                continue
        result.append(_serialize_block(block))

    return result


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
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return _serialize_block(block)


@router.patch("/blocks/{block_id}")
async def patch_block(
    block_id: int,
    data: WeeklyBlockPatch,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    block = db.query(WeeklyBlock).filter(WeeklyBlock.id == block_id).first()
    if not block:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    if block.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    updates = data.model_dump(exclude_unset=True)

    new_start = updates.get("start_time", block.start_time)
    new_end = updates.get("end_time", block.end_time)
    if new_end <= new_start:
        raise HTTPException(status_code=400, detail="end_time must be after start_time")

    for field, value in updates.items():
        if field == "title" and block.block_type != "personal":
            continue
        if field == "color" and block.block_type != "personal":
            continue
        setattr(block, field, value)

    block.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(block)
    return _serialize_block(block)


@router.delete("/blocks/{block_id}")
async def delete_block(
    block_id: int,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    block = db.query(WeeklyBlock).filter(WeeklyBlock.id == block_id).first()
    if not block:
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    if block.user_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    db.delete(block)
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
