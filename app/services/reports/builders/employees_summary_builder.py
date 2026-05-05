"""EmployeesSummaryBuilder — writes the '02_Resumen_Empleados' sheet."""
from __future__ import annotations

from typing import Any, List

from app.services.reports.builders.base_builder import SheetBuilder, WorkbookContext
from app.schemas.report_metrics import EmployeeMetricsDTO

_SHEET = "02_Resumen_Empleados"

_HEADERS = [
    "Nombre Completo",      # A  col 0
    "Equipo",               # B  col 1
    "Rol",                  # C  col 2
    "Horas",                # D  col 3
    "Proy Asignados",       # E  col 4
    "Proy Cerrados",        # F  col 5
    "% Proy Completados",   # G  col 6  ← formula
    "Tareas Cerradas",      # H  col 7
    "Subtareas Cerradas",   # I  col 8
    "Actividades",          # J  col 9
    "Tasa de Cumplimiento", # K  col 10
    "IEL",                  # L  col 11
    "Avg Progress",         # M  col 12
    "SLA Avg",              # N  col 13
    "Detalle",              # O  col 14  ← hyperlink
]

# Aux zone starts at column Q (index 16); 2 columns per chart → 8 total (Q–X)
_AUX_COL = 16   # Q


def _emp_sheet_name(sanitizer, emp) -> str:
    return sanitizer(f"Emp_{emp.full_name}", emp.user_id)


def _top_n(items: List[EmployeeMetricsDTO], key, n: int) -> List[EmployeeMetricsDTO]:
    return sorted(items, key=key, reverse=True)[:n]


class EmployeesSummaryBuilder(SheetBuilder):
    """Builds the '02_Resumen_Empleados' aggregate sheet."""

    def build(self, workbook: Any, ctx: WorkbookContext) -> None:
        ws = workbook.add_worksheet(_SHEET)
        fmt = ctx.formats
        opts = ctx.request.options
        emps = ctx.employees
        san = ctx.sanitizer

        # ── Column widths ──────────────────────────────────────────────────────
        col_widths = [25, 20, 15, 10, 13, 13, 16, 14, 16, 12, 18, 8, 13, 10, 20]
        for col, w in enumerate(col_widths):
            ws.set_column(col, col, w)

        # ── Row 0 (A1): title ──────────────────────────────────────────────────
        ws.write(0, 0, "Resumen General: Trabajadores", fmt["title"])

        # ── Row 1 (A2:F2): filter info ─────────────────────────────────────────
        period = ctx.request.period
        scope  = ctx.org_metrics.scope_label
        ws.merge_range(
            1, 0, 1, 5,
            f"Filtros aplicados: Periodo={period.type.value} "
            f"({period.start_date} → {period.end_date}), Scope={scope}",
            fmt["kpi_label"],
        )

        # ── Row 2: section header for KPI block ────────────────────────────────
        ws.merge_range(2, 0, 2, 4, "KPIs Globales (calculados sobre la tabla)", fmt["section_header"])

        # ── Row 3 (fila 4): KPI labels ─────────────────────────────────────────
        kpi_labels = [
            "Total Empleados",
            "Horas Totales",
            "IEL Promedio",
            "Tareas Cerradas (∑)",
            "Subtareas Cerradas (∑)",
        ]
        for col, label in enumerate(kpi_labels):
            ws.write(3, col, label, fmt["kpi_label"])

        # ── Row 4 (fila 5): KPI formulas ───────────────────────────────────────
        # Data occupies A8:O∞ (Excel rows 8+), so:
        #   col A = "Nombre Completo", col D = "Horas", col H = "Tareas Cerradas",
        #   col I = "Subtareas Cerradas", col L = "IEL"
        kpi_formulas = [
            ("=COUNTA(A8:A1000)",   fmt["kpi_value"]),
            ("=SUM(D8:D1000)",      fmt["kpi_value"]),
            ("=IFERROR(AVERAGE(L8:L1000),0)", fmt["kpi_value"]),
            ("=SUM(H8:H1000)",      fmt["kpi_value"]),
            ("=SUM(I8:I1000)",      fmt["kpi_value"]),
        ]
        for col, (formula, cell_fmt) in enumerate(kpi_formulas):
            ws.write_formula(4, col, formula, cell_fmt)

        # ── Row 6 (fila 7): table header ───────────────────────────────────────
        self._write_table_header(ws, 6, _HEADERS, fmt)

        # ── Rows 7+ (filas 8+): data ───────────────────────────────────────────
        DATA_START = 7  # 0-indexed; Excel row = DATA_START + 1 = 8
        for i, emp in enumerate(emps):
            r = DATA_START + i
            xr = r + 1          # Excel 1-indexed row number
            banded = (i % 2 == 1)
            tf = fmt["banded_row_alt"] if banded else fmt["cell_text"]

            ws.write(r, 0,  emp.full_name,       tf)
            ws.write(r, 1,  emp.team_name,        tf)
            ws.write(r, 2,  emp.role,             tf)
            ws.write(r, 3,  emp.hours_worked,     fmt["cell_hours"])
            ws.write(r, 4,  emp.projects_assigned, fmt["cell_int"])
            ws.write(r, 5,  emp.projects_closed,   fmt["cell_int"])
            ws.write_formula(
                r, 6,
                f"=IF(E{xr}>0,F{xr}/E{xr},0)",
                fmt["cell_percent"],
            )
            ws.write(r, 7,  emp.tasks_closed,      fmt["cell_int"])
            ws.write(r, 8,  emp.subtasks_closed,   fmt["cell_int"])
            ws.write(r, 9,  emp.activities_count,  fmt["cell_int"])
            ws.write(r, 10, emp.completion_rate,   fmt["cell_percent"])
            ws.write(r, 11, emp.iel,               fmt["cell_text"])
            ws.write(r, 12, emp.avg_progress,      fmt["cell_percent"])
            ws.write(r, 13, emp.sla_avg_days,      fmt["cell_days"])

            if opts.include_individual_sheets:
                sheet = _emp_sheet_name(san, emp)
                link  = f'=HYPERLINK("#\'{sheet}\'!A1","→ Ver Detalle")'
                ws.write_formula(r, 14, link, fmt["hyperlink"])
            else:
                ws.write(r, 14, "", fmt["cell_text"])

        last_data_row = DATA_START + len(emps) - 1  # 0-indexed

        # ── Autofilter, freeze, banding ────────────────────────────────────────
        if emps:
            self._apply_autofilter(ws, 6, last_data_row, 14)
        ws.freeze_panes(DATA_START, 0)   # freeze rows 0–6; row 7 is first scrollable

        # ── Aux top-N zone (cols Q–X, rows 8..8+top_n-1 in Excel) ─────────────
        top_n = opts.top_n
        aux_r1 = DATA_START          # 0-indexed first aux row
        aux_r1_xl = aux_r1 + 1       # Excel row of first aux row = 8

        top_hours  = _top_n(emps, lambda e: e.hours_worked, top_n)
        top_tasks  = _top_n(emps, lambda e: e.tasks_closed + e.subtasks_closed, top_n)
        top_acts   = _top_n(emps, lambda e: e.activities_count, top_n)
        top_iel    = _top_n(emps, lambda e: e.iel, top_n)

        for i in range(top_n):
            ar = aux_r1 + i
            if i < len(top_hours):
                ws.write(ar, _AUX_COL + 0, top_hours[i].full_name,   fmt["cell_text"])
                ws.write(ar, _AUX_COL + 1, top_hours[i].hours_worked, fmt["cell_hours"])
            if i < len(top_tasks):
                e = top_tasks[i]
                ws.write(ar, _AUX_COL + 2, e.full_name,                        fmt["cell_text"])
                ws.write(ar, _AUX_COL + 3, e.tasks_closed + e.subtasks_closed, fmt["cell_int"])
            if i < len(top_acts):
                ws.write(ar, _AUX_COL + 4, top_acts[i].full_name,      fmt["cell_text"])
                ws.write(ar, _AUX_COL + 5, top_acts[i].activities_count, fmt["cell_int"])
            if i < len(top_iel):
                ws.write(ar, _AUX_COL + 6, top_iel[i].full_name, fmt["cell_text"])
                ws.write(ar, _AUX_COL + 7, top_iel[i].iel,       fmt["cell_text"])

        # ── Charts (2×2 grid below data) ───────────────────────────────────────
        if not emps:
            return

        chart_row = last_data_row + 5

        # Excel range strings for the aux zone
        r1_xl = aux_r1_xl
        r2_xl = aux_r1_xl + top_n - 1

        def _col_letter(idx: int) -> str:
            # Converts 0-based column index to Excel letter(s); supports A–Z, AA–XFD
            result = ""
            idx += 1
            while idx:
                idx, rem = divmod(idx - 1, 26)
                result = chr(65 + rem) + result
            return result

        def _rng(col_idx: int) -> str:
            c = _col_letter(col_idx)
            return f"='{_SHEET}'!${c}${r1_xl}:${c}${r2_xl}"

        cf = ctx.chart_factory

        # C1 — Top N Horas Trabajadas
        c1 = cf.bar_horizontal(
            f"Top {top_n} Horas Trabajadas",
            _rng(_AUX_COL + 0),
            _rng(_AUX_COL + 1),
        )
        ws.insert_chart(chart_row, 0, c1, {"x_scale": 1.4, "y_scale": 1.4})

        # C2 — Top N Tareas + Subtareas Cerradas
        c2 = cf.bar_horizontal(
            f"Top {top_n} Tareas+Subtareas Cerradas",
            _rng(_AUX_COL + 2),
            _rng(_AUX_COL + 3),
        )
        ws.insert_chart(chart_row, 8, c2, {"x_scale": 1.4, "y_scale": 1.4})

        # C3 — Top N Actividades Realizadas
        c3 = cf.bar_horizontal(
            f"Top {top_n} Actividades Realizadas",
            _rng(_AUX_COL + 4),
            _rng(_AUX_COL + 5),
        )
        ws.insert_chart(chart_row + 15, 0, c3, {"x_scale": 1.4, "y_scale": 1.4})

        # C4 — Top N Mayor IEL
        c4 = cf.bar_horizontal(
            f"Top {top_n} Mayor IEL",
            _rng(_AUX_COL + 6),
            _rng(_AUX_COL + 7),
        )
        ws.insert_chart(chart_row + 15, 8, c4, {"x_scale": 1.4, "y_scale": 1.4})
