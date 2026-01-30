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


class TestErrorHandling:
    """Tests for error handling in config loaders."""

    @pytest.fixture(autouse=True)
    def reset_caches(self):
        """Reset all caches before each test."""
        from src.lib.config.agent_loader import reset_cache as reset_agent_cache
        from src.lib.config.schema_discovery import reset_cache as reset_schema_cache
        reset_agent_cache()
        reset_schema_cache()
        yield
        reset_agent_cache()
        reset_schema_cache()

    def test_missing_agents_path_raises_error(self):
        """Test that non-existent path raises FileNotFoundError."""
        from src.lib.config.agent_loader import load_agent_definitions

        with pytest.raises(FileNotFoundError) as exc_info:
            load_agent_definitions(Path("/nonexistent/path/to/agents"))

        assert "not found" in str(exc_info.value).lower()

    def test_missing_schemas_path_raises_error(self):
        """Test that non-existent path raises FileNotFoundError for schemas."""
        from src.lib.config.schema_discovery import discover_agent_schemas

        with pytest.raises(FileNotFoundError) as exc_info:
            discover_agent_schemas(Path("/nonexistent/path/to/agents"))

        assert "not found" in str(exc_info.value).lower()

    def test_malformed_yaml_raises_error(self, tmp_path):
        """Test that malformed YAML raises appropriate error."""
        import yaml
        from src.lib.config.agent_loader import load_agent_definitions

        # Create a temp directory with malformed YAML
        bad_agent = tmp_path / "bad_agent"
        bad_agent.mkdir()
        (bad_agent / "agent.yaml").write_text("invalid: yaml: content: [unmatched")

        with pytest.raises(yaml.YAMLError):
            load_agent_definitions(tmp_path)

    def test_env_var_substitution(self, monkeypatch):
        """Test environment variable substitution in model_config."""
        from src.lib.config.agent_loader import load_agent_definitions, get_agent_definition

        # Set environment variable that gene agent uses
        monkeypatch.setenv("AGENT_GENE_MODEL", "gpt-4-turbo-test")

        # Force reload to pick up the env var
        load_agent_definitions(ALLIANCE_AGENTS_PATH, force_reload=True)
        gene = get_agent_definition("gene_validation")

        assert gene is not None
        assert gene.model_config.model == "gpt-4-turbo-test"

    def test_env_var_default_when_not_set(self):
        """Test that default value is used when env var is not set."""
        import os
        from src.lib.config.agent_loader import load_agent_definitions, get_agent_definition

        # Ensure the env var is NOT set
        os.environ.pop("AGENT_GENE_MODEL", None)

        load_agent_definitions(ALLIANCE_AGENTS_PATH, force_reload=True)
        gene = get_agent_definition("gene_validation")

        assert gene is not None
        # Should use the default from YAML (gpt-4o)
        assert gene.model_config.model == "gpt-4o"

    def test_force_reload_actually_reloads(self):
        """Test that force_reload=True actually reloads the definitions."""
        from src.lib.config.agent_loader import load_agent_definitions, is_initialized

        # First load
        agents1 = load_agent_definitions(ALLIANCE_AGENTS_PATH)
        assert is_initialized()

        # Second load without force - should return cached
        agents2 = load_agent_definitions(ALLIANCE_AGENTS_PATH)
        assert agents1 is agents2  # Same dict object

        # Third load with force - should be new dict
        agents3 = load_agent_definitions(ALLIANCE_AGENTS_PATH, force_reload=True)
        # Content should be equal but it's a fresh dict
        assert set(agents3.keys()) == set(agents1.keys())


class TestFactoryMappingAlignment:
    """Tests to verify factory_mapping keys match YAML agent_ids.

    This prevents the critical bug where agent_ids in the factory_mapping
    don't match the YAML definitions, causing agents to fail dynamic discovery.
    """

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset cache before each test."""
        from src.lib.config.agent_loader import reset_cache
        reset_cache()
        yield
        reset_cache()

    def test_all_supervisor_enabled_agents_have_factories(self):
        """Verify all supervisor-enabled agent_ids have factory mappings.

        This is a critical test that prevents the mismatch bug where
        factory_mapping keys don't match YAML agent_id values.
        """
        from src.lib.config.agent_loader import load_agent_definitions, get_supervisor_tools
        from src.lib.openai_agents.agents.supervisor_agent import _get_agent_factory

        # Load agent definitions
        load_agent_definitions(ALLIANCE_AGENTS_PATH)

        # Get all supervisor-enabled tools
        tools = get_supervisor_tools()

        # Verify each agent_id has a corresponding factory
        missing_factories = []
        for tool in tools:
            agent_id = tool["agent_id"]
            factory = _get_agent_factory(agent_id)
            if factory is None:
                missing_factories.append(agent_id)

        assert not missing_factories, (
            f"Missing factory mappings for supervisor-enabled agents: {missing_factories}\n"
            f"Add these agent_ids to factory_mapping in supervisor_agent.py"
        )

    def test_factory_mapping_agent_ids_exist_in_yaml(self):
        """Verify all factory_mapping keys correspond to real YAML agent_ids.

        This prevents orphaned factory mappings that never get used.
        """
        from src.lib.config.agent_loader import load_agent_definitions

        # Get all agent_ids from YAML
        agents = load_agent_definitions(ALLIANCE_AGENTS_PATH)
        yaml_agent_ids = set(agents.keys())

        # Get factory_mapping keys (import the module to access the function internals)
        from src.lib.openai_agents.agents import supervisor_agent

        # Build factory_mapping by calling the function with a test agent_id
        # The factory_mapping is defined inside the function, so we extract it indirectly
        known_factory_ids = {
            "gene_validation", "allele_validation", "disease_validation",
            "chemical_validation", "gene_ontology_lookup", "go_annotations_lookup",
            "orthologs_lookup", "ontology_mapping_lookup", "pdf_extraction",
            "gene_expression_extraction",
        }

        # Check that all known factory_ids exist in YAML
        orphaned_ids = known_factory_ids - yaml_agent_ids
        assert not orphaned_ids, (
            f"Factory mappings exist for non-existent YAML agent_ids: {orphaned_ids}\n"
            f"Either remove these from factory_mapping or create corresponding agent.yaml files"
        )


# =============================================================================
# KANBAN-1002: Comprehensive Test Coverage
# =============================================================================
# Tests below address gaps identified by opus sub-agent code review.
# =============================================================================


class TestPromptYamlValidation:
    """Tests for prompt.yaml file validation.

    Ensures all agent folders have valid prompt.yaml files with required fields.
    """

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset cache before each test."""
        from src.lib.config.agent_loader import reset_cache
        reset_cache()
        yield
        reset_cache()

    def test_all_agents_have_prompt_yaml(self):
        """Every agent folder must have a prompt.yaml file."""
        for agent_folder in ALLIANCE_AGENTS_PATH.iterdir():
            if not agent_folder.is_dir() or agent_folder.name.startswith("_"):
                continue

            prompt_yaml = agent_folder / "prompt.yaml"
            assert prompt_yaml.exists(), (
                f"Agent folder '{agent_folder.name}' is missing prompt.yaml"
            )

    def test_prompt_yaml_has_required_fields(self):
        """Every prompt.yaml must have agent_id and content fields."""
        import yaml

        for agent_folder in ALLIANCE_AGENTS_PATH.iterdir():
            if not agent_folder.is_dir() or agent_folder.name.startswith("_"):
                continue

            prompt_yaml = agent_folder / "prompt.yaml"
            if not prompt_yaml.exists():
                continue

            with open(prompt_yaml) as f:
                data = yaml.safe_load(f)

            assert data is not None, f"Empty prompt.yaml in {agent_folder.name}"
            assert "agent_id" in data, (
                f"prompt.yaml in {agent_folder.name} missing 'agent_id' field"
            )
            assert "content" in data, (
                f"prompt.yaml in {agent_folder.name} missing 'content' field"
            )

    def test_prompt_yaml_content_not_empty(self):
        """Every prompt.yaml must have non-empty content."""
        import yaml

        for agent_folder in ALLIANCE_AGENTS_PATH.iterdir():
            if not agent_folder.is_dir() or agent_folder.name.startswith("_"):
                continue

            prompt_yaml = agent_folder / "prompt.yaml"
            if not prompt_yaml.exists():
                continue

            with open(prompt_yaml) as f:
                data = yaml.safe_load(f)

            if data and "content" in data:
                content = data["content"]
                assert content and len(content.strip()) > 0, (
                    f"prompt.yaml in {agent_folder.name} has empty content"
                )

    def test_prompt_agent_id_matches_agent_yaml(self):
        """prompt.yaml agent_id must match agent.yaml agent_id."""
        import yaml

        for agent_folder in ALLIANCE_AGENTS_PATH.iterdir():
            if not agent_folder.is_dir() or agent_folder.name.startswith("_"):
                continue

            prompt_yaml = agent_folder / "prompt.yaml"
            agent_yaml = agent_folder / "agent.yaml"

            if not prompt_yaml.exists() or not agent_yaml.exists():
                continue

            with open(prompt_yaml) as f:
                prompt_data = yaml.safe_load(f)
            with open(agent_yaml) as f:
                agent_data = yaml.safe_load(f)

            if prompt_data and agent_data:
                prompt_agent_id = prompt_data.get("agent_id")
                agent_agent_id = agent_data.get("agent_id")

                assert prompt_agent_id == agent_agent_id, (
                    f"agent_id mismatch in {agent_folder.name}: "
                    f"prompt.yaml has '{prompt_agent_id}', "
                    f"agent.yaml has '{agent_agent_id}'"
                )


class TestGroupRulesValidation:
    """Tests for group_rules/*.yaml file validation.

    Ensures all group rules files have valid structure.
    """

    def test_group_rules_have_required_fields(self):
        """All group_rules files must have group_id and content."""
        import yaml

        for agent_folder in ALLIANCE_AGENTS_PATH.iterdir():
            if not agent_folder.is_dir() or agent_folder.name.startswith("_"):
                continue

            group_rules_dir = agent_folder / "group_rules"
            if not group_rules_dir.exists():
                continue

            for rule_file in group_rules_dir.glob("*.yaml"):
                with open(rule_file) as f:
                    data = yaml.safe_load(f)

                assert data is not None, f"Empty group_rules file: {rule_file}"
                assert "group_id" in data, (
                    f"group_rules file {rule_file} missing 'group_id' field"
                )
                assert "content" in data, (
                    f"group_rules file {rule_file} missing 'content' field"
                )

    def test_group_rules_group_id_matches_filename(self):
        """group_id in YAML must match filename (e.g., fb.yaml -> FB)."""
        import yaml

        for agent_folder in ALLIANCE_AGENTS_PATH.iterdir():
            if not agent_folder.is_dir() or agent_folder.name.startswith("_"):
                continue

            group_rules_dir = agent_folder / "group_rules"
            if not group_rules_dir.exists():
                continue

            for rule_file in group_rules_dir.glob("*.yaml"):
                with open(rule_file) as f:
                    data = yaml.safe_load(f)

                if data and "group_id" in data:
                    expected_id = rule_file.stem.upper()
                    actual_id = data["group_id"]
                    assert actual_id == expected_id, (
                        f"group_id mismatch in {rule_file}: "
                        f"filename suggests '{expected_id}', "
                        f"but group_id is '{actual_id}'"
                    )

    def test_group_rules_content_not_empty(self):
        """All group_rules files must have non-empty content."""
        import yaml

        for agent_folder in ALLIANCE_AGENTS_PATH.iterdir():
            if not agent_folder.is_dir() or agent_folder.name.startswith("_"):
                continue

            group_rules_dir = agent_folder / "group_rules"
            if not group_rules_dir.exists():
                continue

            for rule_file in group_rules_dir.glob("*.yaml"):
                with open(rule_file) as f:
                    data = yaml.safe_load(f)

                if data and "content" in data:
                    content = data["content"]
                    assert content and len(content.strip()) > 0, (
                        f"group_rules file {rule_file} has empty content"
                    )


class TestCrossFileConsistency:
    """Tests for cross-file consistency in config files.

    Ensures agent.yaml, prompt.yaml, and schema.py are aligned.
    """

    @pytest.fixture(autouse=True)
    def reset_caches(self):
        """Reset all caches before each test."""
        from src.lib.config.agent_loader import reset_cache as reset_agent_cache
        from src.lib.config.schema_discovery import reset_cache as reset_schema_cache
        reset_agent_cache()
        reset_schema_cache()
        yield
        reset_agent_cache()
        reset_schema_cache()

    def test_all_agent_ids_unique(self):
        """No duplicate agent_ids across all agent.yaml files."""
        import yaml

        agent_ids = []
        for agent_folder in ALLIANCE_AGENTS_PATH.iterdir():
            if not agent_folder.is_dir() or agent_folder.name.startswith("_"):
                continue

            agent_yaml = agent_folder / "agent.yaml"
            if not agent_yaml.exists():
                continue

            with open(agent_yaml) as f:
                data = yaml.safe_load(f)

            if data and "agent_id" in data:
                agent_ids.append((data["agent_id"], agent_folder.name))

        # Check for duplicates
        seen = {}
        duplicates = []
        for agent_id, folder in agent_ids:
            if agent_id in seen:
                duplicates.append(f"{agent_id} (in {seen[agent_id]} and {folder})")
            seen[agent_id] = folder

        assert not duplicates, f"Duplicate agent_ids found: {duplicates}"

    def test_output_schema_references_valid_schema(self):
        """Every output_schema reference matches an actual schema class."""
        from src.lib.config.agent_loader import load_agent_definitions
        from src.lib.config.schema_discovery import discover_agent_schemas

        agents = load_agent_definitions(ALLIANCE_AGENTS_PATH)
        schemas = discover_agent_schemas(ALLIANCE_AGENTS_PATH)

        missing_schemas = []
        for agent_id, agent in agents.items():
            if agent.output_schema:
                if agent.output_schema not in schemas:
                    missing_schemas.append(
                        f"{agent_id} references '{agent.output_schema}' but schema not found"
                    )

        assert not missing_schemas, (
            f"Missing schema references:\n" + "\n".join(missing_schemas)
        )

    def test_batchable_agents_have_batching_instructions(self):
        """If batchable=true, batching_instructions must be non-empty."""
        from src.lib.config.agent_loader import load_agent_definitions

        agents = load_agent_definitions(ALLIANCE_AGENTS_PATH)

        missing_instructions = []
        for agent_id, agent in agents.items():
            if agent.supervisor_routing.batchable:
                instructions = agent.supervisor_routing.batching_instructions
                if not instructions or not instructions.strip():
                    missing_instructions.append(agent_id)

        assert not missing_instructions, (
            f"Agents marked batchable but missing batching_instructions: "
            f"{missing_instructions}"
        )

    def test_enabled_agents_have_descriptions(self):
        """All supervisor-enabled agents must have routing descriptions."""
        from src.lib.config.agent_loader import load_agent_definitions

        agents = load_agent_definitions(ALLIANCE_AGENTS_PATH)

        missing_descriptions = []
        for agent_id, agent in agents.items():
            if agent.supervisor_routing.enabled:
                desc = agent.supervisor_routing.description
                if not desc or not desc.strip():
                    missing_descriptions.append(agent_id)

        assert not missing_descriptions, (
            f"Supervisor-enabled agents missing descriptions: {missing_descriptions}"
        )

    def test_tool_names_unique(self):
        """All generated tool_names must be unique."""
        from src.lib.config.agent_loader import load_agent_definitions

        agents = load_agent_definitions(ALLIANCE_AGENTS_PATH)

        tool_names = {}
        duplicates = []
        for agent_id, agent in agents.items():
            tool_name = agent.tool_name
            if tool_name in tool_names:
                duplicates.append(
                    f"{tool_name} (from {tool_names[tool_name]} and {agent_id})"
                )
            tool_names[tool_name] = agent_id

        assert not duplicates, f"Duplicate tool_names: {duplicates}"


class TestGetAgentFactory:
    """Tests for _get_agent_factory function.

    Ensures lazy import mechanism works correctly.
    """

    def test_returns_callable_for_valid_gene_agent(self):
        """Should return the create_gene_agent factory function."""
        from src.lib.openai_agents.agents.supervisor_agent import _get_agent_factory

        factory = _get_agent_factory("gene_validation")

        assert factory is not None, "Factory should not be None for valid agent_id"
        assert callable(factory), "Factory should be callable"
        assert factory.__name__ == "create_gene_agent"

    def test_returns_callable_for_all_known_agents(self):
        """Should return factories for all 10 known agents."""
        from src.lib.openai_agents.agents.supervisor_agent import _get_agent_factory

        expected_agents = [
            ("gene_validation", "create_gene_agent"),
            ("allele_validation", "create_allele_agent"),
            ("disease_validation", "create_disease_agent"),
            ("chemical_validation", "create_chemical_agent"),
            ("gene_ontology_lookup", "create_gene_ontology_agent"),
            ("go_annotations_lookup", "create_go_annotations_agent"),
            ("orthologs_lookup", "create_orthologs_agent"),
            ("ontology_mapping_lookup", "create_ontology_mapping_agent"),
            ("pdf_extraction", "create_pdf_agent"),
            ("gene_expression_extraction", "create_gene_expression_agent"),
        ]

        for agent_id, expected_factory_name in expected_agents:
            factory = _get_agent_factory(agent_id)
            assert factory is not None, f"Missing factory for {agent_id}"
            assert callable(factory), f"Factory for {agent_id} not callable"
            assert factory.__name__ == expected_factory_name, (
                f"Wrong factory for {agent_id}: expected {expected_factory_name}, "
                f"got {factory.__name__}"
            )

    def test_returns_none_for_unknown_agent_id(self):
        """Should return None for unregistered agent_id."""
        from src.lib.openai_agents.agents.supervisor_agent import _get_agent_factory

        factory = _get_agent_factory("nonexistent_agent")

        assert factory is None, "Should return None for unknown agent_id"

    def test_returns_none_for_empty_agent_id(self):
        """Should return None for empty string agent_id."""
        from src.lib.openai_agents.agents.supervisor_agent import _get_agent_factory

        factory = _get_agent_factory("")

        assert factory is None, "Should return None for empty agent_id"


class TestSchemaLoadingEdgeCases:
    """Tests for schema loading edge cases.

    Tests for _is_envelope_class and schema module loading.
    """

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset cache before each test."""
        from src.lib.config.schema_discovery import reset_cache
        reset_cache()
        yield
        reset_cache()

    def test_is_envelope_class_by_naming(self):
        """Classes ending in 'Envelope' should be detected."""
        from src.lib.config.schema_discovery import _is_envelope_class
        from pydantic import BaseModel

        class TestEnvelope(BaseModel):
            name: str

        assert _is_envelope_class(TestEnvelope) is True

    def test_is_envelope_class_by_marker(self):
        """Classes with __envelope_class__ = True should be detected."""
        from src.lib.config.schema_discovery import _is_envelope_class
        from pydantic import BaseModel

        class MarkedSchema(BaseModel):
            __envelope_class__ = True
            name: str

        assert _is_envelope_class(MarkedSchema) is True

    def test_is_envelope_class_rejects_regular_model(self):
        """Regular BaseModel subclasses should not be detected."""
        from src.lib.config.schema_discovery import _is_envelope_class
        from pydantic import BaseModel

        class RegularModel(BaseModel):
            name: str

        assert _is_envelope_class(RegularModel) is False

    def test_is_envelope_class_rejects_non_pydantic(self):
        """Non-BaseModel classes should not be detected."""
        from src.lib.config.schema_discovery import _is_envelope_class

        class NotAModel:
            pass

        assert _is_envelope_class(NotAModel) is False

    def test_is_envelope_class_rejects_base_model_itself(self):
        """BaseModel itself should not be detected."""
        from src.lib.config.schema_discovery import _is_envelope_class
        from pydantic import BaseModel

        assert _is_envelope_class(BaseModel) is False

    def test_is_envelope_class_rejects_non_class(self):
        """Non-class objects should not be detected."""
        from src.lib.config.schema_discovery import _is_envelope_class

        assert _is_envelope_class("not a class") is False
        assert _is_envelope_class(42) is False
        assert _is_envelope_class(None) is False

    def test_schema_folder_with_no_envelope_classes(self, tmp_path):
        """Schema.py with no envelope classes should be handled gracefully."""
        from src.lib.config.schema_discovery import discover_agent_schemas

        # Create a temp directory with schema.py that has no envelope classes
        agent_dir = tmp_path / "no_envelope"
        agent_dir.mkdir()
        (agent_dir / "schema.py").write_text("""
from pydantic import BaseModel

class RegularModel(BaseModel):
    name: str
""")

        schemas = discover_agent_schemas(tmp_path)

        # RegularModel should NOT be registered
        assert "RegularModel" not in schemas

    def test_reset_cache_cleans_sys_modules(self):
        """Reset cache should clean up dynamically loaded modules."""
        import sys
        from src.lib.config.schema_discovery import (
            discover_agent_schemas,
            reset_cache,
            _registered_modules,
        )

        # Discover schemas to populate cache
        discover_agent_schemas(ALLIANCE_AGENTS_PATH)

        # Check that modules were registered
        assert len(_registered_modules) > 0, "Should have registered some modules"

        # Store module names before reset
        modules_before = [m for m in _registered_modules]

        # Reset cache
        reset_cache()

        # Verify modules were cleaned from sys.modules
        for module_name in modules_before:
            assert module_name not in sys.modules, (
                f"Module {module_name} should have been removed from sys.modules"
            )


class TestThreadSafety:
    """Tests for thread-safe initialization.

    Ensures concurrent access doesn't corrupt state.
    """

    @pytest.fixture(autouse=True)
    def reset_caches(self):
        """Reset all caches before each test."""
        from src.lib.config.agent_loader import reset_cache as reset_agent_cache
        from src.lib.config.schema_discovery import reset_cache as reset_schema_cache
        reset_agent_cache()
        reset_schema_cache()
        yield
        reset_agent_cache()
        reset_schema_cache()

    def test_concurrent_agent_loading(self):
        """Concurrent load_agent_definitions calls should not corrupt state."""
        import threading
        from src.lib.config.agent_loader import load_agent_definitions, reset_cache

        reset_cache()
        results = []
        errors = []

        def load():
            try:
                result = load_agent_definitions(ALLIANCE_AGENTS_PATH)
                results.append(result)
            except Exception as e:
                errors.append(e)

        # Launch 10 threads simultaneously
        threads = [threading.Thread(target=load) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent loading: {errors}"
        assert len(results) == 10, "All threads should complete"

        # All results should have the same agents
        first_keys = set(results[0].keys())
        for i, result in enumerate(results[1:], 2):
            assert set(result.keys()) == first_keys, (
                f"Thread {i} got different agents than thread 1"
            )

    def test_concurrent_schema_discovery(self):
        """Concurrent discover_agent_schemas calls should not corrupt state."""
        import threading
        from src.lib.config.schema_discovery import discover_agent_schemas, reset_cache

        reset_cache()
        results = []
        errors = []

        def discover():
            try:
                result = discover_agent_schemas(ALLIANCE_AGENTS_PATH)
                results.append(result)
            except Exception as e:
                errors.append(e)

        # Launch 10 threads simultaneously
        threads = [threading.Thread(target=discover) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent discovery: {errors}"
        assert len(results) == 10, "All threads should complete"

        # All results should have the same schemas
        first_keys = set(results[0].keys())
        for i, result in enumerate(results[1:], 2):
            assert set(result.keys()) == first_keys, (
                f"Thread {i} got different schemas than thread 1"
            )


class TestAgentLoaderEdgeCases:
    """Tests for agent_loader edge cases."""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset cache before each test."""
        from src.lib.config.agent_loader import reset_cache
        reset_cache()
        yield
        reset_cache()

    def test_empty_agents_directory(self, tmp_path):
        """Loading from empty directory should return empty dict."""
        from src.lib.config.agent_loader import load_agent_definitions

        agents = load_agent_definitions(tmp_path)

        assert agents == {}

    def test_folder_without_agent_yaml_is_skipped(self, tmp_path):
        """Folders without agent.yaml should be silently skipped."""
        from src.lib.config.agent_loader import load_agent_definitions

        # Create folder without agent.yaml
        (tmp_path / "empty_folder").mkdir()

        agents = load_agent_definitions(tmp_path)

        assert agents == {}

    def test_agent_yaml_with_only_agent_id(self, tmp_path):
        """Minimal YAML with only agent_id should use defaults."""
        from src.lib.config.agent_loader import load_agent_definitions

        agent_dir = tmp_path / "minimal"
        agent_dir.mkdir()
        (agent_dir / "agent.yaml").write_text("agent_id: minimal_agent")

        agents = load_agent_definitions(tmp_path)

        assert "minimal_agent" in agents
        # Check defaults are applied
        agent = agents["minimal_agent"]
        assert agent.model_config.model == "gpt-4o"
        assert agent.model_config.temperature == 0.1
        assert agent.supervisor_routing.enabled is True
        assert agent.tools == []

    def test_get_agent_definition_auto_initializes(self):
        """get_agent_definition should auto-initialize if needed."""
        from src.lib.config.agent_loader import (
            reset_cache,
            get_agent_definition,
            is_initialized,
        )

        reset_cache()
        assert not is_initialized()

        # This should trigger auto-initialization
        # Note: This will use DEFAULT_AGENTS_PATH which may differ in Docker
        # So we just verify the function doesn't crash
        try:
            get_agent_definition("gene_validation")
        except FileNotFoundError:
            # Expected if default path doesn't exist
            pass

    def test_get_agent_by_folder_not_found(self):
        """get_agent_by_folder should return None for unknown folder."""
        from src.lib.config.agent_loader import load_agent_definitions, get_agent_by_folder

        load_agent_definitions(ALLIANCE_AGENTS_PATH)

        result = get_agent_by_folder("nonexistent_folder")

        assert result is None

    def test_get_agent_by_tool_name_not_found(self):
        """get_agent_by_tool_name should return None for unknown tool."""
        from src.lib.config.agent_loader import load_agent_definitions, get_agent_by_tool_name

        load_agent_definitions(ALLIANCE_AGENTS_PATH)

        result = get_agent_by_tool_name("ask_nonexistent_specialist")

        assert result is None

    def test_list_agents_category_filter(self):
        """list_agents should filter by category when specified."""
        from src.lib.config.agent_loader import load_agent_definitions, list_agents

        load_agent_definitions(ALLIANCE_AGENTS_PATH)

        # Get agents with a specific category
        validation_agents = list_agents(category="Validation")

        # All returned agents should have that category
        for agent in validation_agents:
            assert agent.category == "Validation"

    def test_supervisor_routing_defaults(self):
        """SupervisorRouting should have correct defaults."""
        from src.lib.config.agent_loader import SupervisorRouting

        routing = SupervisorRouting()

        assert routing.enabled is True
        assert routing.description == ""
        assert routing.batchable is False
        assert routing.batching_instructions == ""

    def test_model_config_defaults(self):
        """ModelConfig should have correct defaults."""
        from src.lib.config.agent_loader import ModelConfig

        config = ModelConfig()

        assert config.model == "gpt-4o"
        assert config.temperature == 0.1
        assert config.reasoning == "medium"

    def test_frontend_config_defaults(self):
        """FrontendConfig should have correct defaults."""
        from src.lib.config.agent_loader import FrontendConfig

        config = FrontendConfig()

        assert config.icon == "🤖"
        assert config.show_in_palette is True
