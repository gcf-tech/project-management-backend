"""
Database connection and models for Activity Tracker
"""

import os
from datetime import datetime, date
from typing import Optional, List
from contextlib import asynccontextmanager

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Boolean,
    Enum, Date, DateTime, DECIMAL, ForeignKey, Index, CheckConstraint
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.pool import QueuePool

# ============================================================================
# DATABASE CONFIGURATION
# ============================================================================

DB_HOST = os.getenv("DB_HOST", "db-marcela-sandbox")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "p4ssw0rd")
DB_NAME = os.getenv("DB_NAME", "activity_tracker")

DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=5,
    max_overflow=10,
    pool_recycle=3600,
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ============================================================================
# DEPENDENCY
# ============================================================================

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@asynccontextmanager
async def get_db_context():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============================================================================
# MODELS
# ============================================================================

class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    leader_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    parent_team_id = Column(Integer, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    is_tech_team = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    members = relationship("User", back_populates="team", foreign_keys="User.team_id")
    leader = relationship("User", foreign_keys=[leader_id], post_update=True)
    parent_team = relationship("Team", remote_side=[id], backref="sub_teams")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nc_user_id = Column(String(100), nullable=False, unique=True)
    display_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True)
    job_title = Column(String(100), nullable=True)
    team_id = Column(Integer, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    role = Column(Enum("member", "leader", "admin"), default="member")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    team = relationship("Team", back_populates="members", foreign_keys=[team_id])
    tasks = relationship("Task", back_populates="owner", foreign_keys="Task.owner_id")
    activities = relationship("Activity", back_populates="owner", foreign_keys="Activity.owner_id")
    skills = relationship("UserSkill", back_populates="user")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String(50), primary_key=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assigned_to = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    column_status = Column(Enum("actively-working", "working-now", "completed"), default="actively-working")
    type = Column(Enum("project", "task"), default="project")
    priority = Column(Enum("low", "medium", "high", "urgent"), nullable=True)

    start_date = Column(Date, nullable=True)
    deadline = Column(Date, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    progress = Column(Integer, default=0)
    time_spent = Column(Integer, default=0)

    difficulty = Column(Integer, nullable=True)
    difficulty_reason = Column(Text, nullable=True)
    was_difficult = Column(Boolean, default=False)

    deck_card_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)
    deleted_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    owner = relationship("User", back_populates="tasks", foreign_keys=[owner_id])
    assignee = relationship("User", foreign_keys=[assigned_to])
    subtasks = relationship("Subtask", back_populates="task", cascade="all, delete-orphan")
    time_logs = relationship("TimeLog", back_populates="task", cascade="all, delete-orphan")
    observations = relationship("Observation", back_populates="task", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("difficulty BETWEEN 1 AND 10", name="chk_difficulty"),
    )


class Activity(Base):
    __tablename__ = "activities"

    id = Column(String(50), primary_key=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assigned_to = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    type = Column(Enum("meeting", "training", "support", "review", "planning", "other"), default="other")
    priority = Column(Enum("low", "medium", "high", "urgent"), nullable=True)

    start_date = Column(Date, nullable=True)
    deadline = Column(Date, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    progress = Column(Integer, default=0)
    time_spent = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)
    deleted_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    owner = relationship("User", back_populates="activities", foreign_keys=[owner_id])
    assignee = relationship("User", foreign_keys=[assigned_to])
    time_logs = relationship("TimeLog", back_populates="activity", cascade="all, delete-orphan")
    observations = relationship("Observation", back_populates="activity", cascade="all, delete-orphan")


class Subtask(Base):
    __tablename__ = "subtasks"

    id = Column(String(50), primary_key=True)
    task_id = Column(String(50), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    text = Column(String(500), nullable=False)
    completed = Column(Boolean, default=False)
    time_spent = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    task = relationship("Task", back_populates="subtasks")


class TimeLog(Base):
    __tablename__ = "time_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    task_id = Column(String(50), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True)
    activity_id = Column(String(50), ForeignKey("activities.id", ondelete="CASCADE"), nullable=True)
    log_date = Column(Date, nullable=False)
    seconds = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User")
    task = relationship("Task", back_populates="time_logs")
    activity = relationship("Activity", back_populates="time_logs")


class Observation(Base):
    __tablename__ = "observations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(50), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True)
    activity_id = Column(String(50), ForeignKey("activities.id", ondelete="CASCADE"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    task = relationship("Task", back_populates="observations")
    activity = relationship("Activity", back_populates="observations")
    user = relationship("User")


class Skill(Base):
    __tablename__ = "skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    category = Column(Enum("frontend", "backend", "devops", "data", "design", "soft_skill", "other"), default="other")
    description = Column(String(255), nullable=True)
    is_tech_only = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserSkill(Base):
    __tablename__ = "user_skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    skill_id = Column(Integer, ForeignKey("skills.id", ondelete="CASCADE"), nullable=False)
    self_score = Column(Integer, default=5)
    avg_endorsement_score = Column(DECIMAL(3, 1), default=0)
    total_endorsements = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="skills")
    skill = relationship("Skill")
    endorsements = relationship("SkillEndorsement", back_populates="user_skill", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("self_score BETWEEN 1 AND 10", name="chk_self_score"),
    )


class SkillEndorsement(Base):
    __tablename__ = "skill_endorsements"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_skill_id = Column(Integer, ForeignKey("user_skills.id", ondelete="CASCADE"), nullable=False)
    endorsed_by = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    score = Column(Integer, nullable=False)
    comment = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user_skill = relationship("UserSkill", back_populates="endorsements")
    endorser = relationship("User")

    __table_args__ = (
        CheckConstraint("score BETWEEN 1 AND 10", name="chk_endorsement_score"),
    )


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_or_create_user(db, nc_user_id: str, display_name: str, email: str = None) -> User:
    """Get existing user or create new one from Nextcloud data."""
    user = db.query(User).filter(User.nc_user_id == nc_user_id).first()
    if not user:
        user = User(
            nc_user_id=nc_user_id,
            display_name=display_name,
            email=email,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def serialize_task(task: Task) -> dict:
    """Convert Task model to dict for API response."""
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
            {"date": o.created_at.isoformat(), "text": o.text}
            for o in task.observations
        ],
        "timeLog": [
            {"date": t.log_date.isoformat(), "seconds": t.seconds}
            for t in task.time_logs
        ],
        "createdAt": task.created_at.isoformat() if task.created_at else None,
        "updatedAt": task.updated_at.isoformat() if task.updated_at else None,
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
            {"date": o.created_at.isoformat(), "text": o.text}
            for o in activity.observations
        ],
        "timeLog": [
            {"date": t.log_date.isoformat(), "seconds": t.seconds}
            for t in activity.time_logs
        ],
        "createdAt": activity.created_at.isoformat() if activity.created_at else None,
        "updatedAt": activity.updated_at.isoformat() if activity.updated_at else None,
    }