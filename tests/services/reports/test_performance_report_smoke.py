"""
Smoke tests for the full performance report generation pipeline.

These tests call the service layer directly (no HTTP), except for
test_unauthorized_role_403 which uses FastAPI TestClient to verify that the
router enforces role-based access control.

xlsx verification uses stdlib zipfile + xml.etree — openpyxl is not required.

Fixture layout
--------------
- 1 admin user      (team 1, is_active=False — not counted as an employee)
- 3 teams           (Equipo 1 / 2 / 3)
- 8 employees       (3 in team 1, 3 in team 2, 2 in team 3)
- 30 tasks          (alternating project/task type, half completed in-period)
- 50 subtasks       (2 per first 25 tasks, half completed)
- 10 activities     (1 per user, spread across types)
- 100 time logs     (4 dates × 25 tasks)
"""
from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import Activity, Subtask, Task, Team, TimeLog, User
from app.schemas.report_request import (
    PeriodFilter,
    PeriodType,
    ReportOptions,
    ReportRequest,
    ScopeFilter,
    ScopeMode,
)
from app.services.reports.metrics_aggregator import MetricsAggregator
from app.services.reports.performance_report_service import PerformanceReportService

# ── Constants ─────────────────────────────────────────────────────────────────

_PERIOD = PeriodFilter(
    type=PeriodType.CUSTOM,
    start_date=date(2026, 4, 1),
    end_date=date(2026, 4, 30),
)
_SCOPE_FULL = ScopeFilter(mode=ScopeMode.FULL)

FIXED_SHEETS = [
    "00_Portada",
    "01_Indice",
    "02_Resumen_Empleados",
    "03_Resumen_Equipos",
]

# Relationship namespaces used across OOXML inspection helpers
_NS_R   = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"

# ── Engine / session fixtures ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture(scope="module")
def db(engine):
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


# ── Seed fixture ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def seeded(db):
    """
    Insert all test data once for the module.

    admin.is_active = False so they are excluded from employee metrics;
    the object is only used as the ``generated_by`` argument to the service.
    """
    # ── Teams ──────────────────────────────────────────────────────────────────
    teams = [Team(name=f"Equipo {i}") for i in range(1, 4)]
    db.add_all(teams)
    db.flush()

    # ── Admin user (inactive → not counted as employee in reports) ─────────────
    admin = User(
        nc_user_id="nc-admin-smoke",
        display_name="Admin Smoke",
        role="admin",
        team_id=teams[0].id,
        is_active=False,
    )
    db.add(admin)
    db.flush()

    # ── 8 employees: 3 in team1, 3 in team2, 2 in team3 ──────────────────────
    _layout = [
        (0, "leader"), (0, "member"), (0, "member"),
        (1, "leader"), (1, "member"), (1, "member"),
        (2, "leader"), (2, "member"),
    ]
    employees: list[User] = []
    for idx, (ti, role) in enumerate(_layout, start=1):
        u = User(
            nc_user_id=f"nc-emp-smoke-{idx}",
            display_name=f"Empleado Smoke {idx}",
            role=role,
            team_id=teams[ti].id,
            is_active=True,
        )
        db.add(u)
        employees.append(u)
    db.flush()

    # Set team leaders
    teams[0].leader_id = employees[0].id
    teams[1].leader_id = employees[3].id
    teams[2].leader_id = employees[6].id

    # all_users for cycling ownership: admin first (tasks will exist but admin
    # is inactive, so her logs will not appear in the base_users CTE)
    all_users = [admin] + employees  # 9 total

    # ── Activities (10, one per user) ─────────────────────────────────────────
    _act_types = ["meeting", "review", "development", "testing", "other"]
    activities: list[Activity] = []
    for i in range(10):
        act = Activity(
            id=f"act-smk-{i}",
            title=f"Smoke Act {i}",
            owner_id=all_users[i % len(all_users)].id,
            type=_act_types[i % len(_act_types)],
            created_at=datetime(2026, 4, 10),
        )
        db.add(act)
        activities.append(act)
    db.flush()

    # ── Tasks (30, mix of type/status) ────────────────────────────────────────
    tasks: list[Task] = []
    for i in range(30):
        owner = all_users[i % len(all_users)]
        t_type  = "project" if i % 3 == 0 else "task"
        status  = "completed" if i % 2 == 0 else "actively-working"
        t = Task(
            id=f"tsk-smk-{i:03d}",
            title=f"Smoke Task {i}",
            owner_id=owner.id,
            assigned_to=owner.id,
            type=t_type,
            column_status=status,
            progress=100 if status == "completed" else 50,
            difficulty=5,
            created_at=datetime(2026, 4, 5),
            completed_at=datetime(2026, 4, 20) if status == "completed" else None,
        )
        db.add(t)
        tasks.append(t)
    db.flush()

    # ── Subtasks (50: 2 per first 25 tasks) ───────────────────────────────────
    sub_idx = 0
    for task in tasks[:25]:
        for _ in range(2):
            db.add(Subtask(
                id=f"sub-smk-{sub_idx:03d}",
                task_id=task.id,
                text=f"Smoke Sub {sub_idx}",
                completed=(sub_idx % 2 == 0),
            ))
            sub_idx += 1
    db.flush()

    # ── Time logs (100: 4 distinct dates × 25 tasks) ──────────────────────────
    # Unique constraint: (user_id, task_id, log_date) — different task_id per
    # row means no conflict even when the same user owns multiple tasks.
    _dates = [date(2026, 4, d) for d in range(1, 5)]  # Apr 1–4
    for task in tasks[:25]:
        for d in _dates:
            db.add(TimeLog(
                user_id=task.owner_id,
                task_id=task.id,
                log_date=d,
                seconds=3600,
            ))
    db.flush()
    db.commit()

    return {"admin": admin, "teams": teams, "employees": employees, "tasks": tasks}


# ── XLSX inspection helpers (stdlib only) ─────────────────────────────────────


def _sheetnames(raw: bytes) -> list[str]:
    """Return ordered sheet names from an xlsx without external libraries."""
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        with zf.open("xl/workbook.xml") as f:
            root = ET.parse(f).getroot()
    return [
        el.attrib["name"]
        for el in root.iter()
        if el.tag.endswith("}sheet") and "name" in el.attrib
    ]


def _count_charts_for_sheet(raw: bytes, sheet_name: str) -> int:
    """
    Count embedded charts in *sheet_name* by traversing the OOXML zip structure:
      workbook.xml → sheet r:id
      workbook.xml.rels → sheet file
      sheetN.xml → drawing r:id
      sheetN.xml.rels → drawing file
      drawingN.xml.rels → count relationships of type …/chart
    """
    rid_attr = f"{{{_NS_R}}}id"

    def _parse(zf: zipfile.ZipFile, path: str) -> ET.Element:
        with zf.open(path) as f:
            return ET.parse(f).getroot()

    def _rels(root: ET.Element) -> dict[str, dict]:
        tag = f"{{{_NS_PKG}}}Relationship"
        return {r.attrib["Id"]: r.attrib for r in root.iter() if r.tag == tag}

    with zipfile.ZipFile(BytesIO(raw)) as zf:
        # 1. workbook.xml → find the r:id for sheet_name
        wb = _parse(zf, "xl/workbook.xml")
        sheet_rid: str | None = None
        for el in wb.iter():
            if el.tag.endswith("}sheet") and el.attrib.get("name") == sheet_name:
                sheet_rid = el.attrib.get(rid_attr) or el.attrib.get("r:id")
                break
        if sheet_rid is None:
            raise KeyError(f"Sheet '{sheet_name}' not found in workbook.xml")

        # 2. workbook.xml.rels → sheet file path
        wb_rels = _rels(_parse(zf, "xl/_rels/workbook.xml.rels"))
        sheet_target = wb_rels[sheet_rid]["Target"]   # e.g. "worksheets/sheet3.xml"
        sheet_path   = f"xl/{sheet_target}"

        # 3. sheetN.xml → drawing r:id
        sh = _parse(zf, sheet_path)
        drawing_el = next(
            (el for el in sh.iter() if el.tag.endswith("}drawing")), None
        )
        if drawing_el is None:
            return 0
        drawing_rid = drawing_el.attrib.get(rid_attr) or drawing_el.attrib.get("r:id")

        # 4. sheetN.xml.rels → drawing file
        fname = sheet_target.split("/")[-1]
        sh_rels    = _rels(_parse(zf, f"xl/worksheets/_rels/{fname}.rels"))
        draw_fname = sh_rels[drawing_rid]["Target"].split("/")[-1]  # "drawing3.xml"

        # 5. drawingN.xml.rels → count chart-type relationships
        try:
            draw_rels = _rels(_parse(zf, f"xl/drawings/_rels/{draw_fname}.rels"))
        except KeyError:
            return 0
        return sum(1 for v in draw_rels.values() if "/chart" in v.get("Type", ""))


# ── Service call helper ───────────────────────────────────────────────────────


def _make_request(
    period: PeriodFilter | None = None,
    scope: ScopeFilter | None = None,
    options: ReportOptions | None = None,
) -> ReportRequest:
    return ReportRequest(
        period=period or _PERIOD,
        scope=scope or _SCOPE_FULL,
        options=options or ReportOptions(),
    )


def _generate(db, request: ReportRequest, admin_user: User):
    aggregator = MetricsAggregator(db)
    service    = PerformanceReportService(aggregator)
    return service.generate(request, admin_user)


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestFullReportGeneratesValidXlsx:
    def test_full_report_generates_valid_xlsx(self, db, seeded):
        """Default options: 8 employees, 3 teams, no individual sheets."""
        admin   = seeded["admin"]
        raw, _  = _generate(db, _make_request(), admin)

        # ── byte-level validity ────────────────────────────────────────────────
        assert raw, "Report returned empty bytes"
        assert len(raw) > 5_000, f"Expected > 5 KB, got {len(raw)} bytes"
        assert raw[:4] == b"PK\x03\x04", "Missing XLSX magic number (PK\\x03\\x04)"

        # ── sheet names ────────────────────────────────────────────────────────
        names = _sheetnames(raw)
        for sheet in FIXED_SHEETS:
            assert sheet in names, f"Expected sheet '{sheet}' not found; got {names}"

        # 3 Equipo_* sheets (one per team)
        equipo = [n for n in names if n.startswith("Equipo_")]
        assert len(equipo) == 3, (
            f"Expected 3 Equipo_* sheets, got {len(equipo)}: {equipo}"
        )

        # no Emp_* sheets (include_individual_sheets=False by default)
        emp = [n for n in names if n.startswith("Emp_")]
        assert emp == [], f"Expected no Emp_* sheets, got {emp}"

        # ── chart counts on summary sheets ─────────────────────────────────────
        assert _count_charts_for_sheet(raw, "02_Resumen_Empleados") == 4, (
            "02_Resumen_Empleados should have exactly 4 charts"
        )
        assert _count_charts_for_sheet(raw, "03_Resumen_Equipos") == 4, (
            "03_Resumen_Equipos should have exactly 4 charts"
        )


class TestWithIndividualSheets:
    def test_with_individual_sheets(self, db, seeded):
        """include_individual_sheets=True → one Emp_* sheet per active employee."""
        admin  = seeded["admin"]
        opts   = ReportOptions(include_individual_sheets=True)
        raw, _ = _generate(db, _make_request(options=opts), admin)

        names     = _sheetnames(raw)
        emp_sheets = [n for n in names if n.startswith("Emp_")]
        # 8 active employees (admin is inactive and excluded from base_users CTE)
        assert len(emp_sheets) == 8, (
            f"Expected 8 Emp_* sheets (one per active employee), "
            f"got {len(emp_sheets)}: {emp_sheets}"
        )


class TestScopeTeamsFilter:
    def test_scope_teams_filter(self, db, seeded):
        """scope.mode=TEAMS + team_ids=[team1.id] → exactly 1 Equipo_* sheet."""
        admin  = seeded["admin"]
        team   = seeded["teams"][0]
        scope  = ScopeFilter(mode=ScopeMode.TEAMS, team_ids=[team.id])
        raw, _ = _generate(db, _make_request(scope=scope), admin)

        names  = _sheetnames(raw)
        equipo = [n for n in names if n.startswith("Equipo_")]
        assert len(equipo) == 1, (
            f"Expected 1 Equipo_* sheet for team_id={team.id}, "
            f"got {len(equipo)}: {equipo}"
        )


class TestUnauthorizedRole403:
    def test_unauthorized_role_403(self, db, seeded):
        """An employee (role='member') must receive HTTP 403 from the endpoint."""
        from app.main import app
        from app.api.dependencies import get_db, require_user

        employee = seeded["employees"][1]  # role="member"

        def _mock_user():
            return employee

        def _override_db():
            yield db

        app.dependency_overrides[require_user] = _mock_user
        app.dependency_overrides[get_db]       = _override_db
        try:
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/reports/performance/export",
                    json={
                        "period": {
                            "type": "custom",
                            "start_date": "2026-04-01",
                            "end_date": "2026-04-30",
                        },
                        "scope": {"mode": "full"},
                        "options": {},
                    },
                )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 403, (
            f"Expected 403 for role='member', got {response.status_code}: "
            f"{response.text}"
        )


class TestEmptyPeriodNoCrash:
    def test_empty_period_no_crash(self, db, seeded):
        """A period with no activity generates a valid xlsx with zero values (no crash)."""
        admin  = seeded["admin"]
        period = PeriodFilter(
            type=PeriodType.CUSTOM,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 1, 31),
        )
        # Should not raise EmptyScopeError: get_employees_metrics returns all
        # active users (OUTER JOIN), even when no tasks/hours fall in the period.
        raw, _ = _generate(db, _make_request(period=period), admin)

        assert raw[:4] == b"PK\x03\x04", "Missing XLSX magic number for empty-period report"
        names = _sheetnames(raw)
        for sheet in FIXED_SHEETS:
            assert sheet in names, (
                f"Fixed sheet '{sheet}' missing in empty-period report; got {names}"
            )
