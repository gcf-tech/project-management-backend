"""EmployeeDetailBuilder — writes per-employee detail sheets (Emp_<Name>_<id4>)."""
from __future__ import annotations

from typing import Any, Dict, List

from app.services.reports.builders.base_builder import SheetBuilder, WorkbookContext

# ── Headers ───────────────────────────────────────────────────────────────────

_PROJECT_HEADERS = [
    "Proyecto",             # A  col 0
    "Rol",                  # B  col 1
    "Horas Invertidas",     # C  col 2
    "Tareas Cerradas",      # D  col 3
    "Subtareas Cerradas",   # E  col 4
    "Avance Aportado",      # F  col 5
    "Estado Proyecto",      # G  col 6
]

_TASKS_HEADERS = ["Estado", "Tareas", "Subtareas", "Horas"]  # 4 cols (A–D)

_ACTS_HEADERS = ["Tipo de Actividad", "Cantidad", "Horas"]   # 3 cols (A–C)

# DB column_status values → Spanish display labels (spec-mandated order)
_STATUS_ORDER = ["completed", "in_progress", "blocked", "cancelled"]
_STATUS_LABEL: Dict[str, str] = {
    "completed":   "Completada",
    "in_progress": "En Progreso",
    "blocked":     "Bloqueada",
    "cancelled":   "Cancelada",
}

# Aux zone for C2 chart pre-calculation (tasks + subtasks per project)
_AUX_COL = 20   # U — project names
# _AUX_COL + 1  # V — tasks_closed sum

# Chart grid — 2×2 in columns J–Q
_CHART_COL1 = 9    # J
_CHART_COL2 = 13   # N
_CHART_TOP  = 1    # anchored near title row
_CHART_BOT  = _CHART_TOP + 15

# Fixed row constants for the projects sub-table
_PROJ_HDR   = 6    # 0-indexed (fila 7)
_PROJ_START = 7    # 0-indexed (fila 8)


def _sheet_name(sanitizer, emp) -> str:
    return sanitizer(f"Emp_{emp.full_name}", emp.user_id)


def _col_letter(idx: int) -> str:
    """0-based column index → Excel letter(s) (A–XFD)."""
    result = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        result = chr(65 + rem) + result
    return result


class EmployeeDetailBuilder(SheetBuilder):
    """Builds one detail sheet per user_id present in ctx.employee_details."""

    def build(self, workbook: Any, ctx: WorkbookContext) -> None:
        if not ctx.request.options.include_individual_sheets:
            return

        for user_id, detail in ctx.employee_details.items():
            sn = _sheet_name(ctx.sanitizer, detail.header)
            ws = workbook.add_worksheet(sn)
            self._build_sheet(ws, sn, ctx, detail)

    # ── Per-sheet builder ─────────────────────────────────────────────────────

    def _build_sheet(self, ws, sn, ctx, detail):
        emp      = detail.header
        projects = detail.projects
        tbs      = detail.tasks_by_status       # Dict[str, {"count": int}]
        abt      = detail.activities_by_type    # Dict[str, {"count": int}]
        fmt      = ctx.formats
        period   = ctx.request.period
        cf       = ctx.chart_factory

        # ── Column widths ──────────────────────────────────────────────────────
        for col, w in enumerate([28, 15, 14, 14, 16, 14, 15]):
            ws.set_column(col, col, w)
        for col in range(9, 18):
            ws.set_column(col, col, 12)

        # ── Row 0 (A1): title | H1: back hyperlink ────────────────────────────
        ws.write(0, 0, f"Reporte Individual: {emp.full_name}", fmt["title"])
        ws.write_formula(
            0, 7,
            '=HYPERLINK("#\'01_Indice\'!A1","→ Volver al Índice")',
            fmt["hyperlink"],
        )

        # ── Row 2 (fila 3): KPI labels ─────────────────────────────────────────
        kpi_labels = [
            "Rol", "Equipo", "Periodo", "Horas Totales",
            "IEL", "Avg Progress", "Tasa Cumplimiento", "SLA Avg",
        ]
        for col, lbl in enumerate(kpi_labels):
            ws.write(2, col, lbl, fmt["kpi_label"])

        # ── Row 3 (fila 4): KPI values ─────────────────────────────────────────
        kpi_vals = [
            (emp.role,               fmt["cell_text"]),
            (emp.team_name,          fmt["cell_text"]),
            (f"{period.start_date} → {period.end_date}", fmt["cell_text"]),
            (emp.hours_worked,       fmt["cell_hours"]),
            (round(emp.iel, 2),      fmt["kpi_value"]),
            (emp.avg_progress,       fmt["cell_percent"]),
            (emp.completion_rate,    fmt["cell_percent"]),
            (emp.sla_avg_days,       fmt["cell_days"]),
        ]
        for col, (val, cell_fmt) in enumerate(kpi_vals):
            ws.write(3, col, val, cell_fmt)

        # ── Row 5 (fila 6): Projects section ──────────────────────────────────
        self._write_section_header(ws, 5, "Desglose por Proyecto", fmt)

        # ── Row 6 (fila 7): Projects table header ─────────────────────────────
        self._write_table_header(ws, _PROJ_HDR, _PROJECT_HEADERS, fmt)

        # ── Rows 7+ (filas 8+): Projects data + aux zone ──────────────────────
        for i, proj in enumerate(projects):
            r  = _PROJ_START + i
            tf = fmt["banded_row_alt"] if i % 2 else fmt["cell_text"]
            ws.write(r, 0, proj.project_name,   tf)
            ws.write(r, 1, emp.role,            fmt["cell_text"])
            ws.write(r, 2, proj.hours_invested, fmt["cell_hours"])
            ws.write(r, 3, proj.tasks_closed,   fmt["cell_int"])
            ws.write(r, 4, 0,                   fmt["cell_int"])    # subtasks not in DTO
            ws.write(r, 5, proj.avg_progress,   fmt["cell_percent"])
            ws.write(r, 6, proj.status,         fmt["cell_text"])

            # Aux zone: project names + tasks_closed for C2 chart
            ws.write(r, _AUX_COL,     proj.project_name, fmt["cell_text"])
            ws.write(r, _AUX_COL + 1, proj.tasks_closed,  fmt["cell_int"])

        proj_last = (_PROJ_START + len(projects) - 1) if projects else _PROJ_HDR
        if projects:
            self._apply_autofilter(ws, _PROJ_HDR, proj_last, 6)

        # ── Tasks-by-status section ────────────────────────────────────────────
        TASKS_SECT  = proj_last + 3
        TASKS_HDR   = TASKS_SECT + 1
        TASKS_START = TASKS_HDR + 1
        TASKS_END   = TASKS_START + len(_STATUS_ORDER) - 1   # always 4 rows

        self._write_section_header(ws, TASKS_SECT, "Resumen de Tareas por Estado", fmt)
        self._write_table_header(ws, TASKS_HDR, _TASKS_HEADERS, fmt)

        for i, status_key in enumerate(_STATUS_ORDER):
            r     = TASKS_START + i
            label = _STATUS_LABEL[status_key]
            count = tbs.get(status_key, {}).get("count", 0)
            tf    = fmt["banded_row_alt"] if i % 2 else fmt["cell_text"]
            ws.write(r, 0, label, tf)
            ws.write(r, 1, count, fmt["cell_int"])
            ws.write(r, 2, 0,     fmt["cell_int"])   # subtasks not in aggregator
            ws.write(r, 3, 0.0,   fmt["cell_hours"]) # hours not in aggregator

        # ── Activities-by-type section ─────────────────────────────────────────
        ACTS_SECT  = TASKS_END + 3
        ACTS_HDR   = ACTS_SECT + 1
        ACTS_START = ACTS_HDR + 1

        self._write_section_header(ws, ACTS_SECT, "Actividades por Tipo", fmt)
        self._write_table_header(ws, ACTS_HDR, _ACTS_HEADERS, fmt)

        act_types: List[str] = sorted(abt.keys())
        for i, atype in enumerate(act_types):
            r     = ACTS_START + i
            count = abt[atype].get("count", 0)
            tf    = fmt["banded_row_alt"] if i % 2 else fmt["cell_text"]
            ws.write(r, 0, atype, tf)
            ws.write(r, 1, count,  fmt["cell_int"])
            ws.write(r, 2, 0.0,    fmt["cell_hours"])  # hours not in aggregator

        acts_end = (ACTS_START + len(act_types) - 1) if act_types else ACTS_HDR

        # ── Charts 2×2 grid in columns J–Q ────────────────────────────────────
        # C1: Doughnut  "Horas por Proyecto"          top-left
        # C2: BarVert   "Tareas + Subtareas"          top-right (aux zone)
        # C3: Doughnut  "Tareas por Estado"           bottom-left
        # C4: BarHoriz  "Horas por Tipo de Actividad" bottom-right

        if projects:
            pr1 = _PROJ_START + 1       # Excel 1-indexed
            pr2 = proj_last + 1
            proj_cats  = f"='{sn}'!$A${pr1}:$A${pr2}"
            proj_hours = f"='{sn}'!$C${pr1}:$C${pr2}"  # col C = Horas Invertidas

            c1 = cf.doughnut("Horas por Proyecto", proj_cats, proj_hours)
            ws.insert_chart(_CHART_TOP, _CHART_COL1, c1, {"x_scale": 1.4, "y_scale": 1.4})

            # C2 uses aux zone (cols U/V = _AUX_COL / _AUX_COL+1)
            u_ltr = _col_letter(_AUX_COL)
            v_ltr = _col_letter(_AUX_COL + 1)
            aux_cats = f"='{sn}'!${u_ltr}${pr1}:${u_ltr}${pr2}"
            aux_vals = f"='{sn}'!${v_ltr}${pr1}:${v_ltr}${pr2}"
            c2 = cf.bar_vertical("Tareas + Subtareas por Proyecto", aux_cats, aux_vals)
            ws.insert_chart(_CHART_TOP, _CHART_COL2, c2, {"x_scale": 1.4, "y_scale": 1.4})

        # C3: tasks by status (always 4 rows)
        ts_r1 = TASKS_START + 1    # Excel 1-indexed
        ts_r2 = TASKS_END + 1
        ts_cats = f"='{sn}'!$A${ts_r1}:$A${ts_r2}"
        ts_vals = f"='{sn}'!$B${ts_r1}:$B${ts_r2}"
        c3 = cf.doughnut("Tareas por Estado", ts_cats, ts_vals)
        ws.insert_chart(_CHART_BOT, _CHART_COL1, c3, {"x_scale": 1.4, "y_scale": 1.4})

        # C4: activities by type (variable rows)
        if act_types:
            ar1 = ACTS_START + 1
            ar2 = acts_end + 1
            act_cats = f"='{sn}'!$A${ar1}:$A${ar2}"
            act_vals = f"='{sn}'!$B${ar1}:$B${ar2}"
            c4 = cf.bar_horizontal("Horas por Tipo de Actividad", act_cats, act_vals)
            ws.insert_chart(_CHART_BOT, _CHART_COL2, c4, {"x_scale": 1.4, "y_scale": 1.4})
