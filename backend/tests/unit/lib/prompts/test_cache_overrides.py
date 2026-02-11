"""Unit tests for prompt override behavior in prompt cache/context."""

import uuid
from unittest.mock import MagicMock

import pytest

from src.models.sql.prompts import PromptTemplate
from src.lib.prompts import cache as prompt_cache
from src.lib.prompts.context import (
    PromptOverride,
    clear_prompt_context,
    get_prompt_override,
    set_prompt_override,
)
from src.lib.prompts.service import PromptService


@pytest.fixture(autouse=True)
def _reset_prompt_context():
    clear_prompt_context()
    yield
    clear_prompt_context()


@pytest.fixture
def _mock_cache_ready(monkeypatch):
    monkeypatch.setattr(prompt_cache, "_initialized", True)
    monkeypatch.setattr(prompt_cache, "_active_cache", {})


def test_get_prompt_returns_override_for_matching_system_prompt(_mock_cache_ready, monkeypatch):
    """System prompt lookups should return context override when agent matches."""
    base_prompt = PromptTemplate(
        id=uuid.uuid4(),
        agent_name="gene",
        prompt_type="system",
        group_id=None,
        content="base prompt",
        version=3,
        is_active=True,
    )
    monkeypatch.setattr(prompt_cache, "_active_cache", {"gene:system:base": base_prompt})

    custom_agent_id = str(uuid.uuid4())
    set_prompt_override(
        PromptOverride(
            content="custom override prompt",
            agent_name="gene",
            custom_agent_id=custom_agent_id,
        )
    )

    result = prompt_cache.get_prompt("gene")
    assert result.content == "custom override prompt"
    assert result.source_file == f"custom_agent:{custom_agent_id}"
    assert result.prompt_type == "system"
    assert result.agent_name == "gene"


def test_get_prompt_falls_back_to_cache_for_non_matching_agent(_mock_cache_ready, monkeypatch):
    """Override should not affect other agents."""
    disease_prompt = PromptTemplate(
        id=uuid.uuid4(),
        agent_name="disease",
        prompt_type="system",
        group_id=None,
        content="disease base prompt",
        version=4,
        is_active=True,
    )
    monkeypatch.setattr(prompt_cache, "_active_cache", {"disease:system:base": disease_prompt})

    set_prompt_override(
        PromptOverride(
            content="custom override prompt",
            agent_name="gene",
            custom_agent_id=str(uuid.uuid4()),
        )
    )

    result = prompt_cache.get_prompt("disease")
    assert result.content == "disease base prompt"
    assert result.source_file is None


def test_get_prompt_optional_returns_mod_override_for_group_rules(_mock_cache_ready):
    custom_agent_id = str(uuid.uuid4())
    set_prompt_override(
        PromptOverride(
            content="custom base prompt",
            agent_name="gene",
            custom_agent_id=custom_agent_id,
            mod_overrides={"WB": "custom wb mod prompt"},
        )
    )

    result = prompt_cache.get_prompt_optional("gene", prompt_type="group_rules", mod_id="WB")
    assert result is not None
    assert result.content == "custom wb mod prompt"
    assert result.group_id == "WB"
    assert result.prompt_type == "group_rules"
    assert result.source_file == f"custom_agent:{custom_agent_id}"


def test_get_prompt_optional_accepts_group_id_alias(_mock_cache_ready, monkeypatch):
    prompt = PromptTemplate(
        id=uuid.uuid4(),
        agent_name="gene",
        prompt_type="group_rules",
        group_id="WB",
        content="cached wb mod prompt",
        version=2,
        is_active=True,
    )
    monkeypatch.setattr(prompt_cache, "_active_cache", {"gene:group_rules:WB": prompt})

    result = prompt_cache.get_prompt_optional("gene", prompt_type="group_rules", group_id="WB")
    assert result is not None
    assert result.content == "cached wb mod prompt"


def test_clear_prompt_context_clears_override():
    """clear_prompt_context should clear pending/used prompts and active override."""
    set_prompt_override(
        PromptOverride(
            content="custom override prompt",
            agent_name="gene",
            custom_agent_id=str(uuid.uuid4()),
        )
    )
    assert get_prompt_override() is not None

    clear_prompt_context()
    assert get_prompt_override() is None


def test_prompt_service_logs_custom_agent_id_from_prompt_source():
    """PromptService should parse custom agent UUID from override prompt metadata."""
    custom_agent_id = uuid.uuid4()
    prompt = PromptTemplate(
        id=None,
        agent_name="gene",
        prompt_type="system",
        group_id=None,
        content="custom override prompt",
        version=1,
        is_active=True,
        source_file=f"custom_agent:{custom_agent_id}",
    )

    db = MagicMock()
    service = PromptService(db)
    log_entry = service.log_prompt_usage(prompt=prompt, trace_id="trace-1")

    assert log_entry.prompt_template_id is None
    assert log_entry.custom_agent_id == custom_agent_id
    db.add.assert_called_once()
