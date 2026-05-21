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
class StagedAlleleFinding:
    """One retained allele finding staged by the model."""

    staged_id: str
    deterministic_key: str
    payload: StageAllelePaperEvidenceInput
    warnings: tuple[str, ...] = ()
    staged_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


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
    staged_findings: dict[str, StagedAlleleFinding] = field(default_factory=dict)
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
            evidence_ids.extend(finding.payload.evidence_record_ids)
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
    """Validate and stage one retained allele finding."""

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

    missing_fields: list[str] = []
    invalid_fields: list[str] = []
    if not stage_input.mention_text.strip():
        missing_fields.append("mention_text")
    if not stage_input.evidence_record_ids:
        missing_fields.append("evidence_record_ids")

    evidence_records: list[dict[str, Any]] = []
    unknown_evidence_ids: list[str] = []
    for evidence_id in stage_input.evidence_record_ids:
        record = state.verified_evidence_records.get(evidence_id)
        if record is None:
            unknown_evidence_ids.append(evidence_id)
        else:
            evidence_records.append(record)
    if unknown_evidence_ids:
        invalid_fields.append("evidence_record_ids")

    quote_mismatch = _quote_mismatch(stage_input, evidence_records)
    if quote_mismatch:
        invalid_fields.append("verified_quotes")

    location_mismatch = _location_mismatch(stage_input, evidence_records)
    if location_mismatch:
        invalid_fields.append(location_mismatch)

    deterministic_key = _stage_key(stage_input.mention_text, stage_input.evidence_record_ids)
    existing_id = state.staged_keys.get(deterministic_key)
    if existing_id is not None:
        existing = state.staged_findings[existing_id]
        return {
            "status": "staged",
            "staged_id": existing.staged_id,
            "idempotent": True,
            "mention_text": existing.payload.mention_text,
            "evidence_record_ids": list(existing.payload.evidence_record_ids),
            "warnings": list(existing.warnings),
        }

    conflicting = _conflicting_stage_id(state, stage_input)
    if conflicting:
        invalid_fields.append("mention_text")

    if missing_fields or invalid_fields:
        details: list[str] = []
        if unknown_evidence_ids:
            details.append(
                "Unknown evidence_record_ids: " + ", ".join(sorted(unknown_evidence_ids))
            )
        if quote_mismatch:
            details.append(quote_mismatch)
        if location_mismatch:
            details.append(f"{location_mismatch} does not match verified evidence")
        if conflicting:
            details.append(
                f"mention_text is already staged with a different evidence set as {conflicting}"
            )
        instructions = _repair_message(
            state,
            "invalid_stage_input",
            "Repair the staged allele finding input and resubmit one allele finding.",
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

    warnings = _stage_warnings(stage_input)
    staged_id = _staged_id(stage_input.mention_text, stage_input.evidence_record_ids)
    finding = StagedAlleleFinding(
        staged_id=staged_id,
        deterministic_key=deterministic_key,
        payload=stage_input,
        warnings=tuple(warnings),
    )
    state.staged_findings[staged_id] = finding
    state.staged_keys[deterministic_key] = staged_id
    state.warnings.extend(warnings)

    return {
        "status": "staged",
        "staged_id": staged_id,
        "idempotent": False,
        "mention_text": stage_input.mention_text,
        "evidence_record_ids": list(stage_input.evidence_record_ids),
        "verified_quotes": [
            record.get("verified_quote") for record in evidence_records
        ],
        "warnings": warnings,
    }


def finalize_allele_extraction_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Finalize staged allele findings into a backend-built curation envelope."""

    state = current_extraction_staging_state(required=True)
    assert state is not None

    if state.finalization_called:
        return _repair_response(
            state,
            missing_fields=[],
            invalid_fields=["finalize_allele_extraction"],
            repair_code="duplicate_finalization",
            repair_instructions=_repair_message(
                state,
                "duplicate_finalization",
                "finalize_allele_extraction must be called exactly once at the end.",
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

    envelope_payload = _build_allele_extraction_envelope(state, finalize_input)
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


def _stage_key(mention_text: str, evidence_record_ids: list[str]) -> str:
    payload = {
        "mention_text": mention_text.strip().casefold(),
        "evidence_record_ids": sorted(evidence_record_ids),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _staged_id(mention_text: str, evidence_record_ids: list[str]) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", mention_text.strip()).strip("_").lower()
    digest = _stage_key(mention_text, evidence_record_ids)[:12]
    return f"allele_stage_{slug[:32] or 'finding'}_{digest}"


def _looks_like_comma_joined_allele_list(value: str) -> bool:
    if "," not in value:
        return False
    parts = [part.strip() for part in value.split(",")]
    return len([part for part in parts if part]) > 1


def _conflicting_stage_id(
    state: ExtractionStagingState,
    stage_input: StageAllelePaperEvidenceInput,
) -> str | None:
    mention_key = stage_input.mention_text.casefold()
    for staged_id, finding in state.staged_findings.items():
        if finding.payload.mention_text.casefold() != mention_key:
            continue
        if set(finding.payload.evidence_record_ids) != set(stage_input.evidence_record_ids):
            return staged_id
    return None


def _quote_mismatch(
    stage_input: StageAllelePaperEvidenceInput,
    evidence_records: list[dict[str, Any]],
) -> str:
    if not stage_input.verified_quotes:
        return ""
    actual_quotes = {
        str(record.get("verified_quote") or "").strip()
        for record in evidence_records
        if str(record.get("verified_quote") or "").strip()
    }
    missing = [
        quote for quote in stage_input.verified_quotes if quote not in actual_quotes
    ]
    if not missing:
        return ""
    return "verified_quotes do not match verified evidence records"


def _location_mismatch(
    stage_input: StageAllelePaperEvidenceInput,
    evidence_records: list[dict[str, Any]],
) -> str:
    for field_name in ("page", "section", "chunk_id"):
        expected = getattr(stage_input, field_name)
        if expected in (None, ""):
            continue
        if any(record.get(field_name) == expected for record in evidence_records):
            continue
        return field_name
    return ""


def _stage_warnings(stage_input: StageAllelePaperEvidenceInput) -> list[str]:
    warnings: list[str] = []
    if not stage_input.associated_gene_symbol:
        warnings.append("Missing paper-supported associated_gene_symbol selector context.")
    if not stage_input.taxon_curie:
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


def _build_allele_extraction_envelope(
    state: ExtractionStagingState,
    finalize_input: FinalizeAlleleExtractionInput,
) -> dict[str, Any]:
    evidence_records = list(state.verified_evidence_records.values())
    evidence_index_by_id = {
        str(record.get("evidence_record_id")): index
        for index, record in enumerate(evidence_records)
    }
    curatable_objects: list[dict[str, Any]] = []
    evidence_quote_ref_by_id: dict[str, str] = {}

    for finding in state.staged_findings.values():
        stage_input = finding.payload
        reference_ref = f"reference_{finding.staged_id}"
        mention_ref = f"mention_{finding.staged_id}"
        association_ref = f"association_{finding.staged_id}"

        curatable_objects.append(
            _curatable_object(
                object_type="Reference",
                object_role="validated_reference",
                model_ref="ReferencePayload",
                pending_ref_id=reference_ref,
                payload=_reference_payload(stage_input.reference),
            )
        )
        curatable_objects.append(
            _curatable_object(
                object_type="AlleleMention",
                object_role="metadata_only",
                model_ref="AlleleMentionPayload",
                pending_ref_id=mention_ref,
                payload=_mention_payload(stage_input),
            )
        )

        evidence_refs: list[ObjectRef] = []
        metadata_refs: list[EnvelopeMetadataRef] = []
        for evidence_id in stage_input.evidence_record_ids:
            evidence_ref = evidence_quote_ref_by_id.get(evidence_id)
            if evidence_ref is None:
                evidence_ref = f"evidence_quote_{_safe_ref_suffix(evidence_id)}"
                evidence_quote_ref_by_id[evidence_id] = evidence_ref
                record = state.verified_evidence_records[evidence_id]
                curatable_objects.append(
                    _curatable_object(
                        object_type="EvidenceQuote",
                        object_role="metadata_only",
                        model_ref="EvidenceQuotePayload",
                        pending_ref_id=evidence_ref,
                        payload=_evidence_quote_payload(record),
                    )
                )
            evidence_refs.append(
                ObjectRef(pending_ref_id=evidence_ref, object_type="EvidenceQuote")
            )
            evidence_index = evidence_index_by_id.get(evidence_id)
            if evidence_index is not None:
                metadata_refs.append(
                    EnvelopeMetadataRef(
                        metadata_path=f"evidence_records[{evidence_index}]",
                        role="supporting_evidence",
                    )
                )

        curatable_objects.append(
            _curatable_object(
                object_type="AllelePaperEvidenceAssociation",
                object_role="curatable_unit",
                model_ref="AllelePaperEvidenceAssociationPayload",
                pending_ref_id=association_ref,
                payload={
                    "association_kind": "allele_paper_evidence",
                    "evidence_record_ids": list(stage_input.evidence_record_ids),
                },
                evidence_record_ids=list(stage_input.evidence_record_ids),
                object_refs=[
                    ObjectRef(pending_ref_id=reference_ref, object_type="Reference"),
                    ObjectRef(pending_ref_id=mention_ref, object_type="AlleleMention"),
                    *evidence_refs,
                ],
                metadata_refs=metadata_refs,
                metadata={
                    "object_role": "curatable_unit",
                    "write_behavior": {
                        "status": "blocked",
                        "reason": (
                            "Builder-generated allele paper/evidence associations "
                            "remain pending until durable reference and allele "
                            "materialization are verified."
                        ),
                    },
                    "builder": {
                        "run_id": state.run_id,
                        "staged_id": finding.staged_id,
                        "source_tool": state.builder.stage_tool,
                    },
                },
            )
        )

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


def _curatable_object(
    *,
    object_type: str,
    object_role: str,
    model_ref: str,
    pending_ref_id: str,
    payload: dict[str, Any],
    evidence_record_ids: list[str] | None = None,
    object_refs: list[ObjectRef] | None = None,
    metadata_refs: list[EnvelopeMetadataRef] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    obj = CuratableObjectEnvelope(
        object_type=object_type,
        object_role=object_role,
        model_ref=model_ref,
        pending_ref_id=pending_ref_id,
        definition_state=DefinitionState.IN_DEVELOPMENT,
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
    for finding in state.staged_findings.values():
        source_mentions = finding.payload.raw_mentions or [finding.payload.mention_text]
        for mention in source_mentions:
            mentions.append(
                MentionCandidate(
                    mention=mention,
                    entity_type="allele",
                    evidence_record_ids=list(finding.payload.evidence_record_ids),
                )
            )
    return mentions


def _normalization_notes(state: ExtractionStagingState) -> list[str]:
    notes: list[str] = []
    for finding in state.staged_findings.values():
        hint = finding.payload.normalized_hint
        if hint:
            notes.append(
                f"{finding.payload.mention_text}: paper-supplied normalized hint {hint}"
            )
    return notes


def _validator_target_count(
    state: ExtractionStagingState,
    objects: list[Mapping[str, Any]],
) -> int:
    target = state.builder.object_graph.validator_target
    count = 0
    for obj in objects:
        if obj.get("object_type") != target.object_type:
            continue
        payload = obj.get("payload")
        if isinstance(payload, Mapping) and _field_path_exists(payload, target.field_path):
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
    "activate_extraction_staging",
    "clear_extraction_staging",
    "current_extraction_staging_state",
    "enforce_builder_finalized_or_raise",
    "finalize_allele_extraction_payload",
    "finalized_ack_from_state",
    "finalized_envelope_from_state",
    "record_document_retrieval_call",
    "register_verified_evidence_record",
    "stage_allele_paper_evidence_payload",
]
