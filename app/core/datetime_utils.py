from __future__ import annotations

from datetime import datetime, timezone


UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Datetime must include timezone offset (e.g. 2026-05-01T14:30:00-05:00)")
    return value.astimezone(UTC)


def to_rfc3339_z(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value if value.tzinfo is not None and value.utcoffset() is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")
