"""Prepared-session reuse and persistence service."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.curation_workspace.models import (
    CurationActionLogEntry as SessionActionLogModel,
    CurationReviewSession as ReviewSessionModel,
)
from src.lib.curation_workspace.session_common import _normalize_uuid
from src.lib.curation_workspace.session_loading import PREPARED_SESSION_LOAD_OPTIONS
from src.lib.curation_workspace.session_persistence import (
    _apply_progress_counts,
    _delete_candidate_children,
    _delete_session_validation_snapshots,
    _persist_prepared_candidates,
    _persist_session_validation_snapshot,
)
from src.lib.curation_workspace.session_types import (
    PreparedSessionUpsertRequest,
    PreparedSessionUpsertResult,
    ReusablePreparedSessionContext,
)
from src.schemas.curation_workspace import (
    CurationActionType,
    CurationSessionStatus,
)

def find_reusable_prepared_session(
    db: Session,
    *,
    document_id: str,
    adapter_key: str,
    flow_run_id: str | None,
    prep_extraction_result_id: str | UUID,
) -> ReusablePreparedSessionContext | None:
    """Return the newest untouched NEW session that can be refreshed in place."""

    target_extraction_result_id = _normalize_uuid(
        prep_extraction_result_id,
        field_name="prep_extraction_result_id",
    )
    statement = (
        select(ReviewSessionModel)
        .where(ReviewSessionModel.document_id == _normalize_uuid(document_id, field_name="document_id"))
        .where(ReviewSessionModel.adapter_key == adapter_key)
        .where(ReviewSessionModel.status == CurationSessionStatus.NEW)
        .options(*PREPARED_SESSION_LOAD_OPTIONS)
        .order_by(
            ReviewSessionModel.prepared_at.desc(),
            ReviewSessionModel.created_at.desc(),
            ReviewSessionModel.id.desc(),
        )
    )

    if flow_run_id is None:
        statement = statement.where(ReviewSessionModel.flow_run_id.is_(None))
    else:
        statement = statement.where(ReviewSessionModel.flow_run_id == flow_run_id)

    for session_row in db.scalars(statement).all():
        if session_row.reviewed_candidates > 0 or session_row.submissions:
            continue
        if any(
            action_log_entry.candidate_id is not None or action_log_entry.draft_id is not None
            for action_log_entry in session_row.action_log_entries
        ):
            continue
        candidate_extraction_result_ids = {
            candidate.extraction_result_id
            for candidate in session_row.candidates
        }
        if candidate_extraction_result_ids != {target_extraction_result_id}:
            continue
        return ReusablePreparedSessionContext(
            session_id=str(session_row.id),
            created_by_id=session_row.created_by_id,
            assigned_curator_id=session_row.assigned_curator_id,
            notes=session_row.notes,
            tags=list(session_row.tags or []),
        )

    return None

def upsert_prepared_session(
    db: Session,
    request: PreparedSessionUpsertRequest,
    *,
    manage_transaction: bool = True,
) -> PreparedSessionUpsertResult:
    """Create or refresh an unreviewed session with deterministic pipeline output."""

    try:
        session_row, created = _load_or_initialize_prepared_session(db, request)
        _apply_prepared_session_metadata(session_row, request)

        if not created and request.replace_existing_candidates:
            _clear_prepared_session_children(db, session_row)

        candidate_ids = _persist_prepared_candidates(
            db,
            session_row,
            request.candidates,
            prepared_at=request.prepared_at,
        )

        session_row.current_candidate_id = (
            _normalize_uuid(candidate_ids[0], field_name="candidate_id")
            if candidate_ids
            else None
        )
        _apply_progress_counts(session_row, request.candidates)

        validation_log_row = _persist_session_validation_snapshot(
            db,
            session_row,
            request.session_validation_snapshot,
        )

        if created:
            created_actor = dict(request.session_created_actor) or None
            db.add(
                SessionActionLogModel(
                    session_id=session_row.id,
                    action_type=CurationActionType.SESSION_CREATED,
                    actor_type=request.session_created_actor_type,
                    actor=created_actor,
                    occurred_at=request.prepared_at,
                    new_session_status=session_row.status,
                    message=request.session_created_message,
                )
            )

        if not created:
            session_row.session_version += 1

        db.add(session_row)
        if validation_log_row is not None:
            db.add(validation_log_row)
        if manage_transaction:
            db.commit()
        else:
            db.flush()

        return PreparedSessionUpsertResult(
            session_id=str(session_row.id),
            created=created,
            candidate_ids=candidate_ids,
        )
    except Exception:
        if manage_transaction:
            db.rollback()
        raise


def _load_or_initialize_prepared_session(
    db: Session,
    request: PreparedSessionUpsertRequest,
) -> tuple[ReviewSessionModel, bool]:
    if request.review_session_id is None:
        session_row = ReviewSessionModel(
            document_id=_normalize_uuid(request.document_id, field_name="document_id"),
            adapter_key=request.adapter_key,
            prepared_at=request.prepared_at,
            created_at=request.prepared_at,
            updated_at=request.prepared_at,
        )
        db.add(session_row)
        db.flush()
        return session_row, True

    session_id = _normalize_uuid(request.review_session_id, field_name="review_session_id")
    session_row = db.scalars(
        select(ReviewSessionModel)
        .where(ReviewSessionModel.id == session_id)
        .options(*PREPARED_SESSION_LOAD_OPTIONS)
    ).first()

    if session_row is None:
        raise LookupError(f"Curation review session {session_id} not found")

    if session_row.reviewed_candidates > 0 or session_row.submissions:
        raise ValueError(
            "Prepared-session updates require an unreviewed session without submissions"
        )

    if any(
        action_log_entry.candidate_id is not None or action_log_entry.draft_id is not None
        for action_log_entry in session_row.action_log_entries
    ):
        raise ValueError(
            "Prepared-session updates cannot replace candidate data after candidate-level activity"
        )

    return session_row, False


def _apply_prepared_session_metadata(
    session_row: ReviewSessionModel,
    request: PreparedSessionUpsertRequest,
) -> None:
    session_row.status = request.status
    session_row.adapter_key = request.adapter_key
    session_row.profile_key = None
    session_row.document_id = _normalize_uuid(request.document_id, field_name="document_id")
    session_row.flow_run_id = request.flow_run_id
    session_row.assigned_curator_id = request.assigned_curator_id
    session_row.created_by_id = request.created_by_id
    session_row.notes = request.notes
    session_row.tags = list(request.tags)
    session_row.warnings = list(request.warnings)
    session_row.prepared_at = request.prepared_at
    session_row.last_worked_at = None
    session_row.paused_at = None
    session_row.rejection_reason = None
    session_row.updated_at = request.prepared_at
    if request.status != CurationSessionStatus.SUBMITTED:
        session_row.submitted_at = None


def _clear_prepared_session_children(db: Session, session_row: ReviewSessionModel) -> None:
    candidate_ids = [candidate.id for candidate in session_row.candidates]

    _delete_session_validation_snapshots(db, session_id=session_row.id)

    if candidate_ids:
        _delete_candidate_children(
            db,
            session_id=session_row.id,
            candidate_ids=candidate_ids,
        )

    session_row.candidates = []
    session_row.validation_snapshots = []
    session_row.current_candidate_id = None

__all__ = [
    "find_reusable_prepared_session",
    "upsert_prepared_session",
]
