"""
Read-only aggregation layer — single source of truth for all report builders.

All public methods are safe to call in any order; each manages its own
queries and returns frozen DTO objects.

Query budget per public method:
  get_employees_metrics  → 1 SQL (single CTE chain)
  get_teams_metrics      → 2 SQL (team metadata + CTE chain)
  get_org_metrics        → 2 SQL (org counts + CTE chain)
  get_team_detail        → 3 SQL (team meta + CTE chain + projects)
  get_employee_detail    → 4 SQL (CTE chain + projects + tasks_by_status + activities_by_type)
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta
from functools import cached_property
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Activity, Subtask, Task, Team, TimeLog, User
from app.schemas.report_metrics import (
    EmployeeDetailDTO,
    EmployeeMetricsDTO,
    OrgMetricsDTO,
    ProjectMetricsDTO,
    TeamDetailDTO,
    TeamMetricsDTO,
)
from app.schemas.report_request import PeriodFilter, PeriodType, ScopeFilter, ScopeMode

_BOGOTA = ZoneInfo("America/Bogota")


class MetricsAggregator:
    """
    Stateless read-only aggregator. Inject one per request.

    Usage::

        aggregator = MetricsAggregator(db)
        employees = aggregator.get_employees_metrics(period, scope)
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    # ── Dialect helpers ───────────────────────────────────────────────────────

    @cached_property
    def _dialect(self) -> str:
        """Dialect name — drives cross-DB date-diff expression."""
        try:
            return self._db.bind.dialect.name  # legacy sessionmaker(bind=engine)
        except AttributeError:
            pass
        try:
            return self._db.get_bind().dialect.name
        except Exception:
            return "mysql"

    def _datediff(self, end_col, start_col):
        """
        Days between two DateTime columns.
        SQLite (tests): julianday arithmetic.
        MySQL/MariaDB (production): DATEDIFF().
        Always wraps columns in DATE() per project rule — comparisons with
        naive DateTime columns must use func.date() to avoid silent mismatches.
        """
        if self._dialect == "sqlite":
            return (
                func.julianday(func.date(end_col))
                - func.julianday(func.date(start_col))
            )
        return func.datediff(func.date(end_col), func.date(start_col))

    # ── Period resolution ─────────────────────────────────────────────────────

    def resolve_period(self, period_filter: PeriodFilter) -> Tuple[date, date]:
        """
        Return (start, end) for a PeriodFilter in America/Bogota.

        PeriodFilter's model_validator already resolves the range on construction,
        so normally this just unpacks the pre-computed dates. The explicit
        fallback handles direct instantiation without the ReportRequest validator.
        """
        if period_filter.start_date and period_filter.end_date:
            return period_filter.start_date, period_filter.end_date

        today = datetime.now(_BOGOTA).date()

        if period_filter.type == PeriodType.WEEK:
            monday = today - timedelta(days=today.weekday())
            return monday, monday + timedelta(days=6)

        if period_filter.type == PeriodType.MONTH:
            last_day = calendar.monthrange(today.year, today.month)[1]
            return today.replace(day=1), today.replace(day=last_day)

        if period_filter.type == PeriodType.QUARTER:
            q_start_month = ((today.month - 1) // 3) * 3 + 1
            q_end_month = q_start_month + 2
            last_day = calendar.monthrange(today.year, q_end_month)[1]
            return (
                today.replace(month=q_start_month, day=1),
                today.replace(month=q_end_month, day=last_day),
            )

        raise ValueError("CUSTOM period requires start_date and end_date")

    # ── Scope helpers ─────────────────────────────────────────────────────────

    def _user_scope_clauses(self, scope: ScopeFilter) -> list:
        """Per-rule-5 user filter clauses (combined with and_() at call site)."""
        clauses = [User.is_active == True]
        if scope.mode == ScopeMode.TEAMS:
            clauses.append(User.team_id.in_(scope.team_ids))
        elif scope.mode == ScopeMode.EMPLOYEES:
            clauses.append(User.id.in_(scope.user_ids))
        return clauses

    # ── IEL / completion-rate formulas ────────────────────────────────────────

    @staticmethod
    def _completion_rate(total_items: int, total_completed: int) -> float:
        # Source: app/services/metrics_svc.py::calculate_user_metrics
        return (total_completed / total_items * 100) if total_items > 0 else 0.0

    @staticmethod
    def _iel(completion_rate: float, avg_difficulty: float) -> float:
        # Source: app/services/metrics_svc.py::calculate_user_metrics
        return round(completion_rate * (1 + avg_difficulty / 20), 1)

    # ── Core CTE query (rule 1: single SQL for all employees) ─────────────────

    def _execute_employees_cte(
        self, start: date, end: date, scope: ScopeFilter
    ) -> list:
        """
        One SQL statement with six nested CTEs → final JOIN.

        CTEs: base_users, hours_agg, tasks_agg, subtasks_agg,
              activities_agg, sla_agg.

        Rule 2: every DateTime comparison uses func.date() to avoid silent
        mismatches in MySQL/MariaDB (naive DateTime columns).
        """

        # ── 1. base_users ────────────────────────────────────────────────────
        base_users = (
            select(
                User.id.label("user_id"),
                User.display_name.label("full_name"),
                User.role,
                User.team_id,
                func.coalesce(Team.name, "").label("team_name"),
            )
            .outerjoin(Team, User.team_id == Team.id)
            .where(and_(*self._user_scope_clauses(scope)))
        ).cte("base_users")

        # ── 2. hours_agg ─────────────────────────────────────────────────────
        # Replicates join_active_parents logic from app/db/query_helpers.py
        hours_agg = (
            select(
                TimeLog.user_id,
                func.coalesce(func.sum(TimeLog.seconds), 0).label("total_seconds"),
            )
            .outerjoin(Task, TimeLog.task_id == Task.id)
            .outerjoin(Activity, TimeLog.activity_id == Activity.id)
            .where(
                TimeLog.log_date >= start,
                TimeLog.log_date <= end,
                or_(
                    and_(TimeLog.task_id.isnot(None), Task.deleted_at.is_(None)),
                    and_(
                        TimeLog.activity_id.isnot(None),
                        Activity.deleted_at.is_(None),
                    ),
                ),
            )
            .group_by(TimeLog.user_id)
        ).cte("hours_agg")

        # ── 3. tasks_agg ─────────────────────────────────────────────────────
        # Conditional aggregation avoids a second pass per user (rule 1).
        # Formula mirrors calculate_user_metrics: total_items uses created_at,
        # completed uses completed_at — intentionally different windows.
        # Source: app/services/metrics_svc.py::calculate_user_metrics
        _in_period_created = and_(
            func.date(Task.created_at) >= start,
            func.date(Task.created_at) <= end,
        )
        _in_period_completed = and_(
            Task.column_status == "completed",
            func.date(Task.completed_at) >= start,
            func.date(Task.completed_at) <= end,
        )
        tasks_agg = (
            select(
                Task.owner_id.label("user_id"),
                func.sum(
                    case((_in_period_created, 1), else_=0)
                ).label("total_items"),
                func.sum(
                    case((_in_period_completed, 1), else_=0)
                ).label("total_completed"),
                func.sum(
                    case(
                        (
                            and_(Task.type == "task", _in_period_completed),
                            1,
                        ),
                        else_=0,
                    )
                ).label("tasks_closed"),
                func.sum(
                    case(
                        (
                            and_(Task.type == "project", _in_period_created),
                            1,
                        ),
                        else_=0,
                    )
                ).label("projects_assigned"),
                func.sum(
                    case(
                        (
                            and_(Task.type == "project", _in_period_completed),
                            1,
                        ),
                        else_=0,
                    )
                ).label("projects_closed"),
                # avg_difficulty: original service has no end_date bound; kept
                # consistent here but only for tasks with difficulty set.
                func.coalesce(
                    func.avg(
                        case(
                            (Task.difficulty.isnot(None), Task.difficulty),
                            else_=None,
                        )
                    ),
                    5.0,
                ).label("avg_difficulty"),
                func.coalesce(
                    func.avg(
                        case((_in_period_created, Task.progress), else_=None)
                    ),
                    0.0,
                ).label("avg_progress"),
            )
            .where(
                Task.deleted_at.is_(None),
                or_(_in_period_created, _in_period_completed),
            )
            .group_by(Task.owner_id)
        ).cte("tasks_agg")

        # ── 4. subtasks_agg ──────────────────────────────────────────────────
        subtasks_agg = (
            select(
                Task.owner_id.label("user_id"),
                func.sum(
                    case((Subtask.completed == True, 1), else_=0)
                ).label("subtasks_closed"),
            )
            .join(Subtask, Task.id == Subtask.task_id)
            .where(
                Task.deleted_at.is_(None),
                func.date(Task.created_at) >= start,
                func.date(Task.created_at) <= end,
            )
            .group_by(Task.owner_id)
        ).cte("subtasks_agg")

        # ── 5. activities_agg ────────────────────────────────────────────────
        activities_agg = (
            select(
                Activity.owner_id.label("user_id"),
                func.count(Activity.id).label("activities_count"),
            )
            .where(
                Activity.deleted_at.is_(None),
                func.date(Activity.created_at) >= start,
                func.date(Activity.created_at) <= end,
            )
            .group_by(Activity.owner_id)
        ).cte("activities_agg")

        # ── 6. sla_agg ───────────────────────────────────────────────────────
        # AVG(completed_at - created_at) in days for tasks completed in period.
        # Source: app/services/metrics_svc.py::calculate_user_metrics (sla_days)
        sla_agg = (
            select(
                Task.owner_id.label("user_id"),
                func.avg(
                    self._datediff(Task.completed_at, Task.created_at)
                ).label("sla_avg_days"),
            )
            .where(
                Task.deleted_at.is_(None),
                Task.column_status == "completed",
                Task.completed_at.isnot(None),
                Task.created_at.isnot(None),
                func.date(Task.completed_at) >= start,
                func.date(Task.completed_at) <= end,
            )
            .group_by(Task.owner_id)
        ).cte("sla_agg")

        # ── Final JOIN ────────────────────────────────────────────────────────
        final_q = (
            select(
                base_users.c.user_id,
                base_users.c.full_name,
                base_users.c.role,
                base_users.c.team_id,
                base_users.c.team_name,
                func.coalesce(hours_agg.c.total_seconds, 0).label("total_seconds"),
                func.coalesce(tasks_agg.c.total_items, 0).label("total_items"),
                func.coalesce(tasks_agg.c.total_completed, 0).label("total_completed"),
                func.coalesce(tasks_agg.c.tasks_closed, 0).label("tasks_closed"),
                func.coalesce(tasks_agg.c.projects_assigned, 0).label("projects_assigned"),
                func.coalesce(tasks_agg.c.projects_closed, 0).label("projects_closed"),
                func.coalesce(tasks_agg.c.avg_difficulty, 5.0).label("avg_difficulty"),
                func.coalesce(tasks_agg.c.avg_progress, 0.0).label("avg_progress"),
                func.coalesce(subtasks_agg.c.subtasks_closed, 0).label("subtasks_closed"),
                func.coalesce(activities_agg.c.activities_count, 0).label("activities_count"),
                func.coalesce(sla_agg.c.sla_avg_days, 0.0).label("sla_avg_days"),
            )
            .select_from(base_users)
            .outerjoin(hours_agg, base_users.c.user_id == hours_agg.c.user_id)
            .outerjoin(tasks_agg, base_users.c.user_id == tasks_agg.c.user_id)
            .outerjoin(subtasks_agg, base_users.c.user_id == subtasks_agg.c.user_id)
            .outerjoin(activities_agg, base_users.c.user_id == activities_agg.c.user_id)
            .outerjoin(sla_agg, base_users.c.user_id == sla_agg.c.user_id)
        )

        return self._db.execute(final_q).fetchall()

    # ── Row → DTO conversion ──────────────────────────────────────────────────

    def _row_to_employee_dto(self, row) -> EmployeeMetricsDTO:
        hours_worked = round(float(row.total_seconds) / 3600, 1)
        cr = self._completion_rate(int(row.total_items), int(row.total_completed))
        avg_diff = float(row.avg_difficulty or 5.0)
        return EmployeeMetricsDTO(
            user_id=row.user_id,
            full_name=row.full_name,
            team_name=row.team_name or "",
            role=row.role,
            hours_worked=hours_worked,
            projects_assigned=int(row.projects_assigned),
            projects_closed=int(row.projects_closed),
            tasks_closed=int(row.tasks_closed),
            subtasks_closed=int(row.subtasks_closed),
            activities_count=int(row.activities_count),
            completion_rate=round(cr, 1),
            iel=self._iel(cr, avg_diff),
            avg_progress=round(float(row.avg_progress or 0.0), 1),
            sla_avg_days=round(float(row.sla_avg_days or 0.0), 1),
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def get_employees_metrics(
        self, period: PeriodFilter, scope: ScopeFilter
    ) -> List[EmployeeMetricsDTO]:
        """
        All employee metrics in one CTE query (rule 1).
        Returns [] when no users match scope or no activity in period (rule 6).
        """
        start, end = self.resolve_period(period)
        rows = self._execute_employees_cte(start, end, scope)
        return [self._row_to_employee_dto(r) for r in rows]

    def get_org_metrics(
        self,
        period: PeriodFilter,
        scope: ScopeFilter,
        generated_by: str = "system",
    ) -> OrgMetricsDTO:
        """
        Org-level summary.  2 queries: head counts + employee CTE.
        """
        start, end = self.resolve_period(period)
        now = datetime.now(_BOGOTA)

        org_q = select(
            func.count(User.id.distinct()).label("total_employees"),
            func.count(User.team_id.distinct()).label("total_teams"),
        ).where(and_(*self._user_scope_clauses(scope)))
        org_row = self._db.execute(org_q).one()

        employees = self.get_employees_metrics(period, scope)
        total_hours = sum(e.hours_worked for e in employees)
        avg_iel = (
            sum(e.iel for e in employees) / len(employees) if employees else 0.0
        )

        scope_label: str = scope.mode.value
        if scope.mode == ScopeMode.TEAMS:
            scope_label = f"TEAMS({','.join(str(i) for i in (scope.team_ids or []))})"
        elif scope.mode == ScopeMode.EMPLOYEES:
            scope_label = f"EMPLOYEES({','.join(str(i) for i in (scope.user_ids or []))})"

        return OrgMetricsDTO(
            total_employees=org_row.total_employees,
            total_teams=org_row.total_teams,
            total_hours=round(total_hours, 1),
            avg_iel_org=round(avg_iel, 1),
            period_label=f"{start.isoformat()} — {end.isoformat()}",
            generated_at=now,
            generated_by=generated_by,
            scope_label=scope_label,
        )

    def get_teams_metrics(
        self, period: PeriodFilter, scope: ScopeFilter
    ) -> List[TeamMetricsDTO]:
        """
        Team-level aggregation.  2 queries: team metadata + employee CTE.
        weighted_completion_rate = Σ(cr × hours) / Σ(hours) per rule 3.
        Returns [] when no teams match scope (rule 6).
        """
        start, end = self.resolve_period(period)

        # 1 query: team metadata
        team_q = select(
            Team.id,
            Team.name.label("team_name"),
            func.coalesce(User.display_name, "").label("leader_name"),
        ).outerjoin(User, Team.leader_id == User.id)

        if scope.mode == ScopeMode.TEAMS:
            team_q = team_q.where(Team.id.in_(scope.team_ids))
        elif scope.mode == ScopeMode.EMPLOYEES:
            member_teams = select(User.team_id).where(
                User.id.in_(scope.user_ids),
                User.team_id.isnot(None),
            )
            team_q = team_q.where(Team.id.in_(member_teams))
        else:  # FULL — only teams with at least one active member
            active_teams = select(User.team_id).where(
                User.is_active == True, User.team_id.isnot(None)
            )
            team_q = team_q.where(Team.id.in_(active_teams))

        team_meta = {
            r.id: (r.team_name, r.leader_name)
            for r in self._db.execute(team_q).fetchall()
        }

        if not team_meta:
            return []

        # 1 query: all employee rows (with team_id)
        rows = self._execute_employees_cte(start, end, scope)

        by_team: dict = defaultdict(list)
        for row in rows:
            if row.team_id is not None:
                by_team[row.team_id].append(row)

        result: List[TeamMetricsDTO] = []
        for team_id, (team_name, leader_name) in team_meta.items():
            members = by_team.get(team_id, [])
            total_seconds = sum(float(r.total_seconds or 0) for r in members)
            total_hours = total_seconds / 3600
            tasks_closed = sum(int(r.tasks_closed or 0) for r in members)
            subtasks_closed = sum(int(r.subtasks_closed or 0) for r in members)
            activities_count = sum(int(r.activities_count or 0) for r in members)
            projects_assigned = sum(int(r.projects_assigned or 0) for r in members)
            projects_closed = sum(int(r.projects_closed or 0) for r in members)

            # Rule 3: weighted_completion_rate = Σ(cr × hours) / Σ(hours)
            wcr_num = 0.0
            hours_sum = 0.0
            iel_sum = 0.0
            progress_sum = 0.0
            for r in members:
                h = float(r.total_seconds or 0) / 3600
                cr = self._completion_rate(
                    int(r.total_items or 0), int(r.total_completed or 0)
                )
                avg_diff = float(r.avg_difficulty or 5.0)
                wcr_num += cr * h
                hours_sum += h
                iel_sum += self._iel(cr, avg_diff)
                progress_sum += float(r.avg_progress or 0.0)

            # Rule 3: Σhours = 0 → 0.0, never raises ZeroDivisionError
            weighted_cr = (wcr_num / hours_sum) if hours_sum > 0 else 0.0
            n = len(members)

            result.append(
                TeamMetricsDTO(
                    team_id=team_id,
                    team_name=team_name,
                    leader_name=leader_name,
                    members_count=n,
                    total_hours=round(total_hours, 1),
                    avg_hours_per_member=round(total_hours / n, 1) if n else 0.0,
                    tasks_closed=tasks_closed,
                    subtasks_closed=subtasks_closed,
                    activities_count=activities_count,
                    projects_active=max(0, projects_assigned - projects_closed),
                    projects_closed=projects_closed,
                    avg_iel=round(iel_sum / n, 1) if n else 0.0,
                    avg_progress=round(progress_sum / n, 1) if n else 0.0,
                    weighted_completion_rate=round(weighted_cr, 1),
                )
            )

        return result

    def get_team_detail(self, team_id: int, period: PeriodFilter) -> TeamDetailDTO:
        """
        Full team detail: header + member rows + project list.
        3 queries: team meta (via get_teams_metrics) + CTE + projects.
        """
        scope = ScopeFilter(mode=ScopeMode.TEAMS, team_ids=[team_id])
        start, end = self.resolve_period(period)
        today = datetime.now(_BOGOTA).date()

        teams = self.get_teams_metrics(period, scope)
        header = teams[0] if teams else TeamMetricsDTO(
            team_id=team_id, team_name="", leader_name="",
            members_count=0, total_hours=0.0, avg_hours_per_member=0.0,
            tasks_closed=0, subtasks_closed=0, activities_count=0,
            projects_active=0, projects_closed=0,
            avg_iel=0.0, avg_progress=0.0, weighted_completion_rate=0.0,
        )

        members = self.get_employees_metrics(period, scope)
        owner_ids = [m.user_id for m in members]
        projects = self._get_projects(start, end, today, owner_ids) if owner_ids else []

        return TeamDetailDTO(header=header, members=members, projects=projects)

    def get_employee_detail(self, user_id: int, period: PeriodFilter) -> EmployeeDetailDTO:
        """
        Full employee detail: header + projects + tasks by status + activities by type.
        4 queries: CTE + projects + tasks_by_status + activities_by_type.
        """
        scope = ScopeFilter(mode=ScopeMode.EMPLOYEES, user_ids=[user_id])
        start, end = self.resolve_period(period)
        today = datetime.now(_BOGOTA).date()

        employees = self.get_employees_metrics(period, scope)
        if not employees:
            # Rule 6: return zeros, do not raise
            header = EmployeeMetricsDTO(
                user_id=user_id, full_name="", team_name="", role="member",
                hours_worked=0.0, projects_assigned=0, projects_closed=0,
                tasks_closed=0, subtasks_closed=0, activities_count=0,
                completion_rate=0.0, iel=0.0, avg_progress=0.0, sla_avg_days=0.0,
            )
        else:
            header = employees[0]

        return EmployeeDetailDTO(
            header=header,
            projects=self._get_projects(start, end, today, [user_id]),
            tasks_by_status=self._get_tasks_by_status(start, end, user_id),
            activities_by_type=self._get_activities_by_type(start, end, user_id),
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_projects(
        self,
        start: date,
        end: date,
        today: date,
        owner_ids: List[int],
    ) -> List[ProjectMetricsDTO]:
        """
        Project rows for given owners.
        Correlated scalar subqueries avoid Cartesian product from double-joining
        TimeLog and Subtask to the same Task row.
        """
        if not owner_ids:
            return []

        hours_subq = (
            select(func.coalesce(func.sum(TimeLog.seconds), 0))
            .where(
                TimeLog.task_id == Task.id,
                TimeLog.log_date >= start,
                TimeLog.log_date <= end,
            )
            .correlate(Task)
            .scalar_subquery()
        )

        subtasks_subq = (
            select(
                func.coalesce(
                    func.sum(case((Subtask.completed == True, 1), else_=0)), 0
                )
            )
            .where(Subtask.task_id == Task.id)
            .correlate(Task)
            .scalar_subquery()
        )

        proj_q = select(
            Task.id.label("project_id"),
            Task.title.label("project_name"),
            Task.column_status.label("status"),
            func.coalesce(Task.progress, 0).label("avg_progress"),
            Task.deadline,
            Task.completed_at,
            hours_subq.label("hours_seconds"),
            subtasks_subq.label("tasks_closed"),
        ).where(
            Task.deleted_at.is_(None),
            Task.type == "project",
            Task.owner_id.in_(owner_ids),
            or_(
                and_(
                    func.date(Task.created_at) >= start,
                    func.date(Task.created_at) <= end,
                ),
                and_(
                    Task.column_status == "completed",
                    func.date(Task.completed_at) >= start,
                    func.date(Task.completed_at) <= end,
                ),
                Task.column_status != "completed",  # always include active projects
            ),
        )

        rows = self._db.execute(proj_q).fetchall()
        result: List[ProjectMetricsDTO] = []
        for row in rows:
            # Rule 4: days_vs_deadline
            if row.deadline:
                if row.status == "completed" and row.completed_at:
                    completed_date = (
                        row.completed_at.date()
                        if hasattr(row.completed_at, "date")
                        else row.completed_at
                    )
                    days_vs = (row.deadline - completed_date).days
                else:
                    days_vs = (row.deadline - today).days
            else:
                days_vs = 0

            result.append(
                ProjectMetricsDTO(
                    project_id=row.project_id,
                    project_name=row.project_name,
                    status=row.status,
                    avg_progress=float(row.avg_progress or 0),
                    hours_invested=round(float(row.hours_seconds or 0) / 3600, 1),
                    tasks_closed=int(row.tasks_closed or 0),
                    deadline=row.deadline,
                    days_vs_deadline=days_vs,
                )
            )

        return result

    def _get_tasks_by_status(
        self, start: date, end: date, owner_id: int
    ) -> Dict[str, dict]:
        rows = self._db.execute(
            select(
                Task.column_status,
                func.count(Task.id).label("count"),
            )
            .where(
                Task.owner_id == owner_id,
                Task.deleted_at.is_(None),
                Task.type == "task",
                func.date(Task.created_at) >= start,
                func.date(Task.created_at) <= end,
            )
            .group_by(Task.column_status)
        ).fetchall()
        return {r.column_status: {"count": int(r.count)} for r in rows}

    def _get_activities_by_type(
        self, start: date, end: date, owner_id: int
    ) -> Dict[str, dict]:
        rows = self._db.execute(
            select(
                Activity.type,
                func.count(Activity.id).label("count"),
            )
            .where(
                Activity.owner_id == owner_id,
                Activity.deleted_at.is_(None),
                func.date(Activity.created_at) >= start,
                func.date(Activity.created_at) <= end,
            )
            .group_by(Activity.type)
        ).fetchall()
        return {r.type: {"count": int(r.count)} for r in rows}
