"""HTTP surface for the calendar BFF.

Routes (mounted at /api/calendar):
    GET  /events                   — list events overlapping [start, end]
    POST /cache/invalidate         — drop the caller's cached entries

Response caching strategy:
    - Cache-Control: private, max-age = (TTL - 30s clamp) → mirrors the
      backend cache horizon, so a warm browser cache stays in sync.
    - ETag: SHA-256 of the canonical JSON body.
    - 304 when If-None-Match matches.
    - X-Cache header: fresh / stale / miss for diagnostics.
    - GZip is handled by the global GZipMiddleware in main.py (>1 KB).
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db
from app.core.cache import get_cache
from app.core.config import (
    CACHE_TTL_DAY, CACHE_TTL_MONTH, CACHE_TTL_QUARTER, CACHE_TTL_SEMESTER,
    CACHE_TTL_WEEK, CALDAV_MAX_RANGE_DAYS,
)
from app.integrations.calendar.base import CalendarAuthError, CalendarProviderError
from app.integrations.calendar.nextcloud import NextcloudCalDAVAdapter
from app.schemas.calendar import (
    CalendarEventOut, CalendarEventsResponse, InvalidateResponse,
)
from app.services.calendar_service import (
    EventQuery, EventRepository, ViewKey,
)

router = APIRouter()
logger = logging.getLogger(__name__)


_TTL_BY_VIEW: dict[str, int] = {
    "day":      CACHE_TTL_DAY,
    "week":     CACHE_TTL_WEEK,
    "month":    CACHE_TTL_MONTH,
    "quarter":  CACHE_TTL_QUARTER,
    "semester": CACHE_TTL_SEMESTER,
}


@router.get("/events", response_model=CalendarEventsResponse)
async def list_events(
    response: Response,
    start: date = Query(..., description="Inclusive range start (YYYY-MM-DD)"),
    end:   date = Query(..., description="Inclusive range end (YYYY-MM-DD)"),
    view:  ViewKey = Query("week"),
    prefetch: bool = Query(False, description="Warm next adjacent window in background"),
    calendar_ids: Optional[list[str]] = Query(None, description="Filter to specific calendars"),
    authorization: Annotated[str | None, Header()] = None,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
    db: Session = Depends(get_db),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    if end < start:
        raise HTTPException(status_code=400, detail="end must be >= start")
    if (end - start).days > CALDAV_MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"range exceeds {CALDAV_MAX_RANGE_DAYS} day limit",
        )

    # Strip the "Bearer " prefix — CalDAV adapter re-prefixes when calling.
    access_token = _strip_bearer(authorization)
    adapter      = NextcloudCalDAVAdapter(
        nc_user_id=user.nc_user_id,
        access_token=access_token,
    )
    repo = EventRepository(provider=adapter)
    query = EventQuery(
        nc_user_id=user.nc_user_id,
        view=view,
        range_start=start,
        range_end=end,
        calendar_ids=calendar_ids,
    )

    try:
        result = await repo.get_events(query)
    except CalendarAuthError as e:
        # 401 lets the frontend trigger a token refresh + retry.
        logger.info("[calendar] auth error for user=%s: %s", user.nc_user_id, e)
        raise HTTPException(status_code=401, detail="CalDAV authentication failed") from e
    except CalendarProviderError as e:
        # 503 instead of 500 — provider is down, app itself is fine.
        logger.warning("[calendar] provider error for user=%s: %s", user.nc_user_id, e)
        raise HTTPException(
            status_code=503,
            detail="Calendar provider unavailable, try again shortly",
        ) from e

    if prefetch:
        # Fire-and-forget: warm the next window so the user can click "next"
        # without waiting on CalDAV.
        try:
            await repo.prefetch_next_window(query)
        except Exception:  # noqa: BLE001
            logger.exception("[calendar] prefetch dispatch failed")

    body_dicts = [e.to_dict() for e in result.events]
    payload = CalendarEventsResponse(
        events=[CalendarEventOut(**d) for d in body_dicts],
        cache=result.source,
    )

    # ── HTTP caching ────────────────────────────────────────────────────────
    canonical = json.dumps(
        {"events": body_dicts, "cache": result.source},
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    etag = f'"{hashlib.sha256(canonical).hexdigest()[:16]}"'

    # Browser TTL = backend TTL - 30s clamp (≥ 30s), so we never serve a
    # response the backend would already consider stale.
    browser_ttl = max(30, _TTL_BY_VIEW.get(view, CACHE_TTL_WEEK) - 30)

    response.headers["ETag"]          = etag
    response.headers["Cache-Control"] = f"private, max-age={browser_ttl}"
    response.headers["Vary"]          = "Authorization"
    response.headers["X-Cache"]       = result.source

    if if_none_match and if_none_match == etag:
        # Have to clear the response model on 304; FastAPI honors status_code.
        return Response(status_code=304, headers=dict(response.headers))

    return payload


@router.post("/cache/invalidate", response_model=InvalidateResponse)
async def invalidate_cache(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    """Drop every cached entry for the authenticated user.

    Scoped to the caller — there is no admin path that can wipe other users'
    caches via this endpoint.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = await get_current_user(authorization, db)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    cache = get_cache()
    deleted = await cache.delete_prefix(f"cal:{user.nc_user_id}:")
    logger.info(
        "[calendar] cache invalidated user=%s deleted=%d", user.nc_user_id, deleted
    )
    return InvalidateResponse(deleted=deleted)


# ── helpers ────────────────────────────────────────────────────────────────

def _strip_bearer(authorization: str) -> str:
    """Pull the raw access token out of an `Authorization: Bearer …` header."""
    if not authorization:
        return ""
    parts = authorization.strip().split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    # The frontend always sends Bearer, but accept the bare token too so
    # tests don't have to fake the prefix.
    return authorization.strip()
