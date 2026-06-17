from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.core.config import BUSINESS_TIMEZONE
from app.schemas.task_schemas import ActivityCreate, TaskCreate


_NY = ZoneInfo(BUSINESS_TIMEZONE)


def _base_retro_payload(**overrides):
    payload = {
        "title": "test-pasado",
        "type": "meeting",
        "isRetroactive": True,
        "startDate": "2026-05-18",
        "completedAt": "2026-05-20",
    }
    payload.update(overrides)
    return payload


def test_activity_completed_at_date_only_anchored_to_business_tz():
    """Regression: date-only 'YYYY-MM-DD' no longer 422s and is anchored at NY midnight."""
    activity = ActivityCreate.model_validate(_base_retro_payload())

    expected = datetime(2026, 5, 20, 0, 0, tzinfo=_NY).astimezone(timezone.utc)
    assert activity.completed_at == expected
    assert activity.completed_at.utcoffset().total_seconds() == 0
    assert activity.completed_at.date().isoformat() == "2026-05-20"


def test_task_completed_at_date_only_anchored_to_business_tz():
    payload = _base_retro_payload(title="proj-pasado")
    payload["type"] = "project"
    task = TaskCreate.model_validate(payload)

    expected = datetime(2026, 5, 20, 0, 0, tzinfo=_NY).astimezone(timezone.utc)
    assert task.completed_at == expected


def test_activity_completed_at_accepts_iso_with_offset():
    payload = _base_retro_payload(completedAt="2026-05-20T00:00:00-04:00")
    activity = ActivityCreate.model_validate(payload)

    assert activity.completed_at == datetime(2026, 5, 20, 4, 0, tzinfo=timezone.utc)


def test_activity_completed_at_accepts_iso_with_z():
    payload = _base_retro_payload(completedAt="2026-05-20T12:00:00Z")
    activity = ActivityCreate.model_validate(payload)

    assert activity.completed_at == datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)


def test_activity_completed_at_rejects_naive_datetime_string():
    payload = _base_retro_payload(completedAt="2026-05-20T12:00:00")
    with pytest.raises(ValidationError) as exc_info:
        ActivityCreate.model_validate(payload)

    err = str(exc_info.value).lower()
    assert "timezone" in err or "offset" in err


def test_activity_completed_at_rejects_garbage_string():
    payload = _base_retro_payload(completedAt="not-a-date")
    with pytest.raises(ValidationError):
        ActivityCreate.model_validate(payload)
