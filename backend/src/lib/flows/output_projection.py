"""Deterministic projection of flow artifacts into terminal outputs."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import json
import math
import re
from typing import Any, Callable, Literal, Mapping, Protocol, Sequence

from pydantic import BaseModel, Field, model_validator

from src.lib.config.agent_loader import get_agent_definition_for_package
from src.lib.curation_workspace.domain_envelope_normalization import (
    domain_envelope_from_extraction_result,
    is_canonical_domain_envelope_payload,
)
from src.lib.openai_agents.config import (
    get_flow_output_split_list_max_columns,
    get_flow_output_projection_preview_max_depth,
    get_flow_projection_max_field_examples,
    get_flow_projection_max_list_items,
    get_flow_projection_max_object_items,
    get_flow_projection_max_row_chars,
    get_flow_projection_max_text_chars,
    get_flow_projection_max_rows,
)
from src.schemas.curation_workspace import CurationExtractionResultRecord
from src.schemas.domain_validator import ValidatorOutputProjection
from src.schemas.agent_execution_revision import AgentExecutionReceipt
from src.lib.agent_studio.profile_conformance import ProfileIdentityError, ResolvedGenericProfile
from src.lib.flows.profile_projection import ProfileProjectionField, profile_projection_fields
from src.lib.domain_packs.resolvable_values import holds_resolution, without_overruled
from src.lib.flows.value_display import (
    LIST_SEPARATOR,
    display_text,
    path_tokens,
    relative_finding_path,
)
from src.lib.curation_workspace.execution_contracts import require_resolved_profile_conformance

ProfileResolver = Callable[[AgentExecutionReceipt], ResolvedGenericProfile | None]


FlowOutputFormat = Literal["csv", "tsv", "json", "chat"]
FlowOutputRowSource = Literal["artifact", "object", "evidence", "validation_finding"]
FlowOutputRowStrategy = Literal["object", "object_ledger", "wide_union"]
FlowOutputJsonShape = Literal["rows", "grouped", "bundle"]
FlowOutputChatLayout = Literal["table", "sections", "bullets"]
FlowOutputTransformType = Literal[
    "literal",
    "concat",
    "join_list",
    "pair_join",
    "count",
    "map_value",
    "boolean_label",
    "format_elements",
]
FlowOutputFilterOperator = Literal[
    "eq",
    "ne",
    "in",
    "contains",
    "is_empty",
    "is_not_empty",
    "gt",
    "gte",
    "lt",
    "lte",
]
FlowOutputSortDirection = Literal["asc", "desc"]

# Env-configurable (defaults unchanged); see config.py getters and .env.example
# (flow projection tooling group).
MAX_PROJECTION_ROWS = get_flow_projection_max_rows()
MAX_FIELD_EXAMPLES = get_flow_projection_max_field_examples()
MAX_PROJECTION_TEXT_CHARS = get_flow_projection_max_text_chars()
MAX_PROJECTION_LIST_ITEMS = get_flow_projection_max_list_items()
MAX_PROJECTION_OBJECT_ITEMS = get_flow_projection_max_object_items()
MAX_PROJECTION_ROW_CHARS = get_flow_projection_max_row_chars()

ARTIFACT_DEFAULT_FIELD_REFS = [
    "artifact.step",
    "artifact.agent_id",
    "artifact.agent_name",
    "artifact.adapter_key",
    "artifact.source_key",
    "envelope.domain_pack_id",
    "envelope.envelope_id",
    "artifact.object_count",
    "artifact.candidate_count",
    "artifact.artifact_preview",
]

OBJECT_DEFAULT_FIELD_PRIORITY = [
    "artifact.adapter_key",
    "object.object_type",
    "object.status",
    "object.payload.symbol",
    "object.payload.name",
    "object.payload.label",
    "object.payload.primary_external_id",
    "object.payload.external_id",
    "object.payload.id",
    "object.evidence_count",
    "object.validation_status",
]

OBJECT_LEDGER_FIELD_PRIORITY = [
    "artifact.extraction_result_id",
    "artifact.source_key",
    "artifact.adapter_key",
    "envelope.domain_pack_id",
    "object.object_type",
    "object.object_id",
    "object.pending_ref_id",
    "object.payload.class_key",
    "object.label",
    "object.evidence_record_ids",
    "object.validation_status",
]

EVIDENCE_DEFAULT_FIELD_PRIORITY = [
    "artifact.adapter_key",
    "object.object_type",
    "object.object_id",
    "evidence.evidence_record_id",
    "evidence.quote",
    "evidence.verified_quote",
    "evidence.source",
    "evidence.page",
    "evidence.field_path",
]

VALIDATION_DEFAULT_FIELD_PRIORITY = [
    "artifact.adapter_key",
    "object.object_type",
    "object.object_id",
    "validation.finding_id",
    "validation.status",
    "validation.severity",
    "validation.message",
    "validation.field_path",
]

_FIELD_LABEL_OVERRIDES = {
    "artifact.step": "Step",
    "artifact.agent_id": "Agent ID",
    "artifact.agent_name": "Agent",
    "artifact.adapter_key": "Adapter",
    "artifact.source_key": "Source Key",
    "artifact.is_canonical_curation_data": "Canonical Curation Data",
    "artifact.object_count": "Object Count",
    "artifact.evidence_count": "Evidence Count",
    "artifact.candidate_count": "Candidate Count",
    "artifact.artifact_preview": "Artifact Preview",
    "envelope.domain_pack_id": "Domain Pack",
    "envelope.envelope_id": "Envelope ID",
    "object.object_type": "Object Type",
    "object.object_id": "Object ID",
    "object.label": "Label",
    "object.pending_ref_id": "Pending Ref ID",
    "object.status": "Status",
    "object.evidence_count": "Evidence Count",
    "object.evidence_record_ids": "Evidence IDs",
    "object.validation_status": "Validation Status",
    "evidence.evidence_record_id": "Evidence ID",
    "evidence.quote": "Quote",
    "evidence.verified_quote": "Verified Quote",
    "evidence.source": "Evidence Source",
    "validation.finding_id": "Finding ID",
    "validation.status": "Validation Status",
    "validation.severity": "Severity",
    "validation.message": "Message",
}

_ARTIFACT_KEY_BY_REF = {
    "artifact.step": "step",
    "artifact.agent_id": "agent_id",
    "artifact.agent_name": "agent_name",
    "artifact.adapter_key": "adapter_key",
    "artifact.source_key": "source_key",
    "envelope.domain_pack_id": "domain_pack_id",
    "envelope.envelope_id": "envelope_id",
    "artifact.object_count": "object_count",
    "artifact.evidence_count": "evidence_count",
    "artifact.candidate_count": "candidate_count",
    "artifact.artifact_preview": "artifact_preview",
}

_EVIDENCE_RECORD_KEYS = {
    "evidence_record_id",
    "id",
    "verified_quote",
    "quote",
    "evidence_quote",
    "source_quote",
    "source",
    "source_chunk_id",
    "chunk_id",
    "source_section",
    "section",
    "page",
    "page_number",
}

_VALIDATION_RECORD_KEYS = {
    "finding_id",
    "severity",
    "message",
    "field_path",
    "field_key",
    "validator",
    "binding_id",
}

_ORDERED_FILTER_OPS = {"gt", "gte", "lt", "lte"}
_NO_CANONICAL_OBJECT_LIST_WARNING = (
    "No canonical curation object list was found for this artifact."
)
_STRUCTURED_VALIDATOR_REQUIRED_FIELDS = frozenset(
    {
        "status",
        "request_id",
        "validator_binding_id",
        "validator_agent",
        "target",
        "resolved_values",
        "resolved_objects",
        "missing_expected_fields",
        "candidates",
        "lookup_attempts",
        "explanation",
    }
)
_REJECTED_LEGACY_RESULT_LIST_FIELDS = frozenset(
    {"items", "objects", "curatable_objects"}
)
_OBJECT_ATTRIBUTE_FIELD_PREFIX = "object.attribute."
_ATTRIBUTE_KEY_PATTERN = re.compile(r"[^0-9a-zA-Z]+")


class FlowOutputTransformSpec(BaseModel):
    type: FlowOutputTransformType
    field_ref: str | None = None
    field_refs: list[str] = Field(default_factory=list)
    value: Any = None
    values: list[Any] = Field(default_factory=list)
    separator: str = ""
    pair_separator: str = ":"
    mapping: dict[str, Any] = Field(default_factory=dict)
    default: Any = None
    true_label: str = "Yes"
    false_label: str = "No"
    unknown_label: str = ""


class FlowOutputOverrideSpec(BaseModel):
    """Explicit curator-directed change to one derived output row or cell.

    ``row_ref`` is a runtime row reference (``<row_source>#<n>``) resolved
    against the bound bundle; it never carries data. Overrides change only the
    derived output, never the saved results.
    """

    row_ref: str
    column_key: str | None = None
    value: Any = None
    exclude: bool = False


def _split_items(value: Any) -> list[Any]:
    """Items a split column spreads: a list's items, or one non-empty single value."""

    if isinstance(value, list):
        return value
    return [] if _is_empty(value) else [value]


class FlowOutputSplitListSpec(BaseModel):
    """Expand one field into one column per item (never extra rows).

    A single non-empty value counts as a one-item list.

    Headers come from ``header_template`` (must contain ``{n}``; default
    "<header> {n}") or explicit ``headers``, not both.
    """

    max_columns: int | None = None
    header_template: str | None = None
    headers: list[str] = Field(default_factory=list)


class FlowOutputColumnSpec(BaseModel):
    key: str
    header: str | None = None
    field_ref: str | None = None
    transform: FlowOutputTransformSpec | None = None
    source_node_id: str | None = None
    split_list: FlowOutputSplitListSpec | None = None


class FlowOutputFilterSpec(BaseModel):
    field_ref: str
    op: FlowOutputFilterOperator
    value: Any = None
    values: list[Any] = Field(default_factory=list)


class FlowOutputSortSpec(BaseModel):
    field_ref: str
    direction: FlowOutputSortDirection = "asc"


class FlowOutputSelectedSource(BaseModel):
    node_id: str
    schema_fingerprint: str


class FlowOutputProjectionPlan(BaseModel):
    format: FlowOutputFormat
    row_source: FlowOutputRowSource
    columns: list[FlowOutputColumnSpec] = Field(default_factory=list)
    filters: list[FlowOutputFilterSpec] = Field(default_factory=list)
    sort: list[FlowOutputSortSpec] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    json_shape: FlowOutputJsonShape = "rows"
    chat_layout: FlowOutputChatLayout = "table"
    row_strategy: FlowOutputRowStrategy = "object"
    source_extraction_result_ids: list[str] = Field(default_factory=list)
    source_keys: list[str] = Field(default_factory=list)
    missing_value: str | None = ""
    max_rows: int | None = None
    selection_mode: Literal["guided", "selected_fields"] = "guided"
    selected_sources: list[FlowOutputSelectedSource] = Field(default_factory=list)
    overrides: list[FlowOutputOverrideSpec] = Field(default_factory=list)


class FlowOutputProfileBinding(BaseModel):
    source_key: str
    execution_receipt: AgentExecutionReceipt
    profile_path: str
    schema_kind: str
    array_depth: int
    required: bool
    nullable: bool
    enum_values: list[str] = Field(default_factory=list)


class FlowOutputField(BaseModel):
    ref: str
    label: str
    value_type: str
    row_source: FlowOutputRowSource
    non_empty_count: int = 0
    examples: list[Any] = Field(default_factory=list)
    profile_bindings: list[FlowOutputProfileBinding] = Field(default_factory=list)
    # Declared display spec (see value_display); None uses the generic reading.
    display: dict[str, Any] | None = None


class FlowOutputArtifact(BaseModel):
    node_id: str = ""
    export_schema_fingerprint: str = ""
    execution_receipt: AgentExecutionReceipt | None = None
    declared_fields: list[FlowOutputField] = Field(default_factory=list)
    step: int | None = None
    agent_id: str = ""
    agent_name: str = ""
    adapter_key: str = ""
    source_key: str = ""
    is_canonical_curation_data: bool = False
    extraction_result_id: str | None = None
    envelope_id: str = ""
    domain_pack_id: str = ""
    object_count: int = 0
    evidence_count: int = 0
    candidate_count: int = 0
    artifact_preview: str = ""
    artifact_shape: Literal[
        "domain_envelope",
        "domain_envelope_extraction",
        "structured_result",
        "non_structured",
    ] = "non_structured"
    warnings: list[str] = Field(default_factory=list)
    rows_by_source: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    # Standard object columns from the source's declared layout, in order.
    default_object_refs: list[str] = Field(default_factory=list)
    # Object types a default plan lists as rows (the pack's curatable units);
    # empty lists every object. Supporting objects stay selectable on request.
    default_object_types: list[str] = Field(default_factory=list)
    # Per object key ("object_id:<id>" / "pending_ref_id:<id>"): each declared
    # object_ref field path and the keys of the objects it references, in the
    # object's object_refs order, so a cell carries its referenced objects'
    # open findings.
    object_ref_links: dict[str, dict[str, list[str]]] = Field(default_factory=dict)


class FlowOutputArtifactBundle(BaseModel):
    flow_name: str
    flow_run_id: str | None = None
    document_id: str | None = None
    artifacts: list[FlowOutputArtifact] = Field(default_factory=list)
    field_catalog: list[FlowOutputField] = Field(default_factory=list)
    default_row_source: FlowOutputRowSource = "artifact"
    default_source_extraction_result_id: str | None = None
    warnings: list[str] = Field(default_factory=list)

    def rows_for_source(self, row_source: FlowOutputRowSource) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for artifact in self.artifacts:
            rows.extend(artifact.rows_by_source.get(row_source) or [])
        return rows

    def field_refs_for_source(self, row_source: FlowOutputRowSource) -> set[str]:
        return {field.ref for field in self.field_catalog if field.row_source == row_source}


class FlowOutputProjectionResult(BaseModel):
    format: FlowOutputFormat
    row_source: FlowOutputRowSource
    columns: list[FlowOutputColumnSpec]
    rows: list[dict[str, Any]]
    total_count: int
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)
    json_data: Any = None
    chat_output: str | None = None
    group_by: list[str] = Field(default_factory=list)
    row_refs: list[str] = Field(default_factory=list)
    limited_by_max_rows: bool = False
    overrides_applied: int = 0
    rows_excluded: int = 0


class FlowOutputProjectionPreview(BaseModel):
    status: Literal["ok", "invalid"]
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    preview_rows: list[dict[str, Any]] = Field(default_factory=list)
    total_count: int = 0
    truncated: bool = False


def _string_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(_jsonable(value), ensure_ascii=False, default=str)


def _compact_text(value: Any, *, max_chars: int = 800) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "..."


def _bounded_projection_text(value: Any, *, max_chars: int = MAX_PROJECTION_TEXT_CHARS) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    overflow = len(text) - max_chars
    return f"{text[:max_chars].rstrip()}... [truncated {overflow} chars]"


def _bounded_projection_value(
    value: Any,
    *,
    depth: int = 0,
    max_depth: int | None = None,
) -> Any:
    if max_depth is None:
        max_depth = get_flow_output_projection_preview_max_depth()
    if isinstance(value, str):
        return _bounded_projection_text(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if depth >= max_depth:
        return "[truncated:depth]"
    if isinstance(value, Mapping):
        items = list(value.items())
        bounded = {
            str(key): _bounded_projection_value(
                item,
                depth=depth + 1,
                max_depth=max_depth,
            )
            for key, item in items[:MAX_PROJECTION_OBJECT_ITEMS]
        }
        if len(items) > MAX_PROJECTION_OBJECT_ITEMS:
            bounded["_truncated_keys"] = len(items) - MAX_PROJECTION_OBJECT_ITEMS
        return bounded
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = list(value)
        bounded_items = [
            _bounded_projection_value(
                item,
                depth=depth + 1,
                max_depth=max_depth,
            )
            for item in items[:MAX_PROJECTION_LIST_ITEMS]
        ]
        if len(items) > MAX_PROJECTION_LIST_ITEMS:
            bounded_items.append({"_truncated_items": len(items) - MAX_PROJECTION_LIST_ITEMS})
        return bounded_items
    return _bounded_projection_text(value)


def _bounded_projection_row(row: Mapping[str, Any]) -> dict[str, Any]:
    bounded: dict[str, Any] = {}
    max_depth = get_flow_output_projection_preview_max_depth()
    for key, value in row.items():
        bounded[str(key)] = _bounded_projection_value(value, max_depth=max_depth)
        encoded = json.dumps(bounded, ensure_ascii=False, default=str)
        if len(encoded) > MAX_PROJECTION_ROW_CHARS:
            bounded["_truncated_preview"] = True
            bounded["_truncated_after_field"] = str(key)
            break
    return bounded


def _bounded_projection_warnings(warnings: Sequence[str], *, limit: int = 20) -> list[str]:
    bounded = [_bounded_projection_text(warning) for warning in warnings[:limit]]
    if len(warnings) > limit:
        bounded.append(f"... [truncated {len(warnings) - limit} warnings]")
    return bounded


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonable(item) for item in value]
    return value


def _value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "list"
    if isinstance(value, Mapping):
        return "object"
    if value is None:
        return "null"
    return "string"


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _scalar_payload_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    # An identity a validator overruled is informational only, never exported.
    return {
        str(key): value
        for key, value in without_overruled(payload).items()
        if isinstance(value, (str, int, float, bool)) or value is None
    }


def _normalize_attribute_key(key: Any) -> str:
    normalized = _ATTRIBUTE_KEY_PATTERN.sub("_", str(key or "").strip().lower())
    return re.sub(r"_+", "_", normalized).strip("_")


def _is_scalar_attribute_value(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def _scalar_attribute_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    attributes = payload.get("attributes")
    if not isinstance(attributes, Mapping):
        return {}
    fields: dict[str, Any] = {}
    for key, value in without_overruled(attributes).items():
        normalized_key = _normalize_attribute_key(key)
        if not normalized_key:
            continue
        # Resolvable values (ALL-1283) are selectable too; they read "label (ID)"
        # or UNRESOLVED like any other structured value.
        if _is_scalar_attribute_value(value) or holds_resolution(value) or (
            isinstance(value, list)
            and (
                all(_is_scalar_attribute_value(item) for item in value)
                or all(holds_resolution(item) for item in value)
            )
        ):
            fields.setdefault(normalized_key, value)
    return fields


def _field_label(field_ref: str) -> str:
    if field_ref.startswith(_OBJECT_ATTRIBUTE_FIELD_PREFIX):
        return field_ref.removeprefix(_OBJECT_ATTRIBUTE_FIELD_PREFIX).replace("_", " ").title()
    if field_ref in _FIELD_LABEL_OVERRIDES:
        return _FIELD_LABEL_OVERRIDES[field_ref]
    suffix = field_ref.split(".")[-1]
    return suffix.replace("_", " ").title()


def _column_key_from_ref(field_ref: str) -> str:
    if field_ref.startswith(_OBJECT_ATTRIBUTE_FIELD_PREFIX):
        return field_ref.removeprefix(_OBJECT_ATTRIBUTE_FIELD_PREFIX)
    if field_ref in _ARTIFACT_KEY_BY_REF:
        return _ARTIFACT_KEY_BY_REF[field_ref]
    return field_ref.replace(".", "_").replace("[", "_").replace("]", "")


def _artifact_row(
    *,
    step_number: int | None,
    agent_id: str,
    agent_name: str,
    adapter_key: str,
    source_key: str,
    is_canonical_curation_data: bool,
    domain_pack_id: str,
    envelope_id: str,
    object_count: int,
    evidence_count: int,
    candidate_count: int,
    artifact_preview: str,
    extraction_result_id: str | None,
) -> dict[str, Any]:
    return {
        "artifact.step": step_number,
        "artifact.agent_id": agent_id,
        "artifact.agent_name": agent_name,
        "artifact.adapter_key": adapter_key,
        "artifact.source_key": source_key,
        "artifact.is_canonical_curation_data": is_canonical_curation_data,
        "artifact.object_count": object_count,
        "artifact.evidence_count": evidence_count,
        "artifact.candidate_count": candidate_count,
        "artifact.artifact_preview": artifact_preview,
        "artifact.extraction_result_id": extraction_result_id,
        "envelope.domain_pack_id": domain_pack_id,
        "envelope.envelope_id": envelope_id,
    }


def _artifact_context(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "artifact.step",
            "artifact.agent_id",
            "artifact.agent_name",
            "artifact.adapter_key",
            "artifact.source_key",
            "artifact.is_canonical_curation_data",
            "artifact.extraction_result_id",
            "envelope.domain_pack_id",
            "envelope.envelope_id",
        )
    }


def _coerce_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def _object_id_from_item(
    item: Mapping[str, Any],
    index: int,
    *,
    identity_fields: Sequence[str] = (),
) -> str:
    keys = identity_fields or (
        "object_id",
        "id",
        "curie",
        "primary_external_id",
        "external_id",
        "pending_ref_id",
    )
    for key in dict.fromkeys(keys):
        value = _string_value(item.get(key))
        if value:
            return value
    if identity_fields:
        raise ValueError(
            "Structured result row does not provide any declared identity field: "
            f"{', '.join(identity_fields)}"
        )
    return str(index)


def _object_payload(item: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = item.get("payload")
    if isinstance(payload, Mapping):
        return payload
    return {
        key: value
        for key, value in item.items()
        if key
        not in {
            "object_type",
            "object_id",
            "id",
            "pending_ref_id",
            "status",
            "payload",
            "evidence",
            "evidence_records",
            "evidence_anchors",
            "validation_findings",
            "validation",
            "validation_summary",
        }
    }


def _object_validation_status(item: Mapping[str, Any]) -> str:
    for key in ("validation_status", "status"):
        value = _string_value(item.get(key))
        if value and key == "validation_status":
            return value
    validation = item.get("validation") or item.get("validation_summary")
    if isinstance(validation, Mapping):
        for key in ("status", "state", "severity"):
            value = _string_value(validation.get(key))
            if value:
                return value
    findings = item.get("validation_findings")
    if isinstance(findings, list) and findings:
        statuses = [
            _string_value(finding.get("status"))
            for finding in findings
            if isinstance(finding, Mapping) and finding.get("status") is not None
        ]
        return ", ".join(status for status in statuses if status)
    return ""


def _declared_label_fields_text(payload: Mapping[str, Any], label_fields: Sequence[str]) -> str:
    """Every declared label field, joined; one never stands in for another (ALL-1283)."""

    return LIST_SEPARATOR.join(
        text for key in label_fields if (text := display_text(payload.get(key)))
    )


def _object_label(
    item: Mapping[str, Any],
    payload: Mapping[str, Any],
    object_id: str,
    *,
    label_fields: Sequence[str] = (),
) -> str:
    return (
        _declared_label_fields_text(payload, label_fields) if label_fields else _declared_payload_label(item)
    ) or object_id


def _object_metadata(item: Mapping[str, Any]) -> Mapping[str, Any] | None:
    metadata = item.get("metadata")
    return metadata if isinstance(metadata, Mapping) else None


def _declared_payload_label(item: Mapping[str, Any]) -> str | None:
    """Custom and generic objects: their own payload ``label``, nothing else."""

    return _declared_path_label(item, "label")


def _declared_path_label(
    item: Mapping[str, Any], path: str | None, resolvable_fields: Mapping[str, Any] | None = None,
) -> str | None:
    """Packaged objects: the value at the pack-declared label path, nothing else.

    A label naming an unresolved value reads as its paper wording (ALL-1283);
    ``resolvable_fields`` are the pack's declared resolvable values for the
    object, so values stored before the contract get the legacy rule.
    """

    if not path:
        return None
    from src.lib.domain_packs.resolvable_values import unresolved_header_text
    from src.lib.flows.export_fields import _walk_payload
    from src.schemas.domain_envelope import parse_field_path

    payload = _object_payload(item)
    paper_wording = unresolved_header_text(
        payload, path, object_metadata=_object_metadata(item), resolvable_fields=resolvable_fields,
    )
    if paper_wording is not None:
        return paper_wording
    text = display_text(_walk_payload(payload, list(parse_field_path(path))))
    return text or None


def _object_evidence_count(item: Mapping[str, Any]) -> int:
    for key in ("evidence_record_ids", "evidence_ids", "evidence", "evidence_records", "evidence_anchors"):
        value = item.get(key)
        if isinstance(value, list):
            return len(value)
    return 0


def _object_evidence_record_ids(item: Mapping[str, Any]) -> list[str]:
    evidence_ids: list[str] = []
    seen: set[str] = set()

    def add_evidence_id(raw_value: Any) -> None:
        evidence_id = _string_value(raw_value)
        if evidence_id and evidence_id not in seen:
            seen.add(evidence_id)
            evidence_ids.append(evidence_id)

    for key in ("evidence_record_ids", "evidence_ids"):
        value = item.get(key)
        if isinstance(value, list):
            for entry in value:
                add_evidence_id(entry)
        else:
            add_evidence_id(value)

    for key in ("evidence", "evidence_records", "evidence_anchors", "evidence_items"):
        value = item.get(key)
        if not isinstance(value, list):
            continue
        for record in value:
            if isinstance(record, Mapping):
                add_evidence_id(record.get("evidence_record_id") or record.get("anchor_id") or record.get("id"))
            else:
                add_evidence_id(record)

    add_evidence_id(item.get("evidence_record_id"))
    return evidence_ids


def _object_rows_from_items(
    *,
    artifact_context: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
    identity_fields: Sequence[str] = (),
    label_fields: Sequence[str] = (),
    profile_fields: Sequence[ProfileProjectionField] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        payload = _object_payload(item)
        object_id = _object_id_from_item(
            item,
            index,
            identity_fields=identity_fields,
        )
        row: dict[str, Any] = dict(artifact_context)
        row.update(
            {
                "object.object_type": item.get("object_type") or item.get("type") or "",
                "object.object_id": object_id,
                "object.label": _object_label(
                    item,
                    payload,
                    object_id,
                    label_fields=label_fields,
                ),
                "object.pending_ref_id": item.get("pending_ref_id") or "",
                "object.status": item.get("status") or "",
                "object.evidence_count": _object_evidence_count(item),
                "object.evidence_record_ids": _object_evidence_record_ids(item),
                "object.validation_status": _object_validation_status(item),
            }
        )
        for key, value in _scalar_payload_fields(payload).items():
            row[f"object.payload.{key}"] = value
        if profile_fields is None:
            for key, value in _scalar_attribute_fields(payload).items():
                row[f"{_OBJECT_ATTRIBUTE_FIELD_PREFIX}{key}"] = value
        else:
            for field in profile_fields:
                row[field.row_ref] = field.value_from(payload.get("attributes", {}))
        rows.append(row)
    return rows


def _explicit_evidence_records(item: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    for key in ("evidence", "evidence_records", "evidence_anchors", "evidence_items"):
        value = item.get(key)
        if isinstance(value, list):
            records.extend(record for record in value if isinstance(record, Mapping))
    if any(key in item for key in _EVIDENCE_RECORD_KEYS):
        records.append(item)
    return records


def _evidence_rows_from_records(
    *,
    artifact_context: Mapping[str, Any],
    object_row: Mapping[str, Any] | None,
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    object_context = (
        {
            key: object_row.get(key)
            for key in ("object.object_type", "object.object_id", "object.pending_ref_id")
        }
        if object_row
        else {
            "object.object_type": "",
            "object.object_id": "",
            "object.pending_ref_id": "",
        }
    )
    for index, record in enumerate(records, start=1):
        row: dict[str, Any] = dict(artifact_context)
        row.update(object_context)
        row.update(
            {
                "evidence.evidence_record_id": (
                    record.get("evidence_record_id") or record.get("anchor_id") or record.get("id") or index
                ),
                "evidence.quote": record.get("quote") or record.get("evidence_quote") or record.get("source_quote") or "",
                "evidence.verified_quote": record.get("verified_quote") or "",
                "evidence.source": record.get("source") or record.get("source_section") or record.get("section") or "",
                "evidence.page": record.get("page") or record.get("page_number") or "",
                "evidence.field_path": record.get("field_path") or "",
                "evidence.chunk_id": record.get("chunk_id") or record.get("source_chunk_id") or "",
            }
        )
        for key, value in _scalar_payload_fields(record).items():
            row.setdefault(f"evidence.{key}", value)
        rows.append(row)
    return rows


def _explicit_validation_findings(item: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    value = item.get("validation_findings")
    if isinstance(value, list):
        records.extend(record for record in value if isinstance(record, Mapping))
    validation = item.get("validation") or item.get("validation_summary")
    if isinstance(validation, Mapping):
        nested = validation.get("findings") or validation.get("field_results")
        if isinstance(nested, list):
            records.extend(record for record in nested if isinstance(record, Mapping))
        elif isinstance(nested, Mapping):
            for field_key, record in nested.items():
                if isinstance(record, Mapping):
                    copy = dict(record)
                    copy.setdefault("field_path", field_key)
                    records.append(copy)
    if any(key in item for key in _VALIDATION_RECORD_KEYS):
        records.append(item)
    return records


def _validation_rows_from_records(
    *,
    artifact_context: Mapping[str, Any],
    object_row: Mapping[str, Any] | None,
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    object_context = (
        {
            key: object_row.get(key)
            for key in ("object.object_type", "object.object_id", "object.pending_ref_id")
        }
        if object_row
        else {
            "object.object_type": "",
            "object.object_id": "",
            "object.pending_ref_id": "",
        }
    )
    for index, record in enumerate(records, start=1):
        details = record.get("details")
        details = details if isinstance(details, Mapping) else {}
        result = details.get("validation_result")
        result = result if isinstance(result, Mapping) else {}
        field_ref = record.get("field_ref")
        field_ref = field_ref if isinstance(field_ref, Mapping) else {}
        target = result.get("target")
        target = target if isinstance(target, Mapping) else {}
        object_ref = field_ref.get("object_ref") or record.get("object_ref")
        object_ref = object_ref if isinstance(object_ref, Mapping) else {}
        row: dict[str, Any] = dict(artifact_context)
        row.update(object_context)
        if object_row is None:
            for key in ("object_type", "object_id", "pending_ref_id"):
                row[f"object.{key}"] = object_ref.get(key) or ""
        row.update(
            {
                "validation.finding_id": record.get("finding_id") or record.get("id") or index,
                "validation.status": record.get("status") or record.get("state") or "",
                "validation.severity": record.get("severity") or "",
                "validation.message": record.get("message") or record.get("detail") or record.get("reason") or "",
                "validation.field_path": record.get("field_path") or record.get("field_key") or field_ref.get("field_path") or target.get("field_path") or "",
                "validation.validator": record.get("validator") or record.get("binding_id") or result.get("validator_binding_id") or "",
            }
        )
        # Canonical findings keep review evidence beside their compact result.
        # Do not copy receipts or unrelated runtime context into formatter input.
        for key in ("candidate_matches", "lookup_attempts"):
            if key in details:
                row[f"validation.{key}"] = details[key]
        for key in ("request_id", "target", "candidate_count", "resolved_values", "missing_expected_fields"):
            if key in result:
                row[f"validation.{key}"] = result[key]
        for key, value in _scalar_payload_fields(record).items():
            row.setdefault(f"validation.{key}", value)
        rows.append(row)
    return rows


def _object_ref_values_for_matching(record: Mapping[str, Any]) -> set[str]:
    refs: set[str] = set()
    for key in (
        "object_ref",
        "object_id",
        "pending_ref_id",
        "target_object_id",
        "target_ref_id",
        "candidate_object_id",
    ):
        value = _string_value(record.get(key))
        if value:
            refs.add(value)
    return refs


def _object_row_ref_values(row: Mapping[str, Any]) -> set[str]:
    refs: set[str] = set()
    for key in (
        "object.object_id",
        "object.pending_ref_id",
        "object.label",
        "object.payload.primary_external_id",
        "object.payload.external_id",
        "object.payload.id",
        "object.payload.symbol",
    ):
        value = _string_value(row.get(key))
        if value:
            refs.add(value)
    return refs


def _matching_object_row_for_record(
    record: Mapping[str, Any],
    object_rows: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    field_ref = record.get("field_ref")
    nested_ref = (
        field_ref.get("object_ref") if isinstance(field_ref, Mapping) else None
    )
    explicit_ref = record.get("object_ref")
    typed_ref = nested_ref if isinstance(nested_ref, Mapping) else explicit_ref
    if isinstance(typed_ref, Mapping):
        # Canonical references are typed identities, never display labels.
        identities = {
            key: value for key in ("object_id", "pending_ref_id")
            if (value := _string_value(typed_ref.get(key)))
        }
        object_type = _string_value(typed_ref.get("object_type"))
        matches = [
            row for row in object_rows
            if identities
            and (not object_type or row.get("object.object_type") == object_type)
            and all(row.get(f"object.{key}") == value for key, value in identities.items())
        ]
        return matches[0] if len(matches) == 1 else None
    refs = _object_ref_values_for_matching(record)
    if not refs:
        return None
    matches = [row for row in object_rows if refs & _object_row_ref_values(row)]
    return matches[0] if len(matches) == 1 else None


def _evidence_rows_from_step_records(
    *,
    artifact_context: Mapping[str, Any],
    object_rows: Sequence[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    unassociated = 0
    for record in records:
        object_row = _matching_object_row_for_record(record, object_rows)
        if object_row is None:
            unassociated += 1
        rows.extend(
            _evidence_rows_from_records(
                artifact_context=artifact_context,
                object_row=object_row,
                records=[record],
            )
        )
    return rows, unassociated


def _validation_rows_from_step_records(
    *,
    artifact_context: Mapping[str, Any],
    object_rows: Sequence[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    unassociated = 0
    for record in records:
        object_row = _matching_object_row_for_record(record, object_rows)
        if object_row is None:
            unassociated += 1
        rows.extend(
            _validation_rows_from_records(
                artifact_context=artifact_context,
                object_row=object_row,
                records=[record],
            )
        )
    return rows, unassociated


def _dedupe_rows_by_ref(
    rows: Sequence[Mapping[str, Any]],
    *,
    ref: str,
) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = _string_value(row.get(ref))
        if key:
            if key in seen:
                continue
            seen.add(key)
        deduped.append(dict(row))
    return deduped


def _step_evidence_records(step: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = step.get("evidence_records")
    if not isinstance(value, list):
        return []
    return [record for record in value if isinstance(record, Mapping)]


def _step_evidence_count(
    step: Mapping[str, Any],
    *,
    evidence_rows: Sequence[Mapping[str, Any]],
) -> int:
    explicit_count = _coerce_non_negative_int(step.get("evidence_count"))
    if explicit_count is not None:
        return explicit_count
    return len(evidence_rows)


def _validation_records_from_step_metadata(step: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []

    direct_findings = step.get("validation_findings")
    if isinstance(direct_findings, list):
        records.extend(finding for finding in direct_findings if isinstance(finding, Mapping))

    validation_results = step.get("validation_group_results")
    if not isinstance(validation_results, Mapping):
        return records

    groups = validation_results.get("groups")
    if not isinstance(groups, list):
        return records

    for index, group in enumerate(groups, start=1):
        if not isinstance(group, Mapping):
            continue
        record = dict(group)
        stable_id = (
            group.get("finding_id")
            or group.get("request_id")
            or group.get("group_id")
            or group.get("validator_binding_id")
            or group.get("binding_id")
            or index
        )
        record.setdefault("finding_id", stable_id)
        record.setdefault("status", group.get("status") or group.get("outcome") or "")
        record.setdefault(
            "message",
            group.get("curator_message")
            or group.get("message")
            or group.get("error")
            or group.get("reason")
            or "",
        )
        record.setdefault(
            "severity",
            group.get("severity") or ("error" if record.get("status") == "error" else "info"),
        )
        record.setdefault(
            "validator",
            group.get("validator")
            or group.get("validator_binding_id")
            or group.get("binding_id")
            or group.get("validator_id")
            or "",
        )
        records.append(record)

    return records


def _has_mixed_canonical_and_extractor_objects(payload: Mapping[str, Any]) -> bool:
    return isinstance(payload.get("extracted_objects"), list) and isinstance(
        payload.get("curatable_objects"), list
    )


def _structured_validator_projection(
    payload: Mapping[str, Any],
) -> ValidatorOutputProjection | None:
    """Return package-owned row projection metadata for a typed validator result."""

    if not _STRUCTURED_VALIDATOR_REQUIRED_FIELDS.issubset(payload):
        return None
    if any(field in payload for field in _REJECTED_LEGACY_RESULT_LIST_FIELDS):
        return None
    if payload.get("status") not in {"resolved", "unresolved"}:
        return None
    if not isinstance(payload.get("validator_agent"), Mapping) or not isinstance(
        payload.get("target"), Mapping
    ):
        return None
    validator_agent = payload["validator_agent"]
    package_id = str(validator_agent.get("package_id") or "").strip()
    agent_id = str(validator_agent.get("agent_id") or "").strip()
    if not package_id or not agent_id:
        return None
    agent = get_agent_definition_for_package(package_id, agent_id)
    if agent is None:
        raise ValueError(
            "Unknown package-scoped validator agent "
            f"'{package_id}:{agent_id}' in typed validator result"
        )
    projection = agent.output_projection
    if projection is None:
        raise ValueError(
            f"Validator agent '{package_id}:{agent_id}' does not declare "
            "output_projection"
        )
    value = payload.get(projection.row_list_field)
    if not isinstance(value, list) or not all(
        isinstance(item, Mapping) for item in value
    ):
        raise ValueError(
            f"Typed validator result field '{projection.row_list_field}' for "
            f"'{package_id}:{agent_id}' must be a list of object rows"
        )
    return projection


def _classify_payload(
    payload: Any,
) -> tuple[
    Literal["domain_envelope", "structured_result", "non_structured"],
    ValidatorOutputProjection | None,
]:
    if isinstance(payload, Mapping):
        if _has_mixed_canonical_and_extractor_objects(payload):
            return "non_structured", None
        if is_canonical_domain_envelope_payload(payload):
            return "domain_envelope", None
        validator_projection = _structured_validator_projection(payload)
        if validator_projection is not None:
            return "structured_result", validator_projection
    return "non_structured", None


def _payload_shape(payload: Any) -> Literal[
    "domain_envelope",
    "structured_result",
    "non_structured",
]:
    return _classify_payload(payload)[0]


def _payload_object_items(
    payload: Mapping[str, Any],
    *,
    shape: str,
    validator_projection: ValidatorOutputProjection | None = None,
) -> tuple[list[Mapping[str, Any]], list[str]]:
    warnings: list[str] = []
    if shape == "domain_envelope":
        value = payload.get("extracted_objects")
    elif shape == "structured_result":
        if validator_projection is None:
            raise ValueError("Structured validator result is missing projection metadata")
        list_field = validator_projection.row_list_field
        value = payload.get(list_field)
        if isinstance(value, list):
            target = payload.get("target")
            target_object_type = (
                str(target.get("object_type") or "")
                if isinstance(target, Mapping)
                else ""
            )
            inherited_parent_fields = {
                key: payload.get(key)
                for key in validator_projection.inherited_parent_fields
                if payload.get(key) is not None
            }
            items: list[Mapping[str, Any]] = []
            for item in value:
                if not isinstance(item, Mapping):
                    continue
                normalized = dict(item)
                normalized.setdefault(
                    "object_type",
                    target_object_type or list_field,
                )
                for key, inherited_value in inherited_parent_fields.items():
                    normalized.setdefault(key, inherited_value)
                items.append(normalized)
            return items, warnings
    else:
        value = None
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)], warnings
    warnings.append(_NO_CANONICAL_OBJECT_LIST_WARNING)
    return [], warnings


def _candidate_payload_object_items(
    payload: Mapping[str, Any],
) -> tuple[
    list[Mapping[str, Any]],
    list[str],
    Literal["domain_envelope_extraction"] | None,
]:
    """Return trusted candidate object rows for extractor-shaped payloads."""
    if isinstance(payload.get("objects"), list) or isinstance(
        payload.get("extracted_objects"), list
    ):
        return [], [], None

    value = payload.get("curatable_objects")
    if not isinstance(value, list):
        return [], [], None

    return (
        [item for item in value if isinstance(item, Mapping)],
        [],
        "domain_envelope_extraction",
    )


def _payload_from_step_output(step: Mapping[str, Any]) -> Mapping[str, Any] | None:
    output = _step_attr(step, "output")
    if isinstance(output, Mapping):
        payload: Any = output
    elif isinstance(output, str):
        try:
            payload = json.loads(output)
        except (TypeError, ValueError):
            return None
    else:
        return None

    if isinstance(payload, Mapping) and _payload_shape(payload) == "non_structured":
        nested_result = payload.get("result")
        if isinstance(nested_result, Mapping):
            payload = nested_result

    if isinstance(payload, Mapping) and _payload_shape(payload) != "non_structured":
        return payload
    return None


def _step_attr(step: Mapping[str, Any], key: str, default: Any = None) -> Any:
    return step.get(key, default)


def _candidate_attr(candidate: Any, key: str, default: Any = None) -> Any:
    if isinstance(candidate, Mapping):
        return candidate.get(key, default)
    return getattr(candidate, key, default)


def _artifact_source_key(
    *,
    step: Mapping[str, Any],
    candidate: Any,
    metadata: Mapping[str, Any],
    agent_id: str,
    step_number: int | None,
) -> str:
    for value in (
        step.get("source_key"),
        metadata.get("flow_step_key"),
        metadata.get("source_key"),
    ):
        normalized = _string_value(value)
        if normalized:
            return normalized

    key_parts = [
        _string_value(metadata.get("flow_id")),
        _string_value(metadata.get("step") or step_number),
        _string_value(metadata.get("tool_name") or step.get("tool_name")),
        _string_value(_candidate_attr(candidate, "agent_key") or agent_id),
    ]
    source_key = ":".join(part for part in key_parts if part)
    if source_key:
        return source_key

    fallback_parts = [
        _string_value(step_number),
        _string_value(step.get("tool_name")),
        _string_value(agent_id),
    ]
    return ":".join(part for part in fallback_parts if part)


def _build_artifact_from_step(
    step: Mapping[str, Any],
    *,
    profile_resolver: ProfileResolver | None = None,
    packaged_sources: dict[tuple[str, str], Any] | None = None,
) -> FlowOutputArtifact | None:
    """One step's artifact; ``packaged_sources`` shares pack catalogs across a bundle."""

    candidate = _step_attr(step, "validated_candidate")
    if candidate is None:
        candidate = _step_attr(step, "candidate")
    payload = _candidate_attr(candidate, "payload_json")
    if _step_attr(step, "validated_candidate") is not None and not isinstance(payload, Mapping):
        raise ValueError("Validated flow candidate is missing its committed envelope payload")
    payload_from_candidate = payload is not None
    if payload is None:
        payload = _payload_from_step_output(step)
    if payload is None:
        return None

    raw_receipt = _candidate_attr(candidate, "execution_receipt")
    receipt = AgentExecutionReceipt.model_validate(raw_receipt) if raw_receipt is not None else None
    profile_fields = None
    if receipt is not None and receipt.output_contract.generic_profile_ref is not None:
        if profile_resolver is None:
            raise ProfileIdentityError("Projection requires the exact saved output structure resolver")
        profile = profile_resolver(receipt)
        if profile is None:
            raise ProfileIdentityError("Projection's saved output structure is unavailable")
        require_resolved_profile_conformance(profile, receipt, payload)
        profile_fields = profile_projection_fields(profile.contract)

    step_number_raw = _step_attr(step, "step")
    try:
        step_number = int(step_number_raw) if step_number_raw is not None else None
    except (TypeError, ValueError):
        step_number = None

    agent_id = _string_value(_step_attr(step, "agent_id") or _candidate_attr(candidate, "agent_key"))
    if receipt is not None and receipt.agent_key != agent_id:
        raise ProfileIdentityError("Projection producer does not match its saved execution receipt")
    agent_name = _string_value(_step_attr(step, "agent_name"))
    adapter_key = _string_value(_candidate_attr(candidate, "adapter_key") or agent_id)
    candidate_count = int(_candidate_attr(candidate, "candidate_count", 0) or 0)
    metadata = _candidate_attr(candidate, "metadata", {}) or {}
    if not isinstance(metadata, Mapping):
        metadata = {}
    extraction_result_id = _string_value(_step_attr(step, "extraction_result_id") or (metadata.get("extraction_result_id") if isinstance(metadata, Mapping) else None)) or None
    source_key = _artifact_source_key(
        step=step,
        candidate=candidate,
        metadata=metadata,
        agent_id=agent_id,
        step_number=step_number,
    )
    preview = _compact_text(_step_attr(step, "output_preview") or _step_attr(step, "output"))

    shape: Literal[
        "domain_envelope",
        "domain_envelope_extraction",
        "structured_result",
        "non_structured",
    ]
    shape, validator_projection = _classify_payload(payload)
    domain_pack_id = ""
    envelope_id = ""
    object_items: list[Mapping[str, Any]] = []
    warnings: list[str] = []
    if isinstance(payload, Mapping):
        domain_pack_id = _string_value(payload.get("domain_pack_id") or payload.get("adapter_key"))
        envelope_id = _string_value(payload.get("envelope_id"))
        if shape == "non_structured" and payload_from_candidate:
            object_items, object_warnings, trusted_candidate_shape = (
                _candidate_payload_object_items(payload)
            )
            if trusted_candidate_shape is not None:
                shape = trusted_candidate_shape
        else:
            object_items, object_warnings = _payload_object_items(
                payload,
                shape=shape,
                validator_projection=validator_projection,
            )
        if shape == "non_structured":
            object_items = []
            if not object_warnings:
                object_warnings = [_NO_CANONICAL_OBJECT_LIST_WARNING]
        warnings.extend(object_warnings)
    elif shape == "non_structured":
        warnings.append("Artifact payload is not a supported structured mapping.")

    object_count = len(object_items)
    artifact_row = _artifact_row(
        step_number=step_number,
        agent_id=agent_id,
        agent_name=agent_name,
        adapter_key=adapter_key,
        source_key=source_key,
        is_canonical_curation_data=payload_from_candidate,
        domain_pack_id=domain_pack_id,
        envelope_id=envelope_id,
        object_count=object_count,
        evidence_count=0,
        candidate_count=candidate_count,
        artifact_preview=preview,
        extraction_result_id=extraction_result_id,
    )

    rows_by_source: dict[str, list[dict[str, Any]]] = {
        "artifact": [artifact_row],
        "object": [],
        "evidence": [],
        "validation_finding": [],
    }
    artifact_context = _artifact_context(artifact_row)
    object_rows = _object_rows_from_items(
        artifact_context=artifact_context,
        items=object_items,
        identity_fields=(
            validator_projection.identity_fields if validator_projection else ()
        ),
        label_fields=(validator_projection.label_fields if validator_projection else ()),
        profile_fields=profile_fields,
    )
    rows_by_source["object"] = object_rows
    # Derive review status from findings, including older persisted envelopes
    # whose lifecycle status was promoted by one successful field lookup.
    if isinstance(payload, Mapping):
        for finding in _explicit_validation_findings(payload):
            if finding.get("status") != "open":
                continue
            target_row = _matching_object_row_for_record(finding, object_rows)
            if target_row is not None:
                target_row["object.validation_status"] = "needs_review"
                if target_row.get("object.status") == "validated":
                    target_row["object.status"] = "needs_review"
    for object_item, object_row in zip(object_items, object_rows):
        rows_by_source["evidence"].extend(
            _evidence_rows_from_records(
                artifact_context=artifact_context,
                object_row=object_row,
                records=_explicit_evidence_records(object_item),
            )
        )
        rows_by_source["validation_finding"].extend(
            _validation_rows_from_records(
                artifact_context=artifact_context,
                object_row=object_row,
                records=_explicit_validation_findings(object_item),
            )
        )
    if isinstance(payload, Mapping):
        rows_by_source["evidence"].extend(
            _evidence_rows_from_records(
                artifact_context=artifact_context,
                object_row=None,
                records=_explicit_evidence_records(payload),
            )
        )
        payload_metadata = payload.get("metadata")
        if isinstance(payload_metadata, Mapping):
            rows_by_source["evidence"].extend(
                _evidence_rows_from_records(
                    artifact_context=artifact_context,
                    object_row=None,
                    records=_explicit_evidence_records(payload_metadata),
                )
            )
        rows_by_source["validation_finding"].extend(
            _validation_rows_from_records(
                artifact_context=artifact_context,
                object_row=None,
                records=_explicit_validation_findings(payload),
            )
        )
        if isinstance(payload_metadata, Mapping):
            rows_by_source["validation_finding"].extend(
                _validation_rows_from_records(
                    artifact_context=artifact_context,
                    object_row=None,
                    records=_explicit_validation_findings(payload_metadata),
                )
            )

    step_evidence_records = _step_evidence_records(step)
    if step_evidence_records:
        step_evidence_rows, unassociated_evidence_count = _evidence_rows_from_step_records(
            artifact_context=artifact_context,
            object_rows=object_rows,
            records=step_evidence_records,
        )
        rows_by_source["evidence"].extend(
            step_evidence_rows
        )
        rows_by_source["evidence"] = _dedupe_rows_by_ref(
            rows_by_source["evidence"],
            ref="evidence.evidence_record_id",
        )
        if unassociated_evidence_count and object_rows:
            warnings.append(
                f"{unassociated_evidence_count} step-level evidence record(s) had no "
                "explicit matching object ref and were emitted with empty object refs."
            )

    step_validation_records = _validation_records_from_step_metadata(step)
    if step_validation_records:
        step_validation_rows, unassociated_validation_count = _validation_rows_from_step_records(
            artifact_context=artifact_context,
            object_rows=object_rows,
            records=step_validation_records,
        )
        rows_by_source["validation_finding"].extend(
            step_validation_rows
        )
        rows_by_source["validation_finding"] = _dedupe_rows_by_ref(
            rows_by_source["validation_finding"],
            ref="validation.finding_id",
        )
        if unassociated_validation_count and object_rows:
            warnings.append(
                f"{unassociated_validation_count} step-level validation record(s) could not "
                "be associated with a unique output object; explicit source references were retained."
            )

    artifact_row["artifact.evidence_count"] = _step_evidence_count(
        step,
        evidence_rows=rows_by_source["evidence"],
    )

    if shape == "non_structured":
        warnings.append("No canonical curation object rows are available for this artifact.")

    from src.lib.flows.export_fields import (
        PROFILE_RATIONALE_EXPORT_FIELD,
        packaged_export_source,
        packaged_field_value,
        profile_export_fields,
        source_catalog,
    )
    display_specs: dict[str, dict[str, Any]] = {}
    default_object_refs: list[str] = []
    default_object_types: list[str] = []
    object_ref_links: dict[str, dict[str, list[str]]] = {}
    # Object labels are the declared label only (Chris, Sep 22). The legacy
    # label stays on the rows until here so record matching is unchanged.
    declared_labels: list[Any] = [_declared_payload_label(item) for item in object_items]
    if profile_fields is not None:
        export_fields = profile_export_fields(profile_fields)
        # Custom profiles: top-level contract fields in declaration order, then the rationale.
        default_object_refs = [
            *(
                field.row_ref
                for field in profile_fields
                if "." not in field.profile_path.removeprefix("attributes.")
            ),
            PROFILE_RATIONALE_EXPORT_FIELD["ref"],
        ]
    else:
        # A persisted envelope declares its pack independently of whether the
        # producer was a custom agent with an execution receipt.
        pack_entry = {"curation": {"domain_pack_id": domain_pack_id}} if domain_pack_id else None
        source = packaged_export_source(
            agent_id, pack_entry, cache=packaged_sources if packaged_sources is not None else {},
        )
        export_fields = source.fields if source is not None else []
        if source is not None:
            display_specs = source.display_specs
            default_object_refs = source.default_layout(
                sorted({str(item.get("object_type") or "") for item in object_items}),
            )
            default_object_types = list(source.curatable_unit_types)
            for row, item in zip(rows_by_source["object"], object_items):
                links = _object_ref_links(
                    item, source.object_ref_fields.get(str(item.get("object_type") or ""), {}),
                )
                if links:
                    object_ref_links.update({key: links for key in _object_keys(row)})
        if domain_pack_id and domain_pack_id != "generic":
            label_paths = source.object_label_paths if source is not None else {}
            resolvable = source.resolvable_fields if source is not None else {}
            declared_labels = [
                _declared_path_label(
                    item,
                    label_paths.get(str(item.get("object_type") or "")),
                    resolvable.get(str(item.get("object_type") or "")),
                )
                for item in object_items
            ]
        for row, item in zip(rows_by_source["object"], object_items):
            effective = source.effective_item(item) if source is not None else item
            for field in export_fields:
                if "summary_key" not in field:
                    row[field["ref"]] = packaged_field_value(effective, field)
        from src.lib.flows.validation_summary_export import populate_summary_fields
        populate_summary_fields(
            rows_by_source["object"], object_items,
            _explicit_validation_findings(payload) if isinstance(payload, Mapping) else [],
            export_fields, domain_pack_id,
        )
    if shape == "structured_result":
        # Validator result rows: the output projection's declared label fields.
        label_fields = validator_projection.label_fields if validator_projection else ()
        declared_labels = [
            _declared_label_fields_text(_object_payload(item), label_fields) or None
            for item in object_items
        ]
    for row, label in zip(rows_by_source["object"], declared_labels):
        row["object.label"] = label
    catalog = source_catalog(export_fields, receipt.model_dump(mode="json") if receipt else None)
    node_id = str(step.get("node_id") or "")
    for rows in rows_by_source.values():
        for row in rows:
            row["artifact.node_id"] = node_id
    extra_declared = [FlowOutputField(ref=f["ref"], label=f["label"], value_type=f["value_type"], row_source="object",
                                      display=display_specs.get(f["ref"]))
                      for f in catalog["fields"] if not f["ref"].startswith("object.attribute.")]
    return FlowOutputArtifact(
        node_id=node_id, export_schema_fingerprint=catalog["schema_fingerprint"],
        execution_receipt=receipt,
        declared_fields=[FlowOutputField(
            ref=field.row_ref, label=field.label, value_type=field.value_type, row_source="object",
            profile_bindings=[FlowOutputProfileBinding(
                source_key=source_key, execution_receipt=receipt, profile_path=field.profile_path,
                schema_kind=field.schema_kind, array_depth=field.array_depth,
                required=field.required, nullable=field.nullable, enum_values=list(field.enum_values),
            )],
        ) for field in profile_fields or [] if receipt is not None] + extra_declared,
        step=step_number,
        agent_id=agent_id,
        agent_name=agent_name,
        adapter_key=adapter_key,
        source_key=source_key,
        is_canonical_curation_data=payload_from_candidate,
        extraction_result_id=extraction_result_id,
        envelope_id=envelope_id,
        domain_pack_id=domain_pack_id,
        object_count=object_count,
        evidence_count=artifact_row["artifact.evidence_count"],
        candidate_count=candidate_count,
        artifact_preview=preview,
        artifact_shape=shape,
        warnings=warnings,
        rows_by_source=rows_by_source,
        default_object_refs=default_object_refs,
        default_object_types=default_object_types,
        object_ref_links=object_ref_links,
    )


def _catalog_for_rows(
    row_source: FlowOutputRowSource,
    rows: Sequence[Mapping[str, Any]],
) -> list[FlowOutputField]:
    values_by_ref: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        for key, value in row.items():
            values_by_ref[str(key)].append(value)

    fields: list[FlowOutputField] = []
    for field_ref in sorted(values_by_ref):
        values = values_by_ref[field_ref]
        non_empty_values = [value for value in values if not _is_empty(value)]
        examples: list[Any] = []
        for value in non_empty_values:
            json_value = _bounded_projection_value(value)
            if json_value in examples:
                continue
            examples.append(json_value)
            if len(examples) >= MAX_FIELD_EXAMPLES:
                break
        sample = non_empty_values[0] if non_empty_values else None
        fields.append(
            FlowOutputField(
                ref=field_ref,
                label=_field_label(field_ref),
                value_type=_value_type(sample),
                row_source=row_source,
                non_empty_count=len(non_empty_values),
                examples=examples,
            )
        )
    return fields


def _default_row_source_for_bundle(
    *,
    artifacts: Sequence[FlowOutputArtifact],
    rows_by_source: Mapping[str, Sequence[Mapping[str, Any]]],
    output_format: FlowOutputFormat | None,
) -> FlowOutputRowSource:
    del artifacts
    if output_format == "tsv":
        return "object"
    if rows_by_source.get("object"):
        return "object"
    if rows_by_source.get("artifact"):
        return "artifact"
    return "artifact"


def _build_artifact_bundle(
    *,
    artifacts: Sequence[FlowOutputArtifact],
    flow_name: str,
    flow_run_id: str | None = None,
    document_id: str | None = None,
    output_format: FlowOutputFormat | None = None,
) -> FlowOutputArtifactBundle:
    rows_by_source = {
        row_source: [
            row
            for artifact in artifacts
            for row in artifact.rows_by_source.get(row_source, [])
        ]
        for row_source in ("artifact", "object", "evidence", "validation_finding")
    }
    field_catalog: list[FlowOutputField] = []
    for row_source in ("artifact", "object", "evidence", "validation_finding"):
        field_catalog.extend(
            _catalog_for_rows(row_source, rows_by_source[row_source])  # type: ignore[arg-type]
        )
    catalog_by_ref = {(field.row_source, field.ref): field for field in field_catalog}
    declared_types: dict[tuple[str, str], set[str]] = defaultdict(set)
    for artifact in artifacts:
        for declared in artifact.declared_fields:
            key = (declared.row_source, declared.ref)
            declared_types[key].add(declared.value_type)
            existing = catalog_by_ref.get(key)
            if existing is None:
                existing = declared.model_copy(deep=True)
                catalog_by_ref[key] = existing
                field_catalog.append(existing)
            else:
                existing.profile_bindings.extend(declared.profile_bindings)
                existing.label = declared.label
                if declared.display is not None:
                    existing.display = declared.display
            existing.value_type = declared.value_type if len(declared_types[key]) == 1 else "mixed"
    warnings = [
        warning
        for artifact in artifacts
        for warning in artifact.warnings
    ]
    return FlowOutputArtifactBundle(
        flow_name=flow_name,
        flow_run_id=flow_run_id,
        document_id=document_id,
        artifacts=list(artifacts),
        field_catalog=field_catalog,
        default_row_source=_default_row_source_for_bundle(
            artifacts=artifacts,
            rows_by_source=rows_by_source,
            output_format=output_format,
        ),
        warnings=warnings,
    )


def build_flow_output_artifact_bundle(
    *,
    completed_steps: Sequence[Mapping[str, Any]],
    flow_name: str,
    flow_run_id: str | None = None,
    document_id: str | None = None,
    output_format: FlowOutputFormat | None = None,
    profile_resolver: ProfileResolver | None = None,
) -> FlowOutputArtifactBundle:
    """Build the canonical projection bundle from completed flow steps."""

    packaged_sources: dict[tuple[str, str], Any] = {}
    artifacts = [
        artifact
        for step in completed_steps
        if (artifact := _build_artifact_from_step(
            step, profile_resolver=profile_resolver, packaged_sources=packaged_sources,
        )) is not None
    ]
    return _build_artifact_bundle(
        flow_name=flow_name,
        flow_run_id=flow_run_id,
        document_id=document_id,
        artifacts=artifacts,
        output_format=output_format,
    )


def _common_string_value(values: Sequence[str | None]) -> str | None:
    normalized_values = {
        value.strip()
        for value in values
        if isinstance(value, str) and value.strip()
    }
    if len(normalized_values) == 1:
        return next(iter(normalized_values))
    return None


def _step_from_extraction_result(
    extraction_result: CurationExtractionResultRecord,
    *,
    step_number: int,
) -> dict[str, Any]:
    envelope = domain_envelope_from_extraction_result(extraction_result)
    metadata = dict(extraction_result.metadata or {})
    metadata.setdefault(
        "source_key",
        f"extraction_result:{extraction_result.extraction_result_id}",
    )
    return {
        "step": step_number,
        "extraction_result_id": extraction_result.extraction_result_id,
        "agent_id": extraction_result.agent_key,
        "agent_name": extraction_result.agent_key.replace("_", " ").title(),
        "output_preview": extraction_result.conversation_summary or "",
        "candidate": {
            "execution_receipt": extraction_result.execution_receipt,
            "agent_key": extraction_result.agent_key,
            "adapter_key": extraction_result.adapter_key,
            "candidate_count": extraction_result.candidate_count,
            "metadata": metadata,
            "payload_json": envelope.model_dump(mode="json"),
        },
    }


def build_extraction_result_artifact_bundle(
    *,
    extraction_results: Sequence[CurationExtractionResultRecord],
    bundle_name: str = "Extraction Results",
    flow_run_id: str | None = None,
    document_id: str | None = None,
    output_format: FlowOutputFormat | None = None,
    profile_resolver: ProfileResolver | None = None,
) -> FlowOutputArtifactBundle:
    """Build a projection bundle directly from persisted extraction results."""

    completed_steps = [
        _step_from_extraction_result(extraction_result, step_number=index)
        for index, extraction_result in enumerate(extraction_results, start=1)
    ]
    packaged_sources: dict[tuple[str, str], Any] = {}
    artifacts = [
        artifact
        for step in completed_steps
        if (artifact := _build_artifact_from_step(
            step, profile_resolver=profile_resolver, packaged_sources=packaged_sources,
        )) is not None
    ]
    return _build_artifact_bundle(
        artifacts=artifacts,
        flow_name=bundle_name,
        flow_run_id=flow_run_id or _common_string_value(
            [record.flow_run_id for record in extraction_results]
        ),
        document_id=document_id or _common_string_value(
            [record.document_id for record in extraction_results]
        ),
        output_format=output_format,
    )


def default_projection_plan(
    bundle: FlowOutputArtifactBundle,
    *,
    output_format: FlowOutputFormat,
    row_source: FlowOutputRowSource | None = None,
) -> FlowOutputProjectionPlan:
    selected_row_source = row_source or bundle.default_row_source
    row_strategy: FlowOutputRowStrategy = "object"
    if selected_row_source == "object":
        object_rows = bundle.rows_for_source("object")
        source_identities = _source_identities_for_rows(object_rows)
        has_attribute_fields = any(
            field_ref.startswith(_OBJECT_ATTRIBUTE_FIELD_PREFIX)
            for row in object_rows
            for field_ref in row
        )
        if len(source_identities) == 1 and (
            output_format == "tsv"
            or (output_format == "csv" and has_attribute_fields)
        ):
            row_strategy = "wide_union"
    columns = default_columns_for_row_source(
        bundle,
        selected_row_source,
        row_strategy=row_strategy,
    )
    return FlowOutputProjectionPlan(
        format=output_format,
        row_source=selected_row_source,
        row_strategy=row_strategy,
        columns=columns,
        filters=(
            default_object_filters(bundle, bundle.rows_for_source("object"))
            if selected_row_source == "object"
            else []
        ),
    )


def default_object_filters(
    bundle: FlowOutputArtifactBundle,
    rows: Sequence[Mapping[str, Any]],
) -> list[FlowOutputFilterSpec]:
    """Default object rows are each source's curatable units.

    Supporting objects (subjects, terms, validated references) have their own
    rows but their open findings already mark the annotation cells that
    reference them, so a default plan leaves them out with an explicit, visible
    ``object.object_type`` filter; a plan without it lists them. A source whose
    units are absent keeps all of its rows.
    """

    row_ids = {id(row) for row in rows}
    kept: list[str] = []
    hidden = False
    for artifact in bundle.artifacts:
        present = list(dict.fromkeys(
            str(row.get("object.object_type") or "")
            for row in artifact.rows_by_source.get("object") or []
            if id(row) in row_ids
        ))
        units = [object_type for object_type in present if object_type in artifact.default_object_types]
        hidden = hidden or (bool(units) and len(units) < len(present))
        kept.extend(object_type for object_type in (units or present) if object_type not in kept)
    if not hidden:
        return []
    return [FlowOutputFilterSpec(field_ref="object.object_type", op="in", values=kept)]


def default_columns_for_row_source(
    bundle: FlowOutputArtifactBundle,
    row_source: FlowOutputRowSource,
    *,
    row_strategy: FlowOutputRowStrategy = "object",
    available_refs: set[str] | None = None,
    rows: Sequence[Mapping[str, Any]] | None = None,
) -> list[FlowOutputColumnSpec]:
    available = available_refs if available_refs is not None else bundle.field_refs_for_source(row_source)
    selected_rows = rows if rows is not None else bundle.rows_for_source(row_source)
    layout_refs = (
        _declared_layout_refs(bundle, selected_rows, available)
        if row_source == "object" and row_strategy in {"object", "wide_union"}
        else []
    )
    if layout_refs and (
        row_strategy == "object"
        or not any(
            str(ref).startswith(_OBJECT_ATTRIBUTE_FIELD_PREFIX)
            for row in selected_rows
            for ref in row
        )
    ):
        labels = {field.ref: field.label for field in bundle.field_catalog if field.row_source == "object"}
        refs = [*layout_refs, *(["object.validation_status"] if "object.validation_status" in available else [])]
        return [
            FlowOutputColumnSpec(
                key=_column_key_from_ref(field_ref),
                header=labels.get(field_ref) or _field_label(field_ref),
                field_ref=field_ref,
            )
            for field_ref in dict.fromkeys(refs)
        ]
    attribute_field_refs: list[str] = []
    if row_source == "object" and row_strategy == "wide_union":
        all_attribute_refs = _first_seen_refs(selected_rows, prefix=_OBJECT_ATTRIBUTE_FIELD_PREFIX)
        shared_attribute_refs = _shared_refs(selected_rows, refs=all_attribute_refs)
        attribute_field_refs = [
            *shared_attribute_refs,
            *[
                field_ref
                for field_ref in all_attribute_refs
                if field_ref not in shared_attribute_refs
            ],
        ]
        attribute_field_refs = [
            field_ref
            for field_ref in attribute_field_refs
            if field_ref in available
        ]

    if row_source == "object" and row_strategy == "wide_union" and attribute_field_refs:
        selected_identity_refs = [
            field_ref
            for field_ref in (
                "object.label",
                "object.payload.semantic_class",
                "object.object_id",
                "object.evidence_record_ids",
            )
            if field_ref in available
        ]
        return [
            FlowOutputColumnSpec(
                key=_column_key_from_ref(field_ref),
                header=_field_label(field_ref),
                field_ref=field_ref,
            )
            for field_ref in [
                *attribute_field_refs,
                *[
                    field_ref
                    for field_ref in selected_identity_refs
                    if field_ref not in attribute_field_refs
                ],
            ]
        ]

    if row_source == "artifact":
        priority = ARTIFACT_DEFAULT_FIELD_REFS
    elif row_source == "object":
        priority = (
            OBJECT_LEDGER_FIELD_PRIORITY
            if row_strategy in {"object_ledger", "wide_union"}
            else OBJECT_DEFAULT_FIELD_PRIORITY
        )
    elif row_source == "evidence":
        priority = EVIDENCE_DEFAULT_FIELD_PRIORITY
    else:
        priority = VALIDATION_DEFAULT_FIELD_PRIORITY

    selected = [field_ref for field_ref in priority if field_ref in available]
    if row_source == "object" and row_strategy == "wide_union":
        selected = [
            *selected,
            *[
                field.ref
                for field in bundle.field_catalog
                if field.row_source == row_source
                and (
                    field.ref.startswith(_OBJECT_ATTRIBUTE_FIELD_PREFIX)
                    or field.ref.startswith("object.payload.")
                )
                and field.ref not in selected
            ],
        ]
    if not selected:
        selected = [
            field.ref
            for field in bundle.field_catalog
            if field.row_source == row_source
        ][:12]
    return [
        FlowOutputColumnSpec(
            key=_column_key_from_ref(field_ref),
            header=_field_label(field_ref),
            field_ref=field_ref,
        )
        for field_ref in selected
    ]


def _declared_layout_refs(
    bundle: FlowOutputArtifactBundle,
    rows: Sequence[Mapping[str, Any]],
    available: set[str],
) -> list[str]:
    """Declared standard columns of the artifacts that own ``rows``."""

    row_ids = {id(row) for row in rows}
    refs: list[str] = []
    for artifact in bundle.artifacts:
        if not any(id(row) in row_ids for row in artifact.rows_by_source.get("object") or []):
            continue
        refs.extend(ref for ref in artifact.default_object_refs if ref in available and ref not in refs)
    return refs


def _field_catalog_map(bundle: FlowOutputArtifactBundle) -> dict[str, FlowOutputField]:
    return {field.ref: field for field in bundle.field_catalog}


def _validate_ref(
    *,
    field_ref: str | None,
    available_refs: set[str],
    errors: list[str],
    context: str,
) -> None:
    if not field_ref:
        errors.append(f"{context} requires a field_ref.")
        return
    if field_ref not in available_refs:
        errors.append(f"{context} uses unknown field_ref '{field_ref}'.")


def projection_plan_field_refs(plan: FlowOutputProjectionPlan) -> list[str]:
    """Return the same references consumed by runtime projection validation."""
    refs = [*plan.group_by, *(item.field_ref for item in plan.filters), *(item.field_ref for item in plan.sort)]
    for column in plan.columns:
        if column.transform is not None:
            refs.extend(_transform_refs(column.transform))
        elif column.field_ref:
            refs.append(column.field_ref)
    return list(dict.fromkeys(refs))


def projection_plan_predicates(plan: FlowOutputProjectionPlan) -> list[FlowOutputFilterSpec]:
    """Expose the plan's row predicates using runtime semantics."""
    return list(plan.filters)


def _transform_refs(transform: FlowOutputTransformSpec) -> list[str]:
    refs: list[str] = []
    if transform.field_ref:
        refs.append(transform.field_ref)
    refs.extend(transform.field_refs)
    for value in transform.values:
        if isinstance(value, str) and "." in value:
            refs.append(value)
        elif isinstance(value, Mapping) and isinstance(value.get("field_ref"), str):
            refs.append(str(value["field_ref"]))
    return refs


def _pair_join_value_groups(left: Any, right: Any) -> list[tuple[Any, Any]]:
    """Return aligned value pairs, broadcasting a scalar across a list."""

    left_is_list = isinstance(left, list)
    right_is_list = isinstance(right, list)
    left_values = list(left) if left_is_list else ([] if _is_empty(left) else [left])
    right_values = list(right) if right_is_list else ([] if _is_empty(right) else [right])
    if not left_values:
        return [(None, value) for value in right_values]
    if not right_values:
        return [(value, None) for value in left_values]
    if len(left_values) == len(right_values):
        return list(zip(left_values, right_values))
    if not left_is_list and len(left_values) == 1:
        return [(left_values[0], value) for value in right_values]
    if not right_is_list and len(right_values) == 1:
        return [(value, right_values[0]) for value in left_values]
    raise ValueError(
        "pair_join cannot align list values with incompatible lengths "
        f"{len(left_values)} and {len(right_values)}; use equal-length lists or a scalar value."
    )


# Renders one stored value (field ref, value, list element index) as display text.
# ``nested`` marks one item of a list (split column, list element), so a list
# item joins its own items with ", " as it does inside the whole cell.
class ValueRenderer(Protocol):
    def __call__(self, field_ref: str, value: Any, index: int | None, *, nested: bool = False) -> str: ...


def _plain_text(_field_ref: str, value: Any, _index: int | None = None, *, nested: bool = False) -> str:
    return str(value)


# Text of one stored value without unresolved markers: the key a map_value
# lookup matches.
class ValueKey(Protocol):
    def __call__(self, field_ref: str, value: Any, *, nested: bool = False) -> str: ...


def _generic_value_key(_field_ref: str, value: Any, *, nested: bool = False) -> str:
    return display_text(value, marked=False, nested=nested)


def _pair_join_value(
    row: Mapping[str, Any],
    transform: FlowOutputTransformSpec,
    *,
    missing_value: str,
    render: ValueRenderer = _plain_text,
) -> str:
    if len(transform.field_refs) != 2:
        raise ValueError("pair_join requires exactly two field_refs.")
    left_ref, right_ref = transform.field_refs
    pairs = _pair_join_value_groups(row.get(left_ref), row.get(right_ref))
    rendered: list[str] = []
    for index, (left, right) in enumerate(pairs):
        position = index if len(pairs) > 1 else None
        parts = [
            text
            for ref, value in ((left_ref, left), (right_ref, right))
            if not _is_empty(value) and (text := render(ref, value, position))
        ]
        if parts:
            rendered.append(transform.pair_separator.join(parts))
    return transform.separator.join(rendered) if rendered else missing_value


_ELEMENT_PLACEHOLDER = re.compile(r"\{(\d+)\}")


def _element_template_errors(transform: FlowOutputTransformSpec) -> list[str]:
    """Validate format_elements templates and their 1-based placeholders."""

    errors: list[str] = []
    if not transform.field_refs:
        errors.append("format_elements requires at least one field_ref in field_refs.")
    if transform.values:
        errors.append("format_elements uses field_refs and templates; values are not supported.")
    if transform.field_ref is not None or transform.mapping:
        # A per-element template chosen by another field's value is a conditional
        # output (ALL-1283); every element renders with the one template.
        errors.append(
            "format_elements renders every element with its default template; "
            "a field_ref/mapping template selector is not supported."
        )
    for name, template in (("default", transform.default),):
        if not isinstance(template, str) or not template:
            errors.append(f"format_elements {name} must be a non-empty template string.")
            continue
        for match in _ELEMENT_PLACEHOLDER.finditer(template):
            position = int(match.group(1))
            if position < 1 or position > len(transform.field_refs):
                errors.append(
                    f"format_elements {name} placeholder {{{position}}} does not match "
                    f"one of the {len(transform.field_refs)} field_refs."
                )
    return errors


def _aligned_elements(values: Sequence[Any]) -> list[tuple[Any, ...]]:
    """Align list values by index; scalars broadcast and empty lists stay empty."""

    width = 0
    for value in values:
        if isinstance(value, list) and value:
            if width and len(value) != width:
                raise ValueError(
                    "format_elements cannot align list values with incompatible lengths "
                    f"{width} and {len(value)}."
                )
            width = len(value)
    if not width:
        width = 1 if any(not _is_empty(value) for value in values) else 0
    columns: list[list[Any]] = []
    for value in values:
        if isinstance(value, list):
            columns.append(list(value) if value else [None] * width)
        else:
            columns.append([None if _is_empty(value) else value] * width)
    return list(zip(*columns)) if width else []


def _format_elements_value(
    row: Mapping[str, Any],
    transform: FlowOutputTransformSpec,
    *,
    missing_value: str | None,
    render: ValueRenderer | None = None,
) -> str | None:
    refs = list(transform.field_refs)
    values = [row.get(ref) for ref in refs]
    rendered: list[str] = []
    # Values taken from a list are list items; the rest broadcast whole.
    from_list = [isinstance(value, list) for value in values]
    elements = _aligned_elements(values)
    template = str(transform.default)
    for position, field_values in enumerate(elements):
        if all(_is_empty(value) for value in field_values):
            continue

        def substitute(match: re.Match[str]) -> str:
            slot = int(match.group(1)) - 1
            value = field_values[slot]
            if _is_empty(value):
                return missing_value or ""
            if render is None:
                return _string_value(value)
            nested = from_list[slot]
            return render(refs[slot], value, position if len(elements) > 1 else None, nested=nested)

        rendered.append(_ELEMENT_PLACEHOLDER.sub(substitute, template))
    return transform.separator.join(rendered) if rendered else missing_value


def projection_row_ref(row_source: str, index: int) -> str:
    """Runtime reference for the 1-based ``index`` row of one bundle row source."""

    return f"{row_source}#{index}"


def bundle_row_refs(
    bundle: FlowOutputArtifactBundle,
    row_source: FlowOutputRowSource,
) -> dict[int, str]:
    """Map bundle row identity to its stable runtime row reference."""

    return {
        id(row): projection_row_ref(row_source, index)
        for index, row in enumerate(bundle.rows_for_source(row_source), start=1)
    }


def bundle_row_for_ref(
    bundle: FlowOutputArtifactBundle,
    row_ref: str,
) -> tuple[FlowOutputRowSource, dict[str, Any]]:
    """Resolve a runtime row reference against the bound bundle, or fail."""

    raw = str(row_ref or "").strip()
    row_source, separator, position = raw.partition("#")
    if (
        not separator
        or row_source not in ("artifact", "object", "evidence", "validation_finding")
        or not position.isdigit()
    ):
        raise ValueError(
            f"row_ref '{raw}' is not a runtime row reference such as 'object#1'."
        )
    rows = bundle.rows_for_source(row_source)  # type: ignore[arg-type]
    index = int(position)
    if index < 1 or index > len(rows):
        raise ValueError(
            f"row_ref '{raw}' is outside the {len(rows)} saved {row_source} row(s)."
        )
    return row_source, rows[index - 1]  # type: ignore[return-value]


class FlowOutputOperationalCeilingError(RuntimeError):
    """A finalized output exceeded an operational ceiling and was not produced."""

    def __init__(self, message: str, *, measured: int, limit: int, setting: str, unit: str) -> None:
        super().__init__(message)
        self.measured = measured
        self.limit = limit
        self.setting = setting
        self.unit = unit


def projection_plan_allows_empty_bundle(plan: FlowOutputProjectionPlan) -> bool:
    """Return whether a projection plan can safely create one literal-only row."""

    if not plan.columns:
        return False
    if plan.filters or plan.sort or plan.group_by:
        return False
    return all(
        column.transform is not None
        and column.transform.type == "literal"
        and not _transform_refs(column.transform)
        for column in plan.columns
    )


def _source_id_for_row(row: Mapping[str, Any]) -> str:
    return _string_value(row.get("artifact.extraction_result_id"))


def _source_key_for_row(row: Mapping[str, Any]) -> str:
    explicit_source_key = _string_value(row.get("artifact.source_key"))
    if explicit_source_key:
        return explicit_source_key
    source_id = _source_id_for_row(row)
    if source_id:
        return f"extraction_result:{source_id}"
    return ":".join(
        [
            "artifact",
            _string_value(row.get("artifact.step")),
            _string_value(row.get("artifact.agent_id")),
            _string_value(row.get("artifact.adapter_key")),
        ]
    )


def _source_identity_for_row(row: Mapping[str, Any]) -> str:
    source_id = _source_id_for_row(row)
    if source_id:
        return f"extraction_result:{source_id}"
    return f"source_key:{_source_key_for_row(row)}"


def _source_identities_for_rows(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    return {_source_identity_for_row(row) for row in rows}


def _source_keys_for_rows(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    return {_source_key_for_row(row) for row in rows}


def _source_ids_for_rows(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    return {source_id for row in rows if (source_id := _source_id_for_row(row))}


def _field_refs_for_rows(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    return {
        str(key)
        for row in rows
        for key in row
    }


def _first_seen_refs(
    rows: Sequence[Mapping[str, Any]],
    *,
    prefix: str,
) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for field_ref in row:
            field_ref = str(field_ref)
            if field_ref.startswith(prefix) and field_ref not in seen:
                seen.add(field_ref)
                refs.append(field_ref)
    return refs


def _shared_refs(
    rows: Sequence[Mapping[str, Any]],
    *,
    refs: Sequence[str],
) -> list[str]:
    if not rows:
        return []
    return [
        field_ref
        for field_ref in refs
        if all(not _is_empty(row.get(field_ref)) for row in rows)
    ]


def _canonical_object_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in rows
        if row.get("artifact.is_canonical_curation_data") is True
    ]


def _rows_for_plan(
    bundle: FlowOutputArtifactBundle,
    plan: FlowOutputProjectionPlan,
) -> list[dict[str, Any]]:
    rows = bundle.rows_for_source(plan.row_source)
    if plan.selection_mode == "selected_fields":
        node_ids = {source.node_id for source in plan.selected_sources}
        return [row for row in rows if row.get("artifact.node_id") in node_ids]
    selected_source_ids = {
        source_id.strip()
        for source_id in plan.source_extraction_result_ids
        if isinstance(source_id, str) and source_id.strip()
    }
    selected_source_keys = {
        source_key.strip()
        for source_key in plan.source_keys
        if isinstance(source_key, str) and source_key.strip()
    }
    if not selected_source_ids and not selected_source_keys:
        return rows
    return [
        row
        for row in rows
        if (
            (selected_source_ids and _source_id_for_row(row) in selected_source_ids)
            or (selected_source_keys and _source_key_for_row(row) in selected_source_keys)
        )
    ]


def _override_errors(
    bundle: FlowOutputArtifactBundle,
    plan: FlowOutputProjectionPlan,
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[FlowOutputColumnSpec],
) -> list[str]:
    """Validate explicit row/cell overrides against authorized bundle rows."""

    if not plan.overrides:
        return []
    errors: list[str] = []
    selected_row_ids = {id(row) for row in rows}
    selected_refs = {
        ref
        for row_id, ref in bundle_row_refs(bundle, plan.row_source).items()
        if row_id in selected_row_ids
    }
    column_keys = {column.key for column in columns if column.split_list is None}
    split_keys = {column.key for column in columns if column.split_list is not None}
    for override in plan.overrides:
        base, _, number = str(override.column_key or "").rpartition("_")
        if base in split_keys and number.isdigit():
            # Expanded split columns are checked against the data at render time.
            column_keys.add(str(override.column_key))
    excluded_refs = {override.row_ref for override in plan.overrides if override.exclude}
    seen: set[tuple[str, str | None]] = set()
    for index, override in enumerate(plan.overrides, start=1):
        context = f"Override {index}"
        try:
            row_source, _row = bundle_row_for_ref(bundle, override.row_ref)
        except ValueError as exc:
            errors.append(f"{context}: {exc}")
            continue
        if row_source != plan.row_source or override.row_ref not in selected_refs:
            errors.append(
                f"{context}: row_ref '{override.row_ref}' is not one of this projection's "
                f"selected {plan.row_source} rows."
            )
        if override.exclude:
            if override.column_key is not None or override.value is not None:
                errors.append(f"{context}: an exclude override cannot also set column_key or value.")
        elif not override.column_key or override.column_key not in column_keys:
            errors.append(
                f"{context}: column_key '{override.column_key}' is not an output column key."
            )
        elif override.row_ref in excluded_refs:
            errors.append(
                f"{context}: row_ref '{override.row_ref}' is excluded, so its cells cannot be edited."
            )
        identity = (override.row_ref, None if override.exclude else override.column_key)
        if identity in seen:
            errors.append(f"{context}: duplicate override for {identity[0]} {identity[1] or '(row)'}.")
        seen.add(identity)
    return errors


def _split_list_errors(
    plan: FlowOutputProjectionPlan,
    column: FlowOutputColumnSpec,
    rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Static checks for a split_list column; data-sized checks run at render."""

    split = column.split_list
    assert split is not None
    context = f"Column '{column.key}' split_list"
    errors: list[str] = []
    if plan.format == "json":
        errors.append(f"{context} applies to CSV, TSV and chat output; JSON keeps lists lossless.")
    if column.transform is not None or not column.field_ref:
        errors.append(f"{context} needs a field_ref, not a transform.")
    if split.header_template is not None and split.headers:
        errors.append(f"{context} takes header_template or headers, not both.")
    if split.header_template is not None and "{n}" not in split.header_template:
        errors.append(f"{context} header_template must contain {{n}} (e.g. 'Anatomy Term {{n}}').")
    if any(not str(header).strip() for header in split.headers):
        errors.append(f"{context} headers cannot be blank.")
    if len(set(split.headers)) != len(split.headers):
        errors.append(f"{context} headers must be distinct.")
    ceiling = get_flow_output_split_list_max_columns()
    if split.max_columns is not None and not 1 <= split.max_columns <= ceiling:
        errors.append(f"{context} max_columns must be between 1 and {ceiling}.")
    if len(split.headers) > (split.max_columns or ceiling):
        errors.append(f"{context} has more headers than its column limit {split.max_columns or ceiling}.")
    return errors


def _split_list_headers(column: FlowOutputColumnSpec, count: int) -> list[str]:
    split = column.split_list
    assert split is not None
    if split.headers:
        return list(split.headers[:count])
    template = split.header_template or f"{column.header or column.key} {{n}}"
    return [template.replace("{n}", str(number)) for number in range(1, count + 1)]


def _expand_split_columns(
    columns: Sequence[FlowOutputColumnSpec],
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[FlowOutputColumnSpec], dict[str, tuple[FlowOutputColumnSpec, int]]]:
    """Output columns with split lists expanded, sized by the longest list in ``rows``.

    Returns the output columns and, per expanded key, its source column and item
    index. Fails explicitly rather than dropping items or inventing names.
    """

    output: list[FlowOutputColumnSpec] = []
    items: dict[str, tuple[FlowOutputColumnSpec, int]] = {}
    ceiling = get_flow_output_split_list_max_columns()
    for column in columns:
        split = column.split_list
        if split is None:
            output.append(column)
            continue
        longest = max(
            (len(_split_items(row.get(column.field_ref or ""))) for row in rows),
            default=0,
        )
        count = max(1, longest, len(split.headers))
        limit = min(split.max_columns or ceiling, ceiling)
        if count > limit:
            message = (
                f"Column '{column.key}' needs {count} split columns for its longest list, "
                f"above the limit of {limit}. No items were dropped; filter rows, raise "
                "max_columns, or keep the list in one column."
            )
            if split.max_columns is None or split.max_columns >= ceiling:
                raise FlowOutputOperationalCeilingError(
                    message, measured=count, limit=ceiling,
                    setting="FLOW_OUTPUT_SPLIT_LIST_MAX_COLUMNS", unit="columns",
                )
            raise ValueError(message)
        if split.headers and len(split.headers) < longest:
            raise ValueError(
                f"Column '{column.key}' split_list names {len(split.headers)} headers but the "
                f"longest list has {longest} items. Add headers or use header_template."
            )
        for number, header in enumerate(_split_list_headers(column, count), start=1):
            key = f"{column.key}_{number}"
            output.append(FlowOutputColumnSpec(key=key, header=header, field_ref=column.field_ref,
                                               source_node_id=column.source_node_id))
            items[key] = (column, number - 1)
    headers = [column.header or column.key for column in output]
    duplicates = sorted({header for header in headers if headers.count(header) > 1})
    if items and duplicates:
        raise ValueError(
            "Output headers must be distinct after split_list expansion; duplicated: "
            + ", ".join(duplicates)
        )
    keys = [column.key for column in output]
    duplicate_keys = sorted({key for key in keys if keys.count(key) > 1})
    if duplicate_keys:
        raise ValueError(
            "Output column keys must be distinct after split_list expansion; duplicated: "
            + ", ".join(duplicate_keys)
        )
    return output, items


def validate_projection_plan(
    bundle: FlowOutputArtifactBundle,
    plan: FlowOutputProjectionPlan,
) -> tuple[list[str], list[str], list[FlowOutputColumnSpec]]:
    """Validate a projection plan and return errors, warnings, and concrete columns."""

    errors: list[str] = []
    warnings: list[str] = list(bundle.warnings)
    if plan.selection_mode == "selected_fields":
        from src.lib.flows.selected_export import selected_export_errors
        errors.extend(selected_export_errors(bundle, plan))
    all_rows = bundle.rows_for_source(plan.row_source)
    rows = _rows_for_plan(bundle, plan)
    if plan.format == "tsv" and plan.row_source == "artifact":
        errors.append(
            "Artifact-summary rows cannot be used for curation TSV exports; "
            "select canonical object rows from a backend extraction result."
        )
    if plan.row_strategy != "object" and plan.row_source != "object":
        errors.append("row_strategy is only supported with row_source='object'.")
    if (plan.source_extraction_result_ids or plan.source_keys) and plan.row_source != "object":
        errors.append("source selection is only supported with row_source='object'.")
    requested_source_ids = {
        source_id.strip()
        for source_id in plan.source_extraction_result_ids
        if isinstance(source_id, str) and source_id.strip()
    }
    requested_source_keys = {
        source_key.strip()
        for source_key in plan.source_keys
        if isinstance(source_key, str) and source_key.strip()
    }
    if plan.row_source == "object":
        available_source_ids = _source_ids_for_rows(all_rows)
        available_source_keys = _source_keys_for_rows(all_rows)
        missing_source_ids = sorted(requested_source_ids - available_source_ids)
        if missing_source_ids:
            errors.append(
                "source_extraction_result_ids include IDs with no canonical object rows: "
                + ", ".join(missing_source_ids)
            )
        missing_source_keys = sorted(requested_source_keys - available_source_keys)
        if missing_source_keys:
            errors.append(
                "source_keys include keys with no canonical object rows: "
                + ", ".join(missing_source_keys)
            )
        if plan.format == "tsv":
            noncanonical_rows = [
                row
                for row in rows
                if row.get("artifact.is_canonical_curation_data") is not True
            ]
            if noncanonical_rows:
                errors.append(
                    "Curation TSV exports require canonical backend extraction data; "
                    "model-written step output cannot be used as TSV object rows."
                )
        source_identities = _source_identities_for_rows(rows)
        if plan.format == "tsv" and len(source_identities) > 1 and plan.row_strategy == "object":
            errors.append(
                "Multiple canonical extraction sources are available for this TSV export; "
                "select one source_extraction_result_id/source_key or use "
                "row_strategy='object_ledger' or row_strategy='wide_union' for an "
                "explicit combined export plan."
            )

    synthetic_literal_row = not rows and projection_plan_allows_empty_bundle(plan)
    if plan.format == "tsv" and synthetic_literal_row:
        errors.append(
            "Curation TSV exports require canonical backend extraction object rows; "
            "literal-only TSV projections are not allowed."
        )
    if not rows and not synthetic_literal_row and plan.selection_mode != "selected_fields":
        errors.append(f"Row source '{plan.row_source}' is not available for this flow output.")
    if synthetic_literal_row:
        warnings.append(
            "Projected one literal-only row because no upstream flow artifacts were available."
        )

    if plan.max_rows is not None and (plan.max_rows < 1 or plan.max_rows > MAX_PROJECTION_ROWS):
        errors.append(f"max_rows must be between 1 and {MAX_PROJECTION_ROWS}.")

    available_refs = _field_refs_for_rows(rows) if rows else bundle.field_refs_for_source(plan.row_source)
    if plan.selection_mode == "selected_fields":
        available_refs |= bundle.field_refs_for_source(plan.row_source)
        if len(rows) > MAX_PROJECTION_ROWS:
            errors.append("The selected export exceeds the configured row limit; increase FLOW_PROJECTION_MAX_ROWS before exporting all items.")
    if plan.row_source == "object":
        # Source selection is evaluated by the existing row resolver (keys and
        # result IDs have OR semantics). Only those artifacts own the declared
        # types relevant to this projection, not the merged catalog's mixed type.
        selected_fields: dict[str, list[FlowOutputField]] = defaultdict(list)
        for artifact in bundle.artifacts:
            if (not requested_source_ids and not requested_source_keys
                    or artifact.extraction_result_id in requested_source_ids
                    or artifact.source_key in requested_source_keys):
                for field in artifact.declared_fields:
                    selected_fields[field.ref].append(field)
        for predicate in projection_plan_predicates(plan):
            if predicate.op not in {"gt", "gte", "lt", "lte"}:
                continue
            if any(field.value_type not in {"integer", "number"}
                   for field in selected_fields.get(predicate.field_ref, [])):
                errors.append(
                    f"Numeric predicate field '{predicate.field_ref}' is not a scalar number "
                    "in the selected source's saved profile."
                )
    columns = plan.columns or default_columns_for_row_source(
        bundle,
        plan.row_source,
        row_strategy=plan.row_strategy,
        available_refs=available_refs,
    )
    if not columns:
        errors.append(f"No columns are available for row source '{plan.row_source}'.")

    seen_keys: set[str] = set()
    for index, column in enumerate(columns, start=1):
        if not column.key.strip():
            errors.append(f"Column {index} has an empty key.")
        if column.key in seen_keys:
            errors.append(f"Duplicate output column key '{column.key}'.")
        seen_keys.add(column.key)
        if column.split_list is not None:
            errors.extend(_split_list_errors(plan, column, rows))
        if column.transform is None:
            _validate_ref(
                field_ref=column.field_ref,
                available_refs=available_refs,
                errors=errors,
                context=f"Column '{column.key}'",
            )
        else:
            transform = column.transform
            if transform.type == "format_elements":
                errors.extend(
                    f"Column '{column.key}' {error}"
                    for error in _element_template_errors(transform)
                )
            if transform.type == "pair_join":
                if transform.field_ref is not None or transform.values:
                    errors.append(
                        f"Column '{column.key}' pair_join uses field_refs only; "
                        "field_ref and values are not supported."
                    )
                if len(transform.field_refs) != 2:
                    errors.append(
                        f"Column '{column.key}' pair_join requires exactly two field_refs."
                    )
            for ref in _transform_refs(transform):
                _validate_ref(
                    field_ref=ref,
                    available_refs=available_refs,
                    errors=errors,
                    context=f"Column '{column.key}' transform",
                )
            if transform.type == "pair_join" and len(transform.field_refs) == 2:
                for row in rows:
                    try:
                        _pair_join_value(row, transform, missing_value=plan.missing_value)
                    except ValueError as exc:
                        errors.append(f"Column '{column.key}' {exc}")
                        break
            if transform.type == "format_elements" and not _element_template_errors(transform):
                for row in rows:
                    try:
                        _format_elements_value(row, transform, missing_value=plan.missing_value)
                    except ValueError as exc:
                        errors.append(f"Column '{column.key}' {exc}")
                        break
            if transform.type == "literal" and transform.value is None:
                warnings.append(f"Column '{column.key}' literal transform has a null value.")

    for filter_spec in plan.filters:
        _validate_ref(
            field_ref=filter_spec.field_ref,
            available_refs=available_refs,
            errors=errors,
            context="Filter",
        )
        if filter_spec.op == "in" and not filter_spec.values:
            errors.append("Filter operator 'in' requires values.")

    for sort_spec in plan.sort:
        _validate_ref(
            field_ref=sort_spec.field_ref,
            available_refs=available_refs,
            errors=errors,
            context="Sort",
        )

    for field_ref in plan.group_by:
        _validate_ref(
            field_ref=field_ref,
            available_refs=available_refs,
            errors=errors,
            context="Group by",
        )
    errors.extend(_override_errors(bundle, plan, rows, columns))
    if plan.group_by and plan.format in {"csv", "tsv"}:
        errors.append(
            f"group_by is not supported for {plan.format.upper()} projections; "
            "use sort/group columns in a flat export or choose JSON/chat output."
        )
    if plan.group_by and plan.format == "json" and plan.json_shape != "grouped":
        errors.append("JSON projection group_by requires json_shape='grouped'.")
    if plan.json_shape == "grouped" and not plan.group_by:
        errors.append("json_shape='grouped' requires at least one group_by field.")

    return errors, warnings, columns


def _coerce_numeric_filter_value(value: Any, *, field_ref: str, op: str) -> float:
    if isinstance(value, bool) or _is_empty(value):
        raise ValueError(
            f"Filter operator '{op}' requires numeric values for field '{field_ref}'."
        )
    if isinstance(value, (int, float)):
        numeric_value = float(value)
    elif isinstance(value, str):
        try:
            numeric_value = float(value.strip())
        except ValueError as exc:
            raise ValueError(
                f"Filter operator '{op}' requires numeric values for field '{field_ref}'; "
                f"got non-numeric value {value!r}."
            ) from exc
    else:
        raise ValueError(
            f"Filter operator '{op}' requires numeric values for field '{field_ref}'; "
            f"got {type(value).__name__}."
        )
    if not math.isfinite(numeric_value):
        raise ValueError(
            f"Filter operator '{op}' requires finite numeric values for field '{field_ref}'."
        )
    return numeric_value


def _compare_values(left: Any, right: Any, op: str, *, field_ref: str) -> bool:
    if op in {"eq", "ne"}:
        result = left == right
        return result if op == "eq" else not result
    if op == "contains":
        if isinstance(left, list):
            return right in left
        return str(right).lower() in str(left).lower()
    if op not in _ORDERED_FILTER_OPS:
        return False
    left_number = _coerce_numeric_filter_value(left, field_ref=field_ref, op=op)
    right_number = _coerce_numeric_filter_value(right, field_ref=field_ref, op=op)
    if op == "gt":
        return left_number > right_number
    if op == "gte":
        return left_number >= right_number
    if op == "lt":
        return left_number < right_number
    if op == "lte":
        return left_number <= right_number
    return False


def _row_matches_filter(row: Mapping[str, Any], filter_spec: FlowOutputFilterSpec) -> bool:
    value = row.get(filter_spec.field_ref)
    if filter_spec.op == "is_empty":
        return _is_empty(value)
    if filter_spec.op == "is_not_empty":
        return not _is_empty(value)
    if filter_spec.op == "in":
        return value in filter_spec.values
    return _compare_values(value, filter_spec.value, filter_spec.op, field_ref=filter_spec.field_ref)


def _sort_rows(
    rows: list[dict[str, Any]],
    sort_specs: Sequence[FlowOutputSortSpec],
) -> list[dict[str, Any]]:
    sorted_rows = list(rows)
    for sort_spec in reversed(sort_specs):
        sorted_rows.sort(
            key=lambda row: (
                _is_empty(row.get(sort_spec.field_ref)),
                str(row.get(sort_spec.field_ref) or "").lower(),
            ),
            reverse=sort_spec.direction == "desc",
        )
    return sorted_rows


def _transform_value(
    row: Mapping[str, Any],
    transform: FlowOutputTransformSpec,
    *,
    missing_value: str,
    render: ValueRenderer | None = None,
    value_key: ValueKey = _generic_value_key,
) -> Any:
    text = render or _plain_text
    if transform.type == "literal":
        return transform.value
    if transform.type == "concat":
        parts: list[str] = []
        for value in transform.values:
            if isinstance(value, Mapping) and isinstance(value.get("field_ref"), str):
                ref = str(value["field_ref"])
                part = row.get(ref)
                if not _is_empty(part):
                    parts.append(text(ref, part, None))
                continue
            if isinstance(value, str) and value in row:
                part = row.get(value)
                if not _is_empty(part):
                    parts.append(text(value, part, None))
                continue
            if not _is_empty(value):
                parts.append(str(value))
        return transform.separator.join(part for part in parts if part)
    if transform.type == "join_list":
        ref = transform.field_ref or ""
        value = row.get(ref)
        if isinstance(value, list):
            items = [
                text(ref, item, index, nested=True)
                for index, item in enumerate(value)
                if not _is_empty(item)
            ]
            return transform.separator.join(item for item in items if item)
        return missing_value if _is_empty(value) else text(ref, value, None)
    if transform.type == "pair_join":
        return _pair_join_value(row, transform, missing_value=missing_value, render=text)
    if transform.type == "count":
        value = row.get(transform.field_ref or "")
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return 0 if _is_empty(value) else 1
    if transform.type == "map_value":
        ref = transform.field_ref or ""
        value = row.get(ref)
        # Structured values match by their display text, never Python repr.
        key = value_key(ref, value) if isinstance(value, (Mapping, list, tuple)) else str(value)
        if key in transform.mapping:
            return transform.mapping[key]
        return transform.default if transform.default is not None else missing_value
    if transform.type == "format_elements":
        return _format_elements_value(row, transform, missing_value=missing_value, render=render)
    if transform.type == "boolean_label":
        value = row.get(transform.field_ref or "")
        if isinstance(value, bool):
            return transform.true_label if value else transform.false_label
        normalized = str(value).strip().lower()
        if normalized in {"true", "yes", "1", "y"}:
            return transform.true_label
        if normalized in {"false", "no", "0", "n"}:
            return transform.false_label
        return transform.unknown_label
    return missing_value


def _project_row(
    row: Mapping[str, Any],
    columns: Sequence[FlowOutputColumnSpec],
    *,
    missing_value: str | None,
    preserve_empty: bool = False,
    render: ValueRenderer | None = None,
    value_key: ValueKey = _generic_value_key,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for column in columns:
        if column.source_node_id and row.get("artifact.node_id") != column.source_node_id:
            value = None
        elif column.transform is not None:
            value = _transform_value(
                row, column.transform, missing_value=missing_value, render=render, value_key=value_key,
            )
        else:
            value = row.get(column.field_ref or "")
            # Numbers and booleans stay typed; text and structured values render.
            if render is not None and not _is_empty(value) and (
                isinstance(value, (str, list, tuple, dict))
            ):
                value = render(column.field_ref or "", value, None)
        if render is not None and value is not None and not isinstance(value, (str, int, float, bool)):
            value = display_text(value)
        if value is None or (not preserve_empty and _is_empty(value)):
            value = missing_value
        output[column.key] = _jsonable(value)
    return output


def _payload_path_for_ref(field_ref: str) -> str | None:
    """The object payload path a projection field reads, when it has one."""

    if field_ref.startswith("object.pack."):
        _, _, rest = field_ref.removeprefix("object.pack.").partition(".")
        return rest or None
    if field_ref.startswith(_OBJECT_ATTRIBUTE_FIELD_PREFIX):
        return "attributes." + field_ref.removeprefix(_OBJECT_ATTRIBUTE_FIELD_PREFIX)
    if field_ref.startswith("object.payload."):
        return field_ref.removeprefix("object.payload.")
    return None


def _object_keys(row: Mapping[str, Any]) -> list[str]:
    """Envelope-scoped keys of the object a row names (object_id and pending_ref_id)."""

    return [
        f"{kind}:{reference}"
        for kind in ("object_id", "pending_ref_id")
        if (reference := _string_value(row.get(f"object.{kind}")))
    ]


def _ref_key(object_ref: Mapping[str, Any]) -> str | None:
    for kind in ("object_id", "pending_ref_id"):
        reference = _string_value(object_ref.get(kind))
        if reference:
            return f"{kind}:{reference}"
    return None


def _object_ref_links(
    item: Mapping[str, Any], ref_fields: Mapping[str, str],
) -> dict[str, list[str]]:
    """Referenced object keys per object_ref field path, in object_refs order."""

    object_refs = [ref for ref in item.get("object_refs") or [] if isinstance(ref, Mapping)]
    links: dict[str, list[str]] = {}
    for field_path, object_type in ref_fields.items():
        keys = [
            key
            for ref in object_refs
            if ref.get("object_type") == object_type and (key := _ref_key(ref))
        ]
        if keys:
            links[field_path] = keys
    return links


def _open_finding_paths(bundle: FlowOutputArtifactBundle) -> dict[int, list[tuple[Any, ...]]]:
    """Open validation finding paths per object row (keyed by ``id(row)``).

    Pending ref ids restart in every envelope, so findings match objects only
    within their own artifact. An object_ref field also carries the open
    findings of the object it references: the n-th referenced object of the
    field's type is the n-th element of a list field.
    """

    by_row: dict[int, list[tuple[Any, ...]]] = {}
    for artifact in bundle.artifacts:
        direct: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
        for row in artifact.rows_by_source.get("validation_finding") or []:
            if str(row.get("validation.status") or "").strip().lower() != "open":
                continue
            field_path = _string_value(row.get("validation.field_path")).removeprefix("payload.")
            tokens = path_tokens(field_path) if field_path else None
            if tokens is None:
                continue
            for key in _object_keys(row):
                direct[key].append(tokens)
        for row in artifact.rows_by_source.get("object") or []:
            paths: list[tuple[Any, ...]] = []
            for key in _object_keys(row):
                paths.extend(direct.get(key, []))
                for field_path, ref_keys in artifact.object_ref_links.get(key, {}).items():
                    tokens = path_tokens(field_path)
                    flagged = [position for position, ref in enumerate(ref_keys) if direct.get(ref)]
                    if tokens is None or not flagged:
                        continue
                    if isinstance(tokens[-1], int):
                        # An indexed field is the referenced object at that position.
                        if tokens[-1] in flagged:
                            paths.append(tokens)
                    elif len(ref_keys) == 1:
                        paths.append(tokens)
                    else:
                        paths.extend((*tokens, position) for position in flagged)
            if paths:
                by_row[id(row)] = list(dict.fromkeys(paths))
    return by_row


def _unresolved_for(
    finding_paths: Sequence[tuple[Any, ...]], payload_path: str | None,
) -> frozenset[tuple[Any, ...]]:
    """Open finding paths relative to the value a column reads.

    Indexed findings map onto fanned-out columns by position, so the marker
    lands on the unresolved element.
    """

    target = path_tokens(payload_path) if payload_path else None
    if target is None:
        return frozenset()
    return frozenset(
        relative
        for finding in finding_paths
        if (relative := relative_finding_path(finding, target)) is not None
    )


def _display_renderer(
    specs: Mapping[str, Any],
    finding_paths: Sequence[tuple[Any, ...]],
) -> ValueRenderer:
    def render(field_ref: str, value: Any, index: int | None, *, nested: bool = False) -> str:
        unresolved = _unresolved_for(finding_paths, _payload_path_for_ref(field_ref))
        if index is not None:
            # One split_list item: the findings on that position, or on every position.
            unresolved = frozenset(
                path[1:] if path and path[0] == index else path
                for path in unresolved
                if not path or not isinstance(path[0], int) or path[0] == index
            )
        return display_text(value, specs.get(field_ref), unresolved=unresolved, nested=nested)

    return render


def _group_projected_rows(
    source_rows: Sequence[Mapping[str, Any]],
    projected_rows: Sequence[Mapping[str, Any]],
    group_by: Sequence[str],
    *,
    group_value: Callable[[str, Any], Any] | None = None,
) -> list[dict[str, Any]]:
    """Group rows by their group_by values (raw JSON values unless ``group_value`` renders them)."""

    groups: dict[str, dict[str, Any]] = {}
    for source_row, projected_row in zip(source_rows, projected_rows):
        values = {
            field_ref: (
                group_value(field_ref, source_row.get(field_ref))
                if group_value is not None
                else _jsonable(source_row.get(field_ref))
            )
            for field_ref in group_by
        }
        # Structured values are unhashable; their canonical JSON is the key.
        key = json.dumps(list(values.values()), sort_keys=True, default=str)
        if key not in groups:
            groups[key] = {"group": values, "rows": []}
        groups[key]["rows"].append(dict(projected_row))
    return list(groups.values())


def apply_projection_plan(
    bundle: FlowOutputArtifactBundle,
    plan: FlowOutputProjectionPlan,
    *, preview_limit: int | None = None,
    render_display: bool = True,
) -> FlowOutputProjectionResult:
    """Project rows; non-JSON cells become display text (``value_display``).

    JSON output always keeps the raw structured values. Inspection tools pass
    ``render_display=False`` to see the stored values.
    """
    errors, warnings, columns = validate_projection_plan(bundle, plan)
    if errors:
        raise ValueError("; ".join(errors))

    rows = _rows_for_plan(bundle, plan)
    if not rows and projection_plan_allows_empty_bundle(plan):
        rows = [{}]
    for filter_spec in plan.filters:
        rows = [row for row in rows if _row_matches_filter(row, filter_spec)]
    rows = _sort_rows(rows, plan.sort)
    ref_by_row_id = bundle_row_refs(bundle, plan.row_source)
    excluded_refs = {override.row_ref for override in plan.overrides if override.exclude}
    if plan.overrides:
        present_refs = {ref_by_row_id.get(id(row)) for row in rows}
        missing_refs = sorted(
            {override.row_ref for override in plan.overrides} - present_refs
        )
        if missing_refs:
            raise ValueError(
                "Override row_ref(s) are not in the filtered projection: "
                + ", ".join(missing_refs)
            )
    rows_excluded = 0
    if excluded_refs:
        kept_rows = [row for row in rows if ref_by_row_id.get(id(row)) not in excluded_refs]
        rows_excluded = len(rows) - len(kept_rows)
        rows = kept_rows

    total_count = len(rows)
    max_rows = plan.max_rows or MAX_PROJECTION_ROWS
    if preview_limit is not None:
        max_rows = min(max_rows, max(1, preview_limit))
    limited_rows = rows[:max_rows]
    truncated = len(rows) > len(limited_rows)
    limited_by_max_rows = bool(
        plan.max_rows is not None and total_count > plan.max_rows
    )
    row_refs = [ref_by_row_id.get(id(row), "") for row in limited_rows]
    display = render_display and plan.format != "json"
    specs = {field.ref: field.display for field in bundle.field_catalog if field.row_source == plan.row_source}

    def value_key(field_ref: str, value: Any, *, nested: bool = False) -> str:
        return display_text(value, specs.get(field_ref), marked=False, nested=nested)

    open_paths = _open_finding_paths(bundle) if display and plan.row_source == "object" else {}
    # Split lists are sized by the longest list across all filtered rows.
    output_columns, split_items = (
        _expand_split_columns(columns, rows)
        if any(column.split_list is not None for column in columns)
        else (list(columns), {})
    )
    base_columns = [column for column in columns if column.split_list is None]
    preserve_empty = plan.selection_mode == "selected_fields"
    projected_rows = []
    for row in limited_rows:
        render = _display_renderer(specs, open_paths.get(id(row), [])) if display else None
        base = _project_row(
            row,
            base_columns,
            missing_value=plan.missing_value,
            preserve_empty=preserve_empty,
            render=render,
            value_key=value_key,
        )
        if not split_items:
            projected_rows.append(base)
            continue
        projected: dict[str, Any] = {}
        for column in output_columns:
            if column.key not in split_items:
                projected[column.key] = base[column.key]
                continue
            source_column, position = split_items[column.key]
            source_value = row.get(source_column.field_ref or "")
            values = _split_items(source_value)
            item = values[position] if position < len(values) else None
            if source_column.source_node_id and row.get("artifact.node_id") != source_column.source_node_id:
                item = None
            if not _is_empty(item) and render is not None:
                item = render(
                    source_column.field_ref or "", item, position, nested=isinstance(source_value, list),
                )
            if item is None or (not preserve_empty and _is_empty(item)):
                item = plan.missing_value
            projected[column.key] = _jsonable(item)
        projected_rows.append(projected)
    columns = output_columns
    cell_overrides = [override for override in plan.overrides if not override.exclude]
    if cell_overrides:
        position_by_ref = {ref: position for position, ref in enumerate(row_refs)}
        beyond_limit = sorted(
            {override.row_ref for override in cell_overrides} - set(position_by_ref)
        )
        if beyond_limit and preview_limit is None:
            raise ValueError(
                "Override row_ref(s) fall outside the output row limit: "
                + ", ".join(beyond_limit)
            )
        output_keys = {column.key for column in columns}
        for override in cell_overrides:
            if override.column_key not in output_keys:
                raise ValueError(
                    f"Override column_key '{override.column_key}' is not an output column "
                    "after split_list expansion."
                )
            position = position_by_ref.get(override.row_ref)
            if position is not None and override.column_key is not None:
                projected_rows[position][override.column_key] = _jsonable(override.value)

    json_data: Any = None
    chat_output: str | None = None
    if plan.format == "json":
        if plan.json_shape == "grouped":
            json_data = _group_projected_rows(limited_rows, projected_rows, plan.group_by)
        elif plan.json_shape == "bundle":
            json_data = {
                "flow_name": bundle.flow_name,
                "flow_run_id": bundle.flow_run_id,
                "document_id": bundle.document_id,
                "row_source": plan.row_source,
                "field_catalog": [
                    # Display specs only shape non-JSON cells; JSON stays byte-stable.
                    field.model_dump(mode="json", exclude={"display"})
                    for field in bundle.field_catalog
                    if field.row_source == plan.row_source
                ],
                "rows": projected_rows,
                "warnings": warnings,
            }
        else:
            json_data = projected_rows
    elif plan.format == "chat":
        # Chat output renders every projected row; row counts are limited only
        # by an explicit max_rows or an explicit preview request.
        if plan.group_by:
            chat_output = render_grouped_chat_projection(
                # Chat groups read by display text: the grouping key and heading.
                groups=_group_projected_rows(
                    limited_rows,
                    projected_rows,
                    plan.group_by,
                    group_value=lambda field_ref, value: display_text(value, specs.get(field_ref)),
                ),
                columns=columns,
                layout=plan.chat_layout,
                total_count=total_count,
                truncated=truncated,
            )
        else:
            chat_output = render_chat_projection(
                rows=projected_rows,
                columns=columns,
                layout=plan.chat_layout,
                total_count=total_count,
                truncated=truncated,
            )

    return FlowOutputProjectionResult(
        format=plan.format,
        row_source=plan.row_source,
        columns=columns,
        rows=projected_rows,
        total_count=total_count,
        truncated=truncated,
        warnings=warnings,
        json_data=json_data,
        chat_output=chat_output,
        group_by=list(plan.group_by),
        row_refs=row_refs,
        limited_by_max_rows=limited_by_max_rows,
        overrides_applied=len(cell_overrides),
        rows_excluded=rows_excluded,
    )


def inspect_output_artifacts(
    bundle: FlowOutputArtifactBundle,
    *,
    example_limit: int = 3,
) -> dict[str, Any]:
    """Return a bounded projection inventory of artifacts, row sources, and fields."""

    row_sources: dict[str, Any] = {}
    for row_source in ("artifact", "object", "evidence", "validation_finding"):
        rows = bundle.rows_for_source(row_source)  # type: ignore[arg-type]
        bounded_example_limit = max(0, min(example_limit, MAX_PROJECTION_LIST_ITEMS))
        row_sources[row_source] = {
            "row_count": len(rows),
            "default_columns": [
                column.model_dump(mode="json")
                for column in default_columns_for_row_source(bundle, row_source)  # type: ignore[arg-type]
            ],
            "examples": [
                _bounded_projection_row(row)
                for row in rows[:bounded_example_limit]
            ],
            "examples_truncated": len(rows) > bounded_example_limit,
        }
    return {
        "flow_name": bundle.flow_name,
        "flow_run_id": bundle.flow_run_id,
        "document_id": bundle.document_id,
        "default_row_source": bundle.default_row_source,
        "artifact_count": len(bundle.artifacts),
        "row_sources": row_sources,
        "field_catalog": [
            field.model_dump(mode="json")
            for field in bundle.field_catalog
        ],
        "warnings": _bounded_projection_warnings(bundle.warnings),
    }


def preview_output_projection(
    bundle: FlowOutputArtifactBundle,
    plan: FlowOutputProjectionPlan,
    *,
    limit: int = 5,
) -> FlowOutputProjectionPreview:
    errors, warnings, columns = validate_projection_plan(bundle, plan)
    if errors:
        return FlowOutputProjectionPreview(
            status="invalid",
            errors=errors,
            warnings=warnings,
        )
    result = apply_projection_plan(bundle, plan, preview_limit=limit, render_display=True)
    return FlowOutputProjectionPreview(
        status="ok",
        warnings=_bounded_projection_warnings(result.warnings),
        preview_rows=[_bounded_projection_row(row) for row in result.rows[:limit]],
        total_count=result.total_count,
        truncated=result.truncated,
    )


def finalize_output_projection(
    bundle: FlowOutputArtifactBundle,
    plan: FlowOutputProjectionPlan,
) -> FlowOutputProjectionResult:
    """Project every requested row, or fail when an operational ceiling cuts rows.

    An explicit ``max_rows`` is a requested limit and is honored. Without one,
    rows beyond ``FLOW_PROJECTION_MAX_ROWS`` are never silently dropped.
    """

    result = apply_projection_plan(bundle, plan, render_display=True)
    if result.truncated and not result.limited_by_max_rows:
        raise FlowOutputOperationalCeilingError(
            f"The projection matched {result.total_count} rows, above the operational "
            f"ceiling of {MAX_PROJECTION_ROWS} rows. No partial output was produced; "
            "the saved results are unchanged.",
            measured=result.total_count,
            limit=MAX_PROJECTION_ROWS,
            setting="FLOW_PROJECTION_MAX_ROWS",
            unit="rows",
        )
    return result


def render_chat_projection(
    *,
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[FlowOutputColumnSpec],
    layout: FlowOutputChatLayout,
    total_count: int,
    truncated: bool,
) -> str:
    if not rows:
        return "No rows matched the requested output projection."

    if layout == "bullets":
        lines = []
        for row in rows:
            bits = [
                f"{column.header or column.key}: {_string_value(row.get(column.key))}"
                for column in columns
                if not _is_empty(row.get(column.key))
            ]
            lines.append("- " + "; ".join(bits))
    elif layout == "sections":
        lines = []
        for index, row in enumerate(rows, start=1):
            lines.append(f"### Row {index}")
            for column in columns:
                lines.append(f"- {column.header or column.key}: {_string_value(row.get(column.key))}")
    else:
        headers = [column.header or column.key for column in columns]
        divider = ["---" for _ in columns]
        lines = [
            "| " + " | ".join(_markdown_cell(header) for header in headers) + " |",
            "| " + " | ".join(divider) + " |",
        ]
        for row in rows:
            lines.append(
                "| "
                + " | ".join(_markdown_cell(row.get(column.key)) for column in columns)
                + " |"
            )

    if truncated:
        lines.append(f"\nShowing {len(rows)} of {total_count} projected rows.")
    return "\n".join(lines)


def render_grouped_chat_projection(
    *,
    groups: Sequence[Mapping[str, Any]],
    columns: Sequence[FlowOutputColumnSpec],
    layout: FlowOutputChatLayout,
    total_count: int,
    truncated: bool,
) -> str:
    if not groups:
        return "No rows matched the requested output projection."

    lines: list[str] = []
    shown_rows = 0
    for group in groups:
        group_values = group.get("group")
        group_rows = group.get("rows")
        if not isinstance(group_values, Mapping) or not isinstance(group_rows, list):
            continue
        heading_bits = [
            f"{_field_label(str(field_ref))}: {_string_value(value)}"
            for field_ref, value in group_values.items()
        ]
        lines.append(f"## {'; '.join(heading_bits) or 'Ungrouped'}")
        lines.append(
            render_chat_projection(
                rows=group_rows,
                columns=columns,
                layout=layout,
                total_count=len(group_rows),
                truncated=False,
            )
        )
        shown_rows += len(group_rows)

    if truncated:
        lines.append(f"\nShowing {shown_rows} of {total_count} projected rows.")
    return "\n\n".join(lines)


def _markdown_cell(value: Any) -> str:
    text = _string_value(value)
    return text.replace("|", "\\|").replace("\n", " ")


__all__ = [
    "ARTIFACT_DEFAULT_FIELD_REFS",
    "FlowOutputArtifact",
    "FlowOutputArtifactBundle",
    "FlowOutputColumnSpec",
    "FlowOutputField",
    "FlowOutputFilterSpec",
    "FlowOutputOperationalCeilingError",
    "FlowOutputOverrideSpec",
    "FlowOutputSplitListSpec",
    "FlowOutputProjectionPlan",
    "FlowOutputProjectionPreview",
    "FlowOutputProjectionResult",
    "FlowOutputSortSpec",
    "FlowOutputTransformSpec",
    "apply_projection_plan",
    "build_extraction_result_artifact_bundle",
    "bundle_row_for_ref",
    "bundle_row_refs",
    "build_flow_output_artifact_bundle",
    "default_columns_for_row_source",
    "default_object_filters",
    "default_projection_plan",
    "finalize_output_projection",
    "inspect_output_artifacts",
    "projection_plan_allows_empty_bundle",
    "projection_row_ref",
    "preview_output_projection",
    "render_grouped_chat_projection",
    "render_chat_projection",
    "validate_projection_plan",
]
