"""Calendar event repository — orchestrates provider + cache + prefetch.

This is the only module the API layer talks to for calendar events. It:
    1. Builds a per-user, per-range cache key.
    2. Implements the cache-aside / SWR contract:
         fresh hit  → return immediately, no upstream call
         stale hit  → return immediately, dispatch background refresh
         miss       → block on the upstream provider
    3. Optionally prefetches the next/previous window when asked.

Multi-tenant guarantee: every cache key is prefixed with the user's
nc_user_id, and the provider instance passed in is bound to ONE user.
There is NO code path where a cache lookup uses anything other than the
caller's identity.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Literal, Optional

from app.core.cache import Cache, CacheHit, get_cache
from app.core.config import (
    CACHE_STALE_THRESHOLD,
    CACHE_TTL_DAY,
    CACHE_TTL_MONTH,
    CACHE_TTL_QUARTER,
    CACHE_TTL_SEMESTER,
    CACHE_TTL_WEEK,
)
from app.integrations.calendar.base import (
    CalendarAuthError,
    CalendarProvider,
    CalendarProviderError,
)
from app.integrations.calendar.models import CalendarEvent

logger = logging.getLogger(__name__)


ViewKey = Literal["day", "week", "month", "quarter", "semester"]

_TTL_BY_VIEW: dict[ViewKey, int] = {
    "day":      CACHE_TTL_DAY,
    "week":     CACHE_TTL_WEEK,
    "month":    CACHE_TTL_MONTH,
    "quarter":  CACHE_TTL_QUARTER,
    "semester": CACHE_TTL_SEMESTER,
}


@dataclass(slots=True)
class EventQuery:
    nc_user_id: str
    view: ViewKey
    range_start: date
    range_end: date          # inclusive (matches frontend periodAdapters)
    calendar_ids: Optional[list[str]] = None


@dataclass(slots=True)
class EventQueryResult:
    events: list[CalendarEvent]
    source: Literal["fresh", "stale", "miss"]   # for logs / X-Cache header
    age_s: float


# Tracks in-flight refreshes per cache key so we never fire two concurrent
# upstream calls for the same (user, view, range).
_inflight_refresh: dict[str, asyncio.Task] = {}


class EventRepository:
    """One repo instance per request. Owns the per-user provider."""

    def __init__(self, *, provider: CalendarProvider, cache: Optional[Cache] = None) -> None:
        self._provider = provider
        self._cache    = cache or get_cache()

    # ── public API ──────────────────────────────────────────────────────────

    async def get_events(self, q: EventQuery) -> EventQueryResult:
        key   = self._cache_key(q)
        ttl_s = _TTL_BY_VIEW[q.view]

        hit: Optional[CacheHit] = await self._cache.get(key, stale_threshold=CACHE_STALE_THRESHOLD)

        if hit is not None and not hit.is_stale:
            return EventQueryResult(
                events=_decode_events(hit.value),
                source="fresh",
                age_s=hit.age_s,
            )

        if hit is not None and hit.is_stale:
            self._dispatch_refresh(key=key, query=q, ttl_s=ttl_s)
            return EventQueryResult(
                events=_decode_events(hit.value),
                source="stale",
                age_s=hit.age_s,
            )

        # Cache miss — block on the upstream provider.
        events = await self._fetch_and_store(key=key, query=q, ttl_s=ttl_s)
        return EventQueryResult(events=events, source="miss", age_s=0.0)

    async def prefetch_next_window(self, q: EventQuery) -> None:
        """Fire-and-forget: warm the cache for the window AFTER `q.range_end`.

        Does nothing if the next window is already cached fresh — keeps the
        upstream provider quiet during normal navigation.
        """
        next_q = _shift_window_forward(q)
        next_key = self._cache_key(next_q)
        existing = await self._cache.get(next_key, stale_threshold=1.0)
        if existing is not None and not existing.is_stale:
            return
        self._dispatch_refresh(key=next_key, query=next_q, ttl_s=_TTL_BY_VIEW[next_q.view])

    async def invalidate_user(self, nc_user_id: str) -> int:
        """Remove every cached entry for `nc_user_id`. Returns count removed."""
        return await self._cache.delete_prefix(f"cal:{nc_user_id}:")

    # ── internals ───────────────────────────────────────────────────────────

    @staticmethod
    def _cache_key(q: EventQuery) -> str:
        cals = "ALL" if q.calendar_ids is None else ",".join(sorted(q.calendar_ids))
        return f"cal:{q.nc_user_id}:{q.view}:{q.range_start.isoformat()}:{q.range_end.isoformat()}:{cals}"

    async def _fetch_and_store(
        self, *, key: str, query: EventQuery, ttl_s: int,
    ) -> list[CalendarEvent]:
        t0 = time.monotonic()
        # Range is inclusive on both ends → upstream gets [start 00:00, end+1 00:00) UTC.
        start_utc = datetime.combine(query.range_start, datetime.min.time()).replace(tzinfo=timezone.utc)
        end_utc   = datetime.combine(query.range_end + timedelta(days=1), datetime.min.time()).replace(tzinfo=timezone.utc)
        events = await self._provider.list_events(
            calendar_ids=query.calendar_ids,
            range_start_utc=start_utc,
            range_end_utc=end_utc,
        )
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "[calendar] fetch user=%s view=%s range=%s..%s events=%d elapsed_ms=%.1f",
            query.nc_user_id, query.view, query.range_start, query.range_end,
            len(events), elapsed_ms,
        )
        # Store the JSON-friendly form so RedisCache can serialize it.
        await self._cache.set(key, [e.to_dict() for e in events], ttl_s)
        return events

    def _dispatch_refresh(self, *, key: str, query: EventQuery, ttl_s: int) -> None:
        """Schedule a background fetch unless one is already running."""
        existing = _inflight_refresh.get(key)
        if existing is not None and not existing.done():
            return

        async def _runner() -> None:
            try:
                await self._fetch_and_store(key=key, query=query, ttl_s=ttl_s)
            except CalendarAuthError:
                # Auth failures shouldn't poison the cache — let the next
                # foreground request surface a clean 401 to the user.
                logger.info("[calendar] background refresh skipped: auth error key=%s", key)
            except CalendarProviderError as exc:
                logger.warning("[calendar] background refresh failed key=%s: %s", key, exc)
            except Exception:  # noqa: BLE001
                logger.exception("[calendar] background refresh crashed key=%s", key)
            finally:
                _inflight_refresh.pop(key, None)

        task = asyncio.create_task(_runner(), name=f"cal-refresh:{key}")
        _inflight_refresh[key] = task


# ── module helpers ──────────────────────────────────────────────────────────

def _decode_events(value: object) -> list[CalendarEvent]:
    """Reconstruct `CalendarEvent`s from the JSON-friendly cache shape."""
    if not isinstance(value, list):
        return []
    out: list[CalendarEvent] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            out.append(CalendarEvent(
                id=item["id"],
                uid=item["uid"],
                title=item["title"],
                start_utc=datetime.fromisoformat(item["start_utc"]),
                end_utc=datetime.fromisoformat(item["end_utc"]),
                all_day=item.get("all_day", False),
                location=item.get("location"),
                description=item.get("description"),
                calendar_id=item.get("calendar_id"),
                calendar_name=item.get("calendar_name"),
                color=item.get("color"),
                organizer=item.get("organizer"),
                status=item.get("status"),
                recurrence_id=item.get("recurrence_id"),
                source=item.get("source", "nextcloud"),
                deep_link=item.get("deep_link"),
            ))
        except (KeyError, ValueError):
            continue
    return out


def _shift_window_forward(q: EventQuery) -> EventQuery:
    """Compute the next window for prefetch (e.g. next week / next month)."""
    span_days = (q.range_end - q.range_start).days + 1
    new_start = q.range_end + timedelta(days=1)
    new_end   = new_start + timedelta(days=span_days - 1)
    return EventQuery(
        nc_user_id=q.nc_user_id,
        view=q.view,
        range_start=new_start,
        range_end=new_end,
        calendar_ids=q.calendar_ids,
    )
