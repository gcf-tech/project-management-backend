"""Tests for the GET /blocks endpoint — rrule master de-duplication and dtstart helpers."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, or_
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import User, WeeklyBlock
from app.services.weekly_recurrence import serialize_block


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
    u = User(id=1, nc_user_id="test_u", display_name="Test")
    db.add(u)
    db.commit()
    return u


def _make_master(db, user_id: int, week_start: date, day_of_week: int, rrule: str) -> WeeklyBlock:
    block = WeeklyBlock(
        user_id=user_id,
        week_start=week_start,
        day_of_week=day_of_week,
        block_type="personal",
        title="Master block",
        start_time=time(9, 0),
        end_time=time(10, 0),
        recurrence="none",
        series_id=str(uuid4()),
        rrule_string=rrule,
        dtstart=datetime(week_start.year, week_start.month, week_start.day, 9, 0),
        rrule_until=datetime(2026, 5, 31),
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


class TestGetBlocksNoDuplicateMaster:
    """H1 fix: first query must exclude rrule masters so they appear only once."""

    def test_first_query_excludes_rrule_masters(self, db, user):
        ws = date(2026, 4, 27)
        master = _make_master(db, user.id, ws, day_of_week=1, rrule="FREQ=WEEKLY;BYDAY=MO,TU,WE")

        # First query (with H1 fix applied)
        first_query = db.query(WeeklyBlock).filter(
            WeeklyBlock.user_id == user.id,
            WeeklyBlock.week_start == ws,
            WeeklyBlock.rrule_string.is_(None),
        ).all()
        assert len(first_query) == 0, "master should not appear in concrete first query"

    def test_rrule_masters_query_finds_master_once(self, db, user):
        ws = date(2026, 4, 27)
        master = _make_master(db, user.id, ws, day_of_week=1, rrule="FREQ=WEEKLY;BYDAY=MO,TU,WE")

        week_end = ws + timedelta(days=6)
        rrule_masters = db.query(WeeklyBlock).filter(
            WeeklyBlock.user_id == user.id,
            WeeklyBlock.rrule_string.isnot(None),
            WeeklyBlock.week_start <= week_end,
            or_(WeeklyBlock.dtstart.is_(None), WeeklyBlock.dtstart <= datetime.combine(week_end, time.max)),
            or_(WeeklyBlock.rrule_until.is_(None), WeeklyBlock.rrule_until >= datetime.combine(ws, time.min)),
        ).all()

        assert len(rrule_masters) == 1
        serialized = serialize_block(rrule_masters[0], is_master=True)
        assert serialized["is_master"] is True
        assert serialized["id"] == master.id

    def test_master_not_returned_for_future_week_beyond_until(self, db, user):
        ws = date(2026, 4, 27)
        _make_master(db, user.id, ws, day_of_week=1, rrule="FREQ=WEEKLY;BYDAY=MO;UNTIL=20260531T000000Z")

        # Request for week AFTER rrule_until (June 2026)
        future_ws = date(2026, 6, 1)
        future_we = future_ws + timedelta(days=6)
        rrule_masters = db.query(WeeklyBlock).filter(
            WeeklyBlock.user_id == user.id,
            WeeklyBlock.rrule_string.isnot(None),
            WeeklyBlock.week_start <= future_we,
            or_(WeeklyBlock.rrule_until.is_(None), WeeklyBlock.rrule_until >= datetime.combine(future_ws, time.min)),
        ).all()

        assert len(rrule_masters) == 0, "master expired by rrule_until must not be returned"

    def test_master_returned_for_later_week_within_until(self, db, user):
        ws = date(2026, 4, 27)
        _make_master(db, user.id, ws, day_of_week=1, rrule="FREQ=WEEKLY;BYDAY=MO;UNTIL=20260531T000000Z")

        # Request for week within range (May 4)
        later_ws = date(2026, 5, 4)
        later_we = later_ws + timedelta(days=6)
        rrule_masters = db.query(WeeklyBlock).filter(
            WeeklyBlock.user_id == user.id,
            WeeklyBlock.rrule_string.isnot(None),
            WeeklyBlock.week_start <= later_we,
            or_(WeeklyBlock.rrule_until.is_(None), WeeklyBlock.rrule_until >= datetime.combine(later_ws, time.min)),
        ).all()

        assert len(rrule_masters) == 1


_JS_TO_PY_WEEKDAY = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}


def _compute_dtstart(week_start: date, day_of_week: int, start_t: time) -> datetime:
    """Mirror of the same helper in app/api/v1/weekly.py — tested here to avoid FastAPI imports."""
    py_wd = _JS_TO_PY_WEEKDAY.get(day_of_week, 0)
    offset = (py_wd - week_start.weekday() + 7) % 7
    return datetime.combine(week_start + timedelta(days=offset), start_t)


class TestComputeDtstart:
    """_compute_dtstart must resolve the correct calendar date for each JS day_of_week."""

    def test_monday_js1_on_monday_week_start(self):
        assert _compute_dtstart(date(2026, 4, 27), 1, time(9, 0)) == datetime(2026, 4, 27, 9, 0)

    def test_wednesday_js3_from_monday_week_start(self):
        assert _compute_dtstart(date(2026, 4, 27), 3, time(14, 30)) == datetime(2026, 4, 29, 14, 30)

    def test_friday_js5_from_monday_week_start(self):
        assert _compute_dtstart(date(2026, 4, 27), 5, time(8, 0)) == datetime(2026, 5, 1, 8, 0)

    def test_sunday_js0_from_monday_week_start(self):
        # Sunday = JS 0 → py weekday 6; offset from Mon(wd=0) = (6-0+7)%7 = 6 days
        assert _compute_dtstart(date(2026, 4, 27), 0, time(0, 0)) == datetime(2026, 5, 3, 0, 0)
