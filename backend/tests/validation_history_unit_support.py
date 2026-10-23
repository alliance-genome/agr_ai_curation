"""IO-only double for dispatcher unit tests; PostgreSQL tests exercise real history.

This does not replace runners, result finalization, or materialization. It only
supplies the durable receipts that are an input to those units after cutover.
"""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture
def isolated_validation_history(monkeypatch):
    from src.lib.validation_history import runtime, store
    monkeypatch.setattr("src.lib.domain_envelopes.persistence.lock_projection_artifact", lambda *args: None)

    monkeypatch.setattr(store, "record_scheduling", lambda *args, **kwargs: str(uuid4()))
    records = {}
    serial = 0
    binding = SimpleNamespace(
        artifact_id=str(uuid4()),
        scope=SimpleNamespace(
            session_id="unit-run", owner_id="unit-owner", document_id=str(uuid4())
        ),
    )

    def begin(bound, jobs, *, operation_id, receipt):
        attempts = [str(uuid4()) for _ in jobs]
        for attempt, job in zip(attempts, jobs):
            records[attempt] = deepcopy(job.request.model_dump(mode="json"))
        return {"execution_id": operation_id, "attempt_ids": attempts}

    def attach(result, state, attempt=None):
        nonlocal serial
        serial += 1
        result._history_ref = {
            "artifact_id": binding.artifact_id,
            "session_id": binding.scope.session_id,
            "fact_id": str(uuid4()),
            "body_sha256": "unit-body",
            "sequence": serial,
            "created_at": "2026-10-22T00:00:00+00:00",
            "execution_state": state,
            **({"attempt_id": attempt} if attempt else {}),
        }
        return result._history_ref["fact_id"]

    def complete(bound, invocation, results):
        return [
            attach(
                result,
                "failed"
                if result._execution_failure
                else "incomplete"
                if result.output_issues
                else "completed",
                attempt,
            )
            for attempt, result in zip(invocation["attempt_ids"], results, strict=True)
        ]

    from src.lib.validation_history import candidates, selections

    def declare(bound, envelope, matches, **kwargs):
        return envelope.model_copy(update={"metadata": {
            **envelope.metadata,
            "validation_history": {"artifact_id": binding.artifact_id, "session_id": binding.scope.session_id},
            "review_episode_id": str(uuid4()),
        }})

    monkeypatch.setattr(selections, "declare_envelope_episode", declare)
    monkeypatch.setattr(candidates, "_record_recipe", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "record_match_scheduling", lambda *args, **kwargs: str(uuid4()))
    monkeypatch.setattr(runtime, "capture_extraction", lambda **kwargs: kwargs["envelope"])
    monkeypatch.setattr(runtime, "bind_artifact", lambda envelope, context: binding)
    monkeypatch.setattr(runtime, "capture_derived_objects", lambda bound, envelope, results, **kwargs: envelope)
    monkeypatch.setattr(runtime, "record_runner_receipt", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "begin_jobs", begin)
    monkeypatch.setattr(runtime, "complete_jobs", complete)
    monkeypatch.setattr(
        runtime,
        "record_preflight",
        lambda bound, job, result, **kw: attach(result, "not_executed"),
    )
    monkeypatch.setattr(runtime, "fail_jobs", lambda *args: None)
    monkeypatch.setattr(
        runtime,
        "attach_failed_completion",
        lambda bound, attempt, result: attach(result, "failed", attempt),
    )
    return records


def result_with_history_receipt(result):
    """A completed capture receipt supplied to a pure materializer unit test."""
    result._history_ref = {
        "artifact_id": str(uuid4()), "session_id": "unit-run", "fact_id": str(uuid4()),
        "body_sha256": "unit-body", "sequence": 1, "created_at": "2026-10-22T00:00:00+00:00",
        "execution_state": "incomplete" if result.output_issues else "completed",
    }
    return result
