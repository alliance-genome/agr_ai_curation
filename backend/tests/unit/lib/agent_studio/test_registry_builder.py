"""
Tests for registry_builder.py - YAML to AGENT_REGISTRY conversion.

Tests the helper functions that build registry entries from YAML configurations.
"""

import pytest

from src.lib.config.agent_loader import ModelConfig
from src.lib.agent_studio.registry_builder import _build_config_defaults


class TestBuildConfigDefaults:
    """Tests for _build_config_defaults function."""

    def test_returns_empty_dict_when_all_values_match_defaults(self):
        """When all values match ModelConfig defaults, return empty dict."""
        # Use default ModelConfig values
        config = ModelConfig()

        result = _build_config_defaults(config)

        assert result == {}

    def test_returns_model_when_differs_from_default(self):
        """When model differs from default, include it in result."""
        config = ModelConfig(model="gpt-4-turbo")

        result = _build_config_defaults(config)

        assert result == {"model": "gpt-4-turbo"}

    def test_returns_temperature_when_differs_from_default(self):
        """When temperature differs from default, include it in result."""
        config = ModelConfig(temperature=0.7)

        result = _build_config_defaults(config)

        assert result == {"temperature": 0.7}

    def test_returns_reasoning_when_differs_from_default(self):
        """When reasoning differs from default, include it in result."""
        config = ModelConfig(reasoning="high")

        result = _build_config_defaults(config)

        assert result == {"reasoning": "high"}

    def test_returns_multiple_non_default_values(self):
        """When multiple values differ, include all of them."""
        config = ModelConfig(
            model="claude-3-opus",
            temperature=0.5,
            reasoning="low",
        )

        result = _build_config_defaults(config)

        assert result == {
            "model": "claude-3-opus",
            "temperature": 0.5,
            "reasoning": "low",
        }

    def test_returns_only_changed_values(self):
        """Only non-default values should be in result."""
        # Change only model, leave temperature and reasoning as defaults
        config = ModelConfig(model="gpt-4o-mini")

        result = _build_config_defaults(config)

        assert "model" in result
        assert "temperature" not in result
        assert "reasoning" not in result

    def test_compares_against_modelconfig_defaults(self):
        """Verify comparison is against ModelConfig dataclass defaults."""
        # This test ensures we're comparing against the actual ModelConfig
        # defaults rather than hardcoded values
        default_config = ModelConfig()

        # Create config with explicit default values (same as ModelConfig defaults)
        config = ModelConfig(
            model=default_config.model,
            temperature=default_config.temperature,
            reasoning=default_config.reasoning,
        )

        result = _build_config_defaults(config)

        # Should be empty since all values match defaults
        assert result == {}
