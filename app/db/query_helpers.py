from sqlalchemy import or_, and_
from app.db.models import TimeLog, Task, Activity


def join_active_parents(query):
    """
    Apply LEFT JOINs to Task and Activity on a TimeLog query and exclude logs
    whose parent task or activity has been soft-deleted.

    Each TimeLog row has EITHER task_id OR activity_id (never both), so we use
    outerjoin to avoid discarding half the rows. The filter then ensures the
    linked record is not soft-deleted.
    """
    return (
        query
        .outerjoin(Task, TimeLog.task_id == Task.id)
        .outerjoin(Activity, TimeLog.activity_id == Activity.id)
        .filter(
            or_(
                and_(TimeLog.task_id.isnot(None), Task.deleted_at.is_(None)),
                and_(TimeLog.activity_id.isnot(None), Activity.deleted_at.is_(None)),
            )
        )
    )
