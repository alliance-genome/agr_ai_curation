"""Deterministic post-agent pipeline for curation prep outputs."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping, Protocol, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.curation_adapters.reference import (
    REFERENCE_ADAPTER_KEY,
    ReferenceCandidateNormalizer,
)
from src.lib.curation_workspace.evidence_resolver import DeterministicEvidenceAnchorResolver
from src.lib.curation_workspace.evidence_quality import (
    evidence_anchor_payload_with_quality,
    summarize_evidence_records,
)
from src.lib.curation_workspace.models import (
    CurationExtractionResultRecord as ExtractionResultModel,
)
from src.lib.curation_workspace.session_service import (
    PreparedCandidateInput,
    PreparedDraftFieldInput,
    PreparedEvidenceRecordInput,
    PreparedSessionUpsertRequest,
    PreparedSessionUpsertResult,
    PreparedValidationSnapshotInput,
    upsert_prepared_session,
)
from src.lib.openai_agents.agents.curation_prep_agent import CURATION_PREP_AGENT_ID
from src.models.sql.database import SessionLocal
from src.schemas.curation_prep import (
    CurationPrepAgentOutput,
    CurationPrepCandidate,
    CurationPrepEvidenceReference,
)
from src.schemas.curation_workspace import (
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationEvidenceSource,
    CurationExtractionSourceKind,
    CurationSessionStatus,
    CurationValidationCounts,
    CurationValidationScope,
    CurationValidationSnapshotState,
    CurationValidationSummary,
    FieldValidationResult,
    FieldValidationStatus,
)


logger = logging.getLogger(__name__)

DEFAULT_ASYNC_CANDIDATE_THRESHOLD = 25
PREP_EVIDENCE_REFERENCES_METADATA_KEY = "prep_evidence_references"
PREP_UNRESOLVED_AMBIGUITIES_METADATA_KEY = "prep_unresolved_ambiguities"
NORMALIZER_METADATA_KEY = "normalizer"
EVIDENCE_SUMMARY_METADATA_KEY = "evidence_summary"


def _default_candidate_normalizers() -> Mapping[str, CurationCandidateNormalizer]:
    """Build the default adapter registry for candidate normalization."""

    return {
        REFERENCE_ADAPTER_KEY: ReferenceCandidateNormalizer(),
    }


class PipelineExecutionMode(str, Enum):
    """Dispatch mode for the deterministic post-agent pipeline."""

    AUTO = "auto"
    SYNC = "sync"
    ASYNC = "async"


class PipelineRunStatus(str, Enum):
    """High-level outcome of pipeline dispatch."""

    COMPLETED = "completed"
    SCHEDULED = "scheduled"


@dataclass(frozen=True)
class PostCurationPipelineRequest:
    """All context required to turn prep output into a review session."""

    prep_output: CurationPrepAgentOutput
    document_id: str
    source_kind: CurationExtractionSourceKind
    adapter_key: str | None = None
    profile_key: str | None = None
    flow_run_id: str | None = None
    origin_session_id: str | None = None
    trace_id: str | None = None
    user_id: str | None = None
    created_by_id: str | None = None
    assigned_curator_id: str | None = None
    notes: str | None = None
    tags: tuple[str, ...] = ()
    prepared_at: datetime | None = None
    review_session_id: str | None = None
    prep_extraction_result_id: str | None = None
    execution_mode: PipelineExecutionMode = PipelineExecutionMode.AUTO
    async_candidate_threshold: int = DEFAULT_ASYNC_CANDIDATE_THRESHOLD


@dataclass(frozen=True)
class PostCurationPipelineResult:
    """Result of synchronous execution or asynchronous scheduling."""

    status: PipelineRunStatus
    execution_mode: PipelineExecutionMode
    candidate_count: int
    session_id: str | None = None
    created: bool | None = None
    prep_extraction_result_id: str | None = None
    task_name: str | None = None


@dataclass(frozen=True)
class CandidateNormalizationContext:
    """Context passed into adapter-owned candidate normalizers."""

    document_id: str
    adapter_key: str
    profile_key: str | None
    prep_extraction_result_id: str
    candidate_index: int
    flow_run_id: str | None = None


@dataclass(frozen=True)
class NormalizedCandidate:
    """Normalized candidate output emitted by a domain adapter."""

    prep_candidate: CurationPrepCandidate
    normalized_payload: dict[str, Any]
    draft_fields: list[PreparedDraftFieldInput]
    display_label: str | None = None
    secondary_label: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceResolutionContext:
    """Context passed into evidence-anchor resolution implementations."""

    document_id: str
    adapter_key: str
    profile_key: str | None
    prep_extraction_result_id: str
    candidate_index: int


@dataclass(frozen=True)
class BatchValidationContext:
    """Context passed into batch-validation implementations."""

    document_id: str
    adapter_key: str
    profile_key: str | None
    validated_at: datetime


@dataclass(frozen=True)
class BatchValidationOutcome:
    """Prepared validation snapshots emitted by the validation service."""

    candidate_snapshots: list[PreparedValidationSnapshotInput]
    session_snapshot: PreparedValidationSnapshotInput


class CurationCandidateNormalizer(Protocol):
    """Adapter-owned candidate normalization contract."""

    def normalize(
        self,
        candidate: CurationPrepCandidate,
        *,
        context: CandidateNormalizationContext,
    ) -> NormalizedCandidate:
        """Normalize one prep candidate into deterministic draft/session payloads."""


class EvidenceAnchorResolver(Protocol):
    """Evidence-resolution contract used by the deterministic pipeline."""

    def resolve(
        self,
        candidate: CurationPrepCandidate,
        *,
        normalized_candidate: NormalizedCandidate,
        context: EvidenceResolutionContext,
    ) -> list[PreparedEvidenceRecordInput]:
        """Resolve or enrich evidence anchors for a normalized candidate."""


class BatchValidationService(Protocol):
    """Batch-validation contract used by the deterministic pipeline."""

    def validate(
        self,
        normalized_candidates: Sequence[NormalizedCandidate],
        *,
        context: BatchValidationContext,
    ) -> BatchValidationOutcome:
        """Produce candidate-level and session-level validation snapshots."""


class PipelineTaskScheduler(Protocol):
    """Background-task scheduler contract for async pipeline dispatch."""

    def schedule(self, task: Callable[[], None], *, task_name: str) -> str | None:
        """Schedule a synchronous task to run outside the request path."""


class PassthroughCandidateNormalizer:
    """Default adapter normalizer that preserves prep output verbatim."""

    def normalize(
        self,
        candidate: CurationPrepCandidate,
        *,
        context: CandidateNormalizationContext,
    ) -> NormalizedCandidate:
        normalized_payload = candidate.to_extracted_fields_dict()
        draft_fields = [
            PreparedDraftFieldInput(
                field_key=field.field_path,
                label=_humanize_path(field.field_path),
                value=field.to_python_value(),
                seed_value=field.to_python_value(),
                field_type=field.value_type.value,
                group_key=_field_group_key(field.field_path),
                group_label=_humanize_path(_field_group_key(field.field_path)),
                order=index,
                metadata={"source_field_path": field.field_path},
            )
            for index, field in enumerate(candidate.extracted_fields)
        ]
        display_values = _scalar_display_values(normalized_payload)
        display_label = display_values[0] if display_values else f"Candidate {context.candidate_index + 1}"
        secondary_label = display_values[1] if len(display_values) > 1 else None

        return NormalizedCandidate(
            prep_candidate=candidate,
            normalized_payload=normalized_payload,
            draft_fields=draft_fields,
            display_label=display_label,
            secondary_label=secondary_label,
            metadata={NORMALIZER_METADATA_KEY: type(self).__name__},
        )


class PassthroughEvidenceAnchorResolver:
    """Temporary evidence resolver that preserves prep anchors unchanged."""

    def resolve(
        self,
        candidate: CurationPrepCandidate,
        *,
        normalized_candidate: NormalizedCandidate,
        context: EvidenceResolutionContext,
    ) -> list[PreparedEvidenceRecordInput]:
        primary_fields: set[str] = set()
        resolved_records: list[PreparedEvidenceRecordInput] = []

        for reference in candidate.evidence_references:
            field_group_key = _field_group_key(reference.field_path)
            resolved_records.append(
                PreparedEvidenceRecordInput(
                    source=CurationEvidenceSource.EXTRACTED,
                    field_keys=[reference.field_path],
                    field_group_keys=[field_group_key] if field_group_key else [],
                    is_primary=reference.field_path not in primary_fields,
                    anchor=reference.anchor.model_dump(mode="json"),
                    warnings=[],
                )
            )
            primary_fields.add(reference.field_path)

        return resolved_records


class DeterministicStructuralValidationService:
    """Immediate structural validation until adapter validators ship downstream."""

    def validate(
        self,
        normalized_candidates: Sequence[NormalizedCandidate],
        *,
        context: BatchValidationContext,
    ) -> BatchValidationOutcome:
        session_counts = CurationValidationCounts()
        session_warnings = [
            "Deterministic structural validation completed; downstream adapter validation is pending."
        ]
        candidate_snapshots: list[PreparedValidationSnapshotInput] = []

        for normalized_candidate in normalized_candidates:
            candidate_counts = CurationValidationCounts()
            candidate_warnings: list[str] = []
            field_results: dict[str, FieldValidationResult] = {}

            for draft_field in normalized_candidate.draft_fields:
                field_status, field_warnings = _field_validation_status(draft_field.value)
                validation_result = FieldValidationResult(
                    status=field_status,
                    resolver="deterministic_post_agent_pipeline",
                    warnings=field_warnings,
                )
                field_results[draft_field.field_key] = validation_result
                _increment_validation_count(candidate_counts, field_status)
                _increment_validation_count(session_counts, field_status)
                candidate_warnings.extend(field_warnings)

            if normalized_candidate.prep_candidate.unresolved_ambiguities:
                candidate_warnings.append(
                    "Candidate contains unresolved ambiguities that require curator review."
                )

            candidate_summary = CurationValidationSummary(
                state=CurationValidationSnapshotState.COMPLETED,
                counts=candidate_counts,
                last_validated_at=context.validated_at,
                warnings=_dedupe(candidate_warnings),
            )
            candidate_snapshots.append(
                PreparedValidationSnapshotInput(
                    scope=CurationValidationScope.CANDIDATE,
                    adapter_key=normalized_candidate.prep_candidate.adapter_key,
                    state=CurationValidationSnapshotState.COMPLETED,
                    field_results=field_results,
                    summary=candidate_summary,
                    warnings=_dedupe(candidate_warnings),
                    requested_at=context.validated_at,
                    completed_at=context.validated_at,
                )
            )
            session_warnings.extend(candidate_warnings)

        session_summary = CurationValidationSummary(
            state=CurationValidationSnapshotState.COMPLETED,
            counts=session_counts,
            last_validated_at=context.validated_at,
            warnings=_dedupe(session_warnings),
        )
        session_snapshot = PreparedValidationSnapshotInput(
            scope=CurationValidationScope.SESSION,
            adapter_key=context.adapter_key,
            state=CurationValidationSnapshotState.COMPLETED,
            field_results={},
            summary=session_summary,
            warnings=_dedupe(session_warnings),
            requested_at=context.validated_at,
            completed_at=context.validated_at,
        )

        return BatchValidationOutcome(
            candidate_snapshots=candidate_snapshots,
            session_snapshot=session_snapshot,
        )


class AsyncioPipelineTaskScheduler:
    """Default scheduler backed by `asyncio.create_task`."""

    def schedule(self, task: Callable[[], None], *, task_name: str) -> str | None:
        async_task = asyncio.create_task(
            asyncio.to_thread(task),
            name=task_name,
        )
        async_task.add_done_callback(self._log_task_completion)
        return async_task.get_name()

    @staticmethod
    def _log_task_completion(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except Exception:  # pragma: no cover - log side effect only
            logger.exception(
                "Deterministic post-agent pipeline background task %s failed",
                task.get_name(),
            )


@dataclass(frozen=True)
class PostCurationPipelineDependencies:
    """Replaceable collaborators for normalization, evidence, validation, and async dispatch."""

    default_candidate_normalizer: CurationCandidateNormalizer = field(
        default_factory=PassthroughCandidateNormalizer
    )
    candidate_normalizers: Mapping[str, CurationCandidateNormalizer] = field(
        default_factory=_default_candidate_normalizers
    )
    evidence_resolver: EvidenceAnchorResolver = field(
        default_factory=DeterministicEvidenceAnchorResolver
    )
    validation_service: BatchValidationService = field(
        default_factory=DeterministicStructuralValidationService
    )
    task_scheduler: PipelineTaskScheduler = field(
        default_factory=AsyncioPipelineTaskScheduler
    )


async def run_post_curation_pipeline(
    request: PostCurationPipelineRequest,
    *,
    db: Session | None = None,
    dependencies: PostCurationPipelineDependencies | None = None,
) -> PostCurationPipelineResult:
    """Run immediately for small payloads or schedule background execution for larger ones."""

    dependencies = dependencies or PostCurationPipelineDependencies()
    execution_mode = _select_execution_mode(request)
    if execution_mode == PipelineExecutionMode.SYNC:
        return execute_post_curation_pipeline(
            request,
            db=db,
            dependencies=dependencies,
        )

    task_name = _task_name_for_request(request)
    scheduled_task_name = dependencies.task_scheduler.schedule(
        lambda: execute_post_curation_pipeline(
            request,
            dependencies=dependencies,
        ),
        task_name=task_name,
    )
    return PostCurationPipelineResult(
        status=PipelineRunStatus.SCHEDULED,
        execution_mode=PipelineExecutionMode.ASYNC,
        candidate_count=len(request.prep_output.candidates),
        prep_extraction_result_id=request.prep_extraction_result_id,
        task_name=scheduled_task_name,
    )


def execute_post_curation_pipeline(
    request: PostCurationPipelineRequest,
    *,
    db: Session | None = None,
    dependencies: PostCurationPipelineDependencies | None = None,
) -> PostCurationPipelineResult:
    """Execute the deterministic post-agent pipeline synchronously."""

    dependencies = dependencies or PostCurationPipelineDependencies()
    owns_session = db is None
    session = db or SessionLocal()

    try:
        persistence_result, prep_extraction_result_id = _execute_pipeline_steps(
            session,
            request,
            dependencies,
        )
        if owns_session:
            session.commit()
        return PostCurationPipelineResult(
            status=PipelineRunStatus.COMPLETED,
            execution_mode=PipelineExecutionMode.SYNC,
            candidate_count=len(request.prep_output.candidates),
            session_id=persistence_result.session_id,
            created=persistence_result.created,
            prep_extraction_result_id=prep_extraction_result_id,
        )
    except Exception:
        if owns_session and session.in_transaction():
            session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def _execute_pipeline_steps(
    db: Session,
    request: PostCurationPipelineRequest,
    dependencies: PostCurationPipelineDependencies,
) -> tuple[PreparedSessionUpsertResult, str]:
    prep_extraction_result = _resolve_prep_extraction_result(db, request)
    adapter_key = _resolve_pipeline_adapter_key(request)
    profile_key = _resolve_pipeline_profile_key(request)

    normalized_candidates: list[NormalizedCandidate] = []
    evidence_records_by_candidate: list[list[PreparedEvidenceRecordInput]] = []

    for candidate_index, candidate in enumerate(request.prep_output.candidates):
        _validate_candidate_scope(
            candidate,
            adapter_key=adapter_key,
            profile_key=profile_key,
        )
        normalizer = dependencies.candidate_normalizers.get(
            candidate.adapter_key,
            dependencies.default_candidate_normalizer,
        )
        normalized_candidate = normalizer.normalize(
            candidate,
            context=CandidateNormalizationContext(
                document_id=request.document_id,
                adapter_key=adapter_key,
                profile_key=profile_key,
                prep_extraction_result_id=str(prep_extraction_result.id),
                candidate_index=candidate_index,
                flow_run_id=request.flow_run_id,
            ),
        )
        normalized_candidates.append(normalized_candidate)
        resolved_records = dependencies.evidence_resolver.resolve(
            candidate,
            normalized_candidate=normalized_candidate,
            context=EvidenceResolutionContext(
                document_id=request.document_id,
                adapter_key=adapter_key,
                profile_key=profile_key,
                prep_extraction_result_id=str(prep_extraction_result.id),
                candidate_index=candidate_index,
            ),
        )
        evidence_records_by_candidate.append(
            [
                replace(
                    record,
                    anchor=evidence_anchor_payload_with_quality(record.anchor),
                )
                for record in resolved_records
            ]
        )

    validated_at = request.prepared_at or datetime.now(timezone.utc)
    validation_outcome = dependencies.validation_service.validate(
        normalized_candidates,
        context=BatchValidationContext(
            document_id=request.document_id,
            adapter_key=adapter_key,
            profile_key=profile_key,
            validated_at=validated_at,
        ),
    )

    if len(validation_outcome.candidate_snapshots) != len(normalized_candidates):
        raise ValueError(
            "Validation service must return one candidate snapshot per normalized candidate"
        )

    prepared_candidates = [
        _prepared_candidate_input(
            normalized_candidate=normalized_candidate,
            evidence_records=evidence_records_by_candidate[index],
            validation_snapshot=validation_outcome.candidate_snapshots[index],
            prep_extraction_result_id=str(prep_extraction_result.id),
            prep_output=request.prep_output,
            candidate_index=index,
        )
        for index, normalized_candidate in enumerate(normalized_candidates)
    ]

    session_warnings = _dedupe(
        [
            *request.prep_output.run_metadata.warnings,
            *validation_outcome.session_snapshot.warnings,
        ]
    )

    persistence_result = upsert_prepared_session(
        db,
        PreparedSessionUpsertRequest(
            document_id=request.document_id,
            adapter_key=adapter_key,
            profile_key=profile_key,
            review_session_id=request.review_session_id,
            flow_run_id=request.flow_run_id,
            created_by_id=request.created_by_id,
            assigned_curator_id=request.assigned_curator_id,
            notes=request.notes,
            tags=list(request.tags),
            warnings=session_warnings,
            prepared_at=validated_at,
            status=CurationSessionStatus.NEW,
            candidates=prepared_candidates,
            session_validation_snapshot=validation_outcome.session_snapshot,
        ),
        manage_transaction=False,
    )
    return persistence_result, str(prep_extraction_result.id)


def _prepared_candidate_input(
    *,
    normalized_candidate: NormalizedCandidate,
    evidence_records: Sequence[PreparedEvidenceRecordInput],
    validation_snapshot: PreparedValidationSnapshotInput,
    prep_extraction_result_id: str,
    prep_output: CurationPrepAgentOutput,
    candidate_index: int,
) -> PreparedCandidateInput:
    prep_candidate = normalized_candidate.prep_candidate
    metadata = dict(normalized_candidate.metadata)
    evidence_summary = summarize_evidence_records(evidence_records)
    metadata[PREP_EVIDENCE_REFERENCES_METADATA_KEY] = [
        _serialize_evidence_reference(reference)
        for reference in prep_candidate.evidence_references
    ]
    metadata[PREP_UNRESOLVED_AMBIGUITIES_METADATA_KEY] = [
        ambiguity.model_dump(mode="json")
        for ambiguity in prep_candidate.unresolved_ambiguities
    ]
    metadata["prep_candidate_index"] = candidate_index
    if evidence_summary is not None:
        metadata[EVIDENCE_SUMMARY_METADATA_KEY] = evidence_summary.model_dump(mode="json")

    return PreparedCandidateInput(
        source=CurationCandidateSource.EXTRACTED,
        status=CurationCandidateStatus.PENDING,
        order=candidate_index,
        adapter_key=prep_candidate.adapter_key,
        profile_key=prep_candidate.profile_key,
        display_label=normalized_candidate.display_label,
        secondary_label=normalized_candidate.secondary_label,
        confidence=prep_candidate.confidence,
        conversation_summary=prep_candidate.conversation_context_summary,
        unresolved_ambiguities=[
            _ambiguity_message(ambiguity)
            for ambiguity in prep_candidate.unresolved_ambiguities
        ],
        extraction_result_id=prep_extraction_result_id,
        normalized_payload=normalized_candidate.normalized_payload,
        metadata=metadata,
        draft_fields=normalized_candidate.draft_fields,
        draft_title=normalized_candidate.display_label,
        draft_summary=prep_candidate.conversation_context_summary,
        draft_metadata={
            "prep_run_metadata": prep_output.run_metadata.model_dump(mode="json"),
        },
        evidence_records=list(evidence_records),
        validation_snapshot=validation_snapshot,
    )


def _resolve_prep_extraction_result(
    db: Session,
    request: PostCurationPipelineRequest,
) -> ExtractionResultModel:
    if request.prep_extraction_result_id is not None:
        record_id = UUID(str(request.prep_extraction_result_id))
        record = db.scalars(
            select(ExtractionResultModel).where(ExtractionResultModel.id == record_id)
        ).first()
        if record is None:
            raise LookupError(
                f"Prep extraction result {request.prep_extraction_result_id} not found"
            )
        _validate_prep_extraction_result(record, request)
        return record

    statement = (
        select(ExtractionResultModel)
        .where(ExtractionResultModel.document_id == UUID(str(request.document_id)))
        .where(ExtractionResultModel.agent_key == CURATION_PREP_AGENT_ID)
        .where(ExtractionResultModel.source_kind == request.source_kind)
        .where(ExtractionResultModel.candidate_count == len(request.prep_output.candidates))
        .order_by(ExtractionResultModel.created_at.desc())
    )

    if request.adapter_key is not None:
        statement = statement.where(ExtractionResultModel.adapter_key == request.adapter_key)
    if request.profile_key is not None:
        statement = statement.where(ExtractionResultModel.profile_key == request.profile_key)
    if request.flow_run_id is not None:
        statement = statement.where(ExtractionResultModel.flow_run_id == request.flow_run_id)
    if request.origin_session_id is not None:
        statement = statement.where(
            ExtractionResultModel.origin_session_id == request.origin_session_id
        )
    if request.trace_id is not None:
        statement = statement.where(ExtractionResultModel.trace_id == request.trace_id)
    if request.user_id is not None:
        statement = statement.where(ExtractionResultModel.user_id == request.user_id)

    candidates_payload = request.prep_output.model_dump(mode="json")["candidates"]
    run_metadata_payload = request.prep_output.run_metadata.model_dump(mode="json")

    for record in db.scalars(statement).all():
        payload = record.payload_json if isinstance(record.payload_json, dict) else {}
        metadata = dict(record.extraction_metadata or {})
        if payload.get("candidates") != candidates_payload:
            continue
        if metadata.get("final_run_metadata") != run_metadata_payload:
            continue
        return record

    raise LookupError(
        "Unable to verify the persisted curation prep extraction result. "
        "Ensure run_curation_prep() completed with matching persistence context."
    )


def _validate_prep_extraction_result(
    record: ExtractionResultModel,
    request: PostCurationPipelineRequest,
) -> None:
    if record.agent_key != CURATION_PREP_AGENT_ID:
        raise ValueError("prep_extraction_result_id does not reference a curation prep result")
    if str(record.document_id) != str(request.document_id):
        raise ValueError("prep_extraction_result_id document_id does not match pipeline request")
    payload = record.payload_json if isinstance(record.payload_json, dict) else {}
    if payload.get("candidates") != request.prep_output.model_dump(mode="json")["candidates"]:
        raise ValueError("prep_extraction_result_id payload does not match the supplied prep output")
    metadata = dict(record.extraction_metadata or {})
    if metadata.get("final_run_metadata") != request.prep_output.run_metadata.model_dump(mode="json"):
        raise ValueError(
            "prep_extraction_result_id final run metadata does not match the supplied prep output"
        )


def _resolve_pipeline_adapter_key(request: PostCurationPipelineRequest) -> str:
    if request.adapter_key is not None:
        return request.adapter_key

    adapter_keys = sorted({candidate.adapter_key for candidate in request.prep_output.candidates})
    if len(adapter_keys) == 1:
        return adapter_keys[0]
    if not adapter_keys:
        raise ValueError("adapter_key is required when prep_output contains no candidates")
    raise ValueError("Deterministic pipeline requires prep candidates for exactly one adapter")


def _resolve_pipeline_profile_key(request: PostCurationPipelineRequest) -> str | None:
    if request.profile_key is not None:
        return request.profile_key

    profile_keys = {candidate.profile_key for candidate in request.prep_output.candidates}
    profile_keys.discard(None)
    if len(profile_keys) <= 1:
        return next(iter(profile_keys), None)
    raise ValueError("Deterministic pipeline requires prep candidates for at most one profile")


def _validate_candidate_scope(
    candidate: CurationPrepCandidate,
    *,
    adapter_key: str,
    profile_key: str | None,
) -> None:
    if candidate.adapter_key != adapter_key:
        raise ValueError("Deterministic pipeline requires prep candidates for exactly one adapter")
    if candidate.profile_key != profile_key:
        raise ValueError("Deterministic pipeline requires prep candidates for at most one profile")


def _select_execution_mode(
    request: PostCurationPipelineRequest,
) -> PipelineExecutionMode:
    if request.async_candidate_threshold <= 0:
        raise ValueError("async_candidate_threshold must be greater than zero")

    if request.execution_mode == PipelineExecutionMode.SYNC:
        return PipelineExecutionMode.SYNC
    if request.execution_mode == PipelineExecutionMode.ASYNC:
        return PipelineExecutionMode.ASYNC
    if len(request.prep_output.candidates) > request.async_candidate_threshold:
        return PipelineExecutionMode.ASYNC
    return PipelineExecutionMode.SYNC


def _task_name_for_request(request: PostCurationPipelineRequest) -> str:
    prepared_at = request.prepared_at or datetime.now(timezone.utc)
    return (
        "curation-post-agent-pipeline:"
        f"{request.document_id}:"
        f"{prepared_at.strftime('%Y%m%dT%H%M%S')}"
    )


def _field_group_key(field_path: str | None) -> str | None:
    if not field_path or "." not in field_path:
        return None
    return field_path.rsplit(".", 1)[0]


def _humanize_path(path: str | None) -> str | None:
    if path is None:
        return None
    segments = [segment.replace("_", " ").strip() for segment in path.split(".") if segment]
    if not segments:
        return None
    return " / ".join(segment.title() for segment in segments)


def _scalar_display_values(payload: Any) -> list[str]:
    values: list[str] = []
    _collect_scalar_display_values(payload, values)
    return _dedupe(values)


def _collect_scalar_display_values(payload: Any, values: list[str]) -> None:
    if payload is None:
        return
    if isinstance(payload, bool):
        values.append(str(payload).lower())
        return
    if isinstance(payload, (int, float, str)):
        rendered = str(payload).strip()
        if rendered:
            values.append(rendered)
        return
    if isinstance(payload, dict):
        for value in payload.values():
            _collect_scalar_display_values(value, values)
        return
    if isinstance(payload, list):
        for value in payload:
            _collect_scalar_display_values(value, values)


def _field_validation_status(value: Any) -> tuple[FieldValidationStatus, list[str]]:
    if value is None:
        return (
            FieldValidationStatus.INVALID_FORMAT,
            ["Extracted field is empty and needs curator review."],
        )
    if isinstance(value, str) and not value.strip():
        return (
            FieldValidationStatus.INVALID_FORMAT,
            ["Extracted field is blank and needs curator review."],
        )
    if isinstance(value, (list, dict)) and not value:
        return (
            FieldValidationStatus.INVALID_FORMAT,
            ["Extracted field is empty and needs curator review."],
        )
    return (FieldValidationStatus.SKIPPED, [])


def _increment_validation_count(
    counts: CurationValidationCounts,
    status: FieldValidationStatus,
) -> None:
    if status == FieldValidationStatus.VALIDATED:
        counts.validated += 1
    elif status == FieldValidationStatus.AMBIGUOUS:
        counts.ambiguous += 1
    elif status == FieldValidationStatus.NOT_FOUND:
        counts.not_found += 1
    elif status == FieldValidationStatus.INVALID_FORMAT:
        counts.invalid_format += 1
    elif status == FieldValidationStatus.CONFLICT:
        counts.conflict += 1
    elif status == FieldValidationStatus.SKIPPED:
        counts.skipped += 1
    elif status == FieldValidationStatus.OVERRIDDEN:
        counts.overridden += 1


def _serialize_evidence_reference(
    reference: CurationPrepEvidenceReference,
) -> dict[str, Any]:
    payload = reference.model_dump(mode="json")
    return payload


def _ambiguity_message(ambiguity: Any) -> str:
    return f"{ambiguity.field_path}: {ambiguity.description}"


def _dedupe(values: Sequence[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        deduped.append(value)
        seen.add(value)
    return deduped


__all__ = [
    "AsyncioPipelineTaskScheduler",
    "BatchValidationContext",
    "BatchValidationOutcome",
    "BatchValidationService",
    "CandidateNormalizationContext",
    "CurationCandidateNormalizer",
    "DEFAULT_ASYNC_CANDIDATE_THRESHOLD",
    "DeterministicEvidenceAnchorResolver",
    "DeterministicStructuralValidationService",
    "EvidenceAnchorResolver",
    "EvidenceResolutionContext",
    "NormalizedCandidate",
    "PassthroughCandidateNormalizer",
    "PassthroughEvidenceAnchorResolver",
    "PipelineExecutionMode",
    "PipelineRunStatus",
    "PipelineTaskScheduler",
    "PostCurationPipelineDependencies",
    "PostCurationPipelineRequest",
    "PostCurationPipelineResult",
    "execute_post_curation_pipeline",
    "run_post_curation_pipeline",
]
