"""Unit tests for catalog_service tool binding resolution."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from src.lib.agent_studio import catalog_service


class _FakeTool:
    def __init__(self, name: str):
        self.name = name


@dataclass(frozen=True)
class _FakeFunctionTool:
    name: str
    on_invoke_tool: object


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *_args, **_kwargs):
        return _FakeQuery(self._rows)


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


def test_create_db_agent_requires_agr_query_tool_call(monkeypatch):
    fake_row = SimpleNamespace(
        id="agent-id",
        agent_key="ca_custom_gene_validation",
        template_source="gene",
        instructions="validate genes",
        mod_prompt_overrides={},
        group_rules_enabled=False,
        model_id="gpt-4o",
        model_temperature=0.1,
        model_reasoning=None,
        output_schema_key=None,
        tool_ids=["agr_curation_query"],
        name="Gene Validation Agent (Custom)",
    )

    monkeypatch.setattr(catalog_service, "_build_runtime_instructions", lambda **_kwargs: "instructions")
    monkeypatch.setattr(
        catalog_service,
        "resolve_tools",
        lambda _tool_ids, _ctx: [_FakeTool("agr_curation_query")],
    )

    from src.lib.openai_agents import config as agent_config
    monkeypatch.setattr(agent_config, "get_model_for_agent", lambda _model, **_kwargs: "mock-model")
    monkeypatch.setattr(agent_config, "build_model_settings", lambda **_kwargs: {"ok": True})

    from src.lib.openai_agents import guardrails as guardrails_mod

    captured = {}

    class _DummyTracker:
        pass

    def _fake_guardrail(*, tracker, minimum_calls, error_message):
        captured["tracker"] = tracker
        captured["minimum_calls"] = minimum_calls
        captured["error_message"] = error_message
        return {"kind": "tool_required", "minimum_calls": minimum_calls}

    class _FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(guardrails_mod, "ToolCallTracker", _DummyTracker)
    monkeypatch.setattr(guardrails_mod, "create_tool_required_output_guardrail", _fake_guardrail)
    monkeypatch.setattr(catalog_service, "Agent", _FakeAgent)

    built = catalog_service._create_db_agent(fake_row)

    assert isinstance(captured["tracker"], _DummyTracker)
    assert captured["minimum_calls"] == 1
    assert "AGR Curation Database" in captured["error_message"]
    assert built.kwargs["output_guardrails"] == [{"kind": "tool_required", "minimum_calls": 1}]


def test_validate_active_agent_output_schemas_passes(monkeypatch):
    db = _FakeDB([
        ("gene", "Gene Validation Agent", "GeneResultEnvelope"),
        ("orthologs", "Orthologs Agent", "OrthologsResult"),
    ])
    monkeypatch.setattr(
        catalog_service,
        "_resolve_output_schema",
        lambda schema_key: object() if schema_key in {"GeneResultEnvelope", "OrthologsResult"} else None,
    )

    catalog_service.validate_active_agent_output_schemas(db)


def test_validate_active_agent_output_schemas_raises_for_unknown(monkeypatch):
    db = _FakeDB([
        ("gene", "Gene Validation Agent", "GeneResultEnvelope"),
        ("bad_agent", "Bad Agent", "MissingEnvelope"),
    ])
    monkeypatch.setattr(
        catalog_service,
        "_resolve_output_schema",
        lambda schema_key: object() if schema_key == "GeneResultEnvelope" else None,
    )

    with pytest.raises(RuntimeError, match="bad_agent \\(Bad Agent\\) -> MissingEnvelope"):
        catalog_service.validate_active_agent_output_schemas(db)


def test_resolve_agr_curation_tool_without_tracker_returns_singleton(monkeypatch):
    fake_tool = _FakeFunctionTool(name="agr_curation_query", on_invoke_tool=None)
    monkeypatch.setattr(
        "src.lib.openai_agents.tools.agr_curation.agr_curation_query",
        fake_tool,
        raising=False,
    )

    resolved = catalog_service._resolve_agr_curation_tool(catalog_service.ToolExecutionContext())

    assert resolved is fake_tool


@pytest.mark.asyncio
async def test_resolve_agr_curation_tool_with_tracker_wraps_invoke(monkeypatch):
    calls = []

    class _Tracker:
        def record_call(self, tool_name):
            calls.append(tool_name)

    async def _original_invoke(_ctx, input_str):
        return f"original:{input_str}"

    fake_tool = _FakeFunctionTool(
        name="agr_curation_query",
        on_invoke_tool=_original_invoke,
    )
    monkeypatch.setattr(
        "src.lib.openai_agents.tools.agr_curation.agr_curation_query",
        fake_tool,
        raising=False,
    )

    resolved = catalog_service._resolve_agr_curation_tool(
        catalog_service.ToolExecutionContext(tool_tracker=_Tracker())
    )

    assert resolved is not fake_tool
    result = await resolved.on_invoke_tool(None, '{"method":"search_genes"}')

    assert calls == ["agr_curation_query"]
    assert result == 'original:{"method":"search_genes"}'
