"""The review's final record facts reach the supervisor and the chat notes (ALL-1507).

Production trace 57c57c62: the reviewer retired Crb-GFP, yet the supervisor's
answer still counted and listed it, because the saved review summary held only
state and budgets. Code supplies the counts, labels and saved reasons; the
model's prose is never parsed or rewritten.
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
from src.lib.validation_review import admission, reads
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


async def _chat_review(scope, monkeypatch, count):
    """A real chat review of generic extraction-only records, as chat saves them."""
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


def _reviewer(chat, monkeypatch, actions):
    """The reviewer model reads the packet once and saves one decision per record
    through its real bound record_validation_review tool."""
    calls = []

    async def run(agent, input, **kwargs):
        calls.append(agent.name)
        if len(calls) == 1:
            record, = chat.records()
            packet = reads.review_packet(chat.history_scope(record), chat.reference(record)["episode_id"])
            handles = list(_record_handles(packet).values())
            surface = getattr(agent, "tool_surface", None)
            tool = next(tool for tool in (surface.source_tools if surface is not None else agent.tools)
                        if tool.name == "record_validation_review")
            decisions = [{"handle": handle, "action": action,
                          "reason": REASON.format(label=f"VT{index}") if action == "retire"
                          else f"VT{index} is listed among the stocks used.",
                          "absence_assessment": None, "unresolved_fields": None}
                         for index, (handle, action) in enumerate(zip(handles, actions))]
            encoded = json.dumps({"operation_id": "review-save", "packet_id": packet["packet_id"],
                                  "packet_decisions": decisions, "entries": None})
            saved = await tool.on_invoke_tool(ToolContext(context=None, tool_name=tool.name,
                tool_call_id="review-save", tool_arguments=encoded), encoded)
            assert "applied" in saved, saved
        output = "Reviewed every record."
        return SimpleNamespace(final_output=output, to_input_list=lambda: [
            *(input if isinstance(input, list) else [{"role": "user", "content": input}]),
            {"role": "assistant", "content": output}])

    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_with_owned_openai_resources", run)
    return calls


@pytest.mark.asyncio
async def test_reviewer_retirement_reaches_the_supervisor_summary_and_the_chat_notes(scope, monkeypatch):
    """Trace 57c57c62 in small: 4 records extracted, the reviewer retires one itself."""
    chat = await _chat_review(scope, monkeypatch, count=4)
    _reviewer(chat, monkeypatch, ["accept", "accept", "retire", "accept"])
    await chat.resume()
    record, = chat.records()
    target = record.payload_json["extracted_objects"][2]["metadata"]["validation_history"]["target_id"]
    outcome = chat.supervisor_handoff()["review_outcomes"][0]["outcome"]
    assert outcome["review"]["ready"] is True
    assert outcome["record_counts"] == {"accepted": 3, "pending": 0, "retired": 1}
    assert outcome["retired"] == [{"record_target_id": target, "label": "VT2",
                                   "reason": REASON.format(label="VT2"), "retired_by": "review"}]
    notes = chat.render_review_notes(current_turn_only=True)
    assert notes == ("\n\nReviewer's notes:\nReviewed every record.\n\n"
                     "Review removed 1 record that doesn't answer the question: VT2 ("
                     + REASON.format(label="VT2") + "). 3 records are current.")


@pytest.mark.asyncio
async def test_several_retirements_and_a_pending_record_are_named_together(scope, monkeypatch):
    chat = await _chat_review(scope, monkeypatch, count=4)
    _reviewer(chat, monkeypatch, ["retire", "reject", "accept", "retire"])
    await chat.resume()
    outcome = chat.supervisor_handoff()["review_outcomes"][0]["outcome"]
    assert outcome["review"]["ready"] is False
    assert outcome["record_counts"] == {"accepted": 1, "pending": 1, "retired": 2}
    assert [(item["label"], item["retired_by"]) for item in outcome["retired"]] == [
        ("VT0", "review"), ("VT3", "review")]
    notes = chat.render_review_notes(current_turn_only=True)
    removal = ("Review removed 2 records that don't answer the question: VT0 (" + REASON.format(label="VT0")
               + "); VT3 (" + REASON.format(label="VT3") + "). 2 records are current.")
    pending = "Review finished with 1 record still pending: VT1 (the reviewer did not accept it"
    assert removal in notes and pending in notes
    assert notes.index(removal) < notes.index(pending)


@pytest.mark.asyncio
async def test_a_review_that_retires_nothing_adds_only_its_counts(scope, monkeypatch):
    chat = await _chat_review(scope, monkeypatch, count=3)
    _reviewer(chat, monkeypatch, ["accept", "accept", "accept"])
    await chat.resume()
    outcome = chat.supervisor_handoff()["review_outcomes"][0]["outcome"]
    assert set(outcome) == {"output", "review", "record_counts"}
    assert outcome["record_counts"] == {"accepted": 3, "pending": 0, "retired": 0}
    assert chat.render_review_notes(current_turn_only=True) == "\n\nReviewer's notes:\nReviewed every record."


@pytest.mark.asyncio
async def test_a_reconnect_reuses_the_saved_summary_without_recounting(scope, monkeypatch):
    from src.lib.validation_review.chat import ChatReview

    chat = await _chat_review(scope, monkeypatch, count=2)
    calls = _reviewer(chat, monkeypatch, ["accept", "retire"])
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
