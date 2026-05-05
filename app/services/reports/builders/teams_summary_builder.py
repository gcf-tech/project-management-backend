"""TeamsSummaryBuilder — writes the '03_Resumen_Equipos' sheet."""
from __future__ import annotations

from typing import Any

from app.services.reports.builders.base_builder import SheetBuilder, WorkbookContext

_SHEET = "03_Resumen_Equipos"

_HEADERS = [
    "Equipo",                    # A  col 0
    "Líder",                     # B  col 1
    "# Miembros",                # C  col 2
    "Horas Totales",             # D  col 3
    "Horas/Miembro",             # E  col 4  ← formula
    "Tareas Cerradas",           # F  col 5
    "Subtareas Cerradas",        # G  col 6
    "Actividades",               # H  col 7
    "Proy Activos",              # I  col 8
    "Proy Cerrados",             # J  col 9
    "IEL Promedio",              # K  col 10
    "Avg Progress",              # L  col 11
    "Tasa Cumplimiento Global",  # M  col 12
    "Detalle",                   # N  col 13  ← hyperlink
]

# Data starts at row 7 in Excel (0-indexed row 6)
_DATA_START = 6
_DATA_START_XL = _DATA_START + 1  # = 7


def _team_sheet_name(sanitizer, team) -> str:
    return sanitizer(f"Equipo_{team.team_name}", team.team_id)


class TeamsSummaryBuilder(SheetBuilder):
    """Builds the '03_Resumen_Equipos' aggregate sheet."""

    def build(self, workbook: Any, ctx: WorkbookContext) -> None:
        ws = workbook.add_worksheet(_SHEET)
        fmt = ctx.formats
        opts = ctx.request.options
        teams = ctx.teams
        san = ctx.sanitizer

        # ── Column widths ──────────────────────────────────────────────────────
        col_widths = [25, 22, 12, 13, 14, 14, 16, 12, 12, 12, 13, 13, 22, 20]
        for col, w in enumerate(col_widths):
            ws.set_column(col, col, w)

        # ── Row 0 (A1): title ──────────────────────────────────────────────────
        ws.write(0, 0, "Resumen General: Equipos", fmt["title"])

        # ── Rows 2–3 (filas 3–4): KPI block ───────────────────────────────────
        # KPI labels reference data in A7:N∞ (Excel).
        #   col A (0)  = Equipo
        #   col D (3)  = Horas Totales  → Excel letter D
        #   col K (10) = IEL Promedio   → Excel letter K
        kpi_labels = [
            "Total Equipos",
            "Equipo Top IEL",
            "Equipo Top Horas",
            "IEL Promedio Org",
        ]
        kpi_formulas = [
            f"=COUNTA(A{_DATA_START_XL}:A1000)",
            (
                f"=IFERROR(INDEX(A{_DATA_START_XL}:A1000,"
                f"MATCH(MAX(K{_DATA_START_XL}:K1000),"
                f"K{_DATA_START_XL}:K1000,0)),\"N/A\")"
            ),
            (
                f"=IFERROR(INDEX(A{_DATA_START_XL}:A1000,"
                f"MATCH(MAX(D{_DATA_START_XL}:D1000),"
                f"D{_DATA_START_XL}:D1000,0)),\"N/A\")"
            ),
            f"=IFERROR(AVERAGE(K{_DATA_START_XL}:K1000),0)",
        ]
        for col, label in enumerate(kpi_labels):
            ws.write(2, col, label, fmt["kpi_label"])
        for col, formula in enumerate(kpi_formulas):
            ws.write_formula(3, col, formula, fmt["kpi_value"])

        # ── Row 5 (fila 6): table header ───────────────────────────────────────
        self._write_table_header(ws, 5, _HEADERS, fmt)

        # ── Rows 6+ (filas 7+): data ───────────────────────────────────────────
        for i, team in enumerate(teams):
            r = _DATA_START + i
            xr = r + 1                        # Excel row number
            banded = (i % 2 == 1)
            tf = fmt["banded_row_alt"] if banded else fmt["cell_text"]

            ws.write(r, 0,  team.team_name,               tf)
            ws.write(r, 1,  team.leader_name,              tf)
            ws.write(r, 2,  team.members_count,            fmt["cell_int"])
            ws.write(r, 3,  team.total_hours,              fmt["cell_hours"])
            ws.write_formula(
                r, 4,
                f"=IF(C{xr}>0,D{xr}/C{xr},0)",
                fmt["cell_hours"],
            )
            ws.write(r, 5,  team.tasks_closed,             fmt["cell_int"])
            ws.write(r, 6,  team.subtasks_closed,          fmt["cell_int"])
            ws.write(r, 7,  team.activities_count,         fmt["cell_int"])
            ws.write(r, 8,  team.projects_active,          fmt["cell_int"])
            ws.write(r, 9,  team.projects_closed,          fmt["cell_int"])
            ws.write(r, 10, team.avg_iel,                  fmt["cell_text"])
            ws.write(r, 11, team.avg_progress,             fmt["cell_percent"])
            ws.write(r, 12, team.weighted_completion_rate, fmt["cell_percent"])

            if opts.include_team_sheets:
                sheet = _team_sheet_name(san, team)
                link  = f'=HYPERLINK("#\'{sheet}\'!A1","→ Ver Equipo")'
                ws.write_formula(r, 13, link, fmt["hyperlink"])
            else:
                ws.write(r, 13, "", fmt["cell_text"])

        last_data_row = _DATA_START + len(teams) - 1  # 0-indexed

        if teams:
            self._apply_autofilter(ws, 5, last_data_row, 13)

        # ── Charts (2×2 grid below data) ───────────────────────────────────────
        if not teams:
            return

        chart_row = last_data_row + 5
        n = len(teams)
        r1 = _DATA_START_XL          # Excel first data row
        r2 = _DATA_START_XL + n - 1  # Excel last data row
        sn = _SHEET

        cats    = f"='{sn}'!$A${r1}:$A${r2}"
        hours   = f"='{sn}'!$D${r1}:$D${r2}"
        iel     = f"='{sn}'!$K${r1}:$K${r2}"
        tasks   = f"='{sn}'!$F${r1}:$F${r2}"
        subs    = f"='{sn}'!$G${r1}:$G${r2}"
        acts    = f"='{sn}'!$H${r1}:$H${r2}"
        avg_prg = f"='{sn}'!$L${r1}:$L${r2}"

        cf = ctx.chart_factory

        # C1 — Horas Totales por Equipo (column/vertical bar)
        c1 = cf.bar_vertical("Horas Totales por Equipo", cats, hours)
        ws.insert_chart(chart_row, 0, c1, {"x_scale": 1.4, "y_scale": 1.4})

        # C2 — IEL por Equipo (column/vertical bar)
        c2 = cf.bar_vertical("IEL por Equipo", cats, iel)
        ws.insert_chart(chart_row, 8, c2, {"x_scale": 1.4, "y_scale": 1.4})

        # C3 — Composición del Trabajo: stacked bar (Tareas / Subtareas / Actividades)
        c3 = cf.bar_stacked(
            "Composición del Trabajo",
            cats,
            [
                ("Tareas Cerradas",    tasks),
                ("Subtareas Cerradas", subs),
                ("Actividades",        acts),
            ],
        )
        ws.insert_chart(chart_row + 15, 0, c3, {"x_scale": 1.4, "y_scale": 1.4})

        # C4 — Salud de Proyectos: Avg Progress (line)
        c4 = cf.line("Salud de Proyectos (Avg Progress)", cats, avg_prg)
        ws.insert_chart(chart_row + 15, 8, c4, {"x_scale": 1.4, "y_scale": 1.4})
