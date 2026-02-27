"""Unit tests for runner core helper behavior."""

from types import SimpleNamespace
from datetime import datetime

from src.lib.openai_agents import runner


def test_configure_api_mode_uses_provider_mode(monkeypatch):
    calls = []
    monkeypatch.setattr(
        runner,
        "get_default_runner_provider",
        lambda: SimpleNamespace(provider_id="openai", api_mode="chat_completions"),
    )
    monkeypatch.setattr(runner, "set_default_openai_api", lambda mode: calls.append(mode))

    runner._configure_api_mode()
    assert calls[-1] == "chat_completions"

    monkeypatch.setattr(
        runner,
        "get_default_runner_provider",
        lambda: SimpleNamespace(provider_id="openai", api_mode="responses"),
    )
    runner._configure_api_mode()
    assert calls[-1] == "responses"


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


def test_create_openai_client_kwargs_omits_empty_values(monkeypatch):
    monkeypatch.setattr(
        runner,
        "get_default_runner_provider",
        lambda: SimpleNamespace(provider_id="openai"),
    )
    monkeypatch.setattr(runner, "get_api_key", lambda _provider: "")
    monkeypatch.setattr(runner, "get_base_url", lambda _provider: None)

    kwargs = runner._create_openai_client_kwargs()
    assert kwargs == {}


def test_now_iso_is_parseable_utc_timestamp():
    text = runner._now_iso()
    parsed = datetime.fromisoformat(text)
    assert parsed.tzinfo is not None


def test_log_used_prompts_returns_zero_when_no_prompts(monkeypatch):
    monkeypatch.setattr(runner, "get_used_prompts", lambda: [])
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

    captured = {}

    class _FakeSpan:
        def update(self, metadata):
            captured["span_metadata"] = metadata

    class _FakePromptService:
        def __init__(self, _db):
            pass

        def log_all_used_prompts(self, prompts, trace_id, session_id):
            captured["service"] = {
                "prompt_count": len(prompts),
                "trace_id": trace_id,
                "session_id": session_id,
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
    assert captured["span_metadata"]["prompt_count"] == 1
    assert db.committed is True
    assert db.closed is True


def test_log_used_prompts_returns_zero_when_db_write_fails(monkeypatch):
    used_prompt = SimpleNamespace(
        agent_name="Supervisor",
        prompt_type="base",
        group_id=None,
        version=1,
        id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    )
    monkeypatch.setattr(runner, "get_used_prompts", lambda: [used_prompt])
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


def test_log_used_prompts_continues_when_span_update_fails(monkeypatch):
    used_prompt = SimpleNamespace(
        agent_name="Supervisor",
        prompt_type="base",
        group_id=None,
        version=4,
        id="cccccccc-cccc-cccc-cccc-cccccccccccc",
    )
    monkeypatch.setattr(runner, "get_used_prompts", lambda: [used_prompt])

    class _BadSpan:
        def update(self, metadata):
            raise RuntimeError("span write failed")

    class _FakePromptService:
        def __init__(self, _db):
            pass

        def log_all_used_prompts(self, prompts, trace_id, session_id):
            return [SimpleNamespace(id=1)]

    class _FakeDB:
        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(runner, "PromptService", _FakePromptService)
    monkeypatch.setattr(runner, "SessionLocal", lambda: _FakeDB())

    assert runner._log_used_prompts_to_db(trace_id="trace-4", span=_BadSpan()) == 1
