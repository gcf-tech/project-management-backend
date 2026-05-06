from __future__ import annotations

from datetime import date, datetime, time, timezone

import pytest

from app.db.models import TimeLog
from app.services.weekly_aggregator_service import _resolve_log_start_at


def _log(**kwargs) -> TimeLog:
    return TimeLog(**kwargs)


def test_start_at_not_null_returned_as_is():
    naive = datetime(2026, 1, 12, 10, 30, 0)
    log = _log(log_date=date(2026, 1, 12), start_at=naive, created_at=None, seconds=0)
    result = _resolve_log_start_at(log)
    assert result is not None
    assert result.tzinfo is not None
    assert result == datetime(2026, 1, 12, 10, 30, 0, tzinfo=timezone.utc)


def test_start_at_null_log_date_anchors_day_correctly():
    # created_at is 2026-01-13 (different day from log_date 2026-01-12)
    # → cross-day case: anchor time must be 09:00, day must be log_date
    log = _log(
        log_date=date(2026, 1, 12),
        start_at=None,
        created_at=datetime(2026, 1, 13, 12, 0, 0, tzinfo=timezone.utc),
        seconds=3600,
    )
    result = _resolve_log_start_at(log)
    assert result is not None
    assert result.date() == date(2026, 1, 12)  # NOT 2026-01-13


def test_start_at_null_same_day_uses_created_at_time_minus_duration():
    log = _log(
        log_date=date(2026, 1, 12),
        start_at=None,
        created_at=datetime(2026, 1, 12, 14, 0, 0, tzinfo=timezone.utc),
        seconds=3600,
    )
    result = _resolve_log_start_at(log)
    assert result == datetime(2026, 1, 12, 13, 0, 0, tzinfo=timezone.utc)


def test_start_at_null_different_day_anchors_at_9am():
    log = _log(
        log_date=date(2026, 1, 10),
        start_at=None,
        created_at=datetime(2026, 1, 12, 14, 0, 0, tzinfo=timezone.utc),
        seconds=0,
    )
    result = _resolve_log_start_at(log)
    assert result == datetime(2026, 1, 10, 9, 0, 0, tzinfo=timezone.utc)


def test_all_timestamps_null_returns_none():
    log = _log(log_date=None, start_at=None, created_at=None, seconds=0)
    assert _resolve_log_start_at(log) is None
