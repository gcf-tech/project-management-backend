from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional

from pydantic import BaseModel


class OrgMetricsDTO(BaseModel):
    total_employees: int
    total_teams: int
    total_hours: float
    avg_iel_org: float
    period_label: str
    generated_at: datetime
    generated_by: str
    scope_label: str


class EmployeeMetricsDTO(BaseModel):
    user_id: int
    full_name: str
    team_name: str
    role: str
    hours_worked: float
    projects_assigned: int
    projects_closed: int
    tasks_closed: int
    subtasks_closed: int
    activities_count: int
    completion_rate: float
    iel: float
    avg_progress: float
    sla_avg_days: float


class TeamMetricsDTO(BaseModel):
    team_id: int
    team_name: str
    leader_name: str
    members_count: int
    total_hours: float
    avg_hours_per_member: float
    tasks_closed: int
    subtasks_closed: int
    activities_count: int
    projects_active: int
    projects_closed: int
    avg_iel: float
    avg_progress: float
    weighted_completion_rate: float


class ProjectMetricsDTO(BaseModel):
    project_id: str
    project_name: str
    status: str
    avg_progress: float
    hours_invested: float
    tasks_closed: int
    deadline: Optional[date] = None
    days_vs_deadline: int


class EmployeeDetailDTO(BaseModel):
    header: EmployeeMetricsDTO
    projects: List[ProjectMetricsDTO]
    tasks_by_status: Dict[str, dict]
    activities_by_type: Dict[str, dict]


class TeamDetailDTO(BaseModel):
    header: TeamMetricsDTO
    members: List[EmployeeMetricsDTO]
    projects: List[ProjectMetricsDTO]
