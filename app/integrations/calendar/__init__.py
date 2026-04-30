"""Calendar provider abstraction (CalDAV today, Google/Outlook tomorrow).

Public API:
    - CalendarProvider  (ABC) — see base.py
    - CalendarRef, CalendarEvent (dataclasses) — see models.py
    - NextcloudCalDAVAdapter — see nextcloud.py (lazy import)

The Nextcloud adapter is NOT re-exported here on purpose: it pulls in the
`caldav` package, and tests that only exercise the cache / repository
layers shouldn't pay that import cost. Callers wanting the concrete adapter
import it directly: `from app.integrations.calendar.nextcloud import ...`.
"""
from app.integrations.calendar.base import CalendarProvider
from app.integrations.calendar.models import CalendarRef, CalendarEvent

__all__ = [
    "CalendarProvider",
    "CalendarRef",
    "CalendarEvent",
]
