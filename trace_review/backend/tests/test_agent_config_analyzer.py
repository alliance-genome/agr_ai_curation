"""Tests for specialist configuration extraction from Langfuse events."""

from src.analyzers.agent_config import AgentConfigAnalyzer


def test_extract_agent_configs_surfaces_group_tool_exposure():
    exposure = {
        "active_group_ids": ["ZFIN"],
        "base_tool_ids": ["search_document"],
        "added_tool_ids": ["zfin_genotype_context_helper"],
        "denied_tool_ids": ["rgd_annotation_helper"],
    }
    observations = [
        {
            "id": "event-1",
            "type": "EVENT",
            "name": "gene_expression_config",
            "metadata": {
                "agent_config": {
                    "agent_name": "Gene Expression",
                    "instructions": "Extract expression data.",
                    "model": "test-model",
                    "tools": ["search_document", "zfin_genotype_context_helper"],
                    "metadata": {"group_tool_exposure": exposure},
                }
            },
        }
    ]

    result = AgentConfigAnalyzer.extract_agent_configs(observations)

    assert result["agents"][0]["group_tool_exposure"] == exposure
    assert result["agents"][0]["active_group_ids"] == ["ZFIN"]
    assert result["agents"][0]["added_tool_ids"] == ["zfin_genotype_context_helper"]
    assert result["agents"][0]["denied_tool_ids"] == ["rgd_annotation_helper"]
    summary = AgentConfigAnalyzer.summarize_agents(result)[0]
    assert summary["group_tool_exposure"] == exposure
    assert summary["active_group_ids"] == ["ZFIN"]
    assert summary["added_tool_ids"] == ["zfin_genotype_context_helper"]
    assert summary["denied_tool_ids"] == ["rgd_annotation_helper"]


def test_extract_agent_configs_defaults_missing_group_tool_exposure():
    observations = [
        {
            "type": "EVENT",
            "name": "supervisor_config",
            "metadata": {
                "agent_config": {
                    "agent_name": "Supervisor",
                    "model": "test-model",
                }
            },
        }
    ]

    result = AgentConfigAnalyzer.extract_agent_configs(observations)

    assert result["agents"][0]["group_tool_exposure"] == {}
    assert result["agents"][0]["active_group_ids"] == []
    assert result["agents"][0]["added_tool_ids"] == []
    assert result["agents"][0]["denied_tool_ids"] == []


def test_review_phase_configuration_is_distinct_from_routing_supervisor():
    configurations = [
        ("Query Supervisor", "Route to specialists", ["ask_gene_specialist"]),
        ("Query Supervisor Validation Review", "Review exact immutable answers", ["record_validation_review"]),
        ("Direct Chat Supervisor Validation Review", "Review saved direct extraction", ["read_validation_history"]),
    ]
    observations = [{"type": "EVENT", "name": name.lower().replace(" ", "_") + "_config",
        "metadata": {"agent_config": {"agent_name": name, "instructions": instructions,
                                      "model": "same-model", "tools": tools}}}
        for name, instructions, tools in configurations]
    agents = AgentConfigAnalyzer.extract_agent_configs(observations)["agents"]
    assert len(agents) == 3
    assert {agent["agent_name"]: agent["tools"] for agent in agents} == {
        name: tools for name, _, tools in configurations}


def test_followup_and_augmented_validator_configs_preserve_locked_prompt_provenance():
    phases = [('Saved Extractor Targeted Follow-up', 'extractor_followup', ['read_chunk', 'record_evidence', 'read_supplied_history']),
              ('Saved Validator', 'validator_reconsideration', ['lookup', 'read_supplied_history'])]
    observations = [{'type': 'EVENT', 'name': name.lower().replace(' ', '_') + '_config',
        'metadata': {'agent_config': {'agent_name': name, 'instructions': 'Configured policy\n\nLocked history protocol',
            'model': 'saved-model', 'tools': tools, 'metadata': {'review_phase': phase,
                'effective_prompt_hash': 'phase-hash', 'layer_manifest': {'layers': [{'locked': True}]}}}}}
        for name, phase, tools in phases]
    result = AgentConfigAnalyzer.extract_agent_configs(observations)
    assert result['agent_count'] == 2
    assert 'read_supplied_history' in result['tools_available']
    for agent, (name, phase, tools) in zip(result['agents'], phases, strict=True):
        assert agent['agent_name'] == name and agent['tools'] == tools
        assert agent['metadata']['review_phase'] == phase
        assert agent['metadata']['effective_prompt_hash'] == 'phase-hash'
        assert agent['metadata']['layer_manifest']['layers'][0]['locked'] is True
