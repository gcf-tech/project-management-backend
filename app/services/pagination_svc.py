from __future__ import annotations

import base64
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import or_, and_
from sqlalchemy.orm import Query

from app.schemas.pagination import PaginatedResponse

ACTIVE_CAP = 500


def encode_cursor(updated_at: datetime, item_id: str) -> str:
    raw = f"{updated_at.isoformat()}|{item_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> Tuple[datetime, str]:
    # Any malformed input becomes a 400 so callers never see a 500.
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
        updated_at_str, item_id = decoded.split("|", 1)
        updated_at = datetime.fromisoformat(updated_at_str)
        if not item_id:
            raise ValueError("empty id")
        return updated_at, item_id
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid cursor: must be a valid pagination token from a previous response",
        )


def paginate_cursor(
    query: Query,
    model,
    cursor: Optional[str],
    limit: int,
    serialize_fn: Callable,
) -> PaginatedResponse:
    if cursor:
        cursor_updated_at, cursor_id = decode_cursor(cursor)
        # Keyset seek for DESC: (ts < cursor_ts) OR (ts = cursor_ts AND id < cursor_id)
        query = query.filter(
            or_(
                model.updated_at < cursor_updated_at,
                and_(
                    model.updated_at == cursor_updated_at,
                    model.id < cursor_id,
                ),
            )
        )

    query = query.order_by(model.updated_at.desc(), model.id.desc())

    # limit+1 to detect whether a next page exists without a COUNT query.
    rows = query.limit(limit + 1).all()

    has_more = len(rows) > limit
    page_items = rows[:limit]

    next_cursor: Optional[str] = None
    if has_more and page_items:
        last = page_items[-1]
        next_cursor = encode_cursor(last.updated_at, last.id)

    return PaginatedResponse(
        items=[serialize_fn(item) for item in page_items],
        next_cursor=next_cursor,
        has_more=has_more,
        total=None,
    )
