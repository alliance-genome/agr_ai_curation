"""Audit failures never expose database parameters or provider exception chains."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError

from src.lib.validation_history.runtime import fail_jobs


def test_failure_capture_severs_sensitive_exception_chain():
    reports = []
    provider = RuntimeError("PRIVATE_PROVIDER_PAPER")

    def failing_capture(*args, **kwargs):
        raise IntegrityError(
            "PRIVATE_SQL", {"text": "PRIVATE_PARAMETER"}, RuntimeError("PRIVATE_DB")
        )

    binding = SimpleNamespace(scope=None, artifact_id="fixture")
    with (
        patch(
            "src.lib.validation_history.store.complete_attempt",
            side_effect=failing_capture,
        ),
        patch(
            "src.lib.observability.runtime.report_runtime_exception",
            side_effect=lambda exc, **kw: reports.append(exc),
        ),
    ):
        with pytest.raises(RuntimeError, match="PRIVATE_PROVIDER_PAPER"):
            try:
                raise provider
            except RuntimeError as original:
                fail_jobs(binding, {"attempt_ids": ["attempt"]}, original)
                raise
    assert len(reports) == 1
    captured = reports[0]
    assert str(captured) == "Validation history write failed (IntegrityError)"
    assert captured.__cause__ is None and captured.__context__ is None
    assert captured.__traceback__ is not None


def test_receipt_failure_is_operational_unknown_and_cannot_call_provider():
    from src.lib.validation_history.runtime import (
        ACTIVE_VALIDATION_EXECUTION,
        record_runner_receipt,
    )
    from src.lib.validation_history.store import HistoryRecordingError

    binding = SimpleNamespace(scope=None, artifact_id="fixture")
    token = ACTIVE_VALIDATION_EXECUTION.set((binding, {"execution_id": "execution"}))
    try:
        with patch(
            "src.lib.validation_history.store.SessionLocal.begin",
            side_effect=RuntimeError("PRIVATE_DATABASE"),
        ):
            with pytest.raises(HistoryRecordingError) as caught:
                record_runner_receipt(
                    SimpleNamespace(instructions="PRIVATE_PROMPT", tools=[]),
                    model="fake",
                    input_payload={},
                )
        assert "PRIVATE" not in str(caught.value)
        assert caught.value.__context__ is None and caught.value.__cause__ is None
        completions = []
        with patch(
            "src.lib.validation_history.store.complete_attempt",
            side_effect=lambda *a, **kw: completions.append(kw),
        ):
            fail_jobs(binding, {"attempt_ids": ["attempt"]}, caught.value)
        assert completions[0]["state"] == "unknown" and completions[0]["result"] is None
    finally:
        ACTIVE_VALIDATION_EXECUTION.reset(token)


@pytest.mark.parametrize("sidecar", [None, {}, {"artifact_id": "missing-session"}])
def test_missing_capture_is_an_explicit_access_failure(sidecar):
    from src.lib.validation_history.runtime import (
        bind_artifact,
        require_captured_envelope,
    )
    from src.lib.validation_history.store import HistoryAccessError

    metadata = {} if sidecar is None else {"validation_history": sidecar}
    with pytest.raises(HistoryAccessError, match="captured extraction"):
        bind_artifact(SimpleNamespace(metadata=metadata), SimpleNamespace())
    with pytest.raises(HistoryAccessError, match="captured extraction"):
        require_captured_envelope(
            {"metadata": metadata}, user_id="owner", document_id="doc"
        )
