from typing import Optional, List
from pydantic import BaseModel, Field


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

class TimeLogCreate(BaseModel):
    logDate: str
    seconds: int
    clientOpId: Optional[str] = None

class TimeLogPatch(BaseModel):
    seconds: int
    clientOpId: Optional[str] = None