from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from enum import Enum
from typing import List, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator

_BOGOTA = ZoneInfo("America/Bogota")


class PeriodType(str, Enum):
    WEEK = "WEEK"
    MONTH = "MONTH"
    QUARTER = "QUARTER"
    CUSTOM = "CUSTOM"


class ScopeMode(str, Enum):
    FULL = "FULL"
    TEAMS = "TEAMS"
    EMPLOYEES = "EMPLOYEES"


class PeriodFilter(BaseModel):
    type: PeriodType
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class ScopeFilter(BaseModel):
    mode: ScopeMode
    team_ids: Optional[List[int]] = None
    user_ids: Optional[List[int]] = None


class ReportOptions(BaseModel):
    include_individual_sheets: bool = False
    include_team_sheets: bool = True
    top_n: int = Field(default=10, ge=5, le=25)


class ReportRequest(BaseModel):
    """Entry-point schema for report generation requests.

    Example — CUSTOM period with invalid date order raises ValidationError::

        from pydantic import ValidationError
        try:
            ReportRequest(
                period={"type": "CUSTOM", "start_date": "2025-04-30", "end_date": "2025-04-01"},
                scope={"mode": "FULL"},
                options={},
            )
        except ValidationError as exc:
            assert "end_date must be" in str(exc)
    """

    period: PeriodFilter
    scope: ScopeFilter
    options: ReportOptions = Field(default_factory=ReportOptions)

    @model_validator(mode="after")
    def _validate_cross_fields(self) -> ReportRequest:
        self._resolve_period()
        self._validate_scope()
        return self

    def _resolve_period(self) -> None:
        p = self.period
        today = datetime.now(_BOGOTA).date()

        if p.type == PeriodType.CUSTOM:
            if p.start_date is None or p.end_date is None:
                raise ValueError(
                    "start_date and end_date are required when period.type is CUSTOM"
                )
            if p.end_date < p.start_date:
                raise ValueError("end_date must be >= start_date")
            if (p.end_date - p.start_date).days > 366:
                raise ValueError("CUSTOM period range cannot exceed 366 days")

        elif p.type == PeriodType.WEEK:
            monday = today - timedelta(days=today.weekday())
            p.start_date = monday
            p.end_date = monday + timedelta(days=6)

        elif p.type == PeriodType.MONTH:
            last_day = calendar.monthrange(today.year, today.month)[1]
            p.start_date = today.replace(day=1)
            p.end_date = today.replace(day=last_day)

        elif p.type == PeriodType.QUARTER:
            q_start_month = ((today.month - 1) // 3) * 3 + 1
            q_end_month = q_start_month + 2
            last_day = calendar.monthrange(today.year, q_end_month)[1]
            p.start_date = today.replace(month=q_start_month, day=1)
            p.end_date = today.replace(month=q_end_month, day=last_day)

    def _validate_scope(self) -> None:
        s = self.scope
        if s.mode == ScopeMode.TEAMS and not s.team_ids:
            raise ValueError("team_ids must not be empty when scope.mode is TEAMS")
        if s.mode == ScopeMode.EMPLOYEES and not s.user_ids:
            raise ValueError("user_ids must not be empty when scope.mode is EMPLOYEES")
        if s.mode == ScopeMode.FULL:
            if s.team_ids:
                raise ValueError("team_ids must be None or empty when scope.mode is FULL")
            if s.user_ids:
                raise ValueError("user_ids must be None or empty when scope.mode is FULL")
