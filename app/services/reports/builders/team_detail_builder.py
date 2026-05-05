"""TeamDetailBuilder — writes per-team detail sheets (Equipo_<Name>_<id4>)."""
from __future__ import annotations

from typing import Any

from app.services.reports.builders.base_builder import SheetBuilder, WorkbookContext

# ── Headers ───────────────────────────────────────────────────────────────────

_MEMBER_HEADERS = [
    "Miembro",              # A  col 0
    "Rol",                  # B  col 1
    "Horas",                # C  col 2
    "Tareas Cerradas",      # D  col 3
    "Subtareas Cerradas",   # E  col 4
    "Actividades",          # F  col 5
    "IEL",                  # G  col 6
    "Avg Progress",         # H  col 7
    "SLA Avg",              # I  col 8
]

_PROJECT_HEADERS = [
    "Proyecto",             # A  col 0
    "Estado",               # B  col 1
    "Avg Progress",         # C  col 2
    "Horas Invertidas",     # D  col 3
    "Tareas Cerradas",      # E  col 4
    "Deadline",             # F  col 5
    "Días vs Deadline",     # G  col 6
]

# Chart placement: C1/C2 top, C3/C4 bottom; anchored right of tables
_CHART_COL1 = 10   # K
_CHART_COL2 = 14   # O
_CHART_TOP  = 1    # chart grid starts at row index 1 (beside KPIs)
_CHART_BOT  = _CHART_TOP + 15

# Conditional-format colours (days vs deadline)
_GREEN_BG = "#C6EFCE"
_GREEN_FG = "#276221"
_RED_BG   = "#FFC7CE"
_RED_FG   = "#9C0006"


def _sheet_name(sanitizer, team) -> str:
    return sanitizer(f"Equipo_{team.team_name}", team.team_id)


class TeamDetailBuilder(SheetBuilder):
    """Builds one detail sheet per team_id present in ctx.team_details."""

    def build(self, workbook: Any, ctx: WorkbookContext) -> None:
        if not ctx.request.options.include_team_sheets:
            return

        green_fmt = workbook.add_format({"bg_color": _GREEN_BG, "font_color": _GREEN_FG})
        red_fmt   = workbook.add_format({"bg_color": _RED_BG,   "font_color": _RED_FG})

        for team_id, detail in ctx.team_details.items():
            sn = _sheet_name(ctx.sanitizer, detail.header)
            ws = workbook.add_worksheet(sn)
            self._build_sheet(ws, sn, ctx, detail, green_fmt, red_fmt)

    # ── Per-sheet builder ─────────────────────────────────────────────────────

    def _build_sheet(self, ws, sn, ctx, detail, green_fmt, red_fmt):
        team     = detail.header
        members  = detail.members
        projects = detail.projects
        fmt      = ctx.formats
        period   = ctx.request.period
        cf       = ctx.chart_factory

        # ── Column widths ──────────────────────────────────────────────────────
        for col, w in enumerate([25, 15, 10, 14, 16, 12, 10, 13, 10]):
            ws.set_column(col, col, w)
        for col in range(10, 20):
            ws.set_column(col, col, 12)

        # ── Row 0 (A1): title | G1: back hyperlink ────────────────────────────
        ws.write(0, 0, f"Reporte de Equipo: {team.team_name}", fmt["title"])
        ws.write_formula(
            0, 6,
            '=HYPERLINK("#\'01_Indice\'!A1","→ Volver al Índice")',
            fmt["hyperlink"],
        )

        # ── Row 2 (fila 3): KPI labels ─────────────────────────────────────────
        kpi_labels = [
            "Líder", "Periodo", "Total Miembros",
            "Total Horas", "IEL Promedio", "Proyectos Activos",
        ]
        for col, lbl in enumerate(kpi_labels):
            ws.write(2, col, lbl, fmt["kpi_label"])

        # ── Row 3 (fila 4): KPI values ─────────────────────────────────────────
        kpi_vals = [
            (team.leader_name,           fmt["cell_text"]),
            (f"{period.start_date} → {period.end_date}", fmt["cell_text"]),
            (team.members_count,         fmt["cell_int"]),
            (team.total_hours,           fmt["cell_hours"]),
            (round(team.avg_iel, 2),     fmt["kpi_value"]),
            (team.projects_active,       fmt["cell_int"]),
        ]
        for col, (val, cell_fmt) in enumerate(kpi_vals):
            ws.write(3, col, val, cell_fmt)

        # ── Row 5 (fila 6): Members section ───────────────────────────────────
        self._write_section_header(ws, 5, "Rendimiento de Miembros", fmt)

        # ── Row 6 (fila 7): Members table header ──────────────────────────────
        _MEM_HDR = 6
        _MEM_START = 7
        self._write_table_header(ws, _MEM_HDR, _MEMBER_HEADERS, fmt)

        # ── Rows 7+ (filas 8+): Members data ──────────────────────────────────
        for i, mem in enumerate(members):
            r  = _MEM_START + i
            tf = fmt["banded_row_alt"] if i % 2 else fmt["cell_text"]
            ws.write(r, 0, mem.full_name,        tf)
            ws.write(r, 1, mem.role,             tf)
            ws.write(r, 2, mem.hours_worked,     fmt["cell_hours"])
            ws.write(r, 3, mem.tasks_closed,     fmt["cell_int"])
            ws.write(r, 4, mem.subtasks_closed,  fmt["cell_int"])
            ws.write(r, 5, mem.activities_count, fmt["cell_int"])
            ws.write(r, 6, round(mem.iel, 2),    fmt["kpi_value"])
            ws.write(r, 7, mem.avg_progress,     fmt["cell_percent"])
            ws.write(r, 8, mem.sla_avg_days,     fmt["cell_days"])

        mem_last = (_MEM_START + len(members) - 1) if members else _MEM_HDR
        if members:
            self._apply_autofilter(ws, _MEM_HDR, mem_last, 8)

        # ── Projects section (2-row gap after members table) ───────────────────
        PROJ_SECT  = mem_last + 3   # +1 empty, +2 empty, +3 section header
        PROJ_HDR   = PROJ_SECT + 1
        PROJ_START = PROJ_HDR + 1

        self._write_section_header(ws, PROJ_SECT, "Proyectos del Equipo", fmt)
        self._write_table_header(ws, PROJ_HDR, _PROJECT_HEADERS, fmt)

        for i, proj in enumerate(projects):
            r  = PROJ_START + i
            tf = fmt["banded_row_alt"] if i % 2 else fmt["cell_text"]
            ws.write(r, 0, proj.project_name,    tf)
            ws.write(r, 1, proj.status,          fmt["cell_text"])
            ws.write(r, 2, proj.avg_progress,    fmt["cell_percent"])
            ws.write(r, 3, proj.hours_invested,  fmt["cell_hours"])
            ws.write(r, 4, proj.tasks_closed,    fmt["cell_int"])
            ws.write(r, 5, str(proj.deadline) if proj.deadline else "—", fmt["cell_text"])
            ws.write(r, 6, proj.days_vs_deadline, fmt["cell_int"])

        proj_last = (PROJ_START + len(projects) - 1) if projects else PROJ_HDR
        if projects:
            ws.conditional_format(
                PROJ_START, 6, proj_last, 6,
                {"type": "cell", "criteria": ">=", "value": 0, "format": green_fmt},
            )
            ws.conditional_format(
                PROJ_START, 6, proj_last, 6,
                {"type": "cell", "criteria": "<",  "value": 0, "format": red_fmt},
            )
            self._apply_autofilter(ws, PROJ_HDR, proj_last, 6)

        # ── Charts in columns K–S ──────────────────────────────────────────────
        # C1 + C2: member metrics   C3 + C4: project metrics
        m_r1 = _MEM_START + 1          # Excel 1-indexed first member row
        m_r2 = mem_last + 1
        p_r1 = PROJ_START + 1
        p_r2 = proj_last + 1

        if members:
            mem_cats  = f"='{sn}'!$A${m_r1}:$A${m_r2}"
            mem_hours = f"='{sn}'!$C${m_r1}:$C${m_r2}"
            mem_iel   = f"='{sn}'!$G${m_r1}:$G${m_r2}"

            c1 = cf.bar_horizontal("Horas por Miembro", mem_cats, mem_hours)
            ws.insert_chart(_CHART_TOP, _CHART_COL1, c1, {"x_scale": 1.4, "y_scale": 1.4})

            c2 = cf.bar_horizontal("IEL por Miembro", mem_cats, mem_iel)
            ws.insert_chart(_CHART_TOP, _CHART_COL2, c2, {"x_scale": 1.4, "y_scale": 1.4})

        if projects:
            proj_cats  = f"='{sn}'!$A${p_r1}:$A${p_r2}"
            proj_prg   = f"='{sn}'!$C${p_r1}:$C${p_r2}"
            proj_hours = f"='{sn}'!$D${p_r1}:$D${p_r2}"

            c3 = cf.bar_vertical("Avg Progress por Proyecto", proj_cats, proj_prg)
            ws.insert_chart(_CHART_BOT, _CHART_COL1, c3, {"x_scale": 1.4, "y_scale": 1.4})

            c4 = cf.doughnut("Distribución de Horas por Proyecto", proj_cats, proj_hours)
            ws.insert_chart(_CHART_BOT, _CHART_COL2, c4, {"x_scale": 1.4, "y_scale": 1.4})
