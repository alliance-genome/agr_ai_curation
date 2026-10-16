"""Exercise native compaction through the pinned SDK, without paid requests."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
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
    monkeypatch.setattr("src.lib.openai_agents.context_capacity.NativeCapacityRequest.count", AsyncMock(return_value=1000))
    monkeypatch.setattr("src.lib.config.models_loader.get_model", lambda _: SimpleNamespace(
        context_window_tokens=1050000, max_input_tokens=922000, max_output_tokens=128000))
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
@pytest.mark.parametrize("sentry_wrapped", [False, True])
async def test_multiple_native_compactions_preserve_exact_retrieval_and_do_not_repeat_writes(history, monkeypatch, streamed, transport, sentry_wrapped):
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
            assert model_settings.context_management == [{"type": "compaction", "compact_threshold": 250000}]
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
    from sentry_sdk.integrations.openai_agents.patches.runner import _create_run_wrapper, _create_run_streamed_wrapper
    if streamed:
        run = _create_run_streamed_wrapper(Runner.run_streamed) if sentry_wrapped else Runner.run_streamed
        result = run(agent, "Extract and validate all objects.", run_config=config)
        async for _ in result.stream_events():
            pass
    else:
        run = _create_run_wrapper(Runner.run) if sentry_wrapped else Runner.run
        result = await run(agent, "Extract and validate all objects.", run_config=config)
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


def test_native_settings_are_provider_gated_and_do_not_mutate_settings(monkeypatch):
    client = AsyncOpenAI(api_key="fixture")
    settings = ModelSettings(max_tokens=1234)
    native = OpenAIResponsesModel(model="gpt-test", openai_client=client)
    monkeypatch.setenv("AGENT_CONTEXT_COMPACTION_TOKEN_THRESHOLD", "12345")
    changed = native_compaction_settings(native, settings)
    assert changed.context_management == [{"type": "compaction", "compact_threshold": 12345}]
    assert changed.max_tokens == 1234 and settings.context_management is None
    kwargs = native._build_response_create_kwargs(system_instructions="rules", input=[{"role":"user","content":"task"}],
        model_settings=changed, tools=[], output_schema=None, handoffs=[])
    assert kwargs["context_management"] == changed.context_management
    compatible = OpenAIChatCompletionsModel(model="other", openai_client=client)
    assert native_compaction_settings(compatible, settings) is settings
    assert native_compaction_settings(native, settings).context_management == changed.context_management
    monkeypatch.setenv("AGENT_CONTEXT_COMPACTION_ENABLED", "false")
    assert native_compaction_settings(native, settings) is settings


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


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "websocket"])
@pytest.mark.parametrize("streamed", [False, True])
async def test_real_sdk_wire_native_policy_repeated_compaction_and_more_than_sixty_four_turns(history, monkeypatch, transport, streamed):
    """Use the real SDK serializers and transport loops, with local responses only."""
    import httpx
    from src.lib.openai_agents import model_request_measurement as measurement
    from src.lib.openai_agents.config import get_max_turns
    measurement.install_model_request_measurement()
    monkeypatch.setattr(measurement, "_publish", lambda value: None)
    sent, writes = [], []

    @function_tool
    def save_once(number: int) -> dict:
        writes.append(number)
        return {"saved": number, "finding": "NOT observed α😀"}

    def response(wire):
        sent.append(deepcopy(wire))
        number = len(sent)
        assert wire["context_management"] == [{"type": "compaction", "compact_threshold": 250000}]
        assert wire.get("previous_response_id") is None
        if number > 1:
            assert any(item.get("type") == "function_call_output" and item.get("call_id") == f"call-{number-1}" for item in wire["input"])
        output = []
        if number in (20, 40, 60):
            output.append({"type": "compaction", "id": f"cmp-{number}", "encrypted_content": f"opaque-{number}"})
        if number < 70:
            output.append({"type": "function_call", "id": f"fc-{number}", "call_id": f"call-{number}",
                           "name": "save_once", "arguments": json.dumps({"number": number}), "status": "completed"})
        else:
            output.append(final_message().model_dump())
        return {"id": f"resp-{number}", "object": "response", "created_at": 0, "model": "gpt-6-astra",
                "status": "completed", "output": output, "parallel_tool_calls": True, "tools": [], "tool_choice": "auto",
                "usage": {"input_tokens": 1000, "output_tokens": 30, "total_tokens": 1030,
                          "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}

    def http_reply(request):
        wire = json.loads(request.content)
        if request.url.path.endswith("/input_tokens"):
            return httpx.Response(200, json={"input_tokens": 1000})
        payload = response(wire)
        if wire.get("stream"):
            event = {"type": "response.completed", "sequence_number": 1, "response": payload}
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n")
        return httpx.Response(200, json=payload)

    class Socket:
        state = SimpleNamespace(name="OPEN")
        async def send(self, frame):
            self.payload = response(json.loads(frame))
        async def recv(self):
            return json.dumps({"type": "response.completed", "sequence_number": 1, "response": self.payload})
        async def close(self, **kwargs):
            pass

    async with AsyncOpenAI(api_key="fixture", http_client=httpx.AsyncClient(transport=httpx.MockTransport(http_reply))) as client:
        kind = OpenAIResponsesModel if transport == "http" else OpenAIResponsesWSModel
        model = kind(model="gpt-6-astra", openai_client=client)
        if transport == "websocket":
            monkeypatch.setattr(model, "_open_websocket_connection", AsyncMock(return_value=Socket()))
        agent = Agent(name="wire fixture", model=model, tools=[save_once], model_settings=ModelSettings(store=False))
        config = run_config_with_history(RunConfig(tracing_disabled=True), history)
        if streamed:
            result = Runner.run_streamed(agent, "Preserve all findings", run_config=config, max_turns=get_max_turns())
            async for _ in result.stream_events():
                pass
        else:
            result = await Runner.run(agent, "Preserve all findings", run_config=config, max_turns=get_max_turns())
        assert result.final_output == "done"
        assert writes == list(range(1, 70))
        assert len(history.native_compaction_keys) == 3
        for number in (21, 41, 61):
            assert sent[number - 1]["input"][0]["encrypted_content"] == f"opaque-{number - 1}"
        await model.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_kind", ["input_item", "model_context_checkpoint", "model_output"])
async def test_archive_write_failure_cannot_publish_compaction_or_execute_tools(history, monkeypatch, failed_kind):
    from src.lib.openai_agents import model_request_measurement as measurement
    from src.lib.openai_agents.model_context_view import HistoryStorageUnavailable
    measurement.install_model_request_measurement()
    outcomes = []
    monkeypatch.setattr(measurement, "record_outcome", lambda value, **kwargs: outcomes.append(kwargs))
    original_append = history.store.append
    def fail(scope, kind, content, **metadata):
        if kind == failed_kind or (failed_kind == "context_input" and kind == "model_input"):
            raise OSError("storage fixture failure")
        return original_append(scope, kind, content, **metadata)
    history.store.append = fail
    tool_calls = []
    @function_tool
    def save() -> str:
        tool_calls.append("save")
        return "saved"
    class Model(OpenAIResponsesModel):
        async def get_response(self, *args, **kwargs):
            return ModelResponse(output=[ResponseCompactionItem(type="compaction", id="cmp", encrypted_content="opaque"),
                ResponseFunctionToolCall(type="function_call", name="save", call_id="save", arguments="{}")],
                usage=Usage(requests=1, input_tokens=123, output_tokens=17, total_tokens=140), response_id="fixture")
    client = AsyncOpenAI(api_key="fixture")
    model = Model(model="gpt-test", openai_client=client)
    agent = Agent(name="failure fixture", model=model, tools=[save])
    from agents.memory import SQLiteSession
    session = SQLiteSession("failure-fixture")
    with pytest.raises(HistoryStorageUnavailable):
        await Runner.run(agent, "Original exact evidence", session=session,
                         run_config=run_config_with_history(RunConfig(tracing_disabled=True), history))
    assert tool_calls == []
    assert not any(item.get("type") == "compaction" for item in await session.get_items())
    if failed_kind == "model_output":
        assert outcomes[-1]["outcome"] == "completed"
        assert outcomes[-1]["usage"]["input_tokens"] == 123
    await client.close()

@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "websocket"])
@pytest.mark.parametrize("ingress_source", ["tool", "initial"])
async def test_real_sdk_capacity_ingestion_preserves_exact_tool_result_without_repeating_write(monkeypatch, tmp_path, transport, ingress_source):
    import ast
    import httpx
    from agents import SQLiteSession
    from src.lib.openai_agents import model_request_measurement as measurement
    measurement.install_model_request_measurement()
    monkeypatch.setattr(measurement, "_publish", lambda value: None)
    monkeypatch.setenv("AGENT_MODEL_CAPACITY_OVERRIDES_JSON", json.dumps({"gpt-6-astra": {
        "context_window_tokens": 16000, "max_input_tokens": 15000, "max_output_tokens": 1000}}))
    monkeypatch.setenv("AGENT_CONTEXT_COMPACTION_TOKEN_THRESHOLD", "10000")
    monkeypatch.setattr("src.lib.context.get_current_user_id", lambda: "owner")
    monkeypatch.setattr("src.lib.context.get_current_trace_id", lambda: "run")
    monkeypatch.setattr("src.lib.context.get_current_session_id", lambda: None)
    archive = RunHistory(HistoryScope("owner", "run", "inv", "extractor", "inv/"), MemoryStore())
    body = "NOT observed α😀. Exact source.\n" * 1800
    writes, delivered, wires, compacted = [], {}, [], []

    @function_tool
    def save_once() -> str:
        writes.append("saved")
        return body if ingress_source == "tool" else "saved"

    def response(wire):
        wires.append(deepcopy(wire))
        assert len(json.dumps(wire["input"], ensure_ascii=False)) <= 15000
        for item in wire["input"]:
            if item.get("type") == "function_call_output":
                try:
                    page = json.loads(item["output"])
                except ValueError:
                    try:
                        page = ast.literal_eval(item["output"])
                    except (ValueError, SyntaxError):
                        continue
                if isinstance(page, dict) and "source_event_id" in page:
                    delivered[page["returned_range"]["start"]] = page["content"]
        choice = wire.get("tool_choice")
        forced = choice.get("name") if isinstance(choice, dict) else choice
        name = "read_pending_context" if forced == "read_pending_context" else ("save_once" if not writes else None)
        if ingress_source == "initial" and name == "save_once":
            assert forced == "save_once"  # Ingestion must not consume forced business intent.
        if ingress_source == "initial" and name is None:
            assert forced in (None, "auto")  # The actual business call consumes it once.
        n = len(wires)
        output = ([{"type": "reasoning", "id": f"rs-{n}", "summary": [], "encrypted_content": "reasoning"},
                   {"type": "function_call", "id": f"fc-{n}", "call_id": f"call-{n}", "name": name,
                    "arguments": "{}", "status": "completed"}] if name else [final_message().model_dump()])
        return {"id": f"resp-{n}", "object": "response", "created_at": 0, "model": "gpt-6-astra",
            "status": "completed", "output": output, "parallel_tool_calls": False, "tools": [], "tool_choice": "auto",
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                      "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}

    def http_reply(request):
        wire = json.loads(request.content)
        if request.url.path.endswith("/input_tokens"):
            return httpx.Response(200, json={"input_tokens": len(json.dumps(wire["input"], ensure_ascii=False))})
        if request.url.path.endswith("/compact"):
            compacted.append(wire)
            return httpx.Response(200, json={"id": "cmp", "object": "response.compaction", "created_at": 0,
                "output": [{"type": "compaction", "id": "compact", "encrypted_content": "standalone"},
                           {"role": "assistant", "type": "message", "id": "retained", "status": "completed", "content": [{"type": "output_text", "text": "Canonical retained message", "annotations": []}]}],
                "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}})
        return httpx.Response(200, json=response(wire))

    class Socket:
        state = SimpleNamespace(name="OPEN")
        async def send(self, frame):
            self.payload = response(json.loads(frame))
        async def recv(self):
            return json.dumps({"type": "response.completed", "sequence_number": 1, "response": self.payload})
        async def close(self, **kwargs):
            pass

    async with AsyncOpenAI(api_key="fixture", http_client=httpx.AsyncClient(transport=httpx.MockTransport(http_reply))) as client:
        cls = OpenAIResponsesModel if transport == "http" else OpenAIResponsesWSModel
        model = cls(model="gpt-6-astra", openai_client=client)
        if transport == "websocket":
            monkeypatch.setattr(model, "_open_websocket_connection", AsyncMock(side_effect=lambda *args, **kwargs: Socket()))
        agent = Agent(name="capacity fixture", model=model, tools=[save_once, *history_tools(archive)],
                      model_settings=ModelSettings(store=False, max_tokens=1000,
                          tool_choice="save_once" if ingress_source == "initial" else None))
        result = await Runner.run(agent, body if ingress_source == "initial" else "Save and inspect every source character", max_turns=64,
            run_config=run_config_with_history(RunConfig(tracing_disabled=True), archive),
            session=SQLiteSession("capacity", str(tmp_path / "session.db")))
        assert result.final_output == "done"
        assert writes == ["saved"]
        assert "".join(delivered[start] for start in sorted(delivered)) == body
        assert compacted and len(delivered) > 1
        assert len([kind for kind, _ in archive.store.events if kind == "context_ingress"]) == 1
        await model.close()

@pytest.mark.asyncio
async def test_token_count_failure_is_recorded_and_never_dispatches_generation(history, monkeypatch):
    from src.lib.openai_agents import model_request_measurement as measurement
    measurement.install_model_request_measurement()
    events, generations = [], []
    monkeypatch.setattr("src.lib.openai_agents.context_capacity.NativeCapacityRequest.count",
                        AsyncMock(side_effect=ConnectionError("count endpoint unavailable")))
    monkeypatch.setattr("src.lib.openai_agents.extraction_trace_events.write_extraction_trace_event",
                        lambda **event: events.append(event))

    class Model(OpenAIResponsesModel):
        async def get_response(self, *args, **kwargs):
            generations.append(True)
            raise AssertionError("Generation must not follow failed capacity measurement")

    async with AsyncOpenAI(api_key="fixture") as client:
        agent = Agent(name="count failure", model=Model(model="gpt-test", openai_client=client))
        with pytest.raises(ConnectionError, match="count endpoint unavailable"):
            await Runner.run(agent, "Exact source", run_config=run_config_with_history(
                RunConfig(tracing_disabled=True), history))
    assert generations == []
    capacity_event = next(event for event in events if event["event_type"] == "runtime.context_capacity")
    assert capacity_event["output_summary"]["outcome"] == "failed_before_generation"
    assert capacity_event["output_summary"]["error_type"] == "ConnectionError"
    assert any(kind == "model_error" for kind, _ in history.store.events)
