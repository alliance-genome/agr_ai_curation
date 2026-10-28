"""Only actual, novel scientific reads earn progress; corrected refusals clear."""

import asyncio

import pytest

from src.lib.validation_review.observations import (
    Observations, observation_scope, record_read, record_tool,
)


def page(sha="source", offset=0, content="evidence"):
    return {"fact_id": "new-snapshot-id", "sha256": sha, "offset": offset, "content": content}


def test_status_churn_and_repeated_reads_do_not_earn_progress():
    seen = Observations()
    with observation_scope(seen):
        for kind in ("review_packet", "review_status", "decision", "selection"):
            record_read(kind, page(sha=kind))
        assert not seen.snapshot()[0]
        record_read("review_record_source", page())
        record_read("review_record_source", {**page(), "fact_id": "another-id"})
        assert len(seen.snapshot()[0]) == 1
        record_read("review_record_source", page(offset=8))
        record_read("completion", page(sha="new-answer"))
        record_read("completion", page(sha="empty", content=""))
        assert len(seen.snapshot()[0]) == 3


def test_refusals_are_typed_and_clear_only_for_corrected_subject():
    seen = Observations()
    failure = {"state": "not_applied", "code": "configured_extractor_changed",
               "message": "sensitive evidence not retained", "resource": None}
    with observation_scope(seen):
        record_tool("request", {"target_ids": ["a", "b"], "instructions": "secret"}, failure)
        record_tool("request", {"target_ids": ["a"]}, {"state": "completed"})
        assert seen.snapshot()[1] == [{"tool": "request", "target_id": "b",
                                      "code": "configured_extractor_changed", "resource": None}]
        record_tool("request", {"target_ids": ["b"], "operation_id": "new-op"}, {"state": "queued"})
        assert not seen.snapshot()[1]


@pytest.mark.asyncio
async def test_observation_scope_follows_to_thread_and_isolates_invocations():
    seen = Observations()
    with observation_scope(seen):
        await asyncio.to_thread(record_read, "baseline", page())
        nested = Observations()
        with observation_scope(nested):
            await asyncio.to_thread(record_read, "completion", page(sha="answer"))
        record_read("attempt", page(sha="request"))
    record_read("baseline", page(sha="outside"))
    assert len(seen.snapshot()[0]) == 2
    assert len(nested.snapshot()[0]) == 1


def test_recurring_states_do_not_earn_continuation_but_new_evidence_does():
    seen = Observations()
    seen.progress("initial")  # Seed before the first SDK phase.
    assert not seen.progress("initial")
    assert seen.progress("accepted")
    assert not seen.progress("initial")  # revoke/accept cycle
    assert not seen.progress("accepted")
    seen.record_read("completion", page(sha="another-answer"))
    assert seen.progress("accepted")  # Same pending count, new actual answer.
    assert not seen.progress("accepted")
    seen.record_read("review_record_source", page(offset=0))
    assert seen.progress("accepted")
    seen.record_read("review_record_source", page(offset=8))
    assert seen.progress("accepted")


@pytest.mark.parametrize("arguments", [
    {"entry_ids": ["entry-a"]},
    {"contribution": {"fact_id": "contribution-a", "sha256": "digest"}},
])
def test_followup_refusals_keep_exact_subject_identity(arguments):
    seen = Observations()
    seen.record_tool("followup", arguments, {"state": "conflict", "code": "HistoryConflict"})
    assert seen.snapshot()[1][0]["target_id"] in {"entry-a", "contribution-a"}
    seen.record_tool("followup", {"entry_ids": ["unrelated"]}, {"state": "completed"})
    assert seen.snapshot()[1]
    seen.record_tool("followup", arguments, {"state": "completed"})
    assert not seen.snapshot()[1]
