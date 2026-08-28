"""Provider-safe result contracts for package diagnostic tools."""

from __future__ import annotations

import hashlib
import json
import logging
from copy import deepcopy
from typing import Any, Callable, Mapping

import sqlalchemy as sa

from src.lib.openai_agents.config import (
    get_agent_studio_package_diagnostic_chunk_max_chars,
    get_agent_studio_package_diagnostic_page_default_items,
    get_agent_studio_package_diagnostic_page_max_items,
    get_agent_studio_package_diagnostic_result_max_chars,
    get_agent_studio_package_diagnostic_scalar_preview_max_chars,
    get_agent_studio_provider_tool_result_inline_max_chars,
)

logger = logging.getLogger(__name__)

_RESULT_ARGUMENTS = {
    "result_view",
    "result_path",
    "result_cursor",
    "result_limit",
    "detail_path",
    "detail_cursor",
    "detail_max_chars",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        default=str,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_safe(value: Any) -> Any:
    return json.loads(_canonical_json(value))


def _provider_json_chars(value: Any) -> int:
    """Measure the exact JSON representation used for provider continuation."""
    return len(json.dumps(value, default=str))


def _result_max_chars() -> int:
    return min(
        get_agent_studio_package_diagnostic_result_max_chars(),
        get_agent_studio_provider_tool_result_inline_max_chars(),
    )


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _path_parts(path: str) -> list[str]:
    parts = [part for part in path.split(".") if part]
    if not parts:
        raise ValueError("result path must not be empty")
    return parts


def _resolve_path(result: Any, path: str) -> Any:
    current = result
    for part in _path_parts(path):
        if isinstance(current, Mapping):
            if part not in current:
                raise ValueError(f"result path '{path}' does not exist")
            current = current[part]
        elif isinstance(current, list):
            try:
                index = int(part)
            except ValueError as exc:
                raise ValueError(f"result path '{path}' requires a list index at '{part}'") from exc
            if index < 0 or index >= len(current):
                raise ValueError(f"result path '{path}' index {index} is out of range")
            current = current[index]
        else:
            raise ValueError(f"result path '{path}' cannot descend through '{part}'")
    return current


def _detail_descriptor(
    value: Any,
    path: str,
    *,
    include_preview: bool = False,
) -> dict[str, Any]:
    content = value if isinstance(value, str) else _canonical_json(value)
    descriptor: dict[str, Any] = {
        "type": "string" if isinstance(value, str) else type(value).__name__,
        "detail_path": path,
        "total_chars": len(content),
        "sha256": _hash(content),
    }
    if include_preview:
        preview_max = get_agent_studio_package_diagnostic_scalar_preview_max_chars()
        descriptor["preview"] = content[:preview_max]
        descriptor["preview_complete"] = len(content) <= preview_max
    return descriptor


def _summary(result: Mapping[str, Any], *, page_paths: set[str]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    available_page_paths: list[str] = []
    for key, value in result.items():
        path = str(key)
        if isinstance(value, list):
            if path in page_paths:
                available_page_paths.append(path)
            fields[key] = {
                "type": "list",
                "count": len(value),
                "page_path": path if path in page_paths else None,
                "detail_path": path,
                "sha256": _hash(_canonical_json(value)),
            }
        elif isinstance(value, dict):
            fields[key] = {
                "type": "object",
                "key_count": len(value),
                **_detail_descriptor(value, path),
            }
        elif (
            isinstance(value, str)
            and len(value)
            > get_agent_studio_package_diagnostic_scalar_preview_max_chars()
        ):
            descriptor = _detail_descriptor(value, path, include_preview=True)
            if _provider_json_chars(descriptor) > _result_max_chars():
                descriptor = _detail_descriptor(value, path)
            fields[key] = descriptor
        else:
            fields[key] = value

    canonical = _canonical_json(result)
    summary = {
        "status": result.get("status", "ok"),
        "result_view": "summary",
        "result_sha256": _hash(canonical),
        "result_total_chars": len(canonical),
        "fields": fields,
        "available_page_paths": sorted(available_page_paths),
        "continuation": (
            "Use result_view='page' for listed page paths or result_view='detail' "
            "with detail_path for exact content."
            if available_page_paths
            else "Use result_view='detail' with detail_path for exact content."
        ),
    }
    if _provider_json_chars(summary) > _result_max_chars():
        for key, value in list(fields.items()):
            if not isinstance(value, dict) or "preview" not in value:
                continue
            fields[key] = _detail_descriptor(result[key], str(key))
            if _provider_json_chars(summary) <= _result_max_chars():
                break
    return summary


def _page(
    result: Mapping[str, Any],
    *,
    path: str,
    cursor: int,
    limit: int,
    page_paths: set[str],
    path_index_offset: int = 0,
) -> dict[str, Any]:
    if path not in page_paths:
        raise ValueError(f"result_path must be one of: {', '.join(sorted(page_paths))}")
    items = _resolve_path(result, path)
    if not isinstance(items, list):
        raise ValueError(f"configured page path '{path}' did not return a list")
    if cursor < 0 or cursor > len(items):
        raise ValueError(f"result_cursor {cursor} is outside the 0..{len(items)} range")

    output: dict[str, Any] = {
        "status": result.get("status", "ok"),
        "result_view": "page",
        "result_path": path,
        "total_count": len(items),
        "cursor": cursor,
        "items": [],
    }
    result_max = _result_max_chars()
    stop = min(len(items), cursor + limit)
    for index in range(cursor, stop):
        item = items[index]
        candidate = _json_safe(item)
        output["items"].append(candidate)
        if _provider_json_chars(output) > result_max:
            output["items"].pop()
            output["items"].append(
                _detail_descriptor(item, f"{path}.{index + path_index_offset}")
            )
        if _provider_json_chars(output) > result_max:
            output["items"].pop()
            break

    returned = len(output["items"])
    next_cursor = cursor + returned
    output.update(
        returned_count=returned,
        next_cursor=next_cursor if next_cursor < len(items) else None,
        complete=next_cursor >= len(items),
        truncated=next_cursor < len(items),
    )
    while output["items"] and _provider_json_chars(output) > result_max:
        output["items"].pop()
        returned = len(output["items"])
        next_cursor = cursor + returned
        output.update(
            returned_count=returned,
            next_cursor=next_cursor if next_cursor < len(items) else None,
            complete=next_cursor >= len(items),
            truncated=next_cursor < len(items),
        )
    return output


def _detail(
    result: Mapping[str, Any],
    *,
    path: str,
    cursor: int,
    max_chars: int,
    output_path: str | None = None,
) -> dict[str, Any]:
    value = _resolve_path(result, path)
    is_text = isinstance(value, str)
    content = value if is_text else _canonical_json(value)
    if cursor < 0 or cursor > len(content):
        raise ValueError(f"detail_cursor {cursor} is outside the 0..{len(content)} range")
    requested_end = min(len(content), cursor + max_chars)

    def render(end: int) -> dict[str, Any]:
        complete = end >= len(content)
        return {
            "status": result.get("status", "ok"),
            "result_view": "detail",
            "detail_path": output_path or path,
            "encoding": "text" if is_text else "canonical_json",
            "content": content[cursor:end],
            "sha256": _hash(content),
            "total_chars": len(content),
            "returned_range": {"start": cursor, "end": end},
            "next_cursor": None if complete else end,
            "complete": complete,
        }

    low = cursor
    high = requested_end
    while low < high:
        candidate_end = (low + high + 1) // 2
        if _provider_json_chars(render(candidate_end)) <= _result_max_chars():
            low = candidate_end
        else:
            high = candidate_end - 1
    detail = render(low)
    if low == cursor and requested_end > cursor:
        raise ValueError(
            "provider inline limit is too small for diagnostic detail metadata"
        )
    return detail


def _normalized_limit(value: Any) -> int:
    default = get_agent_studio_package_diagnostic_page_default_items()
    maximum = get_agent_studio_package_diagnostic_page_max_items()
    if value is None:
        return min(default, maximum)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("result_limit must be a positive integer")
    return min(value, maximum)


def _normalized_chunk_size(value: Any) -> int:
    maximum = get_agent_studio_package_diagnostic_chunk_max_chars()
    if value is None:
        return maximum
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("detail_max_chars must be a positive integer")
    return min(value, maximum)


def _normalized_cursor(value: Any, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _render_result(
    result: Mapping[str, Any],
    contract: Mapping[str, Any],
    controls: Mapping[str, Any],
) -> dict[str, Any]:
    view = controls.get("result_view", "summary")
    page_paths = set(contract.get("page_paths") or [])
    if view == "summary":
        return _summary(result, page_paths=page_paths)
    if view == "page":
        path = controls.get("result_path")
        if not isinstance(path, str) or not path:
            raise ValueError("result_path is required for result_view='page'")
        return _page(
            result,
            path=path,
            cursor=_normalized_cursor(
                controls.get("result_cursor", 0), name="result_cursor"
            ),
            limit=_normalized_limit(controls.get("result_limit")),
            page_paths=page_paths,
        )
    if view == "detail":
        path = controls.get("detail_path")
        if not isinstance(path, str) or not path:
            raise ValueError("detail_path is required for result_view='detail'")
        return _detail(
            result,
            path=path,
            cursor=_normalized_cursor(
                controls.get("detail_cursor", 0), name="detail_cursor"
            ),
            max_chars=_normalized_chunk_size(controls.get("detail_max_chars")),
        )
    raise ValueError("result_view must be one of: summary, page, detail")


def create_bounded_result_handler(
    base_callable: Callable[..., Any],
    contract: Mapping[str, Any],
) -> Callable[..., dict[str, Any]]:
    """Wrap a package callable in the standard bounded diagnostic protocol."""

    def handler(**kwargs: Any) -> dict[str, Any]:
        controls = {
            key: kwargs.pop(key) for key in list(kwargs) if key in _RESULT_ARGUMENTS
        }
        raw_result = base_callable(**kwargs)
        if hasattr(raw_result, "model_dump"):
            raw_result = raw_result.model_dump()
        elif hasattr(raw_result, "dict"):
            raw_result = raw_result.dict()
        if not isinstance(raw_result, Mapping):
            raise TypeError(
                f"Package diagnostic returned unsupported result type {type(raw_result).__name__}; expected dict or Pydantic model."
            )
        return _render_result(_json_safe(raw_result), contract, controls)

    return handler


def create_sql_query_handler(
    database_url: str,
    contract: Mapping[str, Any],
) -> Callable[..., dict[str, Any]]:
    """Create a SELECT-only diagnostic that counts/pages without full materialization."""
    engine = sa.create_engine(database_url)

    def _clean_query(query: Any) -> str:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query string must not be empty")
        cleaned = query.strip()
        if not cleaned.upper().startswith("SELECT"):
            raise ValueError("Only SELECT queries are allowed. This is a read-only tool.")
        return cleaned[:-1].rstrip() if cleaned.endswith(";") else cleaned

    def handler(**kwargs: Any) -> dict[str, Any]:
        controls = {
            key: kwargs.pop(key) for key in list(kwargs) if key in _RESULT_ARGUMENTS
        }
        view = controls.get("result_view", "summary")
        try:
            query = _clean_query(kwargs.get("query"))
            if view == "summary":
                count_query = sa.text(
                    f"SELECT COUNT(*) AS row_count FROM ({query}) AS diagnostic_count"
                )
                with engine.connect() as connection:
                    count = int(connection.execute(count_query).scalar_one())
                return {
                    "status": "ok",
                    "result_view": "summary",
                    "fields": {
                        "status": "ok",
                        "count": count,
                        "rows": {
                            "type": "list",
                            "count": count,
                            "page_path": "rows",
                        },
                    },
                    "available_page_paths": ["rows"],
                    "continuation": "Use result_view='page' with result_path='rows'; oversized cells expose exact detail paths.",
                }

            path = (
                controls.get("result_path")
                if view == "page"
                else controls.get("detail_path")
            )
            if not isinstance(path, str) or not path.startswith("rows"):
                raise ValueError("SQL result_path/detail_path must address rows")
            cursor = _normalized_cursor(
                controls.get("result_cursor", 0), name="result_cursor"
            )
            limit = _normalized_limit(controls.get("result_limit"))
            if view == "detail":
                parts = _path_parts(path)
                if len(parts) < 2 or not parts[1].isdigit():
                    raise ValueError(
                        "SQL detail_path must identify a row, for example "
                        "rows.0.column_name"
                    )
                cursor = int(parts[1])
                limit = 1
            page_query = sa.text(
                f"SELECT * FROM ({query}) AS diagnostic_page "
                "LIMIT :diagnostic_limit OFFSET :diagnostic_offset"
            )
            with engine.connect() as connection:
                rows = [
                    dict(row._mapping)
                    for row in connection.execute(
                        page_query,
                        {
                            "diagnostic_limit": limit + 1,
                            "diagnostic_offset": cursor,
                        },
                    )
                ]
            rows = rows[:limit]
            result = {"status": "ok", "rows": _json_safe(rows)}
            if view == "detail":
                relative_path = ".".join(["rows", "0", *_path_parts(path)[2:]])
                detail = _detail(
                    result,
                    path=relative_path,
                    cursor=_normalized_cursor(
                        controls.get("detail_cursor", 0), name="detail_cursor"
                    ),
                    max_chars=_normalized_chunk_size(controls.get("detail_max_chars")),
                    output_path=path,
                )
                return detail
            page = _page(
                result,
                path="rows",
                cursor=0,
                limit=limit,
                page_paths={"rows"},
                path_index_offset=cursor,
            )
            returned = page["returned_count"]
            count_query = sa.text(
                f"SELECT COUNT(*) AS row_count FROM ({query}) AS diagnostic_count"
            )
            with engine.connect() as connection:
                total_count = int(connection.execute(count_query).scalar_one())
            if cursor > total_count:
                raise ValueError(
                    f"result_cursor {cursor} is outside the 0..{total_count} range"
                )
            page.update(
                total_count=total_count,
                cursor=cursor,
                next_cursor=cursor + returned if cursor + returned < total_count else None,
                complete=cursor + returned >= total_count,
                truncated=cursor + returned < total_count,
            )
            return page
        except ValueError as exc:
            return {
                "status": "error",
                "result_view": view,
                "message": str(exc),
            }
        except (sa.exc.DBAPIError, sa.exc.SQLAlchemyError):
            logger.exception("Agent Studio diagnostic SQL query failed")
            return {
                "status": "error",
                "result_view": view,
                "message": "Database error: query failed. Check syntax and table/column names.",
            }

    return handler


def diagnostic_input_schema(
    input_schema: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Add the standard continuation controls to a package diagnostic schema."""
    schema = deepcopy(dict(input_schema))
    properties = schema.setdefault("properties", {})
    page_paths = list(contract.get("page_paths") or [])
    page_max = get_agent_studio_package_diagnostic_page_max_items()
    chunk_max = get_agent_studio_package_diagnostic_chunk_max_chars()
    result_views = ["summary", "page", "detail"] if page_paths else ["summary", "detail"]
    properties.update(
        {
            "result_view": {
                "type": "string",
                "enum": result_views,
                "default": "summary",
                "description": (
                    "Bounded result view. Start with summary, then request a "
                    "structured page or exact detail chunk."
                ),
            },
            "detail_path": {
                "type": "string",
                "description": "Dot-separated result path for exact hash-addressed content chunks.",
            },
            "detail_cursor": {"type": "integer", "minimum": 0},
            "detail_max_chars": {"type": "integer", "minimum": 1, "maximum": chunk_max},
        }
    )
    if page_paths:
        properties.update(
            {
                "result_path": {
                    "type": "string",
                    "enum": page_paths,
                    "description": (
                        "Structured list path to page when result_view is page."
                    ),
                },
                "result_cursor": {"type": "integer", "minimum": 0},
                "result_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": page_max,
                },
            }
        )
    return schema


def validate_result_contract(contract: Any, *, tool_id: str) -> dict[str, Any]:
    """Validate package-owned diagnostic result metadata eagerly at registration."""
    if not isinstance(contract, dict):
        raise ValueError(
            f"Package diagnostic tool '{tool_id}' must declare agent_studio.diagnostic.result_contract."
        )
    kind = contract.get("kind")
    if kind not in {"structured", "raw", "sql_rows"}:
        raise ValueError(
            f"Package diagnostic tool '{tool_id}' result_contract.kind must be structured, raw, or sql_rows."
        )
    page_paths = contract.get("page_paths", [])
    if not isinstance(page_paths, list) or not all(
        isinstance(path, str) and path for path in page_paths
    ):
        raise ValueError(
            f"Package diagnostic tool '{tool_id}' result_contract.page_paths must be a list of paths."
        )
    return dict(contract)
