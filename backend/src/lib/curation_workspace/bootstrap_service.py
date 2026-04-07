"""Bootstrap and manual-create orchestration for curation workspace sessions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
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
from src.lib.curation_workspace.models import (
    CurationExtractionResultRecord as ExtractionResultModel,
)
from src.lib.curation_workspace.pipeline import (
    PipelineExecutionMode,
    PostCurationPipelineRequest,
    run_post_curation_pipeline,
)
from src.lib.curation_workspace.session_service import (
    PreparedSessionUpsertRequest,
    find_reusable_prepared_session,
    get_session_detail,
    upsert_prepared_session,
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
    CurationSessionCreateRequest,
    CurationSessionCreateResponse,
    CurationSessionStatus,
)


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
            origin_session_id = _normalized_optional_str(request.origin_session_id)
            if origin_session_id is None:
                return CurationDocumentBootstrapAvailabilityResponse(eligible=False)

            try:
                context, _ = validate_chat_curation_prep_request(
                    session_id=origin_session_id,
                    user_id=current_user_id,
                    db=db,
                    requested_adapter_keys=(
                        [request.adapter_key]
                        if _normalized_optional_str(request.adapter_key) is not None
                        else []
                    ),
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

    origin_session_id = _normalized_optional_str(request.origin_session_id)
    if origin_session_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No persisted curation prep extraction results were found for "
                f"document {document_id}"
            ),
        )

    try:
        context, _ = validate_chat_curation_prep_request(
            session_id=origin_session_id,
            user_id=current_user_id,
            db=db,
            requested_adapter_keys=(
                [request.adapter_key]
                if _normalized_optional_str(request.adapter_key) is not None
                else []
            ),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

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
                adapter_keys=[request.adapter_key] if _normalized_optional_str(request.adapter_key) else [],
            ),
            user_id=current_user_id,
            db=db,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

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
    try:
        return CurationPrepAgentOutput.model_validate(
            {
                "candidates": payload.get("candidates", []),
                "run_metadata": run_metadata,
            }
        )
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
