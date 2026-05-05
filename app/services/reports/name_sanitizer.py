"""Excel sheet-name sanitizer — enforces Excel naming constraints."""
from __future__ import annotations

import re
from typing import List, Optional, Union

_PROHIBITED = re.compile(r'[\\/?*\[\]:]')
_MULTI_SPACE = re.compile(r' {2,}')
_MAX_LEN = 31


def sanitize_sheet_name(
    raw_name: str,
    fallback_id: Optional[Union[int, str]] = None,
) -> str:
    """Return a valid Excel sheet name derived from *raw_name*.

    Prohibited chars (\\/?*[]:) are replaced with a space; runs of spaces are
    collapsed; the result is truncated so the total length stays ≤ 31.
    If *fallback_id* is given, a zero-padded suffix ``_NNNN`` is appended.
    """
    cleaned = _PROHIBITED.sub(" ", raw_name)
    cleaned = _MULTI_SPACE.sub(" ", cleaned).strip()

    suffix = f"_{str(fallback_id).zfill(4)}" if fallback_id is not None else ""
    max_base = _MAX_LEN - len(suffix)
    return cleaned[:max_base].rstrip() + suffix


def dedupe_sheet_names(names: List[str]) -> List[str]:
    """Append ``_2``, ``_3``, … to duplicates after sanitisation.

    Each name is sanitised before deduplication; the counter suffix is
    truncated to keep the total length ≤ 31.
    """
    sanitised = [sanitize_sheet_name(n) for n in names]
    seen: dict[str, int] = {}
    result: List[str] = []

    for name in sanitised:
        if name not in seen:
            seen[name] = 1
            result.append(name)
        else:
            seen[name] += 1
            counter = seen[name]
            suffix = f"_{counter}"
            base = name[: _MAX_LEN - len(suffix)]
            result.append(base + suffix)

    return result
