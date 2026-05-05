"""Unit tests for style_registry and name_sanitizer."""
from __future__ import annotations

import io

import pytest
import xlsxwriter
from xlsxwriter.format import Format

from app.services.reports.name_sanitizer import dedupe_sheet_names, sanitize_sheet_name
from app.services.reports.style_registry import register_formats

# ── Expected format catalogue ─────────────────────────────────────────────────

_EXPECTED_FORMATS = {
    "title",
    "section_header",
    "table_header",
    "kpi_label",
    "kpi_value",
    "cell_text",
    "cell_int",
    "cell_hours",
    "cell_days",
    "cell_percent",
    "cell_date",
    "hyperlink",
    "banded_row_alt",
}


# ── style_registry ────────────────────────────────────────────────────────────

class TestRegisterFormats:
    def setup_method(self):
        buf = io.BytesIO()
        self.wb = xlsxwriter.Workbook(buf)
        self.fmts = register_formats(self.wb)

    def teardown_method(self):
        self.wb.close()

    def test_all_expected_keys_present(self):
        assert _EXPECTED_FORMATS == set(self.fmts.keys())

    def test_all_values_are_format_instances(self):
        for key, fmt in self.fmts.items():
            assert isinstance(fmt, Format), f"'{key}' is not a Format instance"


# ── name_sanitizer ────────────────────────────────────────────────────────────

class TestSanitizeSheetName:
    def test_acceptance_criteria(self):
        result = sanitize_sheet_name("Equipo: Backend/QA", 7)
        assert result == "Equipo Backend QA_0007"
        assert len(result) <= 31

    def test_strips_all_prohibited_chars(self):
        raw = r"A\B/C?D*E[F]G:H"
        result = sanitize_sheet_name(raw)
        for ch in r'\/?*[]':
            assert ch not in result
        assert ":" not in result

    def test_no_fallback_id(self):
        result = sanitize_sheet_name("Simple Name")
        assert result == "Simple Name"

    def test_truncation_to_31(self):
        long_name = "A" * 40
        assert len(sanitize_sheet_name(long_name)) == 31

    def test_truncation_with_suffix_stays_within_31(self):
        long_name = "B" * 40
        result = sanitize_sheet_name(long_name, 1)
        assert len(result) <= 31
        assert result.endswith("_0001")

    def test_fallback_id_zero_padded_4(self):
        result = sanitize_sheet_name("Sheet", 42)
        assert result.endswith("_0042")

    def test_string_fallback_id(self):
        result = sanitize_sheet_name("Sheet", "3")
        assert result.endswith("_0003")


class TestDedupeSheetNames:
    def test_no_duplicates_unchanged(self):
        names = ["Alpha", "Beta", "Gamma"]
        assert dedupe_sheet_names(names) == ["Alpha", "Beta", "Gamma"]

    def test_duplicate_gets_counter_suffix(self):
        names = ["Team A", "Team A", "Team A"]
        result = dedupe_sheet_names(names)
        assert result[0] == "Team A"
        assert result[1] == "Team A_2"
        assert result[2] == "Team A_3"

    def test_deduped_names_respect_max_len(self):
        long = "X" * 30
        names = [long, long]
        result = dedupe_sheet_names(names)
        for name in result:
            assert len(name) <= 31

    def test_sanitises_before_dedup(self):
        # "Eq: A" → replace ':' with space → "Eq  A" → collapse → "Eq A"
        names = ["Eq: A", "Eq: A"]
        result = dedupe_sheet_names(names)
        assert result[0] == "Eq A"
        assert result[1] == "Eq A_2"
