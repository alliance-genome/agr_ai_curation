"""Deterministic post-agent pipeline for curation prep outputs."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Protocol, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.curation_workspace.curation_prep_constants import CURATION_PREP_AGENT_ID
from src.lib.curation_workspace.validation_runtime import (
    dedupe,
    field_validation_status,
    increment_validation_count,
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
from src.models.sql.database import SessionLocal
from src.schemas.curation_prep import (
    CurationPrepAgentOutput,
    CurationPrepCandidate,
)
from src.schemas.curation_workspace import (
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationExtractionSourceKind,
    CurationSessionStatus,
    CurationValidationCounts,
    CurationValidationScope,
    CurationValidationSnapshotState,
    CurationValidationSummary,
    DomainEnvelopeReviewRow,
    FieldValidationResult,
    FieldValidationStatus,
)
from src.lib.domain_packs.materialization import materialize_persisted_envelope_review_rows


logger = logging.getLogger(__name__)

DEFAULT_ASYNC_CANDIDATE_THRESHOLD = 25


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
    prep_extraction_result_id: str
    candidate_index: int
    current_user_id: str | None = None


@dataclass(frozen=True)
class BatchValidationContext:
    """Context passed into batch-validation implementations."""

    document_id: str
    adapter_key: str
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
        payload: dict[str, Any],
        *,
        prep_candidate: CurationPrepCandidate,
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
    """Replaceable collaborators for post-prep pipeline dispatch."""

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
        )

    task_name = _task_name_for_request(request)
    scheduled_task_name = dependencies.task_scheduler.schedule(
        lambda: execute_post_curation_pipeline(
            request,
        ),
        task_name=task_name,
    )
    return PostCurationPipelineResult(
        status=PipelineRunStatus.SCHEDULED,
        execution_mode=PipelineExecutionMode.ASYNC,
        candidate_count=_prep_review_row_count(request.prep_output),
        prep_extraction_result_id=request.prep_extraction_result_id,
        task_name=scheduled_task_name,
    )


def execute_post_curation_pipeline(
    request: PostCurationPipelineRequest,
    *,
    db: Session | None = None,
) -> PostCurationPipelineResult:
    """Execute the deterministic post-agent pipeline synchronously."""

    owns_session = db is None
    session = db or SessionLocal()

    try:
        persistence_result, prep_extraction_result_id = _execute_pipeline_steps(
            session,
            request,
        )
        if owns_session:
            session.commit()
        return PostCurationPipelineResult(
            status=PipelineRunStatus.COMPLETED,
            execution_mode=PipelineExecutionMode.SYNC,
            candidate_count=_prep_review_row_count(request.prep_output),
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
) -> tuple[PreparedSessionUpsertResult, str]:
    prep_extraction_result = _resolve_prep_extraction_result(db, request)
    adapter_key = _resolve_pipeline_adapter_key(request)

    validated_at = request.prepared_at or datetime.now(timezone.utc)
    prepared_candidates, validation_outcome = _prepared_candidates_from_envelope_refs(
        db,
        request,
        adapter_key=adapter_key,
        prep_extraction_result_id=str(prep_extraction_result.id),
        validated_at=validated_at,
    )

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


def _prepared_candidates_from_envelope_refs(
    db: Session,
    request: PostCurationPipelineRequest,
    *,
    adapter_key: str,
    prep_extraction_result_id: str,
    validated_at: datetime,
) -> tuple[list[PreparedCandidateInput], BatchValidationOutcome]:
    review_rows = _materialized_review_rows(db, request)
    prepared_candidates = [
        _prepared_candidate_input_from_review_row(
            review_row,
            adapter_key=adapter_key,
            prep_extraction_result_id=prep_extraction_result_id,
            prep_output=request.prep_output,
            candidate_index=index,
        )
        for index, review_row in enumerate(review_rows)
    ]
    validation_outcome = _validate_prepared_review_rows(
        prepared_candidates,
        adapter_key=adapter_key,
        validated_at=validated_at,
    )
    if len(validation_outcome.candidate_snapshots) != len(prepared_candidates):
        raise ValueError(
            "Validation service must return one candidate snapshot per review row"
        )
    return (
        [
            replace(
                candidate,
                validation_snapshot=validation_outcome.candidate_snapshots[index],
            )
            for index, candidate in enumerate(prepared_candidates)
        ],
        validation_outcome,
    )


def _materialized_review_rows(
    db: Session,
    request: PostCurationPipelineRequest,
) -> list[DomainEnvelopeReviewRow]:
    if not request.prep_output.envelope_refs:
        raise ValueError(
            "Domain-envelope post-curation pipeline requires prep_output.envelope_refs"
        )

    rows: list[DomainEnvelopeReviewRow] = []
    for envelope_ref in request.prep_output.envelope_refs:
        response = materialize_persisted_envelope_review_rows(
            db,
            envelope_ref.envelope_id,
            revision=envelope_ref.envelope_revision,
        )
        rows.extend(response.rows)

    if not rows:
        raise ValueError("Domain-envelope post-curation pipeline materialized no review rows")
    return rows


def _prepared_candidate_input_from_review_row(
    review_row: DomainEnvelopeReviewRow,
    *,
    adapter_key: str,
    prep_extraction_result_id: str,
    prep_output: CurationPrepAgentOutput,
    candidate_index: int,
) -> PreparedCandidateInput:
    draft_fields = _draft_fields_from_review_row(review_row)
    metadata = {
        "semantic_source": "domain_envelope.objects",
        "projection_type": review_row.projection_type,
        "projection_key": review_row.projection_key,
        "domain_pack_id": review_row.domain_pack_id,
        "domain_pack_version": review_row.domain_pack_version,
        "object_type": review_row.object_type,
        "object_role": review_row.object_role,
        "object_status": review_row.status,
        "validation_state": review_row.validation_state,
        "summary_fields": [
            field.model_dump(mode="json")
            for field in review_row.summary_fields
        ],
        "prep_envelope_refs": [
            envelope_ref.model_dump(mode="json")
            for envelope_ref in prep_output.envelope_refs
        ],
        "prep_candidate_index": candidate_index,
    }

    return PreparedCandidateInput(
        source=CurationCandidateSource.EXTRACTED,
        status=CurationCandidateStatus.PENDING,
        order=candidate_index,
        adapter_key=adapter_key,
        display_label=review_row.display_label,
        secondary_label=review_row.secondary_label,
        conversation_summary=(
            f"Materialized {review_row.object_type} review row from domain envelope "
            f"{review_row.envelope_id} revision {review_row.envelope_revision}."
        ),
        extraction_result_id=prep_extraction_result_id,
        envelope_id=review_row.envelope_id,
        object_id=review_row.object_id,
        envelope_revision=review_row.envelope_revision,
        normalized_payload={},
        metadata=metadata,
        draft_fields=draft_fields,
        draft_title=review_row.display_label,
        draft_summary=review_row.secondary_label,
        draft_metadata={
            "semantic_source": "domain_envelope.objects",
            "prep_run_metadata": prep_output.run_metadata.model_dump(mode="json"),
            "projection_ref": {
                "envelope_id": review_row.envelope_id,
                "object_id": review_row.object_id,
                "envelope_revision": review_row.envelope_revision,
            },
        },
        evidence_records=[],
        validation_snapshot=None,
    )


def _draft_fields_from_review_row(
    review_row: DomainEnvelopeReviewRow,
) -> list[PreparedDraftFieldInput]:
    return [
        PreparedDraftFieldInput(
            field_key=field.field_path,
            label=field.label,
            value=field.value,
            seed_value=field.value,
            field_type=field.field_type,
            group_key=_field_group_key(field.field_path),
            group_label=_field_group_label(_field_group_key(field.field_path)),
            order=index,
            metadata={
                "semantic_source": "domain_envelope.objects",
                "source_field_path": field.field_path,
                "projection_type": review_row.projection_type,
                "projection_key": review_row.projection_key,
                "field_metadata": dict(field.metadata),
            },
        )
        for index, field in enumerate(review_row.summary_fields)
    ]


def _validate_prepared_review_rows(
    candidates: Sequence[PreparedCandidateInput],
    *,
    adapter_key: str,
    validated_at: datetime,
) -> BatchValidationOutcome:
    session_counts = CurationValidationCounts()
    candidate_snapshots: list[PreparedValidationSnapshotInput] = []
    session_warnings = [
        "Domain-envelope review-row projection validation completed from persisted envelope fields."
    ]

    for candidate in candidates:
        candidate_counts = CurationValidationCounts()
        field_results: dict[str, FieldValidationResult] = {}
        candidate_warnings: list[str] = []

        for draft_field in candidate.draft_fields:
            field_status, field_warnings = _field_validation_status(draft_field.value)
            field_results[draft_field.field_key] = FieldValidationResult(
                status=field_status,
                resolver="domain_envelope_review_row_materializer",
                warnings=field_warnings,
            )
            _increment_validation_count(candidate_counts, field_status)
            _increment_validation_count(session_counts, field_status)
            candidate_warnings.extend(field_warnings)

        candidate_summary = CurationValidationSummary(
            state=CurationValidationSnapshotState.COMPLETED,
            counts=candidate_counts,
            last_validated_at=validated_at,
            warnings=_dedupe(candidate_warnings),
        )
        candidate_snapshots.append(
            PreparedValidationSnapshotInput(
                scope=CurationValidationScope.CANDIDATE,
                adapter_key=candidate.adapter_key,
                state=CurationValidationSnapshotState.COMPLETED,
                field_results=field_results,
                summary=candidate_summary,
                warnings=_dedupe(candidate_warnings),
                requested_at=validated_at,
                completed_at=validated_at,
            )
        )
        session_warnings.extend(candidate_warnings)

    session_summary = CurationValidationSummary(
        state=CurationValidationSnapshotState.COMPLETED,
        counts=session_counts,
        last_validated_at=validated_at,
        warnings=_dedupe(session_warnings),
    )
    return BatchValidationOutcome(
        candidate_snapshots=candidate_snapshots,
        session_snapshot=PreparedValidationSnapshotInput(
            scope=CurationValidationScope.SESSION,
            adapter_key=adapter_key,
            state=CurationValidationSnapshotState.COMPLETED,
            field_results={},
            summary=session_summary,
            warnings=_dedupe(session_warnings),
            requested_at=validated_at,
            completed_at=validated_at,
        ),
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
        .where(ExtractionResultModel.candidate_count == _prep_review_row_count(request.prep_output))
        .order_by(ExtractionResultModel.created_at.desc())
    )

    if request.adapter_key is not None:
        statement = statement.where(ExtractionResultModel.adapter_key == request.adapter_key)
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

    expected_payload = request.prep_output.model_dump(mode="json")
    run_metadata_payload = request.prep_output.run_metadata.model_dump(mode="json")

    for record in db.scalars(statement).all():
        payload = record.payload_json if isinstance(record.payload_json, dict) else {}
        metadata = dict(record.extraction_metadata or {})
        if payload.get("envelope_refs") != expected_payload.get("envelope_refs"):
            continue
        if payload.get("review_row_count") != expected_payload.get("review_row_count"):
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
    expected_payload = request.prep_output.model_dump(mode="json")
    if payload.get("envelope_refs") != expected_payload.get("envelope_refs"):
        raise ValueError("prep_extraction_result_id payload does not match the supplied prep output")
    if payload.get("review_row_count") != expected_payload.get("review_row_count"):
        raise ValueError("prep_extraction_result_id row count does not match the supplied prep output")
    metadata = dict(record.extraction_metadata or {})
    if metadata.get("final_run_metadata") != request.prep_output.run_metadata.model_dump(mode="json"):
        raise ValueError(
            "prep_extraction_result_id final run metadata does not match the supplied prep output"
        )


def _resolve_pipeline_adapter_key(request: PostCurationPipelineRequest) -> str:
    if request.adapter_key is not None:
        return request.adapter_key

    raise ValueError("adapter_key is required for domain-envelope review-row materialization")


def _select_execution_mode(
    request: PostCurationPipelineRequest,
) -> PipelineExecutionMode:
    if request.async_candidate_threshold <= 0:
        raise ValueError("async_candidate_threshold must be greater than zero")

    if request.execution_mode == PipelineExecutionMode.SYNC:
        return PipelineExecutionMode.SYNC
    if request.execution_mode == PipelineExecutionMode.ASYNC:
        return PipelineExecutionMode.ASYNC
    if _prep_review_row_count(request.prep_output) > request.async_candidate_threshold:
        return PipelineExecutionMode.ASYNC
    return PipelineExecutionMode.SYNC


def _prep_review_row_count(prep_output: CurationPrepAgentOutput) -> int:
    if not prep_output.envelope_refs:
        raise ValueError(
            "Domain-envelope post-curation pipeline requires prep_output.envelope_refs"
        )
    return prep_output.review_row_count


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


def _field_group_label(field_path: str | None) -> str | None:
    if field_path is None:
        return None
    return " / ".join(
        segment.replace("_", " ").strip().title()
        for segment in field_path.split(".")
        if segment
    )


def _field_validation_status(value: Any) -> tuple[FieldValidationStatus, list[str]]:
    return field_validation_status(value)


def _increment_validation_count(
    counts: CurationValidationCounts,
    status: FieldValidationStatus,
) -> None:
    increment_validation_count(counts, status)


def _dedupe(values: Sequence[str]) -> list[str]:
    return dedupe(values)


__all__ = [
    "AsyncioPipelineTaskScheduler",
    "BatchValidationContext",
    "BatchValidationOutcome",
    "BatchValidationService",
    "CandidateNormalizationContext",
    "CurationCandidateNormalizer",
    "DEFAULT_ASYNC_CANDIDATE_THRESHOLD",
    "EvidenceAnchorResolver",
    "EvidenceResolutionContext",
    "NormalizedCandidate",
    "PipelineExecutionMode",
    "PipelineRunStatus",
    "PipelineTaskScheduler",
    "PostCurationPipelineDependencies",
    "PostCurationPipelineRequest",
    "PostCurationPipelineResult",
    "execute_post_curation_pipeline",
    "run_post_curation_pipeline",
]
