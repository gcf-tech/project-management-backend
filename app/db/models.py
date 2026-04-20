from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Boolean,
    Enum, Date, DateTime, DECIMAL, ForeignKey, CheckConstraint, Time, Index
)
from sqlalchemy.orm import relationship
from app.db.database import Base


class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    leader_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    parent_team_id = Column(Integer, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    is_tech_team = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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

    user_skill = relationship("UserSkill", back_populates="endorsements")
    endorser = relationship("User")

    __table_args__ = (
        CheckConstraint("score BETWEEN 1 AND 10", name="chk_endorsement_score"),
    )


class UserPreferences(Base):
    __tablename__ = "user_preferences"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    week_start_day = Column(Integer, default=1, nullable=False)
    week_end_day = Column(Integer, default=5, nullable=False)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)

    user = relationship("User")


class WeeklyBlock(Base):
    __tablename__ = "weekly_blocks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    week_start = Column(Date, nullable=False)
    day_of_week = Column(Integer, nullable=False)
    block_type = Column(Enum("task", "activity", "personal"), nullable=False)
    task_id = Column(String(50), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    activity_id = Column(String(50), ForeignKey("activities.id", ondelete="SET NULL"), nullable=True)
    title = Column(String(200), nullable=True)
    color = Column(String(20), nullable=True)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)

    user = relationship("User")
    task = relationship("Task")
    activity = relationship("Activity")

    __table_args__ = (
        CheckConstraint("end_time > start_time", name="chk_weekly_block_end_after_start"),
        CheckConstraint(
            "(block_type != 'task' OR task_id IS NOT NULL)",
            name="chk_weekly_block_task_id",
        ),
        CheckConstraint(
            "(block_type != 'activity' OR activity_id IS NOT NULL)",
            name="chk_weekly_block_activity_id",
        ),
        CheckConstraint(
            "(block_type != 'personal' OR title IS NOT NULL)",
            name="chk_weekly_block_personal_title",
        ),
        Index("idx_weekly_blocks_user_week", "user_id", "week_start"),
    )