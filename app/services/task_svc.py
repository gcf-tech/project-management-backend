from datetime import datetime, date, timezone
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.db.models import Task, Activity, Subtask, TimeLog, Observation, User
from app.core.datetime_utils import to_rfc3339_z, utc_now, ensure_aware_utc


def serialize_task(task: Task) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "owner": task.owner.nc_user_id if task.owner else None,
        "assignedTo": task.assignee.nc_user_id if task.assignee else None,
        "column": task.column_status,
        "type": task.type,
        "priority": task.priority,
        "startDate": task.start_date.isoformat() if task.start_date else None,
        "deadline": task.deadline.isoformat() if task.deadline else None,
        "progress": task.progress,
        "timeSpent": task.time_spent,
        "difficulty": task.difficulty,
        "difficultyReason": task.difficulty_reason,
        "wasDifficult": task.was_difficult,
        "subtasks": [
            {"id": s.id, "text": s.text, "completed": s.completed, "timeSpent": s.time_spent}
            for s in task.subtasks
        ],
        "observations": [
            {"date": to_rfc3339_z(o.created_at), "text": o.text}
            for o in task.observations
        ],
        "timeLog": [
            {"id": t.id, "date": t.log_date.isoformat(), "seconds": t.seconds}
            for t in task.time_logs
        ],
        "completedAt": to_rfc3339_z(task.completed_at),
        "createdAt": to_rfc3339_z(task.created_at),
        "updatedAt": to_rfc3339_z(task.updated_at),
    }


def serialize_activity(activity: Activity) -> dict:
    return {
        "id": activity.id,
        "title": activity.title,
        "description": activity.description,
        "owner": activity.owner.nc_user_id if activity.owner else None,
        "assignedTo": activity.assignee.nc_user_id if activity.assignee else None,
        "column": "activities",
        "type": "activity",
        "activityType": activity.type,
        "subtasks": [],
        "priority": activity.priority,
        "startDate": activity.start_date.isoformat() if activity.start_date else None,
        "deadline": activity.deadline.isoformat() if activity.deadline else None,
        "progress": activity.progress,
        "timeSpent": activity.time_spent,
        "observations": [
            {"date": to_rfc3339_z(o.created_at), "text": o.text}
            for o in activity.observations
        ],
        "timeLog": [
            {"id": t.id, "date": t.log_date.isoformat(), "seconds": t.seconds}
            for t in activity.time_logs
        ],
        "completedAt": to_rfc3339_z(activity.completed_at),
        "createdAt": to_rfc3339_z(activity.created_at),
        "updatedAt": to_rfc3339_z(activity.updated_at),
    }

def _recalc_time_spent(db: Session, task_id: Optional[str] = None, activity_id: Optional[str] = None):
    if task_id:
        task = db.query(Task).with_for_update().filter(Task.id == task_id).first()
        if task:
            total_seconds = db.query(func.sum(TimeLog.seconds)).filter(TimeLog.task_id == task_id).scalar() or 0
            task.time_spent = int(total_seconds)
            task.updated_at = utc_now()
            return task
    elif activity_id:
        activity = db.query(Activity).with_for_update().filter(Activity.id == activity_id).first()
        if activity:
            total_seconds = db.query(func.sum(TimeLog.seconds)).filter(TimeLog.activity_id == activity_id).scalar() or 0
            activity.time_spent = int(total_seconds)
            activity.updated_at = utc_now()
            return activity
    return None


def record_time_on_task(
    db: Session,
    task: Task,
    user_id: int,
    time_spent: int,
    absolute_time: Optional[int],
    subtask_id: Optional[str],
    feedback: Optional[dict],
    start_at: Optional[datetime] = None,
) -> Task:
    today = datetime.now(timezone.utc).date()
    time_log = db.query(TimeLog).filter(
        TimeLog.task_id == task.id,
        TimeLog.log_date == today,
        TimeLog.user_id == user_id,
    ).first()

    if absolute_time is not None:
        if time_log:
            diff = absolute_time - task.time_spent
            new_seconds = time_log.seconds + diff
            if new_seconds <= 0:
                db.delete(time_log)
            else:
                time_log.seconds = new_seconds
        else:
            db.add(
                TimeLog(
                    user_id=user_id,
                    task_id=task.id,
                    log_date=today,
                    seconds=absolute_time,
                    start_at=ensure_aware_utc(start_at) if start_at is not None else None,
                )
            )
    else:
        if time_log:
            time_log.seconds += time_spent
            # preserve original start_at; only set if not already recorded
            if time_log.start_at is None and start_at is not None:
                time_log.start_at = ensure_aware_utc(start_at)
        else:
            db.add(TimeLog(
                user_id=user_id,
                task_id=task.id,
                log_date=today,
                seconds=time_spent,
                start_at=ensure_aware_utc(start_at) if start_at is not None else None,
            ))

    db.flush()
    task = _recalc_time_spent(db, task_id=task.id)

    if subtask_id and subtask_id != "none":
        subtask = db.query(Subtask).filter(
            Subtask.id == subtask_id, Subtask.task_id == task.id
        ).first()
        if subtask:
            subtask.time_spent += time_spent

    if feedback:
        if "progress" in feedback:
            task.progress = feedback["progress"]
        if feedback.get("observation"):
            db.add(Observation(task_id=task.id, user_id=user_id, text=feedback["observation"]))

    db.commit()
    db.refresh(task)
    return task


def record_time_on_activity(
    db: Session,
    activity: Activity,
    user_id: int,
    time_spent: int,
    absolute_time: Optional[int],
    feedback: Optional[dict],
    start_at: Optional[datetime] = None,
) -> Activity:
    today = datetime.now(timezone.utc).date()
    time_log = db.query(TimeLog).filter(
        TimeLog.activity_id == activity.id,
        TimeLog.log_date == today,
        TimeLog.user_id == user_id,
    ).first()

    if absolute_time is not None:
        if time_log:
            diff = absolute_time - activity.time_spent
            new_seconds = time_log.seconds + diff
            if new_seconds <= 0:
                db.delete(time_log)
            else:
                time_log.seconds = new_seconds
        else:
            db.add(
                TimeLog(
                    user_id=user_id,
                    activity_id=activity.id,
                    log_date=today,
                    seconds=absolute_time,
                    start_at=ensure_aware_utc(start_at) if start_at is not None else None,
                )
            )
    else:
        if time_log:
            time_log.seconds += time_spent
            if time_log.start_at is None and start_at is not None:
                time_log.start_at = ensure_aware_utc(start_at)
        else:
            db.add(TimeLog(
                user_id=user_id,
                activity_id=activity.id,
                log_date=today,
                seconds=time_spent,
                start_at=ensure_aware_utc(start_at) if start_at is not None else None,
            ))

    db.flush()
    activity = _recalc_time_spent(db, activity_id=activity.id)

    if feedback:
        if "progress" in feedback:
            activity.progress = feedback["progress"]
        if feedback.get("observation"):
            db.add(Observation(activity_id=activity.id, user_id=user_id, text=feedback["observation"]))

    db.commit()
    db.refresh(activity)
    return activity