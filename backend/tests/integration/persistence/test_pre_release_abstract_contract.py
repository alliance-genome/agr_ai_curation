"""A review saved before agents stopped receiving the paper's abstract still loads.

Releases before KANBAN-2143 pinned an extractor contract whose saved context had an
"abstract" key and whose instructions carried a "## Paper Abstract" block. Work is
forward-only: such a review is not repaired. It must still load, and asking for a
targeted re-check must end in the typed "saved extractor unavailable" stop that the
curator sees as "re-check did not run", never an unhandled error.
"""
import json
from uuid import uuid4

import pytest

from src.lib.validation_history import store
from src.lib.validation_review.decisions import ReviewContext
from tests.integration.persistence.test_chat_review_adapter import (
    checkpoint_provider, migrated as migrated, planned as planned, scope as scope,
)

PRE_RELEASE_INSTRUCTIONS = (
    "Extract evidence for the curator's question.\n\n"
    "## Document Structure\n\n**Abstract** (p.1, 1 chunks)\n\n"
    "## Paper Abstract\n\nA synthetic summary of a synthetic study."
)


def _pre_release_contract():
    from agents import Agent
    from src.lib.validation_review import extractor_phase

    agent = Agent(name="Saved extractor", model="test", instructions=PRE_RELEASE_INSTRUCTIONS, tools=[])
    # What the earlier release saved: every to_agent_kwargs() key, abstract included.
    agent.review_reconstruction_context = {
        "document_name": "paper.pdf",
        "hierarchy": {"sections": [{"name": "Abstract"}], "top_level_sections": ["Abstract"],
                      "abstract_section_title": "Abstract"},
        "sections": ["Abstract"],
        "abstract": "A synthetic summary of a synthetic study.",
    }
    return extractor_phase.contract(agent, max_turns=17)


@pytest.fixture
def pre_release_capture(monkeypatch):
    """Validation history is immutable, so the old contract is saved at capture, as releases did."""
    from src.lib.validation_history import runtime

    contract = _pre_release_contract()
    capture = runtime.capture_extraction

    def with_old_contract(**kwargs):
        kwargs["provenance"] = {**kwargs["provenance"], "agent_key": "saved_extractor",
                                "execution_receipt": None, "extractor_contract": contract}
        return capture(**kwargs)
    monkeypatch.setattr(runtime, "capture_extraction", with_old_contract)
    return contract


@pytest.mark.asyncio
async def test_review_saved_with_an_abstract_contract_loads_and_its_recheck_stops_typed(
    pre_release_capture, planned, monkeypatch,
):
    from agents.tool_context import ToolContext
    from src.lib.openai_agents import streaming_tools
    from src.lib.validation_review.extractor_adapter import ExtractorAdapter
    from src.lib.validation_review.progress import review_progress
    from src.lib.validation_review.tools import create_tools

    item = planned
    contract = pre_release_capture

    # It loads: the saved contract, its abstract block included, reads back unchanged.
    baseline = store.read_baseline(item.scope, item.aid)
    assert baseline["provenance"]["extractor_contract"] == contract
    assert "## Paper Abstract" in baseline["provenance"]["extractor_contract"]["instructions"]
    assert baseline["provenance"]["extractor_contract"]["reconstruction_context"]["abstract"]

    target = item.plan.envelope.extracted_objects[0].metadata["validation_history"]["target_id"]
    with store.SessionLocal.begin() as db:
        op = uuid4().hex
        parent = store._append(db, item.aid, operation_id="decision:" + op, kind="decision",
            body={"actor": {"type": "agent", "id": "supervisor"}, "entries": [
                {"target_id": obj.metadata["validation_history"]["target_id"], "action": "defer",
                 "reason": "Investigate"} for obj in item.plan.envelope.extracted_objects]})
        store._append(db, item.aid, operation_id="episode-disposition:" + op,
                      kind="episode_disposition", parent_id=item.ep, body={"selection_ids": []})
        parent_id = parent.id

    def never_built(*args, **kwargs):
        raise AssertionError("a pre-release contract must not be rebuilt")
    monkeypatch.setattr("src.lib.agent_studio.catalog_service.get_agent_by_id", never_built)

    async def never_called(*args, **kwargs):
        raise AssertionError("no model call for a stopped re-check")
    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_with_owned_openai_resources", never_called)
    monkeypatch.setattr("src.lib.prompts.assembly.get_all_active_prompts", lambda: {})
    monkeypatch.setattr(streaming_tools, "write_stream_event", lambda *args, **kwargs: None)

    context = ReviewContext(item.scope, item.ep, "supervisor")
    checkpoint = checkpoint_provider(item)
    # No source agent: a later chat turn rebuilds the extractor from the saved contract.
    adapter = ExtractorAdapter(context, item.aid, authenticated_groups=(), checkpoint_provider=checkpoint)
    assert adapter.receipt["contract"] == contract

    async def unused(**arguments):
        raise AssertionError("not called")
    tool = next(tool for tool in create_tools(context, checkpoint_provider=checkpoint,
                                              request_extraction=adapter.request,
                                              select_source=unused, assess_source=unused)
                if tool.name == "request_targeted_extraction")
    arguments = json.dumps({"operation_id": "pre-release-recheck", "parent_decision_id": parent_id,
                            "entry_ids": [target], "instructions": "Read the Abstract section."})

    events = []
    streaming_tools.set_live_event_list(events)
    try:
        with review_progress(item.ep):
            result = await tool.on_invoke_tool(
                ToolContext(context=None, tool_name=tool.name, tool_call_id="pre-release-recheck",
                            tool_arguments=arguments), arguments)
    finally:
        streaming_tools.set_live_event_list(None)

    assert (result["state"], result["code"]) == ("not_applied", "saved_extractor_contract_unavailable")
    shown = [(event["details"]["friendlyName"], event["details"].get("severity"))
             for event in events if event["type"].startswith("TOOL_")]
    assert shown == [
        ("Supervisor review: asking the extraction agent to re-check 1 record", None),
        ("Supervisor review: re-check did not run", "warning"),
    ]
    # The saved review is untouched and still loads after the stop.
    assert store.read_baseline(item.scope, item.aid)["provenance"]["extractor_contract"] == contract
