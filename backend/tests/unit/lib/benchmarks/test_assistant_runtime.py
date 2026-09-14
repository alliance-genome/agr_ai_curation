import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.lib.agent_studio import openai_runtime as shared
from src.lib.benchmarks import assistant_runtime as runtime


def run_stream(**overrides):
    async def collect():
        return [event async for event in runtime.stream_benchmark_assistant(**{
            "input_items": [{"role": "user", "content": "Help with this paper"}],
            "definitions": [], "executor": AsyncMock(),
            "state": shared.AgentStudioRunState(trace_id="trace"),
            "session_id": "session", "user_id": "verified-human", **overrides,
        })]
    return asyncio.run(collect())


@pytest.mark.parametrize("name", ["run_benchmark", "publish_reference", "execute_sql",
                                 "propose_workshop_draft_update", None])
def test_disallowed_catalog_fails_before_provider(monkeypatch, name):
    provider = AsyncMock()
    monkeypatch.setattr(runtime, "stream_agent_studio_run", provider)
    with pytest.raises(ValueError, match="Unsupported"):
        run_stream(definitions=[{"name": name}])
    provider.assert_not_called()


def test_duplicate_catalog_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        run_stream(definitions=[{"name": "read_paper_reference_draft"}] * 2)


def test_shared_stream_has_only_selected_tools_and_preserves_events(monkeypatch):
    captured = {}
    cancellation = asyncio.Event()
    executor = AsyncMock(return_value=shared.ToolExecutionResult(
        full_output={"revision": 2}, provider_output='{"revision":2}',
    ))

    async def stream(**kwargs):
        captured.update(kwargs)
        tool, = kwargs["tools"]
        output = await tool.on_invoke_tool(
            SimpleNamespace(tool_call_id="call"), '{"draft_id":"draft"}',
        )
        assert output == '{"revision":2}'
        yield {"type": "TEXT_DELTA", "delta": "Draft found"}

    monkeypatch.setattr(runtime, "stream_agent_studio_run", stream)
    monkeypatch.setattr(runtime, "get_agent_studio_openai_max_turns", lambda: 7)
    monkeypatch.setattr(runtime, "get_agent_studio_openai_max_output_tokens", lambda: 321)
    events = run_stream(
        definitions=[{"name": "read_paper_reference_draft", "description": "Read selected draft",
                      "input_schema": {"type": "object", "properties": {}}}],
        executor=executor, cancel_event=cancellation,
    )
    assert events == [{"type": "TEXT_DELTA", "delta": "Draft found"}]
    assert captured["surface"] == "benchmark_assistant"
    assert captured["cancel_event"] is cancellation
    assert captured["user_id"] == "verified-human"
    assert captured["max_turns"] == 7
    assert captured["model_settings"].max_tokens == 321
    assert captured["state"].executed_tools[0].output == {"revision": 2}
    executor.assert_awaited_once_with("read_paper_reference_draft", {"draft_id": "draft"}, "call")


def test_assistance_trace_is_distinct_without_changing_workshop():
    values = dict(state=shared.AgentStudioRunState(trace_id="trace"),
                  session_id="session", user_id="human", model_provider=None)
    assert shared._run_config(**values).workflow_name == "Agent Studio AI Chat"
    assert shared._run_config(**values, surface="benchmark_assistant").workflow_name == "Benchmark AI Chat"
