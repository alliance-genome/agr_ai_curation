"""Unit tests for curation-workspace submission transport adapters."""

from __future__ import annotations

import pytest

from src.lib.curation_workspace.submission_adapters import (
    DEFAULT_NOOP_SUBMISSION_TARGET_KEY,
    NoOpSubmissionAdapter,
    SubmissionAdapterRegistry,
    SubmissionTransportError,
    build_default_submission_adapter_registry,
    normalize_submission_transport_result,
)
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


def test_submission_adapter_registry_registers_and_looks_up_adapters():
    registry = SubmissionAdapterRegistry()
    adapter = NoOpSubmissionAdapter(target_key="submit.target")

    registry.register(adapter)

    assert registry.get("submit.target") is adapter
    assert registry.require("submit.target") is adapter
    assert registry.target_keys() == ("submit.target",)


def test_build_default_submission_adapter_registry_exposes_reference_adapter():
    registry = build_default_submission_adapter_registry()

    adapter = registry.require(DEFAULT_NOOP_SUBMISSION_TARGET_KEY)

    assert isinstance(adapter, NoOpSubmissionAdapter)
    assert registry.target_keys() == (DEFAULT_NOOP_SUBMISSION_TARGET_KEY,)


def test_noop_submission_adapter_invokes_transport_with_mock_payload():
    adapter = NoOpSubmissionAdapter(
        response_status=CurationSubmissionStatus.QUEUED,
        response_message="Queued for downstream processing.",
        warnings=["Awaiting downstream worker."],
    )

    result = adapter.submit(payload=_payload())

    assert result.status == CurationSubmissionStatus.QUEUED
    assert result.external_reference == f"noop:{DEFAULT_NOOP_SUBMISSION_TARGET_KEY}:1"
    assert result.response_message == "Queued for downstream processing."
    assert result.validation_errors == ()
    assert result.warnings == ("Awaiting downstream worker.",)
    assert result.completed_at is not None


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
        adapter.submit(payload=_payload(target_key="other.target"))

    assert "does not support target" in str(exc.value)
