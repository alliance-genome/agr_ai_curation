"""Shared explicit-blocker submission helpers for Alliance domain packs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.lib.curation_workspace.submission_adapters.base import (
    SubmissionTransportResult,
    normalize_submission_transport_result,
)
from src.schemas.curation_workspace import CurationSubmissionStatus, SubmissionPayloadContract


def blocked_submission_result(
    *,
    payload: SubmissionPayloadContract,
    domain_label: str,
    reason: str,
    blocked_operations: Sequence[str],
    required_before_write: Sequence[str],
) -> SubmissionTransportResult:
    """Return an explicit non-writing submission response for unverified targets."""

    payload_json = payload.payload_json if isinstance(payload.payload_json, Mapping) else {}
    adapter_blockers = payload_json.get("adapter_blockers")
    if not isinstance(adapter_blockers, list):
        adapter_blockers = []

    validation_errors = [
        str(blocker.get("message"))
        for blocker in adapter_blockers
        if isinstance(blocker, Mapping) and blocker.get("message")
    ]
    validation_errors.append(reason)

    return normalize_submission_transport_result(
        status=CurationSubmissionStatus.MANUAL_REVIEW_REQUIRED,
        response_message=(
            f"{domain_label} direct submission is blocked; no verified write "
            "transport is configured for this target."
        ),
        validation_errors=validation_errors,
        warnings=payload.warnings,
        submission_state={
            "target_key": payload.target_key,
            "adapter_key": payload.adapter_key,
            "candidate_ids": list(payload.candidate_ids),
            "write_behavior": {
                "status": "blocked",
                "reason": reason,
                "blocked_operations": list(blocked_operations),
                "required_before_write": list(required_before_write),
            },
            "adapter_blockers": adapter_blockers,
        },
        target_result_history=[
            {
                "status": "blocked",
                "target_key": payload.target_key,
                "reason": reason,
            }
        ],
    )
