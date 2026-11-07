"""A resumed review is judged against the question of the turn that extracted it (v0.11.6 G2).

Dev trace 3489c72e: turn 1 asked for transgenic flies and its review did not
finish. Turn 2 asked about alleles, resumed turn 1's review, and the reviewer
judged the transgenic records against the allele question and retired a correct
stock. The question is read from the stored user row of the record's own turn.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from agents import Agent

from src.lib.domain_packs.validator_dispatch import ValidatorRuntimeContext
from src.lib.validation_history import runtime, store
from src.schemas.domain_envelope import DomainEnvelope
from tests.cancellation_test_support import install_cancel_reader
from tests.integration.persistence.chat_turn_support import record_user_turn
from tests.integration.persistence.test_review_grouped_sources import QUOTE, _evidence, _object
from tests.integration.persistence.test_validation_history import migrated as migrated, scope as scope

TRANSGENIC = "What transgenic flies are used in the publication?"
ALLELES = "What alleles were examined in the paper?"


async def _extracting_turn(scope, monkeypatch, *, recorded=True):
    """Turn 1 extracts generic records and saves them for review; its review does not run yet."""
    from src.lib.curation_workspace.adapter_registry import resolve_curation_domain_pack_by_id
    from src.lib.observability.cost_context import attach_agent_cost_identity
    from src.lib.validation_review.chat import ChatReview

    install_cancel_reader(monkeypatch, AsyncMock(return_value=False))
    monkeypatch.setattr("src.lib.agent_studio.catalog_service.resolve_tools", lambda *args: [])
    monkeypatch.setattr("src.lib.prompts.assembly.get_all_active_prompts", lambda: {})
    pack = resolve_curation_domain_pack_by_id("generic")
    envelope = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id="generic",
        extracted_objects=[_object(index, [f"e{index}"]) for index in range(2)],
        metadata={"evidence_records": [_evidence(f"e{index}", f"{QUOTE} {index}") for index in range(2)]})
    envelope = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex, envelope=envelope,
                                          typed=envelope.model_dump(mode="json"), provenance={})
    supervisor = Agent(name="Supervisor", model="gpt-6-astra", instructions="Policy")
    attach_agent_cost_identity(supervisor, {"agent_id": "supervisor", "agent_role": "supervisor"})
    turn_id = uuid4().hex
    if recorded:
        record_user_turn(owner_id=scope.owner_id, session_id=scope.session_id, turn_id=turn_id, question=TRANSGENIC)
    chat = ChatReview(scope.owner_id, scope.document_id, scope.session_id, turn_id, supervisor, (), TRANSGENIC)
    await chat.receive(envelope, pack, agent_key="fixture", adapter_key="fixture", tool_name="ask_fixture",
        execution_receipt=None, context=ValidatorRuntimeContext(
            user_id=scope.owner_id, document_id=scope.document_id, authenticated_groups=()))
    return chat


def _later_turn(chat, question):
    from src.lib.validation_review.chat import ChatReview

    turn_id = uuid4().hex
    record_user_turn(owner_id=chat.owner_id, session_id=chat.session_id, turn_id=turn_id, question=question)
    return ChatReview(chat.owner_id, chat.document_id, chat.session_id, turn_id, chat.supervisor, (), question)


def _reviewer_inputs(monkeypatch):
    """The reviewer model's first input of each run; it decides nothing."""
    inputs = []

    async def run(agent, input, **kwargs):
        if isinstance(input, str):
            inputs.append(json.loads(input))
        return SimpleNamespace(final_output="Nothing decided.", to_input_list=lambda: [
            *(input if isinstance(input, list) else [{"role": "user", "content": input}]),
            {"role": "assistant", "content": "Nothing decided."}])

    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_with_owned_openai_resources", run)
    return inputs


@pytest.mark.asyncio
async def test_a_later_turn_reviews_an_earlier_result_against_that_results_own_question(scope, monkeypatch):
    chat = await _extracting_turn(scope, monkeypatch)
    later = _later_turn(chat, ALLELES)
    inputs = _reviewer_inputs(monkeypatch)
    await later.resume()
    assert [item["user_request"] for item in inputs] == [TRANSGENIC], "not the later turn's allele question"
    outcome, = later.supervisor_handoff()["review_outcomes"]
    assert outcome["question"] == TRANSGENIC, "the supervisor sees which question a resumed outcome answered"


@pytest.mark.asyncio
async def test_a_review_in_its_own_turn_is_asked_that_turns_question(scope, monkeypatch):
    chat = await _extracting_turn(scope, monkeypatch)
    inputs = _reviewer_inputs(monkeypatch)
    await chat.resume()
    assert [item["user_request"] for item in inputs] == [TRANSGENIC] == [chat.user_request]
    assert chat.supervisor_handoff()["review_outcomes"][0]["question"] == TRANSGENIC


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["turn_row", "chat"])
async def test_a_result_whose_question_is_not_stored_is_refused_not_guessed(scope, monkeypatch, missing):
    from src.lib.validation_review.chat import ChatReview

    chat = await _extracting_turn(scope, monkeypatch, recorded=False)
    if missing == "turn_row":
        later = _later_turn(chat, ALLELES)
        expected = "The question that produced this saved result is unavailable"
    else:
        later = ChatReview(chat.owner_id, chat.document_id, chat.session_id, uuid4().hex, chat.supervisor, (),
                           ALLELES)
        expected = "The chat that asked for this saved result is unavailable"
    inputs = _reviewer_inputs(monkeypatch)
    with pytest.raises(store.HistoryConflict, match=expected):
        await later.resume()
    assert inputs == [], "the reviewer is never asked a different turn's question"


def test_a_flow_step_reviews_its_record_against_its_own_step_query():
    """A flow reviews only its own step record; its question is the step's resolved query."""
    from src.lib.validation_review.flow import FlowReview

    step = SimpleNamespace(user_request="Extract the alleles named in Table 2.")
    assert FlowReview.review_request(step, record=None) == "Extract the alleles named in Table 2."
