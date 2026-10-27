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


@pytest.mark.parametrize("missing_evidence", [False, True])
def test_late_proposal_retains_original_and_requires_actual_evidence(planned, missing_evidence):
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
    evidence = [{"evidence_record_id": "methods", "verified_quote": "Drosophila melanogaster",
                 "section": "Materials and Methods", "document_id": item.scope.document_id}]
    if missing_evidence:
        evidence = []
    outcome = extraction_results.retain(item.scope, work_id, claim["lease_token"], result, evidence_records=evidence)
    assert extraction_results.retain(item.scope, work_id, claim["lease_token"], result, evidence_records=evidence) == outcome
    work.finish(item.scope, work_id, claim["lease_token"], outcome=outcome)
    assert store.read_baseline(item.scope, item.aid) == before
    with store.SessionLocal() as db:
        contribution = store._fact(
            db, item.aid, outcome["contributions"][0]["fact_id"])
        body = store._content(db, contribution)
        assert body["selectable"] is not missing_evidence
        if missing_evidence:
            assert body["structural_failure"] == "invalid_evidence_reference"
            assert body["evidence"] == []
        else:
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


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
@pytest.mark.parametrize('planned', ['evidence'], indirect=True)
async def test_followup_workspace_cannot_mutate_original_and_failure_is_sanitized(planned, monkeypatch, failure):
    from agents import Agent
    from src.lib.validation_review import extraction_execution, extractor_phase
    from src.lib.openai_agents.tools.evidence_workspace import (
        create_discard_recorded_evidence_tool, get_active_evidence_records_snapshot,
    )
    from src.lib.agent_studio.diagnostic_tools.tool_definitions import _unwrap_function_tool
    item = planned
    agent = Agent(name='Saved extractor', model='test', instructions='Saved')
    args = request_args(item)
    args['receipt'] = {'contract': extractor_phase.contract(agent, max_turns=17)}
    wid = extraction_scheduling.request(ReviewContext(item.scope, item.ep, 'supervisor'), **args)
    reports = []
    monkeypatch.setattr('src.lib.observability.runtime.report_runtime_exception', lambda error, **kw: reports.append(error) or True)
    async def invoke(actual, **kwargs):
        assert kwargs['packet']['context']['original_evidence'][0]['evidence_record_id'] == 'original-evidence'
        assert get_active_evidence_records_snapshot() == []
        discard = _unwrap_function_tool(create_discard_recorded_evidence_tool(item.scope.document_id, item.scope.owner_id))
        result = await discard(evidence_record_id='original-evidence', reason='Do not mutate old evidence')
        assert result['status'] == 'not_found'
        if failure:
            raise RuntimeError('PRIVATE_PAPER_PROVIDER_MESSAGE secret-token')
        return {'entries': [{'entry_id': args['entry_ids'][0], 'outcome': 'no_new_information',
            'assessment': 'No clarification', 'remaining_uncertainty': 'Still unclear',
            'proposed_values': [], 'evidence_record_ids': []}]}
    monkeypatch.setattr(extractor_phase, 'invoke', invoke)
    before = store.read_baseline(item.scope, item.aid)
    if failure:
        with pytest.raises(RuntimeError, match='PRIVATE_PAPER'):
            await extraction_execution.execute(item.scope, wid, resolve_agent=lambda _: agent)
        assert len(reports) == 1
        assert 'PRIVATE' not in str(reports[0]) and 'secret-token' not in str(reports[0])
        assert reports[0].__cause__ is None and reports[0].__context__ is None
    else:
        result = await extraction_execution.execute(item.scope, wid, resolve_agent=lambda _: agent)
        assert result['state'] == 'completed'
        assert not reports
    assert store.read_baseline(item.scope, item.aid) == before


@pytest.mark.asyncio
async def test_followup_reads_real_paper_tools_and_retains_exact_new_evidence(planned, monkeypatch):
    """Only provider and paper storage are fake; tool selection/verification/history are real."""
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    from agents import Agent
    from agr_ai_curation_alliance.tools import documents, weaviate_search
    from src.lib.openai_agents import document_references as refs
    from src.lib.openai_agents.run_history import HistoryScope, HistoryStore
    from src.lib.openai_agents.tools import record_evidence
    from src.lib.packages.package_runner_entrypoint import _build_tool_context
    from src.lib.validation_review import extraction_execution, extractor_phase
    import json

    item = planned
    doc, owner = item.scope.document_id, item.scope.owner_id
    history = HistoryStore(store.SessionLocal)
    history_scope = HistoryScope(owner, uuid4().hex, uuid4().hex, 'extractor', 'followup/', item.scope.session_id)
    binding = refs.SourceBinding(doc, owner, 'fixture-generation', history_scope, history, [])
    binding.pin_generation()
    @asynccontextmanager
    async def scoped(document, user):
        assert (document, user) == (doc, owner)
        token = refs._ACTIVE_SOURCE.set(binding)
        try:
            yield
        finally:
            refs._ACTIVE_SOURCE.reset(token)
    chunk = {'id': str(uuid4()), 'chunk_index': 42, 'text': 'Methods identify the organism.',
             'document_id': doc, 'page_number': 3, 'parent_section': 'Methods', 'section_title': 'Methods',
             'metadata': {'page_number': 3, 'section_title': 'Methods'}}
    monkeypatch.setattr(refs, 'document_reference_scope', scoped)
    monkeypatch.setattr(refs, 'get_chunk_by_index', AsyncMock(return_value=chunk))
    monkeypatch.setattr(weaviate_search, 'get_chunk_by_index', AsyncMock(return_value=chunk))
    monkeypatch.setattr(weaviate_search, 'get_chunk_neighbor_ids', AsyncMock(return_value={}))
    monkeypatch.setattr(record_evidence, 'get_chunk_by_id', AsyncMock(return_value=chunk))
    monkeypatch.setattr('src.lib.weaviate_client.chunks.get_chunk_by_id', AsyncMock(return_value=chunk))
    monkeypatch.setattr('src.lib.context.get_current_user_id', lambda: owner)
    monkeypatch.setattr('src.lib.prompts.assembly.get_all_active_prompts', lambda: {})
    tools = [documents.create_read_chunk_tool({'document_id': doc, 'user_id': owner}),
             documents.create_record_evidence_tool({'document_id': doc, 'user_id': owner})]
    agent = Agent(name='Saved extractor', model='test', instructions='Extract evidence', tools=tools)
    args = request_args(item)
    args['receipt'] = {'contract': extractor_phase.contract(agent, max_turns=17)}
    wid = extraction_scheduling.request(ReviewContext(item.scope, item.ep, 'supervisor'), **args)
    async def provider(phase, input_value, **kwargs):
        available = {tool.name: tool for tool in phase.tools}
        assert set(available) == {'read_chunk', 'record_evidence', 'read_supplied_history'}
        read = available['read_chunk']
        await read.on_invoke_tool(_build_tool_context(read.name, {}), '{"chunk_index":42}')
        record = available['record_evidence']
        evidence = await record.on_invoke_tool(_build_tool_context(record.name, {}), json.dumps({
            'entity': 'organism', 'selections': [{'chunk_index': 42, 'span_index': 0}]}))
        assert evidence['verified_quote'] == chunk['text']
        return type('Answer', (), {'final_output': {'entries': [{'entry_id': args['entry_ids'][0],
            'outcome': 'additional_information', 'assessment': 'Methods clarify the organism',
            'remaining_uncertainty': '', 'evidence_record_ids': [evidence['evidence_record_id']], 'proposed_values': []}]}})()
    monkeypatch.setattr('src.lib.openai_agents.runner.run_agent_with_owned_openai_resources', provider)
    result = await extraction_execution.execute(item.scope, wid, resolve_agent=lambda _: agent)
    assert result['state'] == 'completed'
    fact = store.read_fact(item.scope, item.aid, result['contributions'][0]['fact_id'])
    saved, = fact['content']['evidence']
    assert saved['origin'] == 'followup'
    assert saved['record']['verified_quote'] == chunk['text']
    assert saved['record']['chunk_id'] == chunk['id']


@pytest.mark.asyncio
async def test_malformed_followup_transport_reports_once_without_sensitive_value(planned, monkeypatch):
    from types import SimpleNamespace
    from agents import Agent
    from src.lib.validation_review import extraction_execution, extractor_phase

    item = planned
    original = store.read_baseline(item.scope, item.aid)
    monkeypatch.setattr("src.lib.prompts.assembly.get_all_active_prompts", lambda: {})
    agent = Agent(name="Saved extractor", model="test", instructions="Saved policy")
    args = request_args(item)
    args["receipt"] = {"contract": extractor_phase.contract(agent, max_turns=17)}
    wid = extraction_scheduling.request(ReviewContext(item.scope, item.ep, "supervisor"), **args)
    reports = []
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception",
                        lambda error, **kw: reports.append(error) or True)

    async def provider(*args_, **kwargs):
        return SimpleNamespace(final_output={"entries": [{"entry_id": args["entry_ids"][0],
            "outcome": "proposed_correction", "assessment": "Clarification", "remaining_uncertainty": "",
            "evidence_record_ids": [], "proposed_values": [{"field_path": "organism",
                "value_json": "PRIVATE_PAPER secret-token", "evidence_record_ids": [],
                "collection_mappings": []}]}]})
    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_with_owned_openai_resources", provider)
    with pytest.raises(ValueError, match="one valid finite JSON value"):
        await extraction_execution.execute(item.scope, wid, resolve_agent=lambda _: agent)
    assert len(reports) == 1
    assert "PRIVATE" not in str(reports[0]) and "secret-token" not in str(reports[0])
    assert reports[0].__cause__ is None and reports[0].__context__ is None
    assert store.read_baseline(item.scope, item.aid) == original
    with store.SessionLocal() as db:
        assert db.get(ValidationReviewWork, wid).state == "uncertain"
        assert db.scalar(select(ValidationFact).where(ValidationFact.artifact_id == item.aid,
            ValidationFact.kind == "extraction_contribution")) is None
    assert (await extraction_execution.execute(item.scope, wid,
        resolve_agent=lambda _: pytest.fail("Must not replay malformed output")))["state"] == "not_claimed"
