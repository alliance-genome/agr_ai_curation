"""Tests for agent metadata API endpoint."""
import logging
from types import SimpleNamespace

import pytest
from pydantic import ValidationError


class TestGetRegistryMetadata:
    """Tests for GET /api/agent-studio/registry/metadata endpoint."""

    def test_agent_metadata_response_model_exists(self):
        """Response models should be importable."""
        from src.api.agent_studio import AgentMetadata, RegistryMetadataResponse

        assert AgentMetadata is not None
        assert RegistryMetadataResponse is not None

    def test_agent_metadata_has_required_fields(self):
        """AgentMetadata should have name, icon, category fields."""
        from src.api.agent_studio import AgentMetadata

        metadata = AgentMetadata(
            name="Test Agent",
            icon="🧪",
            category="Validation",
        )
        assert metadata.name == "Test Agent"
        assert metadata.icon == "🧪"
        assert metadata.category == "Validation"
        assert metadata.output_schema_key is None
        assert metadata.is_active is True
        assert metadata.visible is True
        assert metadata.produces_flow_artifacts is False

    def test_agent_metadata_optional_fields(self):
        """AgentMetadata should support optional fields."""
        from src.api.agent_studio import AgentMetadata

        metadata = AgentMetadata(
            name="Test Agent",
            icon="🧪",
            category="Validation",
            subcategory="Entity",
            supervisor_tool="query_test_specialist",
        )
        assert metadata.subcategory == "Entity"
        assert metadata.supervisor_tool == "query_test_specialist"

    def test_agent_metadata_supports_validation_attachments(self):
        """AgentMetadata should carry flow-builder validation attachment options."""
        from src.api.agent_studio import AgentMetadata

        metadata = AgentMetadata(
            name="Test Agent",
            icon="🧪",
            category="Extraction",
            validation_attachments=[
                {
                    "attachment_id": "fixture",
                    "domain_pack_id": "fixture.validation",
                    "validator_id": "fixture.validator",
                    "state": "active",
                    "scope": "field",
                    "required": True,
                    "blocking": True,
                    "export_blocking": True,
                    "default_enabled": True,
                    "allow_opt_out": False,
                }
            ],
        )

        assert metadata.validation_attachments[0]["attachment_id"] == "fixture"

    def test_agent_metadata_supports_domain_envelope_metadata(self):
        """AgentMetadata should carry domain-envelope authoring metadata."""
        from src.api.agent_studio import AgentMetadata

        metadata = AgentMetadata(
            name="Test Extractor",
            icon="E",
            category="Extraction",
            domain_envelope={
                "domain_pack_id": "fixture.validation",
                "domain_pack_version": "0.1.0",
                "display_name": "Fixture Pack",
                "semantic_source_note": (
                    "Domain envelope objects are the semantic source of truth."
                ),
                "object_definitions": [
                    {
                        "object_type": "fixture_object",
                        "display_name": "Fixture object",
                        "fields": [{"field_path": "identifier"}],
                    }
                ],
            },
        )

        assert metadata.domain_envelope is not None
        assert metadata.domain_envelope["domain_pack_id"] == "fixture.validation"

    def test_registry_metadata_response_has_agents(self):
        """RegistryMetadataResponse should have agents dict."""
        from src.api.agent_studio import AgentMetadata, RegistryMetadataResponse

        agents = {
            "gene": AgentMetadata(
                name="Gene Validator",
                icon="🧬",
                category="Validation",
            )
        }
        response = RegistryMetadataResponse(agents=agents)
        assert "gene" in response.agents
        assert response.agents["gene"].name == "Gene Validator"

    def test_get_registry_metadata_function_exists(self):
        """get_registry_metadata function should be importable."""
        from src.api.agent_studio import get_registry_metadata

        assert callable(get_registry_metadata)

    def test_get_registry_metadata_returns_response(self):
        """get_registry_metadata should return RegistryMetadataResponse."""
        import asyncio
        from src.api.agent_studio import get_registry_metadata, RegistryMetadataResponse

        # Run async function
        result = asyncio.run(get_registry_metadata())
        assert isinstance(result, RegistryMetadataResponse)
        assert "agents" in result.model_dump()

    def test_get_registry_metadata_includes_gene_validation_agent(self):
        """Response should include the canonical gene validator with an icon."""
        import asyncio
        from src.api.agent_studio import get_registry_metadata

        result = asyncio.run(get_registry_metadata())
        assert "gene_validation" in result.agents
        assert "gene" not in result.agents
        agent = result.agents["gene_validation"]
        assert agent.name is not None
        assert agent.icon is not None
        assert agent.category is not None

    def test_registry_metadata_exposes_server_authoritative_flow_artifact_capability(self):
        import asyncio
        from src.api.agent_studio import get_registry_metadata

        result = asyncio.run(get_registry_metadata())

        gene_extractor = result.agents["gene_extractor"]
        assert gene_extractor.is_active is True
        assert gene_extractor.visible is True
        assert gene_extractor.produces_flow_artifacts is True

        gene_validator = result.agents["gene_validation"]
        assert gene_validator.category == "Validation"
        assert gene_validator.output_schema_key
        assert gene_validator.produces_flow_artifacts is True

        task_input = result.agents["task_input"]
        assert task_input.produces_flow_artifacts is False

    def test_get_registry_metadata_includes_supervisor_tool(self):
        """Response should include supervisor_tool for routable agents."""
        import asyncio
        from src.api.agent_studio import get_registry_metadata

        result = asyncio.run(get_registry_metadata())

        # The gene validator is intentionally no longer a standalone
        # supervisor-callable chat tool (supervisor_routing.enabled = false); it
        # runs via the extractor's binding-driven validation, so it exposes no
        # supervisor_tool.
        gene = result.agents.get("gene_validation")
        assert gene is not None
        assert gene.supervisor_tool is None

        # Agents that ARE supervisor-enabled (e.g. the gene extractor) still
        # surface their generated supervisor_tool, so the wiring stays verified.
        gene_extractor = result.agents.get("gene_extractor")
        assert gene_extractor is not None
        assert gene_extractor.supervisor_tool == "ask_gene_extractor_specialist"

    def test_get_registry_metadata_includes_extraction_validation_attachments(self):
        """Extraction agents should include domain-pack validation attachment options."""
        import asyncio
        from src.api.agent_studio import get_registry_metadata

        result = asyncio.run(get_registry_metadata())
        extraction_agent = result.agents.get("disease_extractor")

        assert extraction_agent is not None
        assert extraction_agent.validation_attachments
        assert {
            option["state"] for option in extraction_agent.validation_attachments
        }.issuperset({"active", "under_development"})

    def test_get_registry_metadata_projects_under_development_validator_bindings(self):
        """Under-development bindings should be visible metadata with explanations."""
        import asyncio
        from src.api.agent_studio import get_registry_metadata

        result = asyncio.run(get_registry_metadata())
        disease_extractor = result.agents.get("disease_extractor")

        assert disease_extractor is not None
        assert disease_extractor.validation_attachments
        assert all(
            option.get("validator_agent_id") != "ontology_mapping"
            for option in disease_extractor.validation_attachments
        )
        assert any(
            option.get("validator_binding_id") == "disease_ontology_term_lookup"
            and option.get("validator_agent_id") == "ontology_term_validation"
            for option in disease_extractor.validation_attachments
        )
        under_development = [
            option
            for option in disease_extractor.validation_attachments
            if option["state"] == "under_development"
            and option.get("validator_binding_id")
        ]

        assert under_development
        assert all(option["default_enabled"] is False for option in under_development)
        assert all(option["required"] is False for option in under_development)
        assert all(option["export_blocking"] is False for option in under_development)
        assert all(option.get("state_explanation") for option in under_development)
        assert {
            option["validator_binding_id"] for option in under_development
        } == {"disease_reference_materialization"}
        affected_fields = {
            field
            for option in under_development
            for field in option.get("affected_fields", [])
        }
        assert affected_fields == {"single_reference.curie"}

    def test_get_registry_metadata_includes_domain_envelope_authoring_metadata(self):
        """Extraction agents should expose domain-pack envelope metadata."""
        import asyncio
        from src.api.agent_studio import get_registry_metadata

        result = asyncio.run(get_registry_metadata())
        gene_extractor = result.agents.get("gene_extractor")

        assert gene_extractor is not None
        assert gene_extractor.domain_envelope is not None

        envelope = gene_extractor.domain_envelope
        assert envelope["domain_pack_id"] == "gene"
        assert envelope["schema_refs"]
        assert "semantic source of truth" in envelope["semantic_source_note"]
        assert envelope["validation_summary"]["default_enabled"] >= 1

        object_definitions = envelope["object_definitions"]
        assert object_definitions
        gene_object = object_definitions[0]
        assert gene_object["object_type"] == "gene_mention_evidence"
        assert gene_object["schema_ref"]["provider"] == "alliance_linkml"
        field_paths = {
            field["field_path"]
            for field in gene_object["fields"]
        }
        assert {"primary_external_id", "gene_symbol"}.issubset(field_paths)
        fields_by_path = {
            field["field_path"]: field
            for field in gene_object["fields"]
        }
        assert fields_by_path["gene_symbol"]["provider_refs"]
        assert fields_by_path["gene_symbol"]["source_of_truth"] == "alliance_linkml"

    def test_get_registry_metadata_includes_custom_agents_for_user(self, monkeypatch):
        """Metadata endpoint should append current user's active custom agents."""
        import asyncio
        from src.api import agent_studio as api_module
        monkeypatch.setattr(
            "src.lib.agent_studio.domain_envelope_metadata.custom_agent_revision_metadata",
            lambda *args, **kwargs: (SimpleNamespace(output_contract=SimpleNamespace(
                output_state="none", output_schema_key=None)), None),
        )

        fake_custom = SimpleNamespace(
            id="11111111-2222-3333-4444-555555555555",
            user_id=123,
            template_source="gene",
            category="Validation",
            name="Doug's Gene Agent",
            icon="🔧",
            output_schema_key="GeneResultEnvelope",
            is_active=True,
            allowed_group_ids=[],
        )
        monkeypatch.setattr(
            api_module,
            "list_custom_agents_visible_to_user",
            lambda _db, _uid: [fake_custom],
        )
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=123),
        )
        monkeypatch.setattr(
            api_module,
            "make_custom_agent_id",
            lambda custom_id: f"ca_{custom_id}",
        )

        result = asyncio.run(
            api_module.get_registry_metadata(
                user={"sub": "test-sub", "email": "test@example.org"},
                db=SimpleNamespace(),
            )
        )

        custom_id = "ca_11111111-2222-3333-4444-555555555555"
        assert custom_id in result.agents
        assert result.agents[custom_id].name == "Doug's Gene Agent"
        assert result.agents[custom_id].subcategory == "My Custom Agents"
        assert result.agents[custom_id].output_schema_key is None
        assert result.agents[custom_id].is_active is True
        assert result.agents[custom_id].visible is True
        assert result.agents[custom_id].produces_flow_artifacts is False
        assert result.agents[custom_id].domain_envelope is None

    def test_get_registry_metadata_never_falls_back_to_template_for_unavailable_revision(self, monkeypatch):
        """Unavailable saved metadata must not advertise template validators."""
        import asyncio
        from src.api import agent_studio as api_module
        from unittest.mock import Mock
        monkeypatch.setattr(
            "src.lib.agent_studio.domain_envelope_metadata.custom_agent_revision_metadata",
            Mock(side_effect=ValueError("Unavailable revision")),
        )

        fake_custom = SimpleNamespace(
            id="22222222-3333-4444-5555-666666666666",
            user_id=123,
            template_source="gene_extractor",
            category="Extraction",
            name="Custom Gene Extractor",
            icon=None,
            allowed_group_ids=[],
        )
        monkeypatch.setattr(
            api_module,
            "list_custom_agents_visible_to_user",
            lambda _db, _uid: [fake_custom],
        )
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=123),
        )
        monkeypatch.setattr(
            api_module,
            "make_custom_agent_id",
            lambda custom_id: f"ca_{custom_id}",
        )

        result = asyncio.run(
            api_module.get_registry_metadata(
                user={"sub": "test-sub", "email": "test@example.org"},
                db=SimpleNamespace(),
            )
        )

        custom_id = "ca_22222222-3333-4444-5555-666666666666"
        template = result.agents["gene_extractor"]
        custom = result.agents[custom_id]

        assert template.validation_attachments
        assert custom.validation_attachments == []
        assert custom.domain_envelope is None
        assert custom.execution_metadata_error
        assert not custom.is_active and not custom.produces_flow_artifacts

    def test_merge_custom_agents_into_catalog(self, monkeypatch):
        """Catalog augmentation should add custom agents under a custom subcategory."""
        from src.api import agent_studio as api_module
        from src.lib.agent_studio.models import PromptCatalog, AgentPrompts, PromptInfo, GroupRuleInfo

        base_catalog = PromptCatalog(
            categories=[
                AgentPrompts(
                    category="Validation",
                    agents=[
                        PromptInfo(
                            agent_id="gene",
                            agent_name="Gene Specialist",
                            description="Curate genes",
                            base_prompt="Base prompt",
                            source_file="database",
                            has_group_rules=True,
                            group_rules={
                                "WB": GroupRuleInfo(
                                    group_id="WB",
                                    content="Parent WB Rules",
                                    source_file="database",
                                )
                            },
                            tools=[],
                            subcategory="Data Validation",
                        )
                    ],
                )
            ],
            total_agents=1,
            available_groups=[],
        )

        fake_custom = SimpleNamespace(
            id="11111111-2222-3333-4444-555555555555",
            user_id=123,
            template_source="gene",
            category="Validation",
            tool_ids=["agr_curation_query"],
            name="Doug's Gene Agent",
            description="Custom prompt variant",
            instructions="Custom prompt text",
            group_prompt_overrides={"WB": "Custom WB Rules"},
            created_at=None,
            allowed_group_ids=[],
        )

        class _FakeDB:
            def query(self, *args, **kwargs):  # pragma: no cover - never called due monkeypatch
                raise AssertionError("query should not be called in this test")

        # Monkeypatch dependencies used inside helper
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=123),
        )
        monkeypatch.setattr(
            api_module,
            "list_custom_agents_visible_to_user",
            lambda _db, _uid: [fake_custom],
        )

        catalog = api_module._merge_custom_agents_into_catalog(  # type: ignore
            base_catalog,
            {"sub": "test-sub"},
            _FakeDB(),
        )

        assert catalog.total_agents == 2
        all_agents = [a for c in catalog.categories for a in c.agents]
        custom = next(a for a in all_agents if a.agent_name == "Doug's Gene Agent")
        assert custom.subcategory == "My Custom Agents"
        assert custom.has_group_rules is True
        assert custom.group_rules["WB"].content == "Custom WB Rules"
        assert custom.show_in_palette is False

    def test_merge_custom_agents_marks_project_shared_agents(self, monkeypatch):
        """Catalog augmentation should label non-owner custom agents as shared."""
        from src.api import agent_studio as api_module
        from src.lib.agent_studio.models import PromptCatalog, AgentPrompts, PromptInfo

        base_catalog = PromptCatalog(
            categories=[
                AgentPrompts(
                    category="Validation",
                    agents=[
                        PromptInfo(
                            agent_id="gene",
                            agent_name="Gene Specialist",
                            description="Curate genes",
                            base_prompt="Base prompt",
                            source_file="database",
                            has_group_rules=False,
                            group_rules={},
                            tools=[],
                            subcategory="Data Validation",
                        )
                    ],
                )
            ],
            total_agents=1,
            available_groups=[],
        )

        shared_custom = SimpleNamespace(
            id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            user_id=999,
            template_source="gene",
            category="Validation",
            tool_ids=[],
            name="Shared Gene Agent",
            description="Shared",
            instructions="Custom prompt text",
            group_prompt_overrides={},
            created_at=None,
            allowed_group_ids=[],
        )

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=123),
        )
        monkeypatch.setattr(
            api_module,
            "list_custom_agents_visible_to_user",
            lambda _db, _uid: [shared_custom],
        )

        catalog = api_module._merge_custom_agents_into_catalog(  # type: ignore
            base_catalog,
            {"sub": "test-sub"},
            SimpleNamespace(query=lambda *_args, **_kwargs: None),
        )

        all_agents = [a for c in catalog.categories for a in c.agents]
        custom = next(a for a in all_agents if a.agent_name == "Shared Gene Agent")
        assert custom.subcategory == "Shared Agents"

    def test_merge_custom_agents_marks_needs_review_overlay_without_projecting_it(self, monkeypatch):
        """Ambiguous legacy overlay text should be visible as flagged metadata, not prompt content."""
        from src.api import agent_studio as api_module
        from src.lib.agent_studio.models import PromptCatalog, AgentPrompts, PromptInfo

        base_catalog = PromptCatalog(
            categories=[
                AgentPrompts(
                    category="Validation",
                    agents=[
                        PromptInfo(
                            agent_id="gene",
                            agent_name="Gene Specialist",
                            description="Curate genes",
                            base_prompt="Parent base prompt",
                            source_file="database",
                            has_group_rules=False,
                            group_rules={},
                            tools=[],
                        )
                    ],
                )
            ],
            total_agents=1,
            available_groups=[],
        )
        flagged_overlay = "Curator note\n\nPlatform Runtime Contract copied fragment"
        fake_custom = SimpleNamespace(
            id="11111111-2222-3333-4444-555555555555",
            user_id=123,
            template_source="gene",
            category="Validation",
            tool_ids=[],
            name="Flagged Gene Agent",
            description="Custom prompt variant",
            instructions=flagged_overlay,
            group_prompt_overrides={},
            created_at=None,
            allowed_group_ids=[],
        )
        captured_base_overrides = []

        def _fake_build_agent_prompt_layers(_agent_id, **kwargs):
            captured_base_overrides.append(kwargs.get("base_prompt_override"))
            return SimpleNamespace(
                hash="hash-without-flagged-overlay",
                to_manifest=lambda: {
                    "agent_id": "gene",
                    "hash": "hash-without-flagged-overlay",
                    "layers": [
                        {
                            "id": "gene:core_static",
                            "kind": "core_static",
                            "title": "Core Prompt",
                            "content": "Locked core prompt",
                            "provenance": "backend_static",
                            "editable": False,
                            "locked": True,
                            "source_ref": "core",
                            "hash": "hash-core",
                        },
                        {
                            "id": "gene:base_prompt",
                            "kind": "base_prompt",
                            "title": "Base Prompt",
                            "content": "Parent base prompt",
                            "provenance": "prompt_template:system",
                            "editable": True,
                            "locked": False,
                            "source_ref": "base",
                            "hash": "hash-base",
                        },
                    ],
                },
            )

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=123),
        )
        monkeypatch.setattr(
            api_module,
            "list_custom_agents_visible_to_user",
            lambda _db, _uid: [fake_custom],
        )
        monkeypatch.setattr(
            api_module,
            "normalize_custom_overlay_for_parent",
            lambda *_args, **_kwargs: SimpleNamespace(
                content=flagged_overlay,
                status="needs_review",
                removed_layer_kinds=["core_static"],
                warning="Custom-agent prompt still contains locked/core prompt markers after safe cleanup.",
            ),
        )
        monkeypatch.setattr(api_module, "build_agent_prompt_layers", _fake_build_agent_prompt_layers)

        catalog = api_module._merge_custom_agents_into_catalog(  # type: ignore
            base_catalog,
            {"sub": "test-sub"},
            SimpleNamespace(query=lambda *_args, **_kwargs: None),
        )

        custom = next(
            a
            for c in catalog.categories
            for a in c.agents
            if a.agent_name == "Flagged Gene Agent"
        )
        assert captured_base_overrides == []
        assert custom.custom_prompt_overlay_status == "needs_review"
        assert custom.custom_prompt_removed_layer_kinds == ["core_static"]
        assert "locked/core prompt markers" in custom.custom_prompt_warning
        assert custom.base_prompt == ""
        assert custom.prompt_layer_error == "Custom agent prompt needs coordinator review."
        assert flagged_overlay not in "\n\n".join(layer.content for layer in custom.prompt_layers)
        assert not any(layer.kind == "curator_overlay" for layer in custom.prompt_layers)

    def test_merge_custom_agents_surfaces_prompt_layer_projection_errors(self, monkeypatch):
        """Custom-agent catalog entries should expose layer assembly failures."""
        from src.api import agent_studio as api_module
        from src.lib.agent_studio.models import PromptCatalog, AgentPrompts, PromptInfo

        base_catalog = PromptCatalog(
            categories=[
                AgentPrompts(
                    category="Validation",
                    agents=[
                        PromptInfo(
                            agent_id="gene",
                            agent_name="Gene Specialist",
                            description="Curate genes",
                            base_prompt="Parent base prompt",
                            source_file="database",
                            has_group_rules=False,
                            group_rules={},
                            tools=[],
                        )
                    ],
                )
            ],
            total_agents=1,
            available_groups=[],
        )
        fake_custom = SimpleNamespace(
            id="11111111-2222-3333-4444-555555555555",
            user_id=123,
            template_source="gene",
            category="Validation",
            tool_ids=[],
            name="Layer Error Agent",
            description="Custom prompt variant",
            instructions="Curator note",
            group_prompt_overrides={},
            created_at=None,
            allowed_group_ids=[],
        )

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=123),
        )
        monkeypatch.setattr(
            api_module,
            "list_custom_agents_visible_to_user",
            lambda _db, _uid: [fake_custom],
        )
        monkeypatch.setattr(
            api_module,
            "normalize_custom_overlay_for_parent",
            lambda *_args, **_kwargs: SimpleNamespace(
                content="Curator note",
                status="clean",
                removed_layer_kinds=[],
                warning=None,
            ),
        )
        monkeypatch.setattr(
            api_module,
            "build_agent_prompt_layers",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("layer projection failed")),
        )

        catalog = api_module._merge_custom_agents_into_catalog(  # type: ignore
            base_catalog,
            {"sub": "test-sub"},
            SimpleNamespace(query=lambda *_args, **_kwargs: None),
        )

        custom = next(
            a
            for c in catalog.categories
            for a in c.agents
            if a.agent_name == "Layer Error Agent"
        )
        assert custom.prompt_layers == []
        assert custom.effective_prompt_hash is None
        assert custom.prompt_layer_error == "Prompt layer metadata could not be built."

    def test_get_prompt_preview_system_agent(self, monkeypatch):
        """Prompt preview should return base prompt for system agent without group_id."""
        import asyncio
        from src.api import agent_studio as api_module

        class _FakeService:
            def get_effective_prompt_bundle(self, agent_id, group_id=None):
                assert agent_id == "gene"
                assert group_id is None
                return SimpleNamespace(
                    render=lambda: "SYSTEM BASE PROMPT",
                    hash="hash-system",
                    to_manifest=lambda: {
                        "agent_id": "gene",
                        "layers": [],
                        "hash": "hash-system",
                    },
                )

        monkeypatch.setattr(api_module, "get_prompt_catalog", lambda: _FakeService())
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1),
        )
        monkeypatch.setattr(api_module, "get_agent_by_key", lambda *_args, **_kwargs: object())

        result = asyncio.run(
            api_module.get_prompt_preview(
                agent_id="gene",
                group_id=None,
                user={"sub": "test-sub"},
                db=SimpleNamespace(),
            )
        )
        assert result.source == "system_agent"
        assert result.prompt == "SYSTEM BASE PROMPT"

    def test_prompt_preview_route_rejects_legacy_mod_id_query(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.api import auth as auth_module
        from src.api import agent_studio as api_module

        app = FastAPI()
        app.include_router(api_module.router)
        app.dependency_overrides[auth_module.auth.get_user] = lambda: {
            "sub": "test-sub",
        }
        app.dependency_overrides[api_module.get_db] = lambda: SimpleNamespace()

        response = TestClient(app).get(
            "/api/agent-studio/prompt-preview/gene",
            params={"mod_id": "WB"},
        )

        assert response.status_code == 400
        assert response.json() == {
            "detail": "Unsupported query parameter mod_id. Use group_id."
        }

    def test_get_prompt_preview_custom_agent_with_group_rules(self, monkeypatch):
        """Prompt preview should append group rules for custom agent when enabled."""
        import asyncio
        from src.api import agent_studio as api_module

        fake_custom = SimpleNamespace(
            template_source="gene",
            instructions="CUSTOM BASE PROMPT",
            group_prompt_overrides={},
            group_rules_enabled=True,
            allowed_group_ids=[],
        )
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=123),
        )
        monkeypatch.setattr(
            api_module,
            "get_custom_agent_for_user",
            lambda _db, _uuid, _uid: fake_custom,
        )
        monkeypatch.setattr(
            api_module,
            "normalize_custom_overlay_for_parent",
            lambda *_args, **_kwargs: SimpleNamespace(
                content="CUSTOM BASE PROMPT",
                status="clean",
                removed_layer_kinds=[],
                warning=None,
            ),
        )
        monkeypatch.setattr(
            api_module,
            "build_agent_prompt_layers",
            lambda *_args, **_kwargs: SimpleNamespace(
                render=lambda: "SYSTEM BASE PROMPT\n\nWB ONLY RULES\n\nCUSTOM BASE PROMPT",
                hash="hash-custom",
                to_manifest=lambda: {
                    "agent_id": "gene",
                    "layers": [{"kind": "curator_overlay"}],
                    "hash": "hash-custom",
                },
            ),
        )

        result = asyncio.run(
            api_module.get_prompt_preview(
                agent_id="ca_11111111-2222-3333-4444-555555555555",
                group_id="WB",
                user={"sub": "test-sub"},
                db=SimpleNamespace(),
            )
        )

        assert result.source == "custom_agent"
        assert "CUSTOM BASE PROMPT" in result.prompt
        assert "WB ONLY RULES" in result.prompt

    def test_get_prompt_preview_custom_agent_prefers_custom_group_override(self, monkeypatch):
        """Prompt preview should use custom group override content when present."""
        import asyncio
        from src.api import agent_studio as api_module

        fake_custom = SimpleNamespace(
            template_source="gene",
            instructions="CUSTOM BASE PROMPT",
            group_prompt_overrides={"WB": "CUSTOM WB OVERRIDE"},
            group_rules_enabled=True,
            allowed_group_ids=[],
        )

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=123),
        )
        monkeypatch.setattr(
            api_module,
            "get_custom_agent_for_user",
            lambda _db, _uuid, _uid: fake_custom,
        )
        monkeypatch.setattr(
            api_module,
            "normalize_custom_overlay_for_parent",
            lambda *_args, **_kwargs: SimpleNamespace(
                content="CUSTOM BASE PROMPT",
                status="clean",
                removed_layer_kinds=[],
                warning=None,
            ),
        )
        monkeypatch.setattr(
            api_module,
            "build_agent_prompt_layers",
            lambda *_args, **_kwargs: SimpleNamespace(
                render=lambda: "SYSTEM BASE PROMPT\n\nCUSTOM BASE PROMPT\n\nCUSTOM WB OVERRIDE",
                hash="hash-custom",
                to_manifest=lambda: {
                    "agent_id": "gene",
                    "layers": [{"kind": "curator_overlay"}],
                    "hash": "hash-custom",
                },
            ),
        )

        result = asyncio.run(
            api_module.get_prompt_preview(
                agent_id="ca_11111111-2222-3333-4444-555555555555",
                group_id="WB",
                user={"sub": "test-sub"},
                db=SimpleNamespace(),
            )
        )

        assert "CUSTOM WB OVERRIDE" in result.prompt

    def test_get_prompt_preview_custom_agent_rejects_locked_group_override(self, monkeypatch):
        """Prompt preview should not assemble copied locked text from group overrides."""
        import asyncio
        from src.api import agent_studio as api_module

        fake_custom = SimpleNamespace(
            template_source="gene",
            instructions="CUSTOM BASE PROMPT",
            group_prompt_overrides={
                "WB": "Platform Runtime Contract\nCurator tried to copy this.",
            },
            group_rules_enabled=True,
            allowed_group_ids=[],
        )

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=123),
        )
        monkeypatch.setattr(
            api_module,
            "get_custom_agent_for_user",
            lambda _db, _uuid, _uid: fake_custom,
        )
        monkeypatch.setattr(
            api_module,
            "build_agent_prompt_layers",
            lambda *_args, **_kwargs: pytest.fail("locked group override was assembled"),
        )

        with pytest.raises(api_module.HTTPException) as exc_info:
            asyncio.run(
                api_module.get_prompt_preview(
                    agent_id="ca_11111111-2222-3333-4444-555555555555",
                    group_id="WB",
                    user={"sub": "test-sub"},
                    db=SimpleNamespace(),
                )
            )

        assert exc_info.value.status_code == 409
        assert "needs coordinator review" in exc_info.value.detail

    def test_get_prompt_preview_custom_agent_lookup_errors_are_sanitized(self, monkeypatch, caplog):
        import asyncio
        from src.api import agent_studio as api_module

        caplog.set_level(logging.WARNING, logger=api_module.logger.name)

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=123),
        )

        monkeypatch.setattr(
            api_module,
            "get_custom_agent_for_user",
            lambda _db, _uuid, _uid: (_ for _ in ()).throw(
                api_module.CustomAgentNotFoundError("custom prompt missing")
            ),
        )

        with pytest.raises(api_module.HTTPException) as not_found_exc:
            asyncio.run(
                api_module.get_prompt_preview(
                    agent_id="ca_11111111-2222-3333-4444-555555555555",
                    group_id=None,
                    user={"sub": "test-sub"},
                    db=SimpleNamespace(),
                )
            )

        assert not_found_exc.value.status_code == 404
        assert not_found_exc.value.detail == "Custom agent not found"
        assert "custom prompt missing" not in str(not_found_exc.value.detail)
        assert "custom prompt missing" in caplog.text

        monkeypatch.setattr(
            api_module,
            "get_custom_agent_for_user",
            lambda _db, _uuid, _uid: (_ for _ in ()).throw(
                api_module.CustomAgentAccessError("custom prompt forbidden")
            ),
        )
        with pytest.raises(api_module.HTTPException) as access_exc:
            asyncio.run(
                api_module.get_prompt_preview(
                    agent_id="ca_11111111-2222-3333-4444-555555555555",
                    group_id=None,
                    user={"sub": "test-sub"},
                    db=SimpleNamespace(),
                )
            )

        assert access_exc.value.status_code == 403
        assert access_exc.value.detail == "Access denied to custom agent"
        assert "custom prompt forbidden" not in str(access_exc.value.detail)
        assert "custom prompt forbidden" in caplog.text

    @pytest.mark.asyncio
    async def test_get_prompt_preview_maps_unexpected_errors_to_500(self, monkeypatch, caplog):
        from src.api import agent_studio as api_module

        caplog.set_level(logging.ERROR, logger=api_module.logger.name)

        class _BrokenService:
            def get_effective_prompt_bundle(self, _agent_id, group_id=None):
                raise RuntimeError("preview exploded")

        monkeypatch.setattr(api_module, "get_prompt_catalog", lambda: _BrokenService())
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=1),
        )
        monkeypatch.setattr(api_module, "get_agent_by_key", lambda *_args, **_kwargs: object())

        with pytest.raises(api_module.HTTPException) as exc_info:
            await api_module.get_prompt_preview(
                agent_id="gene",
                group_id=None,
                user={"sub": "test-sub"},
                db=SimpleNamespace(),
            )

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Failed to get prompt preview"
        assert "preview exploded" not in str(exc_info.value.detail)
        assert "preview exploded" in caplog.text

    def test_group_rule_info_rejects_legacy_mod_id_alias(self):
        from src.lib.agent_studio.models import GroupRuleInfo

        with pytest.raises(ValidationError):
            GroupRuleInfo.model_validate(
                {
                    "mod_id": "WB",
                    "content": "WormBase rules",
                    "source_file": "database",
                }
            )

    def test_prompt_info_canonical_group_fields_and_rejects_legacy_aliases(self):
        from src.lib.agent_studio.models import GroupRuleInfo, PromptInfo

        prompt = PromptInfo(
            agent_id="gene",
            agent_name="Gene Specialist",
            description="Curate genes",
            base_prompt="Base prompt",
            source_file="database",
            has_group_rules=True,
            group_rules={
                "WB": GroupRuleInfo(
                    group_id="WB",
                    content="WormBase rules",
                    source_file="database",
                )
            },
            tools=[],
        )

        assert prompt.has_group_rules is True

        dumped = prompt.model_dump()
        assert dumped["has_group_rules"] is True
        assert dumped["group_rules"]["WB"]["group_id"] == "WB"

        with pytest.raises(ValidationError):
            PromptInfo.model_validate(
                {
                    "agent_id": "gene",
                    "agent_name": "Gene Specialist",
                    "description": "Curate genes",
                    "base_prompt": "Base prompt",
                    "source_file": "database",
                    "has_mod_rules": True,
                }
            )

    def test_agent_workshop_group_fields_dump_canonical_fields(self):
        from src.lib.agent_studio.models import AgentWorkshopContext

        workshop = AgentWorkshopContext(
            include_group_rules=True,
            selected_group_id="WB",
            selected_group_prompt_draft="WB group draft",
            group_prompt_override_count=2,
            has_group_prompt_overrides=True,
        )

        assert workshop.include_group_rules is True
        assert workshop.selected_group_id == "WB"

        dumped = workshop.model_dump()
        assert dumped["include_group_rules"] is True
        assert dumped["selected_group_id"] == "WB"
        assert dumped["selected_group_prompt_draft"] == "WB group draft"
        assert dumped["group_prompt_override_count"] == 2
        assert dumped["has_group_prompt_overrides"] is True

    def test_agent_workshop_rejects_removed_prompt_override_aliases(self):
        from src.lib.agent_studio.models import AgentWorkshopContext

        with pytest.raises(ValidationError):
            AgentWorkshopContext.model_validate(
                {
                    "include_mod_rules": True,
                    "selected_mod_id": "WB",
                    "selected_mod_prompt_draft": "WB group draft",
                    "mod_prompt_override_count": 2,
                    "has_mod_prompt_overrides": True,
                }
            )

    @pytest.mark.parametrize(
        ("model_name", "canonical_fields", "legacy_field"),
        [
            ("PromptCatalog", {}, {"available_mods": ["WB"]}),
            ("ChatContext", {}, {"selected_mod_id": "WB"}),
            (
                "PromptExecution",
                {
                    "agent_id": "gene",
                    "agent_name": "Gene Specialist",
                    "prompt_preview": "Prompt",
                },
                {"mod_applied": "WB"},
            ),
        ],
    )
    def test_agent_studio_models_reject_remaining_mod_aliases(
        self,
        model_name,
        canonical_fields,
        legacy_field,
    ):
        from src.lib.agent_studio import models

        model = getattr(models, model_name)
        with pytest.raises(ValidationError):
            model(**canonical_fields, **legacy_field)

    def test_chat_context_rejects_legacy_mod_view_mode(self):
        from src.lib.agent_studio.models import ChatContext

        with pytest.raises(ValidationError):
            ChatContext.model_validate({"view_mode": "mod"})

    def test_registry_contract_omits_and_rejects_legacy_mod_rules_field(self):
        from src.lib.agent_studio.registry_types import AgentRegistryEntry, entry_from_dict

        entry = AgentRegistryEntry(
            name="Gene Specialist",
            description="Curate genes",
            category="Extraction",
            has_group_rules=True,
        )
        assert entry.to_dict()["has_group_rules"] is True
        assert "has_mod_rules" not in entry.to_dict()

        with pytest.raises(ValueError, match="removed field 'has_mod_rules'"):
            entry_from_dict(
                "gene",
                {
                    "name": "Gene Specialist",
                    "has_mod_rules": True,
                },
            )
