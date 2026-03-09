"""Tests for agent metadata API endpoint."""
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
        monkeypatch.setattr(api_module, "list_custom_agents_visible_to_user", lambda _db, _uid: [fake_custom])
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
            def get_agent(self, agent_id):
                assert agent_id == "gene"
                return SimpleNamespace(base_prompt="SYSTEM BASE PROMPT")

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
        fake_rule_prompt = "WB ONLY RULES"

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
            "get_custom_agent_group_prompt",
            lambda parent_agent_key, group_id, group_prompt_overrides: (
                fake_rule_prompt if parent_agent_key == "gene" and group_id == "WB" else None
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
            "get_custom_agent_group_prompt",
            lambda parent_agent_key, group_id, group_prompt_overrides: group_prompt_overrides.get(group_id),
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
