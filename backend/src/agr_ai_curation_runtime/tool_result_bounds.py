"""Size-bounded model-facing tool results.

Row limits alone do not bound what a model receives: one wide record, a long
quote, or a page of long passages can each exceed any sensible context share.
These helpers measure the serialized result the model will actually see and
build pages that fit a configured byte budget, with explicit continuation and
exact, hash-addressed chunk reads for values too large to show whole.

Nothing here drops content silently. A page ends where the budget ends and says
where to continue; an oversized single value is replaced by a descriptor that
names its exact detail path; and when even the compact form cannot fit, callers
get a compact ``tool_result_budget_unmet`` failure that the backend adapter
reports as an unexpected contract escape.

This module is pure (standard library only) so isolated package tools and the
backend share one implementation. Package code reads the same environment
variables the backend config getters document.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

TOOL_RESULT_BUDGET_UNMET = "tool_result_budget_unmet"
INVALID_RESULT_CURSOR = "invalid_result_cursor"
STALE_RESULT_CURSOR = "stale_result_cursor"

TOOL_RESULT_MAX_BYTES_ENV = "TOOL_RESULT_MAX_BYTES"
TOOL_RESULT_MAX_BYTES_DEFAULT = 32768
# The compact failure and descriptors must always fit; below this the contract
# cannot be expressed at all, so configuration is clamped up to it.
TOOL_RESULT_MIN_BYTES = 2048


class ToolResultBudgetError(RuntimeError):
    """The compact form of a result still does not fit its byte budget."""

    def __init__(self, *, measured: int, limit: int, field: str | None = None) -> None:
        super().__init__(
            f"Tool result needs {measured} bytes but the configured budget is {limit} bytes"
        )
        self.measured = measured
        self.limit = limit
        self.field = field


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


def tool_result_max_bytes() -> int:
    """Total serialized byte budget for one bounded model-facing tool result."""

    return env_positive_int(
        TOOL_RESULT_MAX_BYTES_ENV,
        TOOL_RESULT_MAX_BYTES_DEFAULT,
        minimum=TOOL_RESULT_MIN_BYTES,
    )


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
    every representation stay inside the budget.
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


def budget_failure(
    *,
    tool_name: str,
    measured: int,
    limit: int,
    field: str | None = None,
) -> dict[str, Any]:
    """Compact failure returned when a result cannot meet its contract.

    Backend tool adapters recognize ``error_code`` and report the escape once.
    """

    return {
        "status": "error",
        "error_code": TOOL_RESULT_BUDGET_UNMET,
        "tool_name": tool_name,
        "result_bounds": {
            "measured_bytes": measured,
            "limit_bytes": limit,
            "setting": TOOL_RESULT_MAX_BYTES_ENV,
            "field": field,
        },
        "message": (
            "The result could not be reduced to fit the tool result budget. "
            "Narrow the request with filters or a smaller limit."
        ),
    }


def invalid_cursor(message: str, **details: Any) -> dict[str, Any]:
    """Explicit caller error for a malformed or out-of-range cursor."""

    return {
        "status": "invalid_request",
        "error_code": INVALID_RESULT_CURSOR,
        "message": message,
        **details,
    }


def is_budget_failure(result: Any) -> bool:
    """Return whether a tool result is the compact budget failure."""

    payload = _plain(result)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return False
    return isinstance(payload, Mapping) and payload.get("error_code") == TOOL_RESULT_BUDGET_UNMET


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
    items: Sequence[Any],
    *,
    start: int,
    limit: int,
    render: Callable[[list[Any], int], Any],
    budget: int,
    oversized: Callable[[Any, int], Any] | None = None,
) -> tuple[Any, int]:
    """Render the largest page of ``items[start:start+limit]`` within ``budget``.

    ``render(page_items, returned_count)`` must build the complete response,
    including continuation metadata derived from ``returned_count``, so the
    whole serialized response is measured, not only the rows. Items are taken
    in order; the page ends before the first item that would overflow.

    A first item that cannot fit alone is replaced by ``oversized(item, index)``
    (an exact-detail descriptor). If there is no replacement, or even the
    replacement or the empty page overflows, ``ToolResultBudgetError`` is
    raised so the tool can return a compact failure.
    """

    stop = min(len(items), start + max(0, limit))
    page: list[Any] = []
    response = render(page, 0)
    measured = serialized_size(response)
    if measured > budget:
        raise ToolResultBudgetError(measured=measured, limit=budget)
    for index in range(start, stop):
        candidate_page = [*page, items[index]]
        candidate = render(candidate_page, len(candidate_page))
        measured = serialized_size(candidate)
        if measured <= budget:
            page, response = candidate_page, candidate
            continue
        if page:
            break
        if oversized is None:
            raise ToolResultBudgetError(measured=measured, limit=budget)
        candidate_page = [oversized(items[index], index)]
        candidate = render(candidate_page, 1)
        measured = serialized_size(candidate)
        if measured > budget:
            raise ToolResultBudgetError(measured=measured, limit=budget)
        page, response = candidate_page, candidate
        break
    return response, len(page)


def fit_text_window(
    text: str,
    *,
    cursor: int,
    render: Callable[[str, int], Any],
    budget: int,
) -> Any:
    """Return ``render(text[cursor:end], end)`` for the largest end that fits.

    The slice is exact (no ellipses, no rewriting). ``render`` builds the whole
    response including the returned range and next cursor. Raises
    ``ValueError`` for an out-of-range cursor and ``ToolResultBudgetError``
    when not even one character fits.
    """

    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0 or cursor > len(text):
        raise ValueError(f"cursor must be an integer between 0 and {len(text)}")
    low, high = cursor, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if serialized_size(render(text[cursor:middle], middle)) <= budget:
            low = middle
        else:
            high = middle - 1
    response = render(text[cursor:low], low)
    measured = serialized_size(response)
    if measured > budget or (low == cursor and cursor < len(text)):
        raise ToolResultBudgetError(measured=measured, limit=budget)
    return response


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
    budget: int,
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

    return fit_text_window(text, cursor=start, render=render, budget=budget)


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
    budget: int,
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
    """Serve a structured result as budget-bounded pages and exact detail chunks.

    Returns ``None`` when no view was requested and the complete result already
    fits, so compact results reach the model unchanged. Otherwise returns the
    requested page or detail chunk. Nothing is dropped: withheld fields and
    oversized items are replaced by descriptors naming an exact ``detail_path``.

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
            budget=budget,
            extra={**identity, "result_sha256": digest} if stateless else identity,
        )
    if detail_cursor is not None:
        raise ValueError("detail_cursor requires detail_path")
    if offset is None and not expected_sha256 and serialized_size(full) <= budget:
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

    # Fields outside the paged collection: shown on the first page, deferred
    # largest-first only as far as needed for the page frame to fit, and named
    # (never silently dropped) on continuation pages.
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
    withheld: list[dict[str, Any]] = []
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
        next_offset = start + returned
        more = next_offset < len(items)
        body["result_page"] = {
            "bounded": True,
            "budget_bytes": budget,
            "primary_path": ".".join(keys) or None,
            "page_mode": mode,
            "total_items": len(items),
            "offset": start,
            "returned_items": returned,
            "next_offset": next_offset if more else None,
            "complete": not more,
            "withheld_fields": withheld,
            "omitted_fields": omitted,
            "next_call": {**carry, "result_offset": next_offset} if more else None,
            "detail_call": {**carry, "detail_path": "<detail_path>", "detail_cursor": 0},
            "instructions": (
                "This result is larger than one tool response, so it is shown in pages. "
                "Continue with the arguments in next_call. Items or fields marked "
                "withheld, and fields listed in omitted_fields, are read exactly with "
                "detail_call using their detail_path; follow each chunk's next_cursor."
            ),
        }
        if stateless:
            body["result_page"]["result_sha256"] = digest
        return body

    if first_page:
        by_size = sorted(shown, key=lambda path: serialized_size(shown[path]), reverse=True)
        for path in by_size:
            if serialized_size(render([], 0)) <= budget:
                break
            withheld.append(value_descriptor(shown.pop(path), detail_path=path))

    def oversized(item: Any, index: int) -> Any:
        if mode == "fields":
            key, value = item
            return (key, value_descriptor(value, detail_path=f"{'.'.join(keys)}.{key}"))
        return value_descriptor(item, detail_path=f"{'.'.join(keys)}.{index}")

    response, _ = fit_page(
        items,
        start=start,
        limit=len(items) - start,
        render=render,
        budget=budget,
        oversized=oversized,
    )
    return response


def json_pointer_for_row(keys: Sequence[str], index: int) -> str:
    """JSON-pointer prefix of one paged row, as recorded by lookup captures."""

    return _json_pointer(keys, index)


__all__ = [
    "INVALID_RESULT_CURSOR",
    "RESULT_IDENTITY_KEYS",
    "STALE_RESULT_CURSOR",
    "TOOL_RESULT_BUDGET_UNMET",
    "TOOL_RESULT_MAX_BYTES_DEFAULT",
    "TOOL_RESULT_MAX_BYTES_ENV",
    "TOOL_RESULT_MIN_BYTES",
    "ToolResultBudgetError",
    "bounded_json_result",
    "budget_failure",
    "canonical_json",
    "clamp_page_limit",
    "content_sha256",
    "detail_chunk",
    "env_positive_int",
    "fit_page",
    "fit_text_window",
    "invalid_cursor",
    "is_budget_failure",
    "json_pointer_for_row",
    "parse_offset",
    "resolve_path",
    "serialized_size",
    "tool_result_max_bytes",
    "value_descriptor",
]
