"""Tests for generic agent config function."""
import pytest
import os
from unittest.mock import patch

from src.lib.openai_agents.config import get_agent_config, AgentConfig


class TestGetAgentConfig:
    """Tests for the get_agent_config function."""

    def test_get_agent_config_returns_config(self):
        """get_agent_config should return an AgentConfig instance."""
        config = get_agent_config("gene")
        assert isinstance(config, AgentConfig)
        assert hasattr(config, "model")

    def test_get_agent_config_uses_registry_defaults(self):
        """Config should use defaults from registry if present."""
        # Gene agent has config_defaults in registry
        config = get_agent_config("gene")
        # Default model should come from registry or fallback
        assert config.model is not None

    def test_get_agent_config_respects_env_override(self):
        """Environment variables should override registry defaults."""
        with patch.dict(os.environ, {"AGENT_GENE_MODEL": "gpt-4o-test"}):
            config = get_agent_config("gene")
            assert config.model == "gpt-4o-test"

    def test_get_agent_config_unknown_agent_uses_fallback(self):
        """Unknown agent should get fallback defaults."""
        config = get_agent_config("nonexistent_agent")
        assert config.model is not None  # Should have fallback

    def test_get_agent_config_env_var_pattern(self):
        """Env var pattern should be AGENT_{ID}_SETTING."""
        with patch.dict(os.environ, {
            "AGENT_CUSTOM_MODEL": "custom-model",
            "AGENT_CUSTOM_REASONING": "high",
        }):
            config = get_agent_config("custom")
            assert config.model == "custom-model"
            assert config.reasoning == "high"

    def test_get_agent_config_temperature_override(self):
        """Temperature env var should be parsed as float."""
        with patch.dict(os.environ, {"AGENT_TEST_TEMPERATURE": "0.7"}):
            config = get_agent_config("test")
            assert config.temperature == 0.7

    def test_get_agent_config_tool_choice_override(self):
        """Tool choice env var should be used."""
        with patch.dict(os.environ, {"AGENT_TEST_TOOL_CHOICE": "required"}):
            config = get_agent_config("test")
            assert config.tool_choice == "required"
