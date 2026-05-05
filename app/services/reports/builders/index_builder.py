"""IndexBuilder — writes the '01_Indice' navigation sheet."""
from __future__ import annotations

from typing import Any

from app.services.reports.builders.base_builder import SheetBuilder, WorkbookContext


def _team_sheet_name(sanitizer, team) -> str:
    return sanitizer(f"Equipo_{team.team_name}", team.team_id)


def _emp_sheet_name(sanitizer, emp) -> str:
    return sanitizer(f"Emp_{emp.full_name}", emp.user_id)


class IndexBuilder(SheetBuilder):
    """Builds the '01_Indice' navigation sheet."""

    def build(self, workbook: Any, ctx: WorkbookContext) -> None:
        ws = workbook.add_worksheet("01_Indice")
        fmt = ctx.formats
        san = ctx.sanitizer
        opts = ctx.request.options

        ws.set_column(0, 0, 30)
        ws.set_column(1, 1, 25)
        ws.set_column(2, 2, 12)
        ws.set_column(3, 3, 10)
        ws.set_column(4, 4, 20)

        # Row 0 (A1): title
        ws.write(0, 0, "Índice de Navegación", fmt["title"])

        row = 2

        # ── Teams section ──────────────────────────────────────────────────────
        ws.merge_range(row, 0, row, 4, "Equipos", fmt["section_header"])
        row += 1
        teams_header_row = row
        self._write_table_header(
            ws, row,
            ["Equipo", "Líder", "# Miembros", "IEL", "Enlace"],
            fmt,
        )
        row += 1
        teams_first_data = row

        if opts.include_team_sheets:
            for team in ctx.teams:
                sheet = _team_sheet_name(san, team)
                link = f'=HYPERLINK("#\'{sheet}\'!A1","→ Ir al Equipo")'
                ws.write(row, 0, team.team_name,     fmt["cell_text"])
                ws.write(row, 1, team.leader_name,   fmt["cell_text"])
                ws.write(row, 2, team.members_count, fmt["cell_int"])
                ws.write(row, 3, round(team.avg_iel, 2), fmt["kpi_value"])
                ws.write_formula(row, 4, link,       fmt["hyperlink"])
                row += 1

        teams_last_data = row - 1
        if teams_last_data >= teams_first_data:
            self._apply_autofilter(ws, teams_header_row, teams_last_data, 4)

        row += 2  # two-row gap before employees section

        # ── Employees section ──────────────────────────────────────────────────
        ws.merge_range(row, 0, row, 4, "Empleados", fmt["section_header"])
        row += 1
        emps_header_row = row
        self._write_table_header(
            ws, row,
            ["Nombre", "Equipo", "Rol", "Enlace"],
            fmt,
        )
        row += 1
        emps_first_data = row

        if opts.include_individual_sheets:
            for emp in ctx.employees:
                sheet = _emp_sheet_name(san, emp)
                link = f'=HYPERLINK("#\'{sheet}\'!A1","→ Ir al Empleado")'
                ws.write(row, 0, emp.full_name,  fmt["cell_text"])
                ws.write(row, 1, emp.team_name,  fmt["cell_text"])
                ws.write(row, 2, emp.role,        fmt["cell_text"])
                ws.write_formula(row, 3, link,   fmt["hyperlink"])
                row += 1

        emps_last_data = row - 1
        if emps_last_data >= emps_first_data:
            self._apply_autofilter(ws, emps_header_row, emps_last_data, 3)
