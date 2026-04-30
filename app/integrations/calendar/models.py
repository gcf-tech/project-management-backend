"""Provider-agnostic calendar dataclasses.

These are the *only* shapes the rest of the application sees. Adapter modules
(nextcloud.py, google.py, …) are responsible for translating their wire
formats into these structures so the service layer / API contract stays stable
across provider swaps.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass(frozen=True, slots=True)
class CalendarRef:
    """A handle to a single calendar belonging to a user.

    `id` is the provider-native identifier (CalDAV path for Nextcloud).
    The application never inspects `id` semantically — it's an opaque key
    used by the adapter to fetch events later.
    """
    id: str
    name: str
    color: Optional[str] = None
    # True when the user is the owner; False for shared/subscribed calendars.
    is_owner: bool = True


@dataclass(slots=True)
class CalendarEvent:
    """A single concrete (non-recurring) occurrence of a calendar event.

    Recurring series are *expanded* by the adapter into one CalendarEvent per
    occurrence inside the requested range. The application never sees raw
    RRULEs — that complexity stays inside the adapter.
    """
    # Stable identifier across responses. For recurring events we suffix the
    # occurrence start so the front-end can dedupe: "<uid>::<iso8601>".
    id: str
    # The original iCalendar UID (without the occurrence suffix). Two events
    # with the same `uid` belong to the same series.
    uid: str
    title: str
    # All times are timezone-aware UTC. The frontend converts to the user's
    # local timezone for display.
    start_utc: datetime
    end_utc: datetime
    all_day: bool = False
    location: Optional[str]    = None
    description: Optional[str] = None
    calendar_id: Optional[str] = None       # CalendarRef.id
    calendar_name: Optional[str] = None
    color: Optional[str]       = None       # inherited from CalendarRef when missing on event
    organizer: Optional[str]   = None
    # iCal STATUS: CONFIRMED / TENTATIVE / CANCELLED. Anything else → None.
    status: Optional[str]      = None
    # ISO 8601 timestamp of the original occurrence (only set for expanded
    # recurrences; lets the client deep-link to the master vs. the override).
    recurrence_id: Optional[str] = None
    # Provider tag — useful for logs / debugging / future multi-provider UI.
    source: str = "nextcloud"
    # URL to open the event in the source UI (deep link).
    deep_link: Optional[str] = None

    def to_dict(self) -> dict:
        """JSON-serializable dict — datetimes become ISO strings."""
        d = asdict(self)
        d["start_utc"] = self.start_utc.isoformat()
        d["end_utc"]   = self.end_utc.isoformat()
        return d
