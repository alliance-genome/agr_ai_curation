"""Coverage tests for streaming_tools retry and text-fallback paths."""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from src.lib.openai_agents import streaming_tools


class _Envelope(BaseModel):
    value: str


class _FakeRunResult:
    def __init__(self, events=None, final_output=None, new_items=None):
        self._events = events or []
        self.final_output = final_output
        self.new_items = new_items or []

    async def stream_events(self):
        for event in self._events:
            yield event

    def to_input_list(self):
        return [{"role": "user", "content": "prior query"}]


@pytest.fixture(autouse=True)
def _reset_streaming_state():
    streaming_tools.reset_consecutive_call_tracker()
    streaming_tools.clear_collected_events()
    streaming_tools.set_live_event_list(None)
    yield
    streaming_tools.reset_consecutive_call_tracker()
    streaming_tools.clear_collected_events()
    streaming_tools.set_live_event_list(None)


@pytest.mark.asyncio
async def test_run_specialist_uses_streaming_text_fallback_when_final_output_missing(monkeypatch):
    class ResponseTextDeltaEvent:
        def __init__(self, delta):
            self.delta = delta

    raw_event = SimpleNamespace(
        type="raw_response_event",
        data=ResponseTextDeltaEvent("fallback text"),
    )

    captured_events = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(events=[raw_event], final_output=None, new_items=[]),
    )

    agent = SimpleNamespace(
        name="Plain Text Specialist",
        tools=[],
        output_type=None,
        instructions="",
        model="gpt-4o",
    )

    result = await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="summarize findings",
        specialist_name="Plain Text Specialist",
        max_turns=3,
        tool_name=None,
    )

    assert result == "fallback text"
    assert any(
        e.get("type") == "SPECIALIST_TEXT_FALLBACK_SUCCESS"
        and e.get("details", {}).get("extraction_method") == "streaming_text_fallback"
        for e in captured_events
    )


@pytest.mark.asyncio
async def test_run_specialist_retry_succeeds_when_initial_output_missing(monkeypatch):
    first = _FakeRunResult(events=[], final_output=None, new_items=[])
    second = _FakeRunResult(events=[], final_output=_Envelope(value="ok"), new_items=[])
    calls = {"count": 0}

    def _run_streamed(*args, **kwargs):
        calls["count"] += 1
        return first if calls["count"] == 1 else second

    captured_events = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(streaming_tools.Runner, "run_streamed", _run_streamed)

    agent = SimpleNamespace(
        name="Structured Specialist",
        tools=[],
        output_type=_Envelope,
        instructions="",
        model="gpt-4o",
    )

    result = await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="extract structured output",
        specialist_name="Structured Specialist",
        max_turns=3,
        tool_name=None,
    )

    assert calls["count"] == 2
    assert result == '{"value": "ok"}'
    assert any(e.get("type") == "SPECIALIST_RETRY" for e in captured_events)
    assert any(e.get("type") == "SPECIALIST_RETRY_SUCCESS" for e in captured_events)


@pytest.mark.asyncio
async def test_run_specialist_retry_raises_when_retry_also_missing_output(monkeypatch):
    first = _FakeRunResult(events=[], final_output=None, new_items=[])
    second = _FakeRunResult(events=[], final_output=None, new_items=[])
    calls = {"count": 0}

    def _run_streamed(*args, **kwargs):
        calls["count"] += 1
        return first if calls["count"] == 1 else second

    captured_events = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(streaming_tools.Runner, "run_streamed", _run_streamed)

    agent = SimpleNamespace(
        name="Structured Specialist",
        tools=[],
        output_type=_Envelope,
        instructions="",
        model="gpt-4o",
    )

    with pytest.raises(streaming_tools.SpecialistOutputError):
        await streaming_tools.run_specialist_with_events(
            agent=agent,
            input_text="extract structured output",
            specialist_name="Structured Specialist",
            max_turns=3,
            tool_name=None,
        )

    assert calls["count"] == 2
    assert any(e.get("type") == "SPECIALIST_RETRY" for e in captured_events)
    assert any(e.get("type") == "SPECIALIST_ERROR" for e in captured_events)
