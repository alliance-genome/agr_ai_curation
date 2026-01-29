"""
Tests for config-driven architecture loaders.

Run with: python -m pytest backend/tests/test_config_loaders.py -v
"""

import sys
from pathlib import Path

# Add backend to path for imports
backend_path = Path(__file__).parent.parent
sys.path.insert(0, str(backend_path))

import pytest


class TestAgentLoader:
    """Tests for agent_loader module."""

    def setup_method(self):
        """Reset cache before each test."""
        from src.lib.config.agent_loader import reset_cache
        reset_cache()

    def test_load_agent_definitions(self):
        """Test loading all agent definitions."""
        from src.lib.config.agent_loader import load_agent_definitions

        agents_path = backend_path.parent / "config" / "agents"
        agents = load_agent_definitions(agents_path)

        # Should have loaded multiple agents
        assert len(agents) > 0
        print(f"\nLoaded {len(agents)} agents:")
        for agent_id in sorted(agents.keys()):
            print(f"  - {agent_id}")

    def test_gene_agent_structure(self):
        """Test that gene agent has expected structure."""
        from src.lib.config.agent_loader import load_agent_definitions, get_agent_definition

        agents_path = backend_path.parent / "config" / "agents"
        load_agent_definitions(agents_path)

        gene = get_agent_definition("gene_validation")
        assert gene is not None, "gene_validation agent not found"

        # Check structure
        assert gene.folder_name == "gene"
        assert gene.agent_id == "gene_validation"
        assert gene.name == "Gene Validation Agent"
        assert gene.tool_name == "ask_gene_specialist"

        # Check supervisor routing
        assert gene.supervisor_routing.enabled is True
        assert gene.supervisor_routing.batchable is True
        assert "genes" in gene.supervisor_routing.batching_instructions.lower()

        # Check tools
        assert "agr_curation_query" in gene.tools

        # Check output schema
        assert gene.output_schema == "GeneValidationEnvelope"

        print(f"\nGene agent loaded successfully:")
        print(f"  - tool_name: {gene.tool_name}")
        print(f"  - batchable: {gene.supervisor_routing.batchable}")
        print(f"  - tools: {gene.tools}")
        print(f"  - output_schema: {gene.output_schema}")

    def test_get_supervisor_tools(self):
        """Test generating supervisor tool list."""
        from src.lib.config.agent_loader import load_agent_definitions, get_supervisor_tools

        agents_path = backend_path.parent / "config" / "agents"
        load_agent_definitions(agents_path)

        tools = get_supervisor_tools()
        assert len(tools) > 0

        print(f"\nSupervisor tools ({len(tools)}):")
        for tool in tools:
            batchable = "✓" if tool["batchable"] else "✗"
            print(f"  - {tool['tool_name']} [{batchable}]")

    def test_formatters_not_enabled(self):
        """Test that formatter agents are not supervisor-enabled."""
        from src.lib.config.agent_loader import load_agent_definitions, get_agent_definition

        agents_path = backend_path.parent / "config" / "agents"
        load_agent_definitions(agents_path)

        csv_formatter = get_agent_definition("csv_output_formatter")
        if csv_formatter:
            assert csv_formatter.supervisor_routing.enabled is False


class TestSchemaDiscovery:
    """Tests for schema_discovery module."""

    def setup_method(self):
        """Reset cache before each test."""
        from src.lib.config.schema_discovery import reset_cache
        reset_cache()

    def test_discover_agent_schemas(self):
        """Test discovering all agent schemas."""
        from src.lib.config.schema_discovery import discover_agent_schemas

        agents_path = backend_path.parent / "config" / "agents"
        schemas = discover_agent_schemas(agents_path)

        assert len(schemas) > 0
        print(f"\nDiscovered {len(schemas)} schema classes:")
        for class_name in sorted(schemas.keys()):
            print(f"  - {class_name}")

    def test_gene_validation_envelope(self):
        """Test that GeneValidationEnvelope is discovered."""
        from src.lib.config.schema_discovery import discover_agent_schemas, get_agent_schema

        agents_path = backend_path.parent / "config" / "agents"
        discover_agent_schemas(agents_path)

        GeneEnvelope = get_agent_schema("GeneValidationEnvelope")
        assert GeneEnvelope is not None, "GeneValidationEnvelope not found"

        # Check it's a Pydantic model
        from pydantic import BaseModel
        assert issubclass(GeneEnvelope, BaseModel)

        print(f"\nGeneValidationEnvelope fields:")
        for field_name, field_info in GeneEnvelope.model_fields.items():
            print(f"  - {field_name}: {field_info.annotation}")

    def test_get_schema_json(self):
        """Test generating JSON schema."""
        from src.lib.config.schema_discovery import discover_agent_schemas, get_schema_json

        agents_path = backend_path.parent / "config" / "agents"
        discover_agent_schemas(agents_path)

        json_schema = get_schema_json("GeneValidationEnvelope")
        assert json_schema is not None
        assert "properties" in json_schema

        print(f"\nGeneValidationEnvelope JSON schema keys:")
        print(f"  - title: {json_schema.get('title')}")
        print(f"  - properties: {list(json_schema.get('properties', {}).keys())}")


if __name__ == "__main__":
    # Run a quick test
    print("=" * 60)
    print("Testing Config-Driven Architecture Loaders")
    print("=" * 60)

    # Test agent loader
    print("\n--- Agent Loader Tests ---")
    test_agents = TestAgentLoader()
    test_agents.setup_method()
    test_agents.test_load_agent_definitions()
    test_agents.setup_method()
    test_agents.test_gene_agent_structure()
    test_agents.setup_method()
    test_agents.test_get_supervisor_tools()

    # Test schema discovery
    print("\n--- Schema Discovery Tests ---")
    test_schemas = TestSchemaDiscovery()
    test_schemas.setup_method()
    test_schemas.test_discover_agent_schemas()
    test_schemas.setup_method()
    test_schemas.test_gene_validation_envelope()
    test_schemas.setup_method()
    test_schemas.test_get_schema_json()

    print("\n" + "=" * 60)
    print("All tests passed!")
    print("=" * 60)
