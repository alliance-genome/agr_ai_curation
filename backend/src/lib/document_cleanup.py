"""Helpers for removing document-owned curation artifacts safely."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
import logging
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from src.lib.curation_workspace.models import (
    CurationActionLogEntry,
    CurationCandidate,
    CurationDraft,
    CurationEvidenceRecord,
    CurationExtractionResultRecord as ExtractionResultModel,
    CurationReviewSession,
    CurationSubmissionRecord,
    CurationValidationSnapshot,
    DomainEnvelopeHistory,
    DomainEnvelopeModel,
    DomainEnvelopeObject,
    DomainEnvelopeProjectionIndex,
    DomainValidationFinding,
)

from src.lib.observability import runtime
from src.lib.observability.sentry import hash_sentry_identifier

logger = logging.getLogger(__name__)
_T = TypeVar("_T")


@dataclass
class DeleteDrainState:
    """Per-operation state shared with destructive delete helpers during shielded drain."""

    started: bool = False
    cancelled: bool = False
    owner_task: asyncio.Task[Any] | None = None
    owner_initial_cancels: int = 0


_DELETE_DRAIN_STATE: ContextVar[DeleteDrainState | None] = ContextVar(
    "delete_drain_state",
    default=None,
)


@contextmanager
def delete_drain_context() -> Iterator[DeleteDrainState]:
    """Bind a fresh ``DeleteDrainState`` for the active paper delete request."""
    state = DeleteDrainState()
    token = _DELETE_DRAIN_STATE.set(state)
    try:
        yield state
    finally:
        _DELETE_DRAIN_STATE.reset(token)


def mark_destructive_delete_started() -> None:
    """Record that the active paper delete has submitted its first destructive worker."""
    state = _DELETE_DRAIN_STATE.get()
    if state is not None:
        state.started = True


def is_cancelled_delete_drain() -> bool:
    """Return whether the active paper delete is draining after caller cancellation.

    Checks both ``state.cancelled`` and the owning task's cancellation count so a child
    resuming ahead of the owner's ``on_cancel`` handler still delegates reporting.
    """
    state = _DELETE_DRAIN_STATE.get()
    if state is None:
        return False
    if state.cancelled:
        return True
    return (
        state.owner_task is not None
        and state.owner_task.cancelling() > state.owner_initial_cancels
    )


def is_delete_cancelled_before_start() -> bool:
    """Return whether the owning delete task was cancelled before destructive work started."""
    state = _DELETE_DRAIN_STATE.get()
    if state is None or state.started:
        return False
    if state.cancelled:
        return True
    return (
        state.owner_task is not None
        and state.owner_task.cancelling() > state.owner_initial_cancels
    )


def report_compensation_file_cleanup_failure(
    *,
    component: str,
    document_id: str,
    sql_record_committed: bool,
    weaviate_document_created: bool,
) -> None:
    """Emit one sanitized Sentry report and companion warning when compensation unlink fails."""
    document_id_hash = hash_sentry_identifier(document_id)
    logger.warning(
        "Retaining remaining document ownership during compensation because staged PDF cleanup failed "
        "(document_id_hash=%s, sql_committed=%s, weaviate_retained=%s)",
        document_id_hash,
        sql_record_committed,
        weaviate_document_created,
        extra={"sentry_skip_event": True},
    )
    try:
        runtime.report_runtime_exception(
            runtime.sanitized_runtime_error(
                "Staged PDF artifact removal failed during intake compensation"
            ),
            component=component,
            operation="intake_compensation_file_cleanup_failed",
            context={
                "document_id_hash": document_id_hash,
                "sql_committed": sql_record_committed,
                "weaviate_retained": weaviate_document_created,
            },
            fingerprint=[
                "intake_compensation_file_cleanup_failed",
                component,
                "post_commit" if sql_record_committed else "pre_commit",
            ],
            include_request=False,
        )
    except Exception:
        logger.warning(
            "Failed to report intake compensation file cleanup failure",
            extra={"sentry_skip_event": True},
        )


def report_sql_cleanup_failure(
    *,
    component: str,
    document_id: str,
    exception_type: str,
) -> None:
    """Emit one sanitized Sentry report and companion warning when post-Weaviate SQL cleanup fails."""
    document_id_hash = hash_sentry_identifier(document_id)
    logger.warning(
        "Failed to delete document from PostgreSQL (%s, document_id_hash=%s)",
        exception_type,
        document_id_hash,
        extra={"sentry_skip_event": True},
    )
    try:
        runtime.report_runtime_exception(
            runtime.sanitized_runtime_error(
                "PostgreSQL cleanup failed after Weaviate document deletion"
            ),
            component=component,
            operation="paper_delete_sql_cleanup_failed",
            context={
                "document_id_hash": document_id_hash,
                "exception_type": exception_type,
            },
            fingerprint=["paper_delete_sql_cleanup_failed", component, exception_type],
            include_request=False,
        )
    except Exception:
        logger.warning(
            "Failed to report PostgreSQL document cleanup failure",
            extra={"sentry_skip_event": True},
        )


async def run_thread_drained(
    func: Callable[..., _T],
    /,
    *args: Any,
    cancellable_before_start: bool = True,
    **kwargs: Any,
) -> _T:
    """Submit ``func`` to the default executor and always drain it before returning on cancellation.

    Ensures no worker thread continues executing SQL on a borrowed ``paper_operation_lock``
    connection concurrently with ``_release`` closing that connection. When
    ``cancellable_before_start`` is True, delivers any already-pending cancellation before
    submitting the worker.
    """
    if cancellable_before_start:
        await asyncio.sleep(0)
    loop = asyncio.get_running_loop()
    ctx = copy_context()

    def _invoke() -> _T:
        return ctx.run(func, *args, **kwargs)

    worker_future = loop.run_in_executor(None, _invoke)

    async def _await_worker() -> _T:
        return await worker_future

    return await drain_shielded(_await_worker())


async def drain_shielded(
    coro: Coroutine[Any, Any, _T],
    *,
    started: Callable[[], bool] | None = None,
    on_cancel: Callable[[], None] | None = None,
    on_cancelled_error: Callable[[Exception], None] | None = None,
) -> _T:
    """Finish owned destructive cleanup across cancellations, then deliver cancellation.

    When ``started`` is provided, cancellation arriving before ``started()`` becomes
    True aborts before destructive work begins (including across the ``create_task``
    scheduling gap and any pre-submission yield in the child). Once ``started()`` is
    True (or when omitted because the first destructive worker was already submitted
    synchronously), cancellation drains the inner task to completion before re-raising
    the original ``CancelledError``.
    """
    caller = asyncio.current_task()
    if started is not None and not started():
        try:
            await asyncio.sleep(0)
        except BaseException:
            coro.close()
            raise

    initial_cancels = caller.cancelling() if caller is not None else 0
    drain_state = _DELETE_DRAIN_STATE.get() if started is not None else None
    previous_owner_task = drain_state.owner_task if drain_state is not None else None
    previous_initial_cancels = drain_state.owner_initial_cancels if drain_state is not None else 0
    if drain_state is not None:
        drain_state.owner_task = caller
        drain_state.owner_initial_cancels = initial_cancels

    async def _guarded() -> _T:
        if (
            started is not None
            and not started()
            and caller is not None
            and caller.cancelling() > initial_cancels
        ):
            coro.close()
            raise asyncio.CancelledError
        return await coro

    task = asyncio.create_task(_guarded())
    cancelled: asyncio.CancelledError | None = None
    try:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                if cancelled is None:
                    cancelled = exc
                    if on_cancel is not None:
                        try:
                            on_cancel()
                        except (Exception, asyncio.CancelledError):
                            logger.warning(
                                "Cancelled cleanup notification callback failed",
                                extra={"sentry_skip_event": True},
                            )
                if started is not None and not started():
                    task.cancel()
            except Exception:
                if cancelled is None:
                    raise
        if task.cancelled():
            # Defensively close coro if the child task was cancelled before awaiting it.
            coro.close()
    finally:
        if drain_state is not None:
            drain_state.owner_task = previous_owner_task
            drain_state.owner_initial_cancels = previous_initial_cancels
    if cancelled is None:
        return task.result()
    if not task.cancelled():
        exc = task.exception()
        if exc is not None:
            if not isinstance(exc, Exception):
                raise exc
            if on_cancelled_error is not None:
                try:
                    on_cancelled_error(exc)
                except (Exception, asyncio.CancelledError):
                    logger.warning(
                        "Cancelled cleanup error callback failed",
                        extra={"sentry_skip_event": True},
                    )
    raise cancelled


def _scalar_list(result) -> list[Any]:
    scalars = result.scalars()
    if hasattr(scalars, "all"):
        return list(scalars.all())
    return list(scalars)


def cleanup_document_curation_dependencies(session: Session, document_id: UUID) -> dict[str, int]:
    """Detach and remove curation records that block pdf_documents deletion."""
    from src.lib.validation_history.store import retire_document_history
    retire_document_history(session, document_id)
    session_ids = _scalar_list(
        session.execute(
            select(CurationReviewSession.id).where(CurationReviewSession.document_id == document_id)
        )
    )
    candidate_ids = (
        _scalar_list(
            session.execute(
                select(CurationCandidate.id).where(
                    CurationCandidate.session_id.in_(session_ids)
                )
            )
        )
        if session_ids
        else []
    )
    extraction_result_ids = _scalar_list(
        session.execute(
            select(ExtractionResultModel.id).where(ExtractionResultModel.document_id == document_id)
        )
    )
    envelope_ids = [
        str(envelope_id)
        for envelope_id in _scalar_list(
            session.execute(
                select(DomainEnvelopeModel.envelope_id).where(
                    DomainEnvelopeModel.document_id == document_id
                )
            )
        )
    ]

    cleared_current_candidate_refs = 0
    deleted_action_logs = 0
    deleted_validation_snapshots = 0
    deleted_submissions = 0
    deleted_evidence_anchors = 0
    deleted_drafts = 0
    deleted_candidates = 0
    deleted_sessions = 0
    cleared_candidate_envelope_refs = 0
    deleted_domain_projection_index = 0
    deleted_domain_history = 0
    deleted_domain_validation_findings = 0
    deleted_domain_objects = 0
    deleted_domain_envelopes = 0

    if session_ids:
        cleared_current_candidate_refs = int(
            session.execute(
                update(CurationReviewSession)
                .where(CurationReviewSession.id.in_(session_ids))
                .values(current_candidate_id=None)
            ).rowcount
            or 0
        )
        deleted_action_logs = int(
            session.execute(
                delete(CurationActionLogEntry).where(
                    CurationActionLogEntry.session_id.in_(session_ids)
                )
            ).rowcount
            or 0
        )
        deleted_validation_snapshots = int(
            session.execute(
                delete(CurationValidationSnapshot).where(
                    CurationValidationSnapshot.session_id.in_(session_ids)
                )
            ).rowcount
            or 0
        )
        deleted_submissions = int(
            session.execute(
                delete(CurationSubmissionRecord).where(
                    CurationSubmissionRecord.session_id.in_(session_ids)
                )
            ).rowcount
            or 0
        )

    if candidate_ids:
        deleted_evidence_anchors = int(
            session.execute(
                delete(CurationEvidenceRecord).where(
                    CurationEvidenceRecord.candidate_id.in_(candidate_ids)
                )
            ).rowcount
            or 0
        )
        deleted_drafts = int(
            session.execute(
                delete(CurationDraft).where(CurationDraft.candidate_id.in_(candidate_ids))
            ).rowcount
            or 0
        )

    cleared_candidate_refs = (
        int(
            session.execute(
                update(CurationCandidate)
                .where(CurationCandidate.extraction_result_id.in_(extraction_result_ids))
                .values(extraction_result_id=None)
            ).rowcount
            or 0
        )
        if extraction_result_ids
        else 0
    )
    if session_ids:
        deleted_candidates = int(
            session.execute(
                delete(CurationCandidate).where(CurationCandidate.session_id.in_(session_ids))
            ).rowcount
            or 0
        )

    if envelope_ids:
        cleared_candidate_envelope_refs = int(
            session.execute(
                update(CurationCandidate)
                .where(CurationCandidate.envelope_id.in_(envelope_ids))
                .values(envelope_id=None, object_id=None, envelope_revision=None)
            ).rowcount
            or 0
        )
        deleted_domain_projection_index = int(
            session.execute(
                delete(DomainEnvelopeProjectionIndex).where(
                    DomainEnvelopeProjectionIndex.envelope_id.in_(envelope_ids)
                )
            ).rowcount
            or 0
        )
        deleted_domain_history = int(
            session.execute(
                delete(DomainEnvelopeHistory).where(
                    DomainEnvelopeHistory.envelope_id.in_(envelope_ids)
                )
            ).rowcount
            or 0
        )
        deleted_domain_validation_findings = int(
            session.execute(
                delete(DomainValidationFinding).where(
                    DomainValidationFinding.envelope_id.in_(envelope_ids)
                )
            ).rowcount
            or 0
        )
        deleted_domain_objects = int(
            session.execute(
                delete(DomainEnvelopeObject).where(
                    DomainEnvelopeObject.envelope_id.in_(envelope_ids)
                )
            ).rowcount
            or 0
        )
        deleted_domain_envelopes = int(
            session.execute(
                delete(DomainEnvelopeModel).where(
                    DomainEnvelopeModel.envelope_id.in_(envelope_ids)
                )
            ).rowcount
            or 0
        )

    if session_ids:
        deleted_sessions = int(
            session.execute(
                delete(CurationReviewSession).where(CurationReviewSession.id.in_(session_ids))
            ).rowcount
            or 0
        )
    deleted_extraction_results = (
        int(
            session.execute(
                delete(ExtractionResultModel).where(
                    ExtractionResultModel.id.in_(extraction_result_ids)
                )
            ).rowcount
            or 0
        )
        if extraction_result_ids
        else 0
    )
    return {
        "current_candidate_refs_cleared": cleared_current_candidate_refs,
        "candidate_refs_cleared": cleared_candidate_refs,
        "action_logs_deleted": deleted_action_logs,
        "validation_snapshots_deleted": deleted_validation_snapshots,
        "submissions_deleted": deleted_submissions,
        "evidence_anchors_deleted": deleted_evidence_anchors,
        "drafts_deleted": deleted_drafts,
        "candidates_deleted": deleted_candidates,
        "sessions_deleted": deleted_sessions,
        "extraction_results_deleted": deleted_extraction_results,
        "candidate_envelope_refs_cleared": cleared_candidate_envelope_refs,
        "domain_projection_index_deleted": deleted_domain_projection_index,
        "domain_history_deleted": deleted_domain_history,
        "domain_validation_findings_deleted": deleted_domain_validation_findings,
        "domain_objects_deleted": deleted_domain_objects,
        "domain_envelopes_deleted": deleted_domain_envelopes,
    }
