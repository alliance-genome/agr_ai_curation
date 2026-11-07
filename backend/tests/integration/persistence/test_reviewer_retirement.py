"""The review actor retires extraction-only records it judged outside the question (ALL-1502).

Code enforces authority, record scope, structure and history; the scope judgment
itself is the reviewer's and is saved with its reason, never recomputed here.
"""
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from agents.tool_context import ToolContext
from sqlalchemy import select

from src.lib.domain_envelopes.persistence import DomainEnvelopeCheckpointRequest, load_domain_envelope
from src.lib.domain_packs.validator_dispatch import ValidatorRuntimeContext, prepare_validator_dispatch
from src.lib.validation_history import runtime, selections, store
from src.lib.validation_history.record_lifecycle import retired_records
from src.lib.validation_review import admission, decisions, extraction_scheduling, reads, work
from src.lib.validation_review.decisions import ReviewContext
from src.lib.validation_review.tools import create_tools
from src.lib.validation_review.validator_adapter import ValidatorAdapter, save_plan
from src.models.sql.validation_history import ValidationFact
from src.models.sql.validation_review import ValidationReviewWork
from src.schemas.domain_envelope import DomainEnvelope
from tests.integration.persistence.test_chat_review_adapter import (
    migrated as migrated, planned as planned, scope as scope,
)
from tests.integration.persistence.test_review_grouped_sources import (
    QUOTE, _evidence, _object, cancel_reader as cancel_reader,
)


def _generic(scope, count=3):
    """Extraction-only records of the generic pack, planned the way chat review plans them."""
    from src.lib.curation_workspace.adapter_registry import resolve_curation_domain_pack_by_id
    from src.lib.curation_workspace.models import DomainEnvelopeModel

    pack = resolve_curation_domain_pack_by_id("generic")
    objects = [_object(index, [f"e{index}"]) for index in range(count)]
    envelope = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id="generic", extracted_objects=objects,
        metadata={"evidence_records": [_evidence(f"e{index}", f"{QUOTE} {index}") for index in range(count)]})
    envelope = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex, envelope=envelope,
                                          typed=envelope.model_dump(mode="json"), provenance={})
    runtime_context = ValidatorRuntimeContext(user_id=scope.owner_id, document_id=scope.document_id,
                                              authenticated_groups=())
    plan = prepare_validator_dispatch(envelope, pack, runtime_context=runtime_context)
    plan_ref = save_plan(plan, context=runtime_context)
    aid = plan.history_binding.artifact_id
    ep = plan.envelope.metadata["review_episode_id"]
    sid = admission.create_scope(scope, operation_id=uuid4().hex, kind="document_run", targets=count, episodes=1)
    admission.attach_episode(scope, artifact_id=aid, episode_id=ep, scope_id=sid)
    request = DomainEnvelopeCheckpointRequest(project_key="fixture", envelope=plan.envelope, expected_revision=0,
        document_id=scope.document_id, adapter_key="fixture", source_extraction_result_id=uuid4())

    def checkpoint():
        with store.SessionLocal() as db:
            row = db.get(DomainEnvelopeModel, plan.envelope.envelope_id)
            return request if row is None else replace(
                request, envelope=load_domain_envelope(db, plan.envelope.envelope_id), expected_revision=row.revision)

    adapter = ValidatorAdapter(scope, aid, ep, plan_ref, authenticated_groups=())
    targets = [obj.metadata["validation_history"]["target_id"] for obj in plan.envelope.extracted_objects]
    return SimpleNamespace(scope=scope, aid=aid, ep=ep, adapter=adapter, checkpoint=checkpoint,
                           targets=targets, context=ReviewContext(scope, ep, "supervisor.validation_review"))


@pytest.fixture
def generic(scope):
    return _generic(scope)


def _record_handles(packet):
    return {record["target_id"]: record["review"][0]["handle"] for record in packet["records"]}


def _save_tool(item):
    """The real bound tool, with retirement routed through the real adapter."""
    tools = create_tools(item.context, checkpoint_provider=item.checkpoint,
        request_extraction=AsyncMock(), assess_source=AsyncMock(),
        select_source=lambda **arguments: item.adapter.select_source(item.context, item.checkpoint, **arguments))
    return next(tool for tool in tools if tool.name == "record_validation_review")


async def _save(item, packet_id, packet_decisions, operation_id="save"):
    tool = _save_tool(item)
    encoded = json.dumps({"operation_id": operation_id, "packet_id": packet_id,
                          "packet_decisions": packet_decisions, "entries": None})
    return await tool.on_invoke_tool(ToolContext(context=None, tool_name=tool.name, tool_call_id=operation_id,
                                                 tool_arguments=encoded), encoded)


def _decision(handle, action, reason=None, **extra):
    return {"handle": handle, "action": action, "reason": reason or f"{handle}: what this record shows.",
            "absence_assessment": None, "unresolved_fields": None, **extra}


def test_reject_leaves_the_record_pending_and_the_save_names_what_clears_it(generic):
    item = generic
    packet = reads.review_packet(item.scope, item.ep)
    handles = _record_handles(packet)
    keep, out, other = (handles[target] for target in item.targets)
    result = decisions.record_packet_decisions(item.context, checkpoint_request=item.checkpoint(),
        operation_id="reject-one", packet_id=packet["packet_id"],
        packet_decisions=[_decision(keep, "accept"), _decision(out, "reject", "A control, not a requested record."),
                          _decision(other, "accept")])
    assert result["ready"] is False, "reject records an opinion; the record is still there"
    assert result["blocking"] == [{"code": "unreviewed", "handles": [out],
                                   "clears_with": "accept, or retire if the record does not answer the question"}]
    with store.SessionLocal() as db:
        state = store._current(db, item.aid, item.targets[1])
        assert (state["active"], state["review"], state["selected"]) == (True, "reject", None)


@pytest.mark.asyncio
async def test_reviewer_retires_out_of_scope_records_in_the_same_save_and_review_becomes_ready(generic):
    item = generic
    packet = reads.review_packet(item.scope, item.ep)
    handles = _record_handles(packet)
    keep, control, mutant = (handles[target] for target in item.targets)
    evidence = packet["records"][1]["evidence"]
    sent = [_decision(keep, "accept"),
            _decision(control, "retire", "A genetic control; the question asks for transgenic flies.", evidence=evidence),
            _decision(mutant, "retire", "A mutant allele, not a transgenic stock.")]
    result = await _save(item, packet["packet_id"], sent, operation_id="retire-two")
    assert result["ready"] is True, result
    assert "blocking" not in result
    assert [(applied["handle"], applied["action"]) for applied in result["applied"]] == [
        (keep, "accept"), (control, "retire"), (mutant, "retire")]
    assert result["decision_id"]
    assert result["source_state_sha256"] != packet["source_state_sha256"]
    current = item.checkpoint().envelope
    assert [obj.metadata["validation_history"]["target_id"] for obj in current.extracted_objects] == [item.targets[0]]
    assert current.metadata["record_counts"] == {"active": 1, "retired": 2, "pending": 0}
    with store.SessionLocal() as db:
        manifest = selections.manifest_in_transaction(db, item.scope, item.aid, episode_id=item.ep)
        assert manifest["ready"] and [unit["target_id"] for unit in manifest["units"]] == [item.targets[0]]
        retired = {record["target_id"]: record for record in retired_records(db, item.aid)}
        assert set(retired) == set(item.targets[1:])
        assert retired[item.targets[1]]["reason"] == sent[1]["reason"]
        assert retired[item.targets[1]]["proposal"] is None, "no extractor recommendation was involved"
        fact = store._fact(db, item.aid, retired[item.targets[1]]["retirement"]["fact_id"],
                           kind="record_retirement_decision")
        body = store._content(db, fact)
        assert body["identity"]["actor"] == {"type": "agent", "id": "supervisor.validation_review"}
        assert body["identity"]["decision"] == "retire" and body["identity"]["reason"] == sent[1]["reason"]
        assert body["origin"] == "review" and fact.parent_id == item.ep
        assert set(body["evidence"]) == set(evidence) and QUOTE in json.dumps(body["evidence"])
        assert store._content(db, store._fact(db, item.aid, retired[item.targets[2]]["retirement"]["fact_id"]))[
            "evidence"] == {}
    replay = await _save(item, packet["packet_id"], sent, operation_id="retire-two")
    assert replay == result, "an exact retry returns the saved outcome without writing again"
    changed = await _save(item, packet["packet_id"], [*sent[:2], _decision(mutant, "accept")], operation_id="retire-two")
    assert changed["state"] == "conflict" and "identity conflict" in changed["message"]


@pytest.mark.asyncio
async def test_curator_restores_a_record_the_reviewer_retired(generic):
    from src.lib.validation_history.record_lifecycle import counts

    item = generic
    packet = reads.review_packet(item.scope, item.ep)
    handles = _record_handles(packet)
    await _save(item, packet["packet_id"], [_decision(handles[item.targets[0]], "accept"),
        _decision(handles[item.targets[1]], "retire", "Not a transgenic fly."),
        _decision(handles[item.targets[2]], "accept")], operation_id="retire-for-restore")
    with store.SessionLocal() as db:
        version = store._current(db, item.aid, item.targets[1])["version"]
    curator = ReviewContext(item.scope, item.ep, item.scope.owner_id, actor_type="curator")
    restored = await item.adapter.select_source(curator, item.checkpoint, operation_id="curator-restore",
        restore_target_id=item.targets[1], expected_version=version, reason="The curator wants it reviewed.")
    assert restored["publication"]["ready"] is False, "a restored record needs review again"
    with store.SessionLocal() as db:
        state = store._current(db, item.aid, item.targets[1])
        assert (state["active"], state["review"]) == (True, "pending")
        assert retired_records(db, item.aid) == []
        assert counts(db, item.aid, [{"target_id": item.targets[1]}]) == {"active": 3, "retired": 0, "pending": 1}
    fresh = reads.review_packet(item.scope, item.ep)
    assert [record["target_id"] for record in fresh["records"]] == [item.targets[1]]


@pytest.mark.asyncio
async def test_a_conflict_in_a_mixed_save_applies_nothing(generic):
    item = generic
    packet = reads.review_packet(item.scope, item.ep)
    handles = _record_handles(packet)
    store.apply_decisions(item.scope, item.aid, operation_id="curator-keeps", actor={"type": "curator",
        "id": item.scope.owner_id}, entries=[{"target_id": item.targets[2], "expected_version": 0,
                                              "action": "defer", "reason": "The curator is checking this one."}])
    with store.SessionLocal() as db:
        digest = selections.source_state_digest(db, item.aid)
    result = await _save(item, packet["packet_id"], [_decision(handles[item.targets[0]], "accept"),
        _decision(handles[item.targets[1]], "retire", "Outside the question."),
        _decision(handles[item.targets[2]], "retire", "Outside the question.")], operation_id="blocked")
    assert result["state"] == "conflict" and "Curator authority" in result["message"]
    assert handles[item.targets[2]] in result["message"] and item.targets[2] not in result["message"]
    with store.SessionLocal() as db:
        assert selections.source_state_digest(db, item.aid) == digest
        assert all(store._current(db, item.aid, target)["active"] for target in item.targets)
        assert store._current(db, item.aid, item.targets[0])["version"] == 0, "the accept did not apply either"


@pytest.mark.asyncio
async def test_a_stale_packet_cannot_retire(generic):
    item = generic
    packet = reads.review_packet(item.scope, item.ep)
    handles = _record_handles(packet)
    await _save(item, packet["packet_id"], [_decision(handles[item.targets[0]], "retire", "Outside the question.")],
                operation_id="first")
    result = await _save(item, packet["packet_id"], [_decision(handles[item.targets[1]], "retire", "Also outside.")],
                         operation_id="second")
    assert result["state"] == "conflict" and "read a fresh packet" in result["message"]
    with store.SessionLocal() as db:
        assert store._current(db, item.aid, item.targets[1])["active"]


@pytest.mark.asyncio
async def test_retire_names_only_record_items_and_existing_evidence(generic):
    item = generic
    packet = reads.review_packet(item.scope, item.ep)
    handle = _record_handles(packet)[item.targets[0]]
    unknown = await _save(item, packet["packet_id"], [_decision(handle, "retire", "Outside.", evidence=["E99"])],
                          operation_id="bad-evidence")
    assert unknown["state"] == "conflict" and "E99" in unknown["message"]
    misplaced = await _save(item, packet["packet_id"], [_decision(handle, "accept", evidence=["E1"])],
                            operation_id="evidence-on-accept")
    assert misplaced["state"] == "conflict" and "Only retire" in misplaced["message"]
    with store.SessionLocal() as db:
        assert store._current(db, item.aid, item.targets[0])["version"] == 0


@pytest.mark.asyncio
async def test_retire_without_the_bound_adapter_is_refused_plainly(generic):
    item = generic
    packet = reads.review_packet(item.scope, item.ep)
    tool = next(tool for tool in create_tools(item.context, checkpoint_provider=item.checkpoint)
                if tool.name == "record_validation_review")
    encoded = json.dumps({"operation_id": "no-adapter", "packet_id": packet["packet_id"], "entries": None,
        "packet_decisions": [_decision(_record_handles(packet)[item.targets[0]], "retire", "Outside.")]})
    result = await tool.on_invoke_tool(ToolContext(context=None, tool_name=tool.name, tool_call_id="no-adapter",
                                                   tool_arguments=encoded), encoded)
    assert result["state"] == "conflict" and "not available" in result["message"]


def test_a_record_with_validator_answers_keeps_the_extractor_recommendation_path(planned):
    from src.lib.validation_review.record_retirement import retire_in_transaction

    item = planned
    root = item.plan.envelope.extracted_objects[0].metadata["validation_history"]["target_id"]
    context = ReviewContext(item.scope, item.ep, "supervisor")
    with store.SessionLocal.begin() as db:
        digest = selections.source_state_digest(db, item.aid)
        with pytest.raises(store.HistoryConflict, match="validator"):
            retire_in_transaction(db, context, operation_id="validator-record", expected_source_sha=digest,
                records=[{"target_id": root, "expected_version": 0, "reason": "Outside.", "evidence": {}}],
                source_envelope=item.plan.envelope, rebuild_inputs=lambda db, envelope: {},
                publish=lambda *args: pytest.fail("nothing is published"))
        assert store._current(db, item.aid, root)["active"]


@pytest.mark.asyncio
async def test_a_curator_round_for_another_record_cannot_retire_this_one(scope):
    item = _generic(scope, count=2)
    with store.SessionLocal() as db:
        previous = selections.effective_episode(db, store._fact(db, item.aid, item.ep))
    ep = selections.start_episode(item.scope, item.aid, episode={"episode_id": str(uuid4()),
        "reason": "Curator round for the first record", "obligations": previous["obligations"],
        "review_target_ids": [item.targets[0]]})
    parent = admission.create_scope(item.scope, operation_id=str(uuid4()), kind="workspace_operation",
                                    targets=1, episodes=1)
    sid = admission.create_scope(item.scope, operation_id=str(uuid4()), kind="document_run", targets=1,
                                 episodes=1, parent_id=parent)
    admission.attach_episode(item.scope, artifact_id=item.aid, episode_id=ep, scope_id=sid, predecessor_id=item.ep,
        renewal_actor_id=item.scope.owner_id, renewal_reason="Curator asked again", curator_round=True)
    from src.lib.validation_review.record_retirement import retire_in_transaction

    context = ReviewContext(item.scope, ep, "supervisor.validation_review")
    with store.SessionLocal.begin() as db:
        digest = selections.source_state_digest(db, item.aid)
        with pytest.raises(store.HistoryAccessError, match="requested record scope"):
            retire_in_transaction(db, context, operation_id="other-record", expected_source_sha=digest,
                records=[{"target_id": item.targets[1], "expected_version": 0, "reason": "Outside.", "evidence": {}}],
                source_envelope=item.checkpoint().envelope, rebuild_inputs=lambda db, envelope: {},
                publish=lambda *args: pytest.fail("nothing is published"))


@pytest.mark.parametrize("planned", ["three"], indirect=True)
def test_a_partial_follow_up_runs_the_records_with_a_follow_up_left(planned):
    item = planned
    entries = {obj.metadata["validation_history"]["target_id"]: obj.model_dump(mode="json")
               for obj in item.plan.envelope.extracted_objects}
    first, second, third = entries
    with store.SessionLocal.begin() as db:
        op = uuid4().hex
        parent = store._append(db, item.aid, operation_id="decision:" + op, kind="decision",
            body={"actor": {"type": "agent", "id": "supervisor"}, "entries": [
                {"target_id": target, "action": "defer", "reason": "Investigate"} for target in entries]})
        store._append(db, item.aid, operation_id="episode-disposition:" + op,
                      kind="episode_disposition", parent_id=item.ep, body={"selection_ids": []})
        parent_id = parent.id
    context = ReviewContext(item.scope, item.ep, "supervisor")
    receipt = {"agent_key": "saved", "execution_receipt": None, "contract": {"instructions": "Keep used reagents."}}

    def ask(names, operation_id):
        return extraction_scheduling.request(context, operation_id=operation_id, parent_decision_id=parent_id,
            entry_ids=names, instructions="Check Methods.", receipt=receipt,
            current_entries={name: entries[name] for name in names})

    ask([first], "first-follow-up")
    partial = ask([first, second], "second-follow-up")
    assert extraction_scheduling.exhausted_entries(item.scope, partial) == [first]
    with store.SessionLocal() as db:
        row = db.get(ValidationReviewWork, partial)
        assert row.payload["entry_ids"] == [second]
        assert set(row.payload["specialist_input"]["context"]["attempt"]) == {second}
    assert ask([first, second], "second-follow-up") == partial, "an exact retry reuses the same work"
    with pytest.raises(admission.ReviewStopped, match="extraction_attempts_exhausted"):
        ask([first, second], "all-spent")
    assert extraction_scheduling.exhausted_entries(item.scope, ask([third], "third-follow-up")) == []
