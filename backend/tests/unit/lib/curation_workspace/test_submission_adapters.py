"""Unit tests for curation-workspace submission transport adapters."""

from __future__ import annotations

import pytest

from src.lib.curation_workspace.submission_adapters import (
    DEFAULT_NOOP_SUBMISSION_TARGET_KEY,
    NoOpSubmissionAdapter,
    READ_ONLY_HANDOFF_WARNING,
    ReadOnlyHandoffSubmissionAdapter,
    SubmissionAdapterRegistry,
    SubmissionTransportError,
    SubmissionTransportResult,
    build_default_submission_adapter_registry,
    coerce_submission_transport_result,
    normalize_submission_transport_result,
)
from src.lib.curation_workspace.adapter_registry import load_curation_adapter_registry
from src.schemas.curation_workspace import (
    CurationSubmissionStatus,
    SubmissionMode,
    SubmissionPayloadContract,
)


def _payload(*, target_key: str = DEFAULT_NOOP_SUBMISSION_TARGET_KEY) -> SubmissionPayloadContract:
    return SubmissionPayloadContract(
        mode=SubmissionMode.DIRECT_SUBMIT,
        target_key=target_key,
        adapter_key="reference",
        candidate_ids=["candidate-1"],
        payload_json={"candidate_count": 1},
    )


class _ExampleReadOnlyHandoffAdapter(ReadOnlyHandoffSubmissionAdapter):
    def __init__(self) -> None:
        super().__init__(
            transport_key="example_read_only_handoff",
            supported_target_keys=("submit.example",),
        )

    def _submit(
        self,
        *,
        payload: SubmissionPayloadContract,
        idempotency_key: str,
    ) -> SubmissionTransportResult:
        return self.build_read_only_handoff_result(
            payload=payload,
            idempotency_key=idempotency_key,
            external_reference="example:submit.example:1",
            response_message="Export prepared for manual handoff.",
            submission_state={"record_count": 1},
            target_result_state={"record_count": 1},
        )


def test_submission_adapter_registry_registers_and_looks_up_adapters():
    registry = SubmissionAdapterRegistry()
    adapter = NoOpSubmissionAdapter(target_key="submit.target")

    registry.register(adapter)

    assert registry.get("submit.target") is adapter
    assert registry.require("submit.target") is adapter
    assert registry.target_keys() == ("submit.target",)


def test_build_default_submission_adapter_registry_exposes_reference_adapter():
    load_curation_adapter_registry.cache_clear()
    try:
        registry = build_default_submission_adapter_registry()
    finally:
        load_curation_adapter_registry.cache_clear()

    adapter = registry.require(DEFAULT_NOOP_SUBMISSION_TARGET_KEY)

    assert isinstance(adapter, NoOpSubmissionAdapter)
    assert DEFAULT_NOOP_SUBMISSION_TARGET_KEY in registry.target_keys()


def test_noop_submission_adapter_invokes_transport_with_mock_payload():
    adapter = NoOpSubmissionAdapter(
        response_status=CurationSubmissionStatus.QUEUED,
        response_message="Queued for downstream processing.",
        warnings=["Awaiting downstream worker."],
    )

    result = adapter.submit(payload=_payload(), idempotency_key="test-submit")

    assert result.status == CurationSubmissionStatus.QUEUED
    assert result.external_reference == f"noop:{DEFAULT_NOOP_SUBMISSION_TARGET_KEY}:1"
    assert result.response_message == "Queued for downstream processing."
    assert result.validation_errors == ()
    assert result.warnings == ("Awaiting downstream worker.",)
    assert result.completed_at is not None


def test_read_only_handoff_adapter_returns_real_non_mutating_result():
    result = _ExampleReadOnlyHandoffAdapter().submit(
        payload=_payload(target_key="submit.example"),
        idempotency_key="test-handoff",
    )

    assert result.status is CurationSubmissionStatus.MANUAL_REVIEW_REQUIRED
    assert result.external_reference == "example:submit.example:1"
    assert result.warnings == (READ_ONLY_HANDOFF_WARNING,)
    assert result.submission_state == {
        "record_count": 1,
        "idempotency_key": "test-handoff",
        "target_status": "manual_review_required",
        "target_key": "submit.example",
        "target_transport": "example_read_only_handoff",
        "external_reference": "example:submit.example:1",
        "write_mode": "read_only_handoff",
    }
    assert result.target_result_history == (
        {
            "record_count": 1,
            "status": "manual_review_required",
            "target_key": "submit.example",
            "write_mode": "read_only_handoff",
        },
    )


@pytest.mark.parametrize("status_value", list(CurationSubmissionStatus))
def test_normalize_submission_transport_result_supports_each_submission_status(status_value):
    result = normalize_submission_transport_result(
        status=status_value,
        response_message="  Normalized result.  ",
        validation_errors=["first", "first", "second"],
        warnings=["warning", "warning"],
    )

    assert result.status == status_value
    assert result.response_message == "Normalized result."
    assert result.validation_errors == ("first", "second")
    assert result.warnings == ("warning",)
    assert result.completed_at is not None


def test_normalize_submission_transport_result_rejects_malformed_history_entries():
    with pytest.raises(TypeError, match="target_result_history item must be a mapping"):
        normalize_submission_transport_result(
            status=CurationSubmissionStatus.ACCEPTED,
            target_result_history=["not a mapping"],
        )


def test_coerce_submission_transport_result_rejects_malformed_history_entries():
    with pytest.raises(TypeError, match="target_result_history item must be a mapping"):
        coerce_submission_transport_result(
            {
                "status": CurationSubmissionStatus.ACCEPTED,
                "target_result_history": ["not a mapping"],
            }
        )


def test_submission_transport_error_produces_failed_result_payload():
    error = SubmissionTransportError(
        "Downstream timeout",
        warnings=["Retry later."],
        validation_errors=["network timeout"],
    )

    result = error.to_result()

    assert result.status == CurationSubmissionStatus.FAILED
    assert result.response_message == "Downstream timeout"
    assert result.validation_errors == ("network timeout",)
    assert result.warnings == ("Retry later.",)
    assert result.completed_at is not None


def test_submission_adapter_rejects_unsupported_target_key():
    adapter = NoOpSubmissionAdapter(target_key="submit.target")

    with pytest.raises(ValueError) as exc:
        adapter.submit(
            payload=_payload(target_key="other.target"),
            idempotency_key="test-submit",
        )

    assert "does not support target" in str(exc.value)
