from datetime import date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, extract
from app.db.models import Task, Activity, TimeLog, User, Team
from app.db.query_helpers import join_active_parents

MONTH_NAMES = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun',
               'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']


def calculate_user_metrics(db: Session, user_id: int,
                            start_date: date = None, end_date: date = None) -> dict:
    if not end_date:
        end_date = date.today()
    if not start_date:
        start_date = end_date - timedelta(days=30)

    total_tasks = db.query(Task).filter(
        Task.owner_id == user_id,
        Task.deleted_at.is_(None),
        func.date(Task.created_at) >= start_date,
        func.date(Task.created_at) <= end_date,
    ).count()

    completed_tasks_q = db.query(Task).filter(
        Task.owner_id == user_id,
        Task.deleted_at.is_(None),
        Task.column_status == "completed",
        func.date(Task.completed_at) >= start_date,
        func.date(Task.completed_at) <= end_date,
    )
    completed_tasks = completed_tasks_q.count()
    completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0

    total_seconds = join_active_parents(
        db.query(func.sum(TimeLog.seconds))
    ).filter(
        TimeLog.user_id == user_id,
        TimeLog.log_date >= start_date,
        TimeLog.log_date <= end_date,
    ).scalar() or 0
    hours_worked = round(total_seconds / 3600, 1)

    avg_difficulty = db.query(func.avg(Task.difficulty)).filter(
        Task.owner_id == user_id,
        Task.deleted_at.is_(None),
        Task.difficulty.isnot(None),
        func.date(Task.created_at) >= start_date,
    ).scalar() or 5

    iel = round(completion_rate * (1 + float(avg_difficulty) / 20), 1)

    completed_list = completed_tasks_q.all()
    sla_days = None
    if completed_list:
        deltas = [
            (t.completed_at.date() - t.created_at.date()).days
            for t in completed_list if t.completed_at and t.created_at
        ]
        if deltas:
            sla_days = round(sum(deltas) / len(deltas), 1)

    six_months_ago = end_date - timedelta(days=180)
    tasks_by_month_q = db.query(
        extract('year', Task.created_at).label('year'),
        extract('month', Task.created_at).label('month'),
        func.sum(func.if_(Task.column_status == 'completed', 1, 0)).label('completed')
    ).filter(
        Task.owner_id == user_id,
        Task.deleted_at.is_(None),
        func.date(Task.created_at) >= six_months_ago,
    ).group_by(
        extract('year', Task.created_at),
        extract('month', Task.created_at),
    ).order_by('year', 'month').all()

    tasks_by_month = [
        {"month": MONTH_NAMES[int(row.month) - 1], "count": int(row.completed or 0)}
        for row in tasks_by_month_q
    ]

    eighty_four_days_ago = end_date - timedelta(days=84)
    time_logs = join_active_parents(
        db.query(TimeLog)
    ).filter(
        TimeLog.user_id == user_id,
        TimeLog.log_date >= eighty_four_days_ago,
        TimeLog.log_date <= end_date,
    ).all()

    deep_work_by_day = {}
    for log in time_logs:
        key = log.log_date.isoformat()
        deep_work_by_day[key] = deep_work_by_day.get(key, 0) + log.seconds

    predictability = []
    for t in completed_list:
        if t.start_date and t.deadline and t.time_spent > 0:
            estimated_h = round(((t.deadline - t.start_date).days or 1) * 8, 1)
            actual_h = round(t.time_spent / 3600, 1)
            predictability.append({
                "title": t.title,
                "estimated": estimated_h,
                "actual": actual_h,
            })

    difficult_list = db.query(Task).filter(
        Task.owner_id == user_id,
        Task.deleted_at.is_(None),
        Task.was_difficult == True,
        func.date(Task.created_at) >= start_date,
    ).order_by(Task.difficulty.desc()).limit(10).all()

    difficult_tasks = [
        {"title": t.title, "difficulty": t.difficulty, "reason": t.difficulty_reason}
        for t in difficult_list
    ]

    status_rows = db.query(
        Task.column_status,
        func.count(Task.id).label('count')
    ).filter(
        Task.owner_id == user_id,
        Task.deleted_at.is_(None),
        func.date(Task.created_at) >= start_date,
        func.date(Task.created_at) <= end_date,
    ).group_by(Task.column_status).all()
    tasks_by_status = {row.column_status: int(row.count) for row in status_rows}

    return {
        "totalTasks": total_tasks,
        "completedTasks": completed_tasks,
        "completionRate": round(completion_rate, 1),
        "hoursWorked": hours_worked,
        "iel": iel,
        "slaAvgDays": sla_days,
        "avgDifficulty": round(float(avg_difficulty), 1),
        "tasksByMonth": tasks_by_month,
        "tasksByStatus": tasks_by_status,
        "deepWorkByDay": deep_work_by_day,
        "predictabilityByTask": predictability,
        "difficultTasks": difficult_tasks,
    }


def calculate_team_metrics(db: Session, team_id: int,
                            start_date: date = None, end_date: date = None) -> dict:
    if not end_date:
        end_date = date.today()
    if not start_date:
        start_date = end_date - timedelta(days=30)

    members = db.query(User).filter(User.team_id == team_id, User.is_active == True).all()
    member_ids = [m.id for m in members]

    if not member_ids:
        return {
            "teamId": team_id, "memberCount": 0, "totalTasks": 0,
            "completedTasks": 0, "completionRate": 0, "hoursWorked": 0,
            "avgProductivity": 0, "memberMetrics": [],
        }

    total_tasks = db.query(Task).filter(
        Task.owner_id.in_(member_ids),
        Task.deleted_at.is_(None),
        func.date(Task.created_at) >= start_date,
        func.date(Task.created_at) <= end_date,
    ).count()

    completed_tasks = db.query(Task).filter(
        Task.owner_id.in_(member_ids),
        Task.deleted_at.is_(None),
        Task.column_status == "completed",
        func.date(Task.completed_at) >= start_date,
        func.date(Task.completed_at) <= end_date,
    ).count()

    completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0

    total_seconds = join_active_parents(
        db.query(func.sum(TimeLog.seconds))
    ).filter(
        TimeLog.user_id.in_(member_ids),
        TimeLog.log_date >= start_date,
        TimeLog.log_date <= end_date,
    ).scalar() or 0

    hours_worked = round(total_seconds / 3600, 1)
    avg_productivity = round(completed_tasks / hours_worked, 2) if hours_worked > 0 else 0

    team_status_rows = db.query(
        Task.column_status,
        func.count(Task.id).label('count')
    ).filter(
        Task.owner_id.in_(member_ids),
        Task.deleted_at.is_(None),
        func.date(Task.created_at) >= start_date,
        func.date(Task.created_at) <= end_date,
    ).group_by(Task.column_status).all()
    tasks_by_status = {row.column_status: int(row.count) for row in team_status_rows}

    member_metrics = []
    for member in members:
        m = calculate_user_metrics(db, member.id, start_date, end_date)
        member_metrics.append({
            "userId": member.id,
            "ncUserId": member.nc_user_id,
            "displayName": member.display_name,
            "completedTasks": m["completedTasks"],
            "totalTasks": m["totalTasks"],
            "completionRate": m["completionRate"],
            "hoursWorked": m["hoursWorked"],
            "iel": m["iel"],
            "slaAvgDays": m["slaAvgDays"],
            "tasksByMonth": m["tasksByMonth"],
            "tasksByStatus": m["tasksByStatus"],
            "deepWorkByDay": m["deepWorkByDay"],
        })

    member_metrics.sort(key=lambda x: x["completionRate"], reverse=True)

    return {
        "teamId": team_id,
        "memberCount": len(members),
        "totalTasks": total_tasks,
        "completedTasks": completed_tasks,
        "completionRate": round(completion_rate, 1),
        "hoursWorked": hours_worked,
        "avgProductivity": avg_productivity,
        "tasksByStatus": tasks_by_status,
        "memberMetrics": member_metrics,
    }