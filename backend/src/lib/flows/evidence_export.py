"""Shared builders for deterministic flow evidence export artifacts."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence

from sqlalchemy.orm import Session

from src.lib.curation_workspace.extraction_results import list_extraction_results
from src.lib.openai_agents.evidence_summary import (
    _EvidenceRegistry,
    extract_evidence_records_from_structured_result,
)
from src.schemas.curation_workspace import CurationExtractionResultRecord


EVIDENCE_EXPORT_FIELD_ORDER: tuple[str, ...] = (
    "entity",
    "verified_quote",
    "page",
    "section",
    "subsection",
    "chunk_id",
    "figure_reference",
    "agent_id",
    "step_number",
    "evidence_record_id",
)
_SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


class FlowEvidenceExportFormat(str, Enum):
    """Supported flow evidence export formats."""

    CSV = "csv"
    TSV = "tsv"
    JSON = "json"

    @property
    def content_type(self) -> str:
        if self is FlowEvidenceExportFormat.CSV:
            return "text/csv"
        if self is FlowEvidenceExportFormat.TSV:
            return "text/tab-separated-values"
        return "application/json"

    @property
    def delimiter(self) -> str | None:
        if self is FlowEvidenceExportFormat.CSV:
            return ","
        if self is FlowEvidenceExportFormat.TSV:
            return "\t"
        return None


@dataclass(frozen=True)
class FlowEvidenceExportArtifact:
    """Serializable export artifact for one flow-run evidence download."""

    content_type: str
    filename: str
    payload_text: str
    record_count: int


class FlowRunEvidenceExportNotFoundError(ValueError):
    """Raised when a requested flow run has no persisted extraction results."""


class FlowRunEvidenceExportPermissionError(PermissionError):
    """Raised when the caller cannot export a requested flow run."""


@dataclass(frozen=True)
class FlowEvidenceExportStep:
    """Evidence-export view for one persisted flow step."""

    step: int
    agent_id: str
    agent_name: str | None
    tool_name: str | None
    evidence_records: list[dict[str, Any]]


def resolve_authorized_flow_run_extraction_results(
    *,
    db: Session,
    flow_run_id: str,
    user_id: str,
) -> list[CurationExtractionResultRecord]:
    """Return persisted extraction results when the caller owns the flow run."""

    normalized_flow_run_id = str(flow_run_id or "").strip()
    if not normalized_flow_run_id:
        raise FlowRunEvidenceExportNotFoundError("Flow run not found")

    extraction_results = list_extraction_results(
        db=db,
        flow_run_id=normalized_flow_run_id,
    )
    if not extraction_results:
        raise FlowRunEvidenceExportNotFoundError(
            f"Flow run {normalized_flow_run_id} not found"
        )

    normalized_user_id = str(user_id or "").strip()
    owner_ids = {
        str(record.user_id or "").strip()
        for record in extraction_results
    }
    if owner_ids != {normalized_user_id}:
        raise FlowRunEvidenceExportPermissionError(
            "You do not have permission to access this flow run"
        )

    return extraction_results


def build_flow_evidence_export_artifact(
    *,
    flow_run_id: str,
    extraction_results: Sequence[CurationExtractionResultRecord],
    export_format: FlowEvidenceExportFormat,
) -> FlowEvidenceExportArtifact:
    """Build a stable CSV, TSV, or JSON export artifact for one flow run."""

    flow_name = _resolve_flow_name(extraction_results)
    unique_evidence_records = build_flow_evidence_records(extraction_results)
    evidence_steps = build_flow_evidence_steps(extraction_results)
    filename = (
        f"flow-{_safe_filename_fragment(flow_run_id)}-evidence.{export_format.value}"
    )

    if export_format is FlowEvidenceExportFormat.JSON:
        payload_text = _build_json_export_payload(
            flow_run_id=flow_run_id,
            flow_name=flow_name,
            total_evidence_records=len(unique_evidence_records),
            steps=evidence_steps,
        )
    else:
        delimiter = export_format.delimiter
        if delimiter is None:
            raise ValueError(f"Unsupported delimited export format: {export_format.value}")
        payload_text = _build_delimited_export_payload(
            evidence_rows=_build_tabular_evidence_rows(evidence_steps),
            delimiter=delimiter,
        )

    return FlowEvidenceExportArtifact(
        content_type=export_format.content_type,
        filename=filename,
        payload_text=payload_text,
        record_count=len(unique_evidence_records),
    )


def build_flow_evidence_records(
    extraction_results: Sequence[CurationExtractionResultRecord],
) -> list[dict[str, Any]]:
    """Extract and deduplicate canonical evidence rows from persisted results."""

    registry = _EvidenceRegistry()

    for extraction_result in extraction_results:
        registry.add_many(
            extract_evidence_records_from_structured_result(
                extraction_result.payload_json
            )
        )

    return registry.records()


def build_flow_evidence_steps(
    extraction_results: Sequence[CurationExtractionResultRecord],
) -> list[FlowEvidenceExportStep]:
    """Build step-scoped evidence groupings with per-step provenance preserved."""

    steps: list[FlowEvidenceExportStep] = []

    for index, extraction_result in enumerate(
        sorted(extraction_results, key=_sort_extraction_result_for_export),
        start=1,
    ):
        step_number = _resolve_step_number(extraction_result, fallback=index)
        local_registry = _EvidenceRegistry()
        local_registry.add_many(
            extract_evidence_records_from_structured_result(
                extraction_result.payload_json
            )
        )
        metadata = dict(extraction_result.metadata or {})
        steps.append(
            FlowEvidenceExportStep(
                step=step_number,
                agent_id=str(extraction_result.agent_key or "").strip() or "unknown_agent",
                agent_name=_optional_text(metadata.get("agent_name")),
                tool_name=_optional_text(metadata.get("tool_name")),
                evidence_records=local_registry.records(),
            )
        )

    return steps


def _build_json_export_payload(
    *,
    flow_run_id: str,
    flow_name: str,
    total_evidence_records: int,
    steps: Sequence[FlowEvidenceExportStep],
) -> str:
    payload = {
        "flow_run_id": flow_run_id,
        "flow_name": flow_name,
        "total_evidence_records": total_evidence_records,
        "steps": [
            {
                "step": step.step,
                "agent_id": step.agent_id,
                "agent_name": step.agent_name,
                "tool_name": step.tool_name,
                "evidence_count": len(step.evidence_records),
                "evidence_records": list(step.evidence_records),
            }
            for step in steps
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _build_delimited_export_payload(
    *,
    evidence_rows: Sequence[dict[str, Any]],
    delimiter: str,
) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(EVIDENCE_EXPORT_FIELD_ORDER),
        delimiter=delimiter,
        lineterminator="\n",
        extrasaction="ignore",
    )
    writer.writeheader()

    for evidence_record in evidence_rows:
        writer.writerow(
            {
                field: _serialize_tabular_value(evidence_record.get(field))
                for field in EVIDENCE_EXPORT_FIELD_ORDER
            }
        )

    return buffer.getvalue()


def _serialize_tabular_value(value: Any) -> str | int:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return value
    return str(value)


def _resolve_flow_name(
    extraction_results: Sequence[CurationExtractionResultRecord],
) -> str:
    for extraction_result in extraction_results:
        flow_name = _optional_text((extraction_result.metadata or {}).get("flow_name"))
        if flow_name is not None:
            return flow_name
    return "Flow run evidence export"


def _resolve_step_number(
    extraction_result: CurationExtractionResultRecord,
    *,
    fallback: int,
) -> int:
    raw_step = (extraction_result.metadata or {}).get("step")
    try:
        step_number = int(raw_step)
    except (TypeError, ValueError):
        return fallback
    return step_number if step_number > 0 else fallback


def _sort_extraction_result_for_export(
    extraction_result: CurationExtractionResultRecord,
) -> tuple[int, str, str]:
    created_at = extraction_result.created_at.isoformat()
    extraction_result_id = str(extraction_result.extraction_result_id or "")
    return (
        _resolve_step_number(extraction_result, fallback=10**9),
        created_at,
        extraction_result_id,
    )


def _optional_text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _build_tabular_evidence_rows(
    steps: Sequence[FlowEvidenceExportStep],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for step in steps:
        for evidence_record in step.evidence_records:
            rows.append(
                {
                    **evidence_record,
                    "agent_id": step.agent_id,
                    "step_number": step.step,
                }
            )
    return rows


def _safe_filename_fragment(value: str) -> str:
    normalized = _SAFE_FILENAME_PATTERN.sub("-", str(value or "").strip()).strip("-")
    if not normalized:
        raise ValueError("Flow run identifier cannot be empty after normalization")
    return normalized


__all__ = [
    "FlowEvidenceExportArtifact",
    "FlowEvidenceExportFormat",
    "FlowRunEvidenceExportNotFoundError",
    "FlowRunEvidenceExportPermissionError",
    "build_flow_evidence_export_artifact",
    "build_flow_evidence_records",
    "resolve_authorized_flow_run_extraction_results",
]
