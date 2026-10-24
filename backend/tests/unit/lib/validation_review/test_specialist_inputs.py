"""Assert the payload/tool surface at the actual validator runner seam."""
import json
from types import SimpleNamespace

import pytest

from src.lib.validation_review.validator_adapter import RECONSIDERATION_INPUT
from src.lib.validation_review.protocol import RECONSIDERATION_PROTOCOL
from src.lib.validation_history.store import HistoryScope
from src.lib.domain_packs.validator_dispatch import (
    run_package_scoped_validator_agent, run_package_scoped_validator_agent_batch,
)
from src.lib.config.agent_loader import AgentDefinition
from tests.unit.lib.domain_packs.test_validator_dispatch import (
    _verbose_validation_request, _compact_lookup_tool, _compact_test_decision,
    _result_payload,
)


@pytest.mark.parametrize("batch", [False, True])
def test_validator_runner_receives_history_and_locked_independence_prompt(monkeypatch, batch):
    from packages.alliance.agents.gene.schema import GeneResultEnvelope
    from src.lib.agent_studio.diagnostic_tools.tool_definitions import _unwrap_function_tool

    request = _verbose_validation_request()
    from agents import Agent
    agent = Agent(name="Saved validator", output_type=GeneResultEnvelope, tools=[_compact_lookup_tool()],
                            instructions="Configured scientific policy", model="test-model")
    monkeypatch.setattr("src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package_id, agent_id: AgentDefinition(folder_name="gene", agent_id=agent_id,
            name="Gene Validation", package_id=package_id, batch_capabilities=["domain_validator_batch"]))
    monkeypatch.setattr("src.lib.agent_studio.catalog_service.get_agent_by_id", lambda _: agent)
    monkeypatch.setattr("src.lib.openai_agents.config.resolve_model_provider", lambda _: "gemini")
    monkeypatch.setattr("src.lib.domain_packs.validator_dispatch.provider_context_preflight", lambda **kw: {})
    context = {"specialist_context": {
        "original_entries": [{"value": "Discussion mention"}],
        "previous_validation": [{"assessed_input": "old input", "answer": "Organism unclear"}],
        "current_selected_entries": {"entry": "Methods clarification"},
        "supervisor_request": {"instructions": "Assess the clarified organism independently"},
        "attempt": {"ordinal": 2, "ceiling": 2},
    }}
    refs = {"records": [{"fact_id": "scoped-context", "sha256": "exact-sha"}],
            "reader": "read_supplied_history"}
    calls = []
    configs = []
    monkeypatch.setattr("src.lib.prompts.assembly.get_all_active_prompts", lambda: {})
    monkeypatch.setattr("src.lib.validation_review.phase_observability.log_agent_config", lambda **kw: configs.append(kw))

    def run(actual_agent, **kwargs):
        payload = json.loads(kwargs["input"])
        calls.append(payload)
        assert payload["validation_reconsideration"] == context
        assert payload["supplied_history"] == refs
        assert RECONSIDERATION_PROTOCOL in actual_agent.instructions
        tools = {tool.name: tool for tool in actual_agent.tools}
        assert "read_supplied_history" in tools
        assert not {"read_section", "read_subsection", "search_document", "read_document"}.intersection(tools)
        name = "finalize_validator_batch_results" if batch else "finalize_validator_result"
        decision = _compact_test_decision(actual_agent, request)
        _unwrap_function_tool(tools[name])(**({"results": [decision]} if batch else {"result": decision}))
        return {"results": [_result_payload(request)]} if batch else {"status": "resolved"}

    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_sync_with_owned_openai_resources", run)
    token = RECONSIDERATION_INPUT.set({"context": context, "history": refs,
        "scope": HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session"), "artifact_id": "artifact",
        "packet_bytes": 2048, "protocol": RECONSIDERATION_PROTOCOL})
    try:
        binding = SimpleNamespace(raw={}, max_tool_calls=None)
        if batch:
            run_package_scoped_validator_agent_batch(
                [SimpleNamespace(request=request, match=SimpleNamespace(binding=binding))], binding=binding)
        else:
            run_package_scoped_validator_agent(request, binding=binding)
    finally:
        RECONSIDERATION_INPUT.reset(token)
    assert len(calls) == 1
    assert configs[-1]["metadata"]["review_phase"] == "validator_reconsideration"
    assert "read_supplied_history" in configs[-1]["tools"]
    assert RECONSIDERATION_PROTOCOL in configs[-1]["instructions"]
