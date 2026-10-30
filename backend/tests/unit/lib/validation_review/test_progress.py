"""Live progress must precede completion, without changing review outcomes."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.lib.validation_review.chat import ChatReview
from src.lib.validation_review.progress import review_progress
from src.lib.openai_agents import streaming_tools, runner


@pytest.fixture
def events(monkeypatch):
    values = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", values.append)
    return values


@pytest.mark.parametrize("ready", [True, False])
def test_review_terminal_uses_authoritative_readiness(events, ready):
    with review_progress("episode") as phase:
        assert [e["type"] for e in events] == ["AGENT_GENERATING"]
        phase["outcome"] = {"output": "Everything is complete", "review": {"ready": ready}}
    assert events[-1]["details"]["success"] is ready
    assert ("unresolved" in events[-1]["details"]["message"]) is not ready


@pytest.mark.parametrize("error,kind", [(RuntimeError("private detail"), "SUPERVISOR_ERROR"),
                                         (asyncio.CancelledError(), "AGENT_COMPLETE")])
def test_review_errors_propagate_without_false_success(events, error, kind):
    with pytest.raises(type(error)):
        with review_progress("episode"):
            raise error
    assert events[-1]["type"] == kind
    assert events[-1]["details"].get("success") is not True
    assert "private detail" not in str(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 3])
async def test_initial_phase_reports_frozen_request_count(count):
    events = []
    adapter = SimpleNamespace(initial=AsyncMock(), plan={"initial_requests": [{}] * count})
    async def wait(awaitable):
        return await awaitable
    await ChatReview.initial_validation(SimpleNamespace(until_stop=wait), adapter, events.append)
    assert events == [{"event": "initial_validation_complete", "status": "complete", "request_count": count}]


@pytest.mark.asyncio
async def test_initial_expected_stop_is_reported_but_still_propagates():
    events = []
    async def wait(awaitable):
        return await awaitable
    adapter = SimpleNamespace(initial=AsyncMock(side_effect=TimeoutError()))
    with pytest.raises(TimeoutError):
        await ChatReview.initial_validation(SimpleNamespace(until_stop=wait), adapter, events.append)
    assert events == [{"event": "initial_validation_complete", "status": "stopped", "reason": "expired"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["complete", "error", "close"])
async def test_resume_delivers_progress_before_completion_and_restores_context(monkeypatch, ending):
    previous = []
    streaming_tools.set_live_event_list(previous)
    released = asyncio.Event()
    finished = asyncio.Event()
    async def resume():
        try:
            streaming_tools.get_live_event_list().append({"type": "AGENT_GENERATING"})
            await released.wait()
            streaming_tools.get_live_event_list().append({"type": "AGENT_COMPLETE"})
            if ending == "error":
                raise RuntimeError("review failure")
        finally:
            finished.set()
    stream = runner._stream_chat_review(SimpleNamespace(resume=resume))
    try:
        assert await asyncio.wait_for(anext(stream), 1) == {"type": "AGENT_GENERATING"}
        assert not finished.is_set()
        if ending == "close":
            await stream.aclose()
        else:
            released.set()
            assert await asyncio.wait_for(anext(stream), 1) == {"type": "AGENT_COMPLETE"}
            with pytest.raises(RuntimeError if ending == "error" else StopAsyncIteration):
                await anext(stream)
        assert finished.is_set()
        assert streaming_tools.get_live_event_list() is previous
        assert previous == []
    finally:
        await stream.aclose()
        streaming_tools.set_live_event_list(None)


@pytest.mark.asyncio
async def test_saved_review_does_not_emit_fake_activity(events, monkeypatch):
    review = ChatReview("owner", "document", "session", "turn", None, (), "request")
    monkeypatch.setattr(review, "context", lambda _: SimpleNamespace(episode_id="episode"))
    monkeypatch.setattr(review, "saved_review_outcome", lambda _: {"review": {"ready": True}})
    assert await review.review_result(object()) == {"review": {"ready": True}}
    assert await review.review_result(object()) is None
    assert events == []


@pytest.mark.asyncio
async def test_real_review_result_streams_before_reviewer_finishes(monkeypatch):
    from src.lib.validation_review import chat
    from src.lib.openai_agents import extraction_trace_events
    review = ChatReview("owner", "document", "session", "turn", None, (), "request")
    record = SimpleNamespace(extraction_result_id="source")
    context = SimpleNamespace(episode_id="episode", history_scope=None, actor_id="supervisor")
    monkeypatch.setattr(review, "context", lambda _: context)
    monkeypatch.setattr(review, "saved_review_outcome", lambda _: None)
    monkeypatch.setattr(review, "reference", lambda _: dict(artifact_id="artifact", episode_id="episode", plan_ref="plan"))
    adapter = SimpleNamespace(initial=AsyncMock(), advance=AsyncMock(), execute=None,
        prepare_inputs=None, refresh_inputs=None)
    monkeypatch.setattr(chat, "ValidatorAdapter", lambda *a, **kw: adapter)
    monkeypatch.setattr(review, "extractor_adapter", lambda *a: SimpleNamespace(request=None))
    monkeypatch.setattr(chat.reviewer, "create_supervisor_phase", lambda *a, **kw: SimpleNamespace(tools=[]))
    monkeypatch.setattr(extraction_trace_events, "write_extraction_trace_event", lambda **kw: None)
    monkeypatch.setattr(streaming_tools, "write_stream_event", lambda *a, **kw: None)
    gate = asyncio.Event()
    async def run(*a, **kw):
        await gate.wait()
        return {"review": {"ready": True}}
    monkeypatch.setattr(chat.reviewer, "run", run)
    async def resume():
        return await review.review_result(record)
    stream = runner._stream_chat_review(SimpleNamespace(resume=resume))
    try:
        event = await anext(stream)
        assert event["type"] == "AGENT_GENERATING"
        assert not gate.is_set()
        gate.set()
        tail = [e async for e in stream]
        assert tail[-1]["details"]["message"] == "Supervisor review complete"
    finally:
        await stream.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["pre_run", "post_run", "direct_continue", "direct_reconnect"])
async def test_chat_wrapper_forwards_review_progress_and_closes_child(monkeypatch, path):
    from src.lib.validation_review import chat_dispatch
    from src.lib.validation_review.chat import ACTIVE_CHAT_REVIEW
    started = asyncio.Event()
    cleaned = asyncio.Event()
    calls = 0
    async def resume():
        nonlocal calls
        calls += 1
        if path == "post_run" and calls == 1:
            return
        try:
            streaming_tools.get_live_event_list().append({"type": "AGENT_GENERATING"})
            started.set()
            await asyncio.Event().wait()
        finally:
            cleaned.set()
    async def fake_run(**kw):
        yield {"type": "RUN_FINISHED", "data": {"response": "source"}}
    review = SimpleNamespace(resume=resume, turn_id="turn", interrupted=False,
        supervisor_handoff=lambda: {"review_outcomes": []},
        direct_action=AsyncMock(return_value=SimpleNamespace(action="continue_review" if path == "direct_continue" else "new_extraction")))
    monkeypatch.setattr(runner, "_run_agent_with_groq_retry", fake_run)
    monkeypatch.setattr(chat_dispatch, "claim", lambda *a: {"claimed": False})
    monkeypatch.setattr("src.lib.observability.cost_context.get_agent_cost_identity", lambda _: {"agent_id": "extractor"})
    previous = []
    streaming_tools.set_live_event_list(previous)
    stream = runner._run_agent_with_chat_review(review_context=review,
        agent=SimpleNamespace(generic_profile={}), input_items=[], user_message="test", trace_id="trace",
        direct_chat_context=object() if path.startswith("direct") else None)
    try:
        assert (await anext(stream))["type"] == "AGENT_GENERATING"
        assert started.is_set() and not cleaned.is_set()
        await stream.aclose()
        assert cleaned.is_set()
        assert streaming_tools.get_live_event_list() is previous
        assert ACTIVE_CHAT_REVIEW.get() is None
    finally:
        await stream.aclose()
        streaming_tools.set_live_event_list(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("direct", [False, True])
async def test_curator_stop_subclass_survives_resume_task_boundary(direct):
    from src.lib.validation_review.stops import ReviewInterrupted
    async def resume():
        raise ReviewInterrupted("Curator stopped validation review")
    review = SimpleNamespace(resume=resume,
        direct_action=AsyncMock(return_value=SimpleNamespace(action="continue_review")))
    stream = runner._run_agent_with_chat_review(review_context=review,
        agent=SimpleNamespace(generic_profile={}), input_items=[],
        direct_chat_context=object() if direct else None)
    try:
        with pytest.raises(ReviewInterrupted):
            await anext(stream)
    finally:
        await stream.aclose()
