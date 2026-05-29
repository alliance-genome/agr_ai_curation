"""Unit tests for the extraction builder workspace lifecycle."""

from __future__ import annotations

import pytest

from src.lib.openai_agents import extraction_builder_workspace as builder


@pytest.fixture
def captured_events(monkeypatch):
    events = []
    monkeypatch.setattr(
        builder,
        "write_extraction_trace_event",
        lambda **event: events.append(event) or event,
    )
    return events


def _workspace() -> builder.ExtractionBuilderWorkspace:
    return builder.ExtractionBuilderWorkspace(
        run_id="trace-1",
        document_id="doc-1",
        domain_pack_id="pack-1",
        agent_id="agent-1",
    )


def test_upsert_is_idempotent_for_retry_and_tracks_state(captured_events):
    workspace = _workspace()

    first = workspace.upsert_candidate(
        candidate_id="candidate-1",
        staged_fields={"items": [{"label": "crumb"}]},
        pending_ref_ids=["pending-1", "pending-1"],
        evidence_record_ids=["evidence-1"],
        resolver_selection_refs=["resolver:gene:1"],
    )
    second = workspace.upsert_candidate(
        candidate_id="candidate-1",
        staged_fields={"items": [{"label": "crumb"}]},
        pending_ref_ids=["pending-1"],
        evidence_record_ids=["evidence-1"],
        resolver_selection_refs=["resolver:gene:1"],
    )

    assert first is second
    assert list(workspace.candidates) == ["candidate-1"]
    assert workspace.snapshot()["pending_ref_ids"] == ["pending-1"]
    assert [event["event_type"] for event in captured_events] == [
        "extraction_builder.candidate_mutation",
        "extraction_builder.candidate_mutation",
    ]


def test_finalize_rejects_late_mutation_and_duplicate_identical_finalize_is_idempotent(
    captured_events,
):
    workspace = _workspace()
    workspace.upsert_candidate(
        candidate_id="candidate-1",
        staged_fields={"items": [{"label": "crumb"}]},
        evidence_record_ids=["evidence-1"],
        resolver_selection_refs=["resolver:gene:1", "resolver:gene:2"],
    )

    finalization = workspace.finalize(candidate_ids=["candidate-1"])
    duplicate = workspace.finalize(candidate_ids=["candidate-1"])

    assert duplicate is finalization
    assert finalization.summary() == {
        "status": "finalized",
        "finalized_candidate_count": 1,
        "validation_errors": [],
        "evidence_record_ids": ["evidence-1"],
        "resolver_selection_count": 2,
        "builder_run_id": "trace-1",
        "candidate_ids": ["candidate-1"],
    }
    with pytest.raises(builder.ExtractionBuilderFinalizedError):
        workspace.upsert_candidate(
            candidate_id="candidate-2",
            staged_fields={"items": []},
        )
    with pytest.raises(builder.ExtractionBuilderFinalizedError):
        workspace.record_validation_failure(
            errors=[{"message": "late failure", "reason": "late"}],
            candidate_ids=["candidate-1"],
        )


def test_duplicate_finalize_with_same_membership_different_order_is_idempotent(
    captured_events,
):
    workspace = _workspace()
    workspace.upsert_candidate(candidate_id="candidate-1", staged_fields={"items": [{"id": 1}]})
    workspace.upsert_candidate(candidate_id="candidate-2", staged_fields={"items": [{"id": 2}]})

    finalization = workspace.finalize(candidate_ids=["candidate-1", "candidate-2"])
    duplicate = workspace.finalize(candidate_ids=["candidate-2", "candidate-1"])

    assert duplicate is finalization
    assert finalization.candidate_ids == ("candidate-1", "candidate-2")
    assert finalization.payload["candidates"] == [
        {"items": [{"id": 1}]},
        {"items": [{"id": 2}]},
    ]


def test_conflicting_duplicate_finalize_fails_clearly(captured_events):
    workspace = _workspace()
    workspace.upsert_candidate(candidate_id="candidate-1", staged_fields={"items": []})
    workspace.upsert_candidate(candidate_id="candidate-2", staged_fields={"items": []})
    workspace.finalize(candidate_ids=["candidate-1"])

    with pytest.raises(builder.ExtractionBuilderFinalizationConflict, match="different candidate membership"):
        workspace.finalize(candidate_ids=["candidate-2"])


def test_validation_failure_supports_repair_before_successful_finalize(captured_events):
    workspace = _workspace()
    workspace.upsert_candidate(candidate_id="candidate-1", staged_fields={"items": []})

    with pytest.raises(builder.ExtractionBuilderValidationError):
        workspace.finalize(
            candidate_ids=["candidate-1"],
            validation_errors=[{"message": "missing evidence", "reason": "missing_evidence"}],
        )

    assert workspace.state == builder.BUILDER_STATE_VALIDATION_FAILED
    assert workspace.candidates["candidate-1"].status == builder.CANDIDATE_STATUS_NEEDS_PATCH

    workspace.upsert_candidate(
        candidate_id="candidate-1",
        staged_fields={"items": [{"label": "crumb"}]},
        evidence_record_ids=["evidence-1"],
        status=builder.CANDIDATE_STATUS_VALID,
    )
    finalization = workspace.finalize(candidate_ids=["candidate-1"])

    assert finalization.status == builder.BUILDER_STATE_FINALIZED
    assert finalization.payload == {"items": [{"label": "crumb"}]}


def test_cancel_and_abort_are_distinct_terminal_states(captured_events):
    cancelled = _workspace()
    cancelled.mark_cancelled(reason="client disconnected")
    assert cancelled.state == builder.BUILDER_STATE_CANCELLED
    with pytest.raises(builder.ExtractionBuilderError, match="cancelled"):
        cancelled.upsert_candidate(candidate_id="candidate-1", staged_fields={})

    aborted = _workspace()
    aborted.mark_aborted(reason="runner error")
    assert aborted.state == builder.BUILDER_STATE_ABORTED
    with pytest.raises(builder.ExtractionBuilderError, match="aborted"):
        aborted.upsert_candidate(candidate_id="candidate-1", staged_fields={})


def test_context_binding_resets_to_previous_workspace(captured_events):
    outer = _workspace()
    token = builder.set_active_extraction_builder_workspace(outer)
    try:
        inner = builder.ExtractionBuilderWorkspace(run_id="trace-2")
        inner_token = builder.set_active_extraction_builder_workspace(inner)
        try:
            assert builder.get_active_extraction_builder_workspace() is inner
        finally:
            builder.reset_active_extraction_builder_workspace(inner_token)
        assert builder.get_active_extraction_builder_workspace() is outer
    finally:
        builder.reset_active_extraction_builder_workspace(token)

    with pytest.raises(RuntimeError, match="No active extraction builder workspace"):
        builder.get_active_extraction_builder_workspace()
