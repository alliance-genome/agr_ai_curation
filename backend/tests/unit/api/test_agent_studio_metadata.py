"""Tests for agent metadata API endpoint."""
import logging
from types import SimpleNamespace

import pytest


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

    def test_get_registry_metadata_includes_gene_agent(self):
        """Response should include gene agent with icon."""
        import asyncio
        from src.api.agent_studio import get_registry_metadata

        result = asyncio.run(get_registry_metadata())
        assert "gene" in result.agents
        agent = result.agents["gene"]
        assert agent.name is not None
        assert agent.icon is not None
        assert agent.category is not None

    def test_get_registry_metadata_includes_supervisor_tool(self):
        """Response should include supervisor_tool for routable agents."""
        import asyncio
        from src.api.agent_studio import get_registry_metadata

        result = asyncio.run(get_registry_metadata())
        gene = result.agents.get("gene")
        assert gene is not None
        assert gene.supervisor_tool == "ask_gene_specialist"

    def test_get_registry_metadata_includes_extraction_validation_attachments(self):
        """Extraction agents should include domain-pack validation attachment options."""
        import asyncio
        from src.api.agent_studio import get_registry_metadata

        result = asyncio.run(get_registry_metadata())
        chemical_extractor = result.agents.get("chemical_extractor")

        assert chemical_extractor is not None
        assert chemical_extractor.validation_attachments
        assert {
            option["state"] for option in chemical_extractor.validation_attachments
        }.issuperset({"active", "under_development"})

    def test_get_registry_metadata_projects_under_development_validator_bindings(self):
        """Under-development bindings should be visible metadata with explanations."""
        import asyncio
        from src.api.agent_studio import get_registry_metadata

        result = asyncio.run(get_registry_metadata())
        disease_extractor = result.agents.get("disease_extractor")

        assert disease_extractor is not None
        assert disease_extractor.validation_attachments
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
        affected_fields = {
            field
            for option in under_development
            for field in option.get("affected_fields", [])
        }
        assert {
            "single_reference.curie",
            "disease_annotation_subject.subject_identifier",
            "condition_relations[0].conditions",
        }.issubset(affected_fields)

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

        fake_custom = SimpleNamespace(
            id="11111111-2222-3333-4444-555555555555",
            user_id=123,
            parent_agent_key="gene",
            category="Validation",
            name="Doug's Gene Agent",
            icon="🔧",
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

    def test_get_registry_metadata_inherits_template_envelope_for_custom_agent(self, monkeypatch):
        """Custom extraction agents should inherit template envelope authoring metadata."""
        import asyncio
        from src.api import agent_studio as api_module

        fake_custom = SimpleNamespace(
            id="22222222-3333-4444-5555-666666666666",
            user_id=123,
            template_source="gene_extractor",
            category="Extraction",
            name="Custom Gene Extractor",
            icon=None,
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

        assert custom.validation_attachments
        assert custom.validation_attachments == template.validation_attachments
        assert custom.domain_envelope is not None
        assert custom.domain_envelope == template.domain_envelope
        assert custom.domain_envelope["domain_pack_id"] == "gene"
        assert custom.domain_envelope["validation_summary"]["default_enabled"] >= 1

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
            parent_agent_key="gene",
            template_source="gene",
            category="Validation",
            tool_ids=["agr_curation_query"],
            name="Doug's Gene Agent",
            description="Custom prompt variant",
            custom_prompt="Custom prompt text",
            group_prompt_overrides={"WB": "Custom WB Rules"},
            created_at=None,
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
            custom_prompt="Custom prompt text",
            group_prompt_overrides={},
            created_at=None,
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

    def test_get_prompt_preview_custom_agent_with_group_rules(self, monkeypatch):
        """Prompt preview should append group rules for custom agent when enabled."""
        import asyncio
        from src.api import agent_studio as api_module

        fake_custom = SimpleNamespace(
            parent_agent_key="gene",
            custom_prompt="CUSTOM BASE PROMPT",
            group_prompt_overrides={},
            group_rules_enabled=True,
        )
        # Build a lightweight module-like object for local imports in endpoint
        fake_custom_module = SimpleNamespace(
            parse_custom_agent_id=lambda _aid: "uuid",
            get_custom_agent_for_user=lambda _db, _uuid, _uid: fake_custom,
            CustomAgentNotFoundError=type("CustomAgentNotFoundError", (Exception,), {}),
            CustomAgentAccessError=type("CustomAgentAccessError", (Exception,), {}),
        )
        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=123),
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
        monkeypatch.setitem(__import__("sys").modules, "src.lib.agent_studio.custom_agent_service", fake_custom_module)

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
            parent_agent_key="gene",
            custom_prompt="CUSTOM BASE PROMPT",
            group_prompt_overrides={"WB": "CUSTOM WB OVERRIDE"},
            group_rules_enabled=True,
        )

        fake_custom_module = SimpleNamespace(
            parse_custom_agent_id=lambda _aid: "uuid",
            get_custom_agent_for_user=lambda _db, _uuid, _uid: fake_custom,
            CustomAgentNotFoundError=type("CustomAgentNotFoundError", (Exception,), {}),
            CustomAgentAccessError=type("CustomAgentAccessError", (Exception,), {}),
        )

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=123),
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
        monkeypatch.setitem(__import__("sys").modules, "src.lib.agent_studio.custom_agent_service", fake_custom_module)

        result = asyncio.run(
            api_module.get_prompt_preview(
                agent_id="ca_11111111-2222-3333-4444-555555555555",
                group_id="WB",
                user={"sub": "test-sub"},
                db=SimpleNamespace(),
            )
        )

        assert "CUSTOM WB OVERRIDE" in result.prompt

    def test_get_prompt_preview_custom_agent_lookup_errors_are_sanitized(self, monkeypatch, caplog):
        import asyncio
        from src.api import agent_studio as api_module

        caplog.set_level(logging.WARNING, logger=api_module.logger.name)

        CustomAgentNotFoundError = type("CustomAgentNotFoundError", (Exception,), {})
        CustomAgentAccessError = type("CustomAgentAccessError", (Exception,), {})

        monkeypatch.setattr(
            api_module,
            "set_global_user_from_cognito",
            lambda _db, _user: SimpleNamespace(id=123),
        )

        fake_custom_module = SimpleNamespace(
            parse_custom_agent_id=lambda _aid: "uuid",
            get_custom_agent_for_user=lambda _db, _uuid, _uid: (_ for _ in ()).throw(
                CustomAgentNotFoundError("custom prompt missing")
            ),
            CustomAgentNotFoundError=CustomAgentNotFoundError,
            CustomAgentAccessError=CustomAgentAccessError,
        )
        monkeypatch.setitem(__import__("sys").modules, "src.lib.agent_studio.custom_agent_service", fake_custom_module)

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

        fake_custom_module.get_custom_agent_for_user = lambda _db, _uuid, _uid: (_ for _ in ()).throw(
            CustomAgentAccessError("custom prompt forbidden")
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

    def test_group_rule_info_legacy_alias_serializes_canonical_group_id(self):
        from src.lib.agent_studio.models import GroupRuleInfo

        rule = GroupRuleInfo(
            mod_id="WB",
            content="WormBase rules",
            source_file="database",
        )

        assert rule.group_id == "WB"
        assert rule.mod_id == "WB"

        dumped = rule.model_dump()
        assert dumped["group_id"] == "WB"
        assert "mod_id" not in dumped

    def test_prompt_info_legacy_aliases_dump_canonical_fields(self):
        from src.lib.agent_studio.models import GroupRuleInfo, PromptInfo

        prompt = PromptInfo(
            agent_id="gene",
            agent_name="Gene Specialist",
            description="Curate genes",
            base_prompt="Base prompt",
            source_file="database",
            has_mod_rules=True,
            mod_rules={
                "WB": GroupRuleInfo(
                    mod_id="WB",
                    content="WormBase rules",
                    source_file="database",
                )
            },
            tools=[],
        )

        assert prompt.has_group_rules is True
        assert prompt.has_mod_rules is True

        dumped = prompt.model_dump()
        assert dumped["has_group_rules"] is True
        assert dumped["group_rules"]["WB"]["group_id"] == "WB"
        assert "has_mod_rules" not in dumped
        assert "mod_rules" not in dumped

    def test_agent_workshop_legacy_aliases_dump_canonical_fields(self):
        from src.lib.agent_studio.models import AgentWorkshopContext

        workshop = AgentWorkshopContext(
            include_mod_rules=True,
            selected_mod_id="WB",
            selected_mod_prompt_draft="WB group draft",
            mod_prompt_override_count=2,
            has_mod_prompt_overrides=True,
        )

        assert workshop.include_group_rules is True
        assert workshop.include_mod_rules is True
        assert workshop.selected_group_id == "WB"
        assert workshop.selected_mod_id == "WB"

        dumped = workshop.model_dump()
        assert dumped["include_group_rules"] is True
        assert dumped["selected_group_id"] == "WB"
        assert dumped["selected_group_prompt_draft"] == "WB group draft"
        assert dumped["group_prompt_override_count"] == 2
        assert dumped["has_group_prompt_overrides"] is True
        assert "include_mod_rules" not in dumped
        assert "selected_mod_id" not in dumped
        assert "selected_mod_prompt_draft" not in dumped
        assert "mod_prompt_override_count" not in dumped
        assert "has_mod_prompt_overrides" not in dumped
