"""The shipped extraction tier is independent of validation and saved model choices."""
import os
from pathlib import Path

import pytest

from src.lib.config.agent_loader import load_agent_definitions, reset_cache
from src.lib.config.models_loader import get_model, load_models
from src.lib.openai_agents.config import build_model_settings

ROOT = Path(__file__).resolve().parents[5]
EXTRACTORS = {'pdf', 'gene_expression', 'gene_extractor', 'allele_extractor',
              'disease_extractor', 'phenotype_extractor', 'rgd_go_paper_curator'}
FORMATTERS = {'csv_formatter', 'tsv_formatter', 'json_formatter', 'chat_output'}


@pytest.fixture
def package_agents(monkeypatch):
    for key in os.environ:
        if key.startswith('AGENT_') and key.endswith(('_MODEL', '_REASONING')):
            monkeypatch.delenv(key)
    # A deployment's GO validator override must not choose the paper curator model.
    monkeypatch.setenv('AGENT_GO_ANNOTATIONS_MODEL', 'gpt-5.6-terra')
    reset_cache()
    agents = load_agent_definitions(ROOT / 'packages/alliance/agents', force_reload=True)
    yield {agent.folder_name: agent for agent in agents.values()}
    reset_cache()


def test_packaged_extraction_and_output_use_astra_low_but_validators_keep_terra(package_agents):
    for folder in EXTRACTORS | FORMATTERS:
        config = package_agents[folder].model_config
        assert (config.model, config.reasoning) == ('gpt-6-astra', 'low'), folder
        settings = build_model_settings(config.model, temperature=config.temperature,
                                        reasoning_effort=config.reasoning)
        assert settings.temperature is None
        assert settings.reasoning.effort == 'low'
    for folder, agent in package_agents.items():
        if folder not in EXTRACTORS | FORMATTERS:
            assert (agent.model_config.model, agent.model_config.reasoning) == ('gpt-5.6-terra', 'medium'), folder


def test_catalog_exposes_astra_default_and_retains_explicit_sol_settings():
    load_models(force_reload=True)
    astra = get_model('gpt-6-astra')
    sol = get_model('gpt-5.6-sol')
    assert astra.default and astra.curator_visible and astra.default_reasoning == 'low'
    assert not sol.default and sol.default_reasoning == 'medium'
    settings = build_model_settings(sol.model_id, reasoning_effort='medium')
    assert settings.reasoning.effort == 'medium'
