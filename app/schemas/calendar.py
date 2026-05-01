from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import ConfigDict
from app.schemas.base import UTCModel


class CalendarEventOut(UTCModel):
    """Wire shape for /api/calendar/events.

    Matches `CalendarEvent` 1:1; defined separately so we can evolve the
    public contract without breaking the internal dataclass.
    """
    id: str
    uid: str
    title: str
    start_utc: datetime
    end_utc: datetime
    all_day: bool = False
    location: Optional[str] = None
    description: Optional[str] = None
    calendar_id: Optional[str] = None
    calendar_name: Optional[str] = None
    color: Optional[str] = None
    organizer: Optional[str] = None
    status: Optional[Literal["CONFIRMED", "TENTATIVE", "CANCELLED"]] = None
    recurrence_id: Optional[str] = None
    source: str = "nextcloud"
    deep_link: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class CalendarEventsResponse(UTCModel):
    events: list[CalendarEventOut]
    # Mirrors `EventQueryResult.source`. Useful for the frontend to decide
    # whether to surface a "actualizando…" indicator when source=="stale".
    cache: Literal["fresh", "stale", "miss"]


class InvalidateResponse(UTCModel):
    deleted: int
