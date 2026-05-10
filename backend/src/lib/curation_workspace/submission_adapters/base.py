"""Transport adapter contracts for external curation submission targets."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from src.schemas.curation_workspace import (
    CurationSubmissionStatus,
    SubmissionPayloadContract,
    SubmissionTargetKey,
)


DIRECT_SUBMISSION_RESULT_STATUSES = frozenset(
    {
        CurationSubmissionStatus.QUEUED,
        CurationSubmissionStatus.ACCEPTED,
        CurationSubmissionStatus.VALIDATION_ERRORS,
        CurationSubmissionStatus.CONFLICT,
        CurationSubmissionStatus.MANUAL_REVIEW_REQUIRED,
        CurationSubmissionStatus.FAILED,
    }
)


@dataclass(frozen=True)
class SubmissionTransportResult:
    """Normalized result returned by an external submission transport adapter."""

    status: CurationSubmissionStatus
    external_reference: str | None = None
    response_message: str | None = None
    validation_errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    completed_at: datetime | None = None
    submission_state: Mapping[str, Any] | None = None
    target_result_history: tuple[Mapping[str, Any], ...] = ()


class SubmissionTransportError(Exception):
    """Adapter-raised error that still maps to a curator-visible submission response."""

    def __init__(
        self,
        message: str,
        *,
        status: CurationSubmissionStatus = CurationSubmissionStatus.FAILED,
        external_reference: str | None = None,
        validation_errors: Sequence[str] = (),
        warnings: Sequence[str] = (),
        completed_at: datetime | None = None,
        submission_state: Mapping[str, Any] | None = None,
        target_result_history: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.external_reference = external_reference
        self.validation_errors = tuple(validation_errors)
        self.warnings = tuple(warnings)
        self.completed_at = completed_at
        self.submission_state = submission_state
        self.target_result_history = tuple(target_result_history)

    def to_result(self) -> SubmissionTransportResult:
        """Return this exception as a normalized submission result payload."""

        return normalize_submission_transport_result(
            status=self.status,
            external_reference=self.external_reference,
            response_message=self.message,
            validation_errors=self.validation_errors,
            warnings=self.warnings,
            completed_at=self.completed_at,
            submission_state=self.submission_state,
            target_result_history=self.target_result_history,
        )


class SubmissionTransportAdapter(ABC):
    """Base class for adapter-owned submission transport implementations."""

    transport_key: str
    supported_target_keys: Sequence[SubmissionTargetKey]

    def __init__(
        self,
        *,
        transport_key: str,
        supported_target_keys: Sequence[SubmissionTargetKey],
    ) -> None:
        self.transport_key = transport_key
        self.supported_target_keys = tuple(supported_target_keys)

    def submit(self, *, payload: SubmissionPayloadContract) -> SubmissionTransportResult:
        """Validate the target key and return a normalized submission result."""

        self._validate_target_key(payload.target_key)
        return coerce_submission_transport_result(self._submit(payload=payload))

    def _validate_target_key(self, target_key: SubmissionTargetKey) -> None:
        if self.supported_target_keys and target_key not in self.supported_target_keys:
            supported_targets = ", ".join(self.supported_target_keys)
            raise ValueError(
                f"Submission adapter '{self.transport_key}' does not support target "
                f"'{target_key}'. Supported targets: {supported_targets}"
            )

    @abstractmethod
    def _submit(
        self,
        *,
        payload: SubmissionPayloadContract,
    ) -> SubmissionTransportResult | Mapping[str, Any]:
        """Deliver one submission payload to an external system."""


def coerce_submission_transport_result(
    result: SubmissionTransportResult | Mapping[str, Any],
) -> SubmissionTransportResult:
    """Convert an adapter-owned result payload into the normalized transport contract."""

    if isinstance(result, SubmissionTransportResult):
        return normalize_submission_transport_result(
            status=result.status,
            external_reference=result.external_reference,
            response_message=result.response_message,
            validation_errors=result.validation_errors,
            warnings=result.warnings,
            completed_at=result.completed_at,
            submission_state=result.submission_state,
            target_result_history=result.target_result_history,
        )

    if not isinstance(result, Mapping):
        raise TypeError("Submission transport adapters must return a mapping or result object")

    return normalize_submission_transport_result(
        status=result.get("status", CurationSubmissionStatus.FAILED),
        external_reference=result.get("external_reference"),
        response_message=result.get("response_message"),
        validation_errors=result.get("validation_errors") or (),
        warnings=result.get("warnings") or (),
        completed_at=result.get("completed_at"),
        submission_state=result.get("submission_state"),
        target_result_history=result.get("target_result_history", ()),
    )


def normalize_submission_transport_result(
    *,
    status: CurationSubmissionStatus | str,
    external_reference: str | None = None,
    response_message: str | None = None,
    validation_errors: Sequence[str] = (),
    warnings: Sequence[str] = (),
    completed_at: datetime | None = None,
    submission_state: Mapping[str, Any] | None = None,
    target_result_history: Sequence[Mapping[str, Any]] | None = (),
) -> SubmissionTransportResult:
    """Normalize one adapter response into the shared transport result contract."""

    history_items = () if target_result_history is None else target_result_history
    return SubmissionTransportResult(
        status=CurationSubmissionStatus(status),
        external_reference=_normalize_optional_string(external_reference),
        response_message=_normalize_optional_string(response_message),
        validation_errors=tuple(_dedupe_preserve_order(validation_errors)),
        warnings=tuple(_dedupe_preserve_order(warnings)),
        completed_at=completed_at or datetime.now(timezone.utc),
        submission_state=_normalize_mapping(submission_state, field_name="submission_state"),
        target_result_history=tuple(
            _normalize_mapping(item, field_name="target_result_history item")
            for item in history_items
        ),
    )


def _dedupe_preserve_order(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(_normalize_string(value) for value in values if _normalize_string(value)))


def _normalize_optional_string(value: str | None) -> str | None:
    normalized = _normalize_string(value)
    return normalized or None


def _normalize_string(value: object) -> str:
    return str(value or "").strip()


def _normalize_mapping(value: Mapping[str, Any] | None, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    return dict(value)
