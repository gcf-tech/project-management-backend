from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import Task, TimeLog, User, WeeklyBlock
from app.schemas.task_schemas import TimeLogCreate
from app.services.weekly_recurrence import serialize_block
from pydantic import ValidationError


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


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def test_insert_bogota_offset_is_normalized_to_utc(db_session, seed_user_task):
    _, task = seed_user_task
    payload = TimeLogCreate.model_validate(
        {"logDate": "2026-05-01", "seconds": 1800, "startAt": "2026-05-01T08:00:00-05:00"}
    )

    created = TimeLog(
        user_id=1,
        task_id=task.id,
        log_date=date.fromisoformat(payload.logDate),
        seconds=payload.seconds,
        start_at=payload.startAt.astimezone(timezone.utc),
    )
    db_session.add(created)
    db_session.commit()
    db_session.refresh(created)

    assert created is not None
    assert _as_utc(created.start_at).isoformat().replace("+00:00", "Z") == "2026-05-01T13:00:00Z"


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


def test_naive_datetime_is_rejected_with_422_shape():
    with pytest.raises(ValidationError) as exc_info:
        TimeLogCreate.model_validate(
            {"logDate": "2026-05-01", "seconds": 1200, "startAt": "2026-05-01T08:00:00"}
        )
    assert "timezone" in str(exc_info.value).lower() or "aware" in str(exc_info.value).lower()
