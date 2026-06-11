"""Deterministic projection of flow artifacts into terminal outputs."""

from __future__ import annotations

from collections import defaultdict
import csv
from datetime import datetime
import io
import json
import math
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, Field

from src.lib.openai_agents.config import (
    get_flow_chat_max_rows,
    get_flow_planner_max_field_examples,
    get_flow_planner_max_list_items,
    get_flow_planner_max_object_items,
    get_flow_planner_max_row_chars,
    get_flow_planner_max_text_chars,
    get_flow_projection_max_rows,
)


FlowOutputFormat = Literal["csv", "tsv", "json", "chat"]
FlowOutputRowSource = Literal["artifact", "object", "evidence", "validation_finding"]
FlowOutputJsonShape = Literal["rows", "grouped", "bundle"]
FlowOutputChatLayout = Literal["table", "sections", "bullets"]
FlowOutputTransformType = Literal[
    "literal",
    "first_non_empty",
    "concat",
    "join_list",
    "count",
    "map_value",
    "boolean_label",
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
# (flow projection planner group).
MAX_PROJECTION_ROWS = get_flow_projection_max_rows()
MAX_CHAT_ROWS = get_flow_chat_max_rows()
MAX_FIELD_EXAMPLES = get_flow_planner_max_field_examples()
MAX_PLANNER_TEXT_CHARS = get_flow_planner_max_text_chars()
MAX_PLANNER_LIST_ITEMS = get_flow_planner_max_list_items()
MAX_PLANNER_OBJECT_ITEMS = get_flow_planner_max_object_items()
MAX_PLANNER_ROW_CHARS = get_flow_planner_max_row_chars()

ARTIFACT_DEFAULT_FIELD_REFS = [
    "artifact.step",
    "artifact.agent_id",
    "artifact.agent_name",
    "artifact.adapter_key",
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

GENERIC_PDF_ANSWER_TABLE_FIELD_PRIORITY = [
    "object.payload.synonym",
    "object.payload.source",
    "object.payload.source_identifier",
    "object.payload.count",
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


class FlowOutputTransformSpec(BaseModel):
    type: FlowOutputTransformType
    field_ref: str | None = None
    field_refs: list[str] = Field(default_factory=list)
    value: Any = None
    values: list[Any] = Field(default_factory=list)
    separator: str = ""
    mapping: dict[str, Any] = Field(default_factory=dict)
    default: Any = None
    true_label: str = "Yes"
    false_label: str = "No"
    unknown_label: str = ""


class FlowOutputColumnSpec(BaseModel):
    key: str
    header: str | None = None
    field_ref: str | None = None
    transform: FlowOutputTransformSpec | None = None


class FlowOutputFilterSpec(BaseModel):
    field_ref: str
    op: FlowOutputFilterOperator
    value: Any = None
    values: list[Any] = Field(default_factory=list)


class FlowOutputSortSpec(BaseModel):
    field_ref: str
    direction: FlowOutputSortDirection = "asc"


class FlowOutputProjectionPlan(BaseModel):
    format: FlowOutputFormat
    row_source: FlowOutputRowSource
    columns: list[FlowOutputColumnSpec] = Field(default_factory=list)
    filters: list[FlowOutputFilterSpec] = Field(default_factory=list)
    sort: list[FlowOutputSortSpec] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    json_shape: FlowOutputJsonShape = "rows"
    chat_layout: FlowOutputChatLayout = "table"
    missing_value: str = ""
    max_rows: int | None = None


class FlowOutputField(BaseModel):
    ref: str
    label: str
    value_type: str
    row_source: FlowOutputRowSource
    non_empty_count: int = 0
    examples: list[Any] = Field(default_factory=list)


class FlowOutputArtifact(BaseModel):
    step: int | None = None
    agent_id: str = ""
    agent_name: str = ""
    adapter_key: str = ""
    extraction_result_id: str | None = None
    envelope_id: str = ""
    domain_pack_id: str = ""
    object_count: int = 0
    evidence_count: int = 0
    candidate_count: int = 0
    artifact_preview: str = ""
    artifact_shape: Literal[
        "domain_envelope",
        "legacy_extraction_envelope",
        "generic_pdf_answer_table",
        "non_structured",
    ] = "non_structured"
    warnings: list[str] = Field(default_factory=list)
    rows_by_source: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class FlowOutputArtifactBundle(BaseModel):
    flow_name: str
    flow_run_id: str | None = None
    document_id: str | None = None
    artifacts: list[FlowOutputArtifact] = Field(default_factory=list)
    field_catalog: list[FlowOutputField] = Field(default_factory=list)
    default_row_source: FlowOutputRowSource = "artifact"
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


def _bounded_planner_text(value: Any, *, max_chars: int = MAX_PLANNER_TEXT_CHARS) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    overflow = len(text) - max_chars
    return f"{text[:max_chars].rstrip()}... [truncated {overflow} chars]"


def _bounded_planner_value(value: Any, *, depth: int = 0) -> Any:
    if isinstance(value, str):
        return _bounded_planner_text(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if depth >= 4:
        return "[truncated:depth]"
    if isinstance(value, Mapping):
        items = list(value.items())
        bounded = {
            str(key): _bounded_planner_value(item, depth=depth + 1)
            for key, item in items[:MAX_PLANNER_OBJECT_ITEMS]
        }
        if len(items) > MAX_PLANNER_OBJECT_ITEMS:
            bounded["_truncated_keys"] = len(items) - MAX_PLANNER_OBJECT_ITEMS
        return bounded
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = list(value)
        bounded_items = [
            _bounded_planner_value(item, depth=depth + 1)
            for item in items[:MAX_PLANNER_LIST_ITEMS]
        ]
        if len(items) > MAX_PLANNER_LIST_ITEMS:
            bounded_items.append({"_truncated_items": len(items) - MAX_PLANNER_LIST_ITEMS})
        return bounded_items
    return _bounded_planner_text(value)


def _bounded_planner_row(row: Mapping[str, Any]) -> dict[str, Any]:
    bounded: dict[str, Any] = {}
    for key, value in row.items():
        bounded[str(key)] = _bounded_planner_value(value)
        encoded = json.dumps(bounded, ensure_ascii=False, default=str)
        if len(encoded) > MAX_PLANNER_ROW_CHARS:
            bounded["_truncated_preview"] = True
            bounded["_truncated_after_field"] = str(key)
            break
    return bounded


def _bounded_planner_warnings(warnings: Sequence[str], *, limit: int = 20) -> list[str]:
    bounded = [_bounded_planner_text(warning) for warning in warnings[:limit]]
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
    return {
        str(key): value
        for key, value in payload.items()
        if isinstance(value, (str, int, float, bool)) or value is None
    }


def _field_label(field_ref: str) -> str:
    if field_ref in _FIELD_LABEL_OVERRIDES:
        return _FIELD_LABEL_OVERRIDES[field_ref]
    suffix = field_ref.split(".")[-1]
    return suffix.replace("_", " ").title()


def _column_key_from_ref(field_ref: str) -> str:
    if field_ref in _ARTIFACT_KEY_BY_REF:
        return _ARTIFACT_KEY_BY_REF[field_ref]
    return field_ref.replace(".", "_").replace("[", "_").replace("]", "")


def _artifact_row(
    *,
    step_number: int | None,
    agent_id: str,
    agent_name: str,
    adapter_key: str,
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


def _object_id_from_item(item: Mapping[str, Any], index: int) -> str:
    for key in ("object_id", "id", "curie", "primary_external_id", "external_id", "pending_ref_id"):
        value = _string_value(item.get(key))
        if value:
            return value
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


def _object_label(item: Mapping[str, Any], payload: Mapping[str, Any], object_id: str) -> str:
    for key in ("label", "symbol", "name", "gene_symbol", "normalized_symbol", "mention", "entity"):
        value = _string_value(payload.get(key))
        if value:
            return value
    for key in ("label", "symbol", "name"):
        value = _string_value(item.get(key))
        if value:
            return value
    return object_id


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
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        payload = _object_payload(item)
        object_id = _object_id_from_item(item, index)
        row: dict[str, Any] = dict(artifact_context)
        row.update(
            {
                "object.object_type": item.get("object_type") or item.get("type") or "",
                "object.object_id": object_id,
                "object.label": _object_label(item, payload, object_id),
                "object.pending_ref_id": item.get("pending_ref_id") or "",
                "object.status": item.get("status") or "",
                "object.evidence_count": _object_evidence_count(item),
                "object.evidence_record_ids": _object_evidence_record_ids(item),
                "object.validation_status": _object_validation_status(item),
            }
        )
        for key, value in _scalar_payload_fields(payload).items():
            row[f"object.payload.{key}"] = value
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
        row: dict[str, Any] = dict(artifact_context)
        row.update(object_context)
        row.update(
            {
                "validation.finding_id": record.get("finding_id") or record.get("id") or index,
                "validation.status": record.get("status") or record.get("state") or "",
                "validation.severity": record.get("severity") or "",
                "validation.message": record.get("message") or record.get("detail") or record.get("reason") or "",
                "validation.field_path": record.get("field_path") or record.get("field_key") or "",
                "validation.validator": record.get("validator") or record.get("binding_id") or "",
            }
        )
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
    refs = _object_ref_values_for_matching(record)
    if not refs:
        return None
    for row in object_rows:
        if refs & _object_row_ref_values(row):
            return row
    return None


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


def _payload_shape(payload: Any) -> Literal[
    "domain_envelope",
    "legacy_extraction_envelope",
    "generic_pdf_answer_table",
    "non_structured",
]:
    if isinstance(payload, Mapping):
        if payload.get("envelope_id") and payload.get("domain_pack_id") and isinstance(payload.get("objects"), list):
            return "domain_envelope"
        if _payload_answer_table_items(payload)[0]:
            return "generic_pdf_answer_table"
        if any(isinstance(payload.get(key), list) for key in ("curatable_objects", "items", "raw_mentions", "exclusions", "ambiguities")):
            return "legacy_extraction_envelope"
    return "non_structured"


def _payload_object_items(payload: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[str]]:
    warnings: list[str] = []
    for key in ("objects", "curatable_objects", "items", "raw_mentions", "exclusions", "ambiguities"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)], warnings
    warnings.append("No supported object list was found for this structured artifact.")
    return [], warnings


def _parse_tabular_answer_rows(text: str) -> list[dict[str, str]]:
    """Extract simple TSV rows embedded in a generic PDF answer."""

    segments: list[list[str]] = []
    current: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```"):
            if current:
                segments.append(current)
                current = []
            continue
        if "\t" not in line:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(line)
    if current:
        segments.append(current)

    best_rows: list[dict[str, str]] = []
    for segment in segments:
        if len(segment) < 2:
            continue
        header = [cell.strip().lower() for cell in segment[0].split("\t")]
        if not any(
            cell in {"synonym", "source", "source_identifier", "count", "label", "name"}
            for cell in header
        ):
            continue
        reader = csv.DictReader(io.StringIO("\n".join(segment)), delimiter="\t")
        rows: list[dict[str, str]] = []
        for row in reader:
            clean_row = {
                str(key or "").strip(): str(value or "").strip()
                for key, value in row.items()
                if str(key or "").strip()
            }
            if any(clean_row.values()):
                rows.append(clean_row)
            if len(rows) >= MAX_PROJECTION_ROWS:
                break
        if len(rows) > len(best_rows):
            best_rows = rows
    return best_rows


def _payload_answer_table_items(payload: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[str]]:
    answer = payload.get("answer")
    if not isinstance(answer, str) or "\t" not in answer:
        return [], []
    rows = _parse_tabular_answer_rows(answer)
    if not rows:
        return [], []
    items: list[Mapping[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        label = (
            row.get("synonym")
            or row.get("label")
            or row.get("name")
            or next((value for value in row.values() if value), f"row {index}")
        )
        items.append(
            {
                "object_type": "extracted_answer_row",
                "object_id": f"answer-row-{index}",
                "label": label,
                "payload": row,
            }
        )
    return items, ["Projected object rows from a TSV-like table in the PDF extraction answer."]


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


def _build_artifact_from_step(step: Mapping[str, Any]) -> FlowOutputArtifact | None:
    candidate = _step_attr(step, "candidate")
    payload = _candidate_attr(candidate, "payload_json")
    if payload is None:
        payload = _payload_from_step_output(step)
    if payload is None:
        return None

    step_number_raw = _step_attr(step, "step")
    try:
        step_number = int(step_number_raw) if step_number_raw is not None else None
    except (TypeError, ValueError):
        step_number = None

    agent_id = _string_value(_step_attr(step, "agent_id") or _candidate_attr(candidate, "agent_key"))
    agent_name = _string_value(_step_attr(step, "agent_name"))
    adapter_key = _string_value(_candidate_attr(candidate, "adapter_key") or agent_id)
    candidate_count = int(_candidate_attr(candidate, "candidate_count", 0) or 0)
    metadata = _candidate_attr(candidate, "metadata", {}) or {}
    extraction_result_id = _string_value(_step_attr(step, "extraction_result_id") or (metadata.get("extraction_result_id") if isinstance(metadata, Mapping) else None)) or None
    preview = _compact_text(_step_attr(step, "output_preview") or _step_attr(step, "output"))

    shape = _payload_shape(payload)
    domain_pack_id = ""
    envelope_id = ""
    object_items: list[Mapping[str, Any]] = []
    warnings: list[str] = []
    if isinstance(payload, Mapping):
        domain_pack_id = _string_value(payload.get("domain_pack_id") or payload.get("adapter_key"))
        envelope_id = _string_value(payload.get("envelope_id"))
        object_items, object_warnings = _payload_answer_table_items(payload)
        if not object_items:
            object_items, object_warnings = _payload_object_items(payload)
        if shape == "non_structured":
            object_items = []
        warnings.extend(object_warnings)
    elif shape == "non_structured":
        warnings.append("Artifact payload is not a supported structured mapping.")

    object_count = len(object_items)
    artifact_row = _artifact_row(
        step_number=step_number,
        agent_id=agent_id,
        agent_name=agent_name,
        adapter_key=adapter_key,
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
    )
    rows_by_source["object"] = object_rows
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
        rows_by_source["validation_finding"].extend(
            _validation_rows_from_records(
                artifact_context=artifact_context,
                object_row=None,
                records=_explicit_validation_findings(payload),
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
                f"{unassociated_validation_count} step-level validation finding(s) had "
                "no explicit matching object ref and were emitted with empty object refs."
            )

    artifact_row["artifact.evidence_count"] = _step_evidence_count(
        step,
        evidence_rows=rows_by_source["evidence"],
    )

    if shape == "legacy_extraction_envelope":
        warnings.append(
            "Legacy extraction envelope projected through explicit top-level list mappings only."
        )
    if shape == "non_structured":
        warnings.append("Only artifact-summary rows are available for this non-structured artifact.")

    return FlowOutputArtifact(
        step=step_number,
        agent_id=agent_id,
        agent_name=agent_name,
        adapter_key=adapter_key,
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
            json_value = _bounded_planner_value(value)
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
    if output_format == "tsv":
        if rows_by_source.get("object") and any(
            artifact.artifact_shape == "generic_pdf_answer_table"
            for artifact in artifacts
        ):
            return "object"
        return "artifact"
    if rows_by_source.get("object"):
        return "object"
    if rows_by_source.get("artifact"):
        return "artifact"
    return "artifact"


def build_flow_output_artifact_bundle(
    *,
    completed_steps: Sequence[Mapping[str, Any]],
    flow_name: str,
    flow_run_id: str | None = None,
    document_id: str | None = None,
    output_format: FlowOutputFormat | None = None,
) -> FlowOutputArtifactBundle:
    """Build the canonical projection bundle from completed flow steps."""

    artifacts = [
        artifact
        for step in completed_steps
        if (artifact := _build_artifact_from_step(step)) is not None
    ]
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
    warnings = [
        warning
        for artifact in artifacts
        for warning in artifact.warnings
    ]
    return FlowOutputArtifactBundle(
        flow_name=flow_name,
        flow_run_id=flow_run_id,
        document_id=document_id,
        artifacts=artifacts,
        field_catalog=field_catalog,
        default_row_source=_default_row_source_for_bundle(
            artifacts=artifacts,
            rows_by_source=rows_by_source,
            output_format=output_format,
        ),
        warnings=warnings,
    )


def default_projection_plan(
    bundle: FlowOutputArtifactBundle,
    *,
    output_format: FlowOutputFormat,
    row_source: FlowOutputRowSource | None = None,
) -> FlowOutputProjectionPlan:
    selected_row_source = row_source or bundle.default_row_source
    columns = default_columns_for_row_source(bundle, selected_row_source)
    return FlowOutputProjectionPlan(
        format=output_format,
        row_source=selected_row_source,
        columns=columns,
    )


def default_columns_for_row_source(
    bundle: FlowOutputArtifactBundle,
    row_source: FlowOutputRowSource,
) -> list[FlowOutputColumnSpec]:
    available = bundle.field_refs_for_source(row_source)
    if row_source == "artifact":
        priority = ARTIFACT_DEFAULT_FIELD_REFS
    elif row_source == "object":
        if any(
            artifact.artifact_shape == "generic_pdf_answer_table"
            for artifact in bundle.artifacts
        ):
            selected = [
                field_ref
                for field_ref in GENERIC_PDF_ANSWER_TABLE_FIELD_PRIORITY
                if field_ref in available
            ]
            if not selected:
                selected = [
                    field.ref
                    for field in bundle.field_catalog
                    if field.row_source == row_source
                    and field.ref.startswith("object.payload.")
                ][:12]
            return [
                FlowOutputColumnSpec(
                    key=field_ref.removeprefix("object.payload."),
                    header=_field_label(field_ref),
                    field_ref=field_ref,
                )
                for field_ref in selected
            ]
        priority = OBJECT_DEFAULT_FIELD_PRIORITY
    elif row_source == "evidence":
        priority = EVIDENCE_DEFAULT_FIELD_PRIORITY
    else:
        priority = VALIDATION_DEFAULT_FIELD_PRIORITY

    selected = [field_ref for field_ref in priority if field_ref in available]
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


def validate_projection_plan(
    bundle: FlowOutputArtifactBundle,
    plan: FlowOutputProjectionPlan,
) -> tuple[list[str], list[str], list[FlowOutputColumnSpec]]:
    """Validate a projection plan and return errors, warnings, and concrete columns."""

    errors: list[str] = []
    warnings: list[str] = list(bundle.warnings)
    rows = bundle.rows_for_source(plan.row_source)
    synthetic_literal_row = not rows and projection_plan_allows_empty_bundle(plan)
    if not rows and not synthetic_literal_row:
        errors.append(f"Row source '{plan.row_source}' is not available for this flow output.")
    if synthetic_literal_row:
        warnings.append(
            "Projected one literal-only row because no upstream flow artifacts were available."
        )

    if plan.max_rows is not None and (plan.max_rows < 1 or plan.max_rows > MAX_PROJECTION_ROWS):
        errors.append(f"max_rows must be between 1 and {MAX_PROJECTION_ROWS}.")

    available_refs = bundle.field_refs_for_source(plan.row_source)
    columns = plan.columns or default_columns_for_row_source(bundle, plan.row_source)
    if not columns:
        errors.append(f"No columns are available for row source '{plan.row_source}'.")

    seen_keys: set[str] = set()
    for index, column in enumerate(columns, start=1):
        if not column.key.strip():
            errors.append(f"Column {index} has an empty key.")
        if column.key in seen_keys:
            errors.append(f"Duplicate output column key '{column.key}'.")
        seen_keys.add(column.key)
        if column.transform is None:
            _validate_ref(
                field_ref=column.field_ref,
                available_refs=available_refs,
                errors=errors,
                context=f"Column '{column.key}'",
            )
        else:
            for ref in _transform_refs(column.transform):
                _validate_ref(
                    field_ref=ref,
                    available_refs=available_refs,
                    errors=errors,
                    context=f"Column '{column.key}' transform",
                )
            if column.transform.type == "literal" and column.transform.value is None:
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
) -> Any:
    if transform.type == "literal":
        return transform.value
    if transform.type == "first_non_empty":
        for field_ref in transform.field_refs:
            value = row.get(field_ref)
            if not _is_empty(value):
                return value
        return missing_value
    if transform.type == "concat":
        parts: list[str] = []
        for value in transform.values:
            if isinstance(value, Mapping) and isinstance(value.get("field_ref"), str):
                part = row.get(str(value["field_ref"]))
            elif isinstance(value, str) and value in row:
                part = row.get(value)
            else:
                part = value
            if not _is_empty(part):
                parts.append(str(part))
        return transform.separator.join(parts)
    if transform.type == "join_list":
        value = row.get(transform.field_ref or "")
        if isinstance(value, list):
            return transform.separator.join(str(item) for item in value if not _is_empty(item))
        return missing_value if _is_empty(value) else str(value)
    if transform.type == "count":
        value = row.get(transform.field_ref or "")
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return 0 if _is_empty(value) else 1
    if transform.type == "map_value":
        value = row.get(transform.field_ref or "")
        key = str(value)
        if key in transform.mapping:
            return transform.mapping[key]
        return transform.default if transform.default is not None else missing_value
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
    missing_value: str,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for column in columns:
        if column.transform is not None:
            value = _transform_value(row, column.transform, missing_value=missing_value)
        else:
            value = row.get(column.field_ref or "")
        if _is_empty(value):
            value = missing_value
        output[column.key] = _jsonable(value)
    return output


def _group_projected_rows(
    source_rows: Sequence[Mapping[str, Any]],
    projected_rows: Sequence[Mapping[str, Any]],
    group_by: Sequence[str],
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for source_row, projected_row in zip(source_rows, projected_rows):
        key = tuple(source_row.get(field_ref) for field_ref in group_by)
        if key not in groups:
            groups[key] = {
                "group": {
                    field_ref: _jsonable(source_row.get(field_ref))
                    for field_ref in group_by
                },
                "rows": [],
            }
        groups[key]["rows"].append(dict(projected_row))
    return list(groups.values())


def apply_projection_plan(
    bundle: FlowOutputArtifactBundle,
    plan: FlowOutputProjectionPlan,
) -> FlowOutputProjectionResult:
    errors, warnings, columns = validate_projection_plan(bundle, plan)
    if errors:
        raise ValueError("; ".join(errors))

    rows = bundle.rows_for_source(plan.row_source)
    if not rows and projection_plan_allows_empty_bundle(plan):
        rows = [{}]
    for filter_spec in plan.filters:
        rows = [row for row in rows if _row_matches_filter(row, filter_spec)]
    rows = _sort_rows(rows, plan.sort)

    total_count = len(rows)
    max_rows = plan.max_rows or MAX_PROJECTION_ROWS
    limited_rows = rows[:max_rows]
    truncated = len(rows) > len(limited_rows)
    projected_rows = [
        _project_row(row, columns, missing_value=plan.missing_value)
        for row in limited_rows
    ]

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
                    field.model_dump(mode="json")
                    for field in bundle.field_catalog
                    if field.row_source == plan.row_source
                ],
                "rows": projected_rows,
                "warnings": warnings,
            }
        else:
            json_data = projected_rows
    elif plan.format == "chat":
        source_chat_rows = limited_rows[:MAX_CHAT_ROWS]
        chat_rows = projected_rows[:MAX_CHAT_ROWS]
        chat_truncated = truncated or len(projected_rows) > len(chat_rows)
        if plan.group_by:
            chat_output = render_grouped_chat_projection(
                groups=_group_projected_rows(source_chat_rows, chat_rows, plan.group_by),
                columns=columns,
                layout=plan.chat_layout,
                total_count=total_count,
                truncated=chat_truncated,
            )
        else:
            chat_output = render_chat_projection(
                rows=chat_rows,
                columns=columns,
                layout=plan.chat_layout,
                total_count=total_count,
                truncated=chat_truncated,
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
    )


def inspect_output_artifacts(
    bundle: FlowOutputArtifactBundle,
    *,
    example_limit: int = 3,
) -> dict[str, Any]:
    """Return a bounded planner inventory of artifacts, row sources, and fields."""

    row_sources: dict[str, Any] = {}
    for row_source in ("artifact", "object", "evidence", "validation_finding"):
        rows = bundle.rows_for_source(row_source)  # type: ignore[arg-type]
        bounded_example_limit = max(0, min(example_limit, MAX_PLANNER_LIST_ITEMS))
        row_sources[row_source] = {
            "row_count": len(rows),
            "default_columns": [
                column.model_dump(mode="json")
                for column in default_columns_for_row_source(bundle, row_source)  # type: ignore[arg-type]
            ],
            "examples": [
                _bounded_planner_row(row)
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
        "warnings": _bounded_planner_warnings(bundle.warnings),
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
    preview_plan = plan.model_copy(update={"max_rows": min(limit, plan.max_rows or limit)})
    result = apply_projection_plan(bundle, preview_plan)
    return FlowOutputProjectionPreview(
        status="ok",
        warnings=_bounded_planner_warnings(result.warnings),
        preview_rows=[_bounded_planner_row(row) for row in result.rows[:limit]],
        total_count=result.total_count,
        truncated=result.truncated,
    )


def finalize_output_projection(
    bundle: FlowOutputArtifactBundle,
    plan: FlowOutputProjectionPlan,
) -> FlowOutputProjectionResult:
    return apply_projection_plan(bundle, plan)


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
    "FlowOutputProjectionPlan",
    "FlowOutputProjectionPreview",
    "FlowOutputProjectionResult",
    "FlowOutputSortSpec",
    "FlowOutputTransformSpec",
    "apply_projection_plan",
    "build_flow_output_artifact_bundle",
    "default_columns_for_row_source",
    "default_projection_plan",
    "finalize_output_projection",
    "inspect_output_artifacts",
    "projection_plan_allows_empty_bundle",
    "preview_output_projection",
    "render_grouped_chat_projection",
    "render_chat_projection",
    "validate_projection_plan",
]
