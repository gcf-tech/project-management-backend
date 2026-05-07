"""
Hotfix regression tests for record_time_on_task / record_time_on_activity.

Covers:
  - start_at never None on INSERT (no 500 from NOT NULL constraint)
  - log_date derived from America/Bogota, not UTC
  - UPDATE preserves original start_at
  - UPDATE sets end_at when it was NULL
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import Activity, Task, TimeLog, User
from app.services.task_svc import record_time_on_activity, record_time_on_task


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
def user(db):
    u = User(id=1, nc_user_id="u-hotfix", display_name="Hotfix Tester")
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def task(db, user):
    t = Task(id="task-hotfix", title="Hotfix Task", owner_id=user.id)
    db.add(t)
    db.commit()
    return t


@pytest.fixture()
def activity(db, user):
    a = Activity(id="act-hotfix", title="Hotfix Activity", owner_id=user.id, type="other")
    db.add(a)
    db.commit()
    return a


_BOGOTA_DATE = date(2026, 5, 6)


class TestInsertWithoutStartAt:

    def test_insert_time_log_without_start_at_uses_utc_now(self, db, user, task):
        """start_at must never be None on INSERT — eliminates the NOT NULL 500."""
        with patch("app.services.task_svc._today_local", return_value=_BOGOTA_DATE):
            record_time_on_task(
                db, task,
                user_id=user.id,
                time_spent=1800,
                absolute_time=None,
                subtask_id=None,
                feedback=None,
                start_at=None,
            )

        log = db.query(TimeLog).filter_by(task_id=task.id).first()
        assert log is not None
        assert log.start_at is not None, "start_at must not be None after INSERT"
        assert log.end_at is not None, "end_at must not be None after INSERT"
        assert log.start_at <= log.end_at

    def test_insert_activity_without_start_at_uses_utc_now(self, db, user, activity):
        """Same guarantee for record_time_on_activity."""
        with patch("app.services.task_svc._today_local", return_value=_BOGOTA_DATE):
            record_time_on_activity(
                db, activity,
                user_id=user.id,
                time_spent=900,
                absolute_time=None,
                feedback=None,
                start_at=None,
            )

        log = db.query(TimeLog).filter_by(activity_id=activity.id).first()
        assert log is not None
        assert log.start_at is not None
        assert log.end_at is not None
        assert log.start_at <= log.end_at


class TestBogotaLogDate:

    def test_log_date_uses_bogota_tz_at_19h_local_after_utc_midnight(self, db, user, task):
        """At UTC 00:01 on May 7, Bogota time is 19:01 on May 6.
        log_date must be 2026-05-06, not 2026-05-07."""
        bogota_date = date(2026, 5, 6)
        with patch("app.services.task_svc._today_local", return_value=bogota_date):
            record_time_on_task(
                db, task,
                user_id=user.id,
                time_spent=3600,
                absolute_time=None,
                subtask_id=None,
                feedback=None,
                start_at=None,
            )

        log = db.query(TimeLog).filter_by(task_id=task.id).first()
        assert log.log_date == date(2026, 5, 6)


class TestUpdatePreservesStartAt:

    def test_existing_log_update_preserves_original_start_at(self, db, user, task):
        """Accumulating seconds onto an existing row must not overwrite start_at."""
        original_start = datetime(2026, 5, 6, 9, 0, 0, tzinfo=timezone.utc)
        existing = TimeLog(
            user_id=user.id,
            task_id=task.id,
            log_date=_BOGOTA_DATE,
            seconds=1800,
            start_at=original_start,
            end_at=datetime(2026, 5, 6, 9, 30, 0, tzinfo=timezone.utc),
        )
        db.add(existing)
        db.commit()

        with patch("app.services.task_svc._today_local", return_value=_BOGOTA_DATE):
            record_time_on_task(
                db, task,
                user_id=user.id,
                time_spent=900,
                absolute_time=None,
                subtask_id=None,
                feedback=None,
                start_at=None,
            )

        db.expire_all()
        log = db.query(TimeLog).filter_by(task_id=task.id, log_date=_BOGOTA_DATE).first()
        # SQLite strips tzinfo on roundtrip — compare wall-clock values only.
        assert log.start_at.replace(tzinfo=None) == original_start.replace(tzinfo=None), \
            "start_at must not be overwritten on UPDATE"
        assert log.seconds == 2700


class TestUpdateSetsEndAt:

    def test_existing_log_update_sets_end_at_to_now_when_was_null(self, db, user, task):
        """If end_at is NULL on an existing row, UPDATE must set it to utc_now()."""
        existing = TimeLog(
            user_id=user.id,
            task_id=task.id,
            log_date=_BOGOTA_DATE,
            seconds=600,
            start_at=datetime(2026, 5, 6, 8, 0, 0, tzinfo=timezone.utc),
            end_at=None,
        )
        db.add(existing)
        db.commit()

        with patch("app.services.task_svc._today_local", return_value=_BOGOTA_DATE):
            record_time_on_task(
                db, task,
                user_id=user.id,
                time_spent=300,
                absolute_time=None,
                subtask_id=None,
                feedback=None,
                start_at=None,
            )

        db.expire_all()
        log = db.query(TimeLog).filter_by(task_id=task.id, log_date=_BOGOTA_DATE).first()
        assert log.end_at is not None, "end_at must be set after UPDATE on a NULL row"
        assert log.seconds == 900
        # SQLite strips tzinfo on roundtrip — compare wall-clock values only.
        assert log.start_at.replace(tzinfo=None) == datetime(2026, 5, 6, 8, 0, 0)
