"""CoverBuilder — writes the '00_Portada' summary sheet."""
from __future__ import annotations

from typing import Any

from app.services.reports.builders.base_builder import SheetBuilder, WorkbookContext

_GLOSSARY = [
    (
        "IEL",
        "Índice de Eficiencia Laboral: promedio ponderado de tasa de cumplimiento, "
        "avance de proyectos y cumplimiento de SLA.",
    ),
    (
        "Tasa de Cumplimiento",
        "Porcentaje de proyectos completados respecto al total asignado en el periodo.",
    ),
    (
        "Avg Progress",
        "Progreso promedio (0–100 %) de los proyectos activos del empleado o equipo.",
    ),
    (
        "SLA Avg",
        "Promedio de días entre el cierre de proyectos y su fecha límite "
        "(negativo = adelantado).",
    ),
    (
        "Avance Aportado",
        "Suma ponderada del progreso reportado por el empleado en todas sus tareas "
        "del periodo.",
    ),
]


class CoverBuilder(SheetBuilder):
    """Builds the '00_Portada' cover sheet."""

    def build(self, workbook: Any, ctx: WorkbookContext) -> None:
        ws = workbook.add_worksheet("00_Portada")
        fmt = ctx.formats
        org = ctx.org_metrics
        req = ctx.request

        ws.set_column(0, 0, 22)
        ws.set_column(1, 1, 50)

        # Row 0 (fila 1): title merged A1:F1
        ws.merge_range(
            0, 0, 0, 5,
            "Reporte de Rendimiento — Activity Tracker",
            fmt["title"],
        )

        # Rows 2–7 (filas 3–8): two-column metadata block
        metadata = [
            ("Tipo de Periodo",  req.period.type.value),
            ("Fecha Inicio",     str(req.period.start_date)),
            ("Fecha Fin",        str(req.period.end_date)),
            ("Fecha Generación", org.generated_at.isoformat()),
            ("Generado por",     org.generated_by),
            ("Scope",            org.scope_label),
        ]
        for offset, (label, value) in enumerate(metadata):
            row = 2 + offset
            ws.write(row, 0, label, fmt["kpi_label"])
            ws.write(row, 1, value,  fmt["cell_text"])

        # Rows 9–11 (filas 10–12): KPIs Globales
        ws.merge_range(9, 0, 9, 5, "KPIs Globales", fmt["section_header"])
        kpi_labels = [
            "Total Empleados",
            "Total Equipos",
            "Horas Registradas",
            "IEL Promedio Org",
        ]
        kpi_values = [
            org.total_employees,
            org.total_teams,
            round(org.total_hours, 1),
            round(org.avg_iel_org, 2),
        ]
        for col, label in enumerate(kpi_labels):
            ws.write(10, col, label,          fmt["kpi_label"])
            ws.write(11, col, kpi_values[col], fmt["kpi_value"])

        # Rows 13–19 (filas 14–20): glossary
        ws.merge_range(13, 0, 13, 5, "Glosario de Métricas", fmt["section_header"])
        ws.write(14, 0, "Métrica",    fmt["table_header"])
        ws.write(14, 1, "Definición", fmt["table_header"])
        for offset, (metric, definition) in enumerate(_GLOSSARY):
            row = 15 + offset
            ws.write(row, 0, metric,     fmt["cell_text"])
            ws.write(row, 1, definition, fmt["cell_text"])
