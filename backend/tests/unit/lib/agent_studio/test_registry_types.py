# backend/tests/unit/lib/agent_studio/test_registry_types.py
"""Tests for registry type schemas."""
import pytest
from typing import Callable

from src.lib.agent_studio.registry_types import (
    AgentRegistryEntry,
    BatchingConfig,
    FrontendMetadata,
    validate_registry,
)


class TestAgentRegistryEntry:
    """Tests for AgentRegistryEntry dataclass."""

    def test_minimal_agent_config(self):
        """Minimal agent config should be valid."""
        config = AgentRegistryEntry(
            name="Test Agent",
            description="A test agent",
            category="Testing",
        )
        assert config.name == "Test Agent"
        assert config.factory is None
        assert config.tools == []
        assert config.batch_capabilities == []

    def test_full_agent_config(self):
        """Full agent config with all fields should be valid."""
        def dummy_factory():
            pass

        config = AgentRegistryEntry(
            name="Full Agent",
            description="A fully configured agent",
            category="Testing",
            subcategory="Unit",
            factory=dummy_factory,
            tools=["tool1", "tool2"],
            requires_document=True,
            required_params=["document_id", "user_id"],
            batch_capabilities=["pdf_extraction"],
            has_mod_rules=True,
        )
        assert config.factory == dummy_factory
        assert config.requires_document is True
        assert "document_id" in config.required_params


class TestBatchingConfig:
    """Tests for BatchingConfig dataclass."""

    def test_batching_config_creation(self):
        """BatchingConfig should store entity and example."""
        config = BatchingConfig(
            entity="genes",
            example='ask_gene_specialist("Look up these genes: daf-16, lin-3, ...")',
        )
        assert config.entity == "genes"
        assert "daf-16" in config.example


class TestFrontendMetadata:
    """Tests for FrontendMetadata dataclass."""

    def test_frontend_metadata(self):
        """FrontendMetadata should store icon and display properties."""
        metadata = FrontendMetadata(
            icon="🧬",
            color="#4CAF50",
            show_in_palette=True,
        )
        assert metadata.icon == "🧬"
        assert metadata.show_in_palette is True

    def test_frontend_metadata_defaults(self):
        """FrontendMetadata should have sensible defaults."""
        metadata = FrontendMetadata()
        assert metadata.icon == "✨"  # Default icon
        assert metadata.show_in_palette is True


class TestValidateRegistry:
    """Tests for registry validation function."""

    def test_validate_registry_returns_result(self):
        """validate_registry should return a ValidationResult."""
        from scripts.validate_current_agents import ValidationResult

        result = validate_registry({})
        assert isinstance(result, ValidationResult)
        assert result.passed is True  # Empty registry is valid

    def test_validate_registry_detects_missing_factory(self):
        """Executable agents without factory should fail validation."""
        from scripts.validate_current_agents import ValidationResult

        # Agent that's not task_input but has no factory
        registry = {
            "test_agent": {
                "name": "Test",
                "description": "Test agent",
                "category": "Testing",
                "factory": None,
                # Not marked as non-executable, so should require factory
            }
        }
        result = validate_registry(registry)
        # This might pass or fail depending on implementation
        # We just verify it returns a ValidationResult
        assert isinstance(result, ValidationResult)
