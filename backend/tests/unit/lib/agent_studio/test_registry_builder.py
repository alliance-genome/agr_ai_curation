"""
Tests for registry_builder.py - YAML to AGENT_REGISTRY conversion.

Tests the helper functions that build registry entries from YAML configurations.
"""

import shutil
from pathlib import Path

from src.lib.config.agent_loader import ModelConfig, load_agent_definitions
from src.lib.config import agent_loader
from src.lib.agent_studio.registry_builder import (
    AGENT_DOCUMENTATION,
    _build_config_defaults,
    build_agent_registry,
)

from ..packages import find_repo_root

REPO_ROOT = find_repo_root(Path(__file__))


def _flatten_strings(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _flatten_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _flatten_strings(child)
    elif isinstance(value, str):
        yield value


class TestBuildConfigDefaults:
    """Tests for _build_config_defaults function."""

    def test_returns_only_model_when_optional_fields_unset(self):
        """With only model set, reasoning/temperature are omitted (both optional)."""
        config = ModelConfig(model="gpt-5.5")

        result = _build_config_defaults(config)

        assert result == {"model": "gpt-5.5"}

    def test_returns_model_when_differs_from_default(self):
        """When model differs from default, include it in result."""
        config = ModelConfig(model="gpt-4-turbo")

        result = _build_config_defaults(config)

        assert result == {"model": "gpt-4-turbo"}

    def test_returns_temperature_when_set(self):
        """When temperature is set, include it in result."""
        config = ModelConfig(model="gpt-5.5", temperature=0.7)

        result = _build_config_defaults(config)

        assert result == {"model": "gpt-5.5", "temperature": 0.7}

    def test_returns_reasoning_when_set(self):
        """When reasoning is set, include it in result."""
        config = ModelConfig(model="gpt-5.5", reasoning="high")

        result = _build_config_defaults(config)

        assert result == {"model": "gpt-5.5", "reasoning": "high"}

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

    def test_omits_optional_fields_when_none(self):
        """reasoning and temperature are omitted when None (no code default to apply)."""
        config = ModelConfig(model="gpt-5.5", reasoning=None, temperature=None)

        result = _build_config_defaults(config)

        assert result == {"model": "gpt-5.5"}
        assert "reasoning" not in result
        assert "temperature" not in result


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

    def test_agent_without_static_documentation_uses_package_owned_documentation(self):
        """Package-owned agents should not need hardcoded core documentation."""
        configured_agents = load_agent_definitions(force_reload=True)
        registry = build_agent_registry()

        agent_def = configured_agents["data_provider_validation"]
        entry = registry["data_provider_validation"]

        assert "data_provider_validation" not in AGENT_DOCUMENTATION
        assert agent_def.documentation is not None
        assert entry["documentation"] == agent_def.documentation

    def test_pdf_alias_is_not_exposed_in_registry(self):
        """Registry should expose only canonical `pdf_extraction` id."""
        registry = build_agent_registry()

        pdf_extraction_entry = registry.get("pdf_extraction")

        assert registry.get("pdf") is None
        assert pdf_extraction_entry is not None

    def test_gene_expression_exposes_flow_alias_and_package_agent_id(self):
        """Gene-expression flows keep the UI alias and package agent ID equivalent."""
        registry = build_agent_registry()

        flow_alias_entry = registry.get("gene_expression")
        package_agent_entry = registry.get("gene_expression_extraction")

        assert flow_alias_entry is not None
        assert package_agent_entry is not None
        assert flow_alias_entry == package_agent_entry
        assert flow_alias_entry["supervisor"]["tool_name"] == (
            "ask_gene_expression_specialist"
        )
        assert flow_alias_entry["curation"] == {
            "adapter_key": "gene_expression",
            "domain_pack_id": "agr.alliance.gene_expression",
            "launchable": True,
        }

    def test_static_extractor_documentation_keeps_validator_boundary_clear(self):
        """Fallback Agent Studio docs must not say extractors own DB resolution."""
        extractor_ids = {
            "allele_extractor",
            "chemical_extractor",
            "disease_extractor",
            "gene_expression_extraction",
            "gene_extractor",
            "phenotype_extractor",
        }
        forbidden_fragments = {
            "database-assisted normalization",
            "database assisted normalization",
            "database normalization",
            "alliance database normalization",
            "disease ontology normalization",
            "chebi normalization",
            "using agr_curation_query",
            "validates gene symbols found in papers",
            "validates and normalizes",
            "resolves retained",
        }

        for agent_id in extractor_ids:
            documentation = AGENT_DOCUMENTATION[agent_id]
            text = " ".join(_flatten_strings(documentation)).lower()

            assert "validator" in text
            assert "does not perform" in text or "validator-owned" in text
            for fragment in forbidden_fragments:
                assert fragment not in text, (
                    f"{agent_id} static documentation exposes "
                    f"validator-owned lookup wording: {fragment}"
                )

    def test_explicit_system_agent_key_suppresses_folder_alias(self):
        """The ontology resolver exposes only `ontology_term_validation` publicly."""
        registry = build_agent_registry()

        ontology_entry = registry.get("ontology_term_validation")

        assert ontology_entry is not None
        assert registry.get("ontology_term") is None

    def test_build_agent_registry_core_only_runtime_excludes_alliance_agents(
        self,
        monkeypatch,
        tmp_path,
    ):
        """Core-only installs should expose task_input plus core system agents."""
        runtime_packages_dir = tmp_path / "runtime-packages"
        shutil.copytree(REPO_ROOT / "packages" / "core", runtime_packages_dir / "agr.core")

        monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(runtime_packages_dir))
        agent_loader.reset_cache()

        registry = build_agent_registry()

        assert set(registry.keys()) == {
            "task_input",
            "supervisor",
            "chat_output",
            "chat_output_formatter",
            "curation_prep",
        }
        assert registry["chat_output"] == registry["chat_output_formatter"]
        assert "pdf_extraction" not in registry
        assert "gene" not in registry

        agent_loader.reset_cache()
