"""Real durable follow-up entitlement, independent of provider success."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from typing import Any

import pytest
from sqlalchemy import select

from src.lib.validation_history import store, runtime
from src.lib.validation_review import admission, extraction_scheduling, work
from src.lib.validation_review import extraction_results
from src.lib.validation_review.decisions import ReviewContext
from src.models.sql.validation_history import ValidationFact
from src.models.sql.validation_review import ValidationReviewWork
from tests.integration.persistence.test_chat_review_adapter import planned as planned, scope as scope, migrated as migrated


def request_args(item) -> dict[str, Any]:
    entry = item.plan.envelope.extracted_objects[0].metadata["validation_history"]["target_id"]
    binding = runtime.binding_target(item.plan.history_binding, item.plan.matches[0])
    op = uuid4().hex
    with store.SessionLocal.begin() as db:
        parent = store._append(db, item.aid, operation_id="decision:" + op,
            kind="decision", body={"actor": {"type": "agent", "id": "supervisor"},
                "entries": [{"target_id": binding, "action": "defer", "reason": "Find organism context"}]})
        store._append(db, item.aid, operation_id="episode-disposition:" + op,
            kind="episode_disposition", parent_id=item.ep, body={"selection_ids": []})
        parent_id = parent.id
    return dict(operation_id=uuid4().hex, parent_decision_id=parent_id,
        entry_ids=[entry], instructions="Check Methods for the organism.",
        receipt={"saved_extractor_contract": "test-contract"},
        current_entries={entry: item.plan.envelope.extracted_objects[0].model_dump(mode="json")})


def test_concurrent_same_operation_has_one_request_and_no_attempt_refill(planned):
    item = planned
    args = request_args(item)
    context = ReviewContext(item.scope, item.ep, "supervisor")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: extraction_scheduling.request(context, **args), range(2)))
    assert results[0] == results[1]
    with store.SessionLocal() as db:
        row = db.get(ValidationReviewWork, results[0])
        assert row.kind == "extraction_followup"
        assert row.payload["token_pool"] == "reviewer_tokens"
        context_input = row.payload["specialist_input"]["context"]
        assert context_input["attempt"][args["entry_ids"][0]] == {
            "ordinal": 2, "ceiling": 2, "remaining_after": 0}
        assert len(list(db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == item.aid,
            ValidationFact.kind == "extraction_followup_request")))) == 1
    with pytest.raises(admission.ReviewStopped, match="extraction_attempts_exhausted"):
        extraction_scheduling.request(context, **{**args, "operation_id": uuid4().hex})
    with pytest.raises(store.HistoryConflict, match="identity conflict"):
        extraction_scheduling.request(context, **{**args, "instructions": "Different investigation"})


def test_uncertain_followup_never_replays_or_refills(planned):
    item = planned
    args = request_args(item)
    context = ReviewContext(item.scope, item.ep, "supervisor")
    work_id = extraction_scheduling.request(context, **args)
    claim = work.claim(item.scope, work_id)
    assert claim is not None
    work.mark_submitted(item.scope, work_id, claim["lease_token"])
    work.mark_uncertain(item.scope, work_id, claim["lease_token"], reason="worker_lost")
    assert work.recover(item.scope, work_id) == "uncertain"
    assert work.claim(item.scope, work_id) is None
    assert extraction_scheduling.request(context, **args) == work_id
    with pytest.raises(admission.ReviewStopped, match="extraction_attempts_exhausted"):
        extraction_scheduling.request(context, **{**args, "operation_id": uuid4().hex})
    with store.SessionLocal() as db:
        uncertain = db.scalar(select(ValidationFact).where(
            ValidationFact.artifact_id == item.aid,
            ValidationFact.kind == "extraction_followup_uncertainty"))
        assert store._content(db, uncertain)["reason"] == "worker_lost"


def test_late_proposal_retains_original_and_requires_actual_evidence(planned):
    item = planned
    args = request_args(item)
    before = store.read_baseline(item.scope, item.aid)
    context = ReviewContext(item.scope, item.ep, "supervisor")
    work_id = extraction_scheduling.request(context, **args)
    claim = work.claim(item.scope, work_id)
    assert claim is not None
    work.mark_submitted(item.scope, work_id, claim["lease_token"])
    work.mark_uncertain(item.scope, work_id, claim["lease_token"], reason="disconnect")
    result = {"entries": [{"entry_id": args["entry_ids"][0],
        "outcome": "additional_information", "assessment": "Methods identifies the organism.",
        "remaining_uncertainty": "", "evidence_record_ids": ["methods"],
        "proposed_values": [{"field_path": "organism", "value": "Drosophila melanogaster",
                             "evidence_record_ids": ["methods"]}]}]}
    with pytest.raises(store.HistoryConflict, match="evidence is missing"):
        extraction_results.retain(item.scope, work_id, claim["lease_token"], result, evidence_records=[])
    evidence = [{"evidence_record_id": "methods", "verified_quote": "Drosophila melanogaster",
                 "section": "Materials and Methods", "document_id": item.scope.document_id}]
    outcome = extraction_results.retain(item.scope, work_id, claim["lease_token"], result, evidence_records=evidence)
    assert extraction_results.retain(item.scope, work_id, claim["lease_token"], result, evidence_records=evidence) == outcome
    work.finish(item.scope, work_id, claim["lease_token"], outcome=outcome)
    assert store.read_baseline(item.scope, item.aid) == before
    with store.SessionLocal() as db:
        contribution = store._fact(
            db, item.aid, outcome["contributions"][0]["fact_id"])
        body = store._content(db, contribution)
        assert body["evidence"][0]["origin"] == "followup"
        assert body["proposal"]["proposed_values"][0]["value"] == "Drosophila melanogaster"
        assert store._current(db, item.aid, args["entry_ids"][0])["selected"] is None
    result["entries"][0]["assessment"] = "A different answer"
    with pytest.raises(store.HistoryConflict):
        extraction_results.retain(item.scope, work_id, claim["lease_token"], result, evidence_records=evidence)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_executor_uses_shared_budget_without_reviewer_tool_charge_and_never_replays(planned, monkeypatch, cancel):
    import asyncio
    from agents import Agent
    from src.lib.validation_review import extraction_execution, extractor_phase
    from src.lib.validation_review.provider import ACTIVE_BUDGET

    item = planned
    agent = Agent(name="Saved extractor", model="gpt-test", instructions="Saved policy")
    args = request_args(item)
    args["receipt"] = {"contract": extractor_phase.contract(agent, max_turns=17)}
    work_id = extraction_scheduling.request(ReviewContext(item.scope, item.ep, "supervisor"), **args)
    calls = []

    async def invoke(actual, **kwargs):
        budget = ACTIVE_BUDGET.get()
        assert budget is not None
        assert budget.token_pool == "reviewer_tokens"
        assert budget.reviewer is False
        assert budget.work_id == work_id
        assert kwargs["max_turns"] == 17
        calls.append(actual)
        if cancel:
            budget.execution.provider_admitted = True
            raise asyncio.CancelledError()
        return {"entries": [{"entry_id": args["entry_ids"][0], "outcome": "no_new_information",
            "assessment": "No clarifying information found.", "remaining_uncertainty": "Organism unclear",
            "proposed_values": [], "evidence_record_ids": []}]}

    monkeypatch.setattr(extractor_phase, "invoke", invoke)
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            await extraction_execution.execute(item.scope, work_id, resolve_agent=lambda _: agent)
    else:
        result = await extraction_execution.execute(item.scope, work_id, resolve_agent=lambda _: agent)
        assert result["state"] == "completed"
        assert len(result["contributions"]) == 1
    replay = await extraction_execution.execute(item.scope, work_id, resolve_agent=lambda _: pytest.fail("Replayed extractor"))
    assert replay["state"] == "not_claimed"
    assert len(calls) == 1
    assert ACTIVE_BUDGET.get() is None
    with store.SessionLocal() as db:
        assert db.get(ValidationReviewWork, work_id).state == ("uncertain" if cancel else "completed")
