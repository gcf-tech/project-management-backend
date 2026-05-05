"""Orchestrates the full report pipeline: aggregation → builders → workbook bytes."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Dict, List, Tuple

import xlsxwriter

from app.core.reports.exceptions import EmptyScopeError, ReportGenerationError
from app.schemas.report_metrics import EmployeeDetailDTO, TeamDetailDTO
from app.schemas.report_request import ReportRequest, ScopeMode
from app.services.reports.builders.base_builder import WorkbookContext
from app.services.reports.builders.cover_builder import CoverBuilder
from app.services.reports.builders.employee_detail_builder import EmployeeDetailBuilder
from app.services.reports.builders.employees_summary_builder import EmployeesSummaryBuilder
from app.services.reports.builders.index_builder import IndexBuilder
from app.services.reports.builders.team_detail_builder import TeamDetailBuilder
from app.services.reports.builders.teams_summary_builder import TeamsSummaryBuilder
from app.services.reports.chart_factory import ChartFactory
from app.services.reports.metrics_aggregator import MetricsAggregator
from app.services.reports.name_sanitizer import sanitize_sheet_name
from app.services.reports.style_registry import register_formats

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]")


@dataclass
class ReportMeta:
    filename: str
    sheet_count: int
    row_count: int
    generated_at: datetime


class PerformanceReportService:
    def __init__(self, aggregator: MetricsAggregator) -> None:
        self._agg = aggregator

    def generate(self, request: ReportRequest, generated_by) -> Tuple[bytes, ReportMeta]:
        start, end = self._agg.resolve_period(request.period)

        # ── 1. Load all DTOs in a single logical pass ─────────────────────────
        org_metrics = self._agg.get_org_metrics(
            request.period, request.scope, generated_by=generated_by.display_name
        )

        employees = self._agg.get_employees_metrics(request.period, request.scope)
        if not employees:
            raise EmptyScopeError("No employees match the given scope and period")

        teams = self._agg.get_teams_metrics(request.period, request.scope)

        team_details: Dict[int, TeamDetailDTO] = {}
        if request.options.include_team_sheets:
            for team in teams:
                team_details[team.team_id] = self._agg.get_team_detail(
                    team.team_id, request.period
                )

        employee_details: Dict[int, EmployeeDetailDTO] = {}
        if request.options.include_individual_sheets:
            for emp in employees:
                employee_details[emp.user_id] = self._agg.get_employee_detail(
                    emp.user_id, request.period
                )

        # ── 2. Build workbook in memory ───────────────────────────────────────
        buf = BytesIO()
        workbook = xlsxwriter.Workbook(
            buf, {"constant_memory": True, "in_memory": True}
        )

        formats = register_formats(workbook)
        chart_factory = ChartFactory(workbook)

        ctx = WorkbookContext(
            org_metrics=org_metrics,
            employees=employees,
            teams=teams,
            formats=formats,
            chart_factory=chart_factory,
            sanitizer=sanitize_sheet_name,
            request=request,
            employee_details=employee_details,
            team_details=team_details,
        )

        # ── 3. Execute builders in strict order ───────────────────────────────
        try:
            CoverBuilder().build(workbook, ctx)
            IndexBuilder().build(workbook, ctx)
            EmployeesSummaryBuilder().build(workbook, ctx)
            TeamsSummaryBuilder().build(workbook, ctx)

            if request.options.include_team_sheets:
                for team in teams:
                    TeamDetailBuilder(team.team_id).build(workbook, ctx)

            if request.options.include_individual_sheets:
                for emp in employees:
                    EmployeeDetailBuilder(emp.user_id).build(workbook, ctx)

            workbook.close()
        except ReportGenerationError:
            raise
        except Exception as exc:
            raise ReportGenerationError(
                f"Builder pipeline failed [{uuid.uuid4()}]: {exc}"
            ) from exc

        raw = buf.getvalue()

        # ── 4. Build filename and metadata ────────────────────────────────────
        period_label = self._period_label(request, start, end)
        scope_label = self._scope_label(request)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
        raw_name = f"GCF_Performance_{period_label}_{scope_label}_{ts}.xlsx"
        filename = _SLUG_RE.sub("_", raw_name)

        sheet_count = self._count_sheets(request, teams, employees)
        row_count = len(employees) + len(teams)

        meta = ReportMeta(
            filename=filename,
            sheet_count=sheet_count,
            row_count=row_count,
            generated_at=datetime.now(timezone.utc),
        )
        return raw, meta

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _period_label(request: ReportRequest, start, end) -> str:
        from app.schemas.report_request import PeriodType

        ptype = request.period.type
        if ptype == PeriodType.QUARTER:
            q = (start.month - 1) // 3 + 1
            return f"Q{q}-{start.year}"
        if ptype == PeriodType.MONTH:
            return start.strftime("%Y-%m")
        if ptype == PeriodType.WEEK:
            return f"W{start.isocalendar()[1]}-{start.year}"
        return f"{start.isoformat()}_{end.isoformat()}"

    @staticmethod
    def _scope_label(request: ReportRequest) -> str:
        mode = request.scope.mode
        if mode == ScopeMode.FULL:
            return "full"
        if mode == ScopeMode.TEAMS:
            n = len(request.scope.team_ids or [])
            return f"teams-{n}"
        n = len(request.scope.user_ids or [])
        return f"employees-{n}"

    @staticmethod
    def _count_sheets(request: ReportRequest, teams: list, employees: list) -> int:
        # cover + index + employees_summary + teams_summary = 4 fixed sheets
        count = 4
        if request.options.include_team_sheets:
            count += len(teams)
        if request.options.include_individual_sheets:
            count += len(employees)
        return count
