"""Nextcloud CalDAV adapter.

Implements `CalendarProvider` against a Nextcloud server's CalDAV endpoint.
Each instance is bound to ONE user via their access token — it must not be
reused or shared across users (that would be a multi-tenant data leak).

The `caldav` library is synchronous; we wrap its calls in `asyncio.to_thread`
so the FastAPI event loop doesn't block. Cache hits in the service layer
mean the slow path runs only on miss / background revalidation, so the
thread overhead is negligible in steady state.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Iterable, Optional

import caldav
import recurring_ical_events
import vobject
from caldav.lib.error import AuthorizationError, NotFoundError, DAVError

from app.core.config import (
    CALDAV_AUTH_MODE,
    CALDAV_TIMEOUT_S,
    CALDAV_USER_URL_TEMPLATE,
    NC_URL,
)
from app.integrations.calendar.base import (
    CalendarAuthError,
    CalendarProvider,
    CalendarProviderError,
)
from app.integrations.calendar.models import CalendarEvent, CalendarRef

logger = logging.getLogger(__name__)


class NextcloudCalDAVAdapter(CalendarProvider):
    """One adapter instance per (user, request). NEVER cache or share."""

    def __init__(self, *, nc_user_id: str, access_token: str) -> None:
        if not nc_user_id or not access_token:
            # Fail fast — never silently fall back to anonymous calls,
            # which on Nextcloud would either 401 or (worse) return public
            # calendars belonging to another principal.
            raise CalendarAuthError("nc_user_id and access_token are required")
        self._nc_user_id   = nc_user_id
        self._access_token = access_token
        self._user_url     = CALDAV_USER_URL_TEMPLATE.format(nc_user_id=nc_user_id)
        self._client: Optional[caldav.DAVClient] = None
        self._principal: Optional[caldav.Principal] = None

    # ── auth ────────────────────────────────────────────────────────────────

    def _build_client(self) -> caldav.DAVClient:
        """Construct the underlying CalDAV client.

        `bearer` mode passes the OAuth2 access token in `Authorization`.
        `app_password` is reserved for future use; it would read an
        encrypted token from the DB (not implemented here — see ADR-005).
        """
        if CALDAV_AUTH_MODE == "bearer":
            # caldav>=1.3 supports a `headers=` kwarg that is merged into
            # every request, so we can inject Authorization without ever
            # writing the token to the URL or to a Basic Auth tuple.
            return caldav.DAVClient(
                url=self._user_url,
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=CALDAV_TIMEOUT_S,
            )
        if CALDAV_AUTH_MODE == "app_password":
            # The token in this case is a plain App Password fetched (and
            # decrypted) by the caller before constructing the adapter.
            return caldav.DAVClient(
                url=self._user_url,
                username=self._nc_user_id,
                password=self._access_token,
                timeout=CALDAV_TIMEOUT_S,
            )
        raise CalendarProviderError(
            f"unsupported CALDAV_AUTH_MODE={CALDAV_AUTH_MODE!r}"
        )

    def _ensure_principal(self) -> caldav.Principal:
        if self._principal is None:
            self._client    = self._build_client()
            try:
                self._principal = self._client.principal()
            except AuthorizationError as e:
                raise CalendarAuthError(str(e)) from e
            except DAVError as e:
                raise CalendarProviderError(f"CalDAV principal lookup failed: {e}") from e
        return self._principal

    # ── public API ──────────────────────────────────────────────────────────

    async def list_calendars(self) -> list[CalendarRef]:
        return await asyncio.to_thread(self._list_calendars_sync)

    async def list_events(
        self,
        calendar_ids: Optional[Iterable[str]],
        range_start_utc: datetime,
        range_end_utc: datetime,
    ) -> list[CalendarEvent]:
        # Normalize the range to UTC-aware to keep recurrence math sane.
        range_start_utc = _ensure_utc(range_start_utc)
        range_end_utc   = _ensure_utc(range_end_utc)
        ids = list(calendar_ids) if calendar_ids is not None else None
        return await asyncio.to_thread(
            self._list_events_sync, ids, range_start_utc, range_end_utc
        )

    # ── sync workers (run inside asyncio.to_thread) ─────────────────────────

    def _list_calendars_sync(self) -> list[CalendarRef]:
        principal = self._ensure_principal()
        try:
            calendars = principal.calendars()
        except AuthorizationError as e:
            raise CalendarAuthError(str(e)) from e
        except DAVError as e:
            raise CalendarProviderError(f"CalDAV calendars query failed: {e}") from e

        refs: list[CalendarRef] = []
        for c in calendars:
            try:
                name  = c.get_display_name() or c.url.path.rstrip("/").rsplit("/", 1)[-1]
                color = _extract_calendar_color(c)
            except DAVError:
                # A single broken calendar shouldn't kill the whole listing.
                logger.warning("[caldav] failed to read calendar %s; skipping", c.url, exc_info=True)
                continue
            refs.append(CalendarRef(
                id=str(c.url),
                name=name,
                color=color,
                # Owner detection is non-trivial in CalDAV; treat shared
                # calendars (different principal in URL) as non-owner.
                is_owner=self._nc_user_id in str(c.url),
            ))
        return refs

    def _list_events_sync(
        self,
        calendar_ids: Optional[list[str]],
        range_start_utc: datetime,
        range_end_utc: datetime,
    ) -> list[CalendarEvent]:
        principal = self._ensure_principal()
        try:
            calendars = principal.calendars()
        except AuthorizationError as e:
            raise CalendarAuthError(str(e)) from e
        except DAVError as e:
            raise CalendarProviderError(f"CalDAV calendars query failed: {e}") from e

        if calendar_ids is not None:
            wanted = set(calendar_ids)
            calendars = [c for c in calendars if str(c.url) in wanted]

        events: list[CalendarEvent] = []
        for cal in calendars:
            try:
                cal_name  = cal.get_display_name() or "(unnamed)"
                cal_color = _extract_calendar_color(cal)
                # `expand=True` would be ideal but Nextcloud's REPORT
                # implementation is inconsistent with timezone handling on
                # recurrences. We fetch raw VEVENTs and expand client-side
                # via `recurring_ical_events`, which is well-tested.
                fetched = cal.search(
                    start=range_start_utc,
                    end=range_end_utc,
                    event=True,
                    expand=False,
                )
            except NotFoundError:
                continue
            except AuthorizationError as e:
                raise CalendarAuthError(str(e)) from e
            except DAVError as e:
                logger.warning(
                    "[caldav] search failed on %s: %s", cal.url, e, exc_info=True
                )
                continue

            for raw in fetched:
                events.extend(_expand_vevent(
                    raw=raw,
                    range_start_utc=range_start_utc,
                    range_end_utc=range_end_utc,
                    calendar_id=str(cal.url),
                    calendar_name=cal_name,
                    fallback_color=cal_color,
                    nc_base_url=NC_URL,
                ))

        return events


# ── helpers ────────────────────────────────────────────────────────────────

def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _extract_calendar_color(c: caldav.Calendar) -> Optional[str]:
    """Read the RFC 7986 / Apple CalDAV `calendar-color` property if set."""
    try:
        props = c.get_properties([caldav.elements.ical.CalendarColor()])
        return next(iter(props.values()), None)
    except DAVError:
        return None


def _to_utc(value) -> datetime:
    """Coerce a vobject date-or-datetime value into UTC datetime."""
    from datetime import date as _date
    if isinstance(value, datetime):
        return _ensure_utc(value)
    if isinstance(value, _date):
        # All-day events: anchor at UTC midnight so the range filter works.
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    raise TypeError(f"unsupported time value: {type(value)!r}")


def _expand_vevent(
    *,
    raw,
    range_start_utc: datetime,
    range_end_utc: datetime,
    calendar_id: str,
    calendar_name: str,
    fallback_color: Optional[str],
    nc_base_url: str,
) -> list[CalendarEvent]:
    """Parse a single raw CalDAV item and emit one event per occurrence."""
    try:
        ical_text = raw.data
        # `recurring_ical_events` accepts a parsed icalendar.Calendar; we
        # use vobject for the rest, so we feed both libraries from the same
        # raw text to keep behavior consistent.
        import icalendar
        ical_obj = icalendar.Calendar.from_ical(ical_text)
        occurrences = recurring_ical_events.of(ical_obj).between(
            range_start_utc, range_end_utc
        )
    except Exception as exc:
        logger.warning("[caldav] failed to parse event from %s: %s", calendar_id, exc)
        return []

    out: list[CalendarEvent] = []
    for occ in occurrences:
        try:
            uid     = str(occ.get("UID", "")) or _fallback_uid(raw)
            summary = str(occ.get("SUMMARY", "")) or "(sin título)"
            dtstart = occ.get("DTSTART")
            dtend   = occ.get("DTEND")
            if dtstart is None or dtend is None:
                continue

            from datetime import date as _date
            start_value = dtstart.dt
            end_value   = dtend.dt
            all_day     = isinstance(start_value, _date) and not isinstance(start_value, datetime)

            start_utc = _to_utc(start_value)
            end_utc   = _to_utc(end_value)

            location    = str(occ.get("LOCATION") or "") or None
            description = str(occ.get("DESCRIPTION") or "") or None
            organizer   = str(occ.get("ORGANIZER") or "") or None
            status      = str(occ.get("STATUS") or "").upper() or None
            if status not in {"CONFIRMED", "TENTATIVE", "CANCELLED"}:
                status = None

            recurrence_id_iso: Optional[str] = None
            recur_id_prop = occ.get("RECURRENCE-ID")
            if recur_id_prop is not None:
                try:
                    recurrence_id_iso = _to_utc(recur_id_prop.dt).isoformat()
                except (TypeError, AttributeError):
                    recurrence_id_iso = None

            occ_id = f"{uid}::{start_utc.isoformat()}"
            deep_link = (
                f"{nc_base_url.rstrip('/')}/apps/calendar/dayGridMonth/"
                f"{start_utc.strftime('%Y-%m-%d')}"
            )

            out.append(CalendarEvent(
                id=occ_id,
                uid=uid,
                title=summary,
                start_utc=start_utc,
                end_utc=end_utc,
                all_day=all_day,
                location=location,
                description=description,
                calendar_id=calendar_id,
                calendar_name=calendar_name,
                color=fallback_color,
                organizer=_strip_mailto(organizer),
                status=status,
                recurrence_id=recurrence_id_iso,
                source="nextcloud",
                deep_link=deep_link,
            ))
        except Exception as exc:
            logger.warning("[caldav] skipping malformed occurrence: %s", exc)
            continue
    return out


def _fallback_uid(raw) -> str:
    """When UID is missing (rare), derive a stable id from the raw etag/url."""
    try:
        return str(raw.url)
    except Exception:
        return "unknown-uid"


def _strip_mailto(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return value[7:] if value.lower().startswith("mailto:") else value
