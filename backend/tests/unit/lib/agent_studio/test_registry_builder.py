"""
Tests for registry_builder.py - YAML to AGENT_REGISTRY conversion.

Tests the helper functions that build registry entries from YAML configurations.
"""

from src.lib.config.agent_loader import ModelConfig, load_agent_definitions
from src.lib.agent_studio.registry_builder import _build_config_defaults, build_agent_registry


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


class TestAgentDocumentationCoverage:
    """Coverage checks for Agent Browser Overview summaries."""

    def test_all_configured_agents_have_non_empty_overview_summary(self):
        """Every configured agent should expose a non-empty documentation summary."""
        configured_agents = load_agent_definitions(force_reload=True)
        registry = build_agent_registry()
        missing_summaries = []

        for agent_id in sorted(configured_agents):
            entry = registry.get(agent_id, {})
            summary = ((entry.get("documentation") or {}).get("summary") or "").strip()
            if not summary:
                missing_summaries.append(agent_id)

        assert not missing_summaries, (
            "Missing Agent Browser Overview summary for configured agents: "
            + ", ".join(missing_summaries)
        )

    def test_pdf_folder_alias_maps_to_pdf_extraction_documentation(self):
        """Legacy `pdf` alias should keep the same docs as `pdf_extraction`."""
        registry = build_agent_registry()

        pdf_entry = registry.get("pdf")
        pdf_extraction_entry = registry.get("pdf_extraction")

        assert pdf_entry is not None
        assert pdf_extraction_entry is not None
        assert (
            (pdf_entry.get("documentation") or {}).get("summary")
            == (pdf_extraction_entry.get("documentation") or {}).get("summary")
        )
