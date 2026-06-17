"""Runtime-bound formatter tools for structure-owned CSV/TSV/JSON exports."""

from __future__ import annotations

from collections import Counter
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, cast, get_args

from agents import function_tool
from pydantic import BaseModel, ValidationError

from src.lib.flows.output_projection import (
    FlowOutputArtifactBundle,
    FlowOutputColumnSpec,
    FlowOutputFilterSpec,
    FlowOutputFormat,
    FlowOutputJsonShape,
    FlowOutputProjectionPlan,
    FlowOutputProjectionResult,
    FlowOutputRowSource,
    FlowOutputRowStrategy,
    FlowOutputSortSpec,
    FlowOutputTransformSpec,
    apply_projection_plan,
    default_columns_for_row_source,
    default_projection_plan,
    finalize_output_projection,
    inspect_output_artifacts as inspect_projection_artifacts,
    preview_output_projection as preview_projection,
    projection_plan_allows_empty_bundle,
    validate_projection_plan,
)
from src.lib.openai_agents.config import (
    get_flow_chat_max_rows,
    get_flow_output_projection_preview_limit,
    get_flow_projection_max_field_examples,
    get_flow_projection_max_list_items,
    get_flow_projection_max_object_items,
    get_flow_projection_max_row_chars,
    get_flow_projection_max_text_chars,
    get_flow_projection_max_rows,
    get_formatter_preview_max_depth,
)


FormatterSaveCallback = Callable[
    [str, FlowOutputProjectionResult, str, str],
    Awaitable[Mapping[str, Any]],
]

_SUPPORTED_FILE_FORMATS = {"csv", "tsv", "json"}
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


def _bounded_row(row: Mapping[str, Any]) -> dict[str, Any]:
    bounded: dict[str, Any] = {}
    for key, value in row.items():
        bounded[str(key)] = _bounded_value(value)
        encoded = json.dumps(bounded, ensure_ascii=False, default=str)
        if len(encoded) > _MAX_ROW_CHARS:
            bounded["_truncated_preview"] = True
            bounded["_truncated_after_field"] = str(key)
            break
    return bounded


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
    for value in transform.values:
        if isinstance(value, Mapping) and isinstance(value.get("field_ref"), str):
            refs.append(str(value["field_ref"]))
        elif isinstance(value, str) and "." in value:
            refs.append(value)
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
    for index, value in enumerate(transform.values, start=1):
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
        source_ref_count += len(_source_refs_for_column(column))
        if column.transform is not None:
            errors.extend(
                _transform_literal_payload_errors(
                    column.transform,
                    context=f"Column '{column.key}' transform",
                )
            )
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
            raw_transform = raw_column.get("transform")
            if isinstance(raw_transform, Mapping):
                errors.extend(
                    _reject_extra_keys(
                        raw_transform,
                        model=FlowOutputTransformSpec,
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
    return {
        "format": result.format,
        "row_source": result.row_source,
        "columns": [
            column.model_dump(mode="json")
            for column in result.columns
        ],
        "total_count": result.total_count,
        "row_count": len(result.rows),
        "truncated": result.truncated,
        "group_by": list(result.group_by),
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
) -> tuple[list[Mapping[str, Any]], dict[str, list[str]]]:
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
    if not str(source_ref or "").strip():
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
        source_ref=source_ref,
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
        **source_update,
    )


def _validate_plan_payload(
    bundle: FlowOutputArtifactBundle,
    plan: FlowOutputProjectionPlan,
) -> dict[str, Any]:
    errors, warnings, columns = validate_projection_plan(bundle, plan)
    if not errors:
        errors.extend(_formatter_plan_constraint_errors(plan, columns))
    status = "invalid" if errors else "ok"
    return {
        "status": status,
        "errors": errors,
        "warnings": [
            _bounded_text(warning)
            for warning in warnings[:_MAX_LIST_ITEMS]
        ],
        "columns": [
            column.model_dump(mode="json")
            for column in columns
        ],
        "plan": plan.model_dump(mode="json"),
    }


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
            "File bytes are generated only from validated projections over the "
            "saved artifact bundle. These tools do not accept raw row arrays, "
            "CSV/TSV/JSON text, or model-composed replacement data."
        ),
        "allowed_row_sources": list(get_args(FlowOutputRowSource)),
        "allowed_row_strategies": list(get_args(FlowOutputRowStrategy)),
        "allowed_filter_operators": list(get_args(FlowOutputFilterSpec.model_fields["op"].annotation)),
        "allowed_sort_directions": list(get_args(FlowOutputSortSpec.model_fields["direction"].annotation)),
        "allowed_transform_types": list(get_args(FlowOutputTransformSpec.model_fields["type"].annotation)),
        "json_shapes": list(get_args(FlowOutputJsonShape)),
        "format_rules": {
            "csv": "Flat row export. group_by is not supported; use sort/filter/columns/transforms.",
            "tsv": (
                "Flat curation table export from canonical object rows. "
                "Artifact-summary TSV exports and model-written rows are rejected."
            ),
            "json": "Structured export. Supports rows, grouped, and bundle json_shape values.",
        }[output_format],
        "source_refs": source_refs,
        "default_row_source": bundle.default_row_source,
        "projection_limits": {
            "max_projection_rows": _MAX_PROJECTION_ROWS,
            "default_preview_rows": _DEFAULT_PREVIEW_LIMIT,
            "default_inspection_rows": _MAX_CHAT_ROWS,
        },
    }


def build_output_formatter_tools(
    *,
    bundle: FlowOutputArtifactBundle,
    output_format: str,
    formatter_agent_id: str,
    save_projected_output: FormatterSaveCallback,
) -> list[Any]:
    """Build runtime-bound CSV/TSV/JSON formatter tools over a saved artifact bundle."""

    normalized_format = str(output_format or "").strip().lower()
    if normalized_format not in _SUPPORTED_FILE_FORMATS:
        supported = ", ".join(sorted(_SUPPORTED_FILE_FORMATS))
        raise ValueError(f"output_format must be one of: {supported}.")
    resolved_output_format = cast(FlowOutputFormat, normalized_format)
    saver = save_projected_output

    @function_tool(
        name_override="explain_formatter_capabilities",
        description_override=(
            "Return the structure-owned CSV/TSV/JSON formatter capabilities, "
            "constraints, row sources, transforms, and no-raw-rows invariant."
        ),
        strict_mode=False,
    )
    async def _explain_formatter_capabilities() -> str:
        return _tool_json(
            _capabilities_payload(
                output_format=resolved_output_format,
                formatter_agent_id=formatter_agent_id,
                bundle=bundle,
            )
        )

    @function_tool(
        name_override="inspect_output_artifacts",
        description_override=(
            "Inspect bounded row-source counts, default columns, field refs, "
            "source ids/keys, examples, and warnings from the saved bundle."
        ),
        strict_mode=False,
    )
    async def _inspect_output_artifacts(example_limit: int | None = None) -> str:
        limit = _positive_limit(
            example_limit,
            default=_MAX_FIELD_EXAMPLES,
            ceiling=_MAX_LIST_ITEMS,
        )
        inventory = inspect_projection_artifacts(bundle, example_limit=limit)
        inventory["source_refs"] = _available_source_refs(bundle)
        inventory["generic_source_summary"] = _generic_source_summary(bundle)
        return _tool_json({"status": "ok", "inventory": inventory})

    @function_tool(
        name_override="inspect_output_rows",
        description_override=(
            "Inspect bounded saved rows or selected field refs after optional "
            "projection-style filters and sorts. Inputs are field refs and plan "
            "metadata only, never row contents."
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
            result = apply_projection_plan(bundle, plan)
            rows = result.rows[offset : offset + page_size]
            next_offset = offset + len(rows)
            return _tool_json(
                {
                    "status": "ok",
                    "row_source": result.row_source,
                    "columns": [
                        column.model_dump(mode="json")
                        for column in result.columns
                    ],
                    "rows": [_bounded_row(row) for row in rows],
                    "total_count": result.total_count,
                    "cursor": str(offset),
                    "next_cursor": str(next_offset)
                    if next_offset < result.total_count
                    else "",
                    "truncated": next_offset < result.total_count or result.truncated,
                    "warnings": result.warnings,
                }
            )
        except Exception as exc:
            return _tool_json({"status": "invalid", "errors": [str(exc)]})

    @function_tool(
        name_override="inspect_field_values",
        description_override=(
            "Inspect distinct saved values and counts for one field ref after "
            "optional projection-style filters. Does not accept replacement values."
        ),
        strict_mode=False,
    )
    async def _inspect_field_values(
        row_source: str,
        field_ref: str,
        filters_json: str = "",
        limit: int | None = None,
    ) -> str:
        try:
            selected_row_source = _coerce_row_source(row_source, bundle.default_row_source)
            selected_field_ref = str(field_ref or "").strip()
            if selected_field_ref not in bundle.field_refs_for_source(selected_row_source):
                raise ValueError(f"Unknown field ref '{selected_field_ref}'.")
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
            result = apply_projection_plan(bundle, plan)
            counts: Counter[str] = Counter()
            examples: dict[str, Any] = {}
            for row in result.rows:
                value = row.get("value")
                encoded = json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, default=str)
                counts[encoded] += 1
                examples.setdefault(encoded, value)
            value_limit = _positive_limit(
                limit,
                default=_MAX_LIST_ITEMS,
                ceiling=_MAX_PROJECTION_ROWS,
            )
            values = [
                {
                    "value": _bounded_value(examples[encoded]),
                    "count": count,
                }
                for encoded, count in counts.most_common(value_limit)
            ]
            return _tool_json(
                {
                    "status": "ok",
                    "row_source": selected_row_source,
                    "field_ref": selected_field_ref,
                    "total_rows": result.total_count,
                    "distinct_count": len(counts),
                    "values": values,
                    "values_truncated": len(counts) > len(values),
                    "warnings": result.warnings,
                }
            )
        except Exception as exc:
            return _tool_json({"status": "invalid", "errors": [str(exc)]})

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
    ) -> str:
        try:
            selected_row_source = _coerce_row_source(row_source, bundle.default_row_source)
            selected_row_strategy = _coerce_row_strategy(row_strategy)
            plan = _default_projection_plan_for_formatter(
                bundle,
                output_format=resolved_output_format,
                row_source=selected_row_source,
                row_strategy=selected_row_strategy,
                source_ref=source_ref,
            )
            return _tool_json(_validate_plan_payload(bundle, plan))
        except Exception as exc:
            return _tool_json({"status": "invalid", "errors": [str(exc)]})

    @function_tool(
        name_override="validate_output_projection",
        description_override=(
            "Validate a projection plan over saved bundle fields. The format is "
            "forced to this formatter's file type. Extra raw-content keys are rejected."
        ),
        strict_mode=False,
    )
    async def _validate_output_projection(plan_json: str) -> str:
        try:
            plan = _projection_plan_from_tool_payload(
                plan_json,
                output_format=resolved_output_format,
            )
            return _tool_json(_validate_plan_payload(bundle, plan))
        except Exception as exc:
            return _tool_json({"status": "invalid", "errors": [str(exc)]})

    @function_tool(
        name_override="preview_output_projection",
        description_override=(
            "Validate and preview a projection plan over saved bundle rows. "
            "Accepts plan JSON only, never replacement row contents."
        ),
        strict_mode=False,
    )
    async def _preview_output_projection(plan_json: str, limit: int | None = None) -> str:
        try:
            plan = _projection_plan_from_tool_payload(
                plan_json,
                output_format=resolved_output_format,
            )
            errors, warnings, columns = validate_projection_plan(bundle, plan)
            if not errors:
                errors.extend(_formatter_plan_constraint_errors(plan, columns))
            if errors:
                return _tool_json(
                    {
                        "status": "invalid",
                        "preview": {
                            "status": "invalid",
                            "errors": errors,
                            "warnings": warnings,
                        },
                    }
                )
            preview_limit = _positive_limit(
                limit,
                default=_DEFAULT_PREVIEW_LIMIT,
                ceiling=_MAX_PROJECTION_ROWS,
            )
            preview = preview_projection(bundle, plan, limit=preview_limit)
            return _tool_json(
                {
                    "status": preview.status,
                    "preview": preview.model_dump(mode="json"),
                }
            )
        except Exception as exc:
            return _tool_json({"status": "invalid", "errors": [str(exc)]})

    @function_tool(
        name_override="finalize_and_save",
        description_override=(
            "Finalize a projection over saved bundle rows and save one CSV/TSV/JSON file. "
            "Empty plan_json uses the validated default projection. This tool never "
            "accepts raw rows or file text."
        ),
        strict_mode=False,
    )
    async def _finalize_and_save(plan_json: str = "", filename_hint: str = "") -> str:
        try:
            if str(plan_json or "").strip():
                plan = _projection_plan_from_tool_payload(
                    plan_json,
                    output_format=resolved_output_format,
                )
            else:
                plan = default_projection_plan(bundle, output_format=resolved_output_format)
            errors, warnings, columns = validate_projection_plan(bundle, plan)
            if not errors:
                errors.extend(_formatter_plan_constraint_errors(plan, columns))
            if errors:
                return _tool_json(
                    {
                        "status": "invalid",
                        "errors": errors,
                        "warnings": warnings,
                    }
                )
            if not bundle.rows_for_source(plan.row_source) and projection_plan_allows_empty_bundle(plan):
                return _tool_json(
                    {
                        "status": "invalid",
                        "errors": [
                            "Formatter tools cannot save literal-only files without saved source rows."
                        ],
                    }
                )
            projection = finalize_output_projection(bundle, plan)
            if projection.total_count < 1:
                return _tool_json(
                    {
                        "status": "invalid",
                        "errors": [
                            "Projection matched no saved rows; call formatter_cannot_complete "
                            "or inspect the saved bundle before trying again."
                        ],
                        "projection_summary": _projection_summary(projection),
                    }
                )
            descriptor = str(filename_hint or "").strip() or f"{bundle.flow_name}_{resolved_output_format}_export"
            file_info = dict(
                await saver(
                    resolved_output_format,
                    projection,
                    descriptor,
                    formatter_agent_id,
                )
            )
            file_info.setdefault("format", resolved_output_format)
            file_info["status"] = "ok"
            file_info["projection_summary"] = _projection_summary(projection)
            return _tool_json(file_info)
        except Exception as exc:
            return _tool_json({"status": "invalid", "errors": [str(exc)]})

    @function_tool(
        name_override="formatter_cannot_complete",
        description_override=(
            "Return a structured cannot-complete result when the saved bundle "
            "cannot support the requested file. This does not save a file."
        ),
        strict_mode=False,
    )
    async def _formatter_cannot_complete(
        reason: str,
        missing_data: str = "",
        suggested_next_step: str = "",
    ) -> str:
        return _tool_json(
            {
                "status": "cannot_complete",
                "format": resolved_output_format,
                "formatter_agent_id": formatter_agent_id,
                "reason": _bounded_text(reason),
                "missing_data": _bounded_text(missing_data),
                "suggested_next_step": _bounded_text(suggested_next_step),
                "saved_file": False,
            }
        )

    return [
        _explain_formatter_capabilities,
        _inspect_output_artifacts,
        _inspect_output_rows,
        _inspect_field_values,
        _build_default_projection_plan,
        _validate_output_projection,
        _preview_output_projection,
        _finalize_and_save,
        _formatter_cannot_complete,
    ]


__all__ = [
    "FormatterSaveCallback",
    "build_output_formatter_tools",
]
