from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import Activity, Task, TimeLog, User
from app.services.weekly_aggregator_service import get_unified_week


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture()
def seed(db):
    user = User(id=1, nc_user_id="u1", display_name="Test User")
    task = Task(id="task-1", title="Diseño API", owner_id=1, column_status="actively-working", type="task")
    activity = Activity(id="act-1", title="Reunión equipo", owner_id=1, type="other")
    db.add_all([user, task, activity])
    db.commit()
    return db


def _monday(ref: date | None = None) -> date:
    d = ref or date.today()
    return d - timedelta(days=d.weekday())


def test_task_log_appears_in_range(seed):
    monday = _monday()
    sunday = monday + timedelta(days=6)
    log = TimeLog(
        user_id=1,
        task_id="task-1",
        log_date=monday,
        seconds=18000,
        start_at=datetime(monday.year, monday.month, monday.day, 14, 0, 0),
    )
    seed.add(log)
    seed.commit()

    result = get_unified_week(seed, user_id=1, start_date=monday, end_date=sunday)

    assert len(result) == 1
    assert result[0].source == "task"
    assert result[0].duration_minutes == 300
    assert result[0].start_at.hour == 14
    assert result[0].title == "Diseño API"


def test_task_log_outside_range_excluded(seed):
    monday = _monday()
    prev_monday = monday - timedelta(days=7)
    log = TimeLog(
        user_id=1,
        task_id="task-1",
        log_date=prev_monday,
        seconds=3600,
        start_at=datetime(prev_monday.year, prev_monday.month, prev_monday.day, 10, 0, 0),
    )
    seed.add(log)
    seed.commit()

    result = get_unified_week(seed, user_id=1, start_date=monday, end_date=monday + timedelta(days=6))

    assert len(result) == 0


def test_activity_log_source_correct(seed):
    monday = _monday()
    wednesday = monday + timedelta(days=2)
    log = TimeLog(
        user_id=1,
        activity_id="act-1",
        log_date=wednesday,
        seconds=3600,
        start_at=datetime(wednesday.year, wednesday.month, wednesday.day, 10, 0, 0),
    )
    seed.add(log)
    seed.commit()

    result = get_unified_week(seed, user_id=1, start_date=monday, end_date=monday + timedelta(days=6))

    assert len(result) == 1
    assert result[0].source == "activity"
    assert result[0].source_ref_id == "act-1"
    assert result[0].title == "Reunión equipo"


def test_no_start_at_falls_back_to_midnight(seed):
    monday = _monday()
    tuesday = monday + timedelta(days=1)
    log = TimeLog(
        user_id=1,
        task_id="task-1",
        log_date=tuesday,
        seconds=7200,
        start_at=None,
    )
    seed.add(log)
    seed.commit()

    result = get_unified_week(seed, user_id=1, start_date=monday, end_date=monday + timedelta(days=6))

    assert len(result) == 1
    assert result[0].start_at.hour == 0
    assert result[0].start_at.minute == 0


def test_results_ordered_by_start_at(seed):
    monday = _monday()
    log_late = TimeLog(
        user_id=1,
        task_id="task-1",
        log_date=monday,
        seconds=3600,
        start_at=datetime(monday.year, monday.month, monday.day, 10, 0, 0),
    )
    log_early = TimeLog(
        user_id=1,
        activity_id="act-1",
        log_date=monday,
        seconds=1800,
        start_at=datetime(monday.year, monday.month, monday.day, 8, 0, 0),
    )
    seed.add_all([log_late, log_early])
    seed.commit()

    result = get_unified_week(seed, user_id=1, start_date=monday, end_date=monday + timedelta(days=6))

    assert len(result) == 2
    assert result[0].start_at < result[1].start_at
    assert result[0].start_at.hour == 8
