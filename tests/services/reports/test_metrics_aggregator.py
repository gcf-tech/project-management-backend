"""
Unit tests for MetricsAggregator.

Fixture: 5 active users in one team, seeded with deterministic tasks and
time-logs so that expected values are computed analytically.

User layout:
  User 1 (leader): 10 h logged
    - 5 tasks (type=task) created in period, 4 completed
    - proj-a (type=project) created 2026-04-01, completed 2026-04-20 ← in period
    - proj-c (type=project) created 2026-04-05, still active        ← in period
    - proj-b (type=project) created 2026-03-01, still active        ← NOT in period (before)
    - task-u1-outside: created 2026-03-01, completed 2026-03-15     ← NOT in period
    total_items = 7 (5 tasks + 2 in-period projects), total_completed = 5 → CR = 5/7 = 71.4 %
    IEL = 71.4 × (1 + 5/20) = 71.4 × 1.25 ≈ 89.3

  User 2: 2 h logged, 1/2 tasks in period, CR = 50 %
  Users 3-5: 1 h each, 0 tasks → CR = 0 %

weighted_completion_rate for the team:
  Σ(CR × h) / Σ(h) = (71.43×10 + 50×2 + 0×1×3) / 15 ≈ 54.3

NOTE: completion_rate formula (from app/services/metrics_svc.py::calculate_user_metrics)
counts ALL Task rows (type=task AND type=project) — not just type=task.

Project fixtures (owner = User 1):
  Project A: deadline 2026-04-25, completed 2026-04-20  → +5 days (ahead)
  Project B: deadline 2026-04-10, still active           → (2026-04-10 - today=2026-05-04) = -24 (behind)
  Project C: deadline 2026-06-01, still active           → (2026-06-01 - today=2026-05-04) = +28 (ahead)
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import Activity, Subtask, Task, Team, TimeLog, User
from app.schemas.report_request import PeriodFilter, PeriodType, ScopeFilter, ScopeMode
from app.services.reports.metrics_aggregator import MetricsAggregator

# ── Fixed period used across all tests ────────────────────────────────────────
PERIOD = PeriodFilter(
    type=PeriodType.CUSTOM,
    start_date=date(2026, 4, 1),
    end_date=date(2026, 4, 30),
)
SCOPE_FULL = ScopeFilter(mode=ScopeMode.FULL)
TODAY = date(2026, 5, 4)  # frozen "today" for days_vs_deadline tests

START = date(2026, 4, 1)
END = date(2026, 4, 30)


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
    Insert all test data once and return references to key objects.
    Isolation: separate module-scoped fixture; no rollback needed because
    the in-memory DB is discarded after the module.
    """
    team = Team(name="Engineering")
    db.add(team)
    db.flush()

    users = []
    for i in range(1, 6):
        u = User(
            nc_user_id=f"nc-user-{i}",
            display_name=f"User {i}",
            is_active=True,
            team_id=team.id,
            role="leader" if i == 1 else "member",
        )
        db.add(u)
        users.append(u)
    db.flush()

    team.leader_id = users[0].id

    # ── Activities (used as time-log parents) ─────────────────────────────────
    activities = []
    for u in users:
        act = Activity(
            id=f"act-{u.nc_user_id}",
            title=f"Activity for {u.display_name}",
            owner_id=u.id,
            type="other",
            created_at=datetime(2026, 4, 5),
        )
        db.add(act)
        activities.append(act)
    db.flush()

    # ── Time logs (linked to activities so join_active_parents logic passes) ──
    # User 1: 10 h  User 2: 2 h  Users 3-5: 1 h each
    hours_map = {
        users[0].id: (36_000, activities[0].id),
        users[1].id: (7_200, activities[1].id),
        users[2].id: (3_600, activities[2].id),
        users[3].id: (3_600, activities[3].id),
        users[4].id: (3_600, activities[4].id),
    }
    for uid, (secs, act_id) in hours_map.items():
        db.add(TimeLog(
            user_id=uid,
            activity_id=act_id,
            log_date=date(2026, 4, 15),
            seconds=secs,
            start_at=datetime(2026, 4, 15, 0, 0),
        ))

    # ── Tasks for User 1: 4 completed + 1 open (in period) ───────────────────
    for idx in range(5):
        completed = idx < 4
        db.add(Task(
            id=f"task-u1-{idx}",
            title=f"Task u1-{idx}",
            owner_id=users[0].id,
            type="task",
            column_status="completed" if completed else "actively-working",
            created_at=datetime(2026, 4, 2 + idx),
            completed_at=datetime(2026, 4, 10 + idx) if completed else None,
            progress=100 if completed else 40,
        ))

    # ── Tasks for User 1: 1 outside the period (must NOT be counted) ─────────
    db.add(Task(
        id="task-u1-outside",
        title="Outside period",
        owner_id=users[0].id,
        type="task",
        column_status="completed",
        created_at=datetime(2026, 3, 1),   # March — before period
        completed_at=datetime(2026, 3, 15),
        progress=100,
    ))

    # ── Tasks for User 2: 1 completed + 1 open (in period) ───────────────────
    for idx in range(2):
        completed = idx == 0
        db.add(Task(
            id=f"task-u2-{idx}",
            title=f"Task u2-{idx}",
            owner_id=users[1].id,
            type="task",
            column_status="completed" if completed else "actively-working",
            created_at=datetime(2026, 4, 3 + idx),
            completed_at=datetime(2026, 4, 12) if completed else None,
            progress=100 if completed else 20,
        ))

    # ── Projects for User 1 (for days_vs_deadline tests) ─────────────────────
    # Project A: completed 2026-04-20, deadline 2026-04-25 → +5
    db.add(Task(
        id="proj-a",
        title="Project A",
        owner_id=users[0].id,
        type="project",
        column_status="completed",
        created_at=datetime(2026, 4, 1),
        completed_at=datetime(2026, 4, 20),
        deadline=date(2026, 4, 25),
        progress=100,
    ))
    # Project B: active, deadline 2026-04-10 → behind (today=2026-05-04 → -24)
    db.add(Task(
        id="proj-b",
        title="Project B",
        owner_id=users[0].id,
        type="project",
        column_status="actively-working",
        created_at=datetime(2026, 3, 1),  # before period but still active
        completed_at=None,
        deadline=date(2026, 4, 10),
        progress=50,
    ))
    # Project C: active, deadline 2026-06-01 → ahead (today=2026-05-04 → +28)
    db.add(Task(
        id="proj-c",
        title="Project C",
        owner_id=users[0].id,
        type="project",
        column_status="working-now",
        created_at=datetime(2026, 4, 5),
        completed_at=None,
        deadline=date(2026, 6, 1),
        progress=30,
    ))

    db.commit()
    return {"team": team, "users": users, "activities": activities}


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestResolvePeriod:
    def test_custom_period_passthrough(self, db, seeded):
        agg = MetricsAggregator(db)
        start, end = agg.resolve_period(PERIOD)
        assert start == date(2026, 4, 1)
        assert end == date(2026, 4, 30)

    def test_week_period_is_monday_to_sunday(self, db, seeded):
        agg = MetricsAggregator(db)
        period = PeriodFilter(type=PeriodType.WEEK)
        start, end = agg.resolve_period(period)
        assert start.weekday() == 0  # Monday
        assert (end - start).days == 6


class TestDateRangeFilter:
    """
    Verifies that tasks outside the period are excluded from totals.

    User 1 fixture:
      In-period:  5 tasks (type=task) + proj-a + proj-c  → 7 total_items
      Completed:  4 tasks + proj-a                        → 5 total_completed
      Outside:    task-u1-outside (March) + proj-b (March, still active) → excluded

    completion_rate = 5/7 × 100 ≈ 71.4  (formula counts all Task rows, any type)
    tasks_closed    = 4               (only type=task completed in period)
    """

    def test_out_of_period_task_excluded(self, db, seeded):
        agg = MetricsAggregator(db)
        employees = agg.get_employees_metrics(PERIOD, SCOPE_FULL)

        user1 = next(e for e in employees if e.full_name == "User 1")
        # type=task completed in period (4 out of 5 in-period tasks)
        assert user1.tasks_closed == 4
        # 5 completed / 7 total items (tasks + in-period projects), not 6 (excludes March task)
        assert user1.completion_rate == pytest.approx(5 / 7 * 100, abs=0.2)

    def test_all_users_returned_even_with_zero_tasks(self, db, seeded):
        agg = MetricsAggregator(db)
        employees = agg.get_employees_metrics(PERIOD, SCOPE_FULL)
        assert len(employees) == 5

    def test_user3_has_zero_tasks_and_zero_cr(self, db, seeded):
        agg = MetricsAggregator(db)
        employees = agg.get_employees_metrics(PERIOD, SCOPE_FULL)
        user3 = next(e for e in employees if e.full_name == "User 3")
        assert user3.tasks_closed == 0
        assert user3.completion_rate == pytest.approx(0.0)
        assert user3.hours_worked == pytest.approx(1.0, abs=0.05)


class TestWeightedCompletionRate:
    """
    weighted_completion_rate = Σ(CR × hours) / Σ(hours).

    User 1: CR=5/7×100≈71.43, h=10  → contrib ≈ 714.3
    User 2: CR=50,              h= 2  → contrib = 100
    User 3: CR= 0,              h= 1  → contrib =   0
    User 4: CR= 0,              h= 1  → contrib =   0
    User 5: CR= 0,              h= 1  → contrib =   0
    Total hours = 15 → weighted_CR = 814.3/15 ≈ 54.3
    """

    def test_weighted_cr_formula(self, db, seeded):
        agg = MetricsAggregator(db)
        teams = agg.get_teams_metrics(PERIOD, SCOPE_FULL)

        assert len(teams) == 1
        team = teams[0]
        expected_wcr = (5 / 7 * 100 * 10 + 50 * 2) / 15
        assert team.weighted_completion_rate == pytest.approx(expected_wcr, abs=0.3)

    def test_zero_hours_does_not_raise(self, db, seeded):
        """If every member has 0 hours, weighted_CR must be 0.0 (no ZeroDivisionError)."""
        agg = MetricsAggregator(db)

        # Scope limited to users 3-5 who have 1 h each but 0 tasks
        scope = ScopeFilter(
            mode=ScopeMode.EMPLOYEES,
            user_ids=[seeded["users"][2].id, seeded["users"][3].id, seeded["users"][4].id],
        )
        teams = agg.get_teams_metrics(PERIOD, scope)
        if teams:
            # All three have 0 completion_rate, so weighted_CR = 0.0
            assert teams[0].weighted_completion_rate == pytest.approx(0.0)

    def test_total_hours_correct(self, db, seeded):
        agg = MetricsAggregator(db)
        teams = agg.get_teams_metrics(PERIOD, SCOPE_FULL)
        team = teams[0]
        # 10 + 2 + 1 + 1 + 1 = 15 hours
        assert team.total_hours == pytest.approx(15.0, abs=0.05)
        assert team.members_count == 5


class TestDaysVsDeadline:
    """
    Rule 4:
      - completed  → (deadline - completed_at.date()).days   positive = ahead
      - active     → (deadline - today).days                 positive = ahead
    """

    def test_project_completed_ahead_of_deadline(self, db, seeded):
        agg = MetricsAggregator(db)
        projects = agg._get_projects(
            start=START, end=END, today=TODAY,
            owner_ids=[seeded["users"][0].id],
        )
        proj_a = next(p for p in projects if p.project_id == "proj-a")
        # deadline=2026-04-25, completed=2026-04-20 → +5
        assert proj_a.days_vs_deadline == 5

    def test_active_project_past_deadline_is_negative(self, db, seeded):
        agg = MetricsAggregator(db)
        projects = agg._get_projects(
            start=START, end=END, today=TODAY,
            owner_ids=[seeded["users"][0].id],
        )
        proj_b = next(p for p in projects if p.project_id == "proj-b")
        # deadline=2026-04-10, today=2026-05-04 → -24
        assert proj_b.days_vs_deadline == (date(2026, 4, 10) - TODAY).days

    def test_active_project_future_deadline_is_positive(self, db, seeded):
        agg = MetricsAggregator(db)
        projects = agg._get_projects(
            start=START, end=END, today=TODAY,
            owner_ids=[seeded["users"][0].id],
        )
        proj_c = next(p for p in projects if p.project_id == "proj-c")
        # deadline=2026-06-01, today=2026-05-04 → +28
        assert proj_c.days_vs_deadline == (date(2026, 6, 1) - TODAY).days

    def test_project_without_deadline_returns_zero(self, db, seeded):
        """Projects with no deadline should return days_vs_deadline=0."""
        agg = MetricsAggregator(db)
        projects = agg._get_projects(
            start=START, end=END, today=TODAY,
            owner_ids=[seeded["users"][0].id],
        )
        for p in projects:
            if p.deadline is None:
                assert p.days_vs_deadline == 0


class TestScopeFiltering:
    def test_teams_scope_filters_by_team_id(self, db, seeded):
        agg = MetricsAggregator(db)
        team_id = seeded["team"].id
        scope = ScopeFilter(mode=ScopeMode.TEAMS, team_ids=[team_id])
        employees = agg.get_employees_metrics(PERIOD, scope)
        assert len(employees) == 5

    def test_employees_scope_filters_by_user_id(self, db, seeded):
        agg = MetricsAggregator(db)
        user1_id = seeded["users"][0].id
        scope = ScopeFilter(mode=ScopeMode.EMPLOYEES, user_ids=[user1_id])
        employees = agg.get_employees_metrics(PERIOD, scope)
        assert len(employees) == 1
        assert employees[0].user_id == user1_id

    def test_nonexistent_team_returns_empty_list(self, db, seeded):
        agg = MetricsAggregator(db)
        scope = ScopeFilter(mode=ScopeMode.TEAMS, team_ids=[99999])
        result = agg.get_teams_metrics(PERIOD, scope)
        assert result == []


class TestIELFormula:
    """IEL = completion_rate × (1 + avg_difficulty / 20).
    With no difficulty set, avg_difficulty defaults to 5.0.
    User 1: CR = 5/7×100 ≈ 71.43 → IEL = 71.43 × 1.25 ≈ 89.3
    """

    def test_iel_default_difficulty(self, db, seeded):
        agg = MetricsAggregator(db)
        employees = agg.get_employees_metrics(PERIOD, SCOPE_FULL)
        user1 = next(e for e in employees if e.full_name == "User 1")
        expected_cr = 5 / 7 * 100
        expected_iel = round(expected_cr * (1 + 5.0 / 20), 1)
        assert user1.iel == pytest.approx(expected_iel, abs=0.3)
