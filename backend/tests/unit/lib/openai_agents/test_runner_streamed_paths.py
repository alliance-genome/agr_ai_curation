"""Branch tests for run_agent_streamed orchestration paths."""

from types import SimpleNamespace

import pytest

from src.lib.openai_agents import runner


async def _collect_events(async_gen):
    events = []
    async for event in async_gen:
        events.append(event)
    return events


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
async def test_run_agent_streamed_with_langfuse_trace_success(monkeypatch):
    captured = {}
    _patch_common_runtime(monkeypatch, captured)
    monkeypatch.setattr(runner, "flush_agent_configs", lambda _span: 2)

    class _RootSpan:
        trace_id = "trace-abc"

        def update_trace(self, **kwargs):
            captured["update_trace"] = kwargs

        def update(self, **kwargs):
            captured["update"] = kwargs

    root_span = _RootSpan()

    class _SpanContext:
        def __enter__(self):
            return root_span

        def __exit__(self, exc_type, exc, tb):
            captured["span_exit"] = (exc_type, exc, tb)

    class _Langfuse:
        def start_as_current_span(self, **kwargs):
            captured["span_start"] = kwargs
            return _SpanContext()

    monkeypatch.setattr(runner, "get_langfuse", lambda: _Langfuse())

    async def _fake_run_agent_with_tracing(**_kwargs):
        yield {
            "type": "RUN_FINISHED",
            "data": {"response_length": 12, "tool_calls": 1, "agents_used": ["Supervisor"]},
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
    assert captured["update_trace"]["user_id"] == "user-2"
    assert captured["logged"][0][0] == "trace-abc"


@pytest.mark.asyncio
async def test_run_agent_streamed_falls_back_when_span_creation_fails(monkeypatch):
    captured = {}
    _patch_common_runtime(monkeypatch, captured)

    class _BrokenLangfuse:
        def start_as_current_span(self, **_kwargs):
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

        def update_trace(self, **_kwargs):
            pass

        def update(self, **kwargs):
            captured.setdefault("span_updates", []).append(kwargs)

    class _SpanContext:
        def __enter__(self):
            return _RootSpan()

        def __exit__(self, exc_type, exc, tb):
            return False

    class _Langfuse:
        def start_as_current_span(self, **_kwargs):
            return _SpanContext()

    monkeypatch.setattr(runner, "get_langfuse", lambda: _Langfuse())

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
    assert captured["logged"][0][0] == "trace-specialist"


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
