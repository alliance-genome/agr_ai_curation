"""Runtime-focused tests for supervisor agent helpers."""

import json
from types import SimpleNamespace

import pytest

from src.lib.openai_agents.agents import supervisor_agent


class _Field:
    def __eq__(self, _other):
        return True

    def asc(self):
        return self


class _FakeAgentRecord:
    visibility = _Field()
    is_active = _Field()
    supervisor_enabled = _Field()
    agent_key = _Field()


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
        self.filtered = False
        self.ordered = False

    def filter(self, *_args, **_kwargs):
        self.filtered = True
        return self

    def order_by(self, *_args, **_kwargs):
        self.ordered = True
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.closed = False
        self.last_query = None

    def query(self, _model):
        self.last_query = _FakeQuery(self._rows)
        return self.last_query

    def close(self):
        self.closed = True


class _PrepExtractionRecord:
    def __init__(self, **overrides):
        payload = {
            "extraction_result_id": "extract-1",
            "document_id": "document-1",
            "adapter_key": "reference_adapter",
            "profile_key": None,
            "domain_key": "disease",
            "agent_key": "disease_extractor",
            "source_kind": "chat",
            "origin_session_id": "session-1",
            "trace_id": "trace-upstream",
            "flow_run_id": None,
            "user_id": "user-1",
            "candidate_count": 1,
            "conversation_summary": "Disease extraction kept APOE-related findings.",
            "payload_json": {
                "items": [
                    {
                        "label": "APOE",
                        "entity_type": "gene",
                        "evidence": [
                            {
                                "entity": "APOE",
                                "verified_quote": "APOE was associated with the disease phenotype.",
                                "page": 3,
                                "section": "Results",
                                "subsection": "Disease association",
                                "chunk_id": "chunk-apoe-1",
                                "figure_reference": "Fig. 2",
                            }
                        ],
                    }
                ],
                "run_summary": {"candidate_count": 1},
            },
            "created_at": "2026-03-21T00:00:00Z",
            "metadata": {},
        }
        payload.update(overrides)
        self._payload = payload
        for key, value in payload.items():
            setattr(self, key, value)

    def model_dump(self, mode="python"):
        return dict(self._payload)


def test_build_model_settings_applies_reasoning_and_provider_parallel_policy(monkeypatch):
    monkeypatch.setattr("src.lib.openai_agents.config.supports_reasoning", lambda _model: True)
    monkeypatch.setattr("src.lib.openai_agents.config.supports_temperature", lambda _model: False)
    monkeypatch.setattr(
        "src.lib.openai_agents.config.resolve_model_provider",
        lambda _model, _provider_override=None: "openai",
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda _provider: SimpleNamespace(supports_parallel_tool_calls=False),
    )

    settings = supervisor_agent._build_model_settings(
        model="gpt-5-mini",
        temperature=0.7,
        reasoning_effort="high",
    )

    assert settings is not None
    assert settings.temperature is None
    assert settings.reasoning is not None
    assert settings.reasoning.effort == "high"
    assert settings.parallel_tool_calls is False


def test_build_model_settings_returns_none_when_no_overrides(monkeypatch):
    monkeypatch.setattr("src.lib.openai_agents.config.supports_reasoning", lambda _model: False)
    monkeypatch.setattr("src.lib.openai_agents.config.supports_temperature", lambda _model: True)
    monkeypatch.setattr(
        "src.lib.openai_agents.config.resolve_model_provider",
        lambda _model, _provider_override=None: "openai",
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda _provider: SimpleNamespace(supports_parallel_tool_calls=True),
    )

    settings = supervisor_agent._build_model_settings(
        model="gpt-4o",
        temperature=None,
        reasoning_effort=None,
    )

    assert settings is not None
    assert settings.temperature is None
    assert settings.reasoning is None
    assert settings.parallel_tool_calls is True


def test_build_model_settings_raises_for_unknown_provider(monkeypatch):
    monkeypatch.setattr("src.lib.openai_agents.config.supports_reasoning", lambda _model: False)
    monkeypatch.setattr("src.lib.openai_agents.config.supports_temperature", lambda _model: True)
    monkeypatch.setattr(
        "src.lib.openai_agents.config.resolve_model_provider",
        lambda _model, _provider_override=None: "missing-provider",
    )
    monkeypatch.setattr("src.lib.config.providers_loader.get_provider", lambda _provider: None)

    with pytest.raises(ValueError, match="Unknown provider_id"):
        supervisor_agent._build_model_settings(model="gpt-4o")


def test_get_supervisor_specialist_specs_builds_specs_and_skips_metadata_failures(monkeypatch):
    rows = [
        SimpleNamespace(
            agent_key="gene-extractor",
            name="Gene Extractor Agent",
            description="Fallback description",
            supervisor_description="Extract genes from paper text",
            group_rules_enabled=1,
            supervisor_batchable=1,
            supervisor_batching_entity="gene",
        ),
        SimpleNamespace(
            agent_key="broken-specialist",
            name="Broken Specialist",
            description=None,
            supervisor_description=None,
            group_rules_enabled=0,
            supervisor_batchable=0,
            supervisor_batching_entity=None,
        ),
    ]
    session = _FakeSession(rows)

    monkeypatch.setattr("src.models.sql.agent.Agent", _FakeAgentRecord)
    monkeypatch.setattr("src.models.sql.database.SessionLocal", lambda: session)

    def _metadata(agent_key):
        if agent_key == "gene-extractor":
            return {"requires_document": True}
        raise RuntimeError("metadata failure")

    monkeypatch.setattr("src.lib.agent_studio.catalog_service.get_agent_metadata", _metadata)

    specs = supervisor_agent._get_supervisor_specialist_specs()

    assert session.closed is True
    assert session.last_query is not None
    assert session.last_query.filtered is True
    assert session.last_query.ordered is True
    assert len(specs) == 1
    assert specs[0]["agent_key"] == "gene-extractor"
    assert specs[0]["tool_name"] == "ask_gene_extractor_specialist"
    assert specs[0]["description"] == "Extract genes from paper text"
    assert specs[0]["requires_document"] is True
    assert specs[0]["group_rules_enabled"] is True
    assert specs[0]["batchable"] is True
    assert specs[0]["batching_entity"] == "gene"


def test_create_dynamic_specialist_tools_skips_document_required_tools_without_document(monkeypatch):
    monkeypatch.setattr(
        supervisor_agent,
        "_get_supervisor_specialist_specs",
        lambda: [
            {
                "tool_name": "ask_pdf_specialist",
                "agent_key": "pdf_extraction",
                "description": "PDF extraction",
                "requires_document": True,
            }
        ],
    )

    calls = []
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        lambda _agent_key, **_kwargs: calls.append(_kwargs),
    )

    tools = supervisor_agent._create_dynamic_specialist_tools(document_id=None, user_id=None)

    assert tools == []
    assert calls == []


def test_create_dynamic_specialist_tools_passes_document_and_group_context(monkeypatch):
    monkeypatch.setattr(
        supervisor_agent,
        "_get_supervisor_specialist_specs",
        lambda: [
            {
                "tool_name": "ask_gene_expression_specialist",
                "agent_key": "gene-expression",
                "name": "Gene Expression Agent",
                "description": "Extract expression assertions",
                "requires_document": True,
                "group_rules_enabled": True,
            }
        ],
    )

    captured = {}

    def _get_agent_by_id(agent_key, **kwargs):
        captured["agent_key"] = agent_key
        captured["kwargs"] = kwargs
        return SimpleNamespace(name="Gene Expression Agent")

    monkeypatch.setattr("src.lib.agent_studio.catalog_service.get_agent_by_id", _get_agent_by_id)
    monkeypatch.setattr(
        supervisor_agent,
        "_create_streaming_tool",
        lambda **kwargs: f"wrapped::{kwargs['tool_name']}::{kwargs['specialist_name']}",
    )

    tools = supervisor_agent._create_dynamic_specialist_tools(
        document_id="doc-1",
        user_id="user-1",
        document_name="paper.pdf",
        sections=["Introduction"],
        hierarchy={"sections": [{"name": "Introduction"}]},
        abstract="abstract text",
        active_groups=["WB"],
    )

    assert captured["agent_key"] == "gene-expression"
    assert captured["kwargs"]["document_id"] == "doc-1"
    assert captured["kwargs"]["user_id"] == "user-1"
    assert captured["kwargs"]["document_name"] == "paper.pdf"
    assert captured["kwargs"]["sections"] == ["Introduction"]
    assert captured["kwargs"]["hierarchy"] == {"sections": [{"name": "Introduction"}]}
    assert captured["kwargs"]["abstract"] == "abstract text"
    assert captured["kwargs"]["active_groups"] == ["WB"]
    assert tools == ["wrapped::ask_gene_expression_specialist::Gene Expression"]


def test_create_dynamic_specialist_tools_continues_after_agent_construction_failure(monkeypatch):
    monkeypatch.setattr(
        supervisor_agent,
        "_get_supervisor_specialist_specs",
        lambda: [
            {
                "tool_name": "ask_bad_specialist",
                "agent_key": "bad",
                "description": "Bad specialist",
                "requires_document": False,
            },
            {
                "tool_name": "ask_good_specialist",
                "agent_key": "good",
                "name": "Good Agent",
                "description": "Good specialist",
                "requires_document": False,
            },
        ],
    )

    def _get_agent_by_id(agent_key, **_kwargs):
        if agent_key == "bad":
            raise RuntimeError("cannot build bad agent")
        return SimpleNamespace(name="Good Agent")

    monkeypatch.setattr("src.lib.agent_studio.catalog_service.get_agent_by_id", _get_agent_by_id)
    monkeypatch.setattr(
        supervisor_agent,
        "_create_streaming_tool",
        lambda **kwargs: f"wrapped::{kwargs['tool_name']}",
    )

    tools = supervisor_agent._create_dynamic_specialist_tools()
    assert tools == ["wrapped::ask_good_specialist"]


def test_fetch_document_sections_sync_uses_asyncio_run_without_running_loop(monkeypatch):
    import asyncio

    async def _fake_get_document_sections(_document_id, _user_id):
        return [{"name": "intro"}]

    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.get_document_sections",
        _fake_get_document_sections,
    )
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: (_ for _ in ()).throw(RuntimeError()))

    def _fake_run(coro):
        coro.close()
        return [{"name": "intro"}]

    monkeypatch.setattr(asyncio, "run", _fake_run)

    sections = supervisor_agent._fetch_document_sections_sync("doc-1", "user-1")
    assert sections == [{"name": "intro"}]


def test_fetch_document_sections_sync_uses_threadpool_when_loop_running(monkeypatch):
    import asyncio
    import concurrent.futures

    async def _fake_get_document_sections(_document_id, _user_id):
        return [{"name": "methods"}]

    class _FakeFuture:
        def __init__(self, coro):
            self._coro = coro

        def result(self, timeout=None):
            assert timeout == 10
            self._coro.close()
            return [{"name": "methods"}]

    class _FakePool:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, coro):
            assert fn is asyncio.run
            return _FakeFuture(coro)

    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.get_document_sections",
        _fake_get_document_sections,
    )
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: object())
    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", lambda: _FakePool())

    sections = supervisor_agent._fetch_document_sections_sync("doc-1", "user-1")
    assert sections == [{"name": "methods"}]


def test_fetch_document_sections_sync_returns_empty_on_exception(monkeypatch):
    import asyncio

    async def _fake_get_document_sections(_document_id, _user_id):
        return [{"name": "ignored"}]

    def _failing_run(coro):
        coro.close()
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.get_document_sections",
        _fake_get_document_sections,
    )
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(asyncio, "run", _failing_run)

    assert supervisor_agent._fetch_document_sections_sync("doc-1", "user-1") == []


def test_fetch_document_hierarchy_sync_returns_none_on_exception(monkeypatch):
    import asyncio

    async def _fake_get_hierarchy(_document_id, _user_id):
        return {"sections": [{"name": "ignored"}]}

    def _failing_run(coro):
        coro.close()
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.get_document_sections_hierarchical",
        _fake_get_hierarchy,
    )
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(asyncio, "run", _failing_run)

    assert supervisor_agent.fetch_document_hierarchy_sync("doc-1", "user-1") is None


def test_create_supervisor_agent_without_document_adds_unavailable_note(monkeypatch):
    captured_agent = {}
    captured_pending = {}
    captured_langfuse = {}

    monkeypatch.setattr(
        "src.lib.openai_agents.config.get_agent_config",
        lambda _name: SimpleNamespace(model="gpt-4o", temperature=None, reasoning=None),
    )
    monkeypatch.setattr("src.lib.openai_agents.config.log_agent_config", lambda *_a, **_k: None)
    monkeypatch.setattr("src.lib.openai_agents.config.resolve_model_provider", lambda _model: "openai")
    monkeypatch.setattr(
        "src.lib.openai_agents.config.get_model_for_agent",
        lambda model, provider_override=None: model,
    )
    monkeypatch.setattr(supervisor_agent, "_build_model_settings", lambda **_kwargs: None)
    monkeypatch.setattr(
        supervisor_agent,
        "_create_dynamic_specialist_tools",
        lambda **_kwargs: [SimpleNamespace(name="ask_gene_specialist")],
    )
    monkeypatch.setattr(
        supervisor_agent,
        "_get_supervisor_specialist_specs",
        lambda: [
            {"tool_name": "ask_gene_specialist", "requires_document": False},
            {"tool_name": "ask_pdf_specialist", "requires_document": True},
        ],
    )
    monkeypatch.setattr(
        supervisor_agent,
        "get_prompt",
        lambda _name: SimpleNamespace(content="Base prompt", version=7),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "set_pending_prompts",
        lambda name, prompts: captured_pending.update({"name": name, "prompts": prompts}),
    )
    monkeypatch.setattr(
        "src.lib.openai_agents.langfuse_client.log_agent_config",
        lambda **kwargs: captured_langfuse.update(kwargs),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "function_tool",
        lambda **decorator_kwargs: (
            lambda fn: (
                setattr(fn, "name", decorator_kwargs.get("name_override", fn.__name__)),
                fn,
            )[1]
        ),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "Agent",
        lambda **kwargs: captured_agent.update(kwargs) or SimpleNamespace(**kwargs),
    )

    created = supervisor_agent.create_supervisor_agent(document_id=None, user_id=None)

    assert "Only these specialist tools are currently installed" in created.instructions
    assert "ask_gene_specialist" in created.instructions
    assert "No PDF document is currently loaded" in created.instructions
    assert "ask_pdf_specialist" in created.instructions
    assert supervisor_agent.CURATION_PREP_CONFIRMATION_QUESTION in created.instructions
    assert any(getattr(tool, "name", "") == "prepare_for_curation" for tool in created.tools)
    assert any(getattr(tool, "name", "") == "export_to_file" for tool in created.tools)
    assert captured_pending["name"] == "Query Supervisor"
    assert captured_langfuse["metadata"]["specialist_count"] == len(created.tools)


def test_create_supervisor_agent_with_zero_specialists_enables_core_only_mode(monkeypatch):
    captured_langfuse = {}

    monkeypatch.setattr(
        "src.lib.openai_agents.config.get_agent_config",
        lambda _name: SimpleNamespace(model="gpt-4o", temperature=None, reasoning=None),
    )
    monkeypatch.setattr("src.lib.openai_agents.config.log_agent_config", lambda *_a, **_k: None)
    monkeypatch.setattr("src.lib.openai_agents.config.resolve_model_provider", lambda _model: "openai")
    monkeypatch.setattr(
        "src.lib.openai_agents.config.get_model_for_agent",
        lambda model, provider_override=None: model,
    )
    monkeypatch.setattr(supervisor_agent, "_build_model_settings", lambda **_kwargs: None)
    monkeypatch.setattr(supervisor_agent, "_get_supervisor_specialist_specs", lambda: [])
    monkeypatch.setattr(
        supervisor_agent,
        "get_prompt",
        lambda _name: SimpleNamespace(content="Base prompt", version=11),
    )
    monkeypatch.setattr(supervisor_agent, "set_pending_prompts", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "src.lib.openai_agents.langfuse_client.log_agent_config",
        lambda **kwargs: captured_langfuse.update(kwargs),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "function_tool",
        lambda **decorator_kwargs: (
            lambda fn: (
                setattr(fn, "name", decorator_kwargs.get("name_override", fn.__name__)),
                fn,
            )[1]
        ),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "Agent",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )

    created = supervisor_agent.create_supervisor_agent(document_id=None, user_id=None)

    assert "CORE-ONLY MODE" in created.instructions
    assert "No domain specialist tools are currently installed" in created.instructions
    assert [getattr(tool, "name", "") for tool in created.tools] == [
        "prepare_for_curation",
        "export_to_file",
    ]
    assert captured_langfuse["metadata"]["specialist_count"] == 2


def test_create_supervisor_agent_with_document_extracts_sections_and_enables_guardrails(monkeypatch):
    captured_dynamic = {}

    monkeypatch.setattr(
        "src.lib.openai_agents.config.get_agent_config",
        lambda _name: SimpleNamespace(model="gpt-4o", temperature=0.0, reasoning="low"),
    )
    monkeypatch.setattr("src.lib.openai_agents.config.log_agent_config", lambda *_a, **_k: None)
    monkeypatch.setattr("src.lib.openai_agents.config.resolve_model_provider", lambda _model: "openai")
    monkeypatch.setattr(
        "src.lib.openai_agents.config.get_model_for_agent",
        lambda model, provider_override=None: model,
    )
    monkeypatch.setattr(supervisor_agent, "_build_model_settings", lambda **_kwargs: None)
    monkeypatch.setattr(
        supervisor_agent,
        "_get_supervisor_specialist_specs",
        lambda: [{"tool_name": "ask_pdf_specialist", "requires_document": True}],
    )
    monkeypatch.setattr(
        supervisor_agent,
        "_create_dynamic_specialist_tools",
        lambda **kwargs: captured_dynamic.update(kwargs) or [SimpleNamespace(name="ask_pdf_specialist")],
    )
    monkeypatch.setattr(
        supervisor_agent,
        "get_prompt",
        lambda _name: SimpleNamespace(content="Base prompt", version=9),
    )
    monkeypatch.setattr(supervisor_agent, "set_pending_prompts", lambda *_a, **_k: None)
    monkeypatch.setattr("src.lib.openai_agents.langfuse_client.log_agent_config", lambda **_kwargs: None)
    monkeypatch.setattr(
        supervisor_agent,
        "function_tool",
        lambda **decorator_kwargs: (
            lambda fn: (
                setattr(fn, "name", decorator_kwargs.get("name_override", fn.__name__)),
                fn,
            )[1]
        ),
    )
    monkeypatch.setattr(supervisor_agent, "safety_guardrail", "safety")
    monkeypatch.setattr(supervisor_agent, "GUARDRAILS_AVAILABLE", True)
    monkeypatch.setattr(
        supervisor_agent,
        "Agent",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )

    created = supervisor_agent.create_supervisor_agent(
        document_id="doc-2",
        user_id="user-2",
        hierarchy={"sections": [{"name": "Introduction"}, {"name": "Methods"}]},
        enable_guardrails=True,
    )

    assert "DOCUMENT CONTEXT: A PDF document is loaded." in created.instructions
    assert "RUNTIME TOOL DESCRIPTIONS ARE AUTHORITATIVE" in created.instructions
    assert created.input_guardrails == ["safety"]
    assert captured_dynamic["sections"] == ["Introduction", "Methods"]


def test_is_explicit_curation_prep_confirmation_rejects_not_ready():
    assert supervisor_agent._is_explicit_curation_prep_confirmation("not ready") is False


def test_filter_extraction_results_for_scope_excludes_unscoped_records_when_scope_confirmed():
    matching_record = _PrepExtractionRecord(
        extraction_result_id="extract-1",
        adapter_key="reference_adapter",
        domain_key="disease",
    )
    unscoped_record = _PrepExtractionRecord(
        extraction_result_id="extract-2",
        adapter_key=None,
        profile_key=None,
        domain_key=None,
    )

    scoped_results, notes = supervisor_agent._filter_extraction_results_for_scope(
        [matching_record, unscoped_record],
        {
            "adapter_keys": ["reference_adapter"],
        },
    )

    assert [record.extraction_result_id for record in scoped_results] == ["extract-1"]
    assert notes == []


def test_filter_extraction_results_for_scope_does_not_fall_back_to_unscoped_records():
    scoped_results, notes = supervisor_agent._filter_extraction_results_for_scope(
        [
            _PrepExtractionRecord(
                extraction_result_id="extract-1",
                adapter_key=None,
                profile_key=None,
                domain_key=None,
            ),
            _PrepExtractionRecord(
                extraction_result_id="extract-2",
                adapter_key=None,
                profile_key=None,
                domain_key=None,
            ),
        ],
        {
            "adapter_keys": ["reference_adapter"],
        },
    )

    assert scoped_results == []
    assert notes == []


@pytest.mark.asyncio
async def test_dispatch_curation_prep_requires_prior_confirmation_prompt(monkeypatch):
    monkeypatch.setattr(supervisor_agent, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_agent, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(
        supervisor_agent,
        "document_state",
        SimpleNamespace(get_document=lambda _user_id: None),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "conversation_manager",
        SimpleNamespace(
            get_session_history=lambda _user_id, _session_id: [
                {"user": "Prepare the disease findings.", "assistant": "I can help with that."}
            ]
        ),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "list_extraction_results",
        lambda *_args, **_kwargs: pytest.fail("extraction lookup should not run without checkpoint"),
    )

    response = await supervisor_agent._dispatch_curation_prep_from_chat_context(
        user_confirmation="Yes, please prepare them.",
    )

    payload = json.loads(response)
    assert payload["status"] == "confirmation_required"
    assert "Ready to prepare these for curation?" in payload["message"]


@pytest.mark.asyncio
async def test_dispatch_curation_prep_runs_deterministic_prep_with_confirmed_scope(monkeypatch):
    captured = {}

    monkeypatch.setattr(supervisor_agent, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_agent, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_agent, "get_current_trace_id", lambda: "trace-1")
    monkeypatch.setattr(
        supervisor_agent,
        "document_state",
        SimpleNamespace(get_document=lambda _user_id: None),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "conversation_manager",
        SimpleNamespace(
            get_session_history=lambda _user_id, _session_id: [
                {
                    "user": "Prepare the disease findings for curation.",
                    "assistant": "Ready to prepare these for curation?",
                    "timestamp": "2026-03-21T00:10:00Z",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "list_extraction_results",
        lambda *_args, **_kwargs: [_PrepExtractionRecord()],
    )
    async def _fake_run_curation_prep(
        extraction_results,
        *,
        scope_confirmation,
        persistence_context=None,
        db=None,
    ):
        captured["extraction_results"] = extraction_results
        captured["scope_confirmation"] = scope_confirmation
        captured["persistence_context"] = persistence_context
        captured["db"] = db
        return SimpleNamespace(
            candidates=[SimpleNamespace(adapter_key="disease")],
            run_metadata=SimpleNamespace(
                warnings=[],
                processing_notes=["Prepared from confirmed chat extraction context."],
            ),
        )

    monkeypatch.setattr(supervisor_agent, "run_curation_prep", _fake_run_curation_prep)

    response = await supervisor_agent._dispatch_curation_prep_from_chat_context(
        user_confirmation="Yes, prepare the confirmed disease findings.",
        scope_summary="Disease findings for APOE.",
        adapter_keys=["reference_adapter"],
    )

    payload = json.loads(response)
    assert payload["status"] == "prepared"
    assert payload["candidate_count"] == 1
    assert payload["document_id"] == "document-1"
    assert payload["processing_notes"] == ["Prepared from confirmed chat extraction context."]
    assert len(captured["extraction_results"]) == 1
    assert captured["scope_confirmation"].confirmed is True
    assert captured["scope_confirmation"].adapter_keys == ["reference_adapter"]
    assert any(
        "Disease findings for APOE." in note
        for note in captured["scope_confirmation"].notes
    )
    assert any(
        "Yes, prepare the confirmed disease findings." in note
        for note in captured["scope_confirmation"].notes
    )
    assert captured["persistence_context"].origin_session_id == "session-1"
    assert captured["persistence_context"].trace_id == "trace-1"
    assert captured["persistence_context"].user_id == "user-1"


@pytest.mark.asyncio
async def test_dispatch_curation_prep_rejects_ambiguous_scope(monkeypatch):
    monkeypatch.setattr(supervisor_agent, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_agent, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(
        supervisor_agent,
        "document_state",
        SimpleNamespace(get_document=lambda _user_id: None),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "conversation_manager",
        SimpleNamespace(
            get_session_history=lambda _user_id, _session_id: [
                {
                    "user": "Prepare these findings.",
                    "assistant": "Ready to prepare these for curation?",
                    "timestamp": "2026-03-21T00:20:00Z",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "list_extraction_results",
        lambda *_args, **_kwargs: [
            _PrepExtractionRecord(adapter_key="reference_adapter", domain_key="disease"),
            _PrepExtractionRecord(
                extraction_result_id="extract-2",
                adapter_key="gene_expression",
                domain_key="gene_expression",
                payload_json={"run_summary": {"candidate_count": 1}},
            ),
        ],
    )

    response = await supervisor_agent._dispatch_curation_prep_from_chat_context(
        user_confirmation="Yes, do it.",
    )

    payload = json.loads(response)
    assert payload["status"] == "scope_confirmation_required"
    assert payload["available_scope"]["adapter_keys"] == ["reference_adapter", "gene_expression"]


@pytest.mark.asyncio
async def test_dispatch_curation_prep_still_filters_loaded_document_before_running(monkeypatch):
    captured = {}

    monkeypatch.setattr(supervisor_agent, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_agent, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_agent, "get_current_trace_id", lambda: "trace-2")
    monkeypatch.setattr(
        supervisor_agent,
        "document_state",
        SimpleNamespace(get_document=lambda _user_id: {"id": "document-2"}),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "conversation_manager",
        SimpleNamespace(
            get_session_history=lambda _user_id, _session_id: [
                {
                    "user": "Prepare the disease findings for curation.",
                    "assistant": "Ready to prepare these for curation?",
                    "timestamp": "2026-03-21T00:30:00Z",
                }
            ]
        ),
    )

    def _fake_list_extraction_results(*_args, **kwargs):
        captured["query_kwargs"] = kwargs
        assert kwargs["document_id"] == "document-2"
        return [
            _PrepExtractionRecord(
                extraction_result_id="extract-2",
                document_id="document-2",
                adapter_key="disease",
                domain_key="disease",
            )
        ]

    monkeypatch.setattr(
        supervisor_agent,
        "list_extraction_results",
        _fake_list_extraction_results,
    )
    async def _fake_run_curation_prep(
        extraction_results,
        *,
        scope_confirmation,
        persistence_context=None,
        db=None,
    ):
        captured["run_scope_confirmation"] = scope_confirmation
        captured["run_persistence_context"] = persistence_context
        return SimpleNamespace(
            candidates=[SimpleNamespace(adapter_key="disease")],
            run_metadata=SimpleNamespace(warnings=[], processing_notes=[]),
        )

    monkeypatch.setattr(supervisor_agent, "run_curation_prep", _fake_run_curation_prep)

    response = await supervisor_agent._dispatch_curation_prep_from_chat_context(
        user_confirmation="Yes, prepare the disease findings in the loaded document.",
        adapter_keys=["disease"],
    )

    payload = json.loads(response)
    assert payload["status"] == "prepared"
    assert captured["query_kwargs"]["document_id"] == "document-2"
    assert captured["run_scope_confirmation"].adapter_keys == ["disease"]
    assert captured["run_persistence_context"].document_id == "document-2"
    assert captured["run_persistence_context"].trace_id == "trace-2"


@pytest.mark.asyncio
async def test_dispatch_curation_prep_does_not_fall_back_to_top_level_evidence_records(monkeypatch):
    monkeypatch.setattr(supervisor_agent, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_agent, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(
        supervisor_agent,
        "document_state",
        SimpleNamespace(get_document=lambda _user_id: None),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "conversation_manager",
        SimpleNamespace(
            get_session_history=lambda _user_id, _session_id: [
                {
                    "user": "Prepare these findings.",
                    "assistant": "Ready to prepare these for curation?",
                    "timestamp": "2026-03-21T00:35:00Z",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "list_extraction_results",
        lambda *_args, **_kwargs: [
            _PrepExtractionRecord(
                payload_json={
                    "items": [{"label": "APOE", "entity_type": "gene", "evidence": []}],
                    "evidence_records": [
                        {
                            "verified_quote": "APOE was associated with the disease phenotype.",
                            "page": 3,
                            "section": "Results",
                            "subsection": "Disease association",
                            "chunk_id": "chunk-apoe-1",
                        }
                    ],
                    "run_summary": {"candidate_count": 1},
                }
            )
        ],
    )

    response = await supervisor_agent._dispatch_curation_prep_from_chat_context(
        user_confirmation="Yes, prepare them.",
        adapter_keys=["reference_adapter"],
    )

    payload = json.loads(response)
    assert payload["status"] == "unable_to_prepare"
    assert "No evidence-verified candidates were available" in payload["message"]


@pytest.mark.asyncio
async def test_dispatch_curation_prep_requires_document_narrowing_for_multi_document_session(monkeypatch):
    monkeypatch.setattr(supervisor_agent, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_agent, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(
        supervisor_agent,
        "document_state",
        SimpleNamespace(get_document=lambda _user_id: None),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "conversation_manager",
        SimpleNamespace(
            get_session_history=lambda _user_id, _session_id: [
                {
                    "user": "Prepare these findings.",
                    "assistant": "Ready to prepare these for curation?",
                    "timestamp": "2026-03-21T00:40:00Z",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "list_extraction_results",
        lambda *_args, **_kwargs: [
            _PrepExtractionRecord(
                extraction_result_id="extract-1",
                document_id="document-1",
                adapter_key="disease",
                domain_key="disease",
            ),
            _PrepExtractionRecord(
                extraction_result_id="extract-2",
                document_id="document-2",
                adapter_key="disease",
                domain_key="disease",
            ),
        ],
    )

    response = await supervisor_agent._dispatch_curation_prep_from_chat_context(
        user_confirmation="Yes, prepare them.",
    )

    payload = json.loads(response)
    assert payload["status"] == "scope_confirmation_required"
    assert payload["available_document_ids"] == ["document-1", "document-2"]
    assert "multiple documents" in payload["message"]
