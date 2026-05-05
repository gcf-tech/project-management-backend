"""Abstract base class and shared context for all sheet builders."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List

from app.schemas.report_metrics import (
    EmployeeDetailDTO,
    EmployeeMetricsDTO,
    OrgMetricsDTO,
    TeamDetailDTO,
    TeamMetricsDTO,
)
from app.schemas.report_request import ReportRequest


@dataclass
class WorkbookContext:
    """Pre-loaded aggregator results and shared infra passed to every builder."""

    org_metrics: OrgMetricsDTO
    employees: List[EmployeeMetricsDTO]
    teams: List[TeamMetricsDTO]
    formats: Dict[str, Any]
    chart_factory: Any
    sanitizer: Any
    request: ReportRequest
    employee_details: Dict[int, EmployeeDetailDTO] = field(default_factory=dict)
    team_details: Dict[int, TeamDetailDTO] = field(default_factory=dict)


class SheetBuilder(ABC):
    """One builder per logical Excel sheet (or sheet group)."""

    @abstractmethod
    def build(self, workbook: Any, ctx: WorkbookContext) -> None:
        """Write one or more worksheets into *workbook* using *ctx*."""

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _write_section_header(
        self,
        worksheet,
        row: int,
        text: str,
        formats: Dict[str, Any],
        last_col: int = 10,
    ) -> None:
        """Merge-write a section header across columns 0 → *last_col*."""
        worksheet.merge_range(row, 0, row, last_col, text, formats["section_header"])

    def _write_table_header(
        self,
        worksheet,
        row: int,
        headers: List[str],
        formats: Dict[str, Any],
    ) -> None:
        """Write column labels in *headers* starting at (row, 0)."""
        for col, label in enumerate(headers):
            worksheet.write(row, col, label, formats["table_header"])

    def _apply_autofilter(
        self,
        worksheet,
        first_row: int,
        last_row: int,
        last_col: int,
    ) -> None:
        """Enable Excel autofilter on the range (first_row, 0) → (last_row, last_col)."""
        worksheet.autofilter(first_row, 0, last_row, last_col)
