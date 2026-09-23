"""The shipped extraction tier is independent of validation and saved model choices."""
import os
from pathlib import Path

import pytest

from src.lib.config.agent_loader import load_agent_definitions, reset_cache
from src.lib.config.models_loader import get_default_model, get_model, load_models
from src.lib.openai_agents.config import PromptCacheIdentity, build_model_settings

ROOT = Path(__file__).resolve().parents[5]
# ALL-1248: the seven packaged extractors run on Sol/medium; routing and output stay on Astra/low.
EXTRACTORS = {
    'allele_extractor': 'allele_extractor',
    'disease_extractor': 'disease_extractor',
    'gene_extractor': 'gene_extractor',
    'gene_expression': 'gene_expression_extraction',
    'phenotype_extractor': 'phenotype_extractor',
    'pdf': 'pdf_extraction',
    'rgd_go_paper_curator': 'rgd_go_paper_curator',
}
FORMATTERS = {'csv_formatter', 'tsv_formatter', 'json_formatter', 'chat_output'}


def _clear_model_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith('AGENT_') and key.endswith(('_MODEL', '_REASONING')):
            monkeypatch.delenv(key)
    monkeypatch.delenv('SUPERVISOR_MODEL', raising=False)


@pytest.fixture
def package_agents(monkeypatch):
    _clear_model_env(monkeypatch)
    # A deployment's GO validator override must not choose the paper curator model.
    monkeypatch.setenv('AGENT_GO_ANNOTATIONS_MODEL', 'gpt-5.6-terra')
    reset_cache()
    agents = load_agent_definitions(ROOT / 'packages/alliance/agents', force_reload=True)
    yield {agent.folder_name: agent for agent in agents.values()}
    reset_cache()


def _assert_effective(config, model, reasoning, label):
    assert (config.model, config.reasoning) == (model, reasoning), label
    settings = build_model_settings(config.model, temperature=config.temperature,
                                    reasoning_effort=config.reasoning,
                                    prompt_cache=PromptCacheIdentity(label, 'static'))
    assert settings.temperature is None, label
    assert settings.reasoning.effort == reasoning, label


def test_packaged_extractors_use_sol_medium_without_env_override(package_agents):
    for folder, agent_id in EXTRACTORS.items():
        agent = package_agents[folder]
        assert agent.agent_id == agent_id
        _assert_effective(agent.model_config, 'gpt-5.6-sol', 'medium', agent_id)


def test_output_agents_stay_astra_low_and_validators_keep_terra(package_agents):
    for folder in FORMATTERS:
        _assert_effective(package_agents[folder].model_config, 'gpt-6-astra', 'low', folder)
    for folder, agent in package_agents.items():
        if folder not in EXTRACTORS.keys() | FORMATTERS:
            assert (agent.model_config.model, agent.model_config.reasoning) == ('gpt-5.6-terra', 'medium'), folder


def test_supervisor_stays_astra_low(monkeypatch):
    _clear_model_env(monkeypatch)
    reset_cache()
    try:
        agents = load_agent_definitions(ROOT / 'packages/core/agents', force_reload=True)
        supervisor = next(a for a in agents.values() if a.folder_name == 'supervisor')
        _assert_effective(supervisor.model_config, 'gpt-6-astra', 'low', 'supervisor')
    finally:
        reset_cache()


def test_extractor_model_env_override_still_applies(monkeypatch):
    _clear_model_env(monkeypatch)
    monkeypatch.setenv('AGENT_GENE_EXTRACTOR_MODEL', 'gpt-6-astra')
    reset_cache()
    try:
        agents = load_agent_definitions(ROOT / 'packages/alliance/agents', force_reload=True)
        gene = next(a for a in agents.values() if a.folder_name == 'gene_extractor')
        assert (gene.model_config.model, gene.model_config.reasoning) == ('gpt-6-astra', 'medium')
    finally:
        reset_cache()


def test_catalog_defaults_new_agents_to_sol_medium_and_keeps_astra_selectable():
    load_models(force_reload=True)
    astra = get_model('gpt-6-astra')
    sol = get_model('gpt-5.6-sol')
    assert get_default_model().model_id == 'gpt-5.6-sol'
    assert sol.default and sol.curator_visible and sol.default_reasoning == 'medium'
    assert not astra.default and astra.curator_visible and astra.default_reasoning == 'low'
    settings = build_model_settings(sol.model_id, reasoning_effort='medium',
                                    prompt_cache=PromptCacheIdentity('new_agent', 'static'))
    assert settings.reasoning.effort == 'medium'
