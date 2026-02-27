"""Contract tests for TOOL_START/TOOL_COMPLETE friendlyName emission."""

from types import SimpleNamespace

import pytest

from src.lib.openai_agents import runner, streaming_tools


class _FakeRunResult:
    def __init__(self, events, final_output="ok"):
        self._events = events
        self.final_output = final_output

    async def stream_events(self):
        for event in self._events:
            yield event


def _tool_call_stream_event(name: str, arguments: str = '{"query":"test"}'):
    return SimpleNamespace(
        type="run_item_stream_event",
        item=SimpleNamespace(
            type="tool_call_item",
            name=name,
            raw_item=SimpleNamespace(arguments=arguments),
        ),
    )


def _tool_output_stream_event(output: str = '{"summary":"ok"}'):
    return SimpleNamespace(
        type="run_item_stream_event",
        item=SimpleNamespace(
            type="tool_call_output_item",
            output=output,
        ),
    )


def _raw_response_stream_event(data):
    return SimpleNamespace(type="raw_response_event", data=data)


def _handoff_call_stream_event(target_name: str):
    return SimpleNamespace(
        type="run_item_stream_event",
        item=SimpleNamespace(
            type="handoff_call_item",
            target_agent=SimpleNamespace(name=target_name),
        ),
    )


def _handoff_output_stream_event(source_name: str):
    return SimpleNamespace(
        type="run_item_stream_event",
        item=SimpleNamespace(
            type="handoff_output_item",
            source_agent=SimpleNamespace(name=source_name),
        ),
    )


def _agent_updated_stream_event(agent_name: str):
    return SimpleNamespace(
        type="agent_updated_stream_event",
        new_agent=SimpleNamespace(name=agent_name),
    )


@pytest.mark.asyncio
async def test_runner_tool_events_emit_canonical_friendly_names(monkeypatch):
    fake_events = [
        _tool_call_stream_event("ask_gene_specialist"),
        _tool_output_stream_event(),
    ]

    monkeypatch.setattr(runner, "SafeLangfuseAsyncOpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "OpenAIProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(runner, "set_live_event_list", lambda _events: None)
    monkeypatch.setattr(
        runner.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(fake_events, final_output="done"),
    )

    agent = SimpleNamespace(
        name="Query Supervisor",
        tools=[
            SimpleNamespace(
                name="ask_gene_specialist",
                description="Ask the Gene Validation Agent",
            )
        ],
        model="gpt-4o",
    )

    emitted_events = [
        event
        async for event in runner._run_agent_with_tracing(
            agent=agent,
            input_items=[{"role": "user", "content": "validate genes"}],
            user_id="user-1",
            document_id=None,
            document_name=None,
            user_message="validate genes",
            trace_id="trace-1",
        )
    ]

    tool_events = [
        event for event in emitted_events if event.get("type") in {"TOOL_START", "TOOL_COMPLETE"}
    ]
    assert tool_events, "Expected TOOL_START/TOOL_COMPLETE events from runner stream"

    assert tool_events[0]["details"]["friendlyName"] == "Calling Gene Validation Agent..."
    assert tool_events[1]["details"]["friendlyName"] == "Gene Validation Agent complete"


@pytest.mark.asyncio
async def test_runner_emits_reasoning_file_ready_chat_output_and_handoff_events(monkeypatch):
    class _FakeTextDelta:
        def __init__(self, delta):
            self.delta = delta

    class _FakeArgsDelta:
        def __init__(self, delta):
            self.delta = delta

    class _FakeReasoningDelta:
        def __init__(self, delta):
            self.delta = delta

    file_output = (
        '{"file_id":"f-1","download_url":"/api/files/f-1/download","filename":"results.csv",'
        '"format":"csv","size_bytes":12,"mime_type":"text/csv"}'
    )
    fake_events = [
        _raw_response_stream_event(_FakeTextDelta("Hello ")),
        _raw_response_stream_event(_FakeArgsDelta('{"query":"x"}')),
        _raw_response_stream_event(_FakeReasoningDelta("thinking...")),
        _tool_call_stream_event("ask_chat_output_specialist"),
        _tool_output_stream_event(file_output),
        _handoff_call_stream_event("Gene Agent"),
        _handoff_output_stream_event("Query Supervisor"),
        _agent_updated_stream_event("Gene Agent"),
    ]

    monkeypatch.setattr(runner, "ResponseTextDeltaEvent", _FakeTextDelta)
    monkeypatch.setattr(runner, "ResponseFunctionCallArgumentsDeltaEvent", _FakeArgsDelta)
    monkeypatch.setattr(runner, "ResponseReasoningSummaryTextDeltaEvent", _FakeReasoningDelta)
    monkeypatch.setattr(runner, "SafeLangfuseAsyncOpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "OpenAIProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(runner, "set_live_event_list", lambda _events: None)
    monkeypatch.setattr(
        runner.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(
            fake_events,
            final_output={"answer": "All good", "citations": [], "sources": []},
        ),
    )

    agent = SimpleNamespace(
        name="Query Supervisor",
        tools=[SimpleNamespace(name="ask_chat_output_specialist", description="Ask the Chat Output Agent")],
        model="gpt-4o",
    )

    emitted_events = [
        event
        async for event in runner._run_agent_with_tracing(
            agent=agent,
            input_items=[{"role": "user", "content": "export results"}],
            user_id="user-1",
            document_id=None,
            document_name=None,
            user_message="export results",
            trace_id="trace-2",
        )
    ]
    event_types = [e.get("type") for e in emitted_events]

    assert "AGENT_GENERATING" in event_types
    assert "TEXT_MESSAGE_CONTENT" in event_types
    assert "TOOL_CALL_ARGS" in event_types
    assert "AGENT_THINKING" in event_types
    assert "CHAT_OUTPUT_READY" in event_types
    assert "FILE_READY" in event_types
    assert "HANDOFF_START" in event_types
    assert "CREW_START" in event_types
    assert "STRUCTURED_RESULT" in event_types
    assert "SUPERVISOR_COMPLETE" in event_types
    assert "RUN_FINISHED" in event_types


@pytest.mark.asyncio
async def test_runner_guardrail_yields_run_error_and_skips_completion(monkeypatch):
    monkeypatch.setattr(runner, "SafeLangfuseAsyncOpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "OpenAIProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(runner, "set_live_event_list", lambda _events: None)
    monkeypatch.setattr(
        runner.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(
            [],
            final_output={"answer": "No results found", "citations": [], "sources": []},
        ),
    )
    monkeypatch.setattr(
        runner.Answer,
        "model_validate",
        lambda data: SimpleNamespace(answer=data["answer"], citations=[], sources=[]),
    )
    monkeypatch.setattr(runner, "enforce_uncited_negative_guardrail", lambda _ans, _tools: "must search first")

    agent = SimpleNamespace(name="Query Supervisor", tools=[], model="gpt-4o")
    emitted_events = [
        event
        async for event in runner._run_agent_with_tracing(
            agent=agent,
            input_items=[{"role": "user", "content": "not found?"}],
            user_id="user-1",
            document_id=None,
            document_name=None,
            user_message="not found?",
            trace_id="trace-3",
        )
    ]
    event_types = [e.get("type") for e in emitted_events]

    assert "RUN_ERROR" in event_types
    assert "SUPERVISOR_COMPLETE" not in event_types
    assert "RUN_FINISHED" not in event_types


@pytest.mark.asyncio
async def test_specialist_tool_events_emit_humanized_internal_labels(monkeypatch):
    fake_events = [
        _tool_call_stream_event("search_document"),
        _tool_output_stream_event(),
    ]
    captured_events = []

    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(fake_events, final_output="done"),
    )

    agent = SimpleNamespace(
        name="Gene Validation Agent",
        tools=[],
        output_type=None,
        instructions="",
        model="gpt-4o",
    )

    result = await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="extract findings",
        specialist_name="Gene Validation Agent",
        max_turns=3,
        tool_name="ask_gene_specialist",
    )
    assert result == "done"

    tool_events = [
        event for event in captured_events if event.get("type") in {"TOOL_START", "TOOL_COMPLETE"}
    ]
    assert tool_events, "Expected TOOL_START/TOOL_COMPLETE events from specialist stream"

    assert tool_events[0]["details"]["friendlyName"] == "Gene Validation Agent: Search Document"
    assert tool_events[1]["details"]["friendlyName"] == "Gene Validation Agent: Search Document complete"


@pytest.mark.asyncio
async def test_specialist_required_tool_enforcement_raises_when_search_not_called(monkeypatch):
    captured_events = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult([], final_output="done"),
    )

    agent = SimpleNamespace(
        name="PDF Specialist",
        tools=[SimpleNamespace(name="search_document")],
        output_type=None,
        instructions="",
        model="gpt-4o",
    )

    with pytest.raises(streaming_tools.SpecialistOutputError):
        await streaming_tools.run_specialist_with_events(
            agent=agent,
            input_text="extract findings",
            specialist_name="PDF Specialist",
            max_turns=3,
            tool_name=None,
        )

    assert any(e.get("type") == "SPECIALIST_ERROR" for e in captured_events)


@pytest.mark.asyncio
async def test_specialist_emits_file_ready_for_fileinfo_output(monkeypatch):
    fake_events = [
        _tool_call_stream_event("save_csv_file"),
        _tool_output_stream_event(
            '{"file_id":"f1","download_url":"/api/files/f1/download","filename":"out.csv","format":"csv"}'
        ),
    ]
    captured_events = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(fake_events, final_output="done"),
    )

    agent = SimpleNamespace(
        name="Formatter Agent",
        tools=[],
        output_type=None,
        instructions="",
        model="gpt-4o",
    )

    result = await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="format this table",
        specialist_name="Formatter Agent",
        max_turns=3,
        tool_name=None,
    )
    assert result == "done"

    file_ready = [e for e in captured_events if e.get("type") == "FILE_READY"]
    assert len(file_ready) == 1
    assert file_ready[0]["details"]["filename"] == "out.csv"


@pytest.mark.asyncio
async def test_specialist_appends_batching_nudge_at_threshold(monkeypatch):
    monkeypatch.setattr(streaming_tools, "add_specialist_event", lambda _event: None)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult([], final_output="done"),
    )
    monkeypatch.setattr(streaming_tools, "_track_specialist_call", lambda _tool_name: 3)
    monkeypatch.setattr(streaming_tools, "_generate_batching_nudge", lambda _tool_name, _count: "\nNUDGE")

    agent = SimpleNamespace(
        name="Gene Validation Agent",
        tools=[],
        output_type=None,
        instructions="",
        model="gpt-4o",
    )

    result = await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="extract findings",
        specialist_name="Gene Validation Agent",
        max_turns=3,
        tool_name="ask_gene_specialist",
    )
    assert result == "done\nNUDGE"
