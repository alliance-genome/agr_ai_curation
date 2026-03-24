"""Reference no-op submission adapter for direct-submit transport wiring."""

from __future__ import annotations

from typing import Sequence

from src.lib.curation_workspace.export_adapters import DEFAULT_JSON_BUNDLE_TARGET_KEY
from src.lib.curation_workspace.submission_adapters.base import (
    SubmissionTransportAdapter,
    SubmissionTransportResult,
    normalize_submission_transport_result,
)
from src.schemas.curation_workspace import CurationSubmissionStatus, SubmissionPayloadContract


DEFAULT_NOOP_SUBMISSION_TARGET_KEY = DEFAULT_JSON_BUNDLE_TARGET_KEY


class NoOpSubmissionAdapter(SubmissionTransportAdapter):
    """Reference adapter that simulates one downstream submission result."""

    def __init__(
        self,
        *,
        target_key: str = DEFAULT_NOOP_SUBMISSION_TARGET_KEY,
        response_status: CurationSubmissionStatus = CurationSubmissionStatus.ACCEPTED,
        response_message: str | None = None,
        warnings: Sequence[str] = (),
        validation_errors: Sequence[str] = (),
        error: Exception | None = None,
    ) -> None:
        super().__init__(
            transport_key="noop_submission",
            supported_target_keys=(target_key,),
        )
        self._response_status = response_status
        self._response_message = response_message
        self._warnings = tuple(warnings)
        self._validation_errors = tuple(validation_errors)
        self._error = error

    def _submit(
        self,
        *,
        payload: SubmissionPayloadContract,
    ) -> SubmissionTransportResult:
        if self._error is not None:
            raise self._error

        candidate_count = len(payload.candidate_ids)
        response_message = self._response_message or _default_response_message(
            status=self._response_status,
            candidate_count=candidate_count,
            target_key=payload.target_key,
        )
        external_reference = (
            None
            if self._response_status
            in {
                CurationSubmissionStatus.VALIDATION_ERRORS,
                CurationSubmissionStatus.CONFLICT,
                CurationSubmissionStatus.FAILED,
            }
            else f"noop:{payload.target_key}:{candidate_count}"
        )
        return normalize_submission_transport_result(
            status=self._response_status,
            external_reference=external_reference,
            response_message=response_message,
            validation_errors=self._validation_errors,
            warnings=self._warnings,
        )


def _default_response_message(
    *,
    status: CurationSubmissionStatus,
    candidate_count: int,
    target_key: str,
) -> str:
    verb = {
        CurationSubmissionStatus.ACCEPTED: "accepted",
        CurationSubmissionStatus.QUEUED: "queued",
        CurationSubmissionStatus.VALIDATION_ERRORS: "rejected with validation errors",
        CurationSubmissionStatus.CONFLICT: "rejected due to a conflict",
        CurationSubmissionStatus.MANUAL_REVIEW_REQUIRED: "requires manual review",
        CurationSubmissionStatus.FAILED: "failed",
        CurationSubmissionStatus.PREVIEW_READY: "returned an invalid preview-ready state",
        CurationSubmissionStatus.EXPORT_READY: "returned an invalid export-ready state",
    }[status]
    return (
        f"No-op submission adapter {verb} {candidate_count} candidate(s) for target "
        f"'{target_key}'."
    )
