"""Exact tool-result views and caller-selected pagination.

Context capacity belongs to the shared model boundary. These helpers preserve
complete results and validate explicit cursors, revisions, and detail paths.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)

INVALID_RESULT_CURSOR = "invalid_result_cursor"
STALE_RESULT_CURSOR = "stale_result_cursor"


_FULL_TOOL_RESULTS_REQUESTED: ContextVar[bool] = ContextVar(
    "full_tool_results_requested",
    default=False,
)


@contextmanager
def full_tool_results_requested() -> Iterator[None]:
    """Let an application-side capture receive a lookup's complete result.

    Validator lookup capture stores the complete provider response and then
    serves the model its own bounded view; the package adapter must not page
    the result underneath it.
    """

    token = _FULL_TOOL_RESULTS_REQUESTED.set(True)
    try:
        yield
    finally:
        _FULL_TOOL_RESULTS_REQUESTED.reset(token)


def full_tool_results_are_requested() -> bool:
    return _FULL_TOOL_RESULTS_REQUESTED.get()


def env_positive_int(name: str, default: int, *, minimum: int = 1) -> int:
    """Read a positive integer setting shared with the backend config getters."""

    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid int value for %s: %s, using default %s", name, raw, default)
        return default
    if value < minimum:
        logger.warning("%s=%s is below minimum %s; using %s", name, value, minimum, minimum)
        return minimum
    return value


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def canonical_json(value: Any) -> str:
    """Stable JSON used for hashing and exact detail chunks."""

    return json.dumps(
        _plain(value),
        default=str,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def serialized_size(value: Any) -> int:
    """UTF-8 bytes of the larger model-visible serialization of ``value``.

    The Agents SDK stringifies structured tool returns (Python ``str``), while
    package transport, validator wrappers and replay use JSON with or without
    ASCII escaping. Measure each and keep the largest so Unicode, escapes and
    the measurement reflects escaping in each representation.
    """

    plain = _plain(value)
    unicode_json_bytes = len(
        json.dumps(plain, default=str, ensure_ascii=False).encode("utf-8")
    )
    # Default ``json.dumps`` escapes non-ASCII text, which can be longer than
    # its UTF-8 form (six characters per escaped code unit).
    ascii_json_bytes = len(json.dumps(plain, default=str))
    str_bytes = len(str(plain).encode("utf-8"))
    return max(unicode_json_bytes, ascii_json_bytes, str_bytes)


def content_sha256(value: Any) -> str:
    text = value if isinstance(value, str) else canonical_json(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def invalid_cursor(message: str, **details: Any) -> dict[str, Any]:
    """Explicit caller error for a malformed or out-of-range cursor."""

    return {
        "status": "invalid_request",
        "error_code": INVALID_RESULT_CURSOR,
        "message": message,
        **details,
    }


def clamp_page_limit(
    requested: Any,
    *,
    default: int,
    maximum: int,
    name: str = "limit",
) -> tuple[int, dict[str, Any]]:
    """Clamp a requested page size to ``maximum`` and describe what happened.

    ``None`` or ``0`` selects the default. Negative, fractional or non-numeric
    values raise ``ValueError`` so the caller returns an explicit error rather
    than guessing. Clamping an excessive request is normal, not a failure.
    """

    effective_default = max(1, min(int(default), int(maximum)))
    if requested is None or (not isinstance(requested, bool) and requested == 0):
        return effective_default, {
            f"requested_{name}": None,
            f"effective_{name}": effective_default,
            f"{name}_clamped": False,
        }
    if isinstance(requested, bool) or not isinstance(requested, int) or requested < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    effective = min(requested, int(maximum))
    return effective, {
        f"requested_{name}": requested,
        f"effective_{name}": effective,
        f"{name}_clamped": effective < requested,
        f"max_{name}": int(maximum),
    }


def requested_text_length(requested: Any, total: int) -> int:
    """An optional caller-selected range; omitted means the complete value."""
    if requested is None:
        return total
    if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
        raise ValueError("Requested text length must be a positive integer")
    return requested


def parse_offset(offset: Any, *, total: int, name: str = "offset") -> int:
    """Validate a forward offset against the current total.

    ``None`` means the start. An offset equal to ``total`` is an empty final
    page. Anything malformed, negative or past the end raises ``ValueError``;
    silently restarting at zero would duplicate records, and silently returning
    nothing would hide that the listing changed underneath the caller.
    """

    if offset is None:
        return 0
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise ValueError(f"{name} must be a non-negative integer")
    if offset < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    if offset > total:
        raise ValueError(
            f"{name} {offset} is past the end of the {total} available items; "
            "the listing may have changed. Restart from 0 or use the returned next offset."
        )
    return offset


def fit_page(
    items: Sequence[Any], *, start: int, limit: int,
    render: Callable[[list[Any], int], Any],
) -> tuple[Any, int]:
    """Render the complete caller-selected page, without a context quota."""
    page = list(items[start:min(len(items), start + max(0, limit))])
    return render(page, len(page)), len(page)


def fit_text_window(text: str, *, cursor: int, render: Callable[[str, int], Any]) -> Any:
    """Return every exact character from a validated cursor to the end."""
    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0 or cursor > len(text):
        raise ValueError(f"cursor must be an integer between 0 and {len(text)}")
    return render(text[cursor:], len(text))


def resolve_path(root: Any, path: str) -> Any:
    """Resolve a dotted path of mapping keys and list indexes.

    Mapping keys that themselves contain dots are matched greedily, so every
    path a descriptor reports resolves back to the same value.
    """

    if not isinstance(path, str) or not path.strip():
        raise ValueError("detail_path must be a non-empty dotted path")
    current = _plain(root)
    parts = path.split(".")
    index = 0
    while index < len(parts):
        if isinstance(current, Mapping):
            for end in range(len(parts), index, -1):
                key = ".".join(parts[index:end])
                if key in current:
                    current = current[key]
                    index = end
                    break
            else:
                raise ValueError(f"detail_path '{path}' does not exist")
        elif isinstance(current, list):
            part = parts[index]
            if not part.isdigit() or int(part) >= len(current):
                raise ValueError(f"detail_path '{path}' has no list item '{part}'")
            current = current[int(part)]
            index += 1
        else:
            raise ValueError(f"detail_path '{path}' cannot descend through '{parts[index]}'")
    return current


def value_descriptor(value: Any, *, detail_path: str) -> dict[str, Any]:
    """Describe a value withheld from a page and name its exact detail path."""

    text = value if isinstance(value, str) else canonical_json(value)
    descriptor: dict[str, Any] = {
        "withheld": True,
        "detail_path": detail_path,
        "type": type(_plain(value)).__name__,
        "total_chars": len(text),
        "sha256": content_sha256(value),
    }
    if isinstance(value, (list, dict)):
        descriptor["item_count"] = len(value)
    return descriptor


def detail_chunk(
    root: Any,
    *,
    path: str,
    cursor: Any,
    expected_sha256: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one exact chunk of the value at ``path``.

    Strings are chunked as text; anything else as canonical JSON. Chunks are
    contiguous and lossless: concatenating every chunk up to ``complete`` gives
    back exactly the value whose ``sha256`` is reported. When the caller passes
    the hash from an earlier chunk and the value has since changed, a
    ``stale_result_cursor`` error is returned instead of mixing versions.
    """

    value = resolve_path(root, path)
    is_text = isinstance(value, str)
    text = value if is_text else canonical_json(value)
    digest = content_sha256(value)
    if expected_sha256 and expected_sha256 != digest:
        return {
            "status": "invalid_request",
            "error_code": STALE_RESULT_CURSOR,
            "detail_path": path,
            "sha256": digest,
            "message": (
                "The value changed since the earlier chunk was read. Restart this "
                "detail read at cursor 0 without the old sha256."
            ),
        }
    start = 0 if cursor is None else cursor
    base = dict(extra or {})

    def render(content: str, end: int) -> dict[str, Any]:
        complete = end >= len(text)
        return {
            **base,
            "status": "ok",
            "detail": {
                "detail_path": path,
                "encoding": "text" if is_text else "canonical_json",
                "content": content,
                "sha256": digest,
                "total_chars": len(text),
                "returned_range": {"start": start, "end": end},
                "next_cursor": None if complete else end,
                "complete": complete,
            },
        }

    return fit_text_window(text, cursor=start, render=render)


# Small top-level fields that identify a result's outcome. They are repeated
# on every page so each page can be interpreted on its own.
RESULT_IDENTITY_KEYS = (
    "status",
    "status_code",
    "lookup_status",
    "failure_classification",
    "count",
    "error_code",
)


def _largest_list_key(mapping: Mapping[str, Any]) -> str | None:
    lists = [
        (serialized_size(value), key)
        for key, value in mapping.items()
        if isinstance(value, list) and value
    ]
    return max(lists)[1] if lists else None


def _primary_collection(
    result: Mapping[str, Any],
    body_key: str,
) -> tuple[tuple[str, ...], str] | None:
    """Locate the collection a bounded view pages over.

    ``body_key`` (conventionally ``data``) names the result body: a list is
    paged by item, a mapping by its largest list value, and a wide mapping
    without lists by field. Otherwise the largest top-level list (``rows``,
    ``annotations``, ``candidate_references`` ...) is paged.
    """

    data = result.get(body_key)
    if isinstance(data, list):
        return (body_key,), "items"
    if isinstance(data, Mapping):
        key = _largest_list_key(data)
        if key is not None:
            return (body_key, key), "items"
        if data:
            return (body_key,), "fields"
    key = _largest_list_key({k: v for k, v in result.items() if k != body_key})
    if key is not None:
        return (key,), "items"
    return None


def _json_pointer(keys: Sequence[str], index: int) -> str:
    return "/" + "/".join([*keys, str(index)])


def bounded_json_result(
    result: Mapping[str, Any],
    *,
    offset: Any = None,
    expected_sha256: str | None = None,
    detail_path: str | None = None,
    detail_cursor: Any = None,
    continuation_args: Mapping[str, Any] | None = None,
    stateless: bool = True,
    page_extras: Callable[[tuple[str, ...] | None, str, int, int], Mapping[str, Any]] | None = None,
    body_key: str = "data",
    identity_keys: Sequence[str] = RESULT_IDENTITY_KEYS,
) -> dict[str, Any] | None:
    """Return complete selected data, with explicit page/detail views when requested.

    Returns ``None`` when no view is requested, so the full result reaches the
    shared model boundary. Explicit pages preserve complete items and expose
    exact detail paths for fields not repeated on subsequent pages.

    ``stateless`` results are recomputed on every call, so pages carry the
    full-result hash and a mismatched ``expected_sha256`` is reported as stale.
    ``continuation_args`` are merged into every suggested next call (for
    example a stored lookup reference). ``page_extras(primary_keys, page_mode,
    start, n)`` adds companion fields for exactly the rows on a page
    (``page_mode`` is ``items``, ``fields`` or ``none``). ``identity_keys``
    are small fields repeated on every page.
    """

    full = dict(_plain(result))
    digest = content_sha256(full)
    carry = dict(continuation_args or {})
    if stateless:
        carry["result_sha256"] = digest
    if stateless and expected_sha256 and expected_sha256 != digest:
        return {
            "status": "invalid_request",
            "error_code": STALE_RESULT_CURSOR,
            "result_sha256": digest,
            "message": (
                "This result changed since the earlier page was read. Repeat the "
                "call without result_offset, result_sha256 or detail arguments to "
                "start again from the first page."
            ),
        }
    if detail_path:
        identity = {key: full[key] for key in identity_keys if key in full}
        return detail_chunk(
            full,
            path=detail_path,
            cursor=detail_cursor,
            extra={**identity, "result_sha256": digest} if stateless else identity,
        )
    if detail_cursor is not None:
        raise ValueError("detail_cursor requires detail_path")
    if offset is None and not expected_sha256:
        return None

    primary = _primary_collection(full, body_key)
    keys, mode = primary if primary else ((), "none")
    if keys:
        container = resolve_path(full, ".".join(keys))
        items = list(container) if mode == "items" else sorted(container.items(), key=lambda kv: str(kv[0]))
    else:
        items = []
    start = parse_offset(offset, total=len(items), name="result_offset")
    first_page = start == 0

    # Fields outside the selected collection are shown at offset zero and named
    # for exact detail reads when the caller explicitly starts later.
    outer: list[tuple[str, Any]] = [
        (key, value)
        for key, value in full.items()
        if key not in identity_keys and (not keys or key != keys[0])
    ]
    if len(keys) == 2:
        outer.extend(
            (f"{keys[0]}.{key}", value)
            for key, value in full[keys[0]].items()
            if key != keys[1]
        )
    shown = dict(outer) if first_page else {}
    omitted = [] if first_page else [path for path, _ in outer]

    def render(page: list[Any], returned: int) -> dict[str, Any]:
        body: dict[str, Any] = {key: full[key] for key in identity_keys if key in full}
        nested: dict[str, Any] = {}
        for path, value in shown.items():
            if "." in path and keys and path.startswith(f"{keys[0]}."):
                nested[path[len(keys[0]) + 1:]] = value
            else:
                body[path] = value
        if keys:
            page_value = page if mode == "items" else dict(page)
            if len(keys) == 2:
                body[keys[0]] = {**nested, keys[1]: page_value}
            else:
                body[keys[0]] = page_value
        if page_extras is not None:
            body.update(page_extras(keys or None, mode, start, returned))
        body["result_page"] = {
            "bounded": True,
            "primary_path": ".".join(keys) or None,
            "page_mode": mode,
            "total_items": len(items),
            "offset": start,
            "returned_items": returned,
            "next_offset": None,
            "complete": True,
            "omitted_fields": omitted,
            "next_call": None,
            "detail_call": {**carry, "detail_path": "<detail_path>", "detail_cursor": 0},
            "instructions": (
                "This is the complete requested result suffix. "
                "Fields listed in omitted_fields are read exactly with "
                "detail_call using their detail_path; follow each chunk's next_cursor."
            ),
        }
        if stateless:
            body["result_page"]["result_sha256"] = digest
        return body

    response, _ = fit_page(
        items,
        start=start,
        limit=len(items) - start,
        render=render,
    )
    return response


def json_pointer_for_row(keys: Sequence[str], index: int) -> str:
    """JSON-pointer prefix of one paged row, as recorded by lookup captures."""

    return _json_pointer(keys, index)


__all__ = [
    "INVALID_RESULT_CURSOR",
    "RESULT_IDENTITY_KEYS",
    "STALE_RESULT_CURSOR",
    "bounded_json_result",
    "canonical_json",
    "clamp_page_limit",
    "content_sha256",
    "detail_chunk",
    "env_positive_int",
    "fit_page",
    "fit_text_window",
    "invalid_cursor",
    "json_pointer_for_row",
    "parse_offset",
    "requested_text_length",
    "resolve_path",
    "serialized_size",
    "value_descriptor",
]
