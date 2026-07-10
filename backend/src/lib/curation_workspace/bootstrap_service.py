"""Bootstrap and manual-create orchestration for curation workspace sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any, Sequence
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.curation_workspace.curation_prep_constants import CURATION_PREP_AGENT_ID
from src.lib.curation_workspace.curation_prep_invocation import (
    run_chat_curation_prep,
    validate_chat_curation_prep_request,
)
from src.lib.curation_workspace.curation_prep_service import (
    CurationPrepPersistenceContext,
    build_flow_scope_confirmation,
    run_curation_prep,
)
from src.lib.curation_workspace.extraction_results import list_extraction_results
from src.lib.curation_workspace.models import (
    CurationExtractionResultRecord as ExtractionResultModel,
)
from src.lib.curation_workspace.pipeline import (
    PipelineExecutionMode,
    PostCurationPipelineRequest,
    run_post_curation_pipeline,
)
from src.lib.http_errors import raise_sanitized_http_exception
from src.lib.curation_workspace.session_service import (
    PreparedSessionUpsertRequest,
    find_reusable_prepared_session,
    get_session_detail,
    upsert_prepared_session,
)
from src.lib.context import get_current_trace_id
from src.lib.observability.sentry import (
    gen_ai_invoke_agent_span,
    set_redacted_ai_span_data,
    set_sentry_span_status,
)
from src.models.sql.pdf_document import PDFDocument
from src.schemas.curation_prep import (
    CurationPrepAgentOutput,
    CurationPrepChatRunRequest,
    CurationPrepChatRunResponse,
    CurationPrepPreparedSession,
)
from src.schemas.curation_workspace import (
    CurationActorType,
    CurationDocumentBootstrapAvailabilityResponse,
    CurationDocumentBootstrapRequest,
    CurationDocumentBootstrapResponse,
    CurationExtractionResultRecord,
    CurationExtractionSourceKind,
    CurationSessionCreateRequest,
    CurationSessionCreateResponse,
    CurationSessionStatus,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FlowCurationHandoffResult:
    review_session_ids: list[str]
    adapter_keys: list[str]


def create_manual_session(
    request: CurationSessionCreateRequest,
    *,
    current_user_id: str,
    actor_claims: dict[str, str | None],
    db: Session,
) -> CurationSessionCreateResponse:
    """Create an empty manual session without replaying the prep pipeline."""

    _require_document(db, request.document_id)

    prepared_at = datetime.now(timezone.utc)
    result = upsert_prepared_session(
        db,
        PreparedSessionUpsertRequest(
            document_id=request.document_id,
            adapter_key=request.adapter_key,
            created_by_id=current_user_id,
            assigned_curator_id=request.curator_id,
            notes=request.notes,
            tags=list(request.tags),
            prepared_at=prepared_at,
            status=CurationSessionStatus.NEW,
            candidates=[],
            session_created_actor_type=CurationActorType.USER,
            session_created_actor=_actor_payload(actor_claims),
            session_created_message="Manual review session created via curation workspace API",
        ),
    )

    return CurationSessionCreateResponse(
        created=result.created,
        session=get_session_detail(db, result.session_id),
    )


async def prepare_chat_curation_sessions(
    request: CurationPrepChatRunRequest,
    *,
    current_user_id: str,
    db: Session,
) -> CurationPrepChatRunResponse:
    """Prepare chat findings and bootstrap one review session per adapter in scope."""

    try:
        prep_response = await run_chat_curation_prep(
            request,
            user_id=current_user_id,
            db=db,
        )
        prepared_sessions: list[CurationPrepPreparedSession] = []

        for adapter_key in prep_response.adapter_keys:
            bootstrap_response = await bootstrap_document_session(
                prep_response.document_id,
                CurationDocumentBootstrapRequest(
                    adapter_key=adapter_key,
                    origin_session_id=request.session_id,
                ),
                current_user_id=current_user_id,
                db=db,
                manage_transaction=False,
            )
            prepared_sessions.append(
                CurationPrepPreparedSession(
                    session_id=bootstrap_response.session.session_id,
                    adapter_key=adapter_key,
                    created=bootstrap_response.created,
                )
            )

        db.commit()
        return prep_response.model_copy(
            update={
                "prepared_sessions": prepared_sessions,
            }
        )
    except Exception:
        if db.in_transaction():
            db.rollback()
        raise


async def run_flow_curation_handoff(
    *,
    extraction_results: Sequence[CurationExtractionResultRecord],
    document_id: str,
    runner_user_id: str,
    flow_run_id: str | None,
    origin_session_id: str | None,
    conversation_summary: str | None,
    db: Session,
) -> FlowCurationHandoffResult:
    """Run curation prep and bootstrap one runner-owned session per adapter."""

    adapter_keys = _handoff_adapter_keys(extraction_results)
    trace_id = get_current_trace_id()
    with gen_ai_invoke_agent_span(
        agent_name="Curation Handoff",
        model="deterministic_curation_handoff_v1",
        conversation_id=origin_session_id or flow_run_id or trace_id or document_id,
        provider_name="ai_curation",
        response_streaming=False,
        workflow="curation_handoff",
        agent_key="curation_handoff",
        agent_source="deterministic",
        trace_id=trace_id,
        flow_run_id=flow_run_id,
        document_id=document_id,
        document_present=True,
        candidate_count=sum(
            max(int(record.candidate_count), 0) for record in extraction_results
        ),
        span_data={
            "ai_curation.curation_handoff.adapter_count": len(adapter_keys),
            "ai_curation.curation_prep.extraction_result_count": len(extraction_results),
        },
    ) as sentry_span:
        try:
            result = await _run_flow_curation_handoff_impl(
                extraction_results=extraction_results,
                document_id=document_id,
                runner_user_id=runner_user_id,
                flow_run_id=flow_run_id,
                origin_session_id=origin_session_id,
                conversation_summary=conversation_summary,
                db=db,
                adapter_keys=adapter_keys,
                trace_id=trace_id,
            )
        except Exception as exc:
            if sentry_span is not None:
                set_sentry_span_status(
                    sentry_span,
                    "invalid_argument" if isinstance(exc, ValueError) else "internal_error",
                )
                set_redacted_ai_span_data(
                    sentry_span,
                    "ai_curation.curation_handoff.status",
                    "error",
                )
                set_redacted_ai_span_data(
                    sentry_span,
                    "ai_curation.error.detail",
                    {
                        "phase": "curation_handoff",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
            raise

        if sentry_span is not None:
            set_sentry_span_status(sentry_span, "ok")
            set_redacted_ai_span_data(
                sentry_span,
                "ai_curation.curation_handoff.status",
                "success",
            )
            set_redacted_ai_span_data(
                sentry_span,
                "ai_curation.curation_handoff.review_session_count",
                len(result.review_session_ids),
            )
            set_redacted_ai_span_data(
                sentry_span,
                "ai_curation.agent.output",
                {
                    "review_session_count": len(result.review_session_ids),
                    "adapter_keys": result.adapter_keys,
                },
            )
        return result


async def _run_flow_curation_handoff_impl(
    *,
    extraction_results: Sequence[CurationExtractionResultRecord],
    document_id: str,
    runner_user_id: str,
    flow_run_id: str | None,
    origin_session_id: str | None,
    conversation_summary: str | None,
    db: Session,
    adapter_keys: Sequence[str],
    trace_id: str | None,
) -> FlowCurationHandoffResult:
    try:
        if not adapter_keys:
            raise ValueError(
                "Curation handoff requires extraction results for at least one adapter key."
            )

        for adapter_key in adapter_keys:
            adapter_records = [
                record for record in extraction_results if record.adapter_key == adapter_key
            ]
            await run_curation_prep(
                adapter_records,
                scope_confirmation=build_flow_scope_confirmation(
                    adapter_records,
                    flow_name="curation handoff",
                ),
                persistence_context=CurationPrepPersistenceContext(
                    document_id=document_id,
                    source_kind=CurationExtractionSourceKind.FLOW,
                    origin_session_id=origin_session_id,
                    trace_id=trace_id,
                    flow_run_id=flow_run_id,
                    user_id=runner_user_id,
                    conversation_summary=conversation_summary,
                    workflow="curation_handoff",
                ),
                db=db,
            )

        review_session_ids: list[str] = []

        for adapter_key in adapter_keys:
            bootstrap_response = await bootstrap_document_session(
                document_id,
                CurationDocumentBootstrapRequest(
                    adapter_key=adapter_key,
                    flow_run_id=flow_run_id,
                    origin_session_id=origin_session_id,
                    curator_id=runner_user_id,
                ),
                current_user_id=runner_user_id,
                db=db,
                manage_transaction=False,
            )
            review_session_ids.append(bootstrap_response.session.session_id)

        db.commit()
        return FlowCurationHandoffResult(
            review_session_ids=review_session_ids,
            adapter_keys=list(adapter_keys),
        )
    except Exception:
        if db.in_transaction():
            db.rollback()
        raise


async def bootstrap_document_session(
    document_id: str,
    request: CurationDocumentBootstrapRequest,
    *,
    current_user_id: str,
    db: Session,
    manage_transaction: bool = True,
) -> CurationDocumentBootstrapResponse:
    """Replay the newest matching persisted prep result into a review session."""

    _require_document(db, document_id)
    extraction_result = await _ensure_bootstrap_extraction_result(
        db,
        document_id=document_id,
        request=request,
        current_user_id=current_user_id,
    )
    prep_output = _replayable_prep_output(extraction_result)
    adapter_key = _resolved_adapter_key(extraction_result)
    reusable_session = find_reusable_prepared_session(
        db,
        document_id=document_id,
        adapter_key=adapter_key,
        flow_run_id=extraction_result.flow_run_id,
        prep_extraction_result_id=str(extraction_result.id),
    )

    prepared_at = datetime.now(timezone.utc)
    try:
        pipeline_result = await run_post_curation_pipeline(
            PostCurationPipelineRequest(
                prep_output=prep_output,
                document_id=document_id,
                source_kind=extraction_result.source_kind,
                adapter_key=adapter_key,
                flow_run_id=extraction_result.flow_run_id,
                origin_session_id=extraction_result.origin_session_id,
                trace_id=extraction_result.trace_id,
                user_id=current_user_id,
                created_by_id=(
                    reusable_session.created_by_id if reusable_session else current_user_id
                ),
                assigned_curator_id=(
                    request.curator_id
                    if request.curator_id is not None
                    else reusable_session.assigned_curator_id if reusable_session else None
                ),
                notes=reusable_session.notes if reusable_session else None,
                tags=tuple(reusable_session.tags) if reusable_session else (),
                prepared_at=prepared_at,
                review_session_id=reusable_session.session_id if reusable_session else None,
                prep_extraction_result_id=str(extraction_result.id),
                execution_mode=PipelineExecutionMode.SYNC,
            ),
            db=db,
        )

        if pipeline_result.session_id is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Bootstrap pipeline did not return a review session identifier",
            )

        response = CurationDocumentBootstrapResponse(
            created=bool(pipeline_result.created),
            session=get_session_detail(db, pipeline_result.session_id),
        )
        if manage_transaction:
            db.commit()
        else:
            db.flush()
        return response
    except Exception:
        if manage_transaction and db.in_transaction():
            db.rollback()
        raise


def get_document_bootstrap_availability(
    document_id: str,
    request: CurationDocumentBootstrapRequest,
    *,
    current_user_id: str,
    db: Session,
) -> CurationDocumentBootstrapAvailabilityResponse:
    """Return whether the current bootstrap selectors can resolve a prep result."""

    _require_document(db, document_id)

    try:
        _select_bootstrap_extraction_result(
            db,
            document_id=document_id,
            request=request,
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            flow_run_id = _normalized_optional_str(request.flow_run_id)
            if flow_run_id is not None:
                flow_results = _list_flow_bootstrap_extraction_results(
                    db,
                    document_id=document_id,
                    request=request,
                    current_user_id=current_user_id,
                )
                distinct_adapter_keys = _handoff_adapter_keys(flow_results)
                return CurationDocumentBootstrapAvailabilityResponse(
                    eligible=bool(flow_results) and len(distinct_adapter_keys) == 1,
                )

            origin_session_id = _normalized_optional_str(request.origin_session_id)
            if origin_session_id is None:
                return CurationDocumentBootstrapAvailabilityResponse(eligible=False)

            adapter_key = _normalized_optional_str(request.adapter_key)
            try:
                context, _ = validate_chat_curation_prep_request(
                    session_id=origin_session_id,
                    user_id=current_user_id,
                    db=db,
                    requested_adapter_keys=[adapter_key] if adapter_key is not None else [],
                )
            except ValueError:
                return CurationDocumentBootstrapAvailabilityResponse(eligible=False)

            if not _chat_prep_matches_bootstrap_request(
                document_id=document_id,
                request=request,
                context=context,
            ):
                return CurationDocumentBootstrapAvailabilityResponse(eligible=False)

            return CurationDocumentBootstrapAvailabilityResponse(eligible=True)
        raise

    return CurationDocumentBootstrapAvailabilityResponse(eligible=True)


def _handoff_adapter_keys(
    extraction_results: Sequence[CurationExtractionResultRecord],
) -> list[str]:
    return sorted(
        {
            str(record.adapter_key).strip()
            for record in extraction_results
            if str(record.adapter_key or "").strip()
        }
    )


def _list_flow_bootstrap_extraction_results(
    db: Session,
    *,
    document_id: str,
    request: CurationDocumentBootstrapRequest,
    current_user_id: str,
) -> list[CurationExtractionResultRecord]:
    adapter_key = _normalized_optional_str(request.adapter_key)
    results = list_extraction_results(
        db=db,
        document_id=document_id,
        flow_run_id=_normalized_optional_str(request.flow_run_id),
        origin_session_id=_normalized_optional_str(request.origin_session_id),
        user_id=current_user_id,
        source_kind=CurationExtractionSourceKind.FLOW,
        exclude_agent_keys=[CURATION_PREP_AGENT_ID],
    )
    if adapter_key is not None:
        return [result for result in results if result.adapter_key == adapter_key]
    return results


def _require_document(db: Session, document_id: str) -> PDFDocument:
    document_uuid = _parse_uuid(document_id, field_name="document_id")
    document = db.get(PDFDocument, document_uuid)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found",
        )
    return document


def _select_bootstrap_extraction_result(
    db: Session,
    *,
    document_id: str,
    request: CurationDocumentBootstrapRequest,
) -> ExtractionResultModel:
    statement = (
        select(ExtractionResultModel)
        .where(ExtractionResultModel.document_id == _parse_uuid(document_id, field_name="document_id"))
        .where(ExtractionResultModel.agent_key == CURATION_PREP_AGENT_ID)
        .order_by(ExtractionResultModel.created_at.desc(), ExtractionResultModel.id.desc())
    )

    adapter_key = _normalized_optional_str(request.adapter_key)
    flow_run_id = _normalized_optional_str(request.flow_run_id)
    origin_session_id = _normalized_optional_str(request.origin_session_id)

    if adapter_key is not None:
        statement = statement.where(ExtractionResultModel.adapter_key == adapter_key)
    if flow_run_id is not None:
        statement = statement.where(ExtractionResultModel.flow_run_id == flow_run_id)
    if origin_session_id is not None:
        statement = statement.where(ExtractionResultModel.origin_session_id == origin_session_id)

    matching_results = list(db.scalars(statement).all())
    if not matching_results:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No persisted curation prep extraction results were found for "
                f"document {document_id}"
            ),
        )

    if adapter_key is None:
        distinct_adapter_keys = {
            str(result.adapter_key).strip()
            for result in matching_results
            if str(result.adapter_key or "").strip()
        }
        if len(distinct_adapter_keys) > 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Multiple prepared curation sessions are available for this document. "
                    "Choose an adapter-specific entry point or open Curation Inventory to select one."
                ),
            )

    return matching_results[0]


def _chat_prep_matches_bootstrap_request(
    *,
    document_id: str,
    request: CurationDocumentBootstrapRequest,
    context: Any,
) -> bool:
    extraction_results = getattr(context, "extraction_results", None) or []
    if not extraction_results:
        return False

    primary_extraction_result = extraction_results[0]
    if _normalized_optional_str(getattr(primary_extraction_result, "document_id", None)) != str(document_id).strip():
        return False

    requested_flow_run_id = _normalized_optional_str(request.flow_run_id)
    if requested_flow_run_id is None:
        return True

    return _normalized_optional_str(getattr(primary_extraction_result, "flow_run_id", None)) == requested_flow_run_id


async def _ensure_bootstrap_extraction_result(
    db: Session,
    *,
    document_id: str,
    request: CurationDocumentBootstrapRequest,
    current_user_id: str,
) -> ExtractionResultModel:
    try:
        return _select_bootstrap_extraction_result(
            db,
            document_id=document_id,
            request=request,
        )
    except HTTPException as exc:
        if exc.status_code != status.HTTP_404_NOT_FOUND:
            raise

    flow_run_id = _normalized_optional_str(request.flow_run_id)
    if flow_run_id is not None:
        adapter_key = _normalized_optional_str(request.adapter_key)
        flow_results = _list_flow_bootstrap_extraction_results(
            db,
            document_id=document_id,
            request=request,
            current_user_id=current_user_id,
        )

        if not flow_results:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    "No persisted flow extraction results were found for "
                    f"document {document_id} and flow run {flow_run_id}"
                ),
            )
        if adapter_key is None and len(_handoff_adapter_keys(flow_results)) > 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Multiple flow extraction adapters are available for this document. "
                    "Choose an adapter-specific entry point."
                ),
            )

        await run_curation_prep(
            flow_results,
            scope_confirmation=build_flow_scope_confirmation(
                flow_results,
                flow_name="Review & Curate",
            ),
            persistence_context=CurationPrepPersistenceContext(
                document_id=document_id,
                source_kind=CurationExtractionSourceKind.FLOW,
                origin_session_id=_normalized_optional_str(request.origin_session_id),
                trace_id=flow_results[0].trace_id,
                flow_run_id=flow_run_id,
                user_id=current_user_id,
                conversation_summary=flow_results[0].conversation_summary,
                workflow="curation_bootstrap_flow",
            ),
            db=db,
        )
        return _select_bootstrap_extraction_result(
            db,
            document_id=document_id,
            request=request,
        )

    origin_session_id = _normalized_optional_str(request.origin_session_id)
    if origin_session_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No persisted curation prep extraction results were found for "
                f"document {document_id}"
            ),
        )

    adapter_key = _normalized_optional_str(request.adapter_key)
    try:
        context, _ = validate_chat_curation_prep_request(
            session_id=origin_session_id,
            user_id=current_user_id,
            db=db,
            requested_adapter_keys=[adapter_key] if adapter_key is not None else [],
        )
    except ValueError as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Bootstrap origin session could not be validated",
            log_message="Bootstrap origin session validation failed",
            exc=exc,
            level=logging.WARNING,
        )

    if not _chat_prep_matches_bootstrap_request(
        document_id=document_id,
        request=request,
        context=context,
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No persisted curation prep extraction results were found for "
                f"document {document_id}"
            ),
        )

    try:
        await run_chat_curation_prep(
            CurationPrepChatRunRequest(
                session_id=origin_session_id,
                adapter_keys=[adapter_key] if adapter_key is not None else [],
            ),
            user_id=current_user_id,
            db=db,
        )
    except ValueError as exc:
        raise_sanitized_http_exception(
            logger,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Bootstrap curation prep could not be prepared",
            log_message="Bootstrap curation prep failed",
            exc=exc,
            level=logging.WARNING,
        )

    return _select_bootstrap_extraction_result(
        db,
        document_id=document_id,
        request=request,
    )


def _replayable_prep_output(extraction_result: ExtractionResultModel) -> CurationPrepAgentOutput:
    payload = extraction_result.payload_json
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored curation prep extraction payload is not replayable",
        )

    metadata = dict(extraction_result.extraction_metadata or {})
    run_metadata = metadata.get("final_run_metadata", payload.get("run_metadata"))
    replay_payload = dict(payload)
    if run_metadata is not None:
        replay_payload["run_metadata"] = run_metadata
    try:
        return CurationPrepAgentOutput.model_validate(replay_payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored curation prep extraction payload is not replayable",
        ) from exc


def _resolved_adapter_key(extraction_result: ExtractionResultModel) -> str:
    adapter_key = _normalized_optional_str(extraction_result.adapter_key)
    if adapter_key is not None:
        return adapter_key

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="Bootstrap requires a persisted prep result with adapter ownership",
    )


def _actor_payload(actor_claims: dict[str, str | None]) -> dict[str, str]:
    actor_id = str(actor_claims.get("sub") or actor_claims.get("uid") or "").strip()
    payload: dict[str, str] = {}
    if actor_id:
        payload["actor_id"] = actor_id

    display_name = str(
        actor_claims.get("name")
        or actor_claims.get("preferred_username")
        or actor_claims.get("email")
        or ""
    ).strip()
    if display_name:
        payload["display_name"] = display_name

    email = str(actor_claims.get("email") or "").strip()
    if email:
        payload["email"] = email

    return payload


def _parse_uuid(value: str, *, field_name: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid {field_name}: {value}",
        ) from exc


def _normalized_optional_str(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


__all__ = [
    "bootstrap_document_session",
    "create_manual_session",
    "prepare_chat_curation_sessions",
]
