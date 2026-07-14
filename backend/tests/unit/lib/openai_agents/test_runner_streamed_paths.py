"""Branch tests for run_agent_streamed orchestration paths."""

import logging
import uuid
from types import SimpleNamespace

import pytest

from src.lib.openai_agents import runner
from src.lib.prompts.context import (
    bind_prompt_run,
    clear_prompt_context,
    get_used_prompt_runs,
    set_pending_prompts,
)
from src.models.sql.prompts import PromptTemplate


async def _collect_events(async_gen):
    events = []
    async for event in async_gen:
        events.append(event)
    return events


class _FakeRunResult:
    def __init__(self, events, final_output: object = "ok"):
        self._events = events
        self.final_output = final_output

    async def stream_events(self):
        for event in self._events:
            yield event


class _FakeFailingRunResult:
    final_output = None

    async def stream_events(self):
        if False:
            yield None
        raise TimeoutError("Responses websocket connect timed out after 5.0 seconds.")


class _FakeContextManager:
    def __init__(self, value=None):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, exc_type, exc, tb):
        return None


class _FakeTextDelta:
    def __init__(self, delta):
        self.delta = delta


def _raw_response_stream_event(data):
    return SimpleNamespace(type="raw_response_event", data=data)


def _prompt(content: str) -> PromptTemplate:
    return PromptTemplate(
        id=uuid.uuid4(),
        agent_name="flow_supervisor",
        prompt_type="system",
        group_id=None,
        content=content,
        version=1,
        is_active=True,
    )


def _manifest(agent_id: str, content: str, hash_value: str) -> dict:
    return {
        "agent_id": agent_id,
        "layers": [
            {
                "id": f"{agent_id}:base_prompt",
                "kind": "base_prompt",
                "title": "Editable base prompt",
                "content": content,
                "provenance": "prompt_template:system",
                "editable": True,
                "locked": False,
                "source_ref": f"prompt_templates:active:{agent_id}:system:base:v1",
                "hash": f"{hash_value}:layer",
            }
        ],
        "hash": hash_value,
    }


def _patch_common_runtime(monkeypatch, captured):
    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(runner, "clear_pending_configs", lambda: None)
    monkeypatch.setattr(runner, "reset_consecutive_call_tracker", lambda: None)
    monkeypatch.setattr(runner, "clear_prompt_context", lambda: None)
    monkeypatch.setattr(
        runner,
        "commit_pending_prompts",
        lambda agent: captured.setdefault("committed", []).append(getattr(agent, "name", agent)),
    )
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


def test_merge_evidence_records_replaces_same_id_and_preserves_revision_history():
    existing = [
        {
            "evidence_record_id": "evidence-live-a",
            "entity": "Actin 5C",
            "verified_quote": "Previous exact source sentence.",
            "page": 4,
            "section": "Results",
            "chunk_id": "chunk-1",
            "evidence_revision_history": [
                {
                    "revision": 1,
                    "previous_source": {
                        "verified_quote": "Earlier exact source sentence.",
                    },
                }
            ],
        }
    ]
    incoming = [
        {
            "evidence_record_id": "evidence-live-a",
            "entity": "Actin 5C",
            "verified_quote": "Updated exact source sentence.",
            "page": 5,
            "section": "Results",
            "chunk_id": "chunk-2",
        }
    ]

    merged = runner._merge_evidence_records(existing, incoming)

    assert merged == [
        {
            **incoming[0],
            "evidence_revision_history": existing[0]["evidence_revision_history"],
        }
    ]


def test_safe_reset_run_context_token_logs_context_mismatch(caplog):
    def _raise_context_mismatch(_token):
        raise ValueError("token was created in a different Context")

    caplog.set_level(logging.WARNING, logger=runner.__name__)

    runner._safe_reset_run_context_token(
        label="evidence_workspace",
        reset_fn=_raise_context_mismatch,
        token=object(),
        trace_id="trace-file-ready",
        user_id="user-1",
    )

    assert "Skipped evidence_workspace context reset after async context switch" in caplog.text


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
            context_messages=[
                {"role": "user", "content": "older"},
                {"role": "assistant", "content": "previous answer"},
                {"role": "user", "content": "hello"},
            ],
            user_id="user-1",
            session_id="session-1",
            turn_id="turn-1",
            document_id="11111111-1111-1111-1111-111111111111",
            document_name="Paper A",
        )
    )

    assert events[0]["type"] == "RUN_STARTED"
    assert events[1]["type"] == "SUPERVISOR_START"
    assert events[-1]["type"] == "RUN_FINISHED"
    fallback_trace = events[0]["data"]["trace_id"]
    assert fallback_trace.startswith("chat-")
    assert captured["run_kwargs"]["trace_id"] == fallback_trace
    assert captured["run_kwargs"]["input_items"] == [
        {"role": "user", "content": "older"},
        {"role": "assistant", "content": "previous answer"},
        {"role": "user", "content": "hello"},
    ]
    assert captured["run_kwargs"]["chat_session_id"] == "session-1"
    assert captured["run_kwargs"]["chat_turn_id"] == "turn-1"
    assert captured["logged"][0][0] == fallback_trace


@pytest.mark.asyncio
async def test_run_agent_with_tracing_compacts_standard_chat_session_before_provider_call(monkeypatch):
    captured = {}
    order = []

    class _FakeCompactionSession:
        async def run_compaction(self, args):
            order.append("compact")
            captured["compaction_args"] = args

    class _FakeProvider:
        async def aclose(self):
            captured["provider_closed"] = True

    _patch_common_runtime(monkeypatch, captured)
    monkeypatch.setattr(runner, "get_max_turns", lambda: 4)
    monkeypatch.setattr(runner, "SafeLangfuseAsyncOpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "_build_request_openai_provider", lambda _client: _FakeProvider())
    monkeypatch.setattr(runner, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "set_live_event_list", lambda _events: None)
    monkeypatch.setattr(runner, "ResponseTextDeltaEvent", _FakeTextDelta)
    monkeypatch.setattr(runner, "provider_context_preflight", lambda **_kwargs: None)
    monkeypatch.setattr(runner, "write_extraction_trace_event", lambda **event: event)
    monkeypatch.setattr(runner, "write_stream_event", lambda *args, **kwargs: None)

    def _build_session(**kwargs):
        captured["session_kwargs"] = kwargs
        return _FakeCompactionSession()

    monkeypatch.setattr(runner, "build_standard_chat_compaction_session", _build_session)

    def _run_streamed(*args, **kwargs):
        order.append("run")
        captured["runner_kwargs"] = kwargs
        return _FakeRunResult(
            [_raw_response_stream_event(_FakeTextDelta("precall compacted"))],
            final_output="precall compacted",
        )

    monkeypatch.setattr(runner.Runner, "run_streamed", _run_streamed)

    events = await _collect_events(
        runner._run_agent_with_tracing(
            agent=SimpleNamespace(name="Supervisor", model="gpt-5.5", tools=[]),
            input_items=[{"role": "user", "content": "current"}],
            user_id="user-1",
            document_id=None,
            document_name=None,
            user_message="current",
            trace_id="trace-compact",
            chat_session_id="session-1",
            chat_turn_id="turn-1",
        )
    )

    assert order == ["compact", "run"]
    assert captured["compaction_args"] == {"compaction_mode": "input"}
    assert captured["session_kwargs"]["session_id"] == "session-1"
    assert captured["session_kwargs"]["current_turn_id"] == "turn-1"
    assert captured["runner_kwargs"]["session"].__class__.__name__ == "_FakeCompactionSession"
    assert events[-1]["type"] == "RUN_FINISHED"
    assert events[-1]["data"]["response"] == "precall compacted"


@pytest.mark.asyncio
async def test_run_agent_streamed_preserves_bound_prompt_runs_for_provided_agent(monkeypatch):
    captured = {}
    agent = SimpleNamespace(name="Flow Supervisor", model="gpt-5", tools=[])
    prompt = _prompt("flow prompt")

    clear_prompt_context()
    bind_prompt_run(
        agent,
        set_pending_prompts(
            agent.name,
            [prompt],
            effective_prompt_hash="hash-flow",
            layer_manifest=_manifest("flow_supervisor", "flow prompt", "hash-flow"),
        ),
    )

    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(runner, "clear_pending_configs", lambda: None)
    monkeypatch.setattr(runner, "reset_consecutive_call_tracker", lambda: None)
    monkeypatch.setattr(runner, "get_langfuse", lambda: None)
    monkeypatch.setattr(
        runner,
        "set_current_trace_id",
        lambda trace_id: captured.setdefault("trace_ids", []).append(trace_id),
    )

    async def _fake_run_agent_with_tracing(**_kwargs):
        yield {
            "type": "RUN_FINISHED",
            "data": {"response_length": 5, "tool_calls": 0, "agents_used": ["Flow Supervisor"]},
        }

    def _capture_prompt_logging(**_kwargs):
        captured["used_prompt_runs"] = get_used_prompt_runs()
        return len(captured["used_prompt_runs"])

    monkeypatch.setattr(runner, "_run_agent_with_tracing", _fake_run_agent_with_tracing)
    monkeypatch.setattr(runner, "_log_used_prompts_to_db", _capture_prompt_logging)

    try:
        events = await _collect_events(
            runner.run_agent_streamed(
                context_messages=[{"role": "user", "content": "hello"}],
                user_id="user-flow",
                agent=agent,
            )
        )
    finally:
        clear_prompt_context()

    assert events[-1]["type"] == "RUN_FINISHED"
    assert len(captured["used_prompt_runs"]) == 1
    used_run = captured["used_prompt_runs"][0]
    assert used_run.agent_name == "Flow Supervisor"
    assert used_run.prompts == [prompt]
    assert used_run.assembly is not None
    assert used_run.assembly.effective_prompt_hash == "hash-flow"


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
            context_messages=[{"role": "user", "content": "hello"}],
            user_id="user-override",
            supervisor_model="gpt-5.4-mini",
            specialist_model="gpt-5.4-mini",
            supervisor_temperature=0.0,
            specialist_temperature=0.0,
            supervisor_reasoning="minimal",
            specialist_reasoning="minimal",
        )
    )

    assert events[0]["type"] == "RUN_STARTED"
    assert events[0]["data"]["model"] == "gpt-5.4-mini"
    assert captured["supervisor_kwargs"]["model_override"] == "gpt-5.4-mini"
    assert captured["supervisor_kwargs"]["specialist_model_override"] == "gpt-5.4-mini"
    assert captured["supervisor_kwargs"]["temperature_override"] == 0.0
    assert captured["supervisor_kwargs"]["specialist_temperature_override"] == 0.0
    assert captured["supervisor_kwargs"]["reasoning_override"] == "minimal"
    assert captured["supervisor_kwargs"]["specialist_reasoning_override"] == "minimal"
    # The runner owns the full current-turn message. Passing it explicitly prevents
    # isolated specialists from seeing only a lossy supervisor-authored summary.
    assert captured["supervisor_kwargs"]["current_user_request"] == "hello"


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

        def set_trace_io(self, **kwargs):
            captured.setdefault("trace_io", []).append(kwargs)

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
            context_messages=[{"role": "user", "content": "longer message"}],
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
    assert captured["trace_io"][0] == {"input": {"query": "longer message"}}
    assert captured["trace_io"][-1] == {
        "input": {"query": "longer message"},
        "output": {"response": "grounded answer"},
    }
    assert captured["logged"][0][0] == "trace-abc"


@pytest.mark.asyncio
async def test_run_agent_streamed_passes_trace_context_to_langfuse(monkeypatch):
    captured = {}
    _patch_common_runtime(monkeypatch, captured)
    monkeypatch.setattr(runner, "flush_agent_configs", lambda _span: 0)

    class _RootSpan:
        trace_id = "trace-existing"
        id = "span-existing"

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
            context_messages=[{"role": "user", "content": "retry me"}],
            user_id="user-3",
            session_id="session-3",
            trace_context={"trace_id": "trace-existing"},
        )
    )

    assert events[0]["type"] == "RUN_STARTED"
    assert events[0]["data"]["trace_id"] == "trace-existing"
    assert captured["span_start"]["trace_context"] == {"trace_id": "trace-existing"}


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
        runner.run_agent_streamed(
            context_messages=[{"role": "user", "content": "hello"}],
            user_id="user-3",
        )
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
        runner.run_agent_streamed(
            context_messages=[{"role": "user", "content": "hello"}],
            user_id="user-4",
        )
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
            tools=[SimpleNamespace(name="inspect_results", description="Inspect results")],
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
        runner.run_agent_streamed(
            context_messages=[{"role": "user", "content": "hello"}],
            user_id="user-core",
        )
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
async def test_runner_traces_impossible_top_level_curation_shaped_output(monkeypatch):
    trace_events = []

    monkeypatch.setattr(runner, "SafeLangfuseAsyncOpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "OpenAIProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "set_live_event_list", lambda _events: None)
    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(
        runner,
        "structured_result_requires_evidence",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        runner,
        "extract_evidence_records_from_structured_result",
        lambda _structured_result: [],
    )
    monkeypatch.setattr(
        runner,
        "write_extraction_trace_event",
        lambda **event: trace_events.append(event) or event,
    )
    monkeypatch.setattr(
        runner.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(
            [],
            final_output={
                "domain_pack_id": "agr.alliance.gene_expression",
                "curatable_objects": [
                    {
                        "object_type": "gene_expression_annotation",
                        "pending_ref_id": "annotation-1",
                    }
                ],
                "metadata": {},
            },
        ),
    )

    emitted_events = [
        event
        async for event in runner._run_agent_with_tracing(
            agent=SimpleNamespace(
                name="Query Supervisor",
                tools=[],
                model="gpt-4o",
                output_type=None,
            ),
            input_items=[{"role": "user", "content": "extract expression"}],
            user_id="user-1",
            document_id=None,
            document_name=None,
            user_message="extract expression",
            trace_id="trace-curation-shaped",
        )
    ]

    assert not any(event.get("type") == "RUN_ERROR" for event in emitted_events)
    diagnostic_event = next(
        event
        for event in trace_events
        if event.get("event_type")
        == "extraction_builder.top_level_curation_shaped_structured_output"
    )
    assert diagnostic_event["trace_id"] == "trace-curation-shaped"
    assert diagnostic_event["output_summary"]["object_count"] == 1
    assert diagnostic_event["metadata"]["agent"] == "Query Supervisor"


@pytest.mark.asyncio
async def test_run_agent_with_tracing_skips_sentry_span_for_unlabeled_custom_agent(monkeypatch):
    monkeypatch.setattr(runner, "SafeLangfuseAsyncOpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "OpenAIProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "set_live_event_list", lambda _events: None)
    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(
        runner,
        "gen_ai_conversation_scope",
        lambda _conversation_id: pytest.fail("custom Agent Studio-style runs must not bind Sentry conversations"),
    )
    monkeypatch.setattr(
        runner,
        "gen_ai_invoke_agent_span",
        lambda **_kwargs: pytest.fail("custom Agent Studio-style runs must not start Sentry AI spans"),
    )
    monkeypatch.setattr(
        runner.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult([], final_output="ok"),
    )

    emitted_events = [
        event
        async for event in runner._run_agent_with_tracing(
            agent=SimpleNamespace(
                name="Agent Studio Custom Agent",
                tools=[],
                model="gpt-5.5",
                output_type=None,
            ),
            input_items=[{"role": "user", "content": "hello"}],
            user_id="user-1",
            document_id=None,
            document_name=None,
            user_message="hello",
            trace_id="trace-custom-agent",
        )
    ]

    assert any(event.get("type") == "RUN_FINISHED" for event in emitted_events)


@pytest.mark.asyncio
async def test_run_agent_with_tracing_starts_sentry_span_for_explicit_workflow(monkeypatch):
    calls = []

    class FakeSpan:
        def set_data(self, key, value):
            calls.append(("data", key, value))

    def _fake_sentry_span(**kwargs):
        calls.append(("span", kwargs))
        return _FakeContextManager(FakeSpan())

    monkeypatch.setattr(runner, "SafeLangfuseAsyncOpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "OpenAIProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "set_live_event_list", lambda _events: None)
    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(runner, "gen_ai_conversation_scope", lambda _conversation_id: _FakeContextManager())
    monkeypatch.setattr(runner, "gen_ai_invoke_agent_span", _fake_sentry_span)
    monkeypatch.setattr(
        runner.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult([], final_output="ok"),
    )

    emitted_events = [
        event
        async for event in runner._run_agent_with_tracing(
            agent=SimpleNamespace(
                name="Flow Supervisor",
                tools=[],
                model="gpt-5.5",
                output_type=None,
            ),
            input_items=[{"role": "user", "content": "run flow"}],
            user_id="user-1",
            document_id="doc-1",
            document_name="paper.pdf",
            user_message="run flow",
            trace_id="trace-flow",
            sentry_workflow="execute_flow",
            sentry_span_data={"ai_curation.flow.total_steps": 2},
        )
    ]

    assert any(event.get("type") == "RUN_FINISHED" for event in emitted_events)
    assert calls[0][0] == "span"
    assert calls[0][1]["workflow"] == "execute_flow"
    assert calls[0][1]["span_data"]["ai_curation.flow.total_steps"] == 2
    assert ("data", "ai_curation.tool_call.count", 0) in calls
    post_stream_span = next(
        call
        for call in calls
        if call[0] == "span" and call[1]["workflow"] == "execute_flow_post_stream"
    )
    assert post_stream_span[1]["output_preview"] == "ok"
    assert post_stream_span[1]["finalization_status"] == "accepted"


@pytest.mark.asyncio
async def test_runner_propagates_sdk_stream_errors(monkeypatch):
    sentry_calls = []

    class FakeSentrySpan:
        def set_data(self, key, value):
            sentry_calls.append(("data", key, value))

    def _fake_sentry_span(**kwargs):
        sentry_calls.append(("span", kwargs))
        return _FakeContextManager(FakeSentrySpan())

    monkeypatch.setattr(runner, "SafeLangfuseAsyncOpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "OpenAIProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "set_live_event_list", lambda _events: None)
    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(runner, "gen_ai_conversation_scope", lambda _conversation_id: _FakeContextManager())
    monkeypatch.setattr(runner, "gen_ai_invoke_agent_span", _fake_sentry_span)
    monkeypatch.setattr(
        runner.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeFailingRunResult(),
    )

    with pytest.raises(
        TimeoutError,
        match="Responses websocket connect timed out",
    ):
        async for _event in runner._run_agent_with_tracing(
            agent=SimpleNamespace(
                name="Flow Supervisor",
                tools=[],
                model="gpt-5.5",
                output_type=None,
            ),
            input_items=[{"role": "user", "content": "run flow"}],
            user_id="user-1",
            document_id=None,
            document_name=None,
            user_message="run flow",
            trace_id="trace-sdk-timeout",
            sentry_workflow="execute_flow",
        ):
            pass

    error_span = next(
        call
        for call in sentry_calls
        if call[0] == "span" and call[1]["workflow"] == "execute_flow_post_stream"
    )
    assert error_span[1]["finalization_status"] == "error"
    assert error_span[1]["validation_status"] == "error"
    assert error_span[1]["span_data"]["ai_curation.error.detail"]["error_type"] == "TimeoutError"
    assert error_span[1]["span_data"]["ai_curation.error.detail"]["phase"] == "runner_stream"
    assert ("data", "ai_curation.finalization.status", "error") in sentry_calls


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
            context_messages=[{"role": "user", "content": "hello"}],
            user_id="user-5",
            agent=SimpleNamespace(
                name="Flow Supervisor",
                model=SimpleNamespace(model="groq/stub-groq-model"),
                tools=[],
            ),
        )
    )

    event_types = [event["type"] for event in events]
    assert "SUPERVISOR_RETRY" in event_types
    assert event_types[-1] == "RUN_FINISHED"
    assert attempts["count"] == 2


@pytest.mark.asyncio
async def test_run_agent_streamed_requires_trailing_user_context_message(monkeypatch):
    captured = {}
    _patch_common_runtime(monkeypatch, captured)

    with pytest.raises(ValueError, match="context_messages must end with a user message"):
        await _collect_events(
            runner.run_agent_streamed(
                context_messages=[{"role": "assistant", "content": "not a prompt"}],
                user_id="user-invalid",
            )
        )


@pytest.mark.asyncio
async def test_run_agent_streamed_requires_context_message_list(monkeypatch):
    captured = {}
    _patch_common_runtime(monkeypatch, captured)

    with pytest.raises(TypeError, match="context_messages must be a list of message dicts"):
        await _collect_events(
            runner.run_agent_streamed(
                context_messages=None,
                user_id="user-invalid",
            )
        )


@pytest.mark.asyncio
async def test_run_agent_streamed_normalizes_flow_context_roles(monkeypatch):
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

    await _collect_events(
        runner.run_agent_streamed(
            context_messages=[
                {"role": "flow", "content": "previous flow memory"},
                {"role": "user", "content": "hello"},
            ],
            user_id="user-flow",
        )
    )

    assert captured["run_kwargs"]["input_items"] == [
        {"role": "assistant", "content": "previous flow memory"},
        {"role": "user", "content": "hello"},
    ]
