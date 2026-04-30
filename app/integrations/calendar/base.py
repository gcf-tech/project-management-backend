"""Abstract calendar-provider contract.

Implementations live in this package (e.g. nextcloud.py). Anything outside
`app.integrations.calendar.*` MUST depend only on this ABC + the dataclasses
in `models.py`, never on a concrete adapter — that keeps Nextcloud-isms
contained.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Iterable, Optional

from app.integrations.calendar.models import CalendarEvent, CalendarRef


class CalendarProviderError(Exception):
    """Raised when the upstream provider returns an unrecoverable error.

    Callers should catch this at the service-layer boundary and translate to
    a graceful HTTP response (5xx with a friendly message, never leak stack
    traces or upstream credentials).
    """


class CalendarAuthError(CalendarProviderError):
    """Provider rejected the user's credentials (401/403).

    Surfaced separately so the API layer can return 401 to the client and
    optionally trigger an OAuth refresh on the front-end.
    """


class CalendarProvider(ABC):
    """Per-request provider instance — never share across users.

    Each `CalendarProvider` is bound to a specific `(user, credentials)`
    pair and MUST refuse to operate on any other user's data. Concrete
    adapters take credentials in their constructor; the methods below take
    only data parameters.
    """

    @abstractmethod
    async def list_calendars(self) -> list[CalendarRef]:
        """Return every calendar the bound user has read access to."""

    @abstractmethod
    async def list_events(
        self,
        calendar_ids: Optional[Iterable[str]],
        range_start_utc: datetime,
        range_end_utc: datetime,
    ) -> list[CalendarEvent]:
        """Return all events overlapping `[range_start_utc, range_end_utc)`.

        Recurring events are expanded — one CalendarEvent per occurrence
        inside the range. When `calendar_ids` is None, every accessible
        calendar is queried.
        """
