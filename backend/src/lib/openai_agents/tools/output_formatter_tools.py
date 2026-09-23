"""Runtime-bound formatter tools for structure-owned CSV/TSV/JSON and chat outputs.

The saved artifact bundle stays application-held. Tools return bounded
inventories, pages and exact value slices; every response obeys the total
``OUTPUT_TOOL_MAX_RESPONSE_CHARS`` budget with explicit continuation. The model
chooses projection operations and application code renders all rows.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, cast, get_args

from agents import function_tool
from pydantic import BaseModel, ValidationError

from src.lib.context import get_current_session_id, get_current_trace_id
from src.lib.flows.chat_output_delivery import (
    record_chat_output_cannot_complete,
    record_chat_output_failure,
)
from src.lib.flows.output_projection import (
    FlowOutputArtifactBundle,
    FlowOutputColumnSpec,
    FlowOutputFilterSpec,
    FlowOutputFormat,
    FlowOutputJsonShape,
    FlowOutputOperationalCeilingError,
    FlowOutputOverrideSpec,
    FlowOutputProjectionPlan,
    FlowOutputProjectionResult,
    FlowOutputRowSource,
    FlowOutputRowStrategy,
    FlowOutputSortSpec,
    FlowOutputSplitListSpec,
    FlowOutputTransformSpec,
    apply_projection_plan,
    bundle_row_for_ref,
    default_columns_for_row_source,
    default_projection_plan,
    finalize_output_projection,
    projection_plan_allows_empty_bundle,
    validate_projection_plan,
)
from src.lib.observability.payload_contracts import (
    PayloadContractViolation,
    report_payload_contract_violation,
)
from src.lib.openai_agents.config import (
    get_flow_chat_max_rows,
    get_flow_output_chat_max_chars,
    get_flow_output_chat_notes_max_chars,
    get_flow_output_projection_preview_limit,
    get_flow_projection_max_field_examples,
    get_flow_projection_max_list_items,
    get_flow_projection_max_object_items,
    get_flow_projection_max_row_chars,
    get_flow_projection_max_text_chars,
    get_flow_projection_max_rows,
    get_formatter_preview_max_depth,
    get_output_tool_catalog_page_size,
    get_output_tool_max_response_chars,
    get_output_tool_value_read_chars,
)


FormatterSaveCallback = Callable[
    [str, FlowOutputProjectionResult, str, str],
    Awaitable[Mapping[str, Any]],
]
ChatDeliveryCallback = Callable[[str, Mapping[str, Any]], Awaitable[Mapping[str, Any]]]

_SUPPORTED_FILE_FORMATS = {"csv", "tsv", "json"}
_CHAT_FORMAT = "chat"
_CURSOR_PLACEHOLDER = "9" * 12
_FORBIDDEN_CONTENT_KEYS = {
    "content",
    "csv",
    "data",
    "data_json",
    "file_content",
    "json",
    "raw",
    "raw_csv",
    "raw_json",
    "raw_rows",
    "raw_tsv",
    "records",
    "rows",
    "tsv",
}
_FIELD_REF_KEY_PATTERN = re.compile(r"[^0-9a-zA-Z_]+")
_OBJECT_ATTRIBUTE_FIELD_PREFIX = "object.attribute."
_MAX_PROJECTION_ROWS = get_flow_projection_max_rows()
_MAX_CHAT_ROWS = get_flow_chat_max_rows()
_MAX_FIELD_EXAMPLES = get_flow_projection_max_field_examples()
_MAX_LIST_ITEMS = get_flow_projection_max_list_items()
_MAX_OBJECT_ITEMS = get_flow_projection_max_object_items()
_MAX_ROW_CHARS = get_flow_projection_max_row_chars()
_MAX_TEXT_CHARS = get_flow_projection_max_text_chars()
_DEFAULT_PREVIEW_LIMIT = get_flow_output_projection_preview_limit()
_MAX_PREVIEW_DEPTH = get_formatter_preview_max_depth()


def _tool_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonable(item) for item in value]
    return value


def _bounded_text(value: Any) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= _MAX_TEXT_CHARS:
        return text
    overflow = len(text) - _MAX_TEXT_CHARS
    return f"{text[:_MAX_TEXT_CHARS].rstrip()}... [truncated {overflow} chars]"


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    value = _jsonable(value)
    if isinstance(value, str):
        return _bounded_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if depth >= _MAX_PREVIEW_DEPTH:
        return "[truncated:depth]"
    if isinstance(value, Mapping):
        items = list(value.items())
        bounded = {
            str(key): _bounded_value(item, depth=depth + 1)
            for key, item in items[:_MAX_OBJECT_ITEMS]
        }
        if len(items) > _MAX_OBJECT_ITEMS:
            bounded["_truncated_keys"] = len(items) - _MAX_OBJECT_ITEMS
        return bounded
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = list(value)
        bounded_items = [
            _bounded_value(item, depth=depth + 1)
            for item in items[:_MAX_LIST_ITEMS]
        ]
        if len(items) > _MAX_LIST_ITEMS:
            bounded_items.append({"_truncated_items": len(items) - _MAX_LIST_ITEMS})
        return bounded_items
    return _bounded_text(value)


def _value_marker(
    value: Any,
    *,
    row_ref: str | None = None,
    field_ref: str | None = None,
) -> dict[str, Any]:
    """Compact stand-in for a value too large to preview; exact reads stay available."""

    text = value if isinstance(value, str) else json.dumps(
        _jsonable(value), ensure_ascii=False, sort_keys=True, default=str
    )
    marker: dict[str, Any] = {
        "_value_omitted": True,
        "value_type": type(value).__name__,
        "total_chars": len(text),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    if row_ref and field_ref:
        marker["read_with"] = {
            "tool": "read_output_value",
            "row_ref": row_ref,
            "field_ref": field_ref,
        }
    else:
        marker["read_with"] = (
            "Use inspect_output_rows to get row_refs, then read_output_value "
            "for the exact value."
        )
    return marker


def _bounded_field_value(
    value: Any,
    *,
    row_ref: str | None = None,
    field_ref: str | None = None,
) -> Any:
    """Bounded preview of one value, or a marker when even the preview is too large."""

    bounded = _bounded_value(value)
    if len(json.dumps(bounded, ensure_ascii=False, default=str)) <= _MAX_ROW_CHARS:
        return bounded
    return _value_marker(value, row_ref=row_ref, field_ref=field_ref)


def _bounded_row(
    row: Mapping[str, Any],
    *,
    row_ref: str | None = None,
    field_refs: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    """Row preview within the row limit; oversized fields become exact-read markers."""

    bounded: dict[str, Any] = {}
    keys = [str(key) for key in row]
    for index, (key, value) in enumerate(row.items()):
        name = str(key)
        field_ref = (field_refs or {}).get(name, name)
        preview = _bounded_field_value(value, row_ref=row_ref, field_ref=field_ref)
        candidate = {**bounded, name: preview}
        if len(json.dumps(candidate, ensure_ascii=False, default=str)) > _MAX_ROW_CHARS:
            preview = _value_marker(value, row_ref=row_ref, field_ref=field_ref)
            candidate = {**bounded, name: preview}
        if len(json.dumps(candidate, ensure_ascii=False, default=str)) > _MAX_ROW_CHARS:
            bounded["_truncated_preview"] = True
            bounded["_truncated_after_field"] = name
            bounded["_omitted_field_count"] = len(keys) - index
            bounded["_omitted_fields_first"] = keys[index:index + _MAX_LIST_ITEMS]
            break
        bounded[name] = preview
    return bounded


def _report_context(bundle: FlowOutputArtifactBundle) -> dict[str, Any]:
    """Trace correlation for payload-contract reports, without any row data."""

    return {
        "trace_id": get_current_trace_id(),
        "session_id": get_current_session_id(),
        "correlation": {
            "flow_run_id": bundle.flow_run_id,
            "document_id": bundle.document_id,
        },
    }


def _budgeted_tool_json(
    payload: Mapping[str, Any],
    *,
    tool_name: str,
    formatter_agent_id: str,
    report_context: Mapping[str, Any],
    committed_keys: Sequence[str] = (),
) -> str:
    """Serialize one tool response, never exceeding the configured total budget.

    Paged tools size their pages to the budget, so an escape here is a contract
    failure: it is reported once and replaced by a compact, actionable error.
    A committed result (a saved file or delivered output) is never rewritten as
    an error; it is reduced to its ``committed_keys`` instead.
    """

    encoded = _tool_json(payload)
    limit = get_output_tool_max_response_chars()
    if len(encoded) <= limit:
        return encoded
    violation = PayloadContractViolation(
        category="tool_result_budget_escape",
        component="output_formatter_tools",
        message=f"{tool_name} response exceeded the output tool response budget.",
        measured=len(encoded),
        limit=limit,
        setting="OUTPUT_TOOL_MAX_RESPONSE_CHARS",
    )
    report_payload_contract_violation(
        violation,
        phase="formatter_tool_response",
        tool_name=tool_name,
        agent=formatter_agent_id,
        trace_id=report_context.get("trace_id"),
        session_id=report_context.get("session_id"),
        correlation=report_context.get("correlation"),
    )
    if committed_keys:
        return _tool_json(
            {
                **{key: payload[key] for key in committed_keys if key in payload},
                "receipt_reduced_to_budget": True,
            }
        )
    return _tool_json(
        {
            "status": "invalid",
            "code": "response_over_budget",
            "errors": [
                f"The {tool_name} response ({len(encoded)} chars) exceeds the "
                f"{limit}-char tool response budget. Narrow the request: select "
                "fewer field refs, use a smaller page limit or cursor, or read one "
                "value with read_output_value."
            ],
        }
    )


def _page_to_budget(
    items: Sequence[Any],
    *,
    start: int,
    max_count: int,
    build_payload: Callable[[list[Any], str], Mapping[str, Any]],
    render: Callable[[Any], Any] = lambda item: item,
) -> tuple[list[Any], str]:
    """Take up to ``max_count`` items from ``start`` that fit the response budget.

    ``render`` converts each item only as it is paged. Returns the rendered page
    and the next cursor ("" when the items are exhausted). At least one item is
    returned so continuation always advances; a single oversized item is then
    caught by the final response budget guard.
    """

    limit = get_output_tool_max_response_chars()
    used = len(_tool_json(build_payload([], _CURSOR_PLACEHOLDER)))
    page: list[Any] = []
    for raw_item in items[start : start + max_count]:
        item = render(raw_item)
        item_chars = len(_tool_json({"i": item})) - len('{"i": }') + 2
        if page and used + item_chars > limit:
            break
        page.append(item)
        used += item_chars
    next_offset = start + len(page)
    return page, str(next_offset) if next_offset < len(items) else ""


def _example_preview(value: Any) -> str:
    """Short preview of one catalog example; exact values come from read_output_value."""

    text = value if isinstance(value, str) else json.dumps(
        _jsonable(value), ensure_ascii=False, sort_keys=True, default=str
    )
    text = text.replace("\r", " ").replace("\n", " ")
    if len(text) <= _MAX_TEXT_CHARS:
        return text
    return f"{text[:_MAX_TEXT_CHARS]}... [{len(text) - _MAX_TEXT_CHARS} more chars; use read_output_value]"


def _compact_columns(columns: Sequence[FlowOutputColumnSpec]) -> list[dict[str, Any]]:
    return [
        {
            "key": column.key,
            "header": column.header or column.key,
            **({"field_ref": column.field_ref} if column.field_ref else {}),
            **({"transform": column.transform.type} if column.transform is not None else {}),
        }
        for column in columns
    ]


def _positive_limit(value: int | None, *, default: int, ceiling: int) -> int:
    if value is None or value < 1:
        return max(1, min(default, ceiling))
    return max(1, min(value, ceiling))


def _parse_cursor(cursor: str | None) -> int:
    if cursor is None or not str(cursor).strip():
        return 0
    try:
        offset = int(str(cursor).strip())
    except ValueError as exc:
        raise ValueError("cursor must be an integer row offset.") from exc
    if offset < 0:
        raise ValueError("cursor must be a non-negative row offset.")
    return offset


def _parse_json_payload(encoded_json: str | None, *, label: str) -> Any:
    if encoded_json is None or not str(encoded_json).strip():
        return None
    try:
        return json.loads(str(encoded_json))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {exc.msg}") from exc


def _parse_field_refs(field_refs_json: str | None) -> list[str]:
    payload = _parse_json_payload(field_refs_json, label="field_refs_json")
    if payload is None:
        return []
    if isinstance(payload, str):
        refs = [payload]
    elif isinstance(payload, Sequence) and not isinstance(payload, (bytes, bytearray)):
        refs = list(payload)
    else:
        raise ValueError("field_refs_json must decode to a JSON string or array of strings.")
    field_refs = [str(ref).strip() for ref in refs if str(ref).strip()]
    if len(field_refs) != len(refs):
        raise ValueError("field_refs_json cannot contain blank field refs.")
    return field_refs


def _parse_model_list(
    encoded_json: str | None,
    *,
    label: str,
    wrapper_key: str,
    model: type[BaseModel],
) -> list[Any]:
    payload = _parse_json_payload(encoded_json, label=label)
    if payload is None:
        return []
    if isinstance(payload, Mapping) and wrapper_key in payload:
        payload = payload[wrapper_key]
    if not isinstance(payload, list):
        raise ValueError(f"{label} must decode to a JSON array or object with '{wrapper_key}'.")
    try:
        return [model.model_validate(item) for item in payload]
    except ValidationError as exc:
        raise ValueError(f"{label} schema is invalid: {exc}") from exc


def _parse_filters(filters_json: str | None) -> list[FlowOutputFilterSpec]:
    return cast(
        list[FlowOutputFilterSpec],
        _parse_model_list(
            filters_json,
            label="filters_json",
            wrapper_key="filters",
            model=FlowOutputFilterSpec,
        ),
    )


def _parse_sorts(sort_json: str | None) -> list[FlowOutputSortSpec]:
    return cast(
        list[FlowOutputSortSpec],
        _parse_model_list(
            sort_json,
            label="sort_json",
            wrapper_key="sort",
            model=FlowOutputSortSpec,
        ),
    )


def _source_refs_for_transform(transform: FlowOutputTransformSpec) -> list[str]:
    refs: list[str] = []
    if transform.field_ref:
        refs.append(transform.field_ref)
    refs.extend(transform.field_refs)
    if transform.type != "conditional":
        for value in transform.values:
            if isinstance(value, Mapping) and isinstance(value.get("field_ref"), str):
                refs.append(str(value["field_ref"]))
            elif isinstance(value, str) and "." in value:
                refs.append(value)
    if transform.type == "conditional":
        if transform.when_true is not None:
            refs.extend(_source_refs_for_transform(transform.when_true))
        if transform.when_false is not None:
            refs.extend(_source_refs_for_transform(transform.when_false))
    return refs


def _source_refs_for_column(column: FlowOutputColumnSpec) -> list[str]:
    if column.transform is None:
        return [column.field_ref] if column.field_ref else []
    return _source_refs_for_transform(column.transform)


def _literal_string_error(value: str, *, context: str) -> str | None:
    text = value.strip()
    if len(value) > _MAX_TEXT_CHARS:
        return f"{context} exceeds the formatter literal text limit."
    if "\n" in value or "\r" in value:
        return f"{context} cannot contain newline-delimited file content."
    if text.startswith(("{", "[")):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, (dict, list)):
            return f"{context} cannot contain encoded JSON objects or arrays."
    return None


def _literal_value_errors(value: Any, *, context: str) -> list[str]:
    if isinstance(value, (dict, list, tuple, set)):
        return [f"{context} must be a scalar value, not structured replacement data."]
    if isinstance(value, str):
        error = _literal_string_error(value, context=context)
        return [error] if error else []
    return []


def _transform_literal_payload_errors(
    transform: FlowOutputTransformSpec,
    *,
    context: str,
) -> list[str]:
    errors: list[str] = []
    if transform.type == "literal":
        errors.extend(_literal_value_errors(transform.value, context=f"{context} literal value"))
    elif transform.type == "conditional":
        errors.extend(
            _literal_value_errors(transform.value, context=f"{context} condition value")
        )
    for index, value in enumerate(transform.values, start=1):
        if transform.type == "conditional":
            errors.extend(_literal_value_errors(value, context=f"{context} values[{index}]"))
            continue
        if isinstance(value, Mapping) and isinstance(value.get("field_ref"), str):
            extra_keys = [key for key in value if key != "field_ref"]
            if extra_keys:
                errors.append(
                    f"{context} values[{index}] field_ref object has unsupported key(s): "
                    + ", ".join(sorted(str(key) for key in extra_keys))
                )
            continue
        errors.extend(_literal_value_errors(value, context=f"{context} values[{index}]"))
    errors.extend(_literal_value_errors(transform.separator, context=f"{context} separator"))
    errors.extend(
        _literal_value_errors(transform.pair_separator, context=f"{context} pair_separator")
    )
    for mapping_key, mapping_value in transform.mapping.items():
        errors.extend(
            _literal_value_errors(
                mapping_value,
                context=f"{context} mapping value for {mapping_key!r}",
            )
        )
    if transform.default is not None:
        errors.extend(_literal_value_errors(transform.default, context=f"{context} default"))
    for label_name, label_value in (
        ("true_label", transform.true_label),
        ("false_label", transform.false_label),
        ("unknown_label", transform.unknown_label),
    ):
        errors.extend(_literal_value_errors(label_value, context=f"{context} {label_name}"))
    if transform.type == "conditional":
        for branch_name, branch in (
            ("when_true", transform.when_true),
            ("when_false", transform.when_false),
        ):
            if branch is not None:
                errors.extend(
                    _transform_literal_payload_errors(
                        branch,
                        context=f"{context} {branch_name}",
                    )
                )
    return errors


def _formatter_plan_constraint_errors(
    plan: FlowOutputProjectionPlan,
    columns: Sequence[FlowOutputColumnSpec],
) -> list[str]:
    errors: list[str] = []
    errors.extend(_literal_value_errors(plan.missing_value, context="Projection missing_value"))
    source_ref_count = 0
    for column in columns:
        errors.extend(_literal_value_errors(column.key, context=f"Column '{column.key}' key"))
        if column.header is not None:
            errors.extend(_literal_value_errors(column.header, context=f"Column '{column.key}' header"))
        if column.split_list is not None:
            if column.split_list.header_template is not None:
                errors.extend(_literal_value_errors(
                    column.split_list.header_template,
                    context=f"Column '{column.key}' split_list header_template",
                ))
            for header in column.split_list.headers:
                errors.extend(_literal_value_errors(
                    header, context=f"Column '{column.key}' split_list header",
                ))
        source_ref_count += len(_source_refs_for_column(column))
        if column.transform is not None:
            errors.extend(
                _transform_literal_payload_errors(
                    column.transform,
                    context=f"Column '{column.key}' transform",
                )
            )
    for index, override in enumerate(plan.overrides, start=1):
        errors.extend(_literal_value_errors(override.value, context=f"Override {index} value"))
    if columns and source_ref_count == 0:
        errors.append(
            "Formatter projections must include at least one source-backed field column "
            "or source-backed transform; literal-only files are not allowed."
        )
    return errors


def _reject_extra_keys(
    payload: Mapping[str, Any],
    *,
    model: type[BaseModel],
    context: str,
) -> list[str]:
    allowed = set(model.model_fields)
    return [
        f"{context} contains unsupported key '{key}'."
        for key in payload
        if key not in allowed
    ]


def _raw_transform_extra_key_errors(
    raw_transform: Mapping[str, Any],
    *,
    context: str,
) -> list[str]:
    errors = _reject_extra_keys(
        raw_transform,
        model=FlowOutputTransformSpec,
        context=context,
    )
    for branch_name in ("when_true", "when_false"):
        branch = raw_transform.get(branch_name)
        if isinstance(branch, Mapping):
            errors.extend(
                _raw_transform_extra_key_errors(
                    branch,
                    context=f"{context} {branch_name}",
                )
            )
    return errors


def _projection_plan_extra_key_errors(raw_plan: Mapping[str, Any]) -> list[str]:
    errors = _reject_extra_keys(
        raw_plan,
        model=FlowOutputProjectionPlan,
        context="Projection plan",
    )
    for key in raw_plan:
        if key in _FORBIDDEN_CONTENT_KEYS:
            errors.append(
                f"Projection plan cannot include model-authored content key '{key}'. "
                "Use field refs, filters, sorts, and transforms over the saved bundle."
            )
    for index, raw_column in enumerate(raw_plan.get("columns") or [], start=1):
        if isinstance(raw_column, Mapping):
            errors.extend(
                _reject_extra_keys(
                    raw_column,
                    model=FlowOutputColumnSpec,
                    context=f"Column {index}",
                )
            )
            raw_split = raw_column.get("split_list")
            if isinstance(raw_split, Mapping):
                errors.extend(
                    _reject_extra_keys(
                        raw_split,
                        model=FlowOutputSplitListSpec,
                        context=f"Column {index} split_list",
                    )
                )
            raw_transform = raw_column.get("transform")
            if isinstance(raw_transform, Mapping):
                errors.extend(
                    _raw_transform_extra_key_errors(
                        raw_transform,
                        context=f"Column {index} transform",
                    )
                )
    for index, raw_filter in enumerate(raw_plan.get("filters") or [], start=1):
        if isinstance(raw_filter, Mapping):
            errors.extend(
                _reject_extra_keys(
                    raw_filter,
                    model=FlowOutputFilterSpec,
                    context=f"Filter {index}",
                )
            )
    for index, raw_sort in enumerate(raw_plan.get("sort") or [], start=1):
        if isinstance(raw_sort, Mapping):
            errors.extend(
                _reject_extra_keys(
                    raw_sort,
                    model=FlowOutputSortSpec,
                    context=f"Sort {index}",
                )
            )
    for index, raw_override in enumerate(raw_plan.get("overrides") or [], start=1):
        if isinstance(raw_override, Mapping):
            errors.extend(
                _reject_extra_keys(
                    raw_override,
                    model=FlowOutputOverrideSpec,
                    context=f"Override {index}",
                )
            )
    return errors


def _projection_plan_from_tool_payload(
    plan_json: str | Mapping[str, Any] | None,
    *,
    output_format: FlowOutputFormat,
) -> FlowOutputProjectionPlan:
    if plan_json is None or (isinstance(plan_json, str) and not plan_json.strip()):
        raise ValueError("Projection plan JSON is empty.")
    raw_plan: Any
    if isinstance(plan_json, str):
        try:
            raw_plan = json.loads(plan_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Projection plan is not valid JSON: {exc.msg}") from exc
    elif isinstance(plan_json, Mapping):
        raw_plan = dict(plan_json)
    else:
        raise ValueError("Projection plan must be a JSON object or encoded JSON object.")

    if isinstance(raw_plan, Mapping) and isinstance(raw_plan.get("plan"), Mapping):
        wrapper_extras = [
            key
            for key in raw_plan
            if key not in {"plan"}
        ]
        if wrapper_extras:
            keys = ", ".join(sorted(str(key) for key in wrapper_extras))
            raise ValueError(f"Projection plan wrapper contains unsupported key(s): {keys}.")
        raw_plan = raw_plan["plan"]
    if not isinstance(raw_plan, Mapping):
        raise ValueError("Projection plan must decode to a JSON object.")

    extra_errors = _projection_plan_extra_key_errors(raw_plan)
    if extra_errors:
        raise ValueError("; ".join(extra_errors))
    try:
        plan = FlowOutputProjectionPlan.model_validate(raw_plan)
    except ValidationError as exc:
        raise ValueError(f"Projection plan schema is invalid: {exc}") from exc
    return plan.model_copy(update={"format": output_format})


def _projection_summary(result: FlowOutputProjectionResult) -> dict[str, Any]:
    """Compact receipt facts about a finalized projection; never its rows."""

    headers = [column.header or column.key for column in result.columns]
    column_limit = get_output_tool_catalog_page_size()
    return {
        "format": result.format,
        "row_source": result.row_source,
        "columns": headers[:column_limit],
        **(
            {"columns_omitted": len(headers) - column_limit}
            if len(headers) > column_limit
            else {}
        ),
        "column_count": len(headers),
        "total_count": result.total_count,
        "row_count": len(result.rows),
        "truncated": result.truncated,
        "limited_by_max_rows": result.limited_by_max_rows,
        "overrides_applied": result.overrides_applied,
        "rows_excluded": result.rows_excluded,
        "group_by": list(result.group_by),
        "warning_count": len(result.warnings),
        "warnings": [
            _bounded_text(warning)
            for warning in result.warnings[:_MAX_LIST_ITEMS]
        ],
    }


def _field_label(bundle: FlowOutputArtifactBundle, field_ref: str) -> str:
    for field in bundle.field_catalog:
        if field.ref == field_ref:
            return field.label
    tail = field_ref.rsplit(".", 1)[-1]
    return tail.replace("_", " ").strip().title() or field_ref


def _column_key_from_field_ref(field_ref: str) -> str:
    key = _FIELD_REF_KEY_PATTERN.sub("_", field_ref.strip()).strip("_").lower()
    if not key:
        return "field"
    if key[0].isdigit():
        return f"field_{key}"
    return key


def _columns_for_field_refs(
    bundle: FlowOutputArtifactBundle,
    row_source: FlowOutputRowSource,
    field_refs: Sequence[str],
) -> list[FlowOutputColumnSpec]:
    available = bundle.field_refs_for_source(row_source)
    unknown = [field_ref for field_ref in field_refs if field_ref not in available]
    if unknown:
        raise ValueError("Unknown field ref(s): " + ", ".join(sorted(unknown)))
    return [
        FlowOutputColumnSpec(
            key=_column_key_from_field_ref(field_ref),
            header=_field_label(bundle, field_ref),
            field_ref=field_ref,
        )
        for field_ref in field_refs
    ]


def _all_columns_for_row_source(
    bundle: FlowOutputArtifactBundle,
    row_source: FlowOutputRowSource,
) -> list[FlowOutputColumnSpec]:
    field_refs = [
        field.ref
        for field in bundle.field_catalog
        if field.row_source == row_source
    ]
    return _columns_for_field_refs(bundle, row_source, field_refs)


def _coerce_row_source(row_source: str | None, default: FlowOutputRowSource) -> FlowOutputRowSource:
    raw = str(row_source or "").strip()
    if not raw:
        return default
    if raw not in get_args(FlowOutputRowSource):
        allowed = ", ".join(get_args(FlowOutputRowSource))
        raise ValueError(f"row_source must be one of: {allowed}.")
    return cast(FlowOutputRowSource, raw)


def _coerce_row_strategy(row_strategy: str | None) -> FlowOutputRowStrategy | None:
    raw = str(row_strategy or "").strip()
    if not raw:
        return None
    if raw not in get_args(FlowOutputRowStrategy):
        allowed = ", ".join(get_args(FlowOutputRowStrategy))
        raise ValueError(f"row_strategy must be one of: {allowed}.")
    return cast(FlowOutputRowStrategy, raw)


def _available_source_refs(bundle: FlowOutputArtifactBundle) -> dict[str, list[str]]:
    rows = bundle.rows_for_source("object")
    source_ids = sorted(
        {
            str(row.get("artifact.extraction_result_id") or "").strip()
            for row in rows
            if str(row.get("artifact.extraction_result_id") or "").strip()
        }
    )
    source_keys = sorted(
        {
            str(row.get("artifact.source_key") or "").strip()
            for row in rows
            if str(row.get("artifact.source_key") or "").strip()
        }
    )
    return {
        "source_extraction_result_ids": source_ids,
        "source_keys": source_keys,
    }


def _first_seen(values: Sequence[Any]) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            items.append(text)
    return items


def _source_ref_for_row(row: Mapping[str, Any]) -> str:
    extraction_result_id = str(row.get("artifact.extraction_result_id") or "").strip()
    if extraction_result_id:
        return extraction_result_id
    source_key = str(row.get("artifact.source_key") or "").strip()
    if source_key:
        return source_key
    return "object_rows"


def _is_generic_object_row(row: Mapping[str, Any]) -> bool:
    adapter_key = str(row.get("artifact.adapter_key") or "").strip().lower()
    domain_pack_id = str(row.get("envelope.domain_pack_id") or "").strip().lower()
    object_type = str(row.get("object.object_type") or "").strip().lower()
    class_key = str(row.get("object.payload.class_key") or "").strip().lower()
    return (
        adapter_key == "generic"
        or domain_pack_id == "generic"
        or object_type.startswith("generic_")
        or class_key.startswith("generic:")
    )


def _attribute_keys_for_row(row: Mapping[str, Any]) -> set[str]:
    return {
        str(field_ref).removeprefix(_OBJECT_ATTRIBUTE_FIELD_PREFIX)
        for field_ref, value in row.items()
        if str(field_ref).startswith(_OBJECT_ATTRIBUTE_FIELD_PREFIX)
        and value not in (None, "", [])
    }


def _attribute_inventory_for_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    row_key_sets = [_attribute_keys_for_row(row) for row in rows]
    all_attribute_keys = _first_seen(
        [
            field_ref.removeprefix(_OBJECT_ATTRIBUTE_FIELD_PREFIX)
            for row in rows
            for field_ref in row
            if str(field_ref).startswith(_OBJECT_ATTRIBUTE_FIELD_PREFIX)
        ]
    )
    shared_attribute_keys = [
        key
        for key in all_attribute_keys
        if row_key_sets and all(key in key_set for key_set in row_key_sets)
    ]
    keys_missing_from_some_objects = [
        key
        for key in all_attribute_keys
        if row_key_sets and any(key not in key_set for key_set in row_key_sets)
    ]
    return {
        "all_attribute_keys": all_attribute_keys,
        "shared_attribute_keys": shared_attribute_keys,
        "keys_missing_from_some_objects": keys_missing_from_some_objects,
    }


def _semantic_class_attribute_groups(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    semantic_classes = _first_seen(
        [row.get("object.payload.semantic_class") or "" for row in rows]
    )
    groups: list[dict[str, Any]] = []
    for semantic_class in semantic_classes or [""]:
        group_rows = [
            row
            for row in rows
            if str(row.get("object.payload.semantic_class") or "").strip()
            == semantic_class
        ]
        inventory = _attribute_inventory_for_rows(group_rows)
        groups.append(
            {
                "semantic_class": semantic_class,
                "row_count": len(group_rows),
                **inventory,
            }
        )
    return groups


def _generic_source_summary(bundle: FlowOutputArtifactBundle) -> dict[str, Any]:
    rows_by_source: dict[str, list[Mapping[str, Any]]] = {}
    for row in bundle.rows_for_source("object"):
        if not _is_generic_object_row(row):
            continue
        rows_by_source.setdefault(_source_ref_for_row(row), []).append(row)

    sources: list[dict[str, Any]] = []
    for source_ref, rows in sorted(rows_by_source.items()):
        inventory = _attribute_inventory_for_rows(rows)
        all_attribute_keys = inventory["all_attribute_keys"]
        semantic_groups = _semantic_class_attribute_groups(rows)
        notices: list[dict[str, Any]] = []
        for group in semantic_groups:
            keys_missing_from_some_objects = group["keys_missing_from_some_objects"]
            if not keys_missing_from_some_objects:
                continue
            notices.append(
                {
                    "code": "generic_attribute_key_drift",
                    "severity": "info",
                    "semantic_class": group["semantic_class"],
                    "message": (
                        "Generic object attribute keys differ across some rows. "
                        "This may be intentional for mixed object shapes; inspect "
                        "the attributes before choosing export columns."
                    ),
                    "keys_missing_from_some_objects": keys_missing_from_some_objects,
                }
            )
        if not all_attribute_keys and any(
            str(row.get("object.payload.claim_text") or "").strip()
            for row in rows
        ):
            notices.append(
                {
                    "code": "generic_claim_text_only_unstructured",
                    "severity": "info",
                    "message": (
                        "Generic rows contain claim_text but no exportable generic "
                        "attributes. Do not split claim_text into columns."
                    ),
                }
            )
        sources.append(
            {
                "source_ref": source_ref,
                "row_count": len(rows),
                "adapter_keys": _first_seen(
                    [row.get("artifact.adapter_key") for row in rows]
                ),
                "domain_pack_ids": _first_seen(
                    [row.get("envelope.domain_pack_id") for row in rows]
                ),
                "object_types": _first_seen(
                    [row.get("object.object_type") for row in rows]
                ),
                "semantic_classes": _first_seen(
                    [row.get("object.payload.semantic_class") for row in rows]
                ),
                **inventory,
                "semantic_class_attribute_groups": semantic_groups,
                "notices": notices,
            }
        )
    return {
        "generic_source_count": len(sources),
        "sources": sources,
    }


def _rows_for_source_ref(
    bundle: FlowOutputArtifactBundle,
    *,
    row_source: FlowOutputRowSource,
    source_ref: str | None,
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    normalized = str(source_ref or "").strip()
    rows = list(bundle.rows_for_source(row_source))
    if not normalized:
        return rows, {}
    source_refs = _available_source_refs(bundle)
    source_id = normalized.removeprefix("extraction-result:")
    if source_id in source_refs["source_extraction_result_ids"]:
        return [
            row
            for row in rows
            if str(row.get("artifact.extraction_result_id") or "").strip() == source_id
        ], {"source_extraction_result_ids": [source_id]}
    if normalized in source_refs["source_keys"]:
        return [
            row
            for row in rows
            if str(row.get("artifact.source_key") or "").strip() == normalized
        ], {"source_keys": [normalized]}
    available = [
        *source_refs["source_extraction_result_ids"],
        *source_refs["source_keys"],
    ]
    available_text = ", ".join(available) if available else "none"
    raise ValueError(
        f"source_ref '{normalized}' is not available for object rows. "
        f"Available source refs: {available_text}."
    )


def _source_identities_for_formatter_rows(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    identities: set[str] = set()
    for row in rows:
        extraction_result_id = str(row.get("artifact.extraction_result_id") or "").strip()
        if extraction_result_id:
            identities.add(f"extraction-result:{extraction_result_id}")
            continue
        source_key = str(row.get("artifact.source_key") or "").strip()
        if source_key:
            identities.add(f"source-key:{source_key}")
    return identities


def _default_row_strategy_for_formatter_rows(
    *,
    rows: Sequence[Mapping[str, Any]],
    row_source: FlowOutputRowSource,
    output_format: FlowOutputFormat,
) -> FlowOutputRowStrategy:
    if row_source != "object":
        return "object"
    has_attribute_fields = any(
        str(field_ref).startswith(_OBJECT_ATTRIBUTE_FIELD_PREFIX)
        for row in rows
        for field_ref in row
    )
    if len(_source_identities_for_formatter_rows(rows)) == 1 and (
        output_format == "tsv"
        or (output_format == "csv" and has_attribute_fields)
    ):
        return "wide_union"
    return "object"


def _default_projection_plan_for_formatter(
    bundle: FlowOutputArtifactBundle,
    *,
    output_format: FlowOutputFormat,
    row_source: FlowOutputRowSource,
    row_strategy: FlowOutputRowStrategy | None,
    source_ref: str | None,
) -> FlowOutputProjectionPlan:
    resolved_source_ref = str(source_ref or "").strip()
    if (
        not resolved_source_ref
        and row_source == "object"
        and bundle.default_source_extraction_result_id
    ):
        resolved_source_ref = (
            f"extraction-result:{bundle.default_source_extraction_result_id}"
        )

    if not resolved_source_ref:
        plan = default_projection_plan(
            bundle,
            output_format=output_format,
            row_source=row_source,
        )
        if row_strategy is None:
            return plan
        return plan.model_copy(
            update={
                "row_strategy": row_strategy,
                "columns": default_columns_for_row_source(
                    bundle,
                    row_source,
                    row_strategy=row_strategy,
                ),
            }
        )

    rows, source_update = _rows_for_source_ref(
        bundle,
        row_source=row_source,
        source_ref=resolved_source_ref,
    )
    selected_strategy = row_strategy or _default_row_strategy_for_formatter_rows(
        rows=rows,
        row_source=row_source,
        output_format=output_format,
    )
    available_refs = {str(field_ref) for row in rows for field_ref in row}
    return FlowOutputProjectionPlan(
        format=output_format,
        row_source=row_source,
        row_strategy=selected_strategy,
        columns=default_columns_for_row_source(
            bundle,
            row_source,
            row_strategy=selected_strategy,
            available_refs=available_refs,
            rows=rows,
        ),
        source_extraction_result_ids=source_update.get(
            "source_extraction_result_ids", []
        ),
        source_keys=source_update.get("source_keys", []),
    )


def _apply_bundle_default_source(
    bundle: FlowOutputArtifactBundle,
    plan: FlowOutputProjectionPlan,
) -> FlowOutputProjectionPlan:
    """Bind an otherwise unscoped object plan to the bundle's default result."""

    default_source_id = str(
        bundle.default_source_extraction_result_id or ""
    ).strip()
    if (
        plan.row_source != "object"
        or plan.source_extraction_result_ids
        or plan.source_keys
        or not default_source_id
    ):
        return plan
    return plan.model_copy(
        update={"source_extraction_result_ids": [default_source_id]}
    )


def _plan_from_args(
    *,
    bundle: FlowOutputArtifactBundle,
    output_format: FlowOutputFormat,
    row_source: str | None,
    field_refs_json: str | None = None,
    filters_json: str | None = None,
    sort_json: str | None = None,
    limit: int | None = None,
) -> FlowOutputProjectionPlan:
    selected_row_source = _coerce_row_source(row_source, bundle.default_row_source)
    field_refs = _parse_field_refs(field_refs_json)
    columns = (
        _columns_for_field_refs(bundle, selected_row_source, field_refs)
        if field_refs
        else _all_columns_for_row_source(bundle, selected_row_source)
    )
    if not columns:
        columns = default_columns_for_row_source(bundle, selected_row_source)
    return FlowOutputProjectionPlan(
        format=output_format,
        row_source=selected_row_source,
        columns=columns,
        filters=_parse_filters(filters_json),
        sort=_parse_sorts(sort_json),
        max_rows=limit,
    )


def _capabilities_payload(
    *,
    output_format: FlowOutputFormat,
    formatter_agent_id: str,
    bundle: FlowOutputArtifactBundle,
) -> dict[str, Any]:
    source_refs = _available_source_refs(bundle)
    return {
        "status": "ok",
        "formatter_agent_id": formatter_agent_id,
        "format": output_format,
        "invariant": (
            "File bytes and chat tables are generated only from validated projections "
            "over the saved artifact bundle, applied by the application to every row. "
            "These tools do not accept raw row arrays, CSV/TSV/JSON/markdown table text, "
            "or model-composed replacement data."
        ),
        "allowed_row_sources": list(get_args(FlowOutputRowSource)),
        "allowed_row_strategies": list(get_args(FlowOutputRowStrategy)),
        "allowed_filter_operators": list(get_args(FlowOutputFilterSpec.model_fields["op"].annotation)),
        "allowed_sort_directions": list(get_args(FlowOutputSortSpec.model_fields["direction"].annotation)),
        "allowed_transform_types": list(get_args(FlowOutputTransformSpec.model_fields["type"].annotation)),
        "transform_rules": {
            "pair_join": (
                "Use exactly two field_refs. Values are joined with pair_separator; "
                "pairs are joined with separator. A scalar broadcasts across a list, "
                "equal-length lists zip, and incompatible list lengths are rejected."
            ),
            "conditional": (
                "Use field_ref plus condition_op/value (or values for 'in') and both "
                "when_true/when_false branch transforms. Branches may use existing "
                "non-conditional transforms such as literal, first_non_empty, or pair_join; "
                "nested conditionals are rejected. The condition is evaluated once per row "
                "against the whole field value, so a list-valued condition selects one branch "
                "for the entire row."
            ),
            "format_elements": (
                "Render aligned list elements with a template. field_refs are the element "
                "values ({1}, {2}, ... in templates); default is the template; optional "
                "field_ref is an element-aligned selector whose value picks a template from "
                "mapping (for example a per-value resolution status). Lists must have equal "
                "lengths, scalars broadcast, empty placeholders render missing_value, and "
                "elements are joined with separator."
            ),
        },
        "overrides": (
            "Optional plan.overrides apply explicit curator-directed changes to the derived "
            "output only: {row_ref, column_key, value} replaces one cell and "
            "{row_ref, exclude: true} drops one row. row_ref values come from "
            "inspect_output_rows or preview_output_projection. Saved results never change."
        ),
        "split_list": (
            "To put list items in separate columns (never extra rows), add split_list to a "
            "field_ref column (a single value counts as a one-item list, giving one numbered "
            "column; do not invent more): {\"header_template\": \"Anatomy Term {n}\"} "
            "or {\"headers\": [\"First\", \"Second\"]} (not both), optional max_columns. "
            "The application sizes it to the longest list, renders each item as display "
            "text and uses missing_value for shorter rows. Too few headers or more items "
            "than the limit fail with an explicit error; items are never dropped. "
            "inspect_output_artifacts and inspect_field_values report max_list_length."
        ),
        "value_display": (
            "CSV, TSV and chat cells render structured values as display text: "
            "\"label (ID)\" from the pack's declared roles (generic curie/id plus "
            "name/label otherwise), lists joined with \"; \", and unresolved values "
            "marked \"(unresolved)\" from declared resolution state, open validation "
            "findings on that field, or a declared ID that is missing. Select the parent "
            "structured field instead of composing leaves. JSON keeps raw values."
        ),
        "detail_access": (
            "inspect_output_artifacts pages and searches the field catalog; "
            "inspect_output_rows and preview_output_projection page rows with row_refs; "
            "read_output_value returns exact slices of one saved value with next_offset."
        ),
        "json_shapes": list(get_args(FlowOutputJsonShape)),
        "format_rules": {
            "csv": "Flat row export. group_by is not supported; use sort/filter/columns/transforms.",
            "tsv": (
                "Flat curation table export from canonical object rows. "
                "Artifact-summary TSV exports and model-written rows are rejected."
            ),
            "json": "Structured export. Supports rows, grouped, and bundle json_shape values.",
            "chat": (
                "Chat table or list rendered by the application from every requested row "
                "and delivered to the curator once. Call finalize_chat_output exactly once; "
                "optional notes carry a brief curator-requested caveat, never table rows."
            ),
        }[output_format],
        "source_refs": source_refs,
        "default_row_source": bundle.default_row_source,
        "projection_limits": {
            "max_projection_rows": _MAX_PROJECTION_ROWS,
            "default_preview_rows": _DEFAULT_PREVIEW_LIMIT,
            "default_inspection_rows": _MAX_CHAT_ROWS,
            "max_tool_response_chars": get_output_tool_max_response_chars(),
            "max_value_read_chars": get_output_tool_value_read_chars(),
        },
    }


def _bounded_list_fields(payload: dict[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    """Cap summary lists at the preview item limit with explicit omitted counts."""

    for key in keys:
        values = payload.get(key)
        if isinstance(values, list) and len(values) > _MAX_LIST_ITEMS:
            payload[key] = values[:_MAX_LIST_ITEMS]
            payload[f"{key}_omitted"] = len(values) - _MAX_LIST_ITEMS
    return payload


def _bounded_generic_source_summary(bundle: FlowOutputArtifactBundle) -> dict[str, Any]:
    """Generic-source summary with bounded key lists; the catalog lists every key."""

    summary = _generic_source_summary(bundle)
    key_lists = ("all_attribute_keys", "shared_attribute_keys", "keys_missing_from_some_objects")
    for source in summary["sources"]:
        _bounded_list_fields(
            source,
            (*key_lists, "adapter_keys", "domain_pack_ids", "object_types", "semantic_classes"),
        )
        for group in source["semantic_class_attribute_groups"]:
            _bounded_list_fields(group, key_lists)
        for notice in source["notices"]:
            _bounded_list_fields(notice, ("keys_missing_from_some_objects",))
        _bounded_list_fields(source, ("semantic_class_attribute_groups", "notices"))
    _bounded_list_fields(summary, ("sources",))
    if "_omitted" in json.dumps(summary):
        summary["complete_keys"] = (
            "Some lists are shortened; search inspect_output_artifacts with "
            "catalog_query='object.attribute.' for every attribute field."
        )
    return summary


def _next_row_cursor(offset: int, page_length: int, available: int) -> str:
    next_offset = offset + page_length
    return str(next_offset) if next_offset < available else ""


def _max_list_length(rows: Sequence[Mapping[str, Any]], field_ref: str) -> int | None:
    lengths = [len(row[field_ref]) for row in rows if isinstance(row.get(field_ref), list)]
    return max(lengths) if lengths else None


def _catalog_entry(field: Any, *, example_limit: int, bundle: Any = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "ref": field.ref,
        "label": field.label,
        "row_source": field.row_source,
        "value_type": field.value_type,
        "non_empty_count": field.non_empty_count,
    }
    if bundle is not None:
        # Lets the formatter size and explain split_list columns.
        longest = _max_list_length(bundle.rows_for_source(field.row_source), field.ref)
        if longest is not None:
            entry["max_list_length"] = longest
    if example_limit:
        entry["examples"] = [
            _example_preview(example) for example in list(field.examples)[:example_limit]
        ]
    return entry


def _report_delivery_failure(
    *,
    message: str,
    tool_name: str,
    formatter_agent_id: str,
    output_format: str,
    report_context: Mapping[str, Any],
    measured: int | None = None,
    limit: int | None = None,
    setting: str | None = None,
    unit: str = "characters",
    correlation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Report one failed output finalization/delivery and return its compact diagnostic."""

    violation = PayloadContractViolation(
        category="output_delivery_failure",
        component="output_formatter_tools",
        message=message,
        measured=measured,
        unit=unit,
        limit=limit,
        setting=setting,
    )
    report_payload_contract_violation(
        violation,
        phase="formatter_finalization",
        tool_name=tool_name,
        agent=formatter_agent_id,
        trace_id=report_context.get("trace_id"),
        session_id=report_context.get("session_id"),
        correlation={
            **dict(report_context.get("correlation") or {}),
            "output_format": output_format,
            **dict(correlation or {}),
        },
    )
    return violation.diagnostic()


def build_output_formatter_tools(
    *,
    bundle: FlowOutputArtifactBundle,
    output_format: str,
    formatter_agent_id: str,
    save_projected_output: FormatterSaveCallback | None = None,
    configured_plan: Any = None,
    deliver_chat_output: ChatDeliveryCallback | None = None,
) -> list[Any]:
    """Build runtime-bound formatter tools over a saved artifact bundle.

    File formats (csv/tsv/json) finalize through ``save_projected_output``.
    The chat format finalizes through ``deliver_chat_output``: application code
    renders every requested row and the model receives only a compact receipt.
    """

    normalized_format = str(output_format or "").strip().lower()
    is_chat = normalized_format == _CHAT_FORMAT
    if normalized_format not in _SUPPORTED_FILE_FORMATS and not is_chat:
        supported = ", ".join(sorted({*_SUPPORTED_FILE_FORMATS, _CHAT_FORMAT}))
        raise ValueError(f"output_format must be one of: {supported}.")
    if is_chat and deliver_chat_output is None:
        raise ValueError("Chat formatter tools require a chat output delivery callback.")
    if not is_chat and save_projected_output is None:
        raise ValueError("File formatter tools require a projected file save callback.")
    resolved_output_format = cast(FlowOutputFormat, normalized_format)
    locked_plan = None
    if isinstance(configured_plan, Mapping) and configured_plan.get("selection_mode") == "selected_fields":
        locked_plan = FlowOutputProjectionPlan.model_validate(configured_plan)
        if locked_plan.format != normalized_format:
            raise ValueError("Saved field layout does not match this file format.")
        errors, _, _ = validate_projection_plan(bundle, locked_plan)
        if errors:
            raise ValueError("; ".join(errors))

    def enforce_selection(plan: FlowOutputProjectionPlan) -> FlowOutputProjectionPlan:
        if locked_plan is not None and plan != locked_plan:
            raise ValueError("The curator selected fixed output fields. Use build_default_projection_plan and keep that exact plan; edit the flow to change it.")
        return plan

    def respond(
        tool_name: str,
        payload: Mapping[str, Any],
        *,
        committed_keys: Sequence[str] = (),
    ) -> str:
        return _budgeted_tool_json(
            payload,
            tool_name=tool_name,
            formatter_agent_id=formatter_agent_id,
            report_context=_report_context(bundle),
            committed_keys=committed_keys,
        )

    def plan_response(
        tool_name: str,
        plan: FlowOutputProjectionPlan,
        cursor: str = "",
    ) -> str:
        """Validated plan echo; wide plans page their columns within the budget."""

        errors, warnings, columns = validate_projection_plan(bundle, plan)
        if not errors:
            errors.extend(_formatter_plan_constraint_errors(plan, columns))
        base = {
            "status": "invalid" if errors else "ok",
            "errors": [_bounded_text(error) for error in errors[:_MAX_LIST_ITEMS]],
            "error_count": len(errors),
            "warnings": [_bounded_text(warning) for warning in warnings[:_MAX_LIST_ITEMS]],
        }
        offset = _parse_cursor(cursor)
        if not offset:
            full = {
                **base,
                "columns": _compact_columns(columns),
                "plan": plan.model_dump(mode="json", exclude_defaults=True),
            }
            if len(_tool_json(full)) <= get_output_tool_max_response_chars():
                return respond(tool_name, full)
        if offset > len(columns):
            raise ValueError(f"cursor {offset} is beyond the plan's {len(columns)} columns.")
        column_specs = [column.model_dump(mode="json", exclude_defaults=True) for column in columns]
        plan_metadata = plan.model_dump(mode="json", exclude_defaults=True, exclude={"columns"})

        def build(page: list[Any], next_cursor: str) -> dict[str, Any]:
            return {
                **base,
                "plan": plan_metadata,
                "plan_columns_paged": True,
                "column_count": len(columns),
                "columns_cursor": str(offset),
                "next_columns_cursor": next_cursor,
                "columns": page,
                "note": (
                    "This plan's columns are paged. Pass an empty plan_json to "
                    "validate/preview/finalize to use this plan unchanged, or rebuild "
                    "plan.columns from all pages before changing it."
                ),
            }

        page, next_cursor = _page_to_budget(
            column_specs,
            start=offset,
            max_count=max(1, len(column_specs)),
            build_payload=build,
        )
        return respond(tool_name, build(page, next_cursor))

    def resolve_final_plan(plan_json: str) -> FlowOutputProjectionPlan:
        if str(plan_json or "").strip():
            plan = _projection_plan_from_tool_payload(
                plan_json,
                output_format=resolved_output_format,
            )
            return enforce_selection(plan) if locked_plan is not None else _apply_bundle_default_source(bundle, plan)
        if locked_plan is not None:
            return locked_plan.model_copy(deep=True)
        return _default_projection_plan_for_formatter(
            bundle,
            output_format=resolved_output_format,
            row_source=bundle.default_row_source,
            row_strategy=None,
            source_ref=None,
        )

    def final_plan_errors(plan: FlowOutputProjectionPlan) -> tuple[list[str], list[str]]:
        errors, warnings, columns = validate_projection_plan(bundle, plan)
        if not errors:
            errors.extend(_formatter_plan_constraint_errors(plan, columns))
        if (
            not errors
            and not bundle.rows_for_source(plan.row_source)
            and projection_plan_allows_empty_bundle(plan)
        ):
            errors.append(
                "Formatter tools cannot produce literal-only output without saved source rows."
            )
        return errors, warnings

    saver = save_projected_output
    finalization_lock = asyncio.Lock()
    finalized_file_info: dict[str, Any] | None = None
    finalized_chat_receipt: dict[str, Any] | None = None

    @function_tool(
        name_override="explain_formatter_capabilities",
        description_override=(
            "Return the structure-owned formatter capabilities, constraints, row "
            "sources, transforms, overrides, response budgets, and no-raw-rows invariant."
        ),
        strict_mode=False,
    )
    async def _explain_formatter_capabilities() -> str:
        return respond(
            "explain_formatter_capabilities",
            _capabilities_payload(
                output_format=resolved_output_format,
                formatter_agent_id=formatter_agent_id,
                bundle=bundle,
            ),
        )

    @function_tool(
        name_override="inspect_output_artifacts",
        description_override=(
            "Inspect row-source counts, default column refs, source ids/keys and one "
            "bounded page of the saved field catalog. Search the catalog with "
            "catalog_query (matches field ref or label) and row_source; continue with "
            "the returned next_cursor."
        ),
        strict_mode=False,
    )
    async def _inspect_output_artifacts(
        catalog_query: str = "",
        row_source: str = "",
        cursor: str = "",
        limit: int | None = None,
        example_limit: int | None = None,
    ) -> str:
        try:
            examples = (
                1
                if example_limit is None
                else max(0, min(int(example_limit), _MAX_FIELD_EXAMPLES))
            )
            page_size = _positive_limit(
                limit,
                default=get_output_tool_catalog_page_size(),
                ceiling=get_output_tool_catalog_page_size(),
            )
            offset = _parse_cursor(cursor)
            selected_row_source = (
                _coerce_row_source(row_source, bundle.default_row_source)
                if str(row_source or "").strip()
                else None
            )
            query = str(catalog_query or "").strip().casefold()
            matching = [
                field
                for field in bundle.field_catalog
                if (selected_row_source is None or field.row_source == selected_row_source)
                and (
                    not query
                    or query in field.ref.casefold()
                    or query in str(field.label or "").casefold()
                )
            ]
            if offset > len(matching):
                raise ValueError(
                    f"cursor {offset} is beyond the {len(matching)} matching catalog fields."
                )
            entries = matching
            row_sources = {
                source: {
                    "row_count": len(bundle.rows_for_source(source)),  # type: ignore[arg-type]
                    "catalog_field_count": sum(
                        1 for field in bundle.field_catalog if field.row_source == source
                    ),
                    **_bounded_list_fields(
                        {
                            "default_column_refs": [
                                column.field_ref
                                for column in default_columns_for_row_source(bundle, source)  # type: ignore[arg-type]
                                if column.field_ref
                            ]
                        },
                        ("default_column_refs",),
                    ),
                }
                for source in get_args(FlowOutputRowSource)
            }

            generic_summary = _bounded_generic_source_summary(bundle)

            def build(page: list[Any], next_cursor: str) -> dict[str, Any]:
                return {
                    "status": "ok",
                    "inventory": {
                        "flow_name": bundle.flow_name,
                        "flow_run_id": bundle.flow_run_id,
                        "document_id": bundle.document_id,
                        "default_row_source": bundle.default_row_source,
                        "artifact_count": len(bundle.artifacts),
                        "row_sources": row_sources,
                        "source_refs": _available_source_refs(bundle),
                        "generic_source_summary": generic_summary,
                        "warning_count": len(bundle.warnings),
                        "warnings": [
                            _bounded_text(warning) for warning in bundle.warnings[:_MAX_LIST_ITEMS]
                        ],
                        "field_catalog": {
                            "total_fields": len(bundle.field_catalog),
                            "matching_fields": len(matching),
                            "catalog_query": catalog_query or "",
                            "row_source": selected_row_source or "",
                            "cursor": str(offset),
                            "next_cursor": next_cursor,
                            "entries": page,
                        },
                    },
                }

            page, next_cursor = _page_to_budget(
                entries,
                start=offset,
                max_count=page_size,
                build_payload=build,
                render=lambda field: _catalog_entry(field, example_limit=examples, bundle=bundle),
            )
            return respond("inspect_output_artifacts", build(page, next_cursor))
        except Exception as exc:
            return respond("inspect_output_artifacts", {"status": "invalid", "errors": [str(exc)]})

    @function_tool(
        name_override="inspect_output_rows",
        description_override=(
            "Inspect a bounded page of saved rows or selected field refs after optional "
            "projection-style filters and sorts. Without field_refs_json, rows are keyed "
            "by field ref and only a column count is returned. Returns row_refs for "
            "overrides and read_output_value, plus next_cursor. Inputs are field refs "
            "and plan metadata only, never row contents."
        ),
        strict_mode=False,
    )
    async def _inspect_output_rows(
        row_source: str = "object",
        field_refs_json: str = "",
        filters_json: str = "",
        sort_json: str = "",
        limit: int | None = None,
        cursor: str = "",
    ) -> str:
        try:
            page_size = _positive_limit(
                limit,
                default=_MAX_CHAT_ROWS,
                ceiling=_MAX_PROJECTION_ROWS,
            )
            offset = _parse_cursor(cursor)
            plan = _plan_from_args(
                bundle=bundle,
                output_format=resolved_output_format,
                row_source=row_source,
                field_refs_json=field_refs_json,
                filters_json=filters_json,
                sort_json=sort_json,
                limit=min(offset + page_size, _MAX_PROJECTION_ROWS),
            )
            result = apply_projection_plan(bundle, plan, render_display=False)
            column_field_refs = {column.key: column.field_ref for column in result.columns}
            available = min(result.total_count, _MAX_PROJECTION_ROWS)
            if offset > available:
                raise ValueError(
                    f"cursor {offset} is beyond the {available} inspectable matching rows."
                )
            items = list(zip(result.row_refs, result.rows))
            # Without requested field refs every field is a column; key rows by
            # field ref and send only a count instead of the full column list
            # on every page, so wide row sources still fit several rows.
            all_fields = not str(field_refs_json or "").strip()
            columns_payload: Any = (
                {
                    "column_count": len(result.columns),
                    "row_keys": "field_refs",
                    "list_fields_with": (
                        f"inspect_output_artifacts(row_source='{result.row_source}') "
                        "pages the full field catalog"
                    ),
                }
                if all_fields
                else _compact_columns(result.columns)
            )

            def render_row(item: tuple[str, Mapping[str, Any]]) -> dict[str, Any]:
                row_ref, row = item
                if all_fields:
                    keyed = {column_field_refs.get(key) or key: value for key, value in row.items()}
                    return {"row_ref": row_ref, "row": _bounded_row(keyed, row_ref=row_ref)}
                return {
                    "row_ref": row_ref,
                    "row": _bounded_row(row, row_ref=row_ref, field_refs=column_field_refs),
                }

            def build(page: list[Any], next_cursor: str) -> dict[str, Any]:
                return {
                    "status": "ok",
                    "row_source": result.row_source,
                    "columns": columns_payload,
                    "rows": [item["row"] for item in page],
                    "row_refs": [item["row_ref"] for item in page],
                    "total_count": result.total_count,
                    **(
                        {
                            "rows_beyond_inspection_ceiling": result.total_count - available,
                            "narrow_with": "filters_json",
                        }
                        if result.total_count > available
                        else {}
                    ),
                    "cursor": str(offset),
                    "next_cursor": next_cursor,
                    "truncated": bool(next_cursor),
                    "warnings": [
                        _bounded_text(warning) for warning in result.warnings[:_MAX_LIST_ITEMS]
                    ],
                }

            page, _ = _page_to_budget(
                items,
                start=offset,
                max_count=page_size,
                build_payload=build,
                render=render_row,
            )
            next_cursor = _next_row_cursor(offset, len(page), available)
            return respond("inspect_output_rows", build(page, next_cursor))
        except Exception as exc:
            return respond("inspect_output_rows", {"status": "invalid", "errors": [str(exc)]})

    @function_tool(
        name_override="inspect_field_values",
        description_override=(
            "Inspect a bounded page of distinct saved values and counts for one field "
            "ref after optional projection-style filters; continue with next_cursor. "
            "Does not accept replacement values."
        ),
        strict_mode=False,
    )
    async def _inspect_field_values(
        row_source: str,
        field_ref: str,
        filters_json: str = "",
        limit: int | None = None,
        cursor: str = "",
    ) -> str:
        try:
            selected_row_source = _coerce_row_source(row_source, bundle.default_row_source)
            selected_field_ref = str(field_ref or "").strip()
            if selected_field_ref not in bundle.field_refs_for_source(selected_row_source):
                raise ValueError(f"Unknown field ref '{selected_field_ref}'.")
            offset = _parse_cursor(cursor)
            plan = FlowOutputProjectionPlan(
                format=resolved_output_format,
                row_source=selected_row_source,
                columns=[
                    FlowOutputColumnSpec(
                        key="value",
                        header=_field_label(bundle, selected_field_ref),
                        field_ref=selected_field_ref,
                    )
                ],
                filters=_parse_filters(filters_json),
                max_rows=_MAX_PROJECTION_ROWS,
            )
            result = apply_projection_plan(bundle, plan, render_display=False)
            counts: Counter[str] = Counter()
            examples: dict[str, Any] = {}
            for row in result.rows:
                value = row.get("value")
                encoded = json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, default=str)
                counts[encoded] += 1
                examples.setdefault(encoded, value)
            if offset > len(counts):
                raise ValueError(
                    f"cursor {offset} is beyond the {len(counts)} distinct values."
                )
            value_limit = _positive_limit(
                limit,
                default=_MAX_LIST_ITEMS,
                ceiling=_MAX_PROJECTION_ROWS,
            )
            values = counts.most_common()
            longest_list = _max_list_length(
                [{selected_field_ref: row.get("value")} for row in result.rows],
                selected_field_ref,
            )

            def build(page: list[Any], next_cursor: str) -> dict[str, Any]:
                return {
                    "status": "ok",
                    "row_source": selected_row_source,
                    "field_ref": selected_field_ref,
                    "total_rows": result.total_count,
                    "distinct_count": len(counts),
                    **(
                        {"max_list_length": longest_list}
                        if longest_list is not None
                        else {}
                    ),
                    "values": page,
                    "cursor": str(offset),
                    "next_cursor": next_cursor,
                    "values_truncated": bool(next_cursor),
                    "warnings": [
                        _bounded_text(warning) for warning in result.warnings[:_MAX_LIST_ITEMS]
                    ],
                }

            page, next_cursor = _page_to_budget(
                values,
                start=offset,
                max_count=value_limit,
                build_payload=build,
                render=lambda item: {
                    "value": _bounded_field_value(examples[item[0]], field_ref=selected_field_ref),
                    "count": item[1],
                },
            )
            return respond("inspect_field_values", build(page, next_cursor))
        except Exception as exc:
            return respond("inspect_field_values", {"status": "invalid", "errors": [str(exc)]})

    @function_tool(
        name_override="read_output_value",
        description_override=(
            "Read an exact slice of one saved value by row_ref and field_ref. Text "
            "values are returned as-is and structured values as JSON; continue with "
            "next_offset until it is null. Use for long or nested fields that other "
            "tools only preview."
        ),
        strict_mode=False,
    )
    async def _read_output_value(
        row_ref: str,
        field_ref: str,
        offset: int = 0,
        max_chars: int | None = None,
    ) -> str:
        try:
            row_source, row = bundle_row_for_ref(bundle, row_ref)
            selected_field_ref = str(field_ref or "").strip()
            if (
                selected_field_ref not in row
                and selected_field_ref not in bundle.field_refs_for_source(row_source)
            ):
                raise ValueError(
                    f"Unknown field ref '{selected_field_ref}' for {row_source} rows."
                )
            value = row.get(selected_field_ref)
            if isinstance(value, str):
                text, encoding = value, "text"
            else:
                text = json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, default=str)
                encoding = "json"
            start = int(offset or 0)
            if start < 0 or start > len(text):
                raise ValueError(
                    f"offset {start} is outside the value's {len(text)} characters."
                )
            read_ceiling = get_output_tool_value_read_chars()
            chunk_size = _positive_limit(max_chars, default=read_ceiling, ceiling=read_ceiling)

            def build(chunk: str, next_offset: int | None) -> dict[str, Any]:
                return {
                    "status": "ok",
                    "row_ref": str(row_ref).strip(),
                    "field_ref": selected_field_ref,
                    "encoding": encoding,
                    "total_chars": len(text),
                    "offset": start,
                    "next_offset": next_offset,
                    "value_slice": chunk,
                }

            # JSON escaping can expand a slice; shrink until the response fits.
            budget = get_output_tool_max_response_chars()
            while True:
                chunk = text[start : start + chunk_size]
                end = start + len(chunk)
                payload = build(chunk, end if end < len(text) else None)
                if len(_tool_json(payload)) <= budget or chunk_size <= 1:
                    break
                chunk_size = max(1, chunk_size // 2)
            return respond("read_output_value", payload)
        except Exception as exc:
            return respond("read_output_value", {"status": "invalid", "errors": [str(exc)]})

    @function_tool(
        name_override="build_default_projection_plan",
        description_override=(
            "Build and validate the default projection plan for this bound "
            "format, optionally selecting row source, object row strategy, or source ref."
        ),
        strict_mode=False,
    )
    async def _build_default_projection_plan(
        row_source: str = "",
        row_strategy: str = "",
        source_ref: str = "",
        cursor: str = "",
    ) -> str:
        try:
            if locked_plan is not None:
                return plan_response("build_default_projection_plan", locked_plan, cursor)
            selected_row_source = _coerce_row_source(row_source, bundle.default_row_source)
            selected_row_strategy = _coerce_row_strategy(row_strategy)
            plan = _default_projection_plan_for_formatter(
                bundle,
                output_format=resolved_output_format,
                row_source=selected_row_source,
                row_strategy=selected_row_strategy,
                source_ref=source_ref,
            )
            return plan_response("build_default_projection_plan", plan, cursor)
        except Exception as exc:
            return respond("build_default_projection_plan", {"status": "invalid", "errors": [str(exc)]})

    @function_tool(
        name_override="validate_output_projection",
        description_override=(
            "Validate a projection plan over saved bundle fields. Empty plan_json "
            "validates the default (or curator-fixed) plan. The format is forced to "
            "this formatter's output type. Extra raw-content keys are rejected."
        ),
        strict_mode=False,
    )
    async def _validate_output_projection(plan_json: str = "", cursor: str = "") -> str:
        try:
            plan = resolve_final_plan(plan_json)
            return plan_response("validate_output_projection", plan, cursor)
        except Exception as exc:
            return respond("validate_output_projection", {"status": "invalid", "errors": [str(exc)]})

    @function_tool(
        name_override="preview_output_projection",
        description_override=(
            "Validate and preview a bounded page of projected rows with row_refs, "
            "showing the rendered cell text (structured values as \"label (ID)\"); "
            "continue with next_cursor. Accepts plan JSON only, never replacement "
            "row contents."
        ),
        strict_mode=False,
    )
    async def _preview_output_projection(
        plan_json: str = "",
        limit: int | None = None,
        cursor: str = "",
    ) -> str:
        try:
            plan = resolve_final_plan(plan_json)
            errors, warnings, columns = validate_projection_plan(bundle, plan)
            if not errors:
                errors.extend(_formatter_plan_constraint_errors(plan, columns))
            if errors:
                return respond(
                    "preview_output_projection",
                    {
                        "status": "invalid",
                        "preview": {
                            "status": "invalid",
                            "errors": [_bounded_text(error) for error in errors[:_MAX_LIST_ITEMS]],
                            "error_count": len(errors),
                            "warnings": [
                                _bounded_text(warning) for warning in warnings[:_MAX_LIST_ITEMS]
                            ],
                        },
                    },
                )
            preview_limit = _positive_limit(
                limit,
                default=_DEFAULT_PREVIEW_LIMIT,
                ceiling=_MAX_PROJECTION_ROWS,
            )
            offset = _parse_cursor(cursor)
            result = apply_projection_plan(
                bundle,
                plan,
                preview_limit=min(offset + preview_limit, _MAX_PROJECTION_ROWS),
                render_display=True,
            )
            column_field_refs = {column.key: column.field_ref for column in result.columns}
            # Rows a finalized output would contain: an explicit max_rows limits them.
            available = min(result.total_count, plan.max_rows or _MAX_PROJECTION_ROWS)
            if offset > available:
                raise ValueError(
                    f"cursor {offset} is beyond the {available} rows this projection outputs."
                )
            items = list(zip(result.row_refs, result.rows))

            def build(page: list[Any], next_cursor: str) -> dict[str, Any]:
                return {
                    "status": "ok",
                    "preview": {
                        "status": "ok",
                        "errors": [],
                        "warnings": [
                            _bounded_text(warning) for warning in result.warnings[:_MAX_LIST_ITEMS]
                        ],
                        "columns": _compact_columns(result.columns),
                        "preview_rows": [item["row"] for item in page],
                        "row_refs": [item["row_ref"] for item in page],
                        "total_count": result.total_count,
                        "output_row_count": available,
                        "limited_by_max_rows": bool(
                            plan.max_rows is not None and result.total_count > plan.max_rows
                        ),
                        "cursor": str(offset),
                        "next_cursor": next_cursor,
                        "truncated": bool(next_cursor),
                    },
                }

            page, _ = _page_to_budget(
                items,
                start=offset,
                max_count=preview_limit,
                build_payload=build,
                render=lambda item: {
                    "row_ref": item[0],
                    "row": _bounded_row(item[1], row_ref=item[0], field_refs=column_field_refs),
                },
            )
            next_cursor = _next_row_cursor(offset, len(page), available)
            return respond("preview_output_projection", build(page, next_cursor))
        except Exception as exc:
            return respond("preview_output_projection", {"status": "invalid", "errors": [str(exc)]})

    @function_tool(
        name_override="finalize_and_save",
        description_override=(
            "Finalize a projection over all saved bundle rows and save one CSV/TSV/JSON file. "
            "Empty plan_json uses the validated default projection. Returns a compact "
            "receipt; this tool never accepts raw rows or file text."
        ),
        strict_mode=False,
    )
    async def _finalize_and_save(plan_json: str = "", filename_hint: str = "") -> str:
        nonlocal finalized_file_info
        async with finalization_lock:
            if finalized_file_info is not None:
                # The duplicate request is invalid; the original finalized file is echoed for model recovery.
                return respond(
                    "finalize_and_save",
                    {
                        "status": "invalid",
                        "code": "already_finalized",
                        "format": resolved_output_format,
                        "formatter_agent_id": formatter_agent_id,
                        "saved_file": True,
                        "errors": [
                            (
                                "This formatter run has already finalized and saved one file. "
                                "Start a new formatter run after any curator-requested changes "
                                "to create another file."
                            )
                        ],
                        "finalized_file": finalized_file_info,
                    },
                )

            try:
                plan = resolve_final_plan(plan_json)
                errors, warnings = final_plan_errors(plan)
                if errors:
                    return respond(
                        "finalize_and_save",
                        {
                            "status": "invalid",
                            "errors": [_bounded_text(error) for error in errors[:_MAX_LIST_ITEMS]],
                            "error_count": len(errors),
                            "warnings": [
                                _bounded_text(warning) for warning in warnings[:_MAX_LIST_ITEMS]
                            ],
                        },
                    )
                projection = finalize_output_projection(bundle, plan)
                if projection.total_count < 1 and locked_plan is None:
                    return respond(
                        "finalize_and_save",
                        {
                            "status": "invalid",
                            "errors": [
                                "Projection matched no saved rows; call formatter_cannot_complete "
                                "or inspect the saved bundle before trying again."
                            ],
                            "projection_summary": _projection_summary(projection),
                        },
                    )
            except FlowOutputOperationalCeilingError as exc:
                diagnostic = _report_delivery_failure(
                    message=str(exc),
                    tool_name="finalize_and_save",
                    formatter_agent_id=formatter_agent_id,
                    output_format=resolved_output_format,
                    report_context=_report_context(bundle),
                    measured=exc.measured,
                    limit=exc.limit,
                    setting=exc.setting,
                    unit=exc.unit,
                )
                return respond(
                    "finalize_and_save",
                    {
                        "status": "failed",
                        "code": "operational_ceiling_exceeded",
                        "saved_file": False,
                        "errors": [str(exc)],
                        "diagnostic": diagnostic,
                    },
                )
            except Exception as exc:
                return respond("finalize_and_save", {"status": "invalid", "errors": [str(exc)]})

            descriptor = (
                str(filename_hint or "").strip()
                or f"{bundle.flow_name}_{resolved_output_format}_export"
            )
            try:
                file_info = dict(
                    await cast(FormatterSaveCallback, saver)(
                        resolved_output_format,
                        projection,
                        descriptor,
                        formatter_agent_id,
                    )
                )
            except Exception as exc:
                diagnostic = _report_delivery_failure(
                    message=f"Projected file save failed: {type(exc).__name__}",
                    tool_name="finalize_and_save",
                    formatter_agent_id=formatter_agent_id,
                    output_format=resolved_output_format,
                    report_context=_report_context(bundle),
                    measured=projection.total_count,
                    unit="rows",
                )
                return respond(
                    "finalize_and_save",
                    {
                        "status": "failed",
                        "code": "save_failed",
                        "saved_file": False,
                        "errors": [_bounded_text(f"The file could not be saved: {exc}")],
                        "diagnostic": diagnostic,
                    },
                )
            file_info.setdefault("format", resolved_output_format)
            file_info["status"] = "ok"
            file_info["projection_summary"] = _projection_summary(projection)
            finalized_file_info = {
                key: _jsonable(value)
                for key, value in file_info.items()
                if key
                in {
                    "file_id",
                    "filename",
                    "format",
                    "download_url",
                    "projection_summary",
                }
            }
            return respond(
                "finalize_and_save",
                _jsonable(file_info),
                committed_keys=(
                    "status", "file_id", "filename", "format", "download_url",
                    "size_bytes", "saved_file",
                ),
            )

    @function_tool(
        name_override="finalize_chat_output",
        description_override=(
            "Finalize a projection over all saved bundle rows and deliver the rendered "
            "chat table to the curator exactly once. Empty plan_json uses the validated "
            "default projection; notes adds a brief caveat below the table. Returns a "
            "compact receipt, never the table."
        ),
        strict_mode=False,
    )
    async def _finalize_chat_output(plan_json: str = "", notes: str = "") -> str:
        nonlocal finalized_chat_receipt

        def chat_failure(payload: dict[str, Any]) -> str:
            # Reported once here; the flow step reports the outcome, not again.
            record_chat_output_failure(payload)
            return respond("finalize_chat_output", payload)

        async with finalization_lock:
            if finalized_chat_receipt is not None:
                return respond(
                    "finalize_chat_output",
                    {
                        "status": "invalid",
                        "code": "already_finalized",
                        "format": resolved_output_format,
                        "delivered": True,
                        "errors": [
                            "This formatter run has already delivered its chat output. "
                            "Stop and report the delivered receipt."
                        ],
                        "finalized_output": finalized_chat_receipt,
                    },
                )
            note_text = str(notes or "").strip()
            notes_limit = get_flow_output_chat_notes_max_chars()
            note_errors: list[str] = []
            if len(note_text) > notes_limit:
                note_errors.append(
                    f"notes has {len(note_text)} characters; the limit is {notes_limit}. "
                    "Keep notes to a brief caveat."
                )
            if any(line.lstrip().startswith("|") for line in note_text.splitlines()):
                note_errors.append(
                    "notes cannot contain table rows; table content comes only from the projection."
                )
            if note_errors:
                return respond("finalize_chat_output", {"status": "invalid", "errors": note_errors})
            try:
                plan = resolve_final_plan(plan_json)
                errors, warnings = final_plan_errors(plan)
                if errors:
                    return respond(
                        "finalize_chat_output",
                        {
                            "status": "invalid",
                            "errors": [_bounded_text(error) for error in errors[:_MAX_LIST_ITEMS]],
                            "error_count": len(errors),
                            "warnings": [
                                _bounded_text(warning) for warning in warnings[:_MAX_LIST_ITEMS]
                            ],
                        },
                    )
                projection = finalize_output_projection(bundle, plan)
                if projection.total_count < 1 and locked_plan is None:
                    return respond(
                        "finalize_chat_output",
                        {
                            "status": "invalid",
                            "errors": [
                                "Projection matched no saved rows; call formatter_cannot_complete "
                                "or inspect the saved bundle before trying again."
                            ],
                            "projection_summary": _projection_summary(projection),
                        },
                    )
            except FlowOutputOperationalCeilingError as exc:
                diagnostic = _report_delivery_failure(
                    message=str(exc),
                    tool_name="finalize_chat_output",
                    formatter_agent_id=formatter_agent_id,
                    output_format=resolved_output_format,
                    report_context=_report_context(bundle),
                    measured=exc.measured,
                    limit=exc.limit,
                    setting=exc.setting,
                    unit=exc.unit,
                )
                return chat_failure(
                    {
                        "status": "failed",
                        "code": "operational_ceiling_exceeded",
                        "delivered": False,
                        "errors": [str(exc)],
                        "diagnostic": diagnostic,
                    },
                )
            except Exception as exc:
                return respond("finalize_chat_output", {"status": "invalid", "errors": [str(exc)]})

            content = str(projection.chat_output or "")
            if note_text:
                content = f"{content}\n\n{note_text}"
            content_limit = get_flow_output_chat_max_chars()
            if len(content) > content_limit:
                message = (
                    f"The rendered chat output has {len(content)} characters, above the "
                    f"operational ceiling of {content_limit}. No partial output was "
                    "delivered; the saved results are unchanged."
                )
                diagnostic = _report_delivery_failure(
                    message=message,
                    tool_name="finalize_chat_output",
                    formatter_agent_id=formatter_agent_id,
                    output_format=resolved_output_format,
                    report_context=_report_context(bundle),
                    measured=len(content),
                    limit=content_limit,
                    setting="FLOW_OUTPUT_CHAT_MAX_CHARS",
                    correlation={"row_count": projection.total_count},
                )
                return chat_failure(
                    {
                        "status": "failed",
                        "code": "operational_ceiling_exceeded",
                        "delivered": False,
                        "errors": [message],
                        "diagnostic": diagnostic,
                    },
                )
            receipt = {
                "status": "ok",
                "delivered": True,
                "format": resolved_output_format,
                "formatter_agent_id": formatter_agent_id,
                "output_chars": len(content),
                "notes_included": bool(note_text),
                "projection_summary": _projection_summary(projection),
            }
            try:
                delivered = dict(
                    await cast(ChatDeliveryCallback, deliver_chat_output)(content, receipt)
                )
            except Exception as exc:
                diagnostic = _report_delivery_failure(
                    message=f"Chat output delivery failed: {type(exc).__name__}",
                    tool_name="finalize_chat_output",
                    formatter_agent_id=formatter_agent_id,
                    output_format=resolved_output_format,
                    report_context=_report_context(bundle),
                    measured=len(content),
                )
                return chat_failure(
                    {
                        "status": "failed",
                        "code": "delivery_failed",
                        "delivered": False,
                        "errors": [_bounded_text(f"The chat output could not be delivered: {exc}")],
                        "diagnostic": diagnostic,
                    },
                )
            finalized_chat_receipt = {
                key: _jsonable(value) for key, value in delivered.items()
            }
            return respond(
                "finalize_chat_output",
                finalized_chat_receipt,
                committed_keys=("status", "delivered", "chat_output_id", "format", "output_chars"),
            )

    @function_tool(
        name_override="formatter_cannot_complete",
        description_override=(
            "Return a structured cannot-complete result when the saved bundle "
            "cannot support the requested output. This does not save or deliver output."
        ),
        strict_mode=False,
    )
    async def _formatter_cannot_complete(
        reason: str,
        missing_data: str = "",
        suggested_next_step: str = "",
    ) -> str:
        payload = {
            "status": "cannot_complete",
            "format": resolved_output_format,
            "formatter_agent_id": formatter_agent_id,
            "reason": _bounded_text(reason),
            "missing_data": _bounded_text(missing_data),
            "suggested_next_step": _bounded_text(suggested_next_step),
            "saved_file": False,
        }
        if is_chat:
            payload["delivered"] = False
            record_chat_output_cannot_complete(payload)
        return respond("formatter_cannot_complete", payload)

    finalizer = _finalize_chat_output if is_chat else _finalize_and_save
    return [
        _explain_formatter_capabilities,
        _inspect_output_artifacts,
        _inspect_output_rows,
        _inspect_field_values,
        _read_output_value,
        _build_default_projection_plan,
        _validate_output_projection,
        _preview_output_projection,
        finalizer,
        _formatter_cannot_complete,
    ]


__all__ = [
    "ChatDeliveryCallback",
    "FormatterSaveCallback",
    "build_output_formatter_tools",
]
