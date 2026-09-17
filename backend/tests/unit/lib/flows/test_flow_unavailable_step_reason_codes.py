"""ALL-1244: pre-start flow refusals record safe per-step reason codes.

The 2026-09-17 production refusal (Sentry AI-CURATION-BACKEND-PROD-J) stored
only step identity, so neither the curator message nor Agent Studio could say
why the flow could not start. These tests pin the stable machine codes, the
per-cause curator message, and that raw reasons/exception text never leak.
"""

import importlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from agents import Agent


PRIVATE_EXCEPTION_TEXT = "connect failed: PRIVATE-CONFIG-SENTINEL-7f3a at internal-host"
DOCUMENT_REASON = "agent requires a document, but no document is loaded"


def _executor():
    return importlib.import_module("src.lib.flows.executor")


def _flow(*agent_ids):
    nodes = [{
        "id": "node_task", "type": "task_input",
        "data": {"agent_id": "task_input", "agent_display_name": "Task Input",
                 "output_key": "task_out", "task_instructions": "Find alleles"},
    }]
    for index, agent_id in enumerate(agent_ids, start=1):
        data = {"output_key": f"n{index}_out"}
        if agent_id is not None:
            data.update(agent_id=agent_id, agent_display_name=f"Display {agent_id}")
        nodes.append({"id": f"n{index}", "type": "agent", "data": data})
    flow = MagicMock()
    flow.flow_definition = {
        "version": "1.1", "nodes": nodes,
        "edges": [{"id": f"e{i}", "source": nodes[i]["id"], "target": nodes[i + 1]["id"]}
                  for i in range(len(nodes) - 1)],
        "entry_node_id": nodes[0]["id"],
    }
    flow.name = "Identify MGI Allele IDs"
    flow.id = "f05d8320-c96d-4145-8c67-f18408edb3ca"
    return flow


_REGISTRY = {
    "mouse_allele": {"display_name": "Mouse Allele Identification", "requires_document": True},
    "allele_extraction": {"display_name": "Allele/Variant Extraction Agent (Custom)2",
                          "requires_document": True},
    "allele_validation": {"display_name": "Allele Validation Agent", "requires_document": False,
                          "category": "Validation", "supervisor": {"enabled": False}},
    "broken_agent": {"display_name": "Broken Agent", "requires_document": False},
    "disabled_agent": {"display_name": "Disabled Agent", "requires_document": False},
}


@pytest.fixture
def registry(monkeypatch):
    def _metadata(agent_id, **_kwargs):
        entry = _REGISTRY.get(agent_id)
        if entry is None:
            raise ValueError(f"Unknown agent_id: {agent_id}")
        return {
            "agent_id": agent_id,
            "display_name": entry["display_name"],
            "description": "fixture",
            "category": entry.get("category", "Test"),
            "subcategory": "",
            "requires_document": entry["requires_document"],
            "required_params": ["document_id"] if entry["requires_document"] else [],
            "curation": None,
            "supervisor": entry.get("supervisor") or {},
        }

    monkeypatch.setattr("src.lib.flows.executor.get_agent_metadata", _metadata)

    def _get_agent(agent_id, **_kwargs):
        from src.lib.openai_agents.config import ProviderDisabledError

        if agent_id == "broken_agent":
            raise RuntimeError(PRIVATE_EXCEPTION_TEXT)
        if agent_id == "disabled_agent":
            raise ProviderDisabledError("private-provider")
        return MagicMock(spec=Agent, instructions="Base")

    monkeypatch.setattr("src.lib.flows.executor.get_agent_by_id", _get_agent)
    monkeypatch.setattr("src.lib.flows.executor._create_streaming_tool",
                        lambda **_kwargs: MagicMock())


def _unavailable(flow, **kwargs):
    _tools, _names, unavailable_steps, _state = _executor().get_all_agent_tools(
        flow, include_unavailable=True, **kwargs,
    )
    return unavailable_steps


def test_document_required_steps_record_reason_code(registry):
    """The 11:19 UTC case: both document-requiring steps with no PDF loaded."""
    steps = _unavailable(_flow("mouse_allele", "allele_extraction"), document_id=None)
    assert [(step["step"], step["reason_code"]) for step in steps] == [
        (1, "document_required"), (2, "document_required"),
    ]


def test_each_unavailable_cause_has_stable_reason_code(registry):
    flow = _flow("allele_validation", "missing_agent", "broken_agent", "disabled_agent", None)
    steps = _unavailable(flow, document_id="doc-1")
    assert {step["step"]: step["reason_code"] for step in steps} == {
        1: "attachment_only_validator",
        2: "agent_unresolvable",
        3: "agent_unavailable",
        4: "provider_disabled",
        5: "missing_agent_id",
    }


async def _run(flow, unavailable_steps, monkeypatch):
    supervisor = SimpleNamespace(_flow_unavailable_steps=unavailable_steps)
    monkeypatch.setattr("src.lib.flows.executor.create_flow_supervisor", lambda **_kwargs: supervisor)
    monkeypatch.setattr("src.lib.flows.executor.build_flow_prompt", lambda *args: "fixture")
    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_streamed",
                        lambda **_kwargs: pytest.fail("Invalid flow started the model"))
    return [event async for event in _executor().execute_flow(flow, user_id="u1", session_id="s1")]


@pytest.mark.asyncio
async def test_missing_pdf_refusal_stores_codes_and_names_pdf_cause(registry, monkeypatch):
    flow = _flow("mouse_allele", "allele_extraction")
    steps = _unavailable(flow, document_id=None)
    events = await _run(flow, steps, monkeypatch)

    assert [event["type"] for event in events] == ["FLOW_STARTED", "FLOW_ERROR", "FLOW_FINISHED"]
    details = events[1]["details"]
    assert details["reason"] == "flow_step_unavailable"
    assert [(s["step"], s["agent_name"], s["reason_code"]) for s in details["unavailable_steps"]] == [
        (1, "Mouse Allele Identification", "document_required"),
        (2, "Allele/Variant Extraction Agent (Custom)2", "document_required"),
    ]
    message = details["message"]
    assert "Mouse Allele Identification" in message
    assert "needs a PDF" in message or "need a PDF" in message
    assert "Open Documents in the top navigation" in message
    assert "validation attachment" not in message

    finished = events[-1]["data"]
    assert finished["status"] == "failed"
    assert finished["failure_reason"] == message
    assert finished["unavailable_step_reason_codes"] == [
        {"step": 1, "reason_code": "document_required"},
        {"step": 2, "reason_code": "document_required"},
    ]
    assert DOCUMENT_REASON not in json.dumps(events)


@pytest.mark.asyncio
async def test_attachment_only_validator_refusal_gives_validator_advice_only(registry, monkeypatch):
    """The 2026-09-16 case: PDF loaded, validator used as an ordinary step."""
    flow = _flow("allele_extraction", "allele_validation")
    steps = _unavailable(flow, document_id="doc-1")
    events = await _run(flow, steps, monkeypatch)
    details = events[1]["details"]
    assert details["reason"] == "flow_step_unavailable"
    assert [s["reason_code"] for s in details["unavailable_steps"]] == ["attachment_only_validator"]
    assert "validation attachment" in details["message"]
    assert "PDF" not in details["message"]
    assert events[-1]["data"]["unavailable_step_reason_codes"] == [
        {"step": 2, "reason_code": "attachment_only_validator"},
    ]
    # The raw policy reason embeds curator-written text and is never emitted.
    assert "DomainValidationRequest" not in json.dumps(events)


@pytest.mark.asyncio
async def test_mixed_causes_list_each_step_and_keep_provider_policy_wording(registry, monkeypatch):
    flow = _flow("mouse_allele", "allele_validation", "disabled_agent")
    steps = _unavailable(flow, document_id=None)
    events = await _run(flow, steps, monkeypatch)
    details = events[1]["details"]
    assert details["reason"] == "provider_disabled"
    assert [s["reason_code"] for s in details["unavailable_steps"]] == [
        "document_required", "attachment_only_validator", "provider_disabled",
    ]
    message = details["message"]
    assert "Step 1 (Mouse Allele Identification) needs a PDF" in message
    assert "Step 2 (Allele Validation Agent)" in message and "validation attachment" in message
    assert "Step 3 (Disabled Agent)" in message
    assert "disabled by policy" in message and "approved model" in message
    assert "private-provider" not in json.dumps(events)


@pytest.mark.asyncio
async def test_agent_unavailable_never_exposes_exception_text(registry, monkeypatch):
    flow = _flow("broken_agent")
    steps = _unavailable(flow, document_id="doc-1")
    assert steps[0]["reason_code"] == "agent_unavailable"
    events = await _run(flow, steps, monkeypatch)
    serialized = json.dumps(events)
    assert PRIVATE_EXCEPTION_TEXT not in serialized
    assert "PRIVATE-CONFIG-SENTINEL-7f3a" not in serialized
    assert events[1]["details"]["reason"] == "flow_step_unavailable"
    assert events[1]["details"]["unavailable_steps"][0]["reason_code"] == "agent_unavailable"
    assert set(events[1]["details"]["unavailable_steps"][0]) == {
        "step", "agent_id", "agent_name", "reason_code",
    }


@pytest.mark.asyncio
async def test_all_provider_disabled_keeps_existing_reason_and_wording(registry, monkeypatch):
    flow = _flow("disabled_agent")
    events = await _run(flow, _unavailable(flow, document_id="doc-1"), monkeypatch)
    details = events[1]["details"]
    assert details["reason"] == "provider_disabled"
    assert details["message"].startswith("Flow cannot start: a model provider for these steps is disabled by policy")
    assert "load a document" not in details["message"]


@pytest.mark.asyncio
async def test_unknown_or_missing_code_is_reported_as_agent_unavailable(monkeypatch):
    flow = _flow("mouse_allele")
    events = await _run(flow, [{
        "step": 1, "agent_id": "x", "agent_name": "Step name",
        "reason": "raw reason text", "reason_code": "raw reason text",
    }], monkeypatch)
    assert events[1]["details"]["unavailable_steps"][0]["reason_code"] == "agent_unavailable"
    assert "raw reason text" not in json.dumps(events)
