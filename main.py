"""
Activity Tracker Backend - With MariaDB persistence
====================================================
Backend API with Nextcloud OAuth2 authentication and MariaDB database.
"""

import os
import httpx
from datetime import datetime, date
from typing import Optional, Annotated, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from database import (
    get_db, get_or_create_user, serialize_task, serialize_activity,
    User, Team, Task, Activity, Subtask, TimeLog, Observation,
    Skill, UserSkill, SkillEndorsement, SessionLocal
)
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_


# =============================================================================
# CONFIGURATION
# =============================================================================

NC_URL = os.getenv("NC_URL", "https://portaltest.gcf.group")
OAUTH_CLIENT_ID = os.getenv("NC_OAUTH_CLIENT_ID", "")
OAUTH_CLIENT_SECRET = os.getenv("NC_OAUTH_CLIENT_SECRET", "")


# =============================================================================
# PYDANTIC MODELS
# =============================================================================

class OAuthCallback(BaseModel):
    code: str
    redirect_uri: str


class SubtaskCreate(BaseModel):
    id: Optional[str] = None
    text: str
    completed: bool = False
    timeSpent: int = 0


class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    column: str = "actively-working"
    type: str = "project"
    priority: Optional[str] = "medium"
    startDate: Optional[str] = None
    deadline: Optional[str] = None
    activityType: Optional[str] = None
    assignedTo: Optional[str] = None
    difficulty: Optional[int] = None
    difficultyReason: Optional[str] = None
    wasDifficult: bool = False
    subtasks: List[dict] = Field(default_factory=list)
    deckCardId: Optional[int] = None


class TaskPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    column: Optional[str] = None
    type: Optional[str] = None
    priority: Optional[str] = None
    startDate: Optional[str] = None
    deadline: Optional[str] = None
    progress: Optional[int] = None
    timeSpent: Optional[int] = None
    activityType: Optional[str] = None
    assignedTo: Optional[str] = None
    difficulty: Optional[int] = None
    difficultyReason: Optional[str] = None
    wasDifficult: Optional[bool] = None
    subtasks: Optional[List[dict]] = None
    observations: Optional[List[dict]] = None
    timeLog: Optional[List[dict]] = None


class ActivityCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    type: str = "other"
    priority: Optional[str] = "medium"
    startDate: Optional[str] = None
    deadline: Optional[str] = None
    assignedTo: Optional[str] = None


class ActivityPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    type: Optional[str] = None
    priority: Optional[str] = None
    startDate: Optional[str] = None
    deadline: Optional[str] = None
    progress: Optional[int] = None
    timeSpent: Optional[int] = None
    assignedTo: Optional[str] = None
    observations: Optional[List[dict]] = None
    timeLog: Optional[List[dict]] = None


class TimeRecord(BaseModel):
    timeSpent: int
    subtaskId: Optional[str] = None
    feedback: Optional[dict] = None
    absoluteTime: Optional[int] = None


class ColumnUpdate(BaseModel):
    column: str


# =============================================================================
# FASTAPI APP SETUP
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    print(f"[INFO] Activity Tracker started")
    print(f"[INFO] Nextcloud URL: {NC_URL}")
    print(f"[INFO] OAuth configured: {'Yes' if OAUTH_CLIENT_ID else 'No'}")
    print(f"[INFO] Database: MariaDB")
    yield
    print("[INFO] Activity Tracker shutting down")


app = FastAPI(
    title="Activity Tracker API",
    description="Backend API with Nextcloud OAuth2 and MariaDB",
    version="3.0.0",
    lifespan=lifespan,
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://portaltest.gcf.group",
        "https://test-project-management-nine.vercel.app",
        "http://localhost:3000",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def generate_task_id() -> str:
    return f"task-{int(datetime.utcnow().timestamp() * 1000)}"


def generate_activity_id() -> str:
    return f"activity-{int(datetime.utcnow().timestamp() * 1000)}"


def generate_subtask_id(index: int) -> str:
    return f"sub-{int(datetime.utcnow().timestamp() * 1000)}-{index}"


def parse_date(date_str: Optional[str]) -> Optional[date]:
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "")).date()
    except:
        return None


async def get_nc_user_info(authorization: str) -> dict:
    """Get user info from Nextcloud using OAuth token."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{NC_URL}/ocs/v1.php/cloud/user",
            headers={
                "Authorization": authorization,
                "OCS-APIREQUEST": "true",
                "Accept": "application/json",
            },
        )
        if response.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return response.json()["ocs"]["data"]


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db)
) -> Optional[User]:
    if not authorization:
        return None
    try:
        nc_data = await get_nc_user_info(authorization)
        user = await sync_user_from_nextcloud(db, nc_data, authorization)
        return user
    except HTTPException:
        return None



async def sync_user_from_nextcloud(db: Session, nc_data: dict, authorization: str) -> User:
    """Sincroniza usuario y equipo desde grupos de Nextcloud."""

    # Obtener grupos del usuario desde Nextcloud
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{NC_URL}/ocs/v1.php/cloud/users/{nc_data['id']}/groups",
            headers={
                "Authorization": authorization,
                "OCS-APIREQUEST": "true",
                "Accept": "application/json",
            },
        )
        groups = []
        if response.status_code == 200:
            groups = response.json()["ocs"]["data"]["groups"]

    # Determinar rol desde grupos
    if "admin" in groups:
        role = "admin"
    elif "Supervisors" in groups:
        role = "leader"
    else:
        role = "member"

    # Determinar equipo desde grupos
    # Excluir grupos que no son equipos de trabajo
    excluded = {"admin", "Supervisors", "emploiee"}
    team_groups = [g for g in groups if g not in excluded]

    team = None
    if team_groups:
        team_name = team_groups[0]  # tomar el primer grupo de equipo

        # Buscar o crear el equipo
        team = db.query(Team).filter(Team.name == team_name).first()
        if not team:
            is_tech = team_name.lower() == "tech"
            team = Team(name=team_name, is_tech_team=is_tech)
            db.add(team)
            db.flush()

    # Buscar o crear usuario
    user = db.query(User).filter(User.nc_user_id == nc_data["id"]).first()
    if not user:
        user = User(
            nc_user_id=nc_data["id"],
            display_name=nc_data.get("displayname", nc_data["id"]),
            email=nc_data.get("email"),
            role=role,
            team_id=team.id if team else None,
        )
        db.add(user)
    else:
        # Actualizar en cada login
        user.display_name = nc_data.get("displayname", nc_data["id"])
        user.email = nc_data.get("email")
        user.role = role
        user.team_id = team.id if team else None

    db.commit()
    db.refresh(user)
    return user

# =============================================================================
# ROUTES - OAUTH2 AUTHENTICATION
# =============================================================================

@app.post("/auth/callback")
async def oauth_callback(body: OAuthCallback):
    print(f"[DEBUG] client_id: {OAUTH_CLIENT_ID}")
    print(f"[DEBUG] client_secret: {OAUTH_CLIENT_SECRET[:10]}...")
    print(f"[DEBUG] redirect_uri: {body.redirect_uri}")
    print(f"[DEBUG] NC_URL: {NC_URL}")
    """Exchange OAuth authorization code for access token."""
    if not OAUTH_CLIENT_ID or not OAUTH_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="OAuth not configured")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{NC_URL}/index.php/apps/oauth2/api/v1/token",
            data={
                "grant_type": "authorization_code",
                "code": body.code,
                "redirect_uri": body.redirect_uri,
                "client_id": OAUTH_CLIENT_ID,
                "client_secret": OAUTH_CLIENT_SECRET,
            },
        )
        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"OAuth token exchange failed: {response.text}"
            )
        return response.json()


@app.get("/auth/me")
async def get_me(
    authorization: Annotated[str, Header()],
    db: Session = Depends(get_db),
):
    nc_data = await get_nc_user_info(authorization)
    user = await sync_user_from_nextcloud(db, nc_data, authorization)

    displayname = nc_data.get("displayname", nc_data["id"])
    parts = displayname.split()
    initials = "".join(p[0].upper() for p in parts[:2]) if parts else "U"

    return {
        "id": nc_data["id"],
        "displayname": displayname,
        "email": nc_data.get("email", ""),
        "initials": initials,
        "role": user.role,
        "teamId": user.team_id,
    }


# =============================================================================
# ROUTES - DECK API
# =============================================================================

@app.get("/api/deck/boards")
async def get_deck_boards(authorization: Annotated[str, Header()]):
    """Get all Deck boards accessible by the authenticated user."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{NC_URL}/index.php/apps/deck/api/v1.0/boards",
            headers={
                "Authorization": authorization,
                "OCS-APIREQUEST": "true",
                "Accept": "application/json",
            },
        )
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Failed to fetch boards")
        boards = response.json()
        return [{"id": b["id"], "title": b["title"]} for b in boards]


@app.get("/api/deck/boards/{board_id}/cards")
async def get_deck_cards_by_board(board_id: int, authorization: Annotated[str, Header()]):
    """Get all cards from a specific board."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{NC_URL}/index.php/apps/deck/api/v1.0/boards/{board_id}/stacks",
            headers={
                "Authorization": authorization,
                "OCS-APIREQUEST": "true",
                "Accept": "application/json",
            },
        )

        if response.status_code in [403, 404]:
            return []

        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Failed to fetch cards")

        stacks = response.json()
        cards = []
        for stack in stacks:
            for card in stack.get("cards", []):
                cards.append({
                    "id": card["id"],
                    "title": card.get("title", "Untitled"),
                    "description": card.get("description", ""),
                    "duedate": card.get("duedate"),
                    "labels": [l["title"] for l in card.get("labels", [])],
                    "stack": stack.get("title", ""),
                })
        return cards


# =============================================================================
# ROUTES - TASKS
# =============================================================================

@app.get("/api/proyectos/tareas")
async def get_all_tasks(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Get all tasks for the authenticated user."""
    if not authorization:
        return []

    user = await get_current_user(authorization, db)
    if not user:
        return []

    tasks = db.query(Task).filter(
        and_(
            Task.deleted_at.is_(None),
            or_(Task.owner_id == user.id, Task.assigned_to == user.id)
        )
    ).all()

    return [serialize_task(t) for t in tasks]


@app.get("/api/proyectos/tareas/{task_id}")
async def get_task_by_id(
    task_id: str,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Get a specific task."""
    task = db.query(Task).filter(Task.id == task_id, Task.deleted_at.is_(None)).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if authorization:
        user = await get_current_user(authorization, db)
        if user and task.owner_id != user.id and task.assigned_to != user.id:
            raise HTTPException(status_code=403, detail="Access denied")

    return serialize_task(task)


@app.post("/api/proyectos/tareas")
async def create_task(
    task_data: TaskCreate,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Create a new task."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    task_id = generate_task_id()

    # Handle assigned_to
    assigned_user = None
    if task_data.assignedTo:
        assigned_user = db.query(User).filter(User.nc_user_id == task_data.assignedTo).first()

    task = Task(
        id=task_id,
        title=task_data.title,
        description=task_data.description,
        owner_id=user.id,
        assigned_to=assigned_user.id if assigned_user else None,
        column_status=task_data.column,
        type=task_data.type,
        priority=task_data.priority,
        start_date=parse_date(task_data.startDate),
        deadline=parse_date(task_data.deadline),
        difficulty=task_data.difficulty,
        difficulty_reason=task_data.difficultyReason,
        was_difficult=task_data.wasDifficult,
        deck_card_id=task_data.deckCardId,
    )
    db.add(task)

    # Add subtasks
    for idx, sub_data in enumerate(task_data.subtasks):
        subtask = Subtask(
            id=sub_data.get("id", generate_subtask_id(idx)),
            task_id=task_id,
            text=sub_data.get("text", f"Subtask {idx + 1}"),
            completed=sub_data.get("completed", False),
            time_spent=sub_data.get("timeSpent", 0),
        )
        db.add(subtask)

    db.commit()
    db.refresh(task)

    return {"success": True, "task": serialize_task(task)}


@app.patch("/api/proyectos/tareas/{task_id}")
async def patch_task(
    task_id: str,
    task_patch: TaskPatch,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Partially update a task."""
    task = db.query(Task).filter(Task.id == task_id, Task.deleted_at.is_(None)).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if authorization:
        user = await get_current_user(authorization, db)
        if user and task.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")

    update_data = task_patch.model_dump(exclude_unset=True)

    for field, value in update_data.items():
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
        elif field == "assignedTo" and value:
            assigned_user = db.query(User).filter(User.nc_user_id == value).first()
            task.assigned_to = assigned_user.id if assigned_user else None
        elif field == "subtasks" and value is not None:
            # Delete existing and recreate
            db.query(Subtask).filter(Subtask.task_id == task_id).delete()
            for idx, sub_data in enumerate(value):
                subtask = Subtask(
                    id=sub_data.get("id", generate_subtask_id(idx)),
                    task_id=task_id,
                    text=sub_data.get("text", ""),
                    completed=sub_data.get("completed", False),
                    time_spent=sub_data.get("timeSpent", 0),
                )
                db.add(subtask)
        elif field == "observations" and value is not None:
            db.query(Observation).filter(Observation.task_id == task_id).delete()
            for obs in value:
                observation = Observation(
                    task_id=task_id,
                    user_id=task.owner_id,
                    text=obs.get("text", ""),
                )
                db.add(observation)
        elif field == "timeLog" and value is not None:
            db.query(TimeLog).filter(TimeLog.task_id == task_id).delete()
            for entry in value:
                time_log = TimeLog(
                    user_id=task.owner_id,
                    task_id=task_id,
                    log_date=parse_date(entry.get("date")),
                    seconds=entry.get("seconds", 0),
                )
                db.add(time_log)
        elif hasattr(task, field):
            setattr(task, field, value)
        elif field == "wasDifficult":
            task.was_difficult = value
        elif field == "difficultyReason":
            task.difficulty_reason = value

    task.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(task)

    return {"success": True, "task": serialize_task(task)}


@app.put("/api/proyectos/tareas/{task_id}")
async def update_task(
    task_id: str,
    task_data: TaskCreate,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Full update of a task."""
    task = db.query(Task).filter(Task.id == task_id, Task.deleted_at.is_(None)).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if authorization:
        user = await get_current_user(authorization, db)
        if user and task.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")

    task.title = task_data.title
    task.description = task_data.description
    task.column_status = task_data.column
    task.type = task_data.type
    task.priority = task_data.priority
    task.start_date = parse_date(task_data.startDate)
    task.deadline = parse_date(task_data.deadline)
    task.difficulty = task_data.difficulty
    task.difficulty_reason = task_data.difficultyReason
    task.was_difficult = task_data.wasDifficult
    task.updated_at = datetime.utcnow()

    # Update subtasks
    db.query(Subtask).filter(Subtask.task_id == task_id).delete()
    for idx, sub_data in enumerate(task_data.subtasks):
        subtask = Subtask(
            id=sub_data.get("id", generate_subtask_id(idx)),
            task_id=task_id,
            text=sub_data.get("text", f"Subtask {idx + 1}"),
            completed=sub_data.get("completed", False),
            time_spent=sub_data.get("timeSpent", 0),
        )
        db.add(subtask)

    db.commit()
    db.refresh(task)

    return {"success": True, "task": serialize_task(task)}


@app.delete("/api/proyectos/tareas/{task_id}")
async def delete_task(
    task_id: str,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Soft delete a task."""
    task = db.query(Task).filter(Task.id == task_id, Task.deleted_at.is_(None)).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    user = None
    if authorization:
        user = await get_current_user(authorization, db)
        if user and task.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")

    task.deleted_at = datetime.utcnow()
    task.deleted_by = user.id if user else None
    db.commit()

    return {"success": True}


@app.post("/api/proyectos/tareas/{task_id}/time")
async def record_time(
    task_id: str,
    time_data: TimeRecord,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Record time spent on a task."""
    task = db.query(Task).filter(Task.id == task_id, Task.deleted_at.is_(None)).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if time_data.absoluteTime is not None:
        task.time_spent = time_data.absoluteTime
    else:
        task.time_spent += time_data.timeSpent

    user = None
    if authorization:
        user = await get_current_user(authorization, db)
        if user and task.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")

    # Update total time
    task.time_spent += time_data.timeSpent

    # Update subtask time if specified
    if time_data.subtaskId and time_data.subtaskId != "none":
        subtask = db.query(Subtask).filter(
            Subtask.id == time_data.subtaskId,
            Subtask.task_id == task_id
        ).first()
        if subtask:
            subtask.time_spent += time_data.timeSpent

    # Add or update time log for today
    today = date.today()
    time_log = db.query(TimeLog).filter(
        TimeLog.task_id == task_id,
        TimeLog.log_date == today,
        TimeLog.user_id == (user.id if user else task.owner_id)
    ).first()

    if time_log:
        time_log.seconds += time_data.timeSpent
    else:
        time_log = TimeLog(
            user_id=user.id if user else task.owner_id,
            task_id=task_id,
            log_date=today,
            seconds=time_data.timeSpent,
        )
        db.add(time_log)

    # Handle feedback
    if time_data.feedback:
        if "progress" in time_data.feedback:
            task.progress = time_data.feedback["progress"]
        if time_data.feedback.get("observation"):
            observation = Observation(
                task_id=task_id,
                user_id=user.id if user else task.owner_id,
                text=time_data.feedback["observation"],
            )
            db.add(observation)

    task.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(task)

    return {"success": True, "task": serialize_task(task)}


@app.patch("/api/proyectos/tareas/{task_id}/columna")
async def update_task_column(
    task_id: str,
    column_update: ColumnUpdate,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Update the column of a task."""
    task = db.query(Task).filter(Task.id == task_id, Task.deleted_at.is_(None)).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    user = None
    if authorization:
        user = await get_current_user(authorization, db)
        if user and task.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")

    valid_columns = ["actively-working", "working-now", "completed"]
    if column_update.column not in valid_columns:
        raise HTTPException(status_code=400, detail="Invalid column")

    # Check working-now limit
    if column_update.column == "working-now" and user:
        existing = db.query(Task).filter(
            Task.column_status == "working-now",
            Task.id != task_id,
            Task.owner_id == user.id,
            Task.deleted_at.is_(None),
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Only one task can be in 'Working Right Now'")

    task.column_status = column_update.column
    if column_update.column == "completed":
        task.completed_at = datetime.utcnow()
        task.progress = 100

    task.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(task)

    return {"success": True, "task": serialize_task(task)}


@app.post("/api/proyectos/tareas/{task_id}/finalizar")
async def finalize_task(
    task_id: str,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Mark a task as complete."""
    task = db.query(Task).filter(Task.id == task_id, Task.deleted_at.is_(None)).first()
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    if authorization:
        user = await get_current_user(authorization, db)
        if user and task.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")

    task.progress = 100
    task.column_status = "completed"
    task.completed_at = datetime.utcnow()

    # Mark all subtasks as complete
    db.query(Subtask).filter(Subtask.task_id == task_id).update({"completed": True})

    task.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(task)

    return {"success": True, "task": serialize_task(task)}


@app.post("/api/proyectos/tareas/{task_id}/reabrir")
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
    # progress se mantiene en 100 — el usuario lo ajusta manualmente
    task.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(task)

    return {"success": True, "task": serialize_task(task)}


# =============================================================================
# ROUTES - ACTIVITIES
# =============================================================================

@app.get("/api/proyectos/activities")
async def get_all_activities(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Get all activities for the authenticated user."""
    if not authorization:
        return []

    user = await get_current_user(authorization, db)
    if not user:
        return []

    activities = db.query(Activity).filter(
        and_(
            Activity.deleted_at.is_(None),
            or_(Activity.owner_id == user.id, Activity.assigned_to == user.id)
        )
    ).all()

    return [serialize_activity(a) for a in activities]


@app.post("/api/proyectos/activities")
async def create_activity(
    activity_data: ActivityCreate,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Create a new activity."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    activity_id = generate_activity_id()

    assigned_user = None
    if activity_data.assignedTo:
        assigned_user = db.query(User).filter(User.nc_user_id == activity_data.assignedTo).first()

    activity = Activity(
        id=activity_id,
        title=activity_data.title,
        description=activity_data.description,
        owner_id=user.id,
        assigned_to=assigned_user.id if assigned_user else None,
        type=activity_data.type,
        priority=activity_data.priority,
        start_date=parse_date(activity_data.startDate),
        deadline=parse_date(activity_data.deadline),
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)

    return {"success": True, "activity": serialize_activity(activity)}


@app.patch("/api/proyectos/activities/{activity_id}")
async def patch_activity(
    activity_id: str,
    activity_patch: ActivityPatch,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Partially update an activity."""
    activity = db.query(Activity).filter(
        Activity.id == activity_id,
        Activity.deleted_at.is_(None)
    ).first()
    if not activity:
        raise HTTPException(status_code=404, detail=f"Activity {activity_id} not found")

    if authorization:
        user = await get_current_user(authorization, db)
        if user and activity.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")

    update_data = activity_patch.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        if field == "startDate":
            activity.start_date = parse_date(value)
        elif field == "deadline":
            activity.deadline = parse_date(value)
        elif field == "timeSpent":
             activity.time_spent = value
        elif field == "progress":
             activity.progress = value
        elif field == "assignedTo" and value:
            assigned_user = db.query(User).filter(User.nc_user_id == value).first()
            activity.assigned_to = assigned_user.id if assigned_user else None
        elif field == "timeLog" and value is not None:
            db.query(TimeLog).filter(TimeLog.activity_id == activity_id).delete()
            for entry in value:
                time_log = TimeLog(
                    user_id=activity.owner_id,
                    activity_id=activity_id,
                    log_date=parse_date(entry.get("date")),
                    seconds=entry.get("seconds", 0),
                )
                db.add(time_log)
        elif hasattr(activity, field):
            setattr(activity, field, value)

    activity.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(activity)

    return {"success": True, "activity": serialize_activity(activity)}


@app.delete("/api/proyectos/activities/{activity_id}")
async def delete_activity(
    activity_id: str,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Soft delete an activity."""
    activity = db.query(Activity).filter(
        Activity.id == activity_id,
        Activity.deleted_at.is_(None)
    ).first()
    if not activity:
        raise HTTPException(status_code=404, detail=f"Activity {activity_id} not found")

    user = None
    if authorization:
        user = await get_current_user(authorization, db)
        if user and activity.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")

    activity.deleted_at = datetime.utcnow()
    activity.deleted_by = user.id if user else None
    db.commit()

    return {"success": True}


@app.post("/api/proyectos/activities/{activity_id}/time")
async def record_activity_time(
    activity_id: str,
    time_data: TimeRecord,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Record time spent on an activity."""
    activity = db.query(Activity).filter(
        Activity.id == activity_id,
        Activity.deleted_at.is_(None)
    ).first()
    if not activity:
        raise HTTPException(status_code=404, detail=f"Activity {activity_id} not found")

    if time_data.absoluteTime is not None:
        activity.time_spent = time_data.absoluteTime
    else:
        activity.time_spent += time_data.timeSpent

    user = None
    if authorization:
        user = await get_current_user(authorization, db)
        if user and activity.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")

    if time_data.absoluteTime is not None:
        activity.time_spent = time_data.absoluteTime
    else:
        activity.time_spent += time_data.timeSpent

    # Add or update time log for today
    today = date.today()
    time_log = db.query(TimeLog).filter(
        TimeLog.activity_id == activity_id,
        TimeLog.log_date == today,
        TimeLog.user_id == (user.id if user else activity.owner_id)
    ).first()

    if time_log:
        time_log.seconds += time_data.timeSpent
    else:
        time_log = TimeLog(
            user_id=user.id if user else activity.owner_id,
            activity_id=activity_id,
            log_date=today,
            seconds=time_data.timeSpent,
        )
        db.add(time_log)

    if time_data.feedback:
        if "progress" in time_data.feedback:
            activity.progress = time_data.feedback["progress"]
        if time_data.feedback.get("observation"):
            observation = Observation(
                activity_id=activity_id,
                user_id=user.id if user else activity.owner_id,
                text=time_data.feedback["observation"],
            )
            db.add(observation)

    activity.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(activity)

    return {"success": True, "activity": serialize_activity(activity)}


@app.post("/api/proyectos/activities/{activity_id}/reabrir")
async def reabrir_activity(
    activity_id: str,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    activity = db.query(Activity).filter(
        Activity.id == activity_id,
        Activity.deleted_at.is_(None)
    ).first()
    if not activity:
        raise HTTPException(status_code=404, detail=f"Activity {activity_id} not found")

    if authorization:
        user = await get_current_user(authorization, db)
        if user and activity.owner_id != user.id:
            raise HTTPException(status_code=403, detail="Access denied")

    activity.completed_at = None
    activity.progress = 0
    activity.updated_at = datetime.utcnow()
    # progress se mantiene — el usuario lo ajusta manualmente
    db.commit()
    db.refresh(activity)

    return {"success": True, "activity": serialize_activity(activity)}


# =============================================================================
# ROUTES - TEAMS & USERS
# =============================================================================

@app.get("/api/teams")
async def get_teams(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Get all teams (for admins/leaders)."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = await get_current_user(authorization, db)
    if not user or user.role not in ["leader", "admin"]:
        raise HTTPException(status_code=403, detail="Access denied")

    teams = db.query(Team).all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "leaderId": t.leader_id,
            "parentTeamId": t.parent_team_id,
            "isTechTeam": t.is_tech_team,
            "memberCount": db.query(User).filter(User.team_id == t.id, User.is_active == True).count(),
        }
        for t in teams
    ]


@app.get("/api/teams/{team_id}/members")
async def get_team_members(
    team_id: int,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Get members of a team."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Check access: user must be in team, team leader, or admin
    if user.team_id != team_id and team.leader_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied")

    members = db.query(User).filter(User.team_id == team_id, User.is_active == True).all()
    return [
        {
            "id": m.id,
            "ncUserId": m.nc_user_id,
            "displayName": m.display_name,
            "email": m.email,
            "role": m.role,
        }
        for m in members
    ]


@app.post("/api/users/{user_id}/team")
async def assign_user_to_team(
    user_id: int,
    team_id: int,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Assign a user to a team (admin only)."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    current_user = await get_current_user(authorization, db)
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    user.team_id = team_id
    db.commit()

    return {"success": True}


# =============================================================================
# ROUTES - HEALTH
# =============================================================================

@app.get("/api/proyectos/health")
async def health_check(db: Session = Depends(get_db)):
    """Health check endpoint."""
    task_count = db.query(Task).filter(Task.deleted_at.is_(None)).count()
    user_count = db.query(User).count()

    return {
        "status": "healthy",
        "service": "Activity Tracker API",
        "version": "3.0.0",
        "database": "MariaDB",
        "tasks_count": task_count,
        "users_count": user_count,
        "oauth_configured": bool(OAUTH_CLIENT_ID),
    }


@app.get("/health")
async def root_health():
    """Root health check."""
    return {"status": "ok"}


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================


# =============================================================================
# ROUTES - DASHBOARD METRICS
# =============================================================================

from sqlalchemy import func, extract
from datetime import timedelta
from decimal import Decimal


class MetricsPeriod(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    period: str = "month"  # week, month, quarter, year


MONTH_NAMES = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic']

def calculate_user_metrics(db: Session, user_id: int, start_date: date = None, end_date: date = None) -> dict:
    if not end_date:
        end_date = date.today()
    if not start_date:
        start_date = end_date - timedelta(days=30)

    # --- Conteos base ---
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

    # --- Horas trabajadas ---
    total_seconds = db.query(func.sum(TimeLog.seconds)).filter(
        TimeLog.user_id == user_id,
        TimeLog.log_date >= start_date,
        TimeLog.log_date <= end_date,
    ).scalar() or 0
    hours_worked = round(total_seconds / 3600, 1)

    # --- Dificultad promedio ---
    avg_difficulty = db.query(func.avg(Task.difficulty)).filter(
        Task.owner_id == user_id,
        Task.deleted_at.is_(None),
        Task.difficulty.isnot(None),
        Task.created_at >= start_date,
    ).scalar() or 5  # default 5 si no hay datos

    # --- IEL: Índice de Efectividad Laboral ---
    # Fórmula: completionRate * (1 + avg_difficulty / 20)
    # Escala: si completionRate=100 y dificultad=10 → IEL=150; dificultad media=5 → IEL=125
    # Normalizado a 100 como objetivo base con dificultad media
    iel = round(completion_rate * (1 + float(avg_difficulty) / 20), 1)

    # --- SLA: días promedio entre creación y cierre ---
    completed_list = completed_tasks_q.all()
    sla_days = None
    if completed_list:
        deltas = [
            (t.completed_at.date() - t.created_at.date()).days
            for t in completed_list
            if t.completed_at and t.created_at
        ]
        if deltas:
            sla_days = round(sum(deltas) / len(deltas), 1)

    # --- tasksByMonth: últimos 6 meses, formato {month: "Ene", count: int} ---
    six_months_ago = end_date - timedelta(days=180)
    tasks_by_month_q = db.query(
        extract('year',  Task.created_at).label('year'),
        extract('month', Task.created_at).label('month'),
        func.sum(func.if_(Task.column_status == 'completed', 1, 0)).label('completed')
    ).filter(
        Task.owner_id == user_id,
        Task.deleted_at.is_(None),
        func.date(Task.created_at) >= six_months_ago,
    ).group_by(
        extract('year',  Task.created_at),
        extract('month', Task.created_at),
    ).order_by('year', 'month').all()

    tasks_by_month = [
        {
            "month": MONTH_NAMES[int(row.month) - 1],
            "count": int(row.completed or 0),
        }
        for row in tasks_by_month_q
    ]

    # --- deepWorkByDay: tiempo registrado por fecha (últimos 84 días = 12 semanas) ---
    eighty_four_days_ago = end_date - timedelta(days=84)
    time_logs = db.query(TimeLog).filter(
        TimeLog.user_id == user_id,
        TimeLog.log_date >= eighty_four_days_ago,
        TimeLog.log_date <= end_date,
    ).all()

    deep_work_by_day = {}
    for log in time_logs:
        key = log.log_date.isoformat()
        deep_work_by_day[key] = deep_work_by_day.get(key, 0) + log.seconds

    # --- predictabilityByTask: estimado (start→deadline) vs real (time_spent) ---
    predictability = []
    for t in completed_list:
        if t.start_date and t.deadline and t.time_spent > 0:
            estimated_h = round(((t.deadline - t.start_date).days or 1) * 8, 1)
            actual_h    = round(t.time_spent / 3600, 1)
            predictability.append({
                "title":     t.title,
                "estimated": estimated_h,
                "actual":    actual_h,
            })

    # --- difficultTasks: lista de tareas marcadas como difíciles ---
    difficult_list = db.query(Task).filter(
        Task.owner_id == user_id,
        Task.deleted_at.is_(None),
        Task.was_difficult == True,
        Task.created_at >= start_date,
    ).order_by(Task.difficulty.desc()).limit(10).all()

    difficult_tasks = [
        {
            "title":      t.title,
            "difficulty": t.difficulty,
            "reason":     t.difficulty_reason,
        }
        for t in difficult_list
    ]

    return {
        "totalTasks":          total_tasks,
        "completedTasks":      completed_tasks,
        "completionRate":      round(completion_rate, 1),
        "hoursWorked":         hours_worked,
        "iel":                 iel,
        "slaAvgDays":          sla_days,
        "avgDifficulty":       round(float(avg_difficulty), 1),
        "tasksByMonth":        tasks_by_month,
        "deepWorkByDay":       deep_work_by_day,
        "predictabilityByTask": predictability,
        "difficultTasks":      difficult_tasks,
    }


def calculate_team_metrics(db: Session, team_id: int, start_date: date = None, end_date: date = None) -> dict:
    if not end_date:
        end_date = date.today()
    if not start_date:
        start_date = end_date - timedelta(days=30)

    members = db.query(User).filter(User.team_id == team_id, User.is_active == True).all()
    member_ids = [m.id for m in members]

    if not member_ids:
        return {
            "teamId":        team_id,
            "memberCount":   0,
            "totalTasks":    0,
            "completedTasks": 0,
            "completionRate": 0,
            "hoursWorked":   0,
            "avgProductivity": 0,
            "memberMetrics": [],
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

    total_seconds = db.query(func.sum(TimeLog.seconds)).filter(
        TimeLog.user_id.in_(member_ids),
        TimeLog.log_date >= start_date,
        TimeLog.log_date <= end_date,
    ).scalar() or 0

    hours_worked     = round(total_seconds / 3600, 1)
    avg_productivity = round((completed_tasks / hours_worked), 2) if hours_worked > 0 else 0

    # memberMetrics plano — cada campo de métricas directo en el objeto miembro
    member_metrics = []
    for member in members:
        m = calculate_user_metrics(db, member.id, start_date, end_date)
        member_metrics.append({
            "userId":          member.id,
            "ncUserId":        member.nc_user_id,
            "displayName":     member.display_name,
            # métricas planas directamente aquí
            "completedTasks":  m["completedTasks"],
            "totalTasks":      m["totalTasks"],
            "completionRate":  m["completionRate"],
            "hoursWorked":     m["hoursWorked"],
            "iel":             m["iel"],
            "slaAvgDays":      m["slaAvgDays"],
            "tasksByMonth":    m["tasksByMonth"],
            "deepWorkByDay":   m["deepWorkByDay"],
        })

    member_metrics.sort(key=lambda x: x["completionRate"], reverse=True)

    return {
        "teamId":          team_id,
        "memberCount":     len(members),
        "totalTasks":      total_tasks,
        "completedTasks":  completed_tasks,
        "completionRate":  round(completion_rate, 1),
        "hoursWorked":     hours_worked,
        "avgProductivity": avg_productivity,
        "memberMetrics":   member_metrics,
    }


@app.get("/api/dashboard/my-metrics")
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

    start = parse_date(start_date) if start_date else None
    end   = parse_date(end_date)   if end_date   else None

    metrics = calculate_user_metrics(db, user.id, start, end)

    # --- teamPercentile: percentil del usuario dentro de su equipo ---
    team_percentile = None
    if user.team_id:
        members = db.query(User).filter(
            User.team_id == user.team_id,
            User.is_active == True,
        ).all()
        if len(members) > 1:
            all_rates = []
            for m in members:
                m_metrics = calculate_user_metrics(db, m.id, start, end)
                all_rates.append((m.id, m_metrics["completionRate"]))

            my_rate    = metrics["completionRate"]
            beaten     = sum(1 for uid, r in all_rates if r < my_rate)
            team_percentile = round((beaten / (len(all_rates) - 1)) * 100, 1)

    s = start or (date.today() - timedelta(days=30))
    e = end   or date.today()

    return {
        "userId":      user.id,
        "displayName": user.display_name,
        "period": {
            "startDate": s.isoformat(),
            "endDate":   e.isoformat(),
        },
        # Campos planos — exactamente lo que espera MyMetricsView
        **metrics,
        "teamPercentile": team_percentile,
    }


@app.get("/api/dashboard/user/{user_id}/metrics")
async def get_user_metrics(
    user_id: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    # ... validaciones existentes sin cambios ...

    metrics = calculate_user_metrics(db, user_id, start, end)

    s = start or (date.today() - timedelta(days=30))
    e = end   or date.today()

    return {
        "userId":      user_id,
        "displayName": target_user.display_name,
        "teamId":      target_user.team_id,
        "period": {
            "startDate": s.isoformat(),
            "endDate":   e.isoformat(),
        },
        **metrics,
        "teamPercentile": None,  # no aplica en vista individual por líder
    }


@app.get("/api/dashboard/team/{team_id}/metrics")
async def get_team_metrics(
    team_id: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Get aggregated metrics for a team."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")

    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Check access: admin, team leader, or member of team
    if current_user.role != "admin":
        if team.leader_id != current_user.id and current_user.team_id != team_id:
            raise HTTPException(status_code=403, detail="Access denied")

    start = parse_date(start_date) if start_date else None
    end = parse_date(end_date) if end_date else None

    metrics = calculate_team_metrics(db, team_id, start, end)
    metrics["teamName"] = team.name
    metrics["isTechTeam"] = team.is_tech_team

    return metrics


@app.get("/api/dashboard/my-team")
async def get_my_team_metrics(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Get metrics for the team I lead."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Find team where user is leader
    team = db.query(Team).filter(Team.leader_id == current_user.id).first()
    if not team:
        # If not a leader, return own team metrics if in a team
        if current_user.team_id:
            team = db.query(Team).filter(Team.id == current_user.team_id).first()
        if not team:
            raise HTTPException(status_code=404, detail="No team found")

    start = parse_date(start_date) if start_date else None
    end = parse_date(end_date) if end_date else None

    metrics = calculate_team_metrics(db, team.id, start, end)
    metrics["teamName"] = team.name
    metrics["isTechTeam"] = team.is_tech_team
    metrics["isLeader"] = team.leader_id == current_user.id

    return metrics


@app.get("/api/dashboard/compare")
async def get_comparison_metrics(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Get comparison metrics within team (tech team only)."""
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

    # Only tech team can see comparisons
    if not team.is_tech_team:
        raise HTTPException(status_code=403, detail="Comparison only available for tech team")

    start = parse_date(start_date) if start_date else None
    end = parse_date(end_date) if end_date else None

    # Get all team members' metrics
    members = db.query(User).filter(User.team_id == team.id, User.is_active == True).all()

    all_metrics = []
    for member in members:
        metrics = calculate_user_metrics(db, member.id, start, end)
        all_metrics.append({
            "userId": member.id,
            "displayName": member.display_name,
            "isCurrentUser": member.id == current_user.id,
            "metrics": metrics,
        })

    # Calculate team averages
    if all_metrics:
        avg_completion = sum(m["metrics"]["completionRate"] for m in all_metrics) / len(all_metrics)
        avg_hours = sum(m["metrics"]["totalHours"] for m in all_metrics) / len(all_metrics)
        avg_productivity = sum(m["metrics"]["productivity"] for m in all_metrics) / len(all_metrics)
    else:
        avg_completion = avg_hours = avg_productivity = 0

    return {
        "teamId": team.id,
        "teamName": team.name,
        "period": {
            "startDate": (start or date.today() - timedelta(days=30)).isoformat(),
            "endDate": (end or date.today()).isoformat(),
        },
        "teamAverages": {
            "completionRate": round(avg_completion, 1),
            "totalHours": round(avg_hours, 1),
            "productivity": round(avg_productivity, 2),
        },
        "members": all_metrics,
    }


# =============================================================================
# ROUTES - SKILLS (Tech team only)
# =============================================================================

class SkillScore(BaseModel):
    skillId: int
    score: int = Field(ge=1, le=10)


class SkillEndorsementCreate(BaseModel):
    score: int = Field(ge=1, le=10)
    comment: Optional[str] = None


@app.get("/api/skills")
async def get_all_skills(
    tech_only: bool = False,
    db: Session = Depends(get_db),
):
    """Get all available skills."""
    query = db.query(Skill)
    if tech_only:
        query = query.filter(Skill.is_tech_only == True)

    skills = query.all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "category": s.category,
            "description": s.description,
            "isTechOnly": s.is_tech_only,
        }
        for s in skills
    ]


@app.get("/api/users/{user_id}/skills")
async def get_user_skills(
    user_id: int,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Get skills for a user."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")

    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if user is in tech team
    if target_user.team_id:
        team = db.query(Team).filter(Team.id == target_user.team_id).first()
        if not team or not team.is_tech_team:
            raise HTTPException(status_code=403, detail="Skills only available for tech team")

    user_skills = db.query(UserSkill).filter(UserSkill.user_id == user_id).all()

    return [
        {
            "skillId": us.skill_id,
            "skillName": us.skill.name,
            "category": us.skill.category,
            "selfScore": us.self_score,
            "avgEndorsementScore": float(us.avg_endorsement_score),
            "totalEndorsements": us.total_endorsements,
        }
        for us in user_skills
    ]


@app.post("/api/users/me/skills")
async def update_my_skills(
    skills: List[SkillScore],
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Update current user's skill self-scores."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Check if user is in tech team
    if current_user.team_id:
        team = db.query(Team).filter(Team.id == current_user.team_id).first()
        if not team or not team.is_tech_team:
            raise HTTPException(status_code=403, detail="Skills only available for tech team")

    for skill_data in skills:
        user_skill = db.query(UserSkill).filter(
            UserSkill.user_id == current_user.id,
            UserSkill.skill_id == skill_data.skillId,
        ).first()

        if user_skill:
            user_skill.self_score = skill_data.score
        else:
            user_skill = UserSkill(
                user_id=current_user.id,
                skill_id=skill_data.skillId,
                self_score=skill_data.score,
            )
            db.add(user_skill)

    db.commit()

    return {"success": True}


@app.post("/api/users/{user_id}/skills/{skill_id}/endorse")
async def endorse_skill(
    user_id: int,
    skill_id: int,
    endorsement: SkillEndorsementCreate,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Endorse a teammate's skill."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")

    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot endorse your own skills")

    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check both users are in same tech team
    if current_user.team_id != target_user.team_id:
        raise HTTPException(status_code=403, detail="Can only endorse teammates")

    if current_user.team_id:
        team = db.query(Team).filter(Team.id == current_user.team_id).first()
        if not team or not team.is_tech_team:
            raise HTTPException(status_code=403, detail="Endorsements only available for tech team")

    # Get or create user skill
    user_skill = db.query(UserSkill).filter(
        UserSkill.user_id == user_id,
        UserSkill.skill_id == skill_id,
    ).first()

    if not user_skill:
        user_skill = UserSkill(
            user_id=user_id,
            skill_id=skill_id,
            self_score=5,
        )
        db.add(user_skill)
        db.flush()

    # Check if already endorsed
    existing = db.query(SkillEndorsement).filter(
        SkillEndorsement.user_skill_id == user_skill.id,
        SkillEndorsement.endorsed_by == current_user.id,
    ).first()

    if existing:
        existing.score = endorsement.score
        existing.comment = endorsement.comment
    else:
        new_endorsement = SkillEndorsement(
            user_skill_id=user_skill.id,
            endorsed_by=current_user.id,
            score=endorsement.score,
            comment=endorsement.comment,
        )
        db.add(new_endorsement)

    db.commit()

    # Recalculate average
    endorsements = db.query(SkillEndorsement).filter(
        SkillEndorsement.user_skill_id == user_skill.id
    ).all()

    if endorsements:
        avg_score = sum(e.score for e in endorsements) / len(endorsements)
        user_skill.avg_endorsement_score = avg_score
        user_skill.total_endorsements = len(endorsements)
        db.commit()

    return {"success": True}


@app.get("/api/dashboard/skills-comparison")
async def get_skills_comparison(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Get skills comparison for tech team."""
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

    # Get all skills
    skills = db.query(Skill).filter(Skill.is_tech_only == True).all()

    # Get team members
    members = db.query(User).filter(User.team_id == team.id, User.is_active == True).all()

    comparison = []
    for skill in skills:
        skill_data = {
            "skillId": skill.id,
            "skillName": skill.name,
            "category": skill.category,
            "members": [],
        }

        total_score = 0
        count = 0

        for member in members:
            user_skill = db.query(UserSkill).filter(
                UserSkill.user_id == member.id,
                UserSkill.skill_id == skill.id,
            ).first()

            if user_skill:
                score = float(user_skill.avg_endorsement_score) if user_skill.total_endorsements > 0 else user_skill.self_score
                skill_data["members"].append({
                    "userId": member.id,
                    "displayName": member.display_name,
                    "isCurrentUser": member.id == current_user.id,
                    "score": score,
                    "endorsements": user_skill.total_endorsements,
                })
                total_score += score
                count += 1

        skill_data["teamAverage"] = round(total_score / count, 1) if count > 0 else 0
        comparison.append(skill_data)

    return {
        "teamId": team.id,
        "teamName": team.name,
        "skills": comparison,
    }


# =============================================================================

# =============================================================================
# ROUTES - ADMIN / LEADER MANAGEMENT
# =============================================================================

class UserUpdate(BaseModel):
    displayName: Optional[str] = None
    email: Optional[str] = None
    jobTitle: Optional[str] = None
    teamId: Optional[int] = None
    role: Optional[str] = None


class TeamCreate(BaseModel):
    name: str
    parentTeamId: Optional[int] = None
    isTechTeam: bool = False


class TeamUpdate(BaseModel):
    name: Optional[str] = None
    leaderId: Optional[int] = None
    parentTeamId: Optional[int] = None
    isTechTeam: Optional[bool] = None


@app.get("/api/admin/users")
async def get_all_users(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Get all users (leaders/admins only)."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")

    if current_user.role not in ["leader", "admin"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Leaders see only their team, admins see all
    if current_user.role == "admin":
        users = db.query(User).filter(User.is_active == True).all()
    else:
        # Leader sees their team members
        team = db.query(Team).filter(Team.leader_id == current_user.id).first()
        if team:
            users = db.query(User).filter(User.team_id == team.id, User.is_active == True).all()
        else:
            users = [current_user]

    return [
        {
            "id": u.id,
            "ncUserId": u.nc_user_id,
            "displayName": u.display_name,
            "email": u.email,
            "jobTitle": u.job_title if hasattr(u, 'job_title') else None,
            "teamId": u.team_id,
            "role": u.role,
            "isActive": u.is_active,
        }
        for u in users
    ]


@app.patch("/api/admin/users/{user_id}")
async def update_user(
    user_id: int,
    user_update: UserUpdate,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Update user (leaders can update team members, admins can update anyone)."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")

    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check permissions
    if current_user.role == "admin":
        pass  # Admin can update anyone
    elif current_user.role == "leader":
        # Leader can only update their team members
        team = db.query(Team).filter(Team.leader_id == current_user.id).first()
        if not team or target_user.team_id != team.id:
            raise HTTPException(status_code=403, detail="Can only update your team members")
        # Leaders cannot change roles to admin
        if user_update.role == "admin":
            raise HTTPException(status_code=403, detail="Cannot assign admin role")
    else:
        raise HTTPException(status_code=403, detail="Access denied")

    update_data = user_update.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        if field == "displayName":
            target_user.display_name = value
        elif field == "jobTitle":
            target_user.job_title = value
        elif field == "teamId":
            target_user.team_id = value
        elif hasattr(target_user, field):
            setattr(target_user, field, value)

    target_user.updated_at = datetime.utcnow()
    db.commit()

    return {"success": True}


@app.post("/api/admin/teams")
async def create_team(
    team_data: TeamCreate,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Create a new team (admin only)."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    current_user = await get_current_user(authorization, db)
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    team = Team(
        name=team_data.name,
        parent_team_id=team_data.parentTeamId,
        is_tech_team=team_data.isTechTeam,
    )
    db.add(team)
    db.commit()
    db.refresh(team)

    return {
        "success": True,
        "team": {
            "id": team.id,
            "name": team.name,
            "parentTeamId": team.parent_team_id,
            "isTechTeam": team.is_tech_team,
        }
    }


@app.patch("/api/admin/teams/{team_id}")
async def update_team(
    team_id: int,
    team_update: TeamUpdate,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Update a team (admin only, or leader of that team for limited fields)."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")

    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Check permissions
    is_admin = current_user.role == "admin"
    is_team_leader = team.leader_id == current_user.id

    if not is_admin and not is_team_leader:
        raise HTTPException(status_code=403, detail="Access denied")

    update_data = team_update.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        # Only admin can change leader or parent team
        if field in ["leaderId", "parentTeamId", "isTechTeam"] and not is_admin:
            raise HTTPException(status_code=403, detail=f"Only admin can change {field}")

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


@app.delete("/api/admin/teams/{team_id}")
async def delete_team(
    team_id: int,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Delete a team (admin only)."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    current_user = await get_current_user(authorization, db)
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    # Check if team has members
    member_count = db.query(User).filter(User.team_id == team_id).count()
    if member_count > 0:
        raise HTTPException(status_code=400, detail=f"Team has {member_count} members. Reassign them first.")

    db.delete(team)
    db.commit()

    return {"success": True}


@app.post("/api/admin/teams/{team_id}/add-member")
async def add_team_member(
    team_id: int,
    authorization: Annotated[str | None, Header()] = None,
    user_id: Optional[int] = None,
    nc_user_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Add a user to a team (admin or team leader). Accepts user_id (int) or nc_user_id (string)."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")

    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    is_admin = current_user.role == "admin"
    is_team_leader = team.leader_id == current_user.id

    if not is_admin and not is_team_leader:
        raise HTTPException(status_code=403, detail="Access denied")

    if nc_user_id:
        target_user = db.query(User).filter(User.nc_user_id == nc_user_id).first()
    elif user_id is not None:
        target_user = db.query(User).filter(User.id == user_id).first()
    else:
        raise HTTPException(status_code=400, detail="Provide user_id or nc_user_id")

    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    target_user.team_id = team_id
    db.commit()

    return {"success": True}


@app.post("/api/admin/teams/{team_id}/remove-member")
async def remove_team_member(
    team_id: int,
    user_id: int,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Remove a user from a team (admin or team leader)."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    current_user = await get_current_user(authorization, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Invalid token")

    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    is_admin = current_user.role == "admin"
    is_team_leader = team.leader_id == current_user.id

    if not is_admin and not is_team_leader:
        raise HTTPException(status_code=403, detail="Access denied")

    target_user = db.query(User).filter(User.id == user_id, User.team_id == team_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not in this team")

    # Cannot remove leader
    if team.leader_id == user_id:
        raise HTTPException(status_code=400, detail="Cannot remove team leader. Assign new leader first.")

    target_user.team_id = None
    db.commit()

    return {"success": True}


@app.post("/api/admin/users/{user_id}/set-role")
async def set_user_role(
    user_id: int,
    role: str,
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Set user role (admin only)."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    current_user = await get_current_user(authorization, db)
    if not current_user or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    if role not in ["member", "leader", "admin"]:
        raise HTTPException(status_code=400, detail="Invalid role")

    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    target_user.role = role
    db.commit()

    return {"success": True}


# =============================================================================
# =============================================================================
# ROUTES - TEAM JOB TITLES
# =============================================================================

@app.get("/api/teams/{team_id}/job-titles")
async def get_team_job_titles(
    team_id: int,
    db: Session = Depends(get_db),
):
    """Get available job titles for a team."""
    from sqlalchemy import text

    result = db.execute(text("""
        SELECT job_title, is_leader_title
        FROM team_job_titles
        WHERE team_id = :team_id
        ORDER BY is_leader_title DESC, job_title
    """), {"team_id": team_id}).fetchall()

    return [
        {"jobTitle": row[0], "isLeaderTitle": bool(row[1])}
        for row in result
    ]


@app.get("/api/job-titles")
async def get_all_job_titles(
    db: Session = Depends(get_db),
):
    """Get all job titles grouped by team."""
    from sqlalchemy import text

    result = db.execute(text("""
        SELECT t.id, t.name, tj.job_title, tj.is_leader_title
        FROM team_job_titles tj
        JOIN teams t ON tj.team_id = t.id
        ORDER BY t.id, tj.is_leader_title DESC, tj.job_title
    """)).fetchall()

    teams_dict = {}
    for row in result:
        team_id, team_name, job_title, is_leader = row
        if team_id not in teams_dict:
            teams_dict[team_id] = {
                "teamId": team_id,
                "teamName": team_name,
                "jobTitles": []
            }
        teams_dict[team_id]["jobTitles"].append({
            "jobTitle": job_title,
            "isLeaderTitle": bool(is_leader)
        })

    return list(teams_dict.values())


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)