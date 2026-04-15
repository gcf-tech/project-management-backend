from datetime import date, timedelta
from typing import Optional, Annotated, List
from fastapi import APIRouter, HTTPException, Header, Depends
from sqlalchemy.orm import Session
from app.api.dependencies import get_db, get_current_user
from app.db.models import User, Team, Skill, UserSkill
from app.services.metrics_svc import calculate_user_metrics, calculate_team_metrics
from app.services.nextcloud_svc import parse_date

router = APIRouter()


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


