"""Exercise native compaction through the pinned SDK, without paid requests."""
from copy import deepcopy
from types import SimpleNamespace
import json

import pytest
from agents import Agent, ModelResponse, ModelSettings, RunConfig, Runner, Usage, function_tool
from agents.models.openai_responses import OpenAIResponsesModel, OpenAIResponsesWSModel
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI
from openai.types.responses import (
    Response, ResponseCompactionItem, ResponseCompletedEvent, ResponseFunctionToolCall,
    ResponseOutputMessage, ResponseOutputText,
)

from src.lib.openai_agents.context_compaction import (
    compacted_window, compaction_key, native_compaction_settings, compaction_progress_event,
)
from src.lib.openai_agents.model_context_view import run_config_with_history
from src.lib.openai_agents.run_history import HistoryScope, RunHistory, history_tools, wrap_history_tools


class MemoryStore:
    def __init__(self):
        self.rows = {}
        self.events = []

    def append(self, scope, kind, content, **metadata):
        ref = str(len(self.rows) + 1)
        self.rows[ref] = json.loads(content)
        self.events.append((kind, ref))
        return ref

    def read(self, scope, event_id):
        return {"event": {"event_id": event_id}, "payload": self.rows[event_id]}


@pytest.fixture
def history(monkeypatch):
    monkeypatch.setattr("src.lib.context.get_current_user_id", lambda: "owner")
    monkeypatch.setattr("src.lib.context.get_current_trace_id", lambda: "run")
    monkeypatch.setattr("src.lib.context.get_current_session_id", lambda: None)
    return RunHistory(HistoryScope("owner", "run", "inv", "extractor", "inv/"), MemoryStore())


def final_message():
    return ResponseOutputMessage(type="message", id="final", role="assistant", status="completed",
        content=[ResponseOutputText(type="output_text", text="done", annotations=[])])


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("transport", ["http", "websocket"])
async def test_multiple_native_compactions_preserve_exact_retrieval_and_do_not_repeat_writes(history, monkeypatch, streamed, transport):
    from src.lib.openai_agents import model_request_measurement as measurement
    measurement.install_model_request_measurement()
    reports = []
    monkeypatch.setattr(measurement, "_publish", lambda value: reports.append(deepcopy(value)))
    mutations = []
    evidence = {"candidate_id": "A", "negated": True, "quote": "NOT observed in section one. α😀",
                "validation": {"proposal": "A", "accepted_object": "B", "unresolved": ["section two comparison"]}}

    @function_tool
    async def read_source() -> dict:
        return evidence

    @function_tool
    async def save_result(candidate_id: str) -> dict:
        mutations.append(candidate_id)
        return {"saved": True, "candidate_id": candidate_id}

    base = OpenAIResponsesWSModel if transport == "websocket" else OpenAIResponsesModel

    class NativeFixture(base):
        def __init__(self):
            super().__init__(model="gpt-test", openai_client=AsyncOpenAI(api_key="fixture"))
            self.inputs = []

        async def get_response(self, instructions, input, model_settings, *args, **kwargs):
            self.inputs.append(deepcopy(input))
            assert model_settings.context_management == [{"type": "compaction", "compact_threshold": 60000}]
            step = len(self.inputs)
            if step == 1:
                output = [ResponseFunctionToolCall(type="function_call", call_id="source", name="read_source", arguments="{}")]
            elif step == 2:
                output = [ResponseCompactionItem(type="compaction", id="cmp1", encrypted_content="opaque-one"),
                          ResponseFunctionToolCall(type="function_call", call_id="save", name="save_result", arguments='{"candidate_id":"B"}')]
            elif step == 3:
                assert input[0]["type"] == "compaction"
                assert not any(item.get("call_id") == "source" for item in input)
                ref = next(ref for kind, ref in history.store.events if kind == "tool_result" and history.store.rows[ref] == evidence)
                output = [ResponseCompactionItem(type="compaction", id="cmp2", encrypted_content="opaque-two"),
                          ResponseFunctionToolCall(type="function_call", call_id="recover", name="read_run_history",
                              arguments=json.dumps({"event_id": ref, "detail_path": "payload"}))]
            else:
                assert input[0]["encrypted_content"] == "opaque-two"
                recovered = next(item["output"] for item in input if item.get("call_id") == "recover" and item["type"] == "function_call_output")
                assert "NOT observed" in recovered and "section two comparison" in recovered
                assert "accepted_object" in recovered
                output = [final_message()]
            return ModelResponse(output=output, usage=Usage(requests=1), response_id=f"response-{step}")

        async def stream_response(self, *args, **kwargs):
            response = await self.get_response(*args, **kwargs)
            terminal = Response.model_construct(id=response.response_id, created_at=0, model="gpt-test",
                object="response", status="completed", output=response.output)
            yield ResponseCompletedEvent(type="response.completed", sequence_number=0, response=terminal)

    model = NativeFixture()
    agent = Agent(name="extractor", instructions="Reconcile both sections and preserve negation.", model=model)
    agent.run_history = history
    agent.tools = [*wrap_history_tools([read_source, save_result], history), *history_tools(history)]
    config = run_config_with_history(RunConfig(tracing_disabled=True), history)
    if streamed:
        result = Runner.run_streamed(agent, "Extract and validate all objects.", run_config=config)
        async for _ in result.stream_events():
            pass
    else:
        result = await Runner.run(agent, "Extract and validate all objects.", run_config=config)
    assert result.final_output == "done"
    assert mutations == ["B"]
    assert len(history.native_compaction_keys) == 2
    assert sum(record.get("compaction_output_count", 0) for record in reports) == 2
    assert any(history.store.rows[ref] == evidence for kind, ref in history.store.events if kind == "tool_result")
    checkpoint_ref = [ref for kind, ref in history.store.events if kind == "model_context_checkpoint"][-1]
    checkpoint = history.store.rows[checkpoint_ref]
    assert checkpoint["complete"]
    assert [history.store.rows[ref] for ref in checkpoint["items"]] == model.inputs[-1]
    assert set(checkpoint["completed_tool_call_ids"]) == {"source", "save", "recover"}
    await model.close()


def test_native_settings_are_provider_gated_and_do_not_mutate_settings(history, monkeypatch):
    client = AsyncOpenAI(api_key="fixture")
    settings = ModelSettings(max_tokens=1234)
    agent = SimpleNamespace(run_history=history)
    native = OpenAIResponsesModel(model="gpt-test", openai_client=client)
    monkeypatch.setenv("AGENT_CONTEXT_COMPACTION_TOKEN_THRESHOLD", "12345")
    changed = native_compaction_settings(native, agent, settings)
    assert changed.context_management == [{"type": "compaction", "compact_threshold": 12345}]
    assert changed.max_tokens == 1234 and settings.context_management is None
    kwargs = native._build_response_create_kwargs(system_instructions="rules", input=[{"role":"user","content":"task"}],
        model_settings=changed, tools=[], output_schema=None, handoffs=[])
    assert kwargs["context_management"] == changed.context_management
    compatible = OpenAIChatCompletionsModel(model="other", openai_client=client)
    assert native_compaction_settings(compatible, agent, settings) is settings
    history.healthy = False
    assert native_compaction_settings(native, agent, settings) is settings
    history.healthy = True
    monkeypatch.setenv("AGENT_CONTEXT_COMPACTION_ENABLED", "false")
    assert native_compaction_settings(native, agent, settings) is settings


def test_compaction_preserves_unpaired_calls_and_standalone_canonical_window():
    opaque = {"type": "compaction", "encrypted_content": "opaque"}
    call = {"type": "function_call", "call_id": "pending", "name": "save", "arguments": "{}"}
    output = {"type": "function_call_output", "call_id": "pending", "output": "saved"}
    original = [{"role": "user", "content": "exact original task"}, call, opaque, output]
    assert compacted_window(original, {compaction_key(opaque)}) == original
    assert compacted_window(original[:-1], {compaction_key(opaque)}) == original[:-1]
    standalone = [original[0], opaque, {"role": "assistant", "content": "retained by provider"}]
    assert compacted_window(standalone, set()) == standalone


def test_compaction_progress_does_not_expose_encrypted_content():
    data = SimpleNamespace(type="response.output_item.added",
                           item=SimpleNamespace(type="compaction", encrypted_content="secret"))
    event = compaction_progress_event(data, "extractor")
    assert event["type"] == "AGENT_GENERATING"
    assert "secret" not in str(event)
    assert "continuing your request" in event["details"]["message"]
