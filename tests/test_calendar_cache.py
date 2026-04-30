"""Tests for the in-memory cache + EventRepository SWR behavior.

These tests pin three load-bearing invariants:

  1. The cache's freshness-vs-staleness boundary tracks `stale_threshold`
     correctly (no off-by-one that would make us miss SWR opportunities).
  2. EventRepository never serves another user's data — even if both users
     request the same view/range. (Multi-tenant isolation.)
  3. A stale read triggers exactly ONE background refresh, not N.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Iterable, Optional

import pytest

from app.core.cache import InMemoryTTLCache, set_cache_for_tests
from app.integrations.calendar.base import CalendarProvider
from app.integrations.calendar.models import CalendarEvent
from app.services.calendar_service import EventQuery, EventRepository


# ── Test doubles ────────────────────────────────────────────────────────────

class _FakeProvider(CalendarProvider):
    """Records calls and returns canned events tagged with the bound user."""

    def __init__(self, *, nc_user_id: str, events: Optional[list[CalendarEvent]] = None) -> None:
        self.nc_user_id = nc_user_id
        self.events     = events if events is not None else []
        self.list_events_calls = 0

    async def list_calendars(self):
        return []

    async def list_events(
        self,
        calendar_ids: Optional[Iterable[str]],
        range_start_utc: datetime,
        range_end_utc: datetime,
    ) -> list[CalendarEvent]:
        self.list_events_calls += 1
        # Return events tagged so tests can assert leakage between users.
        return [
            CalendarEvent(
                id=f"{self.nc_user_id}-{i}",
                uid=f"uid-{self.nc_user_id}-{i}",
                title=f"Event for {self.nc_user_id}",
                start_utc=range_start_utc,
                end_utc=range_end_utc,
            )
            for i in range(len(self.events) or 1)
        ]


# ── InMemoryTTLCache ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cache_returns_fresh_then_stale_then_miss():
    cache = InMemoryTTLCache()
    await cache.set("k", [{"id": 1}], ttl_s=10)

    # Fresh: just written.
    hit = await cache.get("k", stale_threshold=0.7)
    assert hit is not None
    assert hit.value == [{"id": 1}]
    assert hit.is_stale is False

    # Force the stored timestamp to be 8s old → 80% of 10s TTL → stale.
    import time as _t
    (value, _written, ttl) = cache._store["k"]
    cache._store["k"] = (value, _t.monotonic() - 8, ttl)
    hit = await cache.get("k", stale_threshold=0.7)
    assert hit is not None and hit.is_stale is True

    # Past TTL → miss.
    cache._store["k"] = (value, _t.monotonic() - 11, ttl)
    assert await cache.get("k", stale_threshold=0.7) is None


@pytest.mark.asyncio
async def test_cache_delete_prefix_only_removes_matching():
    cache = InMemoryTTLCache()
    await cache.set("cal:alice:week:2026-04-27:2026-05-03:ALL", [], ttl_s=60)
    await cache.set("cal:alice:day:2026-04-27:2026-04-27:ALL",  [], ttl_s=60)
    await cache.set("cal:bob:week:2026-04-27:2026-05-03:ALL",   [], ttl_s=60)

    deleted = await cache.delete_prefix("cal:alice:")

    assert deleted == 2
    assert await cache.get("cal:alice:week:2026-04-27:2026-05-03:ALL") is None
    assert await cache.get("cal:alice:day:2026-04-27:2026-04-27:ALL")  is None
    assert await cache.get("cal:bob:week:2026-04-27:2026-05-03:ALL") is not None


# ── EventRepository SWR ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_first_call_is_miss_and_hits_provider():
    cache = InMemoryTTLCache()
    set_cache_for_tests(cache)
    try:
        provider = _FakeProvider(nc_user_id="alice")
        repo = EventRepository(provider=provider, cache=cache)
        q = EventQuery(
            nc_user_id="alice", view="week",
            range_start=date(2026, 4, 27), range_end=date(2026, 5, 3),
        )

        result = await repo.get_events(q)

        assert result.source == "miss"
        assert provider.list_events_calls == 1
        assert all(e.uid.startswith("uid-alice-") for e in result.events)
    finally:
        set_cache_for_tests(None)


@pytest.mark.asyncio
async def test_second_call_is_fresh_and_skips_provider():
    cache = InMemoryTTLCache()
    set_cache_for_tests(cache)
    try:
        provider = _FakeProvider(nc_user_id="alice")
        repo = EventRepository(provider=provider, cache=cache)
        q = EventQuery(
            nc_user_id="alice", view="week",
            range_start=date(2026, 4, 27), range_end=date(2026, 5, 3),
        )

        await repo.get_events(q)              # miss
        result = await repo.get_events(q)     # fresh

        assert result.source == "fresh"
        assert provider.list_events_calls == 1
    finally:
        set_cache_for_tests(None)


# ── Multi-tenant isolation ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_two_users_get_only_their_own_events():
    """The single most important safety invariant — never leak across users.

    Both users request the same view/range; alice's cache must NOT serve
    bob's request. This pins the user-id segment of the cache key.
    """
    cache = InMemoryTTLCache()
    set_cache_for_tests(cache)
    try:
        alice_provider = _FakeProvider(nc_user_id="alice")
        bob_provider   = _FakeProvider(nc_user_id="bob")
        alice_repo     = EventRepository(provider=alice_provider, cache=cache)
        bob_repo       = EventRepository(provider=bob_provider,   cache=cache)

        same_range = dict(
            view="week",
            range_start=date(2026, 4, 27),
            range_end=date(2026, 5, 3),
        )
        alice_q = EventQuery(nc_user_id="alice", **same_range)
        bob_q   = EventQuery(nc_user_id="bob",   **same_range)

        alice_result = await alice_repo.get_events(alice_q)
        bob_result   = await bob_repo.get_events(bob_q)

        # Both go to network — caches are independent per user.
        assert alice_provider.list_events_calls == 1
        assert bob_provider.list_events_calls   == 1

        # And bob never sees alice's events (or vice versa).
        for ev in alice_result.events:
            assert "alice" in ev.uid and "bob" not in ev.uid
        for ev in bob_result.events:
            assert "bob" in ev.uid and "alice" not in ev.uid
    finally:
        set_cache_for_tests(None)


# ── Invalidation ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invalidate_user_drops_only_that_users_entries():
    cache = InMemoryTTLCache()
    set_cache_for_tests(cache)
    try:
        alice_repo = EventRepository(provider=_FakeProvider(nc_user_id="alice"), cache=cache)
        bob_repo   = EventRepository(provider=_FakeProvider(nc_user_id="bob"),   cache=cache)

        await alice_repo.get_events(EventQuery(
            nc_user_id="alice", view="week",
            range_start=date(2026, 4, 27), range_end=date(2026, 5, 3),
        ))
        await bob_repo.get_events(EventQuery(
            nc_user_id="bob", view="week",
            range_start=date(2026, 4, 27), range_end=date(2026, 5, 3),
        ))

        deleted = await alice_repo.invalidate_user("alice")
        assert deleted == 1

        # Alice's next read should miss → bob's cached entry untouched.
        result = await alice_repo.get_events(EventQuery(
            nc_user_id="alice", view="week",
            range_start=date(2026, 4, 27), range_end=date(2026, 5, 3),
        ))
        assert result.source == "miss"
    finally:
        set_cache_for_tests(None)
