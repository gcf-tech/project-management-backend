from __future__ import annotations

import re
from datetime import date as date_type, datetime, timezone
from enum import Enum as PyEnum
from typing import Optional, List
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from app.core.config import BUSINESS_TIMEZONE
from app.schemas.base import UTCModel


_BUSINESS_TZ = ZoneInfo(BUSINESS_TIMEZONE)
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _coerce_completed_at(v):
    """Normalize completedAt input to a tz-aware UTC datetime.

    Accepts:
      - None
      - tz-aware datetime / date
      - date-only string ('YYYY-MM-DD'): anchored to midnight in BUSINESS_TIMEZONE
        so the calendar day the user picked is preserved when converted to UTC.
      - ISO 8601 string with offset (or trailing 'Z').

    Rejects naive datetime strings explicitly. This guards against
    `datetime.fromisoformat` silently accepting 'YYYY-MM-DD' on Python 3.11+
    and returning a naive value (which AwareDatetime later rejects with a
    less actionable error).
    """
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("completedAt must include timezone offset")
        return v
    if isinstance(v, date_type):
        return datetime(v.year, v.month, v.day, tzinfo=_BUSINESS_TZ).astimezone(timezone.utc)
    if isinstance(v, str):
        if _DATE_ONLY_RE.match(v):
            d = date_type.fromisoformat(v)
            return datetime(d.year, d.month, d.day, tzinfo=_BUSINESS_TZ).astimezone(timezone.utc)
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"Cannot parse completedAt: {v!r}")
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError("completedAt must include timezone offset")
        return dt
    return v


class ActivityType(str, PyEnum):
    meeting = "meeting"
    email = "email"
    planning = "planning"
    other = "other"


class TaskType(str, PyEnum):
    project = "project"
    task = "task"


def _parse_date_str(date_str: Optional[str]) -> Optional[date_type]:
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "")).date()
    except Exception:
        return None


def _validate_retroactive_fields(is_retroactive, completed_at, startDate):
    """Shared validator logic for TaskCreate and ActivityCreate."""
    if not is_retroactive:
        return

    today = date_type.today()

    if completed_at is None:
        raise ValueError("completed_at is required when is_retroactive=True")

    completed_date = completed_at.date() if isinstance(completed_at, datetime) else completed_at

    if completed_date > today:
        raise ValueError("completed_at cannot be in the future")

    start_date = _parse_date_str(startDate)
    if start_date and start_date > completed_date:
        raise ValueError("start_date must be on or before completed_at")


class TaskCreate(UTCModel):
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
    clientOpId: Optional[str] = Field(default=None, max_length=64)
    is_retroactive: bool = False
    completed_at: Optional[AwareDatetime] = None

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )

    @field_validator("completed_at", mode="before")
    @classmethod
    def parse_completed_at(cls, v):
        return _coerce_completed_at(v)

    @model_validator(mode="after")
    def validate_retroactive(self):
        _validate_retroactive_fields(
            self.is_retroactive, self.completed_at, self.startDate
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
    activityType: Optional[str] = None
    assignedTo: Optional[str] = None
    difficulty: Optional[int] = None
    difficultyReason: Optional[str] = None
    wasDifficult: Optional[bool] = None
    subtasks: Optional[List[dict]] = None
    observations: Optional[List[dict]] = None


class ActivityCreate(UTCModel):
    title: str
    description: Optional[str] = ""
    type: ActivityType = ActivityType.other
    priority: Optional[str] = "medium"
    startDate: Optional[str] = None
    deadline: Optional[str] = None
    assignedTo: Optional[str] = None
    clientOpId: Optional[str] = Field(default=None, max_length=64)
    is_retroactive: bool = False
    completed_at: Optional[AwareDatetime] = None

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )

    @field_validator("completed_at", mode="before")
    @classmethod
    def parse_completed_at(cls, v):
        return _coerce_completed_at(v)

    @model_validator(mode="after")
    def validate_retroactive(self):
        _validate_retroactive_fields(
            self.is_retroactive, self.completed_at, self.startDate
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
    assignedTo: Optional[str] = None
    observations: Optional[List[dict]] = None


class ColumnUpdate(BaseModel):
    column: str
