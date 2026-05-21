"""Run-scoped staging state for tool-authored extraction builders."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DefinitionState,
    EnvelopeMetadataRef,
    ObjectRef,
)
from src.schemas.domain_pack_metadata import DomainPackExtractionBuilder
from src.schemas.models.base import (
    AmbiguityRecord,
    EvidenceRecord,
    ExclusionRecord,
    ExtractionRunSummary,
    MentionCandidate,
)
from src.schemas.models.domain_envelope_extraction import (
    DomainEnvelopeExtractionResult,
    ExtractionEnvelopeMetadata,
)


class BuilderRuntimeError(RuntimeError):
    """Raised when a builder tool is called outside active staging state."""


class BuilderReferenceInput(BaseModel):
    """Optional paper/runtime reference metadata submitted with a staged finding."""

    model_config = ConfigDict(extra="forbid")

    reference_id: StrictStr | StrictInt | None = None
    curie: StrictStr | None = None
    pmid: StrictStr | None = None
    doi: StrictStr | None = None
    title: StrictStr | None = None
    document_id: StrictStr | None = None
    source_filename: StrictStr | None = None


class StageAllelePaperEvidenceInput(BaseModel):
    """Strict model-facing payload for one retained allele finding."""

    model_config = ConfigDict(extra="forbid")

    mention_text: StrictStr = Field(
        min_length=1,
        description="Exact allele or variant notation as written in the paper",
    )
    evidence_record_ids: list[StrictStr] = Field(
        min_length=1,
        description="Verified record_evidence IDs supporting this retained allele",
    )
    verified_quotes: list[StrictStr] = Field(default_factory=list)
    page: StrictInt | None = Field(default=None, ge=1)
    section: StrictStr | None = None
    chunk_id: StrictStr | None = None
    associated_gene_symbol: StrictStr | None = None
    taxon_curie: StrictStr | None = None
    normalized_hint: StrictStr | None = None
    reference: BuilderReferenceInput | None = None
    finding_notes: StrictStr | None = None
    raw_mentions: list[StrictStr] = Field(default_factory=list)

    @field_validator(
        "mention_text",
        "section",
        "chunk_id",
        "associated_gene_symbol",
        "taxon_curie",
        "normalized_hint",
        "finding_notes",
        mode="before",
    )
    @classmethod
    def _strip_optional_strings(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        return stripped or None

    @field_validator("evidence_record_ids", "verified_quotes", "raw_mentions", mode="before")
    @classmethod
    def _strip_string_lists(cls, value: Any) -> Any:
        if value is None:
            return []
        if not isinstance(value, list):
            return value
        return [
            stripped
            for item in value
            if isinstance(item, str) and (stripped := item.strip())
        ]

    @model_validator(mode="after")
    def _validate_mention_shape(self) -> "StageAllelePaperEvidenceInput":
        if _looks_like_comma_joined_allele_list(self.mention_text):
            raise ValueError(
                "mention_text must contain one allele/variant notation, not a comma-separated list"
            )
        return self


class BuilderExclusionInput(BaseModel):
    """Finalization record for one excluded allele-like candidate."""

    model_config = ConfigDict(extra="forbid")

    mention: StrictStr
    reason_code: StrictStr
    evidence_record_ids: list[StrictStr] = Field(default_factory=list)
    details: StrictStr | None = None


class BuilderAmbiguityInput(BaseModel):
    """Finalization record for one unresolved ambiguous candidate."""

    model_config = ConfigDict(extra="forbid")

    mention: StrictStr
    why_ambiguous: StrictStr
    recommended_followup: StrictStr | None = None
    evidence_record_ids: list[StrictStr] = Field(default_factory=list)


class FinalizeAlleleExtractionInput(BaseModel):
    """Strict model-facing payload for one final builder finalization."""

    model_config = ConfigDict(extra="forbid")

    summary: StrictStr = Field(min_length=1)
    candidate_count: int = Field(ge=0)
    kept_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    ambiguous_count: int = Field(ge=0)
    exclusions: list[BuilderExclusionInput] = Field(default_factory=list)
    ambiguities: list[BuilderAmbiguityInput] = Field(default_factory=list)
    notes: list[StrictStr] = Field(default_factory=list)

    @field_validator("summary", mode="before")
    @classmethod
    def _strip_summary(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        return value.strip()

    @field_validator("notes", mode="before")
    @classmethod
    def _strip_notes(cls, value: Any) -> Any:
        if value is None:
            return []
        if not isinstance(value, list):
            return value
        return [
            stripped
            for item in value
            if isinstance(item, str) and (stripped := item.strip())
        ]


@dataclass(frozen=True)
class StagedBuilderFinding:
    """One retained finding staged by the model through a builder tool."""

    staged_id: str
    deterministic_key: str
    payload: dict[str, Any]
    evidence_record_ids: tuple[str, ...]
    primary_field: str | None = None
    primary_value: str | None = None
    warnings: tuple[str, ...] = ()
    staged_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# Backward-compatible name for older imports/tests while the builder engine moves
# from allele-specific staging to shared YAML-driven staging.
StagedAlleleFinding = StagedBuilderFinding


@dataclass
class ExtractionStagingState:
    """Mutable state scoped to one specialist run by ContextVar."""

    agent_id: str
    specialist_name: str
    domain_pack_id: str
    domain_pack_version: str | None
    builder: DomainPackExtractionBuilder
    curation_output_type: Any = DomainEnvelopeExtractionResult
    run_id: str = field(default_factory=lambda: f"builder-run-{uuid.uuid4()}")
    document_retrieval_calls: list[dict[str, Any]] = field(default_factory=list)
    verified_evidence_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    staged_findings: dict[str, StagedBuilderFinding] = field(default_factory=dict)
    staged_keys: dict[str, str] = field(default_factory=dict)
    finalization_called: bool = False
    finalized_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    finalized_envelope: dict[str, Any] | None = None
    finalized_ack: dict[str, Any] | None = None
    finalized_object_count: int = 0
    validator_target_count: int = 0
    zero_validator_jobs_status: str | None = None

    @property
    def staged_count(self) -> int:
        return len(self.staged_findings)

    @property
    def staged_evidence_ids(self) -> list[str]:
        evidence_ids: list[str] = []
        for finding in self.staged_findings.values():
            evidence_ids.extend(finding.evidence_record_ids)
        return sorted(set(evidence_ids))

    def summary_metrics(self) -> dict[str, Any]:
        return {
            "builderEnabled": True,
            "builderRunId": self.run_id,
            "builderStageTool": self.builder.stage_tool,
            "builderFinalizeTool": self.builder.finalize_tool,
            "builderStagedCount": self.staged_count,
            "builderFinalizedCount": self.finalized_counts.get("kept_count", 0),
            "builderFinalizationCalled": self.finalization_called,
            "builderStagedEvidenceIds": self.staged_evidence_ids,
            "builderFinalizedObjectCount": self.finalized_object_count,
            "builderValidatorTargetCount": self.validator_target_count,
            "builderZeroValidatorJobsStatus": self.zero_validator_jobs_status,
            "builderWarnings": list(dict.fromkeys(self.warnings)),
        }


_CURRENT_STAGING_STATE: ContextVar[ExtractionStagingState | None] = ContextVar(
    "agr_ai_curation_extraction_staging_state",
    default=None,
)


def activate_extraction_staging(
    *,
    agent_id: str,
    specialist_name: str,
    domain_pack_id: str,
    domain_pack_version: str | None,
    builder: DomainPackExtractionBuilder,
    curation_output_type: Any = DomainEnvelopeExtractionResult,
) -> Token[ExtractionStagingState | None]:
    """Initialize run-scoped extraction staging state."""

    state = ExtractionStagingState(
        agent_id=agent_id,
        specialist_name=specialist_name,
        domain_pack_id=domain_pack_id,
        domain_pack_version=domain_pack_version,
        builder=builder,
        curation_output_type=curation_output_type,
    )
    return _CURRENT_STAGING_STATE.set(state)


def clear_extraction_staging(token: Token[ExtractionStagingState | None]) -> None:
    """Clear run-scoped extraction staging state."""

    _CURRENT_STAGING_STATE.reset(token)


def current_extraction_staging_state(
    *,
    required: bool = False,
) -> ExtractionStagingState | None:
    """Return the current run-scoped staging state."""

    state = _CURRENT_STAGING_STATE.get()
    if state is None and required:
        raise BuilderRuntimeError(
            "Extraction builder tool called without active run-scoped staging state"
        )
    return state


def record_document_retrieval_call(
    tool_name: str,
    tool_args: Mapping[str, Any] | None = None,
) -> None:
    """Record document coverage observed by streaming runtime."""

    state = current_extraction_staging_state()
    if state is None:
        return
    state.document_retrieval_calls.append(
        {
            "tool_name": str(tool_name or "").strip(),
            "tool_args": dict(tool_args or {}),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def register_verified_evidence_record(record: Mapping[str, Any]) -> None:
    """Register one verified record_evidence output in current staging state."""

    state = current_extraction_staging_state()
    if state is None:
        return
    evidence_id = str(record.get("evidence_record_id") or "").strip()
    if not evidence_id:
        return
    state.verified_evidence_records[evidence_id] = dict(record)


def stage_allele_paper_evidence_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and stage one retained allele finding.

    This remains the model-facing allele compatibility adapter. Shared staging,
    evidence checks, idempotency, and warning handling live in
    :func:`stage_extraction_builder_payload`.
    """

    state = current_extraction_staging_state(required=True)
    assert state is not None

    try:
        stage_input = StageAllelePaperEvidenceInput.model_validate(dict(payload))
    except ValidationError as exc:
        return _repair_response(
            state,
            missing_fields=_missing_required_fields(exc),
            invalid_fields=_invalid_fields(exc),
            repair_code="invalid_stage_input",
            repair_instructions=_repair_message(
                state,
                "invalid_stage_input",
                "Repair the staged allele finding input and resubmit one allele finding.",
            ),
        )

    return stage_extraction_builder_payload(
        stage_input.model_dump(mode="json", exclude_none=True)
    )


def stage_extraction_builder_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and stage one retained finding using YAML builder metadata."""

    state = current_extraction_staging_state(required=True)
    assert state is not None

    stage_payload, missing_stage_fields, invalid_stage_fields = _validate_stage_payload(
        state,
        payload,
    )
    missing_fields: list[str] = list(missing_stage_fields)
    invalid_fields: list[str] = list(invalid_stage_fields)
    evidence_field = state.builder.evidence_record_id_field
    evidence_record_ids = _payload_string_list(stage_payload.get(evidence_field))
    if not evidence_record_ids:
        missing_fields.append(evidence_field)
    evidence_records: list[dict[str, Any]] = []
    unknown_evidence_ids: list[str] = []
    for evidence_id in evidence_record_ids:
        record = state.verified_evidence_records.get(evidence_id)
        if record is None:
            unknown_evidence_ids.append(evidence_id)
        else:
            evidence_records.append(record)
    if unknown_evidence_ids:
        invalid_fields.append(evidence_field)

    quote_mismatch = _quote_mismatch(stage_payload, evidence_records)
    if quote_mismatch:
        invalid_fields.append("verified_quotes")

    location_mismatch = _location_mismatch(stage_payload, evidence_records)
    if location_mismatch:
        invalid_fields.append(location_mismatch)

    primary_field, primary_value = _primary_stage_value(state, stage_payload)
    deterministic_key = _stage_key_for_payload(
        state,
        stage_payload,
        evidence_record_ids,
    )
    existing_id = state.staged_keys.get(deterministic_key)
    if existing_id is not None:
        existing = state.staged_findings[existing_id]
        response = {
            "status": "staged",
            "staged_id": existing.staged_id,
            "idempotent": True,
            evidence_field: list(existing.evidence_record_ids),
            "warnings": list(existing.warnings),
        }
        if existing.primary_field and existing.primary_value:
            response[existing.primary_field] = existing.primary_value
        return response

    conflicting = _conflicting_stage_id(
        state,
        primary_field=primary_field,
        primary_value=primary_value,
        evidence_record_ids=evidence_record_ids,
    )
    if conflicting:
        invalid_fields.append(primary_field or "stage_payload")

    if missing_fields or invalid_fields:
        details: list[str] = []
        if unknown_evidence_ids:
            details.append(
                f"Unknown {evidence_field}: " + ", ".join(sorted(unknown_evidence_ids))
            )
        if quote_mismatch:
            details.append(quote_mismatch)
        if location_mismatch:
            details.append(f"{location_mismatch} does not match verified evidence")
        if conflicting:
            details.append(
                f"{primary_field or 'stage payload'} is already staged with a "
                f"different evidence set as {conflicting}"
            )
        instructions = _repair_message(
            state,
            "invalid_stage_input",
            "Repair the staged finding input and resubmit one retained finding.",
        )
        if details:
            instructions = f"{instructions} {' '.join(details)}"
        return _repair_response(
            state,
            missing_fields=missing_fields,
            invalid_fields=invalid_fields,
            repair_code="invalid_stage_input",
            repair_instructions=instructions,
        )

    warnings = _stage_warnings(state, stage_payload)
    staged_id = _staged_id(
        state,
        primary_value or state.builder.retained_unit,
        deterministic_key,
    )
    finding = StagedBuilderFinding(
        staged_id=staged_id,
        deterministic_key=deterministic_key,
        payload=dict(stage_payload),
        evidence_record_ids=tuple(evidence_record_ids),
        primary_field=primary_field,
        primary_value=primary_value,
        warnings=tuple(warnings),
    )
    state.staged_findings[staged_id] = finding
    state.staged_keys[deterministic_key] = staged_id
    state.warnings.extend(warnings)

    response = {
        "status": "staged",
        "staged_id": staged_id,
        "idempotent": False,
        evidence_field: list(evidence_record_ids),
        "verified_quotes": [
            record.get("verified_quote") for record in evidence_records
        ],
        "warnings": warnings,
    }
    if primary_field and primary_value:
        response[primary_field] = primary_value
    return response


def finalize_allele_extraction_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Finalize staged allele findings into a backend-built curation envelope."""

    return finalize_extraction_builder_payload(payload)


def finalize_extraction_builder_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Finalize staged builder findings into a backend-built curation envelope."""

    state = current_extraction_staging_state(required=True)
    assert state is not None

    if state.finalization_called:
        return _repair_response(
            state,
            missing_fields=[],
            invalid_fields=[state.builder.finalize_tool],
            repair_code="duplicate_finalization",
            repair_instructions=_repair_message(
                state,
                "duplicate_finalization",
                f"{state.builder.finalize_tool} must be called exactly once at the end.",
            ),
        )

    try:
        finalize_input = FinalizeAlleleExtractionInput.model_validate(dict(payload))
    except ValidationError as exc:
        return _repair_response(
            state,
            missing_fields=_missing_required_fields(exc),
            invalid_fields=_invalid_fields(exc),
            repair_code="invalid_finalize_input",
            repair_instructions=_repair_message(
                state,
                "invalid_finalize_input",
                "Repair finalization counts and reason records, then call finalization once.",
            ),
        )

    repair_errors = _finalization_repair_errors(state, finalize_input)
    if repair_errors:
        return _repair_response(
            state,
            missing_fields=[],
            invalid_fields=repair_errors,
            repair_code="invalid_finalization",
            repair_instructions=(
                _repair_message(
                    state,
                    "invalid_finalization",
                    "Repair finalization state before ending the run.",
                )
                + " "
                + " ".join(repair_errors)
            ),
        )

    envelope_payload = _build_extraction_envelope(state, finalize_input)
    output_type = state.curation_output_type or DomainEnvelopeExtractionResult
    validated = output_type.model_validate(envelope_payload)
    finalized_envelope = validated.model_dump(mode="json")

    state.finalization_called = True
    state.finalized_counts = {
        "candidate_count": finalize_input.candidate_count,
        "kept_count": finalize_input.kept_count,
        "excluded_count": finalize_input.excluded_count,
        "ambiguous_count": finalize_input.ambiguous_count,
    }
    state.finalized_envelope = finalized_envelope
    state.finalized_object_count = len(finalized_envelope.get("curatable_objects") or [])
    state.validator_target_count = _validator_target_count(
        state,
        finalized_envelope.get("curatable_objects") or [],
    )
    state.zero_validator_jobs_status = (
        "empty_finalized_output"
        if state.finalized_object_count == 0
        else None
    )
    state.finalized_ack = {
        "status": "complete",
        "finalized_run_id": state.run_id,
        "summary": finalize_input.summary,
        "staged_count": state.staged_count,
        "finalized_count": finalize_input.kept_count,
    }

    return {
        "status": "finalized",
        "finalized_run_id": state.run_id,
        "staged_count": state.staged_count,
        "finalized_count": finalize_input.kept_count,
        "finalized_object_count": state.finalized_object_count,
        "validator_target_count": state.validator_target_count,
        "warnings": list(dict.fromkeys(state.warnings)),
        "final_ack": dict(state.finalized_ack),
    }


def finalized_envelope_from_state() -> dict[str, Any] | None:
    """Return finalized backend curation output for the current run."""

    state = current_extraction_staging_state()
    if state is None or state.finalized_envelope is None:
        return None
    return dict(state.finalized_envelope)


def finalized_ack_from_state() -> dict[str, Any] | None:
    """Return model-facing ack derived from finalized state, if available."""

    state = current_extraction_staging_state()
    if state is None or state.finalized_ack is None:
        return None
    return dict(state.finalized_ack)


def enforce_builder_finalized_or_raise() -> None:
    """Fail if a builder run did not successfully finalize staged state."""

    state = current_extraction_staging_state()
    if state is None:
        return
    if state.finalization_called and state.finalized_envelope is not None:
        return
    raise BuilderRuntimeError(
        f"{state.specialist_name} did not call {state.builder.finalize_tool} "
        "successfully before finishing."
    )


def _repair_response(
    state: ExtractionStagingState,
    *,
    missing_fields: list[str],
    invalid_fields: list[str],
    repair_code: str,
    repair_instructions: str,
) -> dict[str, Any]:
    return {
        "status": "needs_repair",
        "repair_code": repair_code,
        "repair_instructions": repair_instructions,
        "missing_fields": sorted(set(missing_fields)),
        "invalid_fields": sorted(set(invalid_fields)),
        "field_hints": _field_hints(state.builder),
        "examples": dict(state.builder.examples),
    }


def _field_hints(builder: DomainPackExtractionBuilder) -> dict[str, str]:
    return {
        name: field.hint
        for name, field in builder.fields.items()
        if field.hint
    }


def _repair_message(
    state: ExtractionStagingState,
    code: str,
    default: str,
) -> str:
    message = state.builder.repair_messages.get(code)
    return message or default


def _missing_required_fields(exc: ValidationError) -> list[str]:
    missing: list[str] = []
    for error in exc.errors():
        if error.get("type") == "missing" and error.get("loc"):
            missing.append(str(error["loc"][0]))
    return missing


def _invalid_fields(exc: ValidationError) -> list[str]:
    invalid: list[str] = []
    for error in exc.errors():
        if error.get("type") == "missing":
            continue
        if error.get("loc"):
            invalid.append(str(error["loc"][0]))
            continue
        message = str(error.get("msg") or "")
        if "mention_text" in message:
            invalid.append("mention_text")
    return invalid


def _validate_stage_payload(
    state: ExtractionStagingState,
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    raw = dict(payload)
    valid_fields = set(state.builder.fields)
    missing_fields: list[str] = []
    invalid_fields: list[str] = [
        field_name for field_name in raw if field_name not in valid_fields
    ]
    normalized: dict[str, Any] = {}

    for field_name, field in state.builder.fields.items():
        if field_name in raw:
            value = raw[field_name]
        elif field.default is not None:
            value = field.default
        else:
            value = None
        value, valid = _normalize_builder_field_value(value, field.json_type)
        if not valid:
            invalid_fields.append(field_name)
            continue
        if _builder_field_missing(value, min_items=field.min_items):
            if field.required:
                missing_fields.append(field_name)
            continue
        normalized[field_name] = value

    return normalized, missing_fields, invalid_fields


def _normalize_builder_field_value(value: Any, json_type: str) -> tuple[Any, bool]:
    if value is None:
        return None, True
    if json_type == "string":
        if not isinstance(value, str):
            return value, False
        stripped = value.strip()
        return (stripped or None), True
    if json_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return value, False
        return value, True
    if json_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return value, False
        return value, True
    if json_type == "boolean":
        return value, isinstance(value, bool)
    if json_type == "object":
        return value, isinstance(value, Mapping)
    if json_type == "array":
        if not isinstance(value, list):
            return value, False
        return _strip_builder_array(value), True
    return value, False


def _strip_builder_array(value: list[Any]) -> list[Any]:
    normalized: list[Any] = []
    for item in value:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                normalized.append(stripped)
            continue
        if item not in (None, ""):
            normalized.append(item)
    return normalized


def _builder_field_missing(value: Any, *, min_items: int | None = None) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        if min_items is not None and len(value) < min_items:
            return True
        return len(value) == 0
    if isinstance(value, Mapping):
        return not any(item not in (None, "", [], {}) for item in value.values())
    return False


def _payload_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _stage_key_for_payload(
    state: ExtractionStagingState,
    stage_payload: Mapping[str, Any],
    evidence_record_ids: list[str],
) -> str:
    evidence_field = state.builder.evidence_record_id_field
    dedupe_fields = _stage_dedupe_fields(state)
    key_payload: dict[str, Any] = {}
    for field_name in dedupe_fields:
        value = stage_payload.get(field_name)
        if field_name == evidence_field:
            value = sorted(evidence_record_ids)
        elif isinstance(value, str):
            value = value.strip().casefold()
        elif isinstance(value, list):
            value = sorted(value) if all(isinstance(item, str) for item in value) else value
        key_payload[field_name] = value
    return hashlib.sha256(
        json.dumps(key_payload, sort_keys=True, default=str).encode()
    ).hexdigest()


def _stage_dedupe_fields(state: ExtractionStagingState) -> list[str]:
    if state.builder.dedupe_fields:
        return list(state.builder.dedupe_fields)
    evidence_field = state.builder.evidence_record_id_field
    if "mention_text" in state.builder.fields:
        return ["mention_text", evidence_field]
    required_fields = [
        name
        for name, field in state.builder.fields.items()
        if field.required and name != evidence_field
    ]
    if required_fields:
        return [*required_fields, evidence_field]
    primary_field, _ = _primary_stage_value(state, {})
    return [field for field in (primary_field, evidence_field) if field]


def _staged_id(
    state: ExtractionStagingState,
    primary_value: str,
    deterministic_key: str,
) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", primary_value.strip()).strip("_").lower()
    digest = deterministic_key[:12]
    prefix = "allele_stage" if state.builder.stage_tool == "stage_allele_paper_evidence" else "builder_stage"
    return f"{prefix}_{slug[:32] or 'finding'}_{digest}"


def _looks_like_comma_joined_allele_list(value: str) -> bool:
    if "," not in value:
        return False
    parts = [part.strip() for part in value.split(",")]
    return len([part for part in parts if part]) > 1


def _conflicting_stage_id(
    state: ExtractionStagingState,
    *,
    primary_field: str | None,
    primary_value: str | None,
    evidence_record_ids: list[str],
) -> str | None:
    if not primary_field or not primary_value:
        return None
    primary_key = primary_value.casefold()
    for staged_id, finding in state.staged_findings.items():
        if finding.primary_field != primary_field:
            continue
        if not finding.primary_value or finding.primary_value.casefold() != primary_key:
            continue
        if set(finding.evidence_record_ids) != set(evidence_record_ids):
            return staged_id
    return None


def _quote_mismatch(
    stage_payload: Mapping[str, Any],
    evidence_records: list[dict[str, Any]],
) -> str:
    verified_quotes = _payload_string_list(stage_payload.get("verified_quotes"))
    if not verified_quotes:
        return ""
    actual_quotes = {
        str(record.get("verified_quote") or "").strip()
        for record in evidence_records
        if str(record.get("verified_quote") or "").strip()
    }
    missing = [
        quote for quote in verified_quotes if quote not in actual_quotes
    ]
    if not missing:
        return ""
    return "verified_quotes do not match verified evidence records"


def _location_mismatch(
    stage_payload: Mapping[str, Any],
    evidence_records: list[dict[str, Any]],
) -> str:
    for field_name in ("page", "section", "chunk_id"):
        expected = stage_payload.get(field_name)
        if expected in (None, ""):
            continue
        if any(record.get(field_name) == expected for record in evidence_records):
            continue
        return field_name
    return ""


def _primary_stage_value(
    state: ExtractionStagingState,
    stage_payload: Mapping[str, Any],
) -> tuple[str | None, str | None]:
    candidate_fields = [
        state.builder.primary_stage_field,
        "mention_text" if "mention_text" in state.builder.fields else None,
    ]
    candidate_fields.extend(
        name
        for name, field in state.builder.fields.items()
        if field.required
        and name != state.builder.evidence_record_id_field
        and field.json_type == "string"
    )
    candidate_fields.extend(
        name for name, field in state.builder.fields.items() if field.json_type == "string"
    )
    for field_name in candidate_fields:
        if not field_name:
            continue
        value = stage_payload.get(field_name)
        if isinstance(value, str) and value.strip():
            return field_name, value.strip()
    return None, None


def _stage_warnings(
    state: ExtractionStagingState,
    stage_payload: Mapping[str, Any],
) -> list[str]:
    warnings: list[str] = []
    if "associated_gene_symbol" in state.builder.fields and not stage_payload.get(
        "associated_gene_symbol"
    ):
        warnings.append("Missing paper-supported associated_gene_symbol selector context.")
    if "taxon_curie" in state.builder.fields and not stage_payload.get("taxon_curie"):
        warnings.append("Missing paper-supported taxon_curie selector context.")
    return warnings


def _finalization_repair_errors(
    state: ExtractionStagingState,
    finalize_input: FinalizeAlleleExtractionInput,
) -> list[str]:
    errors: list[str] = []
    if not state.document_retrieval_calls:
        errors.append("document retrieval tool was not called before finalization")
    if finalize_input.kept_count != state.staged_count:
        errors.append(
            f"kept_count {finalize_input.kept_count} does not match staged count "
            f"{state.staged_count}"
        )
    if finalize_input.excluded_count != len(finalize_input.exclusions):
        errors.append("excluded_count does not match exclusions length")
    if finalize_input.ambiguous_count != len(finalize_input.ambiguities):
        errors.append("ambiguous_count does not match ambiguities length")
    if finalize_input.candidate_count < (
        finalize_input.kept_count
        + finalize_input.excluded_count
        + finalize_input.ambiguous_count
    ):
        errors.append("candidate_count is smaller than kept+excluded+ambiguous counts")
    allowed_exclusions = set(state.builder.allowed_exclusion_reason_codes)
    for exclusion in finalize_input.exclusions:
        if allowed_exclusions and exclusion.reason_code not in allowed_exclusions:
            errors.append(f"unsupported exclusion reason_code {exclusion.reason_code}")
    return errors


def _build_extraction_envelope(
    state: ExtractionStagingState,
    finalize_input: FinalizeAlleleExtractionInput,
) -> dict[str, Any]:
    evidence_records = list(state.verified_evidence_records.values())
    evidence_index_by_id = {
        str(record.get("evidence_record_id")): index
        for index, record in enumerate(evidence_records)
    }
    curatable_objects: list[dict[str, Any]] = []
    global_object_refs: dict[tuple[str, str], ObjectRef] = {}

    for finding in state.staged_findings.values():
        stage_refs: dict[str, list[ObjectRef]] = {}
        object_rules = sorted(
            state.builder.object_graph.objects,
            key=lambda item: item.object_role == "curatable_unit",
        )
        for rule in object_rules:
            repeated_contexts = _object_rule_contexts(
                rule,
                finding=finding,
                state=state,
            )
            for context in repeated_contexts:
                object_key = _object_dedup_key(rule, context)
                if object_key in global_object_refs:
                    ref = global_object_refs[object_key]
                    stage_refs.setdefault(rule.object_type, []).append(ref)
                    continue
                pending_ref_id = _format_pending_ref(rule.pending_ref_template, context)
                payload = _payload_from_rule(rule.payload_fields, finding, context)
                metadata = _metadata_from_rule(rule, state=state, finding=finding)
                object_refs = _object_refs_from_rule(rule, stage_refs)
                metadata_refs = _metadata_refs_from_rule(
                    rule,
                    finding=finding,
                    evidence_index_by_id=evidence_index_by_id,
                    context=context,
                )
                obj = _curatable_object(
                    object_type=rule.object_type,
                    object_role=rule.object_role,
                    model_ref=rule.model_ref,
                    schema_ref=rule.schema_ref.model_dump(mode="json")
                    if rule.schema_ref
                    else None,
                    pending_ref_id=pending_ref_id,
                    payload=payload,
                    evidence_record_ids=_object_evidence_record_ids(
                        rule,
                        finding=finding,
                        context=context,
                    ),
                    object_refs=object_refs,
                    metadata_refs=metadata_refs,
                    metadata=metadata,
                    definition_state=rule.definition_state,
                    definition_notes=list(rule.definition_notes),
                )
                curatable_objects.append(obj)
                ref = ObjectRef(
                    pending_ref_id=pending_ref_id,
                    object_type=rule.object_type,
                )
                global_object_refs[object_key] = ref
                stage_refs.setdefault(rule.object_type, []).append(ref)

    metadata = ExtractionEnvelopeMetadata(
        raw_mentions=_raw_mentions(state),
        evidence_records=[EvidenceRecord.model_validate(record) for record in evidence_records],
        normalization_notes=_normalization_notes(state),
        exclusions=[
            ExclusionRecord.model_validate(exclusion.model_dump())
            for exclusion in finalize_input.exclusions
        ],
        ambiguities=[
            AmbiguityRecord.model_validate(ambiguity.model_dump())
            for ambiguity in finalize_input.ambiguities
        ],
        notes=[*finalize_input.notes, *list(dict.fromkeys(state.warnings))],
        provenance={
            "builder_enabled": True,
            "builder_run_id": state.run_id,
            "stage_tool": state.builder.stage_tool,
            "finalize_tool": state.builder.finalize_tool,
            "document_retrieval_calls": list(state.document_retrieval_calls),
        },
    )
    return {
        "summary": finalize_input.summary,
        "curatable_objects": curatable_objects,
        "metadata": metadata.model_dump(mode="json"),
        "run_summary": ExtractionRunSummary(
            candidate_count=finalize_input.candidate_count,
            kept_count=finalize_input.kept_count,
            excluded_count=finalize_input.excluded_count,
            ambiguous_count=finalize_input.ambiguous_count,
            warnings=list(dict.fromkeys(state.warnings)),
        ).model_dump(mode="json"),
    }


def _build_allele_extraction_envelope(
    state: ExtractionStagingState,
    finalize_input: FinalizeAlleleExtractionInput,
) -> dict[str, Any]:
    """Compatibility alias for callers/tests that still name the allele builder."""

    return _build_extraction_envelope(state, finalize_input)


def _object_rule_contexts(
    rule: Any,
    *,
    finding: StagedBuilderFinding,
    state: ExtractionStagingState,
) -> list[dict[str, Any]]:
    base_context = _base_object_context(finding, state)
    repeat_for = rule.repeat_for
    if repeat_for is None and "{evidence_record_id}" in rule.pending_ref_template:
        repeat_for = state.builder.evidence_record_id_field
    if repeat_for is None:
        return [base_context]

    repeat_values = _value_at_path(finding.payload, repeat_for)
    if repeat_for == state.builder.evidence_record_id_field:
        repeat_values = list(finding.evidence_record_ids)
    if not isinstance(repeat_values, list):
        repeat_values = [repeat_values] if repeat_values not in (None, "") else []

    contexts: list[dict[str, Any]] = []
    for value in repeat_values:
        if value in (None, ""):
            continue
        context = dict(base_context)
        if repeat_for == state.builder.evidence_record_id_field:
            evidence_id = str(value)
            evidence_record = state.verified_evidence_records.get(evidence_id, {})
            context["evidence_record"] = dict(evidence_record)
            context["evidence_record_id_raw"] = evidence_id
            context["evidence_record_id"] = _safe_ref_suffix(evidence_id)
            for key, item in evidence_record.items():
                context.setdefault(str(key), item)
        else:
            context[repeat_for.split(".")[-1]] = value
            context["repeat_value"] = value
        contexts.append(context)
    return contexts


def _base_object_context(
    finding: StagedBuilderFinding,
    state: ExtractionStagingState,
) -> dict[str, Any]:
    primary_slug = _safe_ref_suffix(finding.primary_value or finding.staged_id)
    context: dict[str, Any] = {
        "staged_id": finding.staged_id,
        "primary_slug": primary_slug,
        "builder_run_id": state.run_id,
    }
    for key, value in finding.payload.items():
        context[key] = value
    return context


def _object_dedup_key(rule: Any, context: Mapping[str, Any]) -> tuple[str, str]:
    object_type = str(rule.object_type)
    if rule.object_role == "curatable_unit":
        return (
            object_type,
            f"{context.get('staged_id') or ''}:{context.get('evidence_record_id_raw') or ''}",
        )
    if context.get("evidence_record_id_raw"):
        return object_type, str(context["evidence_record_id_raw"])
    return object_type, str(context.get("staged_id") or "")


def _format_pending_ref(template: str, context: Mapping[str, Any]) -> str:
    try:
        return template.format(**context)
    except KeyError as exc:
        missing = exc.args[0]
        raise BuilderRuntimeError(
            f"Builder pending_ref_template references unknown value '{missing}'"
        ) from exc


def _payload_from_rule(
    payload_fields: Mapping[str, str],
    finding: StagedBuilderFinding,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field_path, source in payload_fields.items():
        value = _resolve_builder_source(source, finding, context)
        if _builder_field_missing(value):
            continue
        _set_field_path(payload, field_path, value)
    return payload


def _resolve_builder_source(
    source: str,
    finding: StagedBuilderFinding,
    context: Mapping[str, Any],
) -> Any:
    if source.startswith("literal:"):
        return source.removeprefix("literal:")
    if source == "evidence_record_id":
        return context.get("evidence_record_id_raw") or context.get("evidence_record_id")
    if source in context:
        value = context[source]
        if source == "raw_mentions" and _builder_field_missing(value):
            return [finding.primary_value] if finding.primary_value else []
        return value
    evidence_record = context.get("evidence_record")
    if isinstance(evidence_record, Mapping):
        value = _value_at_path(evidence_record, source)
        if value is not None:
            return value
    value = _value_at_path(finding.payload, source)
    if source == "raw_mentions" and _builder_field_missing(value):
        return [finding.primary_value] if finding.primary_value else []
    return value


def _value_at_path(payload: Mapping[str, Any], field_path: str) -> Any:
    current: Any = payload
    for segment in field_path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return None
        current = current[segment]
    return current


def _set_field_path(payload: dict[str, Any], field_path: str, value: Any) -> None:
    current = payload
    segments = field_path.split(".")
    for segment in segments[:-1]:
        next_value = current.get(segment)
        if not isinstance(next_value, dict):
            next_value = {}
            current[segment] = next_value
        current = next_value
    current[segments[-1]] = value


def _metadata_from_rule(
    rule: Any,
    *,
    state: ExtractionStagingState,
    finding: StagedBuilderFinding,
) -> dict[str, Any]:
    metadata = dict(rule.metadata or {})
    metadata.setdefault("object_role", rule.object_role)
    if rule.object_role == "curatable_unit":
        metadata["builder"] = {
            "run_id": state.run_id,
            "staged_id": finding.staged_id,
            "source_tool": state.builder.stage_tool,
        }
    return metadata


def _object_refs_from_rule(
    rule: Any,
    stage_refs: Mapping[str, list[ObjectRef]],
) -> list[ObjectRef]:
    refs: list[ObjectRef] = []
    for ref_rule in rule.object_refs:
        candidates = list(stage_refs.get(ref_rule.object_type, []))
        if not ref_rule.collection:
            candidates = candidates[:1]
        refs.extend(candidates)
    return refs


def _metadata_refs_from_rule(
    rule: Any,
    *,
    finding: StagedBuilderFinding,
    evidence_index_by_id: Mapping[str, int],
    context: Mapping[str, Any],
) -> list[EnvelopeMetadataRef]:
    refs: list[EnvelopeMetadataRef] = []
    for evidence_id in _object_evidence_record_ids(
        rule,
        finding=finding,
        context=context,
    ):
        evidence_index = evidence_index_by_id.get(evidence_id)
        if evidence_index is not None:
            refs.append(
                EnvelopeMetadataRef(
                    metadata_path=f"evidence_records[{evidence_index}]",
                    role="supporting_evidence",
                )
            )
    for template in rule.metadata_refs:
        refs.append(
            EnvelopeMetadataRef(
                metadata_path=_format_pending_ref(template, context),
                role="builder_metadata",
            )
        )
    return refs


def _object_evidence_record_ids(
    rule: Any,
    *,
    finding: StagedBuilderFinding,
    context: Mapping[str, Any],
) -> list[str]:
    current_evidence_id = context.get("evidence_record_id_raw")
    if isinstance(current_evidence_id, str) and current_evidence_id.strip():
        return [current_evidence_id.strip()]
    if rule.object_role != "curatable_unit":
        return []
    return list(finding.evidence_record_ids)


def _curatable_object(
    *,
    object_type: str,
    object_role: str,
    model_ref: str | None,
    schema_ref: dict[str, Any] | None = None,
    pending_ref_id: str,
    payload: dict[str, Any],
    evidence_record_ids: list[str] | None = None,
    object_refs: list[ObjectRef] | None = None,
    metadata_refs: list[EnvelopeMetadataRef] | None = None,
    metadata: dict[str, Any] | None = None,
    definition_state: DefinitionState = DefinitionState.IN_DEVELOPMENT,
    definition_notes: list[str] | None = None,
) -> dict[str, Any]:
    obj = CuratableObjectEnvelope(
        object_type=object_type,
        object_role=object_role,
        schema_ref=schema_ref,
        model_ref=model_ref,
        pending_ref_id=pending_ref_id,
        definition_state=definition_state,
        definition_notes=definition_notes or [],
        payload=payload,
        evidence_record_ids=evidence_record_ids or [],
        object_refs=object_refs or [],
        metadata_refs=metadata_refs or [],
        metadata=metadata or {"object_role": object_role},
    )
    return obj.model_dump(mode="json")


def _reference_payload(reference: BuilderReferenceInput | None) -> dict[str, Any]:
    if reference is None:
        return {}
    payload = reference.model_dump(mode="json", exclude_none=True)
    if "reference_id" in payload and payload["reference_id"] is not None:
        payload["reference_id"] = str(payload["reference_id"])
    return payload


def _mention_payload(stage_input: StageAllelePaperEvidenceInput) -> dict[str, Any]:
    payload: dict[str, Any] = {"mention": {"text": stage_input.mention_text}}
    if stage_input.normalized_hint:
        payload["mention"]["normalized_hint"] = stage_input.normalized_hint
    if stage_input.associated_gene_symbol:
        payload["associated_gene"] = {"symbol": stage_input.associated_gene_symbol}
    if stage_input.taxon_curie:
        payload["taxon"] = {"curie": stage_input.taxon_curie}
    source_mentions = stage_input.raw_mentions or [stage_input.mention_text]
    payload["source_mentions"] = list(dict.fromkeys(source_mentions))
    return payload


def _evidence_quote_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "evidence_record_id",
        "verified_quote",
        "page",
        "section",
        "subsection",
        "chunk_id",
        "figure_reference",
    )
    return {
        key: record[key]
        for key in keys
        if key in record and record[key] not in (None, "")
    }


def _raw_mentions(state: ExtractionStagingState) -> list[MentionCandidate]:
    mentions: list[MentionCandidate] = []
    entity_type = state.domain_pack_id.rsplit(".", 1)[-1]
    for finding in state.staged_findings.values():
        source_mentions = finding.payload.get("raw_mentions")
        if not isinstance(source_mentions, list) or not source_mentions:
            source_mentions = [finding.primary_value] if finding.primary_value else []
        for mention in source_mentions:
            if not isinstance(mention, str) or not mention.strip():
                continue
            mentions.append(
                MentionCandidate(
                    mention=mention.strip(),
                    entity_type=entity_type,
                    evidence_record_ids=list(finding.evidence_record_ids),
                )
            )
    return mentions


def _normalization_notes(state: ExtractionStagingState) -> list[str]:
    notes: list[str] = []
    for finding in state.staged_findings.values():
        hint = finding.payload.get("normalized_hint")
        if hint:
            notes.append(
                f"{finding.primary_value or finding.staged_id}: "
                f"paper-supplied normalized hint {hint}"
            )
    return notes


def _validator_target_count(
    state: ExtractionStagingState,
    objects: list[Mapping[str, Any]],
) -> int:
    count = 0
    for target in state.builder.object_graph.validator_targets:
        for obj in objects:
            if obj.get("object_type") != target.object_type:
                continue
            payload = obj.get("payload")
            if isinstance(payload, Mapping) and _field_path_exists(
                payload,
                target.field_path,
            ):
                count += 1
    return count


def _field_path_exists(payload: Mapping[str, Any], field_path: str) -> bool:
    current: Any = payload
    for segment in field_path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return False
        current = current[segment]
    if current is None:
        return False
    if isinstance(current, str):
        return bool(current.strip())
    return True


def _safe_ref_suffix(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    if slug:
        return slug[:48]
    return hashlib.sha256(value.encode()).hexdigest()[:16]


__all__ = [
    "BuilderAmbiguityInput",
    "BuilderExclusionInput",
    "BuilderReferenceInput",
    "BuilderRuntimeError",
    "ExtractionStagingState",
    "FinalizeAlleleExtractionInput",
    "StageAllelePaperEvidenceInput",
    "StagedBuilderFinding",
    "activate_extraction_staging",
    "clear_extraction_staging",
    "current_extraction_staging_state",
    "enforce_builder_finalized_or_raise",
    "finalize_allele_extraction_payload",
    "finalize_extraction_builder_payload",
    "finalized_ack_from_state",
    "finalized_envelope_from_state",
    "record_document_retrieval_call",
    "register_verified_evidence_record",
    "stage_allele_paper_evidence_payload",
    "stage_extraction_builder_payload",
]
