"""Shared bounded-list helpers for read-only catalog and context tools.

These helpers are the single source of truth for the bounded collection
convention used by the supervisor context tools and the agent-registry
lookup tools: cap how many items a caller can pull back, page through a
sequence with an offset cursor, and wrap a page in a standard envelope that
tells the caller how much exists and how to fetch the next page.

The public function names here are intentionally specific
(``normalize_page_limit``, ``offset_page``, ``bounded_envelope``, and so on)
so they will not collide with unrelated module-level helpers elsewhere in the
codebase.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from .config import get_tool_page_default_limit, get_tool_page_max_limit

# Env-configurable (defaults unchanged); see config.py getters and .env.example:
#   TOOL_PAGE_DEFAULT_LIMIT (default 20), TOOL_PAGE_MAX_LIMIT (default 50).
DEFAULT_PAGE_LIMIT = get_tool_page_default_limit()
MAX_PAGE_LIMIT = get_tool_page_max_limit()


def normalize_page_limit(
    limit: int | None,
    *,
    default: int = DEFAULT_PAGE_LIMIT,
    maximum: int = MAX_PAGE_LIMIT,
) -> int:
    """Clamp a caller-supplied page size to the allowed range.

    A missing or unparseable value falls back to ``default``. The result is
    always at least one and never larger than ``maximum``.
    """

    try:
        value = int(limit if limit is not None else default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def parse_offset_cursor(cursor: str | int | None) -> int:
    """Read an opaque offset cursor as a non-negative integer.

    Cursors produced by :func:`offset_page` are simple stringified offsets.
    Anything unparseable is treated as the start of the sequence.
    """

    try:
        return max(0, int(cursor or 0))
    except (TypeError, ValueError):
        return 0


def offset_page(
    items: Sequence[Any],
    *,
    limit: int,
    cursor: str | int | None,
) -> tuple[list[Any], bool, str | None]:
    """Return one forward page of ``items`` starting at ``cursor``.

    Returns the page, whether more items remain after it, and the cursor a
    caller passes back to fetch the next page (``None`` when the page reaches
    the end).
    """

    offset = parse_offset_cursor(cursor)
    page = list(items[offset : offset + limit])
    next_offset = offset + len(page)
    has_more = next_offset < len(items)
    return page, has_more, str(next_offset) if has_more else None


def recent_page(
    items: Sequence[Any],
    *,
    limit: int,
    cursor: str | int | None,
) -> tuple[list[Any], bool, str | None]:
    """Return one page from the end of ``items`` working backwards.

    Useful when the most recent entries should appear first. ``cursor`` counts
    how many trailing items have already been served.
    """

    offset = parse_offset_cursor(cursor)
    end = max(0, len(items) - offset)
    start = max(0, end - limit)
    page = list(items[start:end])
    has_more = start > 0
    return page, has_more, str(offset + len(page)) if has_more else None


def bounded_envelope(
    page: Sequence[Any],
    *,
    total_count: int,
    offset: int,
    has_more: bool,
    next_cursor: str | None,
) -> dict[str, Any]:
    """Wrap one page of results in the standard bounded-list envelope.

    The envelope always reports how many items exist in total, how many were
    returned in this page, whether the page was truncated by the limit, and
    the offset/cursor a caller uses to fetch the next page.
    """

    next_offset = offset + len(page)
    return {
        "total_count": int(total_count),
        "returned_count": len(page),
        "truncated": bool(has_more),
        "next_offset": next_offset if has_more else None,
        "next_cursor": next_cursor,
    }


def substring_match(query: str | None, *fields: Any) -> bool:
    """Return whether a case-insensitive ``query`` appears in any field.

    A blank or missing ``query`` matches everything. Field values are coerced
    to text, so identifiers, names, and descriptions can all be searched
    together.
    """

    needle = str(query or "").strip().lower()
    if not needle:
        return True
    haystack = " ".join(str(field or "") for field in fields).lower()
    return needle in haystack


def search_and_paginate(
    items: Iterable[Any],
    *,
    query: str | None,
    search_fields: Any,
    limit: int | None,
    cursor: str | int | None,
    default_limit: int = DEFAULT_PAGE_LIMIT,
    maximum_limit: int = MAX_PAGE_LIMIT,
) -> tuple[list[Any], dict[str, Any]]:
    """Filter ``items`` by a substring ``query`` then return one bounded page.

    ``search_fields`` is a callable that maps one item to the tuple of values
    the ``query`` should match against. Returns the page plus the standard
    bounded-list envelope describing the full filtered total.
    """

    materialized = list(items)
    if str(query or "").strip():
        materialized = [
            item
            for item in materialized
            if substring_match(query, *_as_field_tuple(search_fields(item)))
        ]
    total_count = len(materialized)
    bounded_limit = normalize_page_limit(
        limit,
        default=default_limit,
        maximum=maximum_limit,
    )
    offset = parse_offset_cursor(cursor)
    page, has_more, next_cursor = offset_page(
        materialized,
        limit=bounded_limit,
        cursor=offset,
    )
    envelope = bounded_envelope(
        page,
        total_count=total_count,
        offset=offset,
        has_more=has_more,
        next_cursor=next_cursor,
    )
    envelope["limit"] = bounded_limit
    return page, envelope


def _as_field_tuple(value: Any) -> tuple[Any, ...]:
    if isinstance(value, Mapping):
        return tuple(value.values())
    if isinstance(value, (str, bytes)):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(value)
    return (value,)


__all__ = [
    "DEFAULT_PAGE_LIMIT",
    "MAX_PAGE_LIMIT",
    "bounded_envelope",
    "normalize_page_limit",
    "offset_page",
    "parse_offset_cursor",
    "recent_page",
    "search_and_paginate",
    "substring_match",
]
