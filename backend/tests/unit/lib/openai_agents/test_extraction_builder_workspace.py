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


def test_workspace_generates_uuid_backed_run_id_when_missing(monkeypatch):
    monkeypatch.setattr(
        builder,
        "uuid4",
        lambda: "00000000-0000-4000-8000-000000000001",
    )

    workspace = builder.ExtractionBuilderWorkspace(run_id=" ")

    assert workspace.run_id == "builder-00000000-0000-4000-8000-000000000001"


def test_upsert_is_idempotent_for_retry_and_tracks_state(captured_events):
    workspace = _workspace()

    first = workspace.upsert_candidate(
        candidate_id="candidate-1",
        staged_fields={"items": [{"label": "crumb"}]},
        pending_ref_ids=["pending-1", "pending-1"],
        evidence_record_ids=["evidence-1"],
    )
    second = workspace.upsert_candidate(
        candidate_id="candidate-1",
        staged_fields={"items": [{"label": "crumb"}]},
        pending_ref_ids=["pending-1"],
        evidence_record_ids=["evidence-1"],
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
    )

    finalization = workspace.finalize(candidate_ids=["candidate-1"])
    duplicate = workspace.finalize(candidate_ids=["candidate-1"])

    assert duplicate is finalization
    assert finalization.summary() == {
        "status": "finalized",
        "finalized_candidate_count": 1,
        "validation_errors": [],
        "evidence_record_ids": ["evidence-1"],
        "builder_run_id": "trace-1",
        "builder_invocation_id": workspace.builder_invocation_id,
        "candidate_ids": ["candidate-1"],
        "source_candidate_ids": [],
    }
    with pytest.raises(builder.ExtractionBuilderFinalizedError):
        workspace.upsert_candidate(
            candidate_id="candidate-2",
            staged_fields={"items": []},
        )
    workspace.record_validation_failure(
        errors=[{"message": "late failure", "reason": "late"}],
        candidate_ids=["candidate-1"],
    )
    assert workspace.state == builder.BUILDER_STATE_VALIDATION_FAILED
    assert workspace.get_candidate("candidate-1").status == builder.CANDIDATE_STATUS_FINALIZED
    assert workspace.validation_errors == [{"message": "late failure", "reason": "late"}]


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


def test_source_candidate_ids_define_duplicate_finalize_identity(captured_events):
    workspace = _workspace()
    workspace.upsert_candidate(
        candidate_id="materialized-envelope-1",
        staged_fields={"items": [{"id": 1}, {"id": 2}]},
    )

    finalization = workspace.finalize(
        candidate_ids=["materialized-envelope-1"],
        source_candidate_ids=["candidate-1", "candidate-2"],
    )
    duplicate = workspace.finalize(
        candidate_ids=["materialized-envelope-1"],
        source_candidate_ids=["candidate-2", "candidate-1"],
    )

    assert duplicate is finalization
    assert finalization.candidate_ids == ("materialized-envelope-1",)
    assert finalization.source_candidate_ids == ("candidate-1", "candidate-2")
    assert finalization.summary()["source_candidate_ids"] == ["candidate-1", "candidate-2"]

    with pytest.raises(builder.ExtractionBuilderFinalizationConflict, match="different candidate membership"):
        workspace.finalize(
            candidate_ids=["materialized-envelope-1"],
            source_candidate_ids=["candidate-1", "candidate-3"],
        )


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


def test_scope_metadata_model_dump_failure_surfaces_loudly(captured_events):
    class BrokenPayload:
        def model_dump(self, *, mode):
            raise RuntimeError(f"cannot serialize in {mode} mode")

    with pytest.raises(RuntimeError, match="cannot serialize in json mode"):
        builder._scope_value_from_payload({"nested": BrokenPayload()}, "document_id")


def test_revisions_cover_patch_validation_discard_and_finalization(captured_events):
    workspace = _workspace()
    first = workspace.upsert_candidate(candidate_id="a", staged_fields={"items": [{"label": "A"}]})
    second = workspace.upsert_candidate(candidate_id="b", staged_fields={"items": [{"label": "B"}]})
    assert (first.revision, second.revision, workspace.revision) == (1, 1, 2)
    workspace.record_validation_failure(errors=[{"reason": "needs evidence"}], candidate_ids=["a"])
    assert (first.revision, second.revision, workspace.revision) == (2, 1, 3)
    workspace.upsert_candidate(candidate_id="a", staged_fields={"items": [{"label": "A revised"}]})
    workspace.discard_candidate("b", reason="duplicate")
    workspace.finalize(candidate_ids=["a"])
    assert (first.revision, second.revision, workspace.revision) == (4, 2, 6)
    snapshot = workspace.snapshot(redact_payload=False)
    assert snapshot["workspace_revision"] == 6
    assert snapshot["candidates"][1]["status"] == "discarded"


def test_concurrent_staging_retains_distinct_references_and_evidence(captured_events):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    workspace = _workspace()
    start = Barrier(8, timeout=10)

    def stage(index):
        start.wait()
        return workspace.stage_candidate(
            id_prefix="candidate",
            pending_ref_id=f"pending-{index}",
            staged_fields={"value": index},
            evidence_record_ids=[f"evidence-{index}"],
            status=builder.CANDIDATE_STATUS_VALID,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        candidates = list(pool.map(stage, range(8)))

    assert len(workspace.candidates) == 8
    assert len({candidate.candidate_id for candidate in candidates}) == 8
    assert workspace.revision == 8
    for index, candidate in enumerate(candidates):
        assert candidate.pending_ref_ids == [f"pending-{index}"]
        assert candidate.evidence_record_ids == [f"evidence-{index}"]
        assert candidate.staged_fields == {"value": index}
    assert len(captured_events) == 8


def test_staging_same_reference_updates_and_revives_discarded_draft(captured_events):
    workspace = _workspace()
    first = workspace.stage_candidate(
        id_prefix="candidate", pending_ref_id="pending-1",
        staged_fields={"value": "first"}, evidence_record_ids=["evidence-1"],
    )
    workspace.discard_candidate(first.candidate_id, reason="draft correction")
    updated = workspace.stage_candidate(
        id_prefix="candidate", pending_ref_id="pending-1",
        staged_fields={"value": "second"}, evidence_record_ids=["evidence-2"],
        status=builder.CANDIDATE_STATUS_VALID,
    )
    assert updated is first
    assert len(workspace.candidates) == 1
    assert updated.revision == 3
    assert updated.status == builder.CANDIDATE_STATUS_VALID
    assert updated.staged_fields == {"value": "second"}
    assert updated.evidence_record_ids == ["evidence-2"]


def test_staging_without_reference_creates_distinct_candidates(captured_events):
    workspace = _workspace()
    candidates = [workspace.stage_candidate(
        id_prefix="generic-candidate", pending_ref_id=None,
        staged_fields={"value": index},
    ) for index in range(2)]
    assert [candidate.candidate_id for candidate in candidates] == [
        "generic-candidate-1", "generic-candidate-2",
    ]
    assert all(candidate.pending_ref_ids == [] for candidate in candidates)


def test_staging_does_not_overwrite_an_occupied_numbered_id(captured_events):
    workspace = _workspace()
    original = workspace.upsert_candidate(
        candidate_id="candidate-2", staged_fields={"value": "original"},
        pending_ref_ids=["existing"],
    )
    added = workspace.stage_candidate(
        id_prefix="candidate", pending_ref_id="new", staged_fields={"value": "new"},
    )
    assert added.candidate_id == "candidate-3"
    assert original.staged_fields == {"value": "original"}
    assert len(workspace.candidates) == 2


def test_finalized_workspace_rejects_staging_without_mutation(captured_events):
    workspace = _workspace()
    candidate = workspace.stage_candidate(
        id_prefix="candidate", pending_ref_id="pending-1", staged_fields={"value": 1},
    )
    workspace.finalize(candidate_ids=[candidate.candidate_id])
    before = workspace.snapshot()
    with pytest.raises(builder.ExtractionBuilderFinalizedError):
        workspace.stage_candidate(
            id_prefix="candidate", pending_ref_id="pending-2", staged_fields={"value": 2},
        )
    assert workspace.snapshot() == before
