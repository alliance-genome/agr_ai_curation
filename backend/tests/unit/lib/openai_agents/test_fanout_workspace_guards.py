"""Workspace contracts that fan-out extraction relies on (planning guard, writer ownership)."""

from __future__ import annotations

import asyncio
import threading

import pytest

from src.lib.openai_agents import extraction_builder_workspace as builder


@pytest.fixture(autouse=True)
def _quiet_trace_events(monkeypatch):
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **event: event)


def _workspace() -> builder.ExtractionBuilderWorkspace:
    return builder.ExtractionBuilderWorkspace(run_id="trace-fanout", agent_id="agent-1")


def _stage(workspace, label, *, pending_ref_id=None):
    return workspace.stage_candidate(
        id_prefix="obj",
        pending_ref_id=pending_ref_id,
        staged_fields={"label": label},
        evidence_record_ids=[f"evidence-{label}"],
    )


def test_staging_is_refused_while_planning_and_allowed_after():
    workspace = _workspace()
    workspace.planning = True

    with pytest.raises(builder.ExtractionBuilderPlanningError, match="planning"):
        _stage(workspace, "a")
    assert workspace.candidates == {}
    assert workspace.revision == 0

    workspace.planning = False
    candidate = _stage(workspace, "a")
    assert candidate.candidate_id == "obj-1"


def test_planning_guard_also_refuses_patch_and_discard():
    workspace = _workspace()
    _stage(workspace, "a")
    workspace.planning = True

    with pytest.raises(builder.ExtractionBuilderPlanningError):
        workspace.upsert_candidate(candidate_id="obj-1", staged_fields={"label": "b"})
    with pytest.raises(builder.ExtractionBuilderPlanningError):
        workspace.discard_candidate("obj-1")
    assert workspace.get_candidate("obj-1").staged_fields == {"label": "a"}


def test_staged_candidates_are_attributed_to_the_staging_owner():
    workspace = _workspace()

    def stage_as(owner, label):
        token = builder.set_staging_owner(owner)
        try:
            return _stage(workspace, label).candidate_id
        finally:
            builder.reset_staging_owner(token)

    first = stage_as("writer-1", "a")
    second = stage_as("writer-2", "b")
    third = stage_as("writer-1", "c")
    unowned = _stage(workspace, "d").candidate_id

    assert workspace.candidates_staged_by("writer-1") == [first, third]
    assert workspace.candidates_staged_by("writer-2") == [second]
    assert workspace.candidates_staged_by("writer-3") == []
    assert unowned not in workspace.candidates_staged_by("writer-1")
    # Ownership is runtime bookkeeping, not part of the persisted candidate snapshot.
    assert "owner" not in workspace.snapshot()["candidates"][0]


def test_writer_cannot_overwrite_another_writers_record_through_pending_ref_id():
    workspace = _workspace()
    token = builder.set_staging_owner("writer-1")
    try:
        original = _stage(workspace, "writer one record", pending_ref_id="item-1")
    finally:
        builder.reset_staging_owner(token)

    token = builder.set_staging_owner("writer-2")
    try:
        with pytest.raises(builder.ExtractionBuilderOwnershipError, match="item-1"):
            _stage(workspace, "writer two record", pending_ref_id="item-1")
    finally:
        builder.reset_staging_owner(token)

    assert workspace.get_candidate(original.candidate_id).staged_fields == {"label": "writer one record"}
    assert workspace.candidates_staged_by("writer-2") == []


def test_same_writer_and_unowned_stagers_may_reuse_pending_ref_id():
    workspace = _workspace()
    token = builder.set_staging_owner("writer-1")
    try:
        first = _stage(workspace, "first draft", pending_ref_id="item-1")
        again = _stage(workspace, "second draft", pending_ref_id="item-1")
    finally:
        builder.reset_staging_owner(token)
    assert again.candidate_id == first.candidate_id

    # The planner thread (no owner) keeps today's upsert-by-pending-ref semantics.
    tidy = _stage(workspace, "tidy fix", pending_ref_id="item-1")
    assert tidy.candidate_id == first.candidate_id
    assert workspace.get_candidate(first.candidate_id).staged_fields == {"label": "tidy fix"}


def test_concurrent_writer_threads_get_unique_ids_and_exact_attribution():
    workspace = _workspace()
    writers, per_writer = 8, 25
    barrier = threading.Barrier(writers)
    errors = []

    def run(owner):
        token = builder.set_staging_owner(owner)
        try:
            barrier.wait()
            for index in range(per_writer):
                _stage(workspace, f"{owner}-{index}")
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)
        finally:
            builder.reset_staging_owner(token)

    threads = [threading.Thread(target=run, args=(f"writer-{n}",)) for n in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(workspace.candidates) == writers * per_writer
    for n in range(writers):
        owned = workspace.candidates_staged_by(f"writer-{n}")
        assert len(owned) == per_writer
        assert {workspace.get_candidate(cid).staged_fields["label"] for cid in owned} == {
            f"writer-{n}-{index}" for index in range(per_writer)
        }


@pytest.mark.asyncio
async def test_staging_owner_follows_asyncio_tasks_into_tool_threads():
    """SDK sync tools run through asyncio.to_thread, which copies the task context."""
    workspace = _workspace()

    async def writer(owner):
        token = builder.set_staging_owner(owner)
        try:
            await asyncio.gather(*(
                asyncio.to_thread(_stage, workspace, f"{owner}-{index}") for index in range(5)
            ))
        finally:
            builder.reset_staging_owner(token)

    await asyncio.gather(*(asyncio.create_task(writer(f"writer-{n}")) for n in range(3)))

    for n in range(3):
        assert len(workspace.candidates_staged_by(f"writer-{n}")) == 5
