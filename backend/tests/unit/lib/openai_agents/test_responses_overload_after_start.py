"""OpenAI's overload frame after the response started (ALL-1704).

Drives the real pinned SDK: ``Runner.run`` with the app's ``MeasuredModel``,
the real ``OpenAIResponsesWSModel`` and its retry loop, over a fake websocket
that sends the production frame order: ``response.created`` (0),
``response.in_progress`` (1), then the typed overload ``error`` frame (2).
No request leaves the process.
"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json
import logging

import pytest
from agents import Agent, ModelSettings, RunConfig, Runner
from agents.models.openai_responses import OpenAIResponsesWSModel, ResponsesWebSocketError
from openai import AsyncOpenAI

from src.lib.openai_agents.config import build_default_model_retry

_FRAME_MESSAGE = "Our servers are currently overloaded. Please try again later."


def _response(status="in_progress", output=()):
    return {"id": "resp_fixture", "object": "response", "created_at": 0, "model": "gpt-test",
            "status": status, "output": list(output), "parallel_tool_calls": True, "tools": [],
            "tool_choice": "auto"}


def _created():
    return {"type": "response.created", "sequence_number": 0, "response": _response()}


def _in_progress():
    return {"type": "response.in_progress", "sequence_number": 1, "response": _response()}


def _overload(sequence_number=2, *, error_type="service_unavailable_error",
              code="server_is_overloaded", headers=None):
    """The frame as logged on production on 2026-11-23 (both failures)."""
    return {"type": "error", "error": {
        "type": error_type, "code": code,
        "headers": {"retry-after": "6", "retry-after-ms": "6000"} if headers is None else headers,
        "message": _FRAME_MESSAGE, "param": None,
    }, "sequence_number": sequence_number, "response_id": "resp_fixture"}


_FINAL_MESSAGE = {"type": "message", "id": "msg_final", "role": "assistant", "status": "completed",
                  "content": [{"type": "output_text", "text": "done", "annotations": []}]}


def _completed():
    usage = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
             "input_tokens_details": {"cached_tokens": 0},
             "output_tokens_details": {"reasoning_tokens": 0}}
    return {"type": "response.completed", "sequence_number": 3,
            "response": {**_response("completed", [_FINAL_MESSAGE]), "usage": usage}}


_PRODUCTION_ORDER = [_created(), _in_progress(), _overload()]
_SUCCESS = [_created(), _in_progress(), _completed()]
_TOOL_CALL_ADDED = {"type": "response.output_item.added", "sequence_number": 2, "output_index": 0,
                    "item": {"type": "function_call", "id": "fc_fixture", "call_id": "call_fixture",
                             "name": "lookup_record", "arguments": "", "status": "in_progress"}}
_TEXT_DELTA = {"type": "response.output_text.delta", "sequence_number": 2, "item_id": "msg_final",
               "output_index": 0, "content_index": 0, "delta": "partial", "logprobs": []}
_MESSAGE_ADDED = {"type": "response.output_item.added", "sequence_number": 2, "output_index": 0,
                  "item": {"type": "message", "id": "msg_final", "role": "assistant",
                           "status": "in_progress", "content": []}}


class _Socket:
    """One websocket connection that replays a fixed list of server frames."""

    state = SimpleNamespace(name="OPEN")

    def __init__(self, frames, sent):
        self._frames = list(frames)
        self._sent = sent

    async def send(self, frame):
        self._sent.append(json.loads(frame))

    async def recv(self):
        return json.dumps(self._frames.pop(0))

    async def close(self, **kwargs):
        pass


@pytest.fixture
def harness(monkeypatch):
    """Real SDK retry loop with the app's measurement; sleeps recorded, not slept."""
    from agents.run_internal import model_retry
    from src.lib.openai_agents import model_request_measurement as measurement

    measurement.install_model_request_measurement()
    reports = []
    monkeypatch.setattr(measurement, "_publish", lambda value: reports.append(deepcopy(value)))
    delays = []

    async def record_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(model_retry, "_sleep_for_retry", record_sleep)
    monkeypatch.setenv("OPENAI_MODEL_MAX_RETRIES", "3")
    monkeypatch.setenv("OPENAI_MODEL_RETRY_INITIAL_DELAY", "0")
    monkeypatch.setenv("OPENAI_MODEL_RETRY_MAX_DELAY", "0")

    async def run(connections, *, previous_response_id=None):
        sent = []
        sockets = [_Socket(frames, sent) for frames in connections]
        client = AsyncOpenAI(api_key="fixture", max_retries=0)
        model = OpenAIResponsesWSModel(model="gpt-test", openai_client=client)
        monkeypatch.setattr(model, "_open_websocket_connection", AsyncMock(side_effect=sockets))
        agent = Agent(name="reviewer", instructions="Review the packet.", model=model,
                      model_settings=ModelSettings(store=False, retry=build_default_model_retry()))
        error = result = None
        try:
            result = await Runner.run(agent, "Review this packet.", run_config=RunConfig(tracing_disabled=True),
                                      previous_response_id=previous_response_id)
        except ResponsesWebSocketError as raised:
            error = raised
        finally:
            await model.close()
            await client.close()
        return SimpleNamespace(result=result, error=error, sent=sent, delays=delays,
                               reports=[r for r in reports if r.get("runtime") == "agents_sdk"])

    return run


def _resend_logs(caplog):
    return [r for r in caplog.records if r.name == "src.lib.openai_agents.config"]


@pytest.mark.asyncio
async def test_non_streamed_overload_after_response_start_is_resent_and_succeeds(harness, caplog):
    with caplog.at_level(logging.INFO):
        run = await harness([_PRODUCTION_ORDER, _SUCCESS])

    assert run.error is None and run.result.final_output == "done"
    assert len(run.sent) == 2 and run.sent[1] == run.sent[0], "the same request is resent unchanged"
    assert run.delays == [6.0], "the frame's retry-after is honoured"
    assert [(r["attempt"], r["outcome"]) for r in run.reports] == [(1, "provider_error"), (2, "completed")]
    logs = _resend_logs(caplog)
    assert len(logs) == 1 and "after the response started" in logs[0].getMessage()
    assert "retry 1/3" in logs[0].getMessage()
    assert _FRAME_MESSAGE not in caplog.text, "no frame content in logs"
    assert "Retrying transient" not in caplog.text


@pytest.mark.asyncio
async def test_overload_after_response_start_raises_the_original_error_when_the_budget_is_spent(harness, caplog):
    with caplog.at_level(logging.INFO):
        run = await harness([_PRODUCTION_ORDER] * 4)

    assert isinstance(run.error, ResponsesWebSocketError)
    assert (run.error.error_type, run.error.code) == ("service_unavailable_error", "server_is_overloaded")
    assert len(run.sent) == 4, "the first request and OPENAI_MODEL_MAX_RETRIES=3 resends"
    assert run.delays == [6.0, 6.0, 6.0]
    assert [r["attempt"] for r in run.reports] == [1, 2, 3, 4]
    assert "retry 3/3; retry_budget_exhausted=True" in _resend_logs(caplog)[-1].getMessage()


@pytest.mark.asyncio
async def test_retry_after_seconds_header_is_used_when_milliseconds_are_absent(harness):
    run = await harness([[_created(), _in_progress(), _overload(headers={"retry-after": "3"})], _SUCCESS])
    assert run.error is None and run.delays == [3.0]


@pytest.mark.asyncio
async def test_without_a_retry_after_header_the_configured_backoff_applies(harness):
    run = await harness([[_created(), _in_progress(), _overload(headers={})], _SUCCESS])
    assert run.error is None and run.delays == [0.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("output_event", [_TOOL_CALL_ADDED, _TEXT_DELTA, _MESSAGE_ADDED],
                         ids=["tool-call", "text-delta", "output-item"])
async def test_overload_after_output_is_not_resent(harness, caplog, output_event):
    frames = [_created(), _in_progress(), output_event, _overload(sequence_number=3)]
    with caplog.at_level(logging.INFO):
        run = await harness([frames, _SUCCESS])

    assert isinstance(run.error, ResponsesWebSocketError) and run.error.code == "server_is_overloaded"
    assert len(run.sent) == 1 and run.delays == []
    logs = _resend_logs(caplog)
    assert len(logs) == 1 and "Not resending" in logs[0].getMessage()
    assert "reason=output_started" in logs[0].getMessage()
    assert _FRAME_MESSAGE not in caplog.text and "partial" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type,code", [
    ("service_unavailable_error", "server_busy"),
    ("rate_limit_error", "rate_limit_exceeded"),
    ("server_error", "server_is_overloaded"),
], ids=["other-code", "rate-limit", "other-type"])
async def test_a_different_error_after_response_start_is_not_resent(harness, caplog, error_type, code):
    frames = [_created(), _in_progress(), _overload(error_type=error_type, code=code)]
    with caplog.at_level(logging.INFO):
        run = await harness([frames, _SUCCESS])

    assert isinstance(run.error, ResponsesWebSocketError) and run.error.code == code
    assert len(run.sent) == 1 and run.delays == []
    assert _resend_logs(caplog) == []


@pytest.mark.asyncio
async def test_a_stateful_request_is_not_resent_after_response_start(harness, caplog):
    with caplog.at_level(logging.INFO):
        run = await harness([_PRODUCTION_ORDER, _SUCCESS], previous_response_id="resp_previous")

    assert isinstance(run.error, ResponsesWebSocketError)
    assert len(run.sent) == 1 and run.sent[0]["previous_response_id"] == "resp_previous"
    logs = _resend_logs(caplog)
    assert len(logs) == 1 and "reason=stateful_request" in logs[0].getMessage()


@pytest.mark.asyncio
async def test_first_frame_overload_is_still_resent(harness, caplog):
    with caplog.at_level(logging.INFO):
        run = await harness([[_overload(sequence_number=0)], _SUCCESS])

    assert run.error is None and run.result.final_output == "done"
    assert len(run.sent) == 2 and run.delays == [6.0]
    assert "Retrying transient Responses WebSocket overload frame" in caplog.text


@pytest.mark.asyncio
async def test_review_provider_calls_are_reserved_and_settled_once_per_attempt(harness, monkeypatch):
    from src.lib.validation_history.store import HistoryScope
    from src.lib.validation_review import provider as review

    admissions, settlements = [], []

    def reserve(*args, **kwargs):
        admissions.append(kwargs)
        return str(len(admissions))

    monkeypatch.setattr(review.admission, "reserve", reserve)
    monkeypatch.setattr(review.admission, "reconcile", lambda *a, **kw: settlements.append(kw["actual"]))
    monkeypatch.setattr(review, "remaining_seconds", lambda _: 1800)
    budget = review.ExecutionBudget(HistoryScope("owner", "11111111-1111-1111-1111-111111111111", "session"),
                                    "episode", "initial_tokens", False)
    with review.budget_scope(budget):
        run = await harness([_PRODUCTION_ORDER, _SUCCESS])

    assert run.error is None and len(run.sent) == 2
    assert len(admissions) == len(settlements) == 2
    # The failed attempt reported no usage, so it closes as uncertain and keeps
    # its reserved charge (admission.reconcile); the resend settles its own.
    assert settlements == [None, {"initial_tokens": 12}]


@pytest.mark.asyncio
async def test_an_unobserved_failure_after_response_start_is_not_resent(monkeypatch, caplog):
    """Without the websocket observation's record the request is not resent."""
    from agents.retry import ModelRetryAdvice
    from agents.run_internal.model_retry import get_response_with_retry

    monkeypatch.setenv("OPENAI_MODEL_MAX_RETRIES", "3")
    attempts = []

    async def get_response():
        attempts.append(True)
        raise ResponsesWebSocketError(_overload())

    async def rewind():
        pytest.fail("an unobserved failure is never resent")

    with caplog.at_level(logging.INFO), pytest.raises(ResponsesWebSocketError):
        await get_response_with_retry(
            get_response=get_response, rewind=rewind, retry_settings=build_default_model_retry(),
            get_retry_advice=lambda _request: ModelRetryAdvice(
                suggested=False, replay_safety="unsafe", response_started=True),
            previous_response_id=None, conversation_id=None)
    assert len(attempts) == 1
    assert "reason=output_not_observed" in caplog.text


def test_the_sdk_retry_safe_event_set_is_the_one_the_guard_relies_on():
    """Pin the pinned SDK's private names the guard reads (ALL-1704)."""
    from agents.run_internal.model_retry import _RETRY_SAFE_STREAM_EVENT_TYPES

    assert _RETRY_SAFE_STREAM_EVENT_TYPES == frozenset({"response.created", "response.in_progress"})
    assert callable(OpenAIResponsesWSModel._iter_websocket_response_events)
