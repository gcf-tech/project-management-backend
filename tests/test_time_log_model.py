"""
TimeLog session model: verifies the append-only contract after migration.

Key invariants under test
  1. Multiple sessions per (user, task, log_date) are allowed.
  2. client_op_id UNIQUE is still enforced (idempotency key).
  3. end_at IS NULL correctly identifies open sessions.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import Task, TimeLog, User


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
    u = User(id=1, nc_user_id="u-test", display_name="Tester")
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def task(db, user):
    t = Task(id="task-001", title="Test Task", owner_id=user.id)
    db.add(t)
    db.commit()
    return t


class TestTimeLogSessionModel:

    def test_two_sessions_same_user_task_day(self, db, user, task):
        """Two TimeLog rows for the same (user, task, log_date) must not raise."""
        today = date(2026, 5, 6)
        log1 = TimeLog(
            user_id=user.id,
            task_id=task.id,
            log_date=today,
            seconds=3600,
            start_at=datetime(2026, 5, 6, 9, 0),
            end_at=datetime(2026, 5, 6, 10, 0),
            client_op_id="op-morning",
        )
        log2 = TimeLog(
            user_id=user.id,
            task_id=task.id,
            log_date=today,
            seconds=1800,
            start_at=datetime(2026, 5, 6, 14, 0),
            end_at=datetime(2026, 5, 6, 14, 30),
            client_op_id="op-afternoon",
        )
        db.add_all([log1, log2])
        db.commit()

        rows = (
            db.query(TimeLog)
            .filter_by(user_id=user.id, task_id=task.id, log_date=today)
            .all()
        )
        assert len(rows) == 2

    def test_duplicate_client_op_id_raises_integrity_error(self, db, user, task):
        """client_op_id UNIQUE constraint rejects duplicate idempotency keys."""
        today = date(2026, 5, 6)
        log1 = TimeLog(
            user_id=user.id,
            task_id=task.id,
            log_date=today,
            seconds=1800,
            start_at=datetime(2026, 5, 6, 9, 0),
            client_op_id="dup-key",
        )
        log2 = TimeLog(
            user_id=user.id,
            task_id=task.id,
            log_date=today,
            seconds=900,
            start_at=datetime(2026, 5, 6, 10, 0),
            client_op_id="dup-key",
        )
        db.add(log1)
        db.commit()
        db.add(log2)
        with pytest.raises(IntegrityError):
            db.commit()

    def test_open_session_query_by_end_at_null(self, db, user, task):
        """end_at IS NULL identifies open sessions; closed sessions are excluded."""
        today = date(2026, 5, 6)
        closed = TimeLog(
            user_id=user.id,
            task_id=task.id,
            log_date=today,
            seconds=3600,
            start_at=datetime(2026, 5, 6, 9, 0),
            end_at=datetime(2026, 5, 6, 10, 0),
            client_op_id="closed-op",
        )
        open_session = TimeLog(
            user_id=user.id,
            task_id=task.id,
            log_date=today,
            seconds=0,
            start_at=datetime(2026, 5, 6, 11, 0),
            end_at=None,
            client_op_id="open-op",
        )
        db.add_all([closed, open_session])
        db.commit()

        open_rows = db.query(TimeLog).filter(TimeLog.end_at.is_(None)).all()
        assert len(open_rows) == 1
        assert open_rows[0].client_op_id == "open-op"
