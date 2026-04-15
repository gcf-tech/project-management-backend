from typing import Optional
from pydantic import BaseModel, Field


class OAuthCallback(BaseModel):
    code: str
    redirect_uri: str


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


class SkillScore(BaseModel):
    skillId: int
    score: int = Field(ge=1, le=10)


class SkillEndorsementCreate(BaseModel):
    score: int = Field(ge=1, le=10)
    comment: Optional[str] = None