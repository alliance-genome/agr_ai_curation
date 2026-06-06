"""Unit tests for runner core helper behavior."""

from types import SimpleNamespace
from datetime import datetime

from src.lib.openai_agents import runner
from src.lib.prompts.context import PromptAssemblyMetadata, PromptRun


def test_configure_api_mode_uses_provider_mode(monkeypatch):
    calls = []
    transport_calls = []
    monkeypatch.setattr(
        runner,
        "get_default_runner_provider",
        lambda: SimpleNamespace(provider_id="openai", api_mode="chat_completions"),
    )
    monkeypatch.setattr(runner, "set_default_openai_api", lambda mode: calls.append(mode))
    monkeypatch.setattr(
        runner,
        "set_default_openai_responses_transport",
        lambda transport: transport_calls.append(transport),
    )

    runner._configure_api_mode()
    assert calls[-1] == "chat_completions"
    assert transport_calls[-1] == "http"

    monkeypatch.setattr(
        runner,
        "get_default_runner_provider",
        lambda: SimpleNamespace(provider_id="openai", api_mode="responses"),
    )
    runner._configure_api_mode()
    assert calls[-1] == "responses"
    assert transport_calls[-1] == "websocket"


def test_configure_api_mode_can_disable_openai_responses_websocket(monkeypatch):
    calls = []
    transport_calls = []
    monkeypatch.setenv("OPENAI_RESPONSES_WEBSOCKET_ENABLED", "false")
    monkeypatch.setattr(
        runner,
        "get_default_runner_provider",
        lambda: SimpleNamespace(
            provider_id="openai",
            driver="openai_native",
            api_mode="responses",
        ),
    )
    monkeypatch.setattr(runner, "set_default_openai_api", lambda mode: calls.append(mode))
    monkeypatch.setattr(
        runner,
        "set_default_openai_responses_transport",
        lambda transport: transport_calls.append(transport),
    )

    runner._configure_api_mode()

    assert calls[-1] == "responses"
    assert transport_calls[-1] == "http"


def test_configure_api_mode_keeps_websocket_disabled_for_non_responses_provider(monkeypatch):
    transport_calls = []
    monkeypatch.setenv("OPENAI_RESPONSES_WEBSOCKET_ENABLED", "true")
    monkeypatch.setattr(
        runner,
        "get_default_runner_provider",
        lambda: SimpleNamespace(
            provider_id="gemini",
            driver="litellm",
            api_mode="chat_completions",
        ),
    )
    monkeypatch.setattr(runner, "set_default_openai_api", lambda _mode: None)
    monkeypatch.setattr(
        runner,
        "set_default_openai_responses_transport",
        lambda transport: transport_calls.append(transport),
    )

    runner._configure_api_mode()

    assert transport_calls[-1] == "http"


def test_create_openai_client_kwargs_includes_configured_key_and_base(monkeypatch):
    monkeypatch.setattr(
        runner,
        "get_default_runner_provider",
        lambda: SimpleNamespace(provider_id="openai"),
    )
    monkeypatch.setattr(runner, "get_api_key", lambda _provider: "test-key")
    monkeypatch.setattr(runner, "get_base_url", lambda _provider: "https://api.example.test/v1")

    kwargs = runner._create_openai_client_kwargs()
    assert kwargs["api_key"] == "test-key"
    assert kwargs["base_url"] == "https://api.example.test/v1"


def test_create_openai_client_kwargs_includes_websocket_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_WEBSOCKET_BASE_URL", "wss://api.example.test/v1")
    monkeypatch.setattr(
        runner,
        "get_default_runner_provider",
        lambda: SimpleNamespace(provider_id="openai"),
    )
    monkeypatch.setattr(runner, "get_api_key", lambda _provider: "test-key")
    monkeypatch.setattr(runner, "get_base_url", lambda _provider: "https://api.example.test/v1")

    kwargs = runner._create_openai_client_kwargs()
    assert kwargs["websocket_base_url"] == "wss://api.example.test/v1"


def test_create_openai_client_kwargs_omits_empty_values(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        runner,
        "get_default_runner_provider",
        lambda: SimpleNamespace(provider_id="openai"),
    )
    monkeypatch.setattr(runner, "get_api_key", lambda _provider: "")
    monkeypatch.setattr(runner, "get_base_url", lambda _provider: None)

    kwargs = runner._create_openai_client_kwargs()
    assert kwargs == {"api_key": "missing-api-key"}


def test_now_iso_is_parseable_utc_timestamp():
    text = runner._now_iso()
    parsed = datetime.fromisoformat(text)
    assert parsed.tzinfo is not None


def test_set_langfuse_trace_io_prefers_root_span_and_falls_back_to_client():
    captured = []

    class _RootSpan:
        def set_trace_io(self, **kwargs):
            captured.append(("root", kwargs))

    class _Client:
        def set_current_trace_io(self, **kwargs):
            captured.append(("client", kwargs))

    runner._set_langfuse_trace_io(_Client(), _RootSpan(), input={"query": "hello"})
    runner._set_langfuse_trace_io(_Client(), object(), output={"response": "hi"})

    assert captured == [
        ("root", {"input": {"query": "hello"}}),
        ("client", {"output": {"response": "hi"}}),
    ]


def _prompt_run(prompt, *, hash_value="hash-1", layer_manifest=None):
    manifest = layer_manifest or {"agent_id": "supervisor", "layers": [], "hash": hash_value}
    return PromptRun(
        agent_name="Query Supervisor",
        prompts=[prompt],
        assembly=PromptAssemblyMetadata(
            effective_prompt_hash=hash_value,
            layer_manifest=manifest,
        ),
    )


def test_log_used_prompts_returns_zero_when_no_prompts(monkeypatch):
    monkeypatch.setattr(runner, "get_used_prompts", lambda: [])
    monkeypatch.setattr(runner, "get_used_prompt_runs", lambda: [])
    assert runner._log_used_prompts_to_db(trace_id="trace-1") == 0


def test_log_used_prompts_persists_entries_and_updates_span(monkeypatch):
    used_prompt = SimpleNamespace(
        agent_name="Supervisor",
        prompt_type="base",
        group_id=None,
        version=3,
        id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    )
    monkeypatch.setattr(runner, "get_used_prompts", lambda: [used_prompt])
    monkeypatch.setattr(runner, "get_used_prompt_runs", lambda: [_prompt_run(used_prompt)])

    captured = {}

    class _FakeSpan:
        def update(self, metadata):
            captured["span_metadata"] = metadata

    class _FakePromptService:
        def __init__(self, _db):
            pass

        def log_all_used_prompts(self, prompts, trace_id, session_id, **_kwargs):
            captured["service"] = {
                "prompt_count": len(prompts),
                "trace_id": trace_id,
                "session_id": session_id,
                "effective_prompt_hash": _kwargs["effective_prompt_hash"],
                "layer_manifest": _kwargs["layer_manifest"],
            }
            return [SimpleNamespace(id=1)]

    class _FakeDB:
        def __init__(self):
            self.committed = False
            self.closed = False

        def commit(self):
            self.committed = True

        def close(self):
            self.closed = True

    db = _FakeDB()
    monkeypatch.setattr(runner, "PromptService", _FakePromptService)
    monkeypatch.setattr(runner, "SessionLocal", lambda: db)

    count = runner._log_used_prompts_to_db(
        trace_id="trace-2",
        session_id="session-1",
        span=_FakeSpan(),
    )

    assert count == 1
    assert captured["service"]["prompt_count"] == 1
    assert captured["service"]["trace_id"] == "trace-2"
    assert captured["service"]["effective_prompt_hash"] == "hash-1"
    assert captured["span_metadata"]["prompt_count"] == 1
    assert captured["span_metadata"]["prompt_assemblies"][0]["effective_prompt_hash"] == "hash-1"
    assert db.committed is True
    assert db.closed is True


def test_log_used_prompts_skips_legacy_prompt_rows_without_prompt_runs(monkeypatch):
    used_prompt = SimpleNamespace(
        agent_name="Supervisor",
        prompt_type="base",
        group_id=None,
        version=1,
        id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    )
    monkeypatch.setattr(runner, "get_used_prompts", lambda: [used_prompt])
    monkeypatch.setattr(runner, "get_used_prompt_runs", lambda: [])
    monkeypatch.setattr(
        runner,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("legacy prompt rows must not be logged")),
    )

    assert runner._log_used_prompts_to_db(trace_id="trace-legacy") == 0


def test_log_used_prompts_skips_prompt_runs_without_assembly(monkeypatch):
    used_prompt = SimpleNamespace(
        agent_name="Supervisor",
        prompt_type="base",
        group_id=None,
        version=1,
        id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    )
    monkeypatch.setattr(runner, "get_used_prompts", lambda: [used_prompt])
    monkeypatch.setattr(
        runner,
        "get_used_prompt_runs",
        lambda: [PromptRun(agent_name="Query Supervisor", prompts=[used_prompt])],
    )
    monkeypatch.setattr(
        runner,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("unassembled prompt runs must not be logged")),
    )

    assert runner._log_used_prompts_to_db(trace_id="trace-no-assembly") == 0


def test_log_used_prompts_returns_zero_when_db_write_fails(monkeypatch):
    used_prompt = SimpleNamespace(
        agent_name="Supervisor",
        prompt_type="base",
        group_id=None,
        version=1,
        id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    )
    monkeypatch.setattr(runner, "get_used_prompts", lambda: [used_prompt])
    monkeypatch.setattr(runner, "get_used_prompt_runs", lambda: [_prompt_run(used_prompt)])
    monkeypatch.setattr(runner, "SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("db down")))

    assert runner._log_used_prompts_to_db(trace_id="trace-3") == 0


def test_safe_langfuse_wrapper_sanitizes_none_metadata_for_responses():
    captured = {}

    class _Responses:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return {"ok": True}

    client = object.__new__(runner.SafeLangfuseAsyncOpenAI)
    client.responses = _Responses()
    client._wrap_responses_api()

    import asyncio

    result = asyncio.run(client.responses.create(metadata=None, input="hello"))
    assert result == {"ok": True}
    assert captured["metadata"] == {}


def test_safe_langfuse_wrapper_preserves_dict_metadata_for_responses():
    captured = {}

    class _Responses:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return {"ok": True}

    client = object.__new__(runner.SafeLangfuseAsyncOpenAI)
    client.responses = _Responses()
    client._wrap_responses_api()

    import asyncio

    asyncio.run(client.responses.create(metadata={"trace": "x"}, input="hello"))
    assert captured["metadata"] == {"trace": "x"}


def test_safe_langfuse_wrapper_sanitizes_none_metadata_for_chat():
    captured = {}

    class _Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return {"ok": True}

    chat = SimpleNamespace(completions=_Completions())
    client = object.__new__(runner.SafeLangfuseAsyncOpenAI)
    client.chat = chat
    client._wrap_chat_api()

    import asyncio

    result = asyncio.run(client.chat.completions.create(metadata=None, messages=[]))
    assert result == {"ok": True}
    assert captured["metadata"] == {}


def test_build_agents_run_config_enables_complete_capture_when_instrumented(monkeypatch):
    monkeypatch.setattr(runner, "is_openai_agents_tracing_enabled", lambda: True)

    config = runner._build_agents_run_config(
        model_provider=object(),
        agent=SimpleNamespace(name="Query Supervisor"),
        trace_id="trace-123",
        session_id="session-1",
        user_id="user-1",
        document_id="doc-1",
        document_name="paper.pdf",
    )

    assert config.tracing_disabled is False
    assert config.trace_include_sensitive_data is True
    assert config.group_id == "session-1"
    assert config.trace_metadata["langfuse_trace_id"] == "trace-123"
    assert config.trace_metadata["openai_agents_tracing"] == "langfuse_openinference"


def test_build_agents_run_config_disables_sdk_export_when_not_instrumented(monkeypatch):
    monkeypatch.setattr(runner, "is_openai_agents_tracing_enabled", lambda: False)

    config = runner._build_agents_run_config(
        model_provider=object(),
        agent=SimpleNamespace(name="Query Supervisor"),
        trace_id="trace-123",
        session_id=None,
        user_id="user-1",
        document_id=None,
        document_name=None,
    )

    assert config.tracing_disabled is True
    assert config.trace_include_sensitive_data is True
    assert config.trace_metadata["openai_agents_tracing"] == "disabled"


def test_log_used_prompts_continues_when_span_update_fails(monkeypatch):
    used_prompt = SimpleNamespace(
        agent_name="Supervisor",
        prompt_type="base",
        group_id=None,
        version=4,
        id="cccccccc-cccc-cccc-cccc-cccccccccccc",
    )
    monkeypatch.setattr(runner, "get_used_prompts", lambda: [used_prompt])
    monkeypatch.setattr(runner, "get_used_prompt_runs", lambda: [_prompt_run(used_prompt)])

    class _BadSpan:
        def update(self, metadata):
            raise RuntimeError("span write failed")

    class _FakePromptService:
        def __init__(self, _db):
            pass

        def log_all_used_prompts(self, prompts, trace_id, session_id, **_kwargs):
            return [SimpleNamespace(id=1)]

    class _FakeDB:
        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(runner, "PromptService", _FakePromptService)
    monkeypatch.setattr(runner, "SessionLocal", lambda: _FakeDB())

    assert runner._log_used_prompts_to_db(trace_id="trace-4", span=_BadSpan()) == 1
