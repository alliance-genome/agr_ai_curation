"""Exercise the pinned SDK connection lifecycle and unchanged request replay."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from agents import Agent, ModelSettings, RunConfig, Runner, function_tool
from agents.models.openai_responses import OpenAIResponsesWSModel, ResponsesWebSocketError
from openai import AsyncOpenAI

from src.lib.openai_agents.config import build_default_model_retry


def rejection(**overrides):
    payload = {
        "type": "error", "status": 400, "response_id": "resp_rejected",
        "error": {"type": "invalid_request_error", "code": "unsupported_parameter",
                  "param": "input", "message": "fake-sensitive-error-content"},
    }
    for key, value in overrides.items():
        if key in {"code", "param"}:
            payload["error"][key] = value
        else:
            payload[key] = value
    return payload


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("fail_twice", [False, True])
async def test_tool_turn_replays_unchanged_on_fresh_socket(monkeypatch, caplog, streamed, fail_twice):
    monkeypatch.setenv("OPENAI_MODEL_MAX_RETRIES", "3")
    monkeypatch.setenv("OPENAI_WEBSOCKET_INPUT_REJECTION_MAX_RETRIES", "1")
    monkeypatch.setenv("OPENAI_MODEL_RETRY_INITIAL_DELAY", "0")
    monkeypatch.setenv("OPENAI_MODEL_RETRY_MAX_DELAY", "0")
    sent, sockets, tool_calls = [], [], []

    @function_tool
    def read_fixture() -> str:
        tool_calls.append(True)
        return "fake-sensitive-tool-output"

    class Socket:
        state = SimpleNamespace(name="OPEN")
        closed = False

        async def send(self, frame):
            assert not self.closed
            sent.append((self, frame))

        async def recv(self):
            number = len(sent)
            if number == 2 or (number == 3 and fail_twice):
                return json.dumps(rejection())
            output = [{"type": "function_call", "id": "fc_original",
                       "call_id": "call_44d8a23249ae4370aec5e205289fe58c",
                       "name": "read_fixture", "arguments": "{}", "status": "completed"}]
            if number > 1:
                output = [{"type": "message", "id": "msg_final", "role": "assistant",
                           "status": "completed", "content": [
                               {"type": "output_text", "text": "done", "annotations": []}]}]
            return json.dumps({"type": "response.completed", "sequence_number": 1, "response": {
                "id": f"resp_{number}", "object": "response", "created_at": 0,
                "model": "gpt-test", "status": "completed", "output": output,
                "parallel_tool_calls": True, "tools": [], "tool_choice": "auto",
            }})

        async def close(self):
            self.closed = True

    async def connect(*args, **kwargs):
        socket = Socket()
        sockets.append(socket)
        return socket

    def no_http(request):
        pytest.fail("The WebSocket retry must not send HTTP requests")

    async with AsyncOpenAI(api_key="fixture", max_retries=0, http_client=httpx.AsyncClient(
        transport=httpx.MockTransport(no_http)
    )) as client:
        model = OpenAIResponsesWSModel(model="gpt-test", openai_client=client)
        monkeypatch.setattr(model, "_open_websocket_connection", connect)
        agent = Agent(name="fixture", model=model, tools=[read_fixture],
                      model_settings=ModelSettings(store=False, retry=build_default_model_retry()))

        async def run():
            config = RunConfig(tracing_disabled=True)
            if streamed:
                result = Runner.run_streamed(agent, "fake-sensitive-user-content", run_config=config)
                async for _ in result.stream_events():
                    pass
            else:
                result = await Runner.run(agent, "fake-sensitive-user-content", run_config=config)
            assert result.final_output == "done"

        with caplog.at_level("WARNING", logger="src.lib.openai_agents.config"):
            if fail_twice:
                with pytest.raises(ResponsesWebSocketError) as caught:
                    await run()
                assert caught.value.code == "unsupported_parameter"
            else:
                await run()
        await model.close()

    assert len(sent) == 3
    assert len(sockets) == 2
    assert sent[0][0] is sent[1][0]
    assert sent[2][0] is not sent[1][0]
    assert sockets[0].closed
    assert sent[1][1] == sent[2][1]
    replay = json.loads(sent[2][1])["input"]
    call = next(item for item in replay if item.get("type") == "function_call")
    assert call["id"] == "fc_original"
    assert call["call_id"] == "call_44d8a23249ae4370aec5e205289fe58c"
    assert tool_calls == [True]
    warnings = [record for record in caplog.records if record.name == "src.lib.openai_agents.config"]
    assert len(warnings) == 1
    assert "response_id=resp_rejected; attempt=1" in warnings[0].getMessage()
    assert "fake-sensitive" not in warnings[0].getMessage()


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"status": 401}, {"status": None}, {"code": "invalid_request"},
    {"param": "tools"}, {"param": None}, {"type": "response.error"},
])
async def test_other_frames_do_not_gain_retry(monkeypatch, overrides):
    from agents.run_internal.model_retry import stream_response_with_retry
    monkeypatch.setenv("OPENAI_MODEL_MAX_RETRIES", "3")
    error = ResponsesWebSocketError(rejection(**overrides))
    calls = []

    async def stream():
        calls.append(True)
        raise error
        yield  # pragma: no cover

    rewind = AsyncMock()
    with pytest.raises(ResponsesWebSocketError) as caught:
        async for _ in stream_response_with_retry(
            get_stream=stream, rewind=rewind, retry_settings=build_default_model_retry(),
            get_retry_advice=lambda _: None, previous_response_id=None, conversation_id=None,
        ):
            pass
    assert caught.value is error
    assert calls == [True]
    rewind.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled_by", ["OPENAI_WEBSOCKET_INPUT_REJECTION_MAX_RETRIES", "OPENAI_MODEL_MAX_RETRIES"])
async def test_retry_can_be_disabled(monkeypatch, disabled_by):
    from agents.run_internal.model_retry import stream_response_with_retry
    monkeypatch.setenv(disabled_by, "0")
    calls = []

    async def stream():
        calls.append(True)
        raise ResponsesWebSocketError(rejection())
        yield  # pragma: no cover

    rewind = AsyncMock()
    with pytest.raises(ResponsesWebSocketError):
        async for _ in stream_response_with_retry(
            get_stream=stream, rewind=rewind, retry_settings=build_default_model_retry(),
            get_retry_advice=lambda _: None, previous_response_id=None, conversation_id=None,
        ):
            pass
    assert len(calls) == 1
    rewind.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("usable_output", [False, True])
async def test_no_http_retry_or_replay_after_output(monkeypatch, usable_output):
    from agents.run_internal.model_retry import stream_response_with_retry
    from openai import BadRequestError
    monkeypatch.setenv("OPENAI_MODEL_MAX_RETRIES", "3")
    error = ResponsesWebSocketError(rejection()) if usable_output else BadRequestError(
        "fake-sensitive-error-content",
        response=httpx.Response(400, request=httpx.Request("POST", "https://example.test/responses")),
        body=rejection()["error"],
    )
    calls = []

    async def stream():
        calls.append(True)
        if usable_output:
            yield {"type": "response.output_text.delta", "delta": "partial"}
        raise error

    rewind = AsyncMock()
    with pytest.raises(type(error)) as caught:
        async for _ in stream_response_with_retry(
            get_stream=stream, rewind=rewind, retry_settings=build_default_model_retry(),
            get_retry_advice=lambda _: None, previous_response_id="resp_previous", conversation_id=None,
        ):
            pass
    assert caught.value is error
    assert len(calls) == 1
    rewind.assert_not_called()


def test_retry_limit_rejects_more_than_one_resend(monkeypatch):
    monkeypatch.setenv("OPENAI_WEBSOCKET_INPUT_REJECTION_MAX_RETRIES", "2")
    with pytest.raises(ValueError, match="must be 0 or 1"):
        build_default_model_retry()
