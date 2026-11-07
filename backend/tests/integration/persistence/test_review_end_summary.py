"""The review's final record facts reach the supervisor and the chat notes (ALL-1507).

Production trace 57c57c62: the reviewer retired Crb-GFP, yet the supervisor's
answer still counted and listed it, because the saved review summary held only
state and budgets. Code supplies the counts, labels and saved reasons; the
model's prose is never parsed or rewritten. A removal is announced in chat once,
on the turn it is first saved.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from agents import Agent
from agents.tool_context import ToolContext

from src.lib.domain_packs.validator_dispatch import ValidatorRuntimeContext
from src.lib.validation_history import runtime, selections, store
from src.lib.validation_review import admission, extraction_results, extraction_scheduling, reads, work
from src.lib.validation_review.decisions import ReviewContext
from src.lib.validation_review.record_retirement import retire_in_transaction
from src.schemas.domain_envelope import DomainEnvelope
from tests.cancellation_test_support import install_cancel_reader
from tests.integration.persistence.test_chat_review_adapter import (
    checkpoint_provider, migrated as migrated, planned as planned, scope as scope,
)
from tests.integration.persistence.test_record_retirement import proposal
from tests.integration.persistence.test_review_grouped_sources import (
    QUOTE, _evidence, _object, cancel_reader as cancel_reader,
)
from tests.integration.persistence.test_reviewer_retirement import _decision, _generic, _record_handles, _save

QUESTION = "What transgenic flies are used in the publication?"
REASON = "{label} is named only in a Discussion legend; its use in this study is not established."
OUTPUT = "Reviewed every record."


def _reason(label):
    return REASON.format(label=label)


def _removal_line(labels, current):
    """The expected chat line: each saved reason without its final period, then the current count."""
    noun = "record that doesn't" if len(labels) == 1 else "records that don't"
    parts = "; ".join(f"{label} ({_reason(label).removesuffix('.')})" for label in labels)
    return f"Review removed {len(labels)} {noun} answer the question: {parts}. {current} records are current."


async def _chat_review(scope, monkeypatch, count):
    """A real chat review of generic extraction-only records VT0..VTn, as chat saves them."""
    from src.lib.curation_workspace.adapter_registry import resolve_curation_domain_pack_by_id
    from src.lib.observability.cost_context import attach_agent_cost_identity
    from src.lib.validation_review.chat import ChatReview

    install_cancel_reader(monkeypatch, AsyncMock(return_value=False))
    monkeypatch.setattr("src.lib.agent_studio.catalog_service.resolve_tools", lambda *args: [])
    monkeypatch.setattr("src.lib.prompts.assembly.get_all_active_prompts", lambda: {})
    pack = resolve_curation_domain_pack_by_id("generic")
    envelope = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id="generic",
        extracted_objects=[_object(index, [f"e{index}"]) for index in range(count)],
        metadata={"evidence_records": [_evidence(f"e{index}", f"{QUOTE} {index}") for index in range(count)]})
    envelope = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex, envelope=envelope,
                                          typed=envelope.model_dump(mode="json"), provenance={})
    supervisor = Agent(name="Supervisor", model="gpt-6-astra", instructions="Policy")
    attach_agent_cost_identity(supervisor, {"agent_id": "supervisor", "agent_role": "supervisor"})
    chat = ChatReview(scope.owner_id, scope.document_id, scope.session_id, uuid4().hex, supervisor, (), QUESTION)
    await chat.receive(envelope, pack, agent_key="fixture", adapter_key="fixture", tool_name="ask_fixture",
        execution_receipt=None, context=ValidatorRuntimeContext(
            user_id=scope.owner_id, document_id=scope.document_id, authenticated_groups=()))
    return chat


def _next_turn(chat):
    """The curator's next chat turn in the same session resumes the same saved review."""
    from src.lib.validation_review.chat import ChatReview

    return ChatReview(chat.owner_id, chat.document_id, chat.session_id, uuid4().hex, chat.supervisor, (), QUESTION)


async def _invoke(tool, arguments):
    encoded = json.dumps(arguments)
    return await tool.on_invoke_tool(ToolContext(context=None, tool_name=tool.name,
        tool_call_id=arguments["operation_id"], tool_arguments=encoded), encoded)


def _reviewer(chat, monkeypatch, actions, *, approve=None):
    """The reviewer model's run, through its real bound tools.

    It first approves an extraction agent's retirement recommendation, when one
    is given as (label, contribution reference), then reads a fresh packet and
    saves one decision per record it is given, by record label.
    """
    calls = []

    async def run(agent, input, **kwargs):
        calls.append(agent.name)
        if len(calls) == 1:
            surface = getattr(agent, "tool_surface", None)
            tools = {tool.name: tool for tool in (surface.source_tools if surface is not None else agent.tools)}
            record, = chat.records()
            scope, episode_id = chat.history_scope(record), chat.reference(record)["episode_id"]
            if approve is not None:
                label, contribution = approve
                decided = await _invoke(tools["decide_record_retirement"], {
                    "operation_id": "approve-recommendation", "contribution": contribution, "decision": "approve",
                    "expected_source_sha": reads.review_packet(scope, episode_id)["source_state_sha256"],
                    "reason": _reason(label)})
                assert decided.get("retired") is True, decided
            packet = reads.review_packet(scope, episode_id)
            labels = {obj["metadata"]["validation_history"]["target_id"]: obj["payload"]["label"]
                      for obj in record.payload_json["extracted_objects"]}
            decisions = [{"handle": item["review"][0]["handle"], "action": actions[labels[item["target_id"]]],
                          "reason": _reason(labels[item["target_id"]]) if actions[labels[item["target_id"]]] == "retire"
                          else labels[item["target_id"]] + " is listed among the stocks used.",
                          "absence_assessment": None, "unresolved_fields": None}
                         for item in packet["records"] if labels[item["target_id"]] in actions]
            saved = await _invoke(tools["record_validation_review"], {
                "operation_id": "review-save:" + chat.turn_id, "packet_id": packet["packet_id"],
                "packet_decisions": decisions, "entries": None})
            assert saved.get("state") != "conflict" and len(saved["applied"]) == len(decisions), saved
        return SimpleNamespace(final_output=OUTPUT, to_input_list=lambda: [
            *(input if isinstance(input, list) else [{"role": "user", "content": input}]),
            {"role": "assistant", "content": OUTPUT}])

    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_with_owned_openai_resources", run)
    return calls


def _outcome(chat):
    return chat.supervisor_handoff()["review_outcomes"][0]["outcome"]


def _target(chat, label):
    record, = chat.records()
    return next(obj["metadata"]["validation_history"]["target_id"] for obj in record.payload_json["extracted_objects"]
                if obj["payload"]["label"] == label)


def _recommend_retirement(chat, label):
    """The extraction agent's targeted re-check recommends retiring one record.

    The follow-up work is admitted, run and saved through the real scheduling and
    result paths, as test_record_retirement.proposal does for its fixture.
    """
    record, = chat.records()
    context = chat.context(record)
    aid = chat.reference(record)["artifact_id"]
    obj = next(obj for obj in chat.checkpoint(record).envelope.extracted_objects if obj.payload["label"] == label)
    entry = obj.metadata["validation_history"]["target_id"]
    operation = uuid4().hex
    with store.SessionLocal.begin() as db:
        parent = store._append(db, aid, operation_id="decision:" + operation, kind="decision",
            body={"actor": {"type": "agent", "id": context.actor_id},
                  "entries": [{"target_id": entry, "action": "defer", "reason": "Check its use in this study"}]})
        store._append(db, aid, operation_id="episode-disposition:" + operation, kind="episode_disposition",
                      parent_id=context.episode_id, body={"selection_ids": []})
        parent_id = parent.id
    wid = extraction_scheduling.request(context, operation_id=uuid4().hex, parent_decision_id=parent_id,
        entry_ids=[entry], instructions="Check whether this stock was used in this study.",
        receipt={"agent_key": "saved", "execution_receipt": None,
                 "contract": {"instructions": "Keep reagents used in this study."}},
        current_entries={entry: obj.model_dump(mode="json")})
    claim = work.claim(context.history_scope, wid)
    work.mark_submitted(context.history_scope, wid, claim["lease_token"])
    retained = extraction_results.retain(context.history_scope, wid, claim["lease_token"], {"entries": [{
        "entry_id": entry, "outcome": "propose_retirement",
        "assessment": "The paper shows this stock only in prior work.",
        "remaining_uncertainty": "Absence alone is not evidence of non-use.",
        "proposed_values": [], "evidence_record_ids": []}]}, evidence_records=[])
    work.finish(context.history_scope, wid, claim["lease_token"], outcome=retained)
    return retained["contributions"][0]


@pytest.mark.asyncio
async def test_reviewer_retirement_reaches_the_supervisor_summary_and_the_chat_notes(scope, monkeypatch):
    """Trace 57c57c62 in small: 4 records extracted, the reviewer retires one itself."""
    chat = await _chat_review(scope, monkeypatch, count=4)
    _reviewer(chat, monkeypatch, {"VT0": "accept", "VT1": "accept", "VT2": "retire", "VT3": "accept"})
    await chat.resume()
    outcome = _outcome(chat)
    assert outcome["review"]["ready"] is True
    assert outcome["review_counts"] == {"accepted": 3, "pending": 0, "retired_in_this_review": 1}
    assert outcome["retired"] == [{"record_target_id": _target(chat, "VT2"), "label": "VT2",
                                   "reason": _reason("VT2"), "retired_by": "review", "new_this_turn": True}]
    assert chat.render_review_notes(current_turn_only=True) == (
        f"\n\nReviewer's notes:\n{OUTPUT}\n\n" + _removal_line(["VT2"], 3))


@pytest.mark.asyncio
async def test_an_approved_extractor_recommendation_is_named_in_the_summary_and_the_chat(scope, monkeypatch):
    chat = await _chat_review(scope, monkeypatch, count=3)
    contribution = _recommend_retirement(chat, "VT1")
    _reviewer(chat, monkeypatch, {"VT0": "accept", "VT2": "accept"}, approve=("VT1", contribution))
    await chat.resume()
    outcome = _outcome(chat)
    assert outcome["review"]["ready"] is True
    assert outcome["review_counts"] == {"accepted": 2, "pending": 0, "retired_in_this_review": 1}
    assert outcome["retired"] == [{"record_target_id": _target(chat, "VT1"), "label": "VT1",
                                   "reason": _reason("VT1"), "retired_by": "extractor_recommendation",
                                   "new_this_turn": True}]
    assert chat.render_review_notes(current_turn_only=True) == (
        f"\n\nReviewer's notes:\n{OUTPUT}\n\n" + _removal_line(["VT1"], 2))


@pytest.mark.asyncio
async def test_several_retirements_and_a_pending_record_are_named_together(scope, monkeypatch):
    chat = await _chat_review(scope, monkeypatch, count=4)
    _reviewer(chat, monkeypatch, {"VT0": "retire", "VT1": "reject", "VT2": "accept", "VT3": "retire"})
    await chat.resume()
    outcome = _outcome(chat)
    assert outcome["review"]["ready"] is False
    assert outcome["review_counts"] == {"accepted": 1, "pending": 1, "retired_in_this_review": 2}
    assert [(item["label"], item["retired_by"], item["new_this_turn"]) for item in outcome["retired"]] == [
        ("VT0", "review", True), ("VT3", "review", True)]
    notes = chat.render_review_notes(current_turn_only=True)
    removal = _removal_line(["VT0", "VT3"], 2)
    pending = "Review finished with 1 record still pending: VT1 (the reviewer did not accept it"
    assert removal in notes and pending in notes
    assert notes.index(removal) < notes.index(pending)


@pytest.mark.asyncio
async def test_a_review_that_retires_nothing_adds_only_its_counts(scope, monkeypatch):
    chat = await _chat_review(scope, monkeypatch, count=3)
    _reviewer(chat, monkeypatch, {"VT0": "accept", "VT1": "accept", "VT2": "accept"})
    await chat.resume()
    outcome = _outcome(chat)
    assert set(outcome) == {"output", "review", "review_counts"}
    assert outcome["review_counts"] == {"accepted": 3, "pending": 0, "retired_in_this_review": 0}
    assert chat.render_review_notes(current_turn_only=True) == f"\n\nReviewer's notes:\n{OUTPUT}"


@pytest.mark.asyncio
@pytest.mark.parametrize("second_turn", ["accept", "retire"])
async def test_a_later_turn_announces_only_its_own_removals(scope, monkeypatch, second_turn):
    """Turn 1 removes VT1 and leaves VT2 undecided; turn 2 resumes the same review."""
    reports = []
    monkeypatch.setattr("src.lib.validation_review.audit.report_operational_failure", reports.append)
    chat = await _chat_review(scope, monkeypatch, count=4)
    _reviewer(chat, monkeypatch, {"VT0": "accept", "VT1": "retire", "VT3": "accept"})
    await chat.resume()
    assert _outcome(chat)["review"]["ready"] is False, "VT2 is still waiting for the reviewer"
    assert "Review removed 1 record that doesn't answer the question: VT1 (" in chat.render_review_notes(
        current_turn_only=True)
    later = _next_turn(chat)
    calls = _reviewer(later, monkeypatch, {"VT2": second_turn})
    await later.resume()
    notes = later.render_review_notes(current_turn_only=True)
    assert "VT1 (" not in notes, "turn 1's removal is not announced again"
    if second_turn == "accept":
        assert notes == f"\n\nReviewer's notes:\n{OUTPUT}", "turn 1's removal is not announced again"
    else:
        assert notes == f"\n\nReviewer's notes:\n{OUTPUT}\n\n" + _removal_line(["VT2"], 2)
    assert len(calls) == 1
    outcome = _outcome(later)
    assert outcome["review"]["ready"] is True
    assert [(item["label"], item["new_this_turn"]) for item in outcome["retired"]] == (
        [("VT1", False)] if second_turn == "accept" else [("VT1", False), ("VT2", True)])
    assert outcome["review_counts"]["retired_in_this_review"] == len(outcome["retired"])


@pytest.mark.asyncio
async def test_a_chat_turn_after_a_curator_round_announces_no_removal(scope, monkeypatch):
    """A curator Revalidate round supersedes the chat's review; the next chat turn
    only reports the pause and does not repeat the earlier removal."""
    chat = await _chat_review(scope, monkeypatch, count=3)
    _reviewer(chat, monkeypatch, {"VT0": "accept", "VT1": "retire", "VT2": "accept"})
    await chat.resume()
    record, = chat.records()
    history, episode_id = chat.history_scope(record), chat.reference(record)["episode_id"]
    aid = chat.reference(record)["artifact_id"]
    with store.SessionLocal() as db:
        previous = selections.effective_episode(db, store._fact(db, aid, episode_id))
    curator_round = selections.start_episode(history, aid, episode={"episode_id": str(uuid4()),
        "reason": "Curator asked to revalidate VT0", "obligations": previous["obligations"],
        "review_target_ids": [_target(chat, "VT0")]})
    parent = admission.create_scope(history, operation_id=str(uuid4()), kind="workspace_operation",
                                    targets=1, episodes=1)
    sid = admission.create_scope(history, operation_id=str(uuid4()), kind="document_run", targets=1,
                                 episodes=1, parent_id=parent)
    admission.attach_episode(history, artifact_id=aid, episode_id=curator_round, scope_id=sid,
        predecessor_id=episode_id, renewal_actor_id=history.owner_id, renewal_reason="Curator asked again",
        curator_round=True)
    later = _next_turn(chat)
    calls = _reviewer(later, monkeypatch, {})
    await later.resume()
    notes = later.render_review_notes(current_turn_only=True)
    assert "Review removed" not in notes, notes
    assert notes == "\n\nReviewer's notes:\nReview paused: superseded. Saved results and history are retained."
    assert calls == []
    assert [(item["label"], item["new_this_turn"]) for item in _outcome(later)["retired"]] == [("VT1", False)]


@pytest.mark.asyncio
async def test_a_reconnect_reuses_the_saved_summary_without_recounting(scope, monkeypatch):
    from src.lib.validation_review.chat import ChatReview

    chat = await _chat_review(scope, monkeypatch, count=2)
    calls = _reviewer(chat, monkeypatch, {"VT0": "accept", "VT1": "retire"})
    await chat.resume()
    saved = chat.review_notes()
    record, = chat.records()
    reconnected = ChatReview(chat.owner_id, chat.document_id, chat.session_id, chat.turn_id,
                             chat.supervisor, (), QUESTION)
    # A reconnect re-retains the saved outcome of its own turn; it never recounts it.
    monkeypatch.setattr("src.lib.validation_review.record_retirement.retired_in_review",
                        lambda *args: pytest.fail("a saved summary is never recounted"))
    reconnected.retain_review_outcome(record, reconnected.saved_review_outcome(record))
    assert reconnected.review_notes() == saved and len(calls) == 1
    assert reconnected.render_review_notes(current_turn_only=True) == chat.render_review_notes(
        current_turn_only=True), "a reconnect of the same turn shows the same notes"


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["approve", "decline"])
async def test_an_approved_extractor_recommendation_is_listed_and_a_declined_one_is_not(planned, decision):
    from src.lib.validation_review.record_retirement import retired_in_review

    item = planned
    context, ref, target = proposal(item)
    with store.SessionLocal() as db:
        digest = selections.source_state_digest(db, item.aid)
    await item.adapter.select_source(context, checkpoint_provider(item), operation_id="decide-recommendation",
        contribution_ref=ref, retirement_decision=decision, expected_source_sha=digest,
        reason="The extraction agent showed it falls outside the requested task.")
    with store.SessionLocal() as db:
        retired = retired_in_review(db, item.aid, item.ep)
    if decision == "decline":
        assert retired == [], "a declined recommendation removes nothing"
        return
    entry, = retired
    assert (entry["record_target_id"], entry["retired_by"], entry["reason"]) == (
        target, "extractor_recommendation", "The extraction agent showed it falls outside the requested task.")
    assert entry["entry"]["metadata"]["validation_history"]["target_id"] == target


@pytest.mark.asyncio
async def test_a_curator_round_lists_no_removal_from_an_earlier_round(scope):
    """The curator's round cannot retire the records the curator asked about, and a
    record retired earlier belongs to the round that retired it."""
    from src.lib.validation_review.record_retirement import retired_in_review

    item = _generic(scope, count=2)
    packet = reads.review_packet(item.scope, item.ep)
    handles = _record_handles(packet)
    await _save(item, packet["packet_id"], [_decision(handles[item.targets[0]], "accept"),
        _decision(handles[item.targets[1]], "retire", "Not a transgenic fly.")], operation_id="first-round")
    with store.SessionLocal() as db:
        previous = selections.effective_episode(db, store._fact(db, item.aid, item.ep))
    curator_round = selections.start_episode(item.scope, item.aid, episode={"episode_id": str(uuid4()),
        "reason": "Curator round for the first record", "obligations": previous["obligations"],
        "review_target_ids": [item.targets[0]]})
    parent = admission.create_scope(item.scope, operation_id=str(uuid4()), kind="workspace_operation",
                                    targets=1, episodes=1)
    sid = admission.create_scope(item.scope, operation_id=str(uuid4()), kind="document_run", targets=1,
                                 episodes=1, parent_id=parent)
    admission.attach_episode(item.scope, artifact_id=item.aid, episode_id=curator_round, scope_id=sid,
        predecessor_id=item.ep, renewal_actor_id=item.scope.owner_id, renewal_reason="Curator asked again",
        curator_round=True)
    with store.SessionLocal.begin() as db:
        with pytest.raises(store.HistoryConflict, match="curator asked to re-review this record"):
            retire_in_transaction(db, ReviewContext(item.scope, curator_round, "supervisor.validation_review"),
                operation_id="requested-record", expected_source_sha=selections.source_state_digest(db, item.aid),
                records=[{"target_id": item.targets[0], "expected_version": None, "reason": "Outside.",
                          "evidence": {}}],
                source_envelope=item.checkpoint().envelope, rebuild_inputs=lambda db, envelope: {},
                publish=lambda *args: pytest.fail("nothing is published"))
    with store.SessionLocal() as db:
        assert retired_in_review(db, item.aid, curator_round) == []
        assert [entry["record_target_id"] for entry in retired_in_review(db, item.aid, item.ep)] == [
            item.targets[1]]
