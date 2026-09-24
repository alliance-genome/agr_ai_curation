"""Deployment model settings are checked against the shipped model catalog."""

import pytest

from src.lib.config.model_env_validation import model_env_errors, validate_model_env
from src.lib.config.models_loader import load_models


@pytest.fixture(autouse=True)
def shipped_catalog():
    load_models(force_reload=True)


def test_current_catalog_models_and_levels_pass():
    env = {
        "DEFAULT_AGENT_MODEL": "gpt-6-astra",
        "DEFAULT_AGENT_REASONING": "low",
        "SUPERVISOR_MODEL": "gpt-6-astra",
        "HIERARCHY_LLM_MODEL": "gpt-6-sol",
        "HIERARCHY_LLM_REASONING": "low",
        "FIGURE_LOCATOR_LLM_MODEL": "gpt-6-sol",
        "FIGURE_LOCATOR_LLM_REASONING": "LOW",
        "ABSTRACT_EXTRACTION_MODEL": "gpt-6-astra",
        "ABSTRACT_EXTRACTION_REASONING": "low",
        "AGENT_STUDIO_OPENAI_MODEL": "gpt-6-astra",
        "AGENT_STUDIO_REASONING_EFFORT": "medium",
        "BENCHMARK_ADJUDICATION_MODEL": "gpt-6-sol",
        "AGENT_GENE_MODEL": "gpt-6-sol",
        "AGENT_GENE_REASONING": "medium",
        # A reasoning override without its model override is checked on the agent row.
        "AGENT_SUPERVISOR_REASONING": "low",
        # Unrelated model-like settings are not LLM catalog entries.
        "EMBEDDING_MODEL": "text-embedding-3-small",
    }

    assert model_env_errors(env) == []


@pytest.mark.parametrize(
    ("env", "message"),
    [
        (
            {"HIERARCHY_LLM_MODEL": "gpt-5.6-terra", "HIERARCHY_LLM_REASONING": "low"},
            "HIERARCHY_LLM_MODEL='gpt-5.6-terra' is not a model in the model catalog",
        ),
        (
            {"FIGURE_LOCATOR_LLM_MODEL": "gpt-5.6-terra"},
            "FIGURE_LOCATOR_LLM_MODEL='gpt-5.6-terra' is not a model in the model catalog",
        ),
        (
            {"AGENT_GENE_MODEL": "gpt-5.6-sol"},
            "AGENT_GENE_MODEL='gpt-5.6-sol' is not a model in the model catalog",
        ),
        (
            {"HIERARCHY_LLM_MODEL": "gpt-6-sol", "HIERARCHY_LLM_REASONING": "minimal"},
            "HIERARCHY_LLM_REASONING='minimal' is not a reasoning level of "
            "HIERARCHY_LLM_MODEL model 'gpt-6-sol' (allowed: low, medium, high, xhigh)",
        ),
        (
            {"AGENT_PDF_MODEL": "gpt-6-sol", "AGENT_PDF_REASONING": "disabled"},
            "AGENT_PDF_REASONING='disabled' is not a reasoning level of "
            "AGENT_PDF_MODEL model 'gpt-6-sol' (allowed: low, medium, high, xhigh)",
        ),
        (
            {"AGENT_STUDIO_OPENAI_MODEL": "gpt-6-astra", "AGENT_STUDIO_REASONING_EFFORT": "max"},
            "AGENT_STUDIO_REASONING_EFFORT='max' is not a reasoning level of "
            "AGENT_STUDIO_OPENAI_MODEL model 'gpt-6-astra' (allowed: low, medium, high, xhigh)",
        ),
    ],
)
def test_retired_models_and_unoffered_levels_are_named(env, message):
    assert model_env_errors(env) == [message]


def test_startup_check_fails_loudly_naming_every_bad_setting(monkeypatch):
    monkeypatch.setenv("HIERARCHY_LLM_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("BENCHMARK_ADJUDICATION_MODEL", "gpt-5.6-sol")

    with pytest.raises(RuntimeError) as error:
        validate_model_env()

    assert "HIERARCHY_LLM_MODEL='gpt-5.6-terra'" in str(error.value)
    assert "BENCHMARK_ADJUDICATION_MODEL='gpt-5.6-sol'" in str(error.value)
