"""Branch tests for run_agent_streamed orchestration paths."""

from types import SimpleNamespace

import pytest

from src.lib.openai_agents import runner


async def _collect_events(async_gen):
    events = []
    async for event in async_gen:
        events.append(event)
    return events


class _FakeRunResult:
    def __init__(self, events, final_output="ok"):
        self._events = events
        self.final_output = final_output

    async def stream_events(self):
        for event in self._events:
            yield event


class _FakeTextDelta:
    def __init__(self, delta):
        self.delta = delta


def _raw_response_stream_event(data):
    return SimpleNamespace(type="raw_response_event", data=data)


def _patch_common_runtime(monkeypatch, captured):
    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(runner, "clear_pending_configs", lambda: None)
    monkeypatch.setattr(runner, "reset_consecutive_call_tracker", lambda: None)
    monkeypatch.setattr(runner, "clear_prompt_context", lambda: None)
    monkeypatch.setattr(runner, "commit_pending_prompts", lambda agent_name: captured.setdefault("committed", []).append(agent_name))
    monkeypatch.setattr(runner, "set_current_trace_id", lambda trace_id: captured.setdefault("trace_ids", []).append(trace_id))
    def _flush():
        captured["flushed"] = captured.get("flushed", 0) + 1

    monkeypatch.setattr(runner, "flush_langfuse", _flush)
    monkeypatch.setattr(
        runner,
        "_log_used_prompts_to_db",
        lambda trace_id, session_id=None, span=None: captured.setdefault("logged", []).append((trace_id, session_id, span)),
    )
    monkeypatch.setattr(runner, "create_supervisor_agent", lambda **_kwargs: SimpleNamespace(name="Supervisor", model="gpt-5"))


@pytest.mark.asyncio
async def test_run_agent_streamed_without_langfuse(monkeypatch):
    captured = {}
    _patch_common_runtime(monkeypatch, captured)
    monkeypatch.setattr(runner, "get_langfuse", lambda: None)

    async def _fake_run_agent_with_tracing(**kwargs):
        captured["run_kwargs"] = kwargs
        yield {
            "type": "RUN_FINISHED",
            "data": {"response_length": 5, "tool_calls": 0, "agents_used": ["Supervisor"]},
        }

    monkeypatch.setattr(runner, "_run_agent_with_tracing", _fake_run_agent_with_tracing)

    events = await _collect_events(
        runner.run_agent_streamed(
            user_message="hello",
            user_id="user-1",
            session_id="session-1",
            document_id="11111111-1111-1111-1111-111111111111",
            document_name="Paper A",
            conversation_history=[{"role": "user", "content": "older"}],
        )
    )

    assert events[0]["type"] == "RUN_STARTED"
    assert events[1]["type"] == "SUPERVISOR_START"
    assert events[-1]["type"] == "RUN_FINISHED"
    fallback_trace = events[0]["data"]["trace_id"]
    assert fallback_trace.startswith("chat-")
    assert captured["run_kwargs"]["trace_id"] == fallback_trace
    assert captured["logged"][0][0] == fallback_trace


@pytest.mark.asyncio
async def test_run_agent_streamed_passes_model_overrides_to_supervisor_builder(monkeypatch):
    captured = {}
    _patch_common_runtime(monkeypatch, captured)
    monkeypatch.setattr(runner, "get_langfuse", lambda: None)

    def _create_supervisor_agent(**kwargs):
        captured["supervisor_kwargs"] = kwargs
        return SimpleNamespace(name="Supervisor", model=kwargs["model_override"])

    monkeypatch.setattr(runner, "create_supervisor_agent", _create_supervisor_agent)

    async def _fake_run_agent_with_tracing(**kwargs):
        captured["run_kwargs"] = kwargs
        yield {
            "type": "RUN_FINISHED",
            "data": {"response": "ok", "response_length": 2, "tool_calls": 0, "agents_used": ["Supervisor"]},
        }

    monkeypatch.setattr(runner, "_run_agent_with_tracing", _fake_run_agent_with_tracing)

    events = await _collect_events(
        runner.run_agent_streamed(
            user_message="hello",
            user_id="user-override",
            supervisor_model="gpt-5.4-nano",
            specialist_model="gpt-5.4-nano",
            supervisor_temperature=0.0,
            specialist_temperature=0.0,
            supervisor_reasoning="minimal",
            specialist_reasoning="minimal",
        )
    )

    assert events[0]["type"] == "RUN_STARTED"
    assert events[0]["data"]["model"] == "gpt-5.4-nano"
    assert captured["supervisor_kwargs"]["model_override"] == "gpt-5.4-nano"
    assert captured["supervisor_kwargs"]["specialist_model_override"] == "gpt-5.4-nano"
    assert captured["supervisor_kwargs"]["temperature_override"] == 0.0
    assert captured["supervisor_kwargs"]["specialist_temperature_override"] == 0.0
    assert captured["supervisor_kwargs"]["reasoning_override"] == "minimal"
    assert captured["supervisor_kwargs"]["specialist_reasoning_override"] == "minimal"


@pytest.mark.asyncio
async def test_run_agent_streamed_with_langfuse_trace_success(monkeypatch):
    captured = {}
    _patch_common_runtime(monkeypatch, captured)
    monkeypatch.setattr(runner, "flush_agent_configs", lambda _span: 2)

    class _RootSpan:
        trace_id = "trace-abc"
        id = "span-abc"

        def update(self, **kwargs):
            captured["update"] = kwargs

    root_span = _RootSpan()

    class _SpanContext:
        def __enter__(self):
            return root_span

        def __exit__(self, exc_type, exc, tb):
            captured["span_exit"] = (exc_type, exc, tb)

    class _TraceAttributeContext:
        def __init__(self, kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            captured["propagate_attributes"] = self.kwargs
            return None

        def __exit__(self, exc_type, exc, tb):
            captured["trace_attr_exit"] = (exc_type, exc, tb)

    class _Langfuse:
        def start_as_current_observation(self, **kwargs):
            captured["span_start"] = kwargs
            return _SpanContext()

    monkeypatch.setattr(runner, "get_langfuse", lambda: _Langfuse())
    monkeypatch.setattr(runner, "propagate_attributes", lambda **kwargs: _TraceAttributeContext(kwargs))

    async def _fake_run_agent_with_tracing(**_kwargs):
        yield {
            "type": "RUN_FINISHED",
            "data": {
                "response": "grounded answer",
                "response_length": 12,
                "tool_calls": 1,
                "agents_used": ["Supervisor"],
            },
        }

    monkeypatch.setattr(runner, "_run_agent_with_tracing", _fake_run_agent_with_tracing)

    events = await _collect_events(
        runner.run_agent_streamed(
            user_message="longer message",
            user_id="user-2",
            session_id="session-2",
            agent=SimpleNamespace(name="Flow Supervisor", model="gpt-5", tools=[]),
            doc_context=SimpleNamespace(hierarchy={"sections": []}, abstract="abstract text", section_count=lambda: 0),
            active_groups=["WB"],
        )
    )

    assert events[0]["type"] == "RUN_STARTED"
    assert events[0]["data"]["trace_id"] == "trace-abc"
    assert events[-1]["type"] == "RUN_FINISHED"
    assert captured["trace_ids"][-1] == "trace-abc"
    assert captured["span_start"]["as_type"] == "span"
    assert captured["propagate_attributes"]["user_id"] == "user-2"
    assert captured["propagate_attributes"]["session_id"] == "session-2"
    assert captured["propagate_attributes"]["tags"] == ["chat", "openai-agents", "group:WB"]
    assert captured["propagate_attributes"]["trace_name"].startswith("chat: longer message")
    assert captured["trace_attr_exit"] == (None, None, None)
    assert captured["span_exit"] == (None, None, None)
    assert captured["update"]["output"]["response"] == "grounded answer"
    assert captured["logged"][0][0] == "trace-abc"


@pytest.mark.asyncio
async def test_run_agent_streamed_falls_back_when_span_creation_fails(monkeypatch):
    captured = {}
    _patch_common_runtime(monkeypatch, captured)

    class _BrokenLangfuse:
        def start_as_current_observation(self, **_kwargs):
            raise RuntimeError("span init failed")

    monkeypatch.setattr(runner, "get_langfuse", lambda: _BrokenLangfuse())

    async def _fake_run_agent_with_tracing(**kwargs):
        captured["fallback_kwargs"] = kwargs
        yield {"type": "RUN_FINISHED", "data": {"response_length": 1, "tool_calls": 0, "agents_used": ["Supervisor"]}}

    monkeypatch.setattr(runner, "_run_agent_with_tracing", _fake_run_agent_with_tracing)

    events = await _collect_events(
        runner.run_agent_streamed(user_message="hello", user_id="user-3")
    )

    assert events[0]["type"] == "RUN_STARTED"
    fallback_trace = events[0]["data"]["trace_id"]
    assert fallback_trace.startswith("chat-")
    assert captured["fallback_kwargs"]["trace_id"] == fallback_trace
    assert captured["logged"][0][0] == fallback_trace


@pytest.mark.asyncio
async def test_run_agent_streamed_specialist_output_error_path(monkeypatch):
    captured = {}
    _patch_common_runtime(monkeypatch, captured)
    monkeypatch.setattr(runner, "flush_agent_configs", lambda _span: 0)

    class _RootSpan:
        trace_id = "trace-specialist"
        id = "span-specialist"

        def update(self, **kwargs):
            captured.setdefault("span_updates", []).append(kwargs)

    class _SpanContext:
        def __enter__(self):
            return _RootSpan()

        def __exit__(self, exc_type, exc, tb):
            return False

    class _TraceAttributeContext:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    class _Langfuse:
        def start_as_current_observation(self, **_kwargs):
            return _SpanContext()

    monkeypatch.setattr(runner, "get_langfuse", lambda: _Langfuse())
    monkeypatch.setattr(runner, "propagate_attributes", lambda **_kwargs: _TraceAttributeContext())

    async def _notify_tool_failure(**_kwargs):
        return None

    monkeypatch.setattr(runner, "notify_tool_failure", _notify_tool_failure)

    async def _raising_stream(**_kwargs):
        if False:
            yield {}
        raise runner.SpecialistOutputError("Gene Specialist", "GeneResultEnvelope")

    monkeypatch.setattr(runner, "_run_agent_with_tracing", _raising_stream)

    events = await _collect_events(
        runner.run_agent_streamed(user_message="hello", user_id="user-4")
    )

    event_types = [event["type"] for event in events]
    assert "SPECIALIST_ERROR" in event_types
    assert "RUN_ERROR" in event_types
    assert any(
        update.get("output", {}).get("error_type") == "SpecialistOutputError"
        and update.get("metadata", {}).get("specialist_retry_failed") is True
        for update in captured["span_updates"]
    )
    assert captured["logged"][0][0] == "trace-specialist"


@pytest.mark.asyncio
async def test_run_agent_streamed_core_only_round_trip_does_not_require_specialists(monkeypatch):
    captured = {}
    _patch_common_runtime(monkeypatch, captured)

    monkeypatch.setattr(runner, "get_langfuse", lambda: None)
    monkeypatch.setattr(runner, "get_max_turns", lambda: 4)
    monkeypatch.setattr(runner, "SafeLangfuseAsyncOpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "OpenAIProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "set_live_event_list", lambda _events: None)
    monkeypatch.setattr(runner, "ResponseTextDeltaEvent", _FakeTextDelta)
    monkeypatch.setattr(
        runner,
        "create_supervisor_agent",
        lambda **_kwargs: SimpleNamespace(
            name="Query Supervisor",
            model="gpt-4o",
            tools=[SimpleNamespace(name="export_to_file", description="Export data")],
        ),
    )
    monkeypatch.setattr(
        runner.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(
            [_raw_response_stream_event(_FakeTextDelta("Core-only hello"))],
            final_output="Core-only hello",
        ),
    )

    events = await _collect_events(
        runner.run_agent_streamed(user_message="hello", user_id="user-core")
    )

    event_types = [event["type"] for event in events]
    assert event_types[0] == "RUN_STARTED"
    assert event_types[1] == "SUPERVISOR_START"
    assert "TEXT_MESSAGE_CONTENT" in event_types
    assert "TOOL_START" not in event_types
    assert "CHAT_OUTPUT_READY" not in event_types
    assert "FILE_READY" not in event_types
    assert event_types[-1] == "RUN_FINISHED"
    assert events[-1]["data"]["response"] == "Core-only hello"
    assert captured["committed"] == ["Query Supervisor"]


@pytest.mark.asyncio
async def test_run_agent_streamed_retries_transient_groq_tool_call_parse_failure(monkeypatch):
    captured = {}
    _patch_common_runtime(monkeypatch, captured)
    monkeypatch.setattr(runner, "get_langfuse", lambda: None)
    monkeypatch.setattr(runner, "get_groq_tool_call_max_retries", lambda: 1)
    monkeypatch.setattr(runner, "get_groq_tool_call_retry_delay_seconds", lambda: 0.0)

    attempts = {"count": 0}

    async def _flaky_run(**_kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            if False:
                yield {}
            raise RuntimeError(
                "GroqException - Failed to parse tool call arguments as JSON"
            )
        yield {
            "type": "RUN_FINISHED",
            "data": {"response_length": 3, "tool_calls": 0, "agents_used": ["Supervisor"]},
        }

    monkeypatch.setattr(runner, "_run_agent_with_tracing", _flaky_run)

    events = await _collect_events(
        runner.run_agent_streamed(
            user_message="hello",
            user_id="user-5",
            agent=SimpleNamespace(
                name="Flow Supervisor",
                model=SimpleNamespace(model="groq/openai/gpt-oss-120b"),
                tools=[],
            ),
        )
    )

    event_types = [event["type"] for event in events]
    assert "SUPERVISOR_RETRY" in event_types
    assert event_types[-1] == "RUN_FINISHED"
    assert attempts["count"] == 2
