"""Shared read-only handoff substrate for export-only submission transports."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from src.lib.curation_workspace.submission_adapters.base import (
    SubmissionTransportAdapter,
    SubmissionTransportResult,
    normalize_submission_transport_result,
)
from src.schemas.curation_workspace import SubmissionPayloadContract


READ_ONLY_HANDOFF_WRITE_MODE = "read_only_handoff"
READ_ONLY_HANDOFF_WARNING = (
    "Read-only handoff recorded; no curation database rows were mutated."
)


class ReadOnlyHandoffSubmissionAdapter(SubmissionTransportAdapter):
    """Base adapter for validated exports awaiting an approved write transport.

    Domain adapters remain responsible for validating their target-specific payload
    shape and constructing the external reference. This shared substrate owns the
    successful export-only posture and never mutates a downstream database.
    """

    def build_read_only_handoff_result(
        self,
        *,
        payload: SubmissionPayloadContract,
        idempotency_key: str,
        external_reference: str,
        response_message: str,
        submission_state: Mapping[str, Any] | None = None,
        target_result_state: Mapping[str, Any] | None = None,
        warnings: Sequence[str] = (),
    ) -> SubmissionTransportResult:
        """Return the canonical persisted result for one non-mutating handoff."""

        if not external_reference.strip():
            raise ValueError("Read-only handoff results require an external reference")

        normalized_submission_state = {
            **dict(submission_state or {}),
            "idempotency_key": idempotency_key,
            "target_status": "manual_review_required",
            "target_key": payload.target_key,
            "target_transport": self.transport_key,
            "external_reference": external_reference,
            "write_mode": READ_ONLY_HANDOFF_WRITE_MODE,
        }
        normalized_target_result_state = {
            **dict(target_result_state or {}),
            "status": "manual_review_required",
            "target_key": payload.target_key,
            "write_mode": READ_ONLY_HANDOFF_WRITE_MODE,
        }
        return normalize_submission_transport_result(
            status="manual_review_required",
            external_reference=external_reference,
            response_message=response_message,
            warnings=(READ_ONLY_HANDOFF_WARNING, *warnings),
            submission_state=normalized_submission_state,
            target_result_history=(normalized_target_result_state,),
        )


__all__ = [
    "READ_ONLY_HANDOFF_WARNING",
    "READ_ONLY_HANDOFF_WRITE_MODE",
    "ReadOnlyHandoffSubmissionAdapter",
]
