"""No paid model calls: exercise SDK filters with exact recoverability."""
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock
import json

import pytest
from agents import Agent, RunConfig
from agents.run import CallModelData, ModelInputData

from src.lib.openai_agents.model_context_view import ContextRecoveryError, ModelContextView, run_config_with_history
from src.lib.openai_agents.run_history import HistoryScope, RunHistory, exact_json


class MemoryStore:
    def __init__(self):
        self.rows = {}
        self.events = []
    def append(self, scope, kind, content, **kwargs):
        ref = str(len(self.rows) + 1)
        self.rows[ref] = json.loads(content)
        self.events.append((kind, ref))
        return ref


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("AGENT_CONTEXT_TRIGGER_ESTIMATED_TOKENS", "4000")
    monkeypatch.setenv("AGENT_CONTEXT_TARGET_ESTIMATED_TOKENS", "2500")
    monkeypatch.setenv("AGENT_CONTEXT_RECENT_TOOL_RESULTS", "1")
    monkeypatch.setenv("AGENT_CONTEXT_OUTPUT_RESERVE_TOKENS", "512")
    monkeypatch.setattr("src.lib.context.get_current_user_id", lambda: "owner")
    monkeypatch.setattr("src.lib.context.get_current_trace_id", lambda: "run")
    monkeypatch.setattr("src.lib.context.get_current_session_id", lambda: None)
    scope = HistoryScope("owner", "run", "invocation", "extractor", "invocation/")
    history = RunHistory(scope, MemoryStore())
    agent = Agent(name="extractor", instructions="Preserve negation and every candidate.")
    return history, agent


def call(agent, items):
    return CallModelData(ModelInputData(items, agent.instructions), agent, None)


def exchange(index, value):
    return [{"type": "function_call", "call_id": f"call-{index}", "name": "read_chunk", "arguments": '{"id":"c"}'},
            {"type": "function_call_output", "call_id": f"call-{index}", "output": exact_json(value)}]


@pytest.mark.asyncio
async def test_checkpoint_keeps_request_arguments_recent_and_opaque_items_and_recovers_exact_negation(setup):
    history, agent = setup
    early = {"content": "NOT present α😀. " * 1400, "next_span_offset": 4}
    items = [{"role": "user", "content": "Extract only negatives."},
             {"type": "reasoning", "id": "opaque", "encrypted_content": "unchanged"},
             *exchange(1, early), *exchange(2, {"status": "ok", "last": "recent"}),
             {"type": "function_call", "call_id": "pending", "name": "stage", "arguments": "{}"}]
    original = deepcopy(items)
    view = ModelContextView(history)
    result = await view.filter(call(agent, items))
    assert items == original
    assert result.instructions == agent.instructions
    assert result.input[:3] == items[:3]
    assert result.input[-3:] == items[-3:]
    receipt = json.loads(result.input[3]["output"])
    assert receipt["continuation_fields"] == {"next_span_offset": 4}
    restored = history.store.rows[receipt["history_event_id"]]
    assert json.loads(restored["output"]) == early
    assert view.last_metrics["externalized_output_count"] == 1
    assert view.last_metrics["after"]["estimated_total_tokens"] < view.last_metrics["before"]["estimated_total_tokens"]
    # Stable checkpoint at the next request; no prefix churn.
    later = await view.filter(call(agent, items + [{"role": "assistant", "content": "continue"}]))
    assert later.input[:len(items)] == result.input
    assert view.checkpoint == 1


@pytest.mark.asyncio
async def test_small_runs_are_unchanged_and_budget_counts_instructions_tools_and_reserve(setup):
    history, agent = setup
    agent.model_settings.max_tokens = 500
    view = ModelContextView(history)
    items = [{"role": "user", "content": "tiny task"}]
    result = await view.filter(call(agent, items))
    assert result.input == items
    assert view.last_metrics["checkpoint"] == 0
    assert view.last_metrics["before"]["output_reserve_tokens"] == 500
    assert view.last_metrics["before"]["outbound"]["instructions"]["chars"] > 0


@pytest.mark.asyncio
async def test_toolless_retry_restores_exact_bodies_before_new_native_compaction(setup):
    from src.lib.openai_agents.context_compaction import compaction_key

    history, agent = setup
    early = {"content": "NOT present α😀. " * 1400, "next_span_offset": 4}
    foreign = {"type": "compaction", "encrypted_content": "standalone-canonical-state"}
    items = [foreign, {"role": "user", "content": "Extract only negatives."},
             *exchange(1, early), *exchange(2, {"status": "ok"})]
    projected = await ModelContextView(history).filter(call(agent, items))
    assert json.loads(projected.input[3]["output"])["content_externalized"]
    native = {"type": "compaction", "encrypted_content": "summary-of-externalized-ref"}
    history.native_compaction_keys.add(compaction_key(native))
    canonical = items + [native, {"role": "user", "content": "Return the final object."}]
    original = deepcopy(canonical)
    retry = ModelContextView(history, allow_projection=False)
    result = await retry.filter(call(agent, canonical))
    assert agent.tools == []
    assert canonical == original
    assert result.input == items + [canonical[-1]]
    assert json.loads(result.input[3]["output"]) == early
    assert retry.last_metrics["compacted_prefix_items"] == 0
    assert retry.last_metrics["reset_native_compaction_items"] == 1
    assert retry.last_metrics["externalized_output_count"] == 0


@pytest.mark.asyncio
async def test_actionable_errors_are_not_summarized_away(setup):
    history, agent = setup
    items = [*exchange(1, {"status": "error", "errors": ["negative evidence missing"] * 1500}),
             *exchange(2, {"status": "ok"})]
    view = ModelContextView(history)
    result = await view.filter(call(agent, items))
    assert result.input == items
    assert view.last_metrics["above_soft_target"]
    assert history.healthy


@pytest.mark.asyncio
async def test_archive_failure_prohibits_eviction(setup):
    history, agent = setup
    def broken(*args, **kwargs):
        raise OSError("offline")
    history.store.append = broken
    items = [*exchange(1, {"data": "original " * 2500}), *exchange(2, {"ok": True})]
    with pytest.raises(ContextRecoveryError, match="history could not be saved"):
        await ModelContextView(history).filter(call(agent, items))
    assert "original" in items[1]["output"]


@pytest.mark.asyncio
async def test_disabling_projection_keeps_complete_history(setup, monkeypatch):
    history, agent = setup
    monkeypatch.setenv("AGENT_CONTEXT_VIEW_ENABLED", "false")
    items = [*exchange(1, {"data": "original " * 2500}), *exchange(2, {"ok": True})]
    assert (await ModelContextView(history).filter(call(agent, items))).input == items
    assert len(history.item_refs) == len(items)


@pytest.mark.asyncio
async def test_inherited_filter_does_not_mix_parent_and_child_history(setup):
    history, agent = setup
    parent = run_config_with_history(RunConfig(), history)
    child = RunHistory(replace(history.scope, invocation_id="child", lineage="invocation/child/"), MemoryStore())
    config = run_config_with_history(parent, child)
    await config.call_model_input_filter(call(agent, [{"role": "user", "content": "child input"}]))
    assert not history.store.rows
    assert child.store.rows


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [False, True])
async def test_real_sdk_loop_preserves_canonical_history_and_can_read_old_output(setup, monkeypatch, streamed):
    from agents import Runner, Model, ModelResponse, Usage, function_tool
    from src.lib.openai_agents import model_request_measurement as measurement
    measurement.install_model_request_measurement()
    measurements = []
    monkeypatch.setattr(measurement, "_publish", lambda value: measurements.append(deepcopy(value)))
    from openai.types.responses import Response, ResponseCompletedEvent, ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText
    from src.lib.openai_agents.run_history import history_tools, wrap_history_tools
    history, agent = setup
    expected = {"objects": [{"id": "A", "negated": True, "quote": "NOT detected. " * 2200}]}
    calls = []
    @function_tool
    async def lookup(index: int) -> dict:
        calls.append(index)
        return expected if index == 0 else {"status": "ok", "later": index}
    class TestModel(Model):
        def __init__(self):
            self.inputs = []
        async def get_response(self, instructions, input, *args, **kwargs):
            self.inputs.append(deepcopy(input))
            number = len(self.inputs)
            if number < 3:
                output = [ResponseFunctionToolCall(type="function_call", call_id=f"lookup-{number}",
                    name="lookup", arguments=json.dumps({"index": number - 1}))]
            else:
                output = [ResponseOutputMessage(type="message", id="final", role="assistant", status="completed",
                    content=[ResponseOutputText(type="output_text", text="done", annotations=[])])]
            return ModelResponse(output=output, usage=Usage(requests=1), response_id=f"response-{number}")
        async def stream_response(self, *args, **kwargs):
            response = await self.get_response(*args, **kwargs)
            terminal = Response(id=response.response_id, created_at=0, model="fixture", object="response",
                output=response.output, parallel_tool_calls=False, tool_choice="auto", tools=[],
                status="completed", temperature=None, top_p=None)
            yield ResponseCompletedEvent(type="response.completed", sequence_number=0, response=terminal)
    model = TestModel()
    agent.model = model
    agent.tools = [*wrap_history_tools([lookup], history), *history_tools(history)]
    agent.run_history = history
    config = run_config_with_history(RunConfig(tracing_disabled=True), history)
    # Allow the first result to remain for the immediate following model call;
    # checkpoint once there is an older completed output to externalize.
    from src.lib.openai_agents import config as runtime_config
    original_trigger = runtime_config.get_agent_context_trigger_estimated_tokens
    runtime_config.get_agent_context_trigger_estimated_tokens = lambda: 12000 if len(model.inputs) < 2 else 4000
    try:
        if streamed:
            result = Runner.run_streamed(agent, "Extract negatives; do not lose A.", run_config=config)
            async for _ in result.stream_events():
                pass
        else:
            result = await Runner.run(agent, "Extract negatives; do not lose A.", run_config=config)
    finally:
        runtime_config.get_agent_context_trigger_estimated_tokens = original_trigger
    assert calls == [0, 1]
    canonical = result.to_input_list()
    assert any(expected["objects"][0]["quote"] in item.get("output", "") for item in canonical if isinstance(item, dict))
    last = model.inputs[-1]
    receipt = next(json.loads(item["output"]) for item in last if isinstance(item, dict)
                   and item.get("type") == "function_call_output" and item["call_id"] == "lookup-1")
    assert receipt["content_externalized"] is True
    original_output = history.store.rows[receipt["history_event_id"]]["output"]
    import ast
    assert ast.literal_eval(original_output) == expected

    assert any(kind == "model_output" for kind, _ in history.store.events)
    completed = [m for m in measurements if m.get("outcome") == "completed"]
    assert completed
    assert completed[-1]["context_view"]["externalized_output_count"] == 1
    assert "provider_usage" in completed[-1]


@pytest.mark.asyncio
async def test_native_multimodal_outputs_are_protected_and_toolless_retry_never_inserts_unreadable_refs(setup):
    history, agent = setup
    image_output = [{"type": "input_image", "image_url": "data:image/png;base64," + "a" * 30000}]
    items = [*exchange(1, {}), *exchange(2, {"ok": True})]
    items[1]["output"] = image_output
    result = await ModelContextView(history).filter(call(agent, items))
    assert result.input == items
    assert items[1]["output"] == image_output
    text_items = [*exchange(1, {"text": "old evidence " * 3000}), *exchange(2, {"ok": True})]
    view = ModelContextView(history, allow_projection=False)
    result = await view.filter(call(agent, text_items))
    assert result.input == text_items
    assert not view.retired


@pytest.mark.asyncio
async def test_multiple_validations_and_discarded_proposal_remain_independently_recoverable(setup):
    from pathlib import Path
    history, agent = setup
    fixture = json.loads((Path(__file__).parent / "fixtures/context_recovery.json").read_text())
    values = [{**event, "full_explanation": "Exact original explanation β😀. " * 400} for event in fixture["events"]]
    items = [{"role": "user", "content": "Retain all original findings and the accepted object."}]
    for index, value in enumerate(values):
        items.extend(exchange(index, value))
    items.extend(exchange(len(values), {"status": "ok", "current_candidate_id": "candidate-B"}))
    result = await ModelContextView(history).filter(call(agent, items))
    recovered = []
    for item in result.input:
        if not isinstance(item, dict) or item.get("type") != "function_call_output":
            continue
        value = json.loads(item["output"])
        if value.get("content_externalized"):
            value = json.loads(history.store.rows[value["history_event_id"]]["output"])
        recovered.append(value)
    assert recovered[:-1] == values
    assert recovered[1]["decision_status"] == "rejected"
    assert recovered[2]["status"] == "valid" and recovered[2]["staged_fields"]["subject"] == "B"
    assert recovered[4]["status"] == "discarded"


@pytest.mark.asyncio
async def test_child_without_history_does_not_borrow_parent_filter(setup):
    history, agent = setup
    original = RunConfig()
    parent = run_config_with_history(original, history)
    child = run_config_with_history(parent, None)
    data = call(agent, [{"role": "user", "content": "child request"}])
    result = await child.call_model_input_filter(data)
    assert result.input == data.model_data.input
    assert not history.store.rows
    assert child.call_model_input_filter is not parent.call_model_input_filter


@pytest.mark.asyncio
async def test_thirteen_results_then_two_six_read_bursts_keep_original_event_identity(setup, monkeypatch):
    """October 15 regression: SDK repr reads must not become unreducible copies."""
    from types import SimpleNamespace
    from src.lib.openai_agents.run_history import history_tools
    history, agent = setup
    history.store.read = lambda scope, ref: {"event": {"event_id": ref}, "payload": history.store.rows[ref]}
    original = {"content": "Critical NOT present evidence. " * 400, "next_span_offset": 20}
    ref = await history.record("tool_result", original)
    items = [{"role": "user", "content": "Compare all sections, retaining negatives."}]
    for n in range(13):
        items.extend(exchange(n, original))
    view = ModelContextView(history)
    await view.filter(call(agent, items))
    reader = next(tool for tool in history_tools(history) if tool.name == "read_run_history")
    for burst in range(2):
        for n in range(6):
            call_id = f"read-{burst}-{n}"
            arguments = json.dumps({"event_id": ref, "detail_path": "payload.content", "detail_cursor": 0})
            result = await reader.on_invoke_tool(SimpleNamespace(tool_name=reader.name, tool_call_id=call_id, run_config=None), arguments)
            items.extend([
                {"type": "function_call", "call_id": call_id, "name": reader.name, "arguments": arguments},
                {"type": "function_call_output", "call_id": call_id, "output": str(result)},
            ])
        projected = await view.filter(call(agent, items))
        retired_reads = [json.loads(item["output"]) for item in projected.input
                         if item.get("type") == "function_call_output" and item["call_id"].startswith("read-")
                         and item["output"].startswith('{"content_externalized"')]
        assert retired_reads
        assert all(item["read"]["event_id"] == ref for item in retired_reads)
        assert all(item["read"]["detail_path"] == "payload.content" for item in retired_reads)
    assert view.last_metrics["retrieval_call_count"] == 12
    assert view.last_metrics["externalized_output_count"] >= 13
    assert history.store.rows[ref] == original


@pytest.mark.parametrize("enabled", ["true", "false"])
@pytest.mark.parametrize("failure", ["prior", "capture", "checkpoint"])
@pytest.mark.asyncio
async def test_archive_failure_preserves_numeric_source_view(setup, monkeypatch, enabled, failure):
    history, agent = setup
    monkeypatch.setenv("AGENT_CONTEXT_VIEW_ENABLED", enabled)
    monkeypatch.setattr("src.lib.weaviate_client.chunks.get_chunk_by_id", AsyncMock(
        return_value={"id": "12345678-1234-1234-1234-123456789abc", "document_id": "doc", "chunk_index": 7}))
    if failure == "prior":
        history.healthy = False
    else:
        append = history.store.append
        def fail(scope, kind, content, **kwargs):
            if failure == "capture" or kind == "context_view":
                raise OSError("archive offline")
            return append(scope, kind, content, **kwargs)
        history.store.append = fail
    items = exchange(1, {"chunk_id": "12345678-1234-1234-1234-123456789abc", "content": "Not expressed."})
    original = deepcopy(items)
    result = await ModelContextView(history).filter(call(agent, items))
    assert json.loads(result.input[1]["output"]) == {"chunk_index": 7, "document_id": "doc", "content": "Not expressed."}
    assert items == original
    assert not history.healthy
