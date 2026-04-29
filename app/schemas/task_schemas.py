from __future__ import annotations

from datetime import date as date_type, datetime, timezone
from enum import Enum as PyEnum
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel


class ActivityType(str, PyEnum):
    meeting = "meeting"
    email = "email"
    planning = "planning"
    other = "other"


class TaskType(str, PyEnum):
    project = "project"
    task = "task"


class SubtaskCreate(BaseModel):
    id: Optional[str] = None
    text: str
    completed: bool = False
    timeSpent: int = 0


class TimeLogEntry(BaseModel):
    log_date: date_type
    hours: float = Field(gt=0, le=24)

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


def _parse_date_str(date_str: Optional[str]) -> Optional[date_type]:
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "")).date()
    except Exception:
        return None


def _validate_retroactive_fields(is_retroactive, completed_at, startDate, time_logs):
    """Shared validator logic for TaskCreate and ActivityCreate."""
    if not is_retroactive:
        return

    today = date_type.today()

    if completed_at is None:
        raise ValueError("completed_at is required when is_retroactive=True")

    # Normalize naive datetimes to UTC so comparisons don't fail
    if isinstance(completed_at, datetime) and completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)

    completed_date = completed_at.date() if isinstance(completed_at, datetime) else completed_at

    if completed_date > today:
        raise ValueError("completed_at cannot be in the future")

    if not time_logs:
        raise ValueError("timeLogs is required when isRetroactive=True")

    start_date = _parse_date_str(startDate)
    if start_date and start_date > completed_date:
        raise ValueError("start_date must be on or before completed_at")

    seen_dates: set = set()
    for entry in (time_logs or []):
        if entry.log_date > today:
            raise ValueError(f"time_log log_date {entry.log_date} cannot be in the future")
        if entry.log_date > completed_date:
            raise ValueError(
                f"time_log log_date {entry.log_date} is after completed_at ({completed_date})"
            )
        if start_date and entry.log_date < start_date:
            raise ValueError(
                f"time_log log_date {entry.log_date} is before start_date ({start_date})"
            )
        if entry.log_date in seen_dates:
            raise ValueError(f"Duplicate log_date {entry.log_date} in time_logs")
        seen_dates.add(entry.log_date)


class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    column: str = "actively-working"
    type: TaskType = TaskType.project
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
    is_retroactive: bool = False
    completed_at: Optional[datetime] = None
    time_logs: Optional[List[TimeLogEntry]] = Field(default_factory=list)

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )

    @field_validator("completed_at", mode="before")
    @classmethod
    def parse_completed_at(cls, v):
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, date_type):
            return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                pass
            try:
                d = date_type.fromisoformat(v)
                return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
            except ValueError:
                raise ValueError(f"Cannot parse completedAt: {v!r}")
        return v

    @model_validator(mode="after")
    def validate_retroactive(self):
        _validate_retroactive_fields(
            self.is_retroactive, self.completed_at, self.startDate, self.time_logs
        )
        return self


class TaskPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    column: Optional[str] = None
    type: Optional[TaskType] = None
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
    type: ActivityType = ActivityType.other
    priority: Optional[str] = "medium"
    startDate: Optional[str] = None
    deadline: Optional[str] = None
    assignedTo: Optional[str] = None
    is_retroactive: bool = False
    completed_at: Optional[datetime] = None
    time_logs: Optional[List[TimeLogEntry]] = Field(default_factory=list)

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )

    @field_validator("completed_at", mode="before")
    @classmethod
    def parse_completed_at(cls, v):
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, date_type):
            return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
        if isinstance(v, str):
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                pass
            try:
                d = date_type.fromisoformat(v)
                return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
            except ValueError:
                raise ValueError(f"Cannot parse completedAt: {v!r}")
        return v

    @model_validator(mode="after")
    def validate_retroactive(self):
        _validate_retroactive_fields(
            self.is_retroactive, self.completed_at, self.startDate, self.time_logs
        )
        return self


class ActivityPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    type: Optional[ActivityType] = None
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
    startAt: Optional[datetime] = None


class ColumnUpdate(BaseModel):
    column: str


class TimeLogCreate(BaseModel):
    logDate: str
    seconds: int
    clientOpId: Optional[str] = None
    startAt: Optional[datetime] = None


class TimeLogPatch(BaseModel):
    seconds: int
    clientOpId: Optional[str] = None
    startAt: Optional[datetime] = None
