from __future__ import annotations

import base64
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import or_, and_
from sqlalchemy.orm import Query

from app.schemas.pagination import PaginatedResponse

ACTIVE_CAP = 500

_CURSOR_VERSION = "v2"


def encode_cursor(sort_value: Optional[datetime], item_id: str) -> str:
    sort_str = sort_value.isoformat() if sort_value is not None else ""
    raw = f"{_CURSOR_VERSION}|{sort_str}|{item_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> Tuple[Optional[datetime], str]:
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
        if not decoded.startswith(f"{_CURSOR_VERSION}|"):
            raise HTTPException(
                status_code=400,
                detail="Cursor format outdated, please refetch from start",
            )
        _, sort_str, item_id = decoded.split("|", 2)
        sort_value = datetime.fromisoformat(sort_str) if sort_str else None
        if not item_id:
            raise ValueError("empty id")
        return sort_value, item_id
    except HTTPException:
        raise
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
    sort_col=None,
    tie_breaker=None,
) -> PaginatedResponse:
    _sort_col = sort_col if sort_col is not None else model.updated_at
    _tie_col = tie_breaker if tie_breaker is not None else model.id
    _sort_key = _sort_col.key
    _tie_key = _tie_col.key

    if cursor:
        cursor_sort_val, cursor_tie_val = decode_cursor(cursor)
        query = query.filter(
            or_(
                _sort_col < cursor_sort_val,
                and_(
                    _sort_col == cursor_sort_val,
                    _tie_col < cursor_tie_val,
                ),
            )
        )

    query = query.order_by(_sort_col.desc(), _tie_col.desc())

    # limit+1 to detect whether a next page exists without a COUNT query.
    rows = query.limit(limit + 1).all()

    has_more = len(rows) > limit
    page_items = rows[:limit]

    next_cursor: Optional[str] = None
    if has_more and page_items:
        last = page_items[-1]
        next_cursor = encode_cursor(getattr(last, _sort_key), getattr(last, _tie_key))

    return PaginatedResponse(
        items=[serialize_fn(item) for item in page_items],
        next_cursor=next_cursor,
        has_more=has_more,
        total=None,
    )
