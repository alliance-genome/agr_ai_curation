"""Additional branch tests for catalog_service helpers and service methods."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from src.lib.agent_studio import catalog_service


def test_prompt_key_and_documentation_conversion_branches(monkeypatch):
    assert catalog_service.get_prompt_key_for_agent("task_input") == "task_input"

    monkeypatch.setattr(catalog_service, "get_agent_by_folder", lambda _key: None)
    monkeypatch.setattr(catalog_service, "get_agent_definition", lambda _key: None)
    monkeypatch.setattr(
        catalog_service,
        "AGENT_REGISTRY",
        {
            "gene_alias": {
                "supervisor": {"tool_name": "ask_gene_specialist"},
            }
        },
    )
    assert catalog_service.get_prompt_key_for_agent("gene_alias") == "gene"

    doc = catalog_service._convert_documentation(
        {
            "summary": "Summary",
            "capabilities": [
                {"name": "cap", "description": "desc", "example_query": "q"},
            ],
            "data_sources": [
                {"name": "AGR", "description": "source", "species_supported": ["WB"]},
            ],
            "limitations": ["limit-1"],
        }
    )
    assert doc is not None
    assert doc.capabilities[0].name == "cap"
    assert doc.data_sources[0].name == "AGR"
    assert doc.limitations == ["limit-1"]


def test_get_tool_registry_handles_introspection_errors(monkeypatch):
    fake_good = SimpleNamespace(params_json_schema={}, description="desc")
    fake_bad = SimpleNamespace(params_json_schema={}, description="desc")
    module_a = ModuleType("fake_agr")
    module_b = ModuleType("fake_weaviate")
    module_a.good_tool = fake_good
    module_b.bad_tool = fake_bad

    import src.lib.openai_agents.tools as tools_pkg
    monkeypatch.setattr(tools_pkg, "agr_curation", module_a, raising=False)
    monkeypatch.setattr(tools_pkg, "weaviate_search", module_b, raising=False)

    from src.lib.agent_studio import tool_introspection

    def _fake_introspect(obj):
        if obj is fake_bad:
            raise RuntimeError("boom")
        return SimpleNamespace(
            name="search_document",
            description="Search docs",
            parameters={},
            source_file="x.py",
        )

    monkeypatch.setattr(tool_introspection, "introspect_tool", _fake_introspect)
    monkeypatch.setattr(catalog_service, "TOOL_OVERRIDES", {"search_document": {"category": "Document"}})

    registry = catalog_service.get_tool_registry()
    assert "search_document" in registry
    assert registry["search_document"]["category"] == "Document"


def test_resolver_helpers_and_resolve_tools_error_paths(monkeypatch):
    monkeypatch.setattr(
        "src.lib.openai_agents.tools.weaviate_search.create_search_tool",
        lambda **kwargs: ("search", kwargs),
    )
    monkeypatch.setattr(
        "src.lib.openai_agents.tools.weaviate_search.create_read_section_tool",
        lambda **kwargs: ("section", kwargs),
    )
    monkeypatch.setattr(
        "src.lib.openai_agents.tools.weaviate_search.create_read_subsection_tool",
        lambda **kwargs: ("subsection", kwargs),
    )
    monkeypatch.setattr(
        "src.lib.openai_agents.tools.sql_query.create_sql_query_tool",
        lambda db_url, tool_name: ("sql", db_url, tool_name),
    )
    monkeypatch.setattr(
        "src.lib.openai_agents.tools.rest_api.create_rest_api_tool",
        lambda **kwargs: ("rest", kwargs),
    )
    monkeypatch.setattr(
        "src.lib.openai_agents.tools.file_output_tools.create_csv_tool",
        lambda: "csv-tool",
    )
    monkeypatch.setattr(
        "src.lib.openai_agents.tools.file_output_tools.create_tsv_tool",
        lambda: "tsv-tool",
    )
    monkeypatch.setattr(
        "src.lib.openai_agents.tools.file_output_tools.create_json_tool",
        lambda: "json-tool",
    )

    ctx = catalog_service.ToolExecutionContext(document_id="d1", user_id="u1", database_url="postgres://db")
    assert catalog_service._resolve_search_document_tool(ctx)[0] == "search"
    assert catalog_service._resolve_read_section_tool(ctx)[0] == "section"
    assert catalog_service._resolve_read_subsection_tool(ctx)[0] == "subsection"
    assert catalog_service._resolve_curation_db_sql_tool(ctx)[0] == "sql"
    assert catalog_service._resolve_chebi_api_tool(ctx)[0] == "rest"
    assert catalog_service._resolve_quickgo_api_tool(ctx)[0] == "rest"
    assert catalog_service._resolve_go_api_tool(ctx)[0] == "rest"
    assert catalog_service._resolve_alliance_api_tool(ctx)[0] == "rest"
    assert catalog_service._resolve_save_csv_tool(ctx) == "csv-tool"
    assert catalog_service._resolve_save_tsv_tool(ctx) == "tsv-tool"
    assert catalog_service._resolve_save_json_tool(ctx) == "json-tool"

    monkeypatch.setattr(
        catalog_service,
        "TOOL_BINDINGS",
        {"x": {"binding": "static", "required_context": [], "resolver": "not-callable"}},
    )
    with pytest.raises(ValueError, match="invalid binding resolver"):
        catalog_service.resolve_tools(["x"], catalog_service.ToolExecutionContext())

    monkeypatch.setattr(
        catalog_service,
        "TOOL_BINDINGS",
        {"x": {"binding": "static", "required_context": [], "resolver": lambda _ctx: None}},
    )
    with pytest.raises(ValueError, match="returned no tool instance"):
        catalog_service.resolve_tools(["x"], catalog_service.ToolExecutionContext())


def test_tool_lookup_and_expansion_helpers(monkeypatch):
    monkeypatch.setattr(
        catalog_service,
        "METHOD_TOOL_ENTRIES",
        {"search_genes": {"parent_tool": "agr_curation_query", "name": "Search genes"}},
    )
    monkeypatch.setattr(
        catalog_service,
        "TOOL_REGISTRY",
        {
            "agr_curation_query": {
                "name": "AGR",
                "methods": {"search_genes": {"name": "Search genes"}},
                "agent_methods": {"gene": {"methods": ["search_genes"]}},
            },
            "search_document": {"name": "Search document"},
        },
    )

    assert catalog_service._canonical_tool_ids(["search_genes", "agr_curation_query"]) == ["agr_curation_query"]
    assert catalog_service._uses_document_tools(["search_document"]) is True
    assert catalog_service._uses_document_tools(["agr_curation_query"]) is False

    expanded = catalog_service.expand_tools_for_agent("gene", ["agr_curation_query", "unknown_tool"])
    assert expanded == ["search_genes", "unknown_tool"]

    assert catalog_service.get_tool_details("agr_curation_query")["name"] == "AGR"
    assert catalog_service.get_tool_details("search_genes")["name"] == "Search genes"
    assert catalog_service.get_tool_details("missing") is None

    all_tools = catalog_service.get_all_tools()
    assert "agr_curation_query" in all_tools and "search_genes" in all_tools

    assert catalog_service.get_tool_for_agent("search_genes", "gene")["name"] == "Search genes"
    context_tool = catalog_service.get_tool_for_agent("agr_curation_query", "gene")
    assert context_tool["agent_context"]["methods"] == ["search_genes"]
    assert "search_genes" in context_tool["relevant_methods"]
    assert catalog_service.get_tool_for_agent("missing", "gene") is None


def test_build_catalog_and_service_branches(monkeypatch):
    from src.lib.prompts import cache as prompt_cache

    monkeypatch.setattr(prompt_cache, "is_initialized", lambda: False)
    empty_catalog = catalog_service._build_catalog()
    assert empty_catalog.total_agents == 0

    monkeypatch.setattr(prompt_cache, "is_initialized", lambda: True)
    monkeypatch.setattr(prompt_cache, "get_all_active_prompts", lambda: {"bad-key": object()})
    monkeypatch.setattr(
        catalog_service,
        "AGENT_REGISTRY",
        {
            "task_input": {
                "name": "Initial Instructions",
                "description": "Start",
                "category": "Routing",
                "tools": [],
                "frontend": {"show_in_palette": False},
            },
            "gene": {
                "name": "Gene Specialist",
                "description": "Gene",
                "category": "Validation",
                "tools": [],
            },
        },
    )
    monkeypatch.setattr(catalog_service, "expand_tools_for_agent", lambda _agent, _tools: [])

    built = catalog_service._build_catalog()
    assert built.total_agents == 1
    assert built.categories[0].agents[0].agent_id == "task_input"
    assert built.categories[0].agents[0].show_in_palette is False

    service = catalog_service.PromptCatalogService()
    monkeypatch.setattr(catalog_service, "_build_catalog", lambda: built)
    assert service.catalog.total_agents == 1
    assert service.refresh().total_agents == 1
    assert service.get_agent("missing") is None
    assert service.get_agents_by_category("missing") == []

    fake_agent = SimpleNamespace(
        base_prompt="BASE",
        has_group_rules=True,
        group_rules={"WB": SimpleNamespace(content="WB rules")},
    )
    monkeypatch.setattr(service, "get_agent", lambda _agent_id: fake_agent)
    combined = service.get_combined_prompt("gene", "WB")
    assert "GROUP-SPECIFIC RULES" in combined
    assert "WB rules" in combined
    assert service.get_combined_prompt("gene", "FB") == "BASE"
    monkeypatch.setattr(service, "get_agent", lambda _agent_id: None)
    assert service.get_combined_prompt("missing", "WB") is None


def test_singleton_context_and_runtime_helpers(monkeypatch):
    monkeypatch.setattr(catalog_service, "_catalog_service", None)
    first = catalog_service.get_prompt_catalog()
    second = catalog_service.get_prompt_catalog()
    assert first is second

    assert catalog_service._coerce_db_user_id(7) == 7
    assert catalog_service._coerce_db_user_id(" 42 ") == 42
    assert catalog_service._coerce_db_user_id("abc") is None

    monkeypatch.delenv("CURATION_DB_URL", raising=False)
    ctx = catalog_service._build_tool_execution_context({"document_id": 10, "user_id": 5})
    assert ctx.document_id == "10"
    assert ctx.user_id == "5"
    assert ctx.database_url is None

    monkeypatch.setenv("CURATION_DB_URL", "postgres://env")
    ctx_env = catalog_service._build_tool_execution_context({})
    assert ctx_env.database_url == "postgres://env"
    ctx_kw = catalog_service._build_tool_execution_context({"database_url": " postgres://kw "})
    assert ctx_kw.database_url == "postgres://kw"


def test_group_rules_runtime_and_agent_lookup_paths(monkeypatch):
    fake_cache_module = SimpleNamespace(
        get_prompt_optional=lambda _component, prompt_type, group_id=None: (
            SimpleNamespace(content="group rule")
            if prompt_type == "group_rules" and group_id == "WB"
            else None
        )
    )
    monkeypatch.setitem(sys.modules, "src.lib.prompts.cache", fake_cache_module)

    injected = catalog_service._inject_group_rules_with_overrides(
        base_prompt="BASE\n## GROUP-SPECIFIC RULES",
        group_ids=[" wb "],
        component_name="gene",
        group_overrides={"WB": "override rule"},
    )
    assert "override rule" in injected

    no_groups = catalog_service._inject_group_rules_with_overrides(
        base_prompt="BASE",
        group_ids=[],
        component_name="gene",
    )
    assert no_groups == "BASE"

    fallback = catalog_service._inject_group_rules_with_overrides(
        base_prompt="BASE",
        group_ids=["WB"],
        component_name="gene",
        group_overrides={},
    )
    assert "group rule" in fallback

    from src.lib.openai_agents import prompt_utils
    monkeypatch.setattr(prompt_utils, "format_document_context_for_prompt", lambda **_kwargs: ("\nCTX", {}))
    monkeypatch.setattr(prompt_utils, "inject_structured_output_instruction", lambda text, output_type: text + f"\nSCHEMA:{output_type}")
    monkeypatch.setattr(catalog_service, "_inject_group_rules_with_overrides", lambda **kwargs: kwargs["base_prompt"] + "\nRULES")

    db_agent = SimpleNamespace(
        agent_key="gene",
        instructions="BASE",
        group_rules_enabled=True,
        group_rules_component="gene",
        template_source=None,
        mod_prompt_overrides={},
    )
    runtime_text = catalog_service._build_runtime_instructions(
        db_agent=db_agent,
        runtime_kwargs={
            "active_groups": ["WB"],
            "hierarchy": {},
            "sections": [],
            "abstract": None,
            "document_name": "Paper A",
        },
        output_schema="SchemaX",
        canonical_tool_ids=["search_document"],
    )
    assert runtime_text.startswith('You are helping the user with the document: "Paper A"')
    assert "RULES" in runtime_text
    assert "SCHEMA:SchemaX" in runtime_text

    monkeypatch.setattr(
        catalog_service,
        "_inject_group_rules_with_overrides",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("inject failed")),
    )
    with pytest.raises(ValueError, match="Failed group-rules injection"):
        catalog_service._build_runtime_instructions(
            db_agent=db_agent,
            runtime_kwargs={"active_groups": ["WB"]},
            output_schema=None,
            canonical_tool_ids=[],
        )

    import src.lib.openai_agents as openai_agents_pkg

    monkeypatch.setattr(openai_agents_pkg, "models", SimpleNamespace(MySchema=object()), raising=False)
    assert catalog_service._resolve_output_schema("MySchema") is not None
    monkeypatch.setattr(openai_agents_pkg, "models", SimpleNamespace(), raising=False)
    assert catalog_service._resolve_output_schema("Missing") is None

    fake_db = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr("src.models.sql.database.SessionLocal", lambda: fake_db)
    monkeypatch.setattr(
        "src.lib.agent_studio.agent_service.get_agent_by_key",
        lambda _db, _agent_id, user_id=None: (_ for _ in ()).throw(RuntimeError("db fail")),
    )
    assert catalog_service._get_db_agent_row("gene", {}) is None

    monkeypatch.setattr(catalog_service, "_get_db_agent_row", lambda _aid, _kwargs: SimpleNamespace())
    monkeypatch.setattr(catalog_service, "_create_db_agent", lambda _row, **_kwargs: None)
    with pytest.raises(ValueError, match="could not be built"):
        catalog_service.get_agent_by_id("gene")

    monkeypatch.setattr(catalog_service, "_get_db_agent_row", lambda _aid, _kwargs: None)
    assert catalog_service.get_agent_metadata("task_input")["display_name"] == "Initial Instructions"
    with pytest.raises(ValueError, match="Unknown agent_id"):
        catalog_service.get_agent_metadata("missing")


def test_list_available_agents_filters_invalid_metadata(monkeypatch):
    fake_query = SimpleNamespace(
        filter=lambda *_args, **_kwargs: SimpleNamespace(all=lambda: [("gene",), ("bad",)]),
    )
    fake_db = SimpleNamespace(query=lambda *_args, **_kwargs: fake_query, close=lambda: None)
    monkeypatch.setattr("src.models.sql.database.SessionLocal", lambda: fake_db)

    def _meta(agent_id, **_kwargs):
        if agent_id == "bad":
            raise ValueError("skip")
        return {"agent_id": agent_id}

    monkeypatch.setattr(catalog_service, "get_agent_metadata", _meta)

    listed = catalog_service.list_available_agents(db_user_id=7)
    assert listed == [{"agent_id": "gene"}]


def test_create_db_agent_output_schema_and_reasoning_paths(monkeypatch):
    fake_row = SimpleNamespace(
        id="agent-id",
        agent_key="gene",
        instructions="BASE",
        mod_prompt_overrides={},
        group_rules_enabled=False,
        template_source=None,
        model_id="gpt-4o",
        model_temperature=0.1,
        model_reasoning="invalid-level",
        output_schema_key="MissingSchema",
        tool_ids=[],
        name="Gene",
    )
    monkeypatch.setattr(catalog_service, "_resolve_output_schema", lambda _key: None)
    with pytest.raises(ValueError, match="Unknown output schema"):
        catalog_service._create_db_agent(fake_row)

    fake_row.output_schema_key = None
    fake_row.tool_ids = ["save_csv_file"]
    fake_row.model_reasoning = "high"

    from src.lib.openai_agents import config as agent_config
    captured = {}
    monkeypatch.setattr(agent_config, "resolve_model_provider", lambda _model_id: "openai")
    monkeypatch.setattr(agent_config, "get_model_for_agent", lambda _model_id, **_kwargs: "model")
    monkeypatch.setattr(agent_config, "build_model_settings", lambda **kwargs: captured.setdefault("settings", kwargs) or kwargs)
    monkeypatch.setattr(catalog_service, "resolve_tools", lambda _tool_ids, _ctx: ["csv-tool"])
    monkeypatch.setattr(catalog_service, "_build_runtime_instructions", lambda **_kwargs: "INSTR")
    monkeypatch.setattr(catalog_service, "Agent", lambda **kwargs: SimpleNamespace(**kwargs))

    built = catalog_service._create_db_agent(fake_row)
    assert built.tools == ["csv-tool"]
    assert captured["settings"]["reasoning_effort"] is None
    assert captured["settings"]["parallel_tool_calls"] is False
