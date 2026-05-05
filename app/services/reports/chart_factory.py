"""Unified xlsxwriter chart factory — enforces consistent GCF chart style."""
from __future__ import annotations

from typing import List, Optional, Tuple

from app.services.reports.style_registry import GCF_BLUE

_TITLE_FONT = {"name": "Arial", "size": 11, "bold": True}
_NO_BORDER = {"border": {"none": True}}


class ChartFactory:
    """Create styled xlsxwriter charts.

    Inject the active workbook on construction; call chart methods to get
    ready-to-insert chart objects.

    ``categories`` and ``values`` must be Excel-range strings already
    formatted by the caller, e.g. ``"=Summary!$A$8:$A$17"``.
    """

    def __init__(self, workbook) -> None:
        self._wb = workbook

    # ── Private helpers ───────────────────────────────────────────────────────

    def _base_style(self, chart, title: str, *, value_axis: str = "y") -> None:
        """Apply uniform title, border, and legend style."""
        chart.set_title({"name": title, "name_font": _TITLE_FONT})
        chart.set_chartarea(_NO_BORDER)
        chart.set_plotarea(_NO_BORDER)
        chart.set_legend({"position": "bottom"})
        axis_setter = getattr(chart, f"set_{value_axis}_axis")
        axis_setter({"num_format": "0.0"})

    def _single_series(
        self,
        chart,
        title: str,
        categories: str,
        values: str,
        color: Optional[str],
        value_axis: str,
    ):
        props: dict = {"categories": categories, "values": values, "name": title}
        if color:
            props["fill"] = {"color": color}
        chart.add_series(props)
        self._base_style(chart, title, value_axis=value_axis)
        return chart

    # ── Public chart methods ──────────────────────────────────────────────────

    def bar_horizontal(
        self,
        title: str,
        categories: str,
        values: str,
        color: Optional[str] = None,
    ):
        """Horizontal bar chart. Values axis = x-axis in xlsxwriter 'bar' type."""
        chart = self._wb.add_chart({"type": "bar"})
        return self._single_series(chart, title, categories, values, color or GCF_BLUE, "x")

    def bar_vertical(
        self,
        title: str,
        categories: str,
        values: str,
        color: Optional[str] = None,
    ):
        """Vertical column chart."""
        chart = self._wb.add_chart({"type": "column"})
        return self._single_series(chart, title, categories, values, color or GCF_BLUE, "y")

    def bar_stacked(
        self,
        title: str,
        categories: str,
        series_list: List[Tuple[str, str]],
    ):
        """Horizontal stacked bar.

        *series_list* is a sequence of (series_name, values_ref) tuples;
        all series share the same *categories* range.
        """
        chart = self._wb.add_chart({"type": "bar", "subtype": "stacked"})
        for name, values_ref in series_list:
            chart.add_series({"name": name, "categories": categories, "values": values_ref})
        self._base_style(chart, title, value_axis="x")
        return chart

    def doughnut(
        self,
        title: str,
        categories: str,
        values: str,
    ):
        chart = self._wb.add_chart({"type": "doughnut"})
        chart.add_series({"categories": categories, "values": values, "name": title})
        chart.set_title({"name": title, "name_font": _TITLE_FONT})
        chart.set_chartarea(_NO_BORDER)
        chart.set_plotarea(_NO_BORDER)
        chart.set_legend({"position": "right"})
        return chart

    def pie(
        self,
        title: str,
        categories: str,
        values: str,
    ):
        chart = self._wb.add_chart({"type": "pie"})
        chart.add_series({"categories": categories, "values": values, "name": title})
        chart.set_title({"name": title, "name_font": _TITLE_FONT})
        chart.set_chartarea(_NO_BORDER)
        chart.set_plotarea(_NO_BORDER)
        chart.set_legend({"position": "right"})
        return chart

    def line(
        self,
        title: str,
        categories: str,
        values: str,
        color: Optional[str] = None,
    ):
        chart = self._wb.add_chart({"type": "line"})
        return self._single_series(chart, title, categories, values, color or GCF_BLUE, "y")
