"""OpenAI's overload frame after the response started (ALL-1704).

Drives the real pinned SDK: ``Runner.run`` and ``Runner.run_streamed`` with the
app's ``MeasuredModel``, the real ``OpenAIResponsesWSModel`` and the SDK's retry
loops, over a fake websocket that sends the production frame order:
``response.created`` (0), ``response.in_progress`` (1), then the typed overload
``error`` frame (2). The resend arrives on a new connection with a new response
id. No request leaves the process.
"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json
import logging
import time

import pytest
from agents import Agent, ModelSettings, RunConfig, Runner, function_tool
from agents.models.openai_responses import OpenAIResponsesWSModel, ResponsesWebSocketError
from openai import AsyncOpenAI

from src.lib.openai_agents.config import build_default_model_retry

_FRAME_MESSAGE = "Our servers are currently overloaded. Please try again later."


def _response(response_id, status="in_progress", output=()):
    return {"id": response_id, "object": "response", "created_at": 0, "model": "gpt-test",
            "status": status, "output": list(output), "parallel_tool_calls": True, "tools": [],
            "tool_choice": "auto"}


def _created(response_id="resp_first"):
    return {"type": "response.created", "sequence_number": 0, "response": _response(response_id)}


def _in_progress(response_id="resp_first"):
    return {"type": "response.in_progress", "sequence_number": 1, "response": _response(response_id)}


def _overload(sequence_number=2, *, error_type="service_unavailable_error",
              code="server_is_overloaded", headers=None, response_id="resp_first"):
    """The frame as logged on production on 2026-11-23 (both failures)."""
    return {"type": "error", "error": {
        "type": error_type, "code": code,
        "headers": {"retry-after": "6", "retry-after-ms": "6000"} if headers is None else headers,
        "message": _FRAME_MESSAGE, "param": None,
    }, "sequence_number": sequence_number, "response_id": response_id}


_FINAL_MESSAGE = {"type": "message", "id": "msg_final", "role": "assistant", "status": "completed",
                  "content": [{"type": "output_text", "text": "done", "annotations": []}]}


def _completed(response_id, output=(_FINAL_MESSAGE,)):
    usage = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
             "input_tokens_details": {"cached_tokens": 0},
             "output_tokens_details": {"reasoning_tokens": 0}}
    return {"type": "response.completed", "sequence_number": 3,
            "response": {**_response(response_id, "completed", output), "usage": usage}}


def _success(response_id="resp_second", output=(_FINAL_MESSAGE,)):
    return [_created(response_id), _in_progress(response_id), _completed(response_id, output)]


_PRODUCTION_ORDER = [_created(), _in_progress(), _overload()]
_TOOL_CALL_ADDED = {"type": "response.output_item.added", "sequence_number": 2, "output_index": 0,
                    "item": {"type": "function_call", "id": "fc_fixture", "call_id": "call_fixture",
                             "name": "lookup_record", "arguments": "", "status": "in_progress"}}
_TEXT_DELTA = {"type": "response.output_text.delta", "sequence_number": 2, "item_id": "msg_final",
               "output_index": 0, "content_index": 0, "delta": "partial", "logprobs": []}
_MESSAGE_ADDED = {"type": "response.output_item.added", "sequence_number": 2, "output_index": 0,
                  "item": {"type": "message", "id": "msg_final", "role": "assistant",
                           "status": "in_progress", "content": []}}
_REASONING_ADDED = {"type": "response.output_item.added", "sequence_number": 2, "output_index": 0,
                    "item": {"type": "reasoning", "id": "rs_fixture", "summary": []}}
_OUTPUT_EVENTS = [_TEXT_DELTA, _MESSAGE_ADDED, _TOOL_CALL_ADDED, _REASONING_ADDED]
_OUTPUT_IDS = ["text-delta", "output-item", "function-call", "reasoning"]


class _Socket:
    """One websocket connection; each request it receives is answered by the next script."""

    state = SimpleNamespace(name="OPEN")

    def __init__(self, scripts, sent):
        self._scripts = scripts
        self._sent = sent
        self._frames = []

    async def send(self, frame):
        self._sent.append(json.loads(frame))
        self._frames = list(self._scripts.pop(0))

    async def recv(self):
        return json.dumps(self._frames.pop(0))

    async def close(self, **kwargs):
        pass


def _review_budget(monkeypatch, *, remaining=1800):
    from src.lib.validation_history.store import HistoryScope
    from src.lib.validation_review import provider as review

    admissions, settlements = [], []

    def reserve(*args, **kwargs):
        admissions.append(kwargs)
        return str(len(admissions))

    monkeypatch.setattr(review.admission, "reserve", reserve)
    monkeypatch.setattr(review.admission, "reconcile", lambda *a, **kw: settlements.append(kw["actual"]))
    monkeypatch.setattr(review, "remaining_seconds", lambda _: remaining)
    budget = review.ExecutionBudget(HistoryScope("owner", "11111111-1111-1111-1111-111111111111", "session"),
                                    "episode", "initial_tokens", False)
    return budget, admissions, settlements


@pytest.fixture
def harness(monkeypatch):
    """Real SDK retry loops with the app's measurement; sleeps recorded, not slept."""
    from agents.run_internal import model_retry
    from src.lib.openai_agents import model_request_measurement as measurement

    measurement.install_model_request_measurement()
    reports = []

    def publish(value):
        value["_published"] = True  # As the real _publish: one line per attempt.
        reports.append(deepcopy(value))

    monkeypatch.setattr(measurement, "_publish", publish)
    delays = []
    opened = [0]
    sleeping = {"real": False}

    async def record_sleep(delay):
        delays.append(delay)
        if sleeping["real"]:
            await asyncio.sleep(delay)

    monkeypatch.setattr(model_retry, "_sleep_for_retry", record_sleep)
    monkeypatch.setenv("OPENAI_MODEL_MAX_RETRIES", "3")
    monkeypatch.setenv("OPENAI_MODEL_RETRY_INITIAL_DELAY", "0")
    monkeypatch.setenv("OPENAI_MODEL_RETRY_MAX_DELAY", "8")

    async def run(scripts, *, streamed=False, previous_response_id=None, tools=(), wrap=None):
        sent, raw_types = [], []
        scripts = [list(script) for script in scripts]
        client = AsyncOpenAI(api_key="fixture", max_retries=0)
        model = OpenAIResponsesWSModel(model="gpt-test", openai_client=client)
        def open_socket(*args, **kwargs):
            opened[0] += 1
            return _Socket(scripts, sent)

        monkeypatch.setattr(model, "_open_websocket_connection", AsyncMock(side_effect=open_socket))
        agent = Agent(name="reviewer", instructions="Review the packet.", model=model, tools=list(tools),
                      model_settings=ModelSettings(store=False, retry=build_default_model_retry()))
        config = RunConfig(tracing_disabled=True)

        async def go():
            if not streamed:
                return await Runner.run(agent, "Review this packet.", run_config=config,
                                        previous_response_id=previous_response_id)
            result = Runner.run_streamed(agent, "Review this packet.", run_config=config,
                                         previous_response_id=previous_response_id)
            async for event in result.stream_events():
                if event.type == "raw_response_event":
                    raw_types.append(event.data.type)
            return result

        error = result = None
        try:
            result = await (wrap(go()) if wrap is not None else go())
        except (ResponsesWebSocketError, TimeoutError) as raised:
            error = raised
        finally:
            await model.close()
            await client.close()
        return SimpleNamespace(result=result, error=error, sent=sent, delays=delays, raw_types=raw_types,
                               reports=[r for r in reports if r.get("runtime") == "agents_sdk"])

    run.sleep_for_real = lambda: sleeping.update(real=True)
    run.delays_seen = lambda: list(delays)
    run.sockets_opened = lambda: opened[0]
    return run


def _config_logs(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "src.lib.openai_agents.config"]


both_modes = pytest.mark.parametrize("streamed", [False, True], ids=["non-streamed", "streamed"])


@pytest.mark.asyncio
@both_modes
async def test_overload_after_response_start_is_resent_and_succeeds(harness, caplog, streamed):
    with caplog.at_level(logging.INFO):
        run = await harness([_PRODUCTION_ORDER, _success()], streamed=streamed)

    assert run.error is None and run.result.final_output == "done"
    assert len(run.sent) == 2 and run.sent[1] == run.sent[0], "the same request is resent unchanged"
    assert run.delays == [6.0], "the frame's retry-after is honoured"
    # One measurement line per attempt, as for the refused-request resends.
    assert [(r["attempt"], r["outcome"]) for r in run.reports] == [(1, "provider_error"), (2, "completed")]
    assert run.reports[1]["provider_response_id"] == "resp_second"
    assert "provider_response_id" not in run.reports[0]
    assert run.result.last_response_id == "resp_second"
    assert [response.response_id for response in run.result.raw_responses] == ["resp_second"]
    logs = _config_logs(caplog)
    assert logs == ["Resending Responses WebSocket overload frame after the response started "
                    "and before any output (retry 1/3; delay=6.0)"]
    assert _FRAME_MESSAGE not in caplog.text, "no frame content in logs"
    if streamed:
        # The consumer sees the first attempt's created/in_progress, then the resend's.
        assert run.raw_types[:4] == ["response.created", "response.in_progress"] * 2


@pytest.mark.asyncio
@both_modes
async def test_overload_after_response_start_raises_the_original_error_when_the_budget_is_spent(
        harness, caplog, streamed):
    with caplog.at_level(logging.INFO):
        run = await harness([_PRODUCTION_ORDER] * 4, streamed=streamed)

    assert isinstance(run.error, ResponsesWebSocketError)
    assert (run.error.error_type, run.error.code) == ("service_unavailable_error", "server_is_overloaded")
    assert len(run.sent) == 4, "the first request and OPENAI_MODEL_MAX_RETRIES=3 resends"
    assert run.delays == [6.0, 6.0, 6.0]
    assert [r["attempt"] for r in run.reports] == [1, 2, 3, 4]
    logs = _config_logs(caplog)
    assert logs[:3] == [f"Resending Responses WebSocket overload frame after the response started "
                        f"and before any output (retry {n}/3; delay=6.0)" for n in (1, 2, 3)]
    assert logs[3:] == ["Not resending Responses WebSocket overload frame "
                        "(reason=retry_budget_exhausted; attempt 4/3)"]


@pytest.mark.asyncio
@both_modes
@pytest.mark.parametrize("headers,max_delay,expected", [
    ({"retry-after": "3"}, "8", 3.0),
    ({"retry-after-ms": "250", "retry-after": "9"}, "8", 0.25),
    ({"retry-after": "6", "retry-after-ms": "6000"}, "2", 2.0),
    ({"retry-after": "3600"}, "8", 8.0),
    ({"retry-after-ms": "0", "retry-after": "0"}, "8", 0.0),
    ({}, "8", 0.0),
    ({"retry-after": "soon"}, "8", 0.0),
    ({"retry-after": "-5"}, "8", 0.0),
    ({"retry-after": "nan"}, "8", 0.0),
], ids=["seconds", "milliseconds-first", "bounded", "huge-bounded", "zero", "missing", "not-a-number",
        "negative", "nan"])
async def test_retry_after_is_bounded_and_otherwise_the_backoff_applies(
        harness, monkeypatch, streamed, headers, max_delay, expected):
    monkeypatch.setenv("OPENAI_MODEL_RETRY_MAX_DELAY", max_delay)
    run = await harness([[_created(), _in_progress(), _overload(headers=headers)], _success()],
                        streamed=streamed)
    # The configured backoff here is 0 s (OPENAI_MODEL_RETRY_INITIAL_DELAY=0).
    assert run.error is None and run.delays == [expected]


@pytest.mark.asyncio
@both_modes
@pytest.mark.parametrize("output_event", _OUTPUT_EVENTS, ids=_OUTPUT_IDS)
async def test_overload_after_output_is_not_resent(harness, caplog, streamed, output_event):
    frames = [_created(), _in_progress(), output_event, _overload(sequence_number=3)]
    with caplog.at_level(logging.INFO):
        run = await harness([frames, _success()], streamed=streamed)

    assert isinstance(run.error, ResponsesWebSocketError) and run.error.code == "server_is_overloaded"
    assert len(run.sent) == 1 and run.delays == []
    assert _config_logs(caplog) == ["Not resending Responses WebSocket overload frame "
                                    "(reason=output_started; attempt 1/3)"]
    assert _FRAME_MESSAGE not in caplog.text and "partial" not in caplog.text


@pytest.mark.asyncio
@both_modes
@pytest.mark.parametrize("error_type,code", [
    ("service_unavailable_error", "server_busy"),
    ("rate_limit_error", "rate_limit_exceeded"),
    ("server_error", "server_is_overloaded"),
], ids=["other-code", "rate-limit", "other-type"])
async def test_a_different_error_after_response_start_is_not_resent(harness, caplog, streamed, error_type, code):
    frames = [_created(), _in_progress(), _overload(error_type=error_type, code=code)]
    with caplog.at_level(logging.INFO):
        run = await harness([frames, _success()], streamed=streamed)

    assert isinstance(run.error, ResponsesWebSocketError) and run.error.code == code
    assert len(run.sent) == 1 and run.delays == []
    assert _config_logs(caplog) == []


@pytest.mark.asyncio
@both_modes
async def test_a_stateful_request_is_not_resent_after_response_start(harness, caplog, streamed):
    with caplog.at_level(logging.INFO):
        run = await harness([_PRODUCTION_ORDER, _success()], streamed=streamed,
                            previous_response_id="resp_previous")

    assert isinstance(run.error, ResponsesWebSocketError)
    assert len(run.sent) == 1 and run.sent[0]["previous_response_id"] == "resp_previous"
    assert _config_logs(caplog) == ["Not resending Responses WebSocket overload frame "
                                    "(reason=stateful_request; attempt 1/3)"]


@pytest.mark.asyncio
@both_modes
async def test_first_frame_overload_is_still_resent(harness, caplog, streamed):
    with caplog.at_level(logging.INFO):
        run = await harness([[_overload(sequence_number=0)], _success()], streamed=streamed)

    assert run.error is None and run.result.final_output == "done"
    assert len(run.sent) == 2 and run.delays == [6.0]
    assert _config_logs(caplog) == ["Retrying transient Responses WebSocket overload frame "
                                    "(retry 1/3; retry_budget_exhausted=False)"]


@pytest.mark.asyncio
@both_modes
async def test_review_provider_calls_are_reserved_and_settled_once_per_attempt(harness, monkeypatch, streamed):
    from src.lib.validation_review import provider as review

    budget, admissions, settlements = _review_budget(monkeypatch)
    with review.budget_scope(budget):
        run = await harness([_PRODUCTION_ORDER, _success()], streamed=streamed)

    assert run.error is None and len(run.sent) == 2
    assert len(admissions) == 2
    # The failed attempt reported no usage, so it closes as uncertain and keeps
    # its reserved charge (admission.reconcile); the resend settles its own.
    assert settlements == [None, {"initial_tokens": 12}]


@pytest.mark.asyncio
@both_modes
async def test_a_resend_wait_beyond_the_review_deadline_ends_as_the_deadline_does(harness, monkeypatch, streamed):
    """The review deadline (guarded_await) cancels the retry wait; nothing hangs or is resent."""
    from src.lib.validation_review import provider as review

    budget, admissions, settlements = _review_budget(monkeypatch, remaining=0.5)
    harness.sleep_for_real()
    started = time.monotonic()
    with review.budget_scope(budget):
        run = await harness([_PRODUCTION_ORDER, _success()], streamed=streamed, wrap=review.guarded_await)

    assert isinstance(run.error, TimeoutError) and run.error._review_deadline_expired is True
    assert time.monotonic() - started < 3, "the 6 s retry wait did not outlive the 0.5 s deadline"
    assert run.delays == [6.0] and len(run.sent) == 1
    assert len(admissions) == len(settlements) == 1


@pytest.mark.asyncio
@both_modes
async def test_cancelling_the_run_during_a_resend_wait_cancels_it(harness, streamed):
    """A cancelled run does not finish its retry wait or send the resend."""
    harness.sleep_for_real()
    task = asyncio.create_task(harness([_PRODUCTION_ORDER, _success()], streamed=streamed))
    deadline = time.monotonic() + 3
    while not harness.delays_seen() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert harness.delays_seen() == [6.0], "the run is waiting to resend"
    started = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - started < 1
    assert harness.sockets_opened() == 1, "the resend was never sent"


@pytest.mark.asyncio
@both_modes
async def test_a_resend_after_tools_ran_in_earlier_requests_runs_each_tool_once(harness, streamed):
    """This morning's extractor case: tool results in earlier turns, then the frame."""
    staged = []

    @function_tool
    def stage_record(label: str) -> str:
        """Stage one record."""
        staged.append(label)
        return "staged"

    calls = [{"type": "function_call", "id": f"fc_{n}", "call_id": f"call_{n}", "name": "stage_record",
              "arguments": json.dumps({"label": f"record {n}"}), "status": "completed"} for n in range(3)]
    scripts = [
        _success("resp_turn1", calls[:2]),
        _success("resp_turn2", calls[2:]),
        _PRODUCTION_ORDER,
        _success("resp_turn3"),
    ]
    run = await harness(scripts, streamed=streamed, tools=[stage_record])

    assert run.error is None and run.result.final_output == "done"
    assert staged == ["record 0", "record 1", "record 2"], "each tool ran once"
    assert len(run.sent) == 4 and run.sent[3] == run.sent[2]
    outputs = [item["call_id"] for item in run.sent[3]["input"] if item.get("type") == "function_call_output"]
    assert outputs == ["call_0", "call_1", "call_2"]


@pytest.mark.asyncio
async def test_an_unobserved_failure_after_response_start_is_not_resent(monkeypatch, caplog):
    """Without the websocket observation's record the request is not resent."""
    from agents.retry import ModelRetryAdvice, ModelRetryAdviceRequest
    from agents.run_internal.model_retry import get_response_with_retry
    from src.lib.openai_agents.config import responses_websocket_overload_advice

    monkeypatch.setenv("OPENAI_MODEL_MAX_RETRIES", "3")
    attempts = []
    unsafe = ModelRetryAdvice(suggested=False, replay_safety="unsafe", response_started=True)

    async def get_response():
        attempts.append(True)
        raise ResponsesWebSocketError(_overload())

    async def rewind():
        pytest.fail("an unobserved failure is never resent")

    with caplog.at_level(logging.INFO), pytest.raises(ResponsesWebSocketError):
        await get_response_with_retry(
            get_response=get_response, rewind=rewind, retry_settings=build_default_model_retry(),
            get_retry_advice=lambda _request: unsafe, previous_response_id=None, conversation_id=None)
    assert len(attempts) == 1
    streamed = ModelRetryAdviceRequest(error=ResponsesWebSocketError(_overload()), attempt=1, stream=True)
    assert responses_websocket_overload_advice(
        streamed, unsafe, retry_settings=build_default_model_retry()) is unsafe
    assert _config_logs(caplog) == ["Not resending Responses WebSocket overload frame "
                                    "(reason=output_not_observed; attempt 1/3)"] * 2


def test_the_sdk_private_names_the_guard_relies_on():
    """Pin the pinned SDK's private names this fix reads or wraps (ALL-1704)."""
    from agents.run_internal.model_retry import _RETRY_SAFE_STREAM_EVENT_TYPES
    from src.lib.openai_agents.config import install_responses_websocket_output_observation

    assert _RETRY_SAFE_STREAM_EVENT_TYPES == frozenset({"response.created", "response.in_progress"})
    install_responses_websocket_output_observation()
    assert OpenAIResponsesWSModel._iter_websocket_response_events._agr_output_observation is True
