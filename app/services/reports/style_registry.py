"""GCF corporate format palette — single source of truth for xlsxwriter styles."""
from __future__ import annotations

from typing import Dict

# ── GCF colour palette ────────────────────────────────────────────────────────
GCF_BLUE_DARK  = "#1F4E79"   # corporate dark blue  — titles
GCF_BLUE       = "#2E75B6"   # corporate light blue — table headers
GCF_GRAY       = "#BFBFBF"   # medium gray          — section headers
GCF_GRAY_LIGHT = "#F2F2F2"   # very light gray      — banding / KPI bg
GCF_WHITE      = "#FFFFFF"


def register_formats(workbook) -> Dict[str, object]:
    """Register all named xlsxwriter formats and return them keyed by label."""

    def _add(props: dict):
        return workbook.add_format(props)

    return {
        "title": _add({
            "font_name": "Arial", "font_size": 14, "bold": True,
            "font_color": GCF_BLUE_DARK,
        }),
        "section_header": _add({
            "font_name": "Arial", "font_size": 11, "bold": True,
            "bg_color": GCF_GRAY,
            "bottom": 1,
        }),
        "table_header": _add({
            "font_name": "Arial", "font_size": 11, "bold": True,
            "bg_color": GCF_BLUE, "font_color": GCF_WHITE,
            "align": "center",
        }),
        "kpi_label": _add({
            "font_name": "Arial", "font_size": 10, "bold": True,
            "bg_color": GCF_GRAY_LIGHT,
        }),
        "kpi_value": _add({
            "font_name": "Arial", "font_size": 12, "bold": True,
            "align": "right",
        }),
        "cell_text": _add({
            "font_name": "Arial", "font_size": 10,
        }),
        "cell_int": _add({
            "font_name": "Arial", "font_size": 10, "num_format": "0",
        }),
        "cell_hours": _add({
            "font_name": "Arial", "font_size": 10, "num_format": '0.0 "h"',
        }),
        "cell_days": _add({
            "font_name": "Arial", "font_size": 10, "num_format": '0.0 "d"',
        }),
        "cell_percent": _add({
            "font_name": "Arial", "font_size": 10, "num_format": "0.0%",
        }),
        "cell_date": _add({
            "font_name": "Arial", "font_size": 10, "num_format": "yyyy-mm-dd",
        }),
        "hyperlink": _add({
            "font_name": "Arial", "font_size": 10,
            "font_color": GCF_BLUE, "underline": 1,
        }),
        "banded_row_alt": _add({
            "font_name": "Arial", "font_size": 10,
            "bg_color": GCF_GRAY_LIGHT,
        }),
    }
