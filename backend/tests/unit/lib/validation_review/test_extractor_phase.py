"""The follow-up uses the saved extractor's identity and a narrower tool surface."""
import json
from types import SimpleNamespace

import pytest
from agents import Agent, function_tool

from src.lib.validation_history.store import HistoryScope
from src.lib.validation_review import extractor_phase
from src.lib.validation_review.protocol import TARGETED_EXTRACTION_PROTOCOL


@pytest.mark.asyncio
async def test_followup_actual_runner_input_and_tools(phase_trace, monkeypatch):
    @function_tool
    def read_section(section: str) -> str:
        return "Materials and Methods"

    @function_tool
    def finalize_extraction() -> str:
        return "not permitted"

    @function_tool
    def dispatch_other_agent() -> str:
        return "not permitted"

    agent = Agent(name="Saved extractor", model="gpt-test", instructions="Domain extraction policy",
                  tools=[read_section, finalize_extraction, dispatch_other_agent])
    monkeypatch.setattr("src.lib.packages.tool_roles.tool_metadata_by_name", lambda: {
        "read_section": {"paper_source": True},
        "finalize_extraction": {"builder_finalization": True, "builder_run_state": True},
    })
    packet = {"context": {"original_entries": [{"entry_id": "entry", "value": "Discussion mention"}],
        "previous_validation": [{"answer": "Organism unclear", "assessed_input": "exact initial input"}],
        "supervisor_request": {"instructions": "Read Methods for the organism"},
        "attempt": {"entry": {"ordinal": 2, "ceiling": 2, "remaining_after": 0}}},
        "history": {"records": [{"fact_id": "context", "sha256": "exact"}], "reader": "read_supplied_history"}}
    answer = {"entries": [{"entry_id": "entry", "outcome": "no_new_information",
        "assessment": "The paper does not clarify this mention.", "remaining_uncertainty": "Organism unknown",
        "proposed_values": [], "evidence_record_ids": []}]}
    seen = []
    configs = phase_trace.configs

    async def run(phase, input_value, **kwargs):
        seen.append(phase)
        from src.lib.prompts.context import get_used_prompt_runs
        assert get_used_prompt_runs()[-1].agent_name == phase.name
        assert phase_trace.events[-1]['trace_context'] == {'trace_id': 'phase-trace', 'parent_span_id': 'phase-parent'}
        assert configs[-1]['instructions'] == phase.instructions
        assert configs[-1]['tools'] == [tool.name for tool in phase.tools]
        assert json.loads(input_value) == packet
        assert kwargs["max_turns"] == 17
        assert phase.model == agent.model
        assert phase.handoffs == []
        assert phase.tool_surface.mode == "eager_policy"
        assert {tool.name for tool in phase.tools} == {"read_section", "read_supplied_history"}
        assert TARGETED_EXTRACTION_PROTOCOL in phase.instructions
        assert "Domain extraction policy" in phase.instructions
        return SimpleNamespace(final_output=answer)

    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_with_owned_openai_resources", run)
    result = await extractor_phase.invoke(agent,
        scope=HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session"),
        artifact_id="artifact", packet=packet, packet_bytes=2048, max_turns=17, evidence_records=[])
    assert result.model_dump(mode="json") == answer
    assert len(seen) == 1
    assert configs[0]['agent_name'] == seen[0].name
    assert configs[0]['instructions'] == seen[0].instructions
    assert configs[0]['tools'] == [tool.name for tool in seen[0].tools]
    assert configs[0]['metadata']['review_phase'] == 'extractor_followup'
    assert configs[0]['metadata']['effective_prompt_hash']
    assert len(agent.tools) == 3
    assert agent.instructions == "Domain extraction policy"


def test_resumed_extractor_reconstructs_original_context_and_reauthorizes(monkeypatch):
    from src.lib.validation_review.extractor_adapter import ExtractorAdapter
    from src.lib.validation_review.decisions import ReviewContext
    context = ReviewContext(HistoryScope('owner', '00000000-0000-0000-0000-000000000001', 'session'), 'episode', 'supervisor')
    original = Agent(name='Saved extractor', model='gpt-test', instructions='Domain policy plus original paper context')
    original.review_reconstruction_context = extractor_phase.reconstruction_context({
        'document_name': 'paper.pdf', 'sections': [{'title': 'Methods'}],
        'additional_runtime_context': ['Configured extraction instructions'],
        'model_reasoning_override': 'high', 'user_id': 'must-not-restore', 'api_key': 'must-not-save',
    })
    pinned = extractor_phase.contract(original, max_turns=17)
    receipt = {'agent_key': 'saved', 'execution_receipt': None, 'contract': pinned}
    monkeypatch.setattr('src.lib.validation_history.store.read_baseline', lambda *a: {'provenance': {
        'agent_key': 'saved', 'execution_receipt': None, 'extractor_contract': pinned}})
    monkeypatch.setattr('src.lib.agent_studio.custom_profile_validators.runtime_validator_user_id', lambda _: 7)
    calls = []
    def build(key, **kwargs):
        calls.append((key, kwargs))
        assert kwargs['user_id'] == 'owner' and kwargs['db_user_id'] == 7
        assert kwargs['authenticated_groups'] == ['WB']
        assert kwargs['document_name'] == 'paper.pdf'
        assert kwargs['sections'] == [{'title': 'Methods'}]
        assert kwargs['additional_runtime_context'] == ['Configured extraction instructions']
        assert 'api_key' not in kwargs
        rebuilt = original.clone()
        rebuilt.review_reconstruction_context = extractor_phase.reconstruction_context(kwargs)
        return rebuilt
    monkeypatch.setattr('src.lib.agent_studio.catalog_service.get_agent_by_id', build)
    adapter = ExtractorAdapter(context, 'artifact', authenticated_groups=['WB'], checkpoint_provider=lambda: None)
    rebuilt = adapter.resolve_agent(receipt)
    assert extractor_phase.contract(rebuilt, max_turns=17) == pinned
    assert len(calls) == 1
