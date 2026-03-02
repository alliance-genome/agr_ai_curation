"""Runtime-focused tests for supervisor agent helpers."""

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
        model="gpt-5.2-mini",
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
                "agent_key": "pdf",
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

    assert "No PDF document is currently loaded." in created.instructions
    assert any(getattr(tool, "name", "") == "export_to_file" for tool in created.tools)
    assert captured_pending["name"] == "Query Supervisor"
    assert captured_langfuse["metadata"]["specialist_count"] == len(created.tools)


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
        "_create_dynamic_specialist_tools",
        lambda **kwargs: captured_dynamic.update(kwargs) or [],
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

    assert "**DOCUMENT CONTEXT**" in created.instructions
    assert created.input_guardrails == ["safety"]
    assert captured_dynamic["sections"] == ["Introduction", "Methods"]
