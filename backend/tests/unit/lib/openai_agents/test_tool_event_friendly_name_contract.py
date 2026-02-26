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


@pytest.mark.asyncio
async def test_runner_tool_events_always_include_friendly_name(monkeypatch):
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

    for event in tool_events:
        details = event.get("details", {})
        assert "friendlyName" in details
        assert isinstance(details["friendlyName"], str)
        assert details["friendlyName"].strip()


@pytest.mark.asyncio
async def test_specialist_tool_events_always_include_friendly_name(monkeypatch):
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

    for event in tool_events:
        details = event.get("details", {})
        assert "friendlyName" in details
        assert isinstance(details["friendlyName"], str)
        assert details["friendlyName"].strip()
