from datetime import datetime
from typing import Optional, Annotated, List
from fastapi import APIRouter, HTTPException, Header, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.api.dependencies import get_db, get_current_user
from app.db.models import User, Team, Skill, UserSkill, SkillEndorsement
from app.schemas.user_schemas import UserUpdate, TeamCreate, TeamUpdate, SkillScore, SkillEndorsementCreate
from app.services.nextcloud_svc import fetch_deck_boards, fetch_deck_cards

router = APIRouter()


# ── Deck ──────────────────────────────────────────────────────────────────────

@router.get("/deck/boards")
async def get_deck_boards(authorization: Annotated[str, Header()]):
    return await fetch_deck_boards(authorization)


@router.get("/deck/boards/{board_id}/cards")
async def get_deck_cards(board_id: int, authorization: Annotated[str, Header()]):
    return await fetch_deck_cards(board_id, authorization)


# ── Skills ────────────────────────────────────────────────────────────────────

@router.get("/skills")
async def get_all_skills(tech_only: bool = False, db: Session = Depends(get_db)):
    query = db.query(Skill)
    if tech_only:
        query = query.filter(Skill.is_tech_only == True)
    return [
        {"id": s.id, "name": s.name, "category": s.category,
         "description": s.description, "isTechOnly": s.is_tech_only}
        for s in query.all()
    ]


@router.post("/skills")
async def create_skill(
    data: dict,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    current_user = await get_current_user(authorization, db)
    if not current_user or current_user.role not in ["leader", "admin"]:
        raise HTTPException(status_code=403, detail="Access denied")

    skill = Skill(
        name=data.get("name"),
        category=data.get("category", "other"),
        description=data.get("description"),
        is_tech_only=data.get("isTechOnly", True),
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    return {"id": skill.id, "name": skill.name, "category": skill.category}


@router.get("/users/{user_id}/skills")
async def get_user_skills(
    user_id: int,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    user_skills = db.query(UserSkill).filter(UserSkill.user_id == user_id).all()
    return [
        {
            "skillId": us.skill_id, "skillName": us.skill.name,
            "category": us.skill.category, "selfScore": us.self_score,
            "avgEndorsementScore": float(us.avg_endorsement_score),
            "totalEndorsements": us.total_endorsements,
        }
        for us in user_skills
    ]


@router.post("/users/me/skills")
async def update_my_skills(
    skills: List[SkillScore],
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")

    for s in skills:
        us = db.query(UserSkill).filter(
            UserSkill.user_id == current_user.id, UserSkill.skill_id == s.skillId
        ).first()
        if us:
            us.self_score = s.score
        else:
            db.add(UserSkill(user_id=current_user.id, skill_id=s.skillId, self_score=s.score))
    db.commit()
    return {"success": True}


@router.post("/users/{user_id}/skills/{skill_id}/endorse")
async def endorse_skill(
    user_id: int,
    skill_id: int,
    endorsement: SkillEndorsementCreate,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot endorse your own skills")

    user_skill = db.query(UserSkill).filter(
        UserSkill.user_id == user_id, UserSkill.skill_id == skill_id
    ).first()
    if not user_skill:
        user_skill = UserSkill(user_id=user_id, skill_id=skill_id, self_score=5)
        db.add(user_skill)
        db.flush()

    existing = db.query(SkillEndorsement).filter(
        SkillEndorsement.user_skill_id == user_skill.id,
        SkillEndorsement.endorsed_by == current_user.id,
    ).first()
    if existing:
        existing.score = endorsement.score
        existing.comment = endorsement.comment
    else:
        db.add(SkillEndorsement(
            user_skill_id=user_skill.id,
            endorsed_by=current_user.id,
            score=endorsement.score,
            comment=endorsement.comment,
        ))
    db.commit()

    endorsements = db.query(SkillEndorsement).filter(
        SkillEndorsement.user_skill_id == user_skill.id
    ).all()
    if endorsements:
        user_skill.avg_endorsement_score = sum(e.score for e in endorsements) / len(endorsements)
        user_skill.total_endorsements = len(endorsements)
        db.commit()
    return {"success": True}


@router.post("/users/{user_id}/skills/evaluate")
async def evaluate_user_skills(
    user_id: int,
    skills: List[SkillScore],
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Batch skill evaluation — used by leaders to score all skills of a team member."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")
    if current_user.role not in ["leader", "admin"]:
        raise HTTPException(status_code=403, detail="Access denied")
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot evaluate your own skills")

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    for s in skills:
        user_skill = db.query(UserSkill).filter(
            UserSkill.user_id == user_id, UserSkill.skill_id == s.skillId
        ).first()
        if not user_skill:
            user_skill = UserSkill(user_id=user_id, skill_id=s.skillId, self_score=5)
            db.add(user_skill)
            db.flush()

        existing = db.query(SkillEndorsement).filter(
            SkillEndorsement.user_skill_id == user_skill.id,
            SkillEndorsement.endorsed_by == current_user.id,
        ).first()
        if existing:
            existing.score = s.score
        else:
            db.add(SkillEndorsement(
                user_skill_id=user_skill.id,
                endorsed_by=current_user.id,
                score=s.score,
            ))

    db.flush()

    # Recalculate averages for all affected skills
    for s in skills:
        user_skill = db.query(UserSkill).filter(
            UserSkill.user_id == user_id, UserSkill.skill_id == s.skillId
        ).first()
        if user_skill:
            endorsements = db.query(SkillEndorsement).filter(
                SkillEndorsement.user_skill_id == user_skill.id
            ).all()
            if endorsements:
                user_skill.avg_endorsement_score = sum(e.score for e in endorsements) / len(endorsements)
                user_skill.total_endorsements = len(endorsements)

    db.commit()
    return {"success": True}


# ── Teams ─────────────────────────────────────────────────────────────────────

@router.get("/teams")
async def get_teams(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user or user.role not in ["leader", "admin"]:
        raise HTTPException(status_code=403, detail="Access denied")

    teams = db.query(Team).all()
    return [
        {
            "id": t.id, "name": t.name, "leaderId": t.leader_id,
            "parentTeamId": t.parent_team_id, "isTechTeam": t.is_tech_team,
            "memberCount": db.query(User).filter(User.team_id == t.id, User.is_active == True).count(),
        }
        for t in teams
    ]


@router.get("/teams/{team_id}/members")
async def get_team_members(
    team_id: int,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    members = db.query(User).filter(User.team_id == team_id, User.is_active == True).all()
    return [
        {"id": m.id, "ncUserId": m.nc_user_id, "displayName": m.display_name,
         "email": m.email, "role": m.role}
        for m in members
    ]


@router.get("/admin/users")
async def get_all_users(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")
    if current_user.role not in ["leader", "admin"]:
        raise HTTPException(status_code=403, detail="Access denied")

    if current_user.role == "admin":
        users = db.query(User).filter(User.is_active == True).all()
    else:
        team = db.query(Team).filter(Team.leader_id == current_user.id).first()
        users = db.query(User).filter(User.team_id == team.id, User.is_active == True).all() if team else [current_user]

    return [
        {
            "id": u.id, "ncUserId": u.nc_user_id, "displayName": u.display_name,
            "email": u.email, "jobTitle": u.job_title,
            "teamId": u.team_id, "role": u.role, "isActive": u.is_active,
        }
        for u in users
    ]


@router.patch("/admin/users/{user_id}")
async def update_user(
    user_id: int,
    data: UserUpdate,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if current_user.role != "admin":
        if current_user.role == "leader":
            team = db.query(Team).filter(Team.leader_id == current_user.id).first()
            if not team or target.team_id != team.id:
                raise HTTPException(status_code=403, detail="Can only update your team members")
        else:
            raise HTTPException(status_code=403, detail="Access denied")

    for field, value in data.model_dump(exclude_unset=True).items():
        if field == "displayName":
            target.display_name = value
        elif field == "jobTitle":
            target.job_title = value
        elif field == "teamId":
            target.team_id = value
        elif hasattr(target, field):
            setattr(target, field, value)

    target.updated_at = datetime.utcnow()
    db.commit()
    return {"success": True}


@router.post("/admin/users/{user_id}/set-role")
async def set_user_role(
    user_id: int,
    role: str,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    current_user = await get_current_user(authorization, db)
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    if role not in ["member", "leader", "admin"]:
        raise HTTPException(status_code=400, detail="Invalid role")

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    target.role = role
    db.commit()
    return {"success": True}


@router.post("/admin/teams")
async def create_team(
    data: TeamCreate,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    current_user = await get_current_user(authorization, db)
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    team = Team(name=data.name, parent_team_id=data.parentTeamId, is_tech_team=data.isTechTeam)
    db.add(team)
    db.commit()
    db.refresh(team)
    return {"success": True, "team": {"id": team.id, "name": team.name}}


@router.patch("/admin/teams/{team_id}")
async def update_team(
    team_id: int,
    data: TeamUpdate,
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

    is_admin = current_user.role == "admin"
    is_leader = team.leader_id == current_user.id
    if not is_admin and not is_leader:
        raise HTTPException(status_code=403, detail="Access denied")

    for field, value in data.model_dump(exclude_unset=True).items():
        if field == "leaderId":
            team.leader_id = value
        elif field == "parentTeamId":
            team.parent_team_id = value
        elif field == "isTechTeam":
            team.is_tech_team = value
        elif hasattr(team, field):
            setattr(team, field, value)

    team.updated_at = datetime.utcnow()
    db.commit()
    return {"success": True}


@router.delete("/admin/teams/{team_id}")
async def delete_team(
    team_id: int,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    current_user = await get_current_user(authorization, db)
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    count = db.query(User).filter(User.team_id == team_id).count()
    if count > 0:
        raise HTTPException(status_code=400, detail=f"Team has {count} members. Reassign first.")

    db.delete(team)
    db.commit()
    return {"success": True}


@router.post("/admin/teams/{team_id}/add-member")
async def add_team_member(
    team_id: int,
    authorization: Annotated[str | None, Header()] = None,
    user_id: Optional[int] = None,
    nc_user_id: Optional[str] = None,
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

    if current_user.role != "admin" and team.leader_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    if nc_user_id:
        target = db.query(User).filter(User.nc_user_id == nc_user_id).first()
    elif user_id is not None:
        target = db.query(User).filter(User.id == user_id).first()
    else:
        raise HTTPException(status_code=400, detail="Provide user_id or nc_user_id")

    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    target.team_id = team_id
    db.commit()
    return {"success": True}


@router.post("/admin/teams/{team_id}/remove-member")
async def remove_team_member(
    team_id: int,
    user_id: int,
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

    if current_user.role != "admin" and team.leader_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    target = db.query(User).filter(User.id == user_id, User.team_id == team_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not in this team")
    if team.leader_id == user_id:
        raise HTTPException(status_code=400, detail="Cannot remove team leader.")

    target.team_id = None
    db.commit()
    return {"success": True}


@router.get("/teams/{team_id}/job-titles")
async def get_team_job_titles(team_id: int, db: Session = Depends(get_db)):
    result = db.execute(text(
        "SELECT job_title, is_leader_title FROM team_job_titles "
        "WHERE team_id = :tid ORDER BY is_leader_title DESC, job_title"
    ), {"tid": team_id}).fetchall()
    return [{"jobTitle": row[0], "isLeaderTitle": bool(row[1])} for row in result]


@router.get("/job-titles")
async def get_all_job_titles(db: Session = Depends(get_db)):
    result = db.execute(text(
        "SELECT t.id, t.name, tj.job_title, tj.is_leader_title "
        "FROM team_job_titles tj JOIN teams t ON tj.team_id = t.id "
        "ORDER BY t.id, tj.is_leader_title DESC, tj.job_title"
    )).fetchall()

    teams_dict = {}
    for team_id, team_name, job_title, is_leader in result:
        if team_id not in teams_dict:
            teams_dict[team_id] = {"teamId": team_id, "teamName": team_name, "jobTitles": []}
        teams_dict[team_id]["jobTitles"].append({"jobTitle": job_title, "isLeaderTitle": bool(is_leader)})
    return list(teams_dict.values())