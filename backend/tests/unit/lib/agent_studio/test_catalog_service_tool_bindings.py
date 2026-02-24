"""Unit tests for catalog_service tool binding resolution."""

from types import SimpleNamespace

import pytest

from src.lib.agent_studio import catalog_service


class _FakeTool:
    def __init__(self, name: str):
        self.name = name


def test_resolve_tools_rejects_unknown_binding(monkeypatch):
    monkeypatch.setattr(catalog_service, "TOOL_BINDINGS", {})

    with pytest.raises(ValueError, match="Unknown tool binding"):
        catalog_service.resolve_tools(
            ["nonexistent_tool"],
            catalog_service.ToolExecutionContext(),
        )


def test_resolve_tools_requires_execution_context(monkeypatch):
    monkeypatch.setattr(
        catalog_service,
        "TOOL_BINDINGS",
        {
            "search_document": {
                "binding": "context_factory",
                "required_context": ["document_id", "user_id"],
                "resolver": lambda _ctx: _FakeTool("search_document"),
            }
        },
    )

    with pytest.raises(ValueError, match="requires execution context"):
        catalog_service.resolve_tools(
            ["search_document"],
            catalog_service.ToolExecutionContext(document_id=None, user_id=None),
        )


def test_resolve_tools_canonicalizes_method_aliases(monkeypatch):
    monkeypatch.setattr(
        catalog_service,
        "METHOD_TOOL_ENTRIES",
        {"search_genes": {"parent_tool": "agr_curation_query"}},
    )
    monkeypatch.setattr(
        catalog_service,
        "TOOL_BINDINGS",
        {
            "agr_curation_query": {
                "binding": "static",
                "required_context": [],
                "resolver": lambda _ctx: _FakeTool("agr_curation_query"),
            }
        },
    )

    tools = catalog_service.resolve_tools(
        ["search_genes", "agr_curation_query"],
        catalog_service.ToolExecutionContext(),
    )

    assert len(tools) == 1
    assert tools[0].name == "agr_curation_query"


def test_build_tool_execution_context_uses_env_database_url(monkeypatch):
    monkeypatch.setenv("CURATION_DB_URL", "postgresql://example/db")

    context = catalog_service._build_tool_execution_context(
        {"document_id": "doc-1", "user_id": "user-1"}
    )

    assert context.document_id == "doc-1"
    assert context.user_id == "user-1"
    assert context.database_url == "postgresql://example/db"


def test_required_context_for_tool_ids_includes_document_requirements(monkeypatch):
    monkeypatch.setattr(
        catalog_service,
        "TOOL_BINDINGS",
        {
            "search_document": {
                "binding": "context_factory",
                "required_context": ["document_id", "user_id"],
                "resolver": lambda _ctx: _FakeTool("search_document"),
            },
            "agr_curation_query": {
                "binding": "static",
                "required_context": [],
                "resolver": lambda _ctx: _FakeTool("agr_curation_query"),
            },
        },
    )

    required = catalog_service._required_context_for_tool_ids(
        ["agr_curation_query", "search_document"]
    )
    assert required == ["document_id", "user_id"]


def test_create_db_agent_propagates_tool_resolution_errors(monkeypatch):
    fake_row = SimpleNamespace(
        id="agent-id",
        agent_key="disease_validation",
        template_source="disease",
        instructions="do work",
        mod_prompt_overrides={},
        group_rules_enabled=False,
        model_id="gpt-4o",
        model_temperature=0.1,
        model_reasoning="medium",
        output_schema_key=None,
        tool_ids=["curation_db_sql"],
        name="Disease Specialist",
    )
    monkeypatch.setattr(catalog_service, "_build_runtime_instructions", lambda **_kwargs: "instructions")
    monkeypatch.setattr(catalog_service, "resolve_tools", lambda _tool_ids, _ctx: (_ for _ in ()).throw(ValueError("tool resolution failed")))

    from src.lib.openai_agents import config as agent_config
    monkeypatch.setattr(agent_config, "get_model_for_agent", lambda _model, **_kwargs: "mock-model")
    monkeypatch.setattr(agent_config, "build_model_settings", lambda **_kwargs: {"ok": True})

    with pytest.raises(ValueError, match="tool resolution failed"):
        catalog_service._create_db_agent(fake_row)


def test_get_agent_metadata_derives_required_params_from_tool_bindings(monkeypatch):
    fake_row = SimpleNamespace(
        agent_key="pdf_extraction",
        name="PDF Specialist",
        description="Reads documents",
        tool_ids=["search_document", "read_section"],
    )
    monkeypatch.setattr(catalog_service, "_get_db_agent_row", lambda _agent_id, _kwargs: fake_row)

    metadata = catalog_service.get_agent_metadata("pdf_extraction")

    assert metadata["requires_document"] is True
    assert metadata["required_params"] == ["document_id", "user_id"]


def test_get_agent_metadata_db_lookup_prefers_db_user_id(monkeypatch):
    fake_row = SimpleNamespace(
        agent_key="pdf_extraction",
        name="PDF Specialist",
        description="Reads documents",
        tool_ids=["search_document"],
    )
    observed = {"user_id": None, "closed": False}

    fake_db = SimpleNamespace(close=lambda: observed.__setitem__("closed", True))
    monkeypatch.setattr("src.models.sql.database.SessionLocal", lambda: fake_db)

    def _fake_get_agent_by_key(db, agent_id, user_id):
        assert db is fake_db
        assert agent_id == "pdf_extraction"
        observed["user_id"] = user_id
        return fake_row

    monkeypatch.setattr("src.lib.agent_studio.agent_service.get_agent_by_key", _fake_get_agent_by_key)

    metadata = catalog_service.get_agent_metadata(
        "pdf_extraction",
        user_id="9",
        db_user_id=42,
    )

    assert observed["user_id"] == 42
    assert observed["closed"] is True
    assert metadata["display_name"] == "PDF Specialist"


def test_get_agent_metadata_db_lookup_coerces_string_user_id(monkeypatch):
    fake_row = SimpleNamespace(
        agent_key="gene_validation",
        name="Gene Specialist",
        description="Curates genes",
        tool_ids=["agr_curation_query"],
    )
    observed = {"user_id": None}

    fake_db = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr("src.models.sql.database.SessionLocal", lambda: fake_db)

    def _fake_get_agent_by_key(db, agent_id, user_id):
        assert db is fake_db
        assert agent_id == "gene_validation"
        observed["user_id"] = user_id
        return fake_row

    monkeypatch.setattr("src.lib.agent_studio.agent_service.get_agent_by_key", _fake_get_agent_by_key)

    metadata = catalog_service.get_agent_metadata("gene_validation", user_id="17")

    assert observed["user_id"] == 17
    assert metadata["required_params"] == []
