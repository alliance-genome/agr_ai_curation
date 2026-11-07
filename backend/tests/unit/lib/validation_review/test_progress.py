"""Live progress must precede completion, without changing review outcomes."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.lib.validation_history.store import HistoryConflict
from src.lib.validation_review.admission import ReviewStopped
from src.lib.validation_review.chat import ChatReview
from src.lib.validation_review.decisions import PacketDecision, ReviewEntry
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
    saved_receipt = {"agent_key": "saved", "contract": {"instructions": "Rules."}}
    context = SimpleNamespace(episode_id="episode", history_scope=None, actor_id="supervisor")
    monkeypatch.setattr(review, "context", lambda _: context)
    monkeypatch.setattr(review, "saved_review_outcome", lambda _: None)
    monkeypatch.setattr(review, "reference", lambda _: dict(artifact_id="artifact", episode_id="episode", plan_ref="plan"))
    monkeypatch.setattr(review, "review_request", lambda _: "request")
    adapter = SimpleNamespace(initial=AsyncMock(), advance=AsyncMock(), execute=None,
        prepare_inputs=None, refresh_inputs=None)
    monkeypatch.setattr(chat, "ValidatorAdapter", lambda *a, **kw: adapter)
    monkeypatch.setattr(review, "extractor_adapter", lambda *a: SimpleNamespace(request=None, receipt=saved_receipt))
    phases = []
    monkeypatch.setattr(chat.reviewer, "create_supervisor_phase",
                        lambda *a, **kw: phases.append(kw) or SimpleNamespace(tools=[]))
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
        assert phases[0]["extractor_receipt"] is saved_receipt
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


def _lines(events):
    return [event["details"]["friendlyName"] for event in events]


def test_review_tool_lines_need_a_live_curator_stream(monkeypatch):
    from src.lib.validation_review.progress import ReviewLines

    added = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", added.append)
    streaming_tools.set_live_event_list(None)
    lines = ReviewLines("episode")
    entry = ReviewEntry(target_id="target", decision_unit_id="target", expected_version=1,
                        action="accept", reason="Shown in Methods")
    lines.start("record_validation_review", {"entries": [entry]})
    lines.finish("record_validation_review", {"applied": []})
    assert added == []
    live = []
    streaming_tools.set_live_event_list(live)
    try:
        lines.start("record_validation_review", {"packet_decisions": [
            PacketDecision(handle="D1", action="accept", reason="Shown in Methods"),
            PacketDecision(handle="D2", action="defer", reason="Genotype unclear")]})
        lines.finish("record_validation_review", {"applied": []})
    finally:
        streaming_tools.set_live_event_list(None)
    assert _lines(added) == ["Supervisor review: saving 2 decisions"]
    details = added[0]["details"]
    assert added[0]["type"] == "TOOL_START"
    assert (details["reviewPhase"], details["agentRole"], details["episodeId"]) == (
        "supervisor_review", "supervisor", "episode")


def test_a_burst_of_one_tool_shows_one_line_and_the_next_burst_another(events):
    from src.lib.validation_review.progress import ReviewLines

    streaming_tools.set_live_event_list([])
    try:
        lines = ReviewLines("episode")
        for _ in range(3):
            lines.start("read_validation_history", {})
        lines.start("get_validation_record_sources", {"target_ids": ["a", "b"]})
        for _ in range(3):
            lines.finish("read_validation_history", {})
        lines.finish("get_validation_record_sources", {})
        lines.start("read_validation_history", {})
        lines.finish("read_validation_history", None)
    finally:
        streaming_tools.set_live_event_list(None)
    assert _lines(events) == [
        "Supervisor review: reading saved validation history",
        "Supervisor review: reading 2 saved records",
        "Supervisor review: reading saved validation history",
    ]


@pytest.mark.parametrize("result,line", [
    ({"records": [{}], "page": 1, "pages": 1, "record_count": 8, "answer_count": 24},
     "Supervisor review: reviewing 8 records and 24 validation results"),
    ({"records": [{}], "page": 1, "pages": 2, "record_count": 81, "answer_count": 0},
     "Supervisor review: reviewing 81 records (part 1 of 2)"),
    ({"records": [{}], "page": 2, "pages": 2, "record_count": 81, "answer_count": 0},
     "Supervisor review: reviewing more records (part 2 of 2)"),
    ({"records": [], "page": 1, "pages": 1, "record_count": 0, "answer_count": 0},
     "Supervisor review: checking the remaining review work"),
])
def test_packet_line_describes_what_the_packet_returned(events, result, line):
    from src.lib.validation_review.progress import ReviewLines

    streaming_tools.set_live_event_list([])
    try:
        lines = ReviewLines("episode")
        lines.start("read_review_packet", {"packet_id": None, "page": None})
        lines.finish("read_review_packet", result)
        lines.start("read_review_packet", {"packet_id": None, "page": None})
        lines.finish("read_review_packet", {"state": "conflict", "message": "stale"})
    finally:
        streaming_tools.set_live_event_list(None)
    assert _lines(events) == [line]
    assert events[0]["type"] == "TOOL_COMPLETE"


@pytest.mark.parametrize("actions,line", [
    (["accept", "reject", "defer"], "saving 3 decisions"),
    (["accept", "retire", "accept", "retire", "accept"],
     "saving 3 decisions and removing 2 records that don't answer the question"),
    (["accept", "retire"], "saving 1 decision and removing 1 record that doesn't answer the question"),
    (["retire", "retire"], "removing 2 records that don't answer the question"),
    (["retire"], "removing 1 record that doesn't answer the question"),
])
def test_save_line_names_records_the_reviewer_removes(actions, line):
    from src.lib.validation_review.progress import _start_line

    # Bound tool arguments are model objects; the line reads only their action.
    decisions = [PacketDecision(handle=f"D{index}", action=action, reason="Specific to this record")
                 for index, action in enumerate(actions)]
    assert _start_line("record_validation_review", {"packet_decisions": decisions, "entries": None}) == line


def test_save_line_reads_real_packet_decisions():
    from src.lib.validation_review.progress import _start_line

    decisions = [PacketDecision(handle="D1", action="accept", reason="Shown in Methods"),
                 PacketDecision(handle="D2", action="defer", reason="Organism unclear")]
    assert _start_line("record_validation_review", {"packet_decisions": decisions, "entries": None}) == (
        "saving 2 decisions")


_ENTRY_IDS = ["cafa5538-3de0-48ba-a2c2-bdf1d8912198", "df85e2e1-0cb1-4d38-be3f-a8a4c494eee6",
              "21910a7e-6dd4-435d-90cf-f5029516d901"]


_FINISHED = "Supervisor review: re-check finished for "
_NONE_LEFT = "Supervisor review: no re-check left for "
_NOT_RUN = "Supervisor review: re-check did not run"


@pytest.mark.parametrize("result,finish", [
    ({"state": "completed", "work_id": "work", "contributions": [{"fact_id": "a", "sha256": "1"},
                                                                {"fact_id": "b", "sha256": "2"},
                                                                {"fact_id": "c", "sha256": "3"}]},
     [(_FINISHED + "3 records", None)]),
    ({"state": "completed", "work_id": "work", "contributions": [{"fact_id": "a", "sha256": "1"}]},
     [(_FINISHED + "1 record", None)]),
    # Partly refused: the rest ran, and the spent record is named under exhausted.
    ({"state": "completed", "work_id": "work", "contributions": [{"fact_id": "a", "sha256": "1"},
                                                                {"fact_id": "b", "sha256": "2"}],
      "exhausted": [_ENTRY_IDS[2]], "exhausted_note": "These records had no follow-up left."},
     [(_FINISHED + "2 records", None), (_NONE_LEFT + "1 record", "warning")]),
    # Fully refused: every requested record is spent, so none ran.
    ({"state": "not_applied", "code": "extraction_attempts_exhausted", "resource": _ENTRY_IDS[0],
      "applied": [], "targets": [], "exhausted": _ENTRY_IDS},
     [(_NONE_LEFT + "3 records", "warning")]),
    ({"state": "not_applied", "code": "fresh_extraction_required", "resource": None,
      "applied": [], "targets": []},
     [(_NOT_RUN, "warning")]),
    ({"state": "conflict", "code": "HistoryConflict", "applied": [],
      "message": "Parent decision must concern every requested entry"},
     [(_NOT_RUN, "warning")]),
    ({"state": "not_claimed", "work_id": "work"}, [(_NOT_RUN, "warning")]),
    # A finished re-check with an empty exhausted list has nothing to refuse.
    ({"state": "completed", "work_id": "work", "contributions": [{}], "exhausted": []},
     [(_FINISHED + "1 record", None)]),
    # A refusal that does not list its records cannot say how many; nothing ran.
    ({"state": "not_applied", "code": "extraction_attempts_exhausted", "resource": _ENTRY_IDS[0],
      "applied": [], "targets": []},
     [(_NOT_RUN, "warning")]),
    # Unknown shapes never claim that a re-check ran.
    ({"state": "completed", "work_id": "work"}, [(_NOT_RUN, "warning")]),
    ({"state": "queued"}, [(_NOT_RUN, "warning")]),
    ({}, [(_NOT_RUN, "warning")]),
])
def test_recheck_says_how_it_ended(events, result, finish):
    from src.lib.validation_review.progress import ReviewLines

    streaming_tools.set_live_event_list([])
    try:
        lines = ReviewLines("episode")
        lines.start("request_targeted_extraction", {"entry_ids": _ENTRY_IDS})
        lines.finish("request_targeted_extraction", result)
    finally:
        streaming_tools.set_live_event_list(None)
    assert _lines(events) == ["Supervisor review: asking the extraction agent to re-check 3 records",
                              *(line for line, _ in finish)]
    assert [event["type"] for event in events] == ["TOOL_START"] + ["TOOL_COMPLETE"] * len(finish)
    assert not any(entry_id in line for entry_id in _ENTRY_IDS for line in _lines(events))
    assert [event["details"].get("severity") for event in events[1:]] == [severity for _, severity in finish]
    assert all(event["details"]["reviewPhase"] == "supervisor_review" and "success" not in event["details"]
               for event in events[1:])


def test_a_stopped_recheck_shows_no_finish_line(events):
    from src.lib.validation_review.progress import ReviewLines

    streaming_tools.set_live_event_list([])
    try:
        lines = ReviewLines("episode")
        lines.start("request_targeted_extraction", {"entry_ids": _ENTRY_IDS[:1]})
        lines.finish("request_targeted_extraction", None)
    finally:
        streaming_tools.set_live_event_list(None)
    assert _lines(events) == ["Supervisor review: asking the extraction agent to re-check 1 record"]


def test_each_of_two_overlapping_rechecks_reports_its_own_end(events):
    from src.lib.validation_review.progress import ReviewLines

    streaming_tools.set_live_event_list([])
    try:
        lines = ReviewLines("episode")
        lines.start("request_targeted_extraction", {"entry_ids": _ENTRY_IDS[:2]})
        lines.start("request_targeted_extraction", {"entry_ids": _ENTRY_IDS[2:]})
        lines.finish("request_targeted_extraction", {"state": "not_applied", "applied": [], "targets": [],
                                                     "code": "extraction_attempts_exhausted",
                                                     "resource": _ENTRY_IDS[2], "exhausted": _ENTRY_IDS[2:]})
        lines.finish("request_targeted_extraction", {"state": "completed", "work_id": "work",
                                                     "contributions": [{}, {}]})
        lines.start("request_targeted_extraction", {"entry_ids": _ENTRY_IDS[:1]})
        lines.finish("request_targeted_extraction", {"state": "completed", "work_id": "next",
                                                     "contributions": [{}]})
    finally:
        streaming_tools.set_live_event_list(None)
    assert _lines(events) == [
        "Supervisor review: asking the extraction agent to re-check 2 records",
        "Supervisor review: no re-check left for 1 record",
        "Supervisor review: re-check finished for 2 records",
        "Supervisor review: asking the extraction agent to re-check 1 record",
        "Supervisor review: re-check finished for 1 record",
    ]


def test_overlapping_packet_reads_show_each_distinct_page_once(events):
    """Finish lines collapse by text: two pages read together each show (since ALL-1504)."""
    from src.lib.validation_review.progress import ReviewLines

    first = {"records": [{}], "page": 1, "pages": 2, "record_count": 81, "answer_count": 0}
    streaming_tools.set_live_event_list([])
    try:
        lines = ReviewLines("episode")
        for _ in range(3):
            lines.start("read_review_packet", {"packet_id": "packet", "page": None})
        lines.finish("read_review_packet", first)
        lines.finish("read_review_packet", first)
        lines.finish("read_review_packet", {**first, "page": 2})
        lines.start("read_review_packet", {"packet_id": "packet", "page": None})
        lines.finish("read_review_packet", first)
    finally:
        streaming_tools.set_live_event_list(None)
    assert _lines(events) == [
        "Supervisor review: reviewing 81 records (part 1 of 2)",
        "Supervisor review: reviewing more records (part 2 of 2)",
        "Supervisor review: reviewing 81 records (part 1 of 2)",
    ]


def _every_record_spent():
    # Built as extraction_scheduling.request builds a full refusal.
    stopped = ReviewStopped("extraction_attempts_exhausted", resource=_ENTRY_IDS[0])
    stopped.exhausted = list(_ENTRY_IDS)
    return stopped


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,finish", [
    (_every_record_spent, [_NONE_LEFT + "3 records"]),
    (lambda: ReviewStopped("fresh_extraction_required"), [_NOT_RUN]),
    (lambda: ReviewStopped("saved_extractor_contract_unavailable"), [_NOT_RUN]),
    (lambda: HistoryConflict("Parent decision must concern every requested entry"), [_NOT_RUN]),
    (lambda: {"state": "completed", "work_id": "work", "contributions": [{}, {}],
              "exhausted": [_ENTRY_IDS[2]], "exhausted_note": "Decide these records now."},
     [_FINISHED + "2 records", _NONE_LEFT + "1 record"]),
])
async def test_recheck_lines_through_the_real_review_tool(events, outcome, finish):
    """The bound tool, its recoverable-result conversion and the lines run for real."""
    import json
    from unittest.mock import Mock
    from agents.tool_context import ToolContext
    from src.lib.validation_history.store import HistoryScope
    from src.lib.validation_review.decisions import ReviewContext
    from src.lib.validation_review.tools import create_tools

    async def request_extraction(**arguments):
        value = outcome()
        if isinstance(value, BaseException):
            raise value
        return value

    async def unused(**arguments):
        raise AssertionError("not called")

    context = ReviewContext(HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session"),
                            "episode", "reviewer")
    tool = next(t for t in create_tools(context, checkpoint_provider=Mock(), request_extraction=request_extraction,
                                        select_source=unused, assess_source=unused)
                if t.name == "request_targeted_extraction")
    arguments = json.dumps({"operation_id": "recheck", "parent_decision_id": "decision",
                            "entry_ids": _ENTRY_IDS, "instructions": "Read Methods for the genotype."})
    streaming_tools.set_live_event_list([])
    try:
        await tool.on_invoke_tool(ToolContext(context=None, tool_name=tool.name, tool_call_id="call",
                                              tool_arguments=arguments), arguments)
    finally:
        streaming_tools.set_live_event_list(None)
    assert _lines(events) == ["Supervisor review: asking the extraction agent to re-check 3 records", *finish]
    assert [event["details"].get("severity") for event in events[1:]] == [
        None if line.startswith(_FINISHED) else "warning" for line in finish]


def test_live_review_episode_resets_after_review_progress(events):
    from src.lib.validation_review.progress import _LIVE_REVIEW_EPISODE

    assert _LIVE_REVIEW_EPISODE.get() is None
    with review_progress("outer"):
        assert _LIVE_REVIEW_EPISODE.get() == "outer"
        with pytest.raises(RuntimeError):
            with review_progress("inner"):
                assert _LIVE_REVIEW_EPISODE.get() == "inner"
                raise RuntimeError("review failure")
        assert _LIVE_REVIEW_EPISODE.get() == "outer"
    assert _LIVE_REVIEW_EPISODE.get() is None


def test_live_review_episode_is_not_left_bound_when_the_opening_line_fails(monkeypatch):
    from src.lib.validation_review.progress import _LIVE_REVIEW_EPISODE

    def unavailable(event):
        raise RuntimeError("stream closed")
    monkeypatch.setattr(streaming_tools, "add_specialist_event", unavailable)
    with pytest.raises(RuntimeError, match="stream closed"):
        with review_progress("episode"):
            raise AssertionError("the review body never starts")
    assert _LIVE_REVIEW_EPISODE.get() is None


def test_every_review_tool_has_a_plain_language_line():
    from src.lib.validation_review.progress import _finish_lines, _start_line
    from src.lib.validation_review.tools import runtime_tool_contract

    packet = {"records": [{}], "page": 1, "pages": 1, "record_count": 1, "answer_count": 1}
    for tool in runtime_tool_contract():
        name = tool["name"]
        lines = [_start_line(name, {}), *(line for line, _ in _finish_lines(name, packet))]
        shown = [line for line in lines if line is not None]
        assert shown, name
        assert all("_" not in line and name not in line for line in shown), (name, shown)


@pytest.mark.asyncio
async def test_parallel_tool_calls_from_one_model_turn_collapse_into_one_line(events, monkeypatch):
    import json
    import threading
    from unittest.mock import Mock
    from agents.tool_context import ToolContext
    from src.lib.validation_history.store import HistoryScope
    from src.lib.validation_review import reads
    from src.lib.validation_review.decisions import ReviewContext
    from src.lib.validation_review.tools import create_tools

    barrier = threading.Barrier(3, timeout=5)

    def read(*args, **kwargs):
        barrier.wait()
        return {"content": "{}", "next_offset": None}

    monkeypatch.setattr(reads, "read", read)
    context = ReviewContext(HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session"),
                            "episode", "reviewer")
    tool = next(t for t in create_tools(context, checkpoint_provider=Mock()) if t.name == "read_validation_history")
    arguments = json.dumps({"fact_id": "f", "sha256": "s", "offset": 0})

    async def call(index):
        return await tool.on_invoke_tool(ToolContext(context=None, tool_name=tool.name, tool_call_id=f"c{index}",
                                                     tool_arguments=arguments), arguments)

    streaming_tools.set_live_event_list([])
    try:
        await asyncio.gather(*(call(index) for index in range(3)))
    finally:
        streaming_tools.set_live_event_list(None)
    assert _lines(events) == ["Supervisor review: reading saved validation history"]
