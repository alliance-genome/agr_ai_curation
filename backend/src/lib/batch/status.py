"""Canonical batch and batch-document status transition rules."""

from collections.abc import Mapping

from src.models.sql.batch import BatchDocumentStatus, BatchStatus


BATCH_STATUS_TRANSITIONS: Mapping[BatchStatus, frozenset[BatchStatus]] = {
    BatchStatus.PENDING: frozenset({BatchStatus.RUNNING, BatchStatus.CANCELLED}),
    BatchStatus.RUNNING: frozenset({BatchStatus.COMPLETED, BatchStatus.CANCELLED}),
    BatchStatus.COMPLETED: frozenset(),
    BatchStatus.CANCELLED: frozenset(),
}

BATCH_DOCUMENT_STATUS_TRANSITIONS: Mapping[
    BatchDocumentStatus, frozenset[BatchDocumentStatus]
] = {
    BatchDocumentStatus.PENDING: frozenset(
        {BatchDocumentStatus.PROCESSING, BatchDocumentStatus.FAILED}
    ),
    BatchDocumentStatus.PROCESSING: frozenset(
        {BatchDocumentStatus.COMPLETED, BatchDocumentStatus.FAILED}
    ),
    BatchDocumentStatus.COMPLETED: frozenset(),
    BatchDocumentStatus.FAILED: frozenset(),
}


def require_batch_status_transition(
    current: BatchStatus,
    target: BatchStatus,
) -> None:
    """Reject a batch status transition not allowed by the state machine."""
    if target not in BATCH_STATUS_TRANSITIONS[current]:
        raise ValueError(
            f"Invalid batch status transition: {current.value} -> {target.value}"
        )


def require_batch_document_status_transition(
    current: BatchDocumentStatus,
    target: BatchDocumentStatus,
) -> None:
    """Reject a batch-document transition not allowed by the state machine."""
    if target not in BATCH_DOCUMENT_STATUS_TRANSITIONS[current]:
        raise ValueError(
            "Invalid batch document status transition: "
            f"{current.value} -> {target.value}"
        )
