"""
Tests for config-driven architecture loaders.

Run with: docker compose -f docker-compose.test.yml run --rm backend-unit-tests \
    python -m pytest tests/unit/test_config_loaders.py -v
"""

from pathlib import Path

import pytest


# Path to alliance_agents (source of truth for Alliance agents)
# In Docker, backend is mounted at /app/backend, so parent is /app
ALLIANCE_AGENTS_PATH = Path(__file__).parent.parent.parent.parent / "alliance_agents"


class TestAgentLoader:
    """Tests for agent_loader module."""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset cache before each test."""
        from src.lib.config.agent_loader import reset_cache
        reset_cache()
        yield
        reset_cache()

    def test_load_agent_definitions(self):
        """Test loading all agent definitions."""
        from src.lib.config.agent_loader import load_agent_definitions

        agents = load_agent_definitions(ALLIANCE_AGENTS_PATH)

        # Should have loaded multiple agents
        assert len(agents) >= 10, f"Expected at least 10 agents, got {len(agents)}"

        # Check some expected agents exist
        expected_agents = ["gene_validation", "allele_validation", "pdf_extraction"]
        for agent_id in expected_agents:
            assert agent_id in agents, f"Expected agent {agent_id} not found"

    def test_gene_agent_structure(self):
        """Test that gene agent has expected structure."""
        from src.lib.config.agent_loader import load_agent_definitions, get_agent_definition

        load_agent_definitions(ALLIANCE_AGENTS_PATH)
        gene = get_agent_definition("gene_validation")

        assert gene is not None, "gene_validation agent not found"

        # Check basic structure
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

    def test_pdf_agent_not_batchable(self):
        """Test that PDF agent is marked as not batchable."""
        from src.lib.config.agent_loader import load_agent_definitions, get_agent_definition

        load_agent_definitions(ALLIANCE_AGENTS_PATH)
        pdf = get_agent_definition("pdf_extraction")

        assert pdf is not None
        assert pdf.supervisor_routing.enabled is True
        assert pdf.supervisor_routing.batchable is False

    def test_get_supervisor_tools(self):
        """Test generating supervisor tool list."""
        from src.lib.config.agent_loader import load_agent_definitions, get_supervisor_tools

        load_agent_definitions(ALLIANCE_AGENTS_PATH)
        tools = get_supervisor_tools()

        assert len(tools) >= 8, f"Expected at least 8 supervisor tools, got {len(tools)}"

        # Check tool structure
        tool_names = [t["tool_name"] for t in tools]
        assert "ask_gene_specialist" in tool_names
        assert "ask_pdf_specialist" in tool_names

        # Check batchable flags
        gene_tool = next(t for t in tools if t["tool_name"] == "ask_gene_specialist")
        assert gene_tool["batchable"] is True
        assert gene_tool["agent_id"] == "gene_validation"

    def test_formatters_not_enabled(self):
        """Test that formatter agents are not supervisor-enabled."""
        from src.lib.config.agent_loader import load_agent_definitions, get_agent_definition

        load_agent_definitions(ALLIANCE_AGENTS_PATH)

        csv_formatter = get_agent_definition("csv_output_formatter")
        if csv_formatter:
            assert csv_formatter.supervisor_routing.enabled is False

    def test_get_agent_by_folder(self):
        """Test getting agent by folder name."""
        from src.lib.config.agent_loader import load_agent_definitions, get_agent_by_folder

        load_agent_definitions(ALLIANCE_AGENTS_PATH)
        gene = get_agent_by_folder("gene")

        assert gene is not None
        assert gene.agent_id == "gene_validation"

    def test_get_agent_by_tool_name(self):
        """Test getting agent by supervisor tool name."""
        from src.lib.config.agent_loader import load_agent_definitions, get_agent_by_tool_name

        load_agent_definitions(ALLIANCE_AGENTS_PATH)
        gene = get_agent_by_tool_name("ask_gene_specialist")

        assert gene is not None
        assert gene.agent_id == "gene_validation"
        assert gene.folder_name == "gene"

    def test_list_agents_enabled_only(self):
        """Test listing only supervisor-enabled agents."""
        from src.lib.config.agent_loader import load_agent_definitions, list_agents

        load_agent_definitions(ALLIANCE_AGENTS_PATH)

        all_agents = list_agents()
        enabled_agents = list_agents(enabled_only=True)

        # Enabled should be subset
        assert len(enabled_agents) <= len(all_agents)
        assert len(enabled_agents) >= 8  # At least 8 enabled agents

        # All enabled agents should have supervisor_routing.enabled=True
        for agent in enabled_agents:
            assert agent.supervisor_routing.enabled is True

    def test_skips_underscore_folders(self):
        """Test that underscore-prefixed folders are skipped."""
        from src.lib.config.agent_loader import load_agent_definitions

        agents = load_agent_definitions(ALLIANCE_AGENTS_PATH)

        # _examples folder should not be loaded
        for agent_id in agents:
            assert not agent_id.startswith("_")
            assert "example" not in agent_id.lower()


class TestSchemaDiscovery:
    """Tests for schema_discovery module."""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset cache before each test."""
        from src.lib.config.schema_discovery import reset_cache
        reset_cache()
        yield
        reset_cache()

    def test_discover_agent_schemas(self):
        """Test discovering all agent schemas."""
        from src.lib.config.schema_discovery import discover_agent_schemas

        schemas = discover_agent_schemas(ALLIANCE_AGENTS_PATH)

        assert len(schemas) >= 5, f"Expected at least 5 schemas, got {len(schemas)}"

        # Check some expected schemas exist
        expected_schemas = ["GeneValidationEnvelope", "AlleleValidationEnvelope"]
        for schema_name in expected_schemas:
            assert schema_name in schemas, f"Expected schema {schema_name} not found"

    def test_gene_validation_envelope(self):
        """Test that GeneValidationEnvelope is discovered and valid."""
        from src.lib.config.schema_discovery import discover_agent_schemas, get_agent_schema
        from pydantic import BaseModel

        discover_agent_schemas(ALLIANCE_AGENTS_PATH)
        GeneEnvelope = get_agent_schema("GeneValidationEnvelope")

        assert GeneEnvelope is not None, "GeneValidationEnvelope not found"
        assert issubclass(GeneEnvelope, BaseModel)

        # Check it has expected fields
        field_names = list(GeneEnvelope.model_fields.keys())
        assert "gene_curies" in field_names, f"Expected 'gene_curies' field, got: {field_names}"

    def test_get_schema_json(self):
        """Test generating JSON schema."""
        from src.lib.config.schema_discovery import discover_agent_schemas, get_schema_json

        discover_agent_schemas(ALLIANCE_AGENTS_PATH)
        json_schema = get_schema_json("GeneValidationEnvelope")

        assert json_schema is not None
        assert "properties" in json_schema
        assert "title" in json_schema

    def test_list_agent_schemas(self):
        """Test listing all schemas with descriptions."""
        from src.lib.config.schema_discovery import discover_agent_schemas, list_agent_schemas

        discover_agent_schemas(ALLIANCE_AGENTS_PATH)
        schema_list = list_agent_schemas()

        assert len(schema_list) >= 5
        assert all(isinstance(desc, str) for desc in schema_list.values())

    def test_get_schema_for_agent(self):
        """Test getting schema by agent folder name."""
        from src.lib.config.schema_discovery import discover_agent_schemas, get_schema_for_agent

        discover_agent_schemas(ALLIANCE_AGENTS_PATH)
        gene_schema = get_schema_for_agent("gene")

        assert gene_schema is not None
        assert gene_schema.__name__ == "GeneValidationEnvelope"

    def test_envelope_class_detection(self):
        """Test that only envelope classes are registered."""
        from src.lib.config.schema_discovery import discover_agent_schemas

        schemas = discover_agent_schemas(ALLIANCE_AGENTS_PATH)

        # All registered schemas should end in "Envelope" or have __envelope_class__
        for name, cls in schemas.items():
            has_marker = getattr(cls, "__envelope_class__", False)
            ends_with_envelope = name.endswith("Envelope")
            assert has_marker or ends_with_envelope, f"Unexpected schema registered: {name}"
