from app.db.models import Task, Activity
from app.core.datetime_utils import to_rfc3339_z


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
        "difficulty": task.difficulty,
        "difficultyReason": task.difficulty_reason,
        "wasDifficult": task.was_difficult,
        "subtasks": [
            {"id": s.id, "text": s.text, "completed": s.completed}
            for s in task.subtasks
        ],
        "observations": [
            {"date": to_rfc3339_z(o.created_at), "text": o.text}
            for o in task.observations
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
        "observations": [
            {"date": to_rfc3339_z(o.created_at), "text": o.text}
            for o in activity.observations
        ],
        "completedAt": to_rfc3339_z(activity.completed_at),
        "createdAt": to_rfc3339_z(activity.created_at),
        "updatedAt": to_rfc3339_z(activity.updated_at),
    }
