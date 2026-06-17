"""Additional branch tests for catalog_service helpers and service methods."""

from types import SimpleNamespace

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


def test_get_tool_registry_propagates_package_tool_instantiation_errors(monkeypatch):
    fake_good = SimpleNamespace(params_json_schema={}, description="desc")
    binding_good = SimpleNamespace(
        tool_id="search_document",
        description="Search docs",
        metadata={},
        required_context=("document_id", "user_id"),
        binding_kind=SimpleNamespace(value="context_factory"),
        source=SimpleNamespace(
            package_id="agr.alliance",
            package_version="1.0.0",
            package_display_name="Alliance Defaults",
            export_name="default",
            source_file="packages/alliance/python/src/agr_ai_curation_alliance/tools/documents.py",
        ),
    )
    binding_bad = SimpleNamespace(
        tool_id="broken_tool",
        description="Broken tool",
        metadata={},
        required_context=(),
        binding_kind=SimpleNamespace(value="static"),
        source=SimpleNamespace(
            package_id="agr.alliance",
            package_version="1.0.0",
            package_display_name="Alliance Defaults",
            export_name="default",
            source_file="packages/alliance/python/src/agr_ai_curation_alliance/tools/broken.py",
        ),
    )
    monkeypatch.setattr(
        catalog_service,
        "_load_package_tool_registry",
        lambda: SimpleNamespace(bindings=(binding_good, binding_bad)),
    )
    monkeypatch.setattr(
        catalog_service,
        "_instantiate_package_tool",
        lambda binding, execution_context=None: (
            (_ for _ in ()).throw(RuntimeError("boom"))
            if binding.tool_id == "broken_tool"
            else fake_good
        ),
    )

    from src.lib.agent_studio import tool_introspection

    def _fake_introspect(obj):
        return SimpleNamespace(
            name="search_document",
            description="Search docs",
            parameters={},
            source_file="x.py",
        )

    monkeypatch.setattr(tool_introspection, "introspect_tool", _fake_introspect)

    catalog_service.clear_package_tool_runtime_caches()
    with pytest.raises(RuntimeError, match="boom"):
        catalog_service.get_tool_registry()
    catalog_service.clear_package_tool_runtime_caches()


def test_tool_registry_is_lazy_and_cache_resettable(monkeypatch):
    call_counter = {"count": 0}
    fake_binding = SimpleNamespace(
        tool_id="search_document",
        description="Search docs",
        metadata={},
        required_context=("document_id", "user_id"),
        binding_kind=SimpleNamespace(value="context_factory"),
        source=SimpleNamespace(
            package_id="agr.alliance",
            package_version="1.0.0",
            package_display_name="Alliance Defaults",
            export_name="default",
            source_file="packages/alliance/python/src/agr_ai_curation_alliance/tools/documents.py",
        ),
    )

    def _fake_load_registry():
        call_counter["count"] += 1
        return SimpleNamespace(bindings=(fake_binding,))

    monkeypatch.setattr(catalog_service, "_load_package_tool_registry", _fake_load_registry)
    monkeypatch.setattr(
        catalog_service,
        "_instantiate_package_tool",
        lambda binding, execution_context=None: SimpleNamespace(params_json_schema={}, description="desc"),
    )

    from src.lib.agent_studio import tool_introspection

    monkeypatch.setattr(
        tool_introspection,
        "introspect_tool",
        lambda _obj: SimpleNamespace(
            name="search_document",
            description="Search docs",
            parameters={},
            source_file="x.py",
        ),
    )

    catalog_service.clear_package_tool_runtime_caches()
    assert call_counter["count"] == 0
    assert "search_document" in catalog_service.TOOL_REGISTRY
    assert call_counter["count"] == 1
    assert "search_document" in catalog_service.TOOL_REGISTRY
    assert call_counter["count"] == 1

    catalog_service.clear_package_tool_runtime_caches()
    assert "search_document" in catalog_service.TOOL_REGISTRY
    assert call_counter["count"] == 2
    catalog_service.clear_package_tool_runtime_caches()


def test_resolve_tools_error_paths(monkeypatch):
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

    fake_bundle = SimpleNamespace(
        render=lambda: "BASE\n\nWB rules",
    )
    monkeypatch.setattr(
        service,
        "get_effective_prompt_bundle",
        lambda agent_id, group_id=None: (
            fake_bundle if agent_id == "gene" and group_id == "WB" else None
        ),
    )
    combined = service.get_combined_prompt("gene", "WB")
    assert "WB rules" in combined
    assert service.get_combined_prompt("gene", "FB") is None
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
    from src.lib.openai_agents import prompt_utils
    monkeypatch.setattr(prompt_utils, "format_document_context_for_prompt", lambda **_kwargs: ("\nCTX", {}))
    captured_assembly = {}

    def _fake_build_agent_prompt_layers(agent_id, **kwargs):
        kwargs["agent_id"] = agent_id
        captured_assembly.update(kwargs)
        runtime_context = str(kwargs.get("runtime_context") or "")
        return SimpleNamespace(
            render=lambda: runtime_context,
            hash="hash-runtime",
            to_manifest=lambda: {"agent_id": kwargs["agent_id"], "layers": [], "hash": "hash-runtime"},
        )

    monkeypatch.setattr(catalog_service, "build_agent_prompt_layers", _fake_build_agent_prompt_layers)

    db_agent = SimpleNamespace(
        agent_key="gene",
        instructions="BASE",
        visibility="system",
        group_rules_enabled=True,
        group_rules_component="gene",
        template_source=None,
        group_prompt_overrides={},
    )
    runtime_bundle = catalog_service._build_runtime_instructions(
        db_agent=db_agent,
        runtime_kwargs={
            "active_groups": ["WB"],
            "hierarchy": {},
            "sections": [],
            "abstract": None,
            "document_name": "Paper A",
        },
        canonical_tool_ids=["search_document", "record_evidence"],
    )
    runtime_text = runtime_bundle.render()
    assert captured_assembly["agent_id"] == "gene"
    assert captured_assembly["group_id"] == ["WB"]
    assert "CTX" in captured_assembly["runtime_context"]
    assert runtime_text.startswith('You are helping the user with the document: "Paper A"')
    assert "Call `record_evidence` once for each distinct evidence unit you intend to keep." in runtime_text

    runtime_text_with_evidence = catalog_service._build_runtime_instructions(
        db_agent=db_agent,
        runtime_kwargs={
            "active_groups": ["WB"],
            "hierarchy": {},
            "sections": [],
            "abstract": None,
            "document_name": "Paper A",
        },
        canonical_tool_ids=["search_document", "record_evidence"],
    ).render()
    assert "Use multiple evidence records when one evidence unit alone does not fully support" in runtime_text_with_evidence
    assert "Pass the entity label and `span_ids`; do not write source evidence text yourself." in runtime_text_with_evidence
    assert "Source quote, source span IDs, source fragments, chunk IDs, page, and section provenance are backend-owned" in runtime_text_with_evidence

    formatter_runtime_text = catalog_service._build_runtime_instructions(
        db_agent=db_agent,
        runtime_kwargs={
            "active_groups": ["WB"],
            "document_name": "Smith et al. (2024).pdf",
        },
        canonical_tool_ids=["finalize_and_save"],
    ).render()
    assert formatter_runtime_text.startswith(
        'Use "Smith_et_al_2024" as the filename_hint when calling finalize_and_save unless the user explicitly requests a different filename.'
    )
    assert 'You are helping the user with the document: "Smith et al. (2024).pdf"' not in formatter_runtime_text

    formatter_runtime_text_with_invalid_filename = catalog_service._build_runtime_instructions(
        db_agent=db_agent,
        runtime_kwargs={
            "active_groups": ["WB"],
            "document_name": "().pdf",
        },
        canonical_tool_ids=["finalize_and_save"],
    ).render()
    assert formatter_runtime_text_with_invalid_filename.startswith(
        'Use "output" as the filename_hint when calling finalize_and_save unless the user explicitly requests a different filename.'
    )
    with pytest.raises(TypeError, match="list items must be strings"):
        catalog_service._additional_runtime_contexts({"additional_runtime_context": ["ok", 7]})

    import src.lib.config.schema_discovery as schema_discovery

    monkeypatch.setattr(
        schema_discovery,
        "resolve_output_schema",
        lambda schema_key: object() if schema_key == "MySchema" else None,
    )
    assert catalog_service._resolve_output_schema("MySchema") is not None
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
    fake_row.tool_ids = ["finalize_and_save"]
    fake_row.model_reasoning = "high"

    from src.lib.openai_agents import config as agent_config
    captured = {}
    monkeypatch.setattr(agent_config, "resolve_model_provider", lambda _model_id: "openai")
    monkeypatch.setattr(agent_config, "get_model_for_agent", lambda _model_id, **_kwargs: "model")
    monkeypatch.setattr(agent_config, "build_model_settings", lambda **kwargs: captured.setdefault("settings", kwargs) or kwargs)
    monkeypatch.setattr(catalog_service, "resolve_tools", lambda _tool_ids, _ctx: ["csv-tool"])
    monkeypatch.setattr(
        catalog_service,
        "_build_runtime_instructions",
        lambda **_kwargs: SimpleNamespace(
            render=lambda: "INSTR",
            hash="hash-1",
            to_manifest=lambda: {"agent_id": "gene", "layers": [], "hash": "hash-1"},
        ),
    )
    monkeypatch.setattr(catalog_service, "prompt_templates_for_bundle", lambda _bundle: [])
    monkeypatch.setattr(catalog_service, "Agent", lambda **kwargs: SimpleNamespace(**kwargs))

    built = catalog_service._create_db_agent(fake_row)
    assert built.tools == ["csv-tool"]
    assert captured["settings"]["reasoning_effort"] == "high"
    assert captured["settings"]["parallel_tool_calls"] is False


def test_create_db_agent_uses_domain_extraction_schema_directly(monkeypatch):
    from src.lib.config import schema_discovery

    fake_row = SimpleNamespace(
        id="agent-id",
        agent_key="gene_extractor",
        visibility="system",
        instructions="BASE",
        mod_prompt_overrides={},
        group_rules_enabled=False,
        template_source=None,
        model_id="gpt-4o",
        model_temperature=0.1,
        model_reasoning="medium",
        output_schema_key="GeneExtractionResultEnvelope",
        tool_ids=[],
        name="Gene Extractor",
    )

    from src.lib.openai_agents import config as agent_config

    monkeypatch.setattr(agent_config, "resolve_model_provider", lambda _model_id: "openai")
    monkeypatch.setattr(agent_config, "get_model_for_agent", lambda _model_id, **_kwargs: "model")
    monkeypatch.setattr(agent_config, "build_model_settings", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        catalog_service,
        "_build_runtime_instructions",
        lambda **_kwargs: SimpleNamespace(
            render=lambda: "INSTR",
            hash="hash-1",
            to_manifest=lambda: {"agent_id": "gene_extractor", "layers": [], "hash": "hash-1"},
        ),
    )
    monkeypatch.setattr(catalog_service, "prompt_templates_for_bundle", lambda _bundle: [])
    monkeypatch.setattr(catalog_service, "Agent", lambda **kwargs: SimpleNamespace(**kwargs))

    built = catalog_service._create_db_agent(fake_row)
    canonical_schema = schema_discovery.get_agent_schema("GeneExtractionResultEnvelope")

    assert built.instructions == "INSTR"
    assert built.output_type is canonical_schema


def test_create_db_agent_attaches_structured_finalization_metadata(monkeypatch):
    fake_schema = object()
    fake_row = SimpleNamespace(
        id="agent-id",
        agent_key="gene",
        visibility="system",
        instructions="BASE",
        mod_prompt_overrides={},
        group_rules_enabled=False,
        template_source=None,
        group_rules_component=None,
        model_id="gpt-4o",
        model_temperature=0.1,
        model_reasoning="medium",
        output_schema_key="GeneResultEnvelope",
        tool_ids=[],
        name="Gene",
    )

    from src.lib.openai_agents import config as agent_config

    monkeypatch.setattr(catalog_service, "_resolve_output_schema", lambda _key: fake_schema)
    monkeypatch.setattr(agent_config, "resolve_model_provider", lambda _model_id: "openai")
    monkeypatch.setattr(agent_config, "get_model_for_agent", lambda _model_id, **_kwargs: "model")
    monkeypatch.setattr(agent_config, "build_model_settings", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        catalog_service,
        "_build_runtime_instructions",
        lambda **_kwargs: SimpleNamespace(
            render=lambda: "INSTR",
            hash="hash-1",
            to_manifest=lambda: {"agent_id": "gene", "layers": [], "hash": "hash-1"},
        ),
    )
    monkeypatch.setattr(catalog_service, "prompt_templates_for_bundle", lambda _bundle: [])
    monkeypatch.setattr(catalog_service, "Agent", lambda **kwargs: SimpleNamespace(**kwargs))

    built = catalog_service._create_db_agent(fake_row)

    assert built.output_type is fake_schema
    assert built.structured_finalization["tool_name"] == "finalize_gene_lookup"


def test_create_db_agent_attaches_inherited_curation_metadata_for_custom_template(monkeypatch):
    fake_row = SimpleNamespace(
        id="agent-id",
        agent_key="ca_custom_gene_extractor",
        visibility="private",
        instructions="CUSTOM",
        mod_prompt_overrides={},
        group_rules_enabled=False,
        template_source="gene_extractor",
        group_rules_component="gene_extractor",
        model_id="gpt-4o",
        model_temperature=0.1,
        model_reasoning="low",
        output_schema_key=None,
        tool_ids=["search_document", "finalize_gene_extraction"],
        name="Custom Gene Extractor",
    )
    fake_parent_definition = SimpleNamespace(
        curation=SimpleNamespace(adapter_key="gene", launchable=True),
        structured_finalization={"tool_name": "finalize_gene_extraction"},
    )

    from src.lib.openai_agents import config as agent_config

    monkeypatch.setattr(catalog_service, "_resolve_output_schema", lambda _key: None)
    monkeypatch.setattr(catalog_service, "resolve_tools", lambda _tool_ids, _ctx: [])
    monkeypatch.setattr(agent_config, "resolve_model_provider", lambda _model_id: "openai")
    monkeypatch.setattr(agent_config, "get_model_for_agent", lambda _model_id, **_kwargs: "model")
    monkeypatch.setattr(agent_config, "build_model_settings", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        catalog_service,
        "_build_runtime_instructions",
        lambda **_kwargs: SimpleNamespace(
            render=lambda: "INSTR",
            hash="hash-1",
            to_manifest=lambda: {
                "agent_id": "ca_custom_gene_extractor",
                "layers": [],
                "hash": "hash-1",
            },
        ),
    )
    monkeypatch.setattr(catalog_service, "prompt_templates_for_bundle", lambda _bundle: [])
    monkeypatch.setattr(catalog_service, "Agent", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(catalog_service, "resolve_tools", lambda _tool_ids, _ctx: [])
    monkeypatch.setattr(
        catalog_service,
        "get_agent_definition",
        lambda agent_id: fake_parent_definition if agent_id == "gene_extractor" else None,
    )
    monkeypatch.setattr(catalog_service, "get_agent_by_folder", lambda _agent_id: None)

    built = catalog_service._create_db_agent(fake_row, db_user_id=17)

    assert built is not None
    assert built.agent_key == "ca_custom_gene_extractor"
    assert built.curation_metadata == {"adapter_key": "gene", "launchable": True}
    assert built.curation == {"adapter_key": "gene", "launchable": True}


def test_create_db_agent_inherits_when_custom_definition_is_not_launchable(monkeypatch):
    fake_row = SimpleNamespace(
        id="agent-id",
        agent_key="ca_custom_gene_extractor",
        visibility="private",
        instructions="CUSTOM",
        mod_prompt_overrides={},
        group_rules_enabled=False,
        template_source="gene_extractor",
        group_rules_component="gene_extractor",
        model_id="gpt-4o",
        model_temperature=0.1,
        model_reasoning="low",
        output_schema_key=None,
        tool_ids=["search_document", "finalize_gene_extraction"],
        name="Custom Gene Extractor",
    )
    fake_custom_definition = SimpleNamespace(
        curation=SimpleNamespace(adapter_key=None, launchable=False),
        structured_finalization=None,
    )
    fake_parent_definition = SimpleNamespace(
        curation=SimpleNamespace(adapter_key="gene", launchable=True),
        structured_finalization={"tool_name": "finalize_gene_extraction"},
    )

    from src.lib.openai_agents import config as agent_config

    monkeypatch.setattr(catalog_service, "_resolve_output_schema", lambda _key: None)
    monkeypatch.setattr(agent_config, "resolve_model_provider", lambda _model_id: "openai")
    monkeypatch.setattr(agent_config, "get_model_for_agent", lambda _model_id, **_kwargs: "model")
    monkeypatch.setattr(agent_config, "build_model_settings", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        catalog_service,
        "_build_runtime_instructions",
        lambda **_kwargs: SimpleNamespace(
            render=lambda: "INSTR",
            hash="hash-1",
            to_manifest=lambda: {
                "agent_id": "ca_custom_gene_extractor",
                "layers": [],
                "hash": "hash-1",
            },
        ),
    )
    monkeypatch.setattr(catalog_service, "prompt_templates_for_bundle", lambda _bundle: [])
    monkeypatch.setattr(catalog_service, "Agent", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(catalog_service, "resolve_tools", lambda _tool_ids, _ctx: [])
    monkeypatch.setattr(
        catalog_service,
        "get_agent_definition",
        lambda agent_id: {
            "ca_custom_gene_extractor": fake_custom_definition,
            "gene_extractor": fake_parent_definition,
        }.get(agent_id),
    )
    monkeypatch.setattr(catalog_service, "get_agent_by_folder", lambda _agent_id: None)

    built = catalog_service._create_db_agent(fake_row, db_user_id=17)

    assert built is not None
    assert built.curation_metadata == {"adapter_key": "gene", "launchable": True}
    assert built.curation == {"adapter_key": "gene", "launchable": True}


def test_create_db_agent_does_not_attach_curation_metadata_without_finalizer_tool(monkeypatch):
    fake_row = SimpleNamespace(
        id="agent-id",
        agent_key="ca_repurposed_gene_extractor",
        visibility="private",
        instructions="CUSTOM",
        mod_prompt_overrides={},
        group_rules_enabled=False,
        template_source="gene_extractor",
        group_rules_component="gene_extractor",
        model_id="gpt-4o",
        model_temperature=0.1,
        model_reasoning="low",
        output_schema_key=None,
        tool_ids=["search_document"],
        name="Repurposed Gene Extractor",
    )
    fake_parent_definition = SimpleNamespace(
        curation=SimpleNamespace(adapter_key="gene", launchable=True),
        structured_finalization={"tool_name": "finalize_gene_extraction"},
    )

    from src.lib.openai_agents import config as agent_config

    monkeypatch.setattr(catalog_service, "_resolve_output_schema", lambda _key: None)
    monkeypatch.setattr(agent_config, "resolve_model_provider", lambda _model_id: "openai")
    monkeypatch.setattr(agent_config, "get_model_for_agent", lambda _model_id, **_kwargs: "model")
    monkeypatch.setattr(agent_config, "build_model_settings", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        catalog_service,
        "_build_runtime_instructions",
        lambda **_kwargs: SimpleNamespace(
            render=lambda: "INSTR",
            hash="hash-1",
            to_manifest=lambda: {
                "agent_id": "ca_repurposed_gene_extractor",
                "layers": [],
                "hash": "hash-1",
            },
        ),
    )
    monkeypatch.setattr(catalog_service, "prompt_templates_for_bundle", lambda _bundle: [])
    monkeypatch.setattr(catalog_service, "Agent", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(catalog_service, "resolve_tools", lambda _tool_ids, _ctx: [])
    monkeypatch.setattr(
        catalog_service,
        "get_agent_definition",
        lambda agent_id: fake_parent_definition if agent_id == "gene_extractor" else None,
    )
    monkeypatch.setattr(catalog_service, "get_agent_by_folder", lambda _agent_id: None)

    built = catalog_service._create_db_agent(fake_row, db_user_id=17)

    assert built is not None
    assert not hasattr(built, "curation_metadata")
    assert not hasattr(built, "curation")


def test_create_db_agent_does_not_inherit_curation_without_parent_adapter_key(monkeypatch):
    fake_row = SimpleNamespace(
        id="agent-id",
        agent_key="ca_gene_extractor_missing_adapter",
        visibility="private",
        instructions="CUSTOM",
        mod_prompt_overrides={},
        group_rules_enabled=False,
        template_source="gene_extractor",
        group_rules_component="gene_extractor",
        model_id="gpt-4o",
        model_temperature=0.1,
        model_reasoning="low",
        output_schema_key=None,
        tool_ids=["search_document", "finalize_gene_extraction"],
        name="Custom Gene Extractor",
    )
    fake_parent_definition = SimpleNamespace(
        curation=SimpleNamespace(adapter_key="", launchable=True),
    )

    from src.lib.openai_agents import config as agent_config

    monkeypatch.setattr(catalog_service, "_resolve_output_schema", lambda _key: None)
    monkeypatch.setattr(agent_config, "resolve_model_provider", lambda _model_id: "openai")
    monkeypatch.setattr(agent_config, "get_model_for_agent", lambda _model_id, **_kwargs: "model")
    monkeypatch.setattr(agent_config, "build_model_settings", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        catalog_service,
        "_build_runtime_instructions",
        lambda **_kwargs: SimpleNamespace(
            render=lambda: "INSTR",
            hash="hash-1",
            to_manifest=lambda: {
                "agent_id": "ca_gene_extractor_missing_adapter",
                "layers": [],
                "hash": "hash-1",
            },
        ),
    )
    monkeypatch.setattr(catalog_service, "prompt_templates_for_bundle", lambda _bundle: [])
    monkeypatch.setattr(catalog_service, "Agent", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(catalog_service, "resolve_tools", lambda _tool_ids, _ctx: [])
    monkeypatch.setattr(
        catalog_service,
        "get_agent_definition",
        lambda agent_id: fake_parent_definition if agent_id == "gene_extractor" else None,
    )
    monkeypatch.setattr(catalog_service, "get_agent_by_folder", lambda _agent_id: None)

    built = catalog_service._create_db_agent(fake_row, db_user_id=17)

    assert built is not None
    assert not hasattr(built, "curation_metadata")
    assert not hasattr(built, "curation")


def test_create_db_agent_applies_model_overrides(monkeypatch):
    fake_row = SimpleNamespace(
        id="agent-id",
        agent_key="gene",
        instructions="BASE",
        mod_prompt_overrides={},
        group_rules_enabled=False,
        template_source=None,
        model_id="gpt-5.5",
        model_temperature=0.3,
        model_reasoning="medium",
        output_schema_key=None,
        tool_ids=[],
        name="Gene",
    )

    from src.lib.openai_agents import config as agent_config

    captured = {}
    monkeypatch.setattr(agent_config, "resolve_model_provider", lambda _model_id: "openai")
    monkeypatch.setattr(
        agent_config,
        "get_model_for_agent",
        lambda model_id, **_kwargs: captured.setdefault("model_id", model_id) or model_id,
    )
    monkeypatch.setattr(
        agent_config,
        "build_model_settings",
        lambda **kwargs: captured.setdefault("settings", kwargs) or kwargs,
    )
    monkeypatch.setattr(
        catalog_service,
        "_build_runtime_instructions",
        lambda **_kwargs: SimpleNamespace(
            render=lambda: "INSTR",
            hash="hash-1",
            to_manifest=lambda: {"agent_id": "gene", "layers": [], "hash": "hash-1"},
        ),
    )
    monkeypatch.setattr(catalog_service, "prompt_templates_for_bundle", lambda _bundle: [])
    monkeypatch.setattr(catalog_service, "Agent", lambda **kwargs: SimpleNamespace(**kwargs))

    built = catalog_service._create_db_agent(
        fake_row,
        model_id_override="gpt-5.4-mini",
        model_temperature_override=0.0,
        model_reasoning_override="minimal",
    )

    assert built.model == "gpt-5.4-mini"
    assert captured["settings"]["model"] == "gpt-5.4-mini"
    assert captured["settings"]["temperature"] == 0.0
    assert captured["settings"]["reasoning_effort"] == "minimal"
