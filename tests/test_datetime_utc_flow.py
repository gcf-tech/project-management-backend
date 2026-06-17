from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import Task, User, WeeklyBlock
from app.services.weekly_recurrence import serialize_block


@pytest.fixture()
def db_session():
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
def seed_user_task(db_session):
    user = User(id=1, nc_user_id="u-1", display_name="UTC User")
    task = Task(id="task-utc-1", title="UTC Task", owner_id=1, type="task")
    db_session.add_all([user, task])
    db_session.commit()
    return user, task


def test_weekly_blocks_serializes_dtstart_with_z(db_session, seed_user_task):
    user, task = seed_user_task
    block = WeeklyBlock(
        user_id=user.id,
        week_start=date(2026, 4, 27),
        day_of_week=1,
        block_type="task",
        task_id=task.id,
        start_time=time(9, 0),
        end_time=time(10, 0),
        recurrence="none",
        rrule_string="FREQ=WEEKLY;BYDAY=MO",
        dtstart=datetime(2026, 4, 27, 9, 0, tzinfo=timezone.utc),
    )
    db_session.add(block)
    db_session.commit()

    payload = serialize_block(block)
    assert payload["dtstart"].endswith("Z")
