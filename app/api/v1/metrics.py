from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional, Annotated, List
from fastapi import APIRouter, HTTPException, Header, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.api.dependencies import get_db, get_current_user
from app.db.models import Task, User, Team, Skill, UserSkill
from app.services.metrics_svc import calculate_user_metrics, calculate_team_metrics
from app.services.nextcloud_svc import parse_date

router = APIRouter()

_MONTHS = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun',
           'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']


def _bucket_label(bucket_key, bucket_type: str) -> str:
    if bucket_type == "week":
        yw = int(bucket_key)
        year, week = yw // 100, yw % 100
        try:
            d = datetime.strptime(f"{year} {week:02d} 1", "%G %V %u")
            return f"{d.day} {_MONTHS[d.month - 1]}"
        except ValueError:
            return str(bucket_key)
    else:
        parts = str(bucket_key).split('-')
        try:
            return f"{_MONTHS[int(parts[1]) - 1]} {parts[0][2:]}"
        except (IndexError, ValueError):
            return str(bucket_key)


@router.get("/my-metrics")
async def get_my_metrics(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    start = parse_date(start_date)
    end = parse_date(end_date)
    metrics = calculate_user_metrics(db, user.id, start, end)

    team_percentile = None
    if user.team_id:
        members = db.query(User).filter(
            User.team_id == user.team_id, User.is_active == True
        ).all()
        if len(members) > 1:
            all_rates = [(m.id, calculate_user_metrics(db, m.id, start, end)["completionRate"])
                         for m in members]
            beaten = sum(1 for uid, r in all_rates if r < metrics["completionRate"])
            team_percentile = round((beaten / (len(all_rates) - 1)) * 100, 1)

    s = start or (date.today() - timedelta(days=30))
    e = end or date.today()
    return {
        "userId": user.id, "displayName": user.display_name,
        "period": {"startDate": s.isoformat(), "endDate": e.isoformat()},
        **metrics,
        "teamPercentile": team_percentile,
    }


@router.get("/team/{team_id}/metrics")
async def get_team_metrics(
    team_id: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")

    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    if current_user.role != "admin":
        if team.leader_id != current_user.id and current_user.team_id != team_id:
            raise HTTPException(status_code=403, detail="Access denied")

    start = parse_date(start_date)
    end = parse_date(end_date)
    metrics = calculate_team_metrics(db, team_id, start, end)
    metrics["teamName"] = team.name
    metrics["isTechTeam"] = team.is_tech_team
    return metrics


@router.get("/my-team")
async def get_my_team_metrics(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")

    team = db.query(Team).filter(Team.leader_id == current_user.id).first()
    if not team and current_user.team_id:
        team = db.query(Team).filter(Team.id == current_user.team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="No team found")

    start = parse_date(start_date)
    end = parse_date(end_date)
    metrics = calculate_team_metrics(db, team.id, start, end)
    metrics["teamName"] = team.name
    metrics["isTechTeam"] = team.is_tech_team
    metrics["isLeader"] = team.leader_id == current_user.id
    return metrics


@router.get("/user/{user_id}/metrics")
async def get_user_metrics(
    user_id: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")

    if current_user.id != user_id and current_user.role not in ["admin", "leader"]:
        raise HTTPException(status_code=403, detail="Access denied")

    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    start = parse_date(start_date)
    end = parse_date(end_date)
    metrics = calculate_user_metrics(db, user_id, start, end)

    s = start or (date.today() - timedelta(days=30))
    e = end or date.today()
    return {
        "userId": user_id,
        "displayName": target_user.display_name,
        "teamId": target_user.team_id,
        "period": {"startDate": s.isoformat(), "endDate": e.isoformat()},
        **metrics,
        "teamPercentile": None,
    }


@router.get("/compare")
async def get_comparison_metrics(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")

    if not current_user.team_id:
        raise HTTPException(status_code=400, detail="User not in a team")

    team = db.query(Team).filter(Team.id == current_user.team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if not team.is_tech_team:
        raise HTTPException(status_code=403, detail="Comparison only available for tech team")

    start = parse_date(start_date)
    end = parse_date(end_date)
    members = db.query(User).filter(User.team_id == team.id, User.is_active == True).all()

    all_metrics = [
        {
            "userId": m.id,
            "displayName": m.display_name,
            "isCurrentUser": m.id == current_user.id,
            "metrics": calculate_user_metrics(db, m.id, start, end),
        }
        for m in members
    ]

    s = start or (date.today() - timedelta(days=30))
    e = end or date.today()
    return {
        "teamId": team.id,
        "teamName": team.name,
        "period": {"startDate": s.isoformat(), "endDate": e.isoformat()},
        "members": all_metrics,
    }


@router.get("/skills-comparison")
async def get_skills_comparison(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")

    if not current_user.team_id:
        raise HTTPException(status_code=400, detail="User not in a team")

    team = db.query(Team).filter(Team.id == current_user.team_id).first()
    if not team or not team.is_tech_team:
        raise HTTPException(status_code=403, detail="Skills comparison only for tech team")

    skills = db.query(Skill).filter(Skill.is_tech_only == True).all()
    members = db.query(User).filter(User.team_id == team.id, User.is_active == True).all()

    comparison = []
    for skill in skills:
        total_score, count = 0, 0
        member_data = []
        for member in members:
            us = db.query(UserSkill).filter(
                UserSkill.user_id == member.id, UserSkill.skill_id == skill.id
            ).first()
            if us:
                score = float(us.avg_endorsement_score) if us.total_endorsements > 0 else us.self_score
                member_data.append({
                    "userId": member.id,
                    "displayName": member.display_name,
                    "isCurrentUser": member.id == current_user.id,
                    "score": score,
                    "endorsements": us.total_endorsements,
                })
                total_score += score
                count += 1
        comparison.append({
            "skillId": skill.id,
            "skillName": skill.name,
            "category": skill.category,
            "teamAverage": round(total_score / count, 1) if count > 0 else 0,
            "members": member_data,
        })

    return {"teamId": team.id, "teamName": team.name, "skills": comparison}


@router.get("/delivery-trend")
async def get_delivery_trend(
    scope: str,
    start_date: str,
    end_date: str,
    team_id: Optional[int] = None,
    user_id: Optional[int] = None,
    bucket: str = "week",
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")

    if scope not in ("teams", "members"):
        raise HTTPException(status_code=422, detail="scope must be 'teams' or 'members'")
    if bucket not in ("week", "month"):
        raise HTTPException(status_code=422, detail="bucket must be 'week' or 'month'")

    start = parse_date(start_date)
    end = parse_date(end_date)
    if not start or not end:
        raise HTTPException(status_code=422, detail="start_date and end_date are required")

    role = current_user.role
    leader_team_id = None

    if role == "member":
        if user_id and user_id != current_user.id:
            raise HTTPException(status_code=403, detail="Access denied")
        if team_id and team_id != current_user.team_id:
            raise HTTPException(status_code=403, detail="Access denied")
        user_id = current_user.id

    elif role == "leader":
        lt = db.query(Team).filter(Team.leader_id == current_user.id).first()
        leader_team_id = lt.id if lt else current_user.team_id
        if team_id and team_id != leader_team_id:
            raise HTTPException(status_code=403, detail="Access denied")
        if user_id:
            tgt = db.query(User).filter(User.id == user_id, User.team_id == leader_team_id).first()
            if not tgt:
                raise HTTPException(status_code=403, detail="Access denied")
        if not team_id:
            team_id = leader_team_id

    if bucket == "week":
        bucket_expr = func.yearweek(Task.completed_at, 3)
    else:
        bucket_expr = func.date_format(Task.completed_at, '%Y-%m')

    days_expr = func.datediff(Task.deadline, func.date(Task.completed_at))

    base_filters = [
        Task.column_status == 'completed',
        Task.deleted_at.is_(None),
        Task.deadline.isnot(None),
        Task.completed_at.isnot(None),
        func.date(Task.completed_at) >= start,
        func.date(Task.completed_at) <= end,
        User.is_active == True,
    ]
    if user_id:
        base_filters.append(Task.owner_id == user_id)
    elif team_id:
        base_filters.append(User.team_id == team_id)
    elif leader_team_id:
        base_filters.append(User.team_id == leader_team_id)

    if scope == "teams" and not user_id:
        base_filters.append(User.team_id.isnot(None))
        rows = (
            db.query(
                User.team_id.label("series_id"),
                Team.name.label("series_label"),
                bucket_expr.label("bucket_key"),
                func.avg(days_expr).label("avg_days"),
            )
            .select_from(Task)
            .join(User, Task.owner_id == User.id)
            .join(Team, User.team_id == Team.id)
            .filter(*base_filters)
            .group_by(User.team_id, Team.name, bucket_expr)
            .order_by(bucket_expr)
            .all()
        )
        series_type = "team"
    else:
        rows = (
            db.query(
                Task.owner_id.label("series_id"),
                User.display_name.label("series_label"),
                bucket_expr.label("bucket_key"),
                func.avg(days_expr).label("avg_days"),
            )
            .select_from(Task)
            .join(User, Task.owner_id == User.id)
            .filter(*base_filters)
            .group_by(Task.owner_id, User.display_name, bucket_expr)
            .order_by(bucket_expr)
            .all()
        )
        series_type = "user"

    series_map = {}
    all_buckets = set()
    for row in rows:
        sid = str(row.series_id)
        if sid not in series_map:
            series_map[sid] = {"label": row.series_label, "data": {}}
        if row.avg_days is not None:
            series_map[sid]["data"][row.bucket_key] = round(float(row.avg_days), 2)
        all_buckets.add(row.bucket_key)

    sorted_buckets = sorted(all_buckets)
    labels = [_bucket_label(bk, bucket) for bk in sorted_buckets]
    series = [
        {
            "id": sid,
            "label": info["label"],
            "type": series_type,
            "data": [info["data"].get(bk) for bk in sorted_buckets],
        }
        for sid, info in series_map.items()
    ]

    return {"labels": labels, "series": series}

