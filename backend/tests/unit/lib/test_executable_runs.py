import asyncio
import logging
from typing import Any, cast

import pytest

from src.lib.executable_runs import (
    ExecutableRunAccessError,
    ExecutableRunConflictError,
    ExecutableRunManager,
)


@pytest.mark.asyncio
async def test_observer_detach_does_not_cancel_producer(monkeypatch):
    monkeypatch.setattr(
        "src.lib.executable_runs.get_executable_run_event_replay_limit",
        lambda: 10,
    )
    monkeypatch.setattr(
        "src.lib.executable_runs.get_executable_run_retention_seconds",
        lambda: 60,
    )

    manager = ExecutableRunManager()
    continue_stream = asyncio.Event()

    async def stream_factory():
        yield 'data: {"type":"RUN_STARTED"}\n\n'
        await continue_stream.wait()
        yield 'data: {"type":"DONE"}\n\n'

    run, created = await manager.get_or_start_stream(
        run_id="assistant_chat_turn:session-1:turn-1",
        kind="assistant_chat_turn",
        owner_user_id="user-1",
        session_id="session-1",
        turn_id="turn-1",
        stream_factory=stream_factory,
    )

    assert created is True

    first_observer = manager.observe(run)
    first_event = await asyncio.wait_for(first_observer.__anext__(), timeout=1)
    assert "RUN_STARTED" in first_event
    await cast(Any, first_observer).aclose()

    same_run, same_created = await manager.get_or_start_stream(
        run_id="assistant_chat_turn:session-1:turn-1",
        kind="assistant_chat_turn",
        owner_user_id="user-1",
        session_id="session-1",
        turn_id="turn-1",
        stream_factory=stream_factory,
    )

    assert same_run is run
    assert same_created is False

    second_observer = manager.observe(run)
    replay_event = await asyncio.wait_for(second_observer.__anext__(), timeout=1)
    assert replay_event == first_event

    continue_stream.set()
    done_event = await asyncio.wait_for(second_observer.__anext__(), timeout=1)
    assert "DONE" in done_event

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(second_observer.__anext__(), timeout=1)

    assert run.status == "completed"


@pytest.mark.asyncio
async def test_active_session_rejects_different_run_and_other_owner(monkeypatch):
    monkeypatch.setattr(
        "src.lib.executable_runs.get_executable_run_event_replay_limit",
        lambda: 10,
    )
    monkeypatch.setattr(
        "src.lib.executable_runs.get_executable_run_retention_seconds",
        lambda: 60,
    )

    manager = ExecutableRunManager()
    block_stream = asyncio.Event()

    async def stream_factory():
        await block_stream.wait()
        yield 'data: {"type":"DONE"}\n\n'

    run, _ = await manager.get_or_start_stream(
        run_id="assistant_chat_turn:session-1:turn-1",
        kind="assistant_chat_turn",
        owner_user_id="user-1",
        session_id="session-1",
        turn_id="turn-1",
        stream_factory=stream_factory,
    )

    with pytest.raises(ExecutableRunConflictError):
        await manager.get_or_start_stream(
            run_id="assistant_chat_turn:session-1:turn-2",
            kind="assistant_chat_turn",
            owner_user_id="user-1",
            session_id="session-1",
            turn_id="turn-2",
            stream_factory=stream_factory,
        )

    with pytest.raises(ExecutableRunAccessError):
        await manager.get_or_start_stream(
            run_id="assistant_chat_turn:session-1:turn-1",
            kind="assistant_chat_turn",
            owner_user_id="user-2",
            session_id="session-1",
            turn_id="turn-1",
            stream_factory=stream_factory,
        )

    block_stream.set()
    if run.task is not None:
        await asyncio.wait_for(run.task, timeout=1)
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_cancel_request_marks_active_session_run_before_producer_claims_lifecycle(monkeypatch):
    monkeypatch.setattr(
        "src.lib.executable_runs.get_executable_run_event_replay_limit",
        lambda: 10,
    )
    monkeypatch.setattr(
        "src.lib.executable_runs.get_executable_run_retention_seconds",
        lambda: 60,
    )

    manager = ExecutableRunManager()
    block_stream = asyncio.Event()

    async def stream_factory():
        await block_stream.wait()
        yield 'data: {"type":"turn_interrupted"}\n\n'

    run, created = await manager.get_or_start_stream(
        run_id="assistant_chat_turn:session-1:turn-1",
        kind="assistant_chat_turn",
        owner_user_id="user-1",
        session_id="session-1",
        turn_id="turn-1",
        stream_factory=stream_factory,
    )

    assert created is True
    assert run.status == "running"

    cancelled_run = await manager.request_cancel_for_session(
        session_id="session-1",
        owner_user_id="user-1",
    )

    assert cancelled_run is run
    assert run.status == "cancel_requested"

    block_stream.set()
    if run.task is not None:
        await asyncio.wait_for(run.task, timeout=1)
    assert run.status == "cancelled"


@pytest.mark.asyncio
async def test_producer_startup_failure_publishes_terminal_error_event(monkeypatch, caplog):
    monkeypatch.setattr(
        "src.lib.executable_runs.get_executable_run_event_replay_limit",
        lambda: 10,
    )
    monkeypatch.setattr(
        "src.lib.executable_runs.get_executable_run_retention_seconds",
        lambda: 60,
    )
    runtime_reports = []
    monkeypatch.setattr(
        "src.lib.executable_runs.report_runtime_exception",
        lambda exc, **kwargs: runtime_reports.append((exc, kwargs)) or True,
    )

    manager = ExecutableRunManager()
    caplog.set_level(logging.WARNING, logger="src.lib.executable_runs")

    async def stream_factory():
        raise RuntimeError("startup rejected")
        yield 'data: {"type":"unreachable"}\n\n'

    run, created = await manager.get_or_start_stream(
        run_id="assistant_chat_turn:session-1:turn-1",
        kind="assistant_chat_turn",
        owner_user_id="user-1",
        session_id="session-1",
        turn_id="turn-1",
        stream_factory=stream_factory,
        terminal_error_event_factory=lambda exc: (
            f'data: {{"type":"turn_failed","message":"{exc}"}}\n\n'
        ),
    )

    assert created is True

    observer = manager.observe(run)
    error_event = await asyncio.wait_for(observer.__anext__(), timeout=1)
    assert "turn_failed" in error_event
    assert "startup rejected" in error_event

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(observer.__anext__(), timeout=1)

    assert run.status == "failed"
    assert len(runtime_reports) == 1
    reported_exc, report_kwargs = runtime_reports[0]
    assert isinstance(reported_exc, RuntimeError)
    assert str(reported_exc) == "startup rejected"
    assert report_kwargs["component"] == "executable_run"
    assert report_kwargs["operation"] == "producer_failed"
    assert report_kwargs["tags"] == {"run_kind": "assistant_chat_turn"}
    assert report_kwargs["context"] == {
        "run_id": "assistant_chat_turn:session-1:turn-1",
        "kind": "assistant_chat_turn",
        "session_id": "session-1",
        "turn_id": "turn-1",
        "flow_run_id": None,
        "batch_id": None,
        "job_id": None,
        "terminal_error_event_factory": True,
    }
    failure_logs = [
        record
        for record in caplog.records
        if record.message.startswith("Executable run producer failed")
    ]
    assert len(failure_logs) == 1
    assert failure_logs[0].levelno == logging.WARNING
    assert failure_logs[0].exc_info is not None


@pytest.mark.asyncio
async def test_failed_terminal_run_replays_without_restart_for_same_run_id(monkeypatch):
    monkeypatch.setattr(
        "src.lib.executable_runs.get_executable_run_event_replay_limit",
        lambda: 10,
    )
    monkeypatch.setattr(
        "src.lib.executable_runs.get_executable_run_retention_seconds",
        lambda: 60,
    )

    manager = ExecutableRunManager()
    attempts = 0

    async def stream_factory():
        nonlocal attempts
        attempts += 1
        raise RuntimeError("first attempt failed")
        yield 'data: {"type":"unreachable"}\n\n'

    run, created = await manager.get_or_start_stream(
        run_id="curation_flow_run:session-1:turn-1",
        kind="curation_flow_run",
        owner_user_id="user-1",
        session_id="session-1",
        turn_id="turn-1",
        stream_factory=stream_factory,
        terminal_error_event_factory=lambda exc: (
            f'data: {{"type":"turn_failed","message":"{exc}"}}\n\n'
        ),
    )

    assert created is True
    if run.task is not None:
        await asyncio.wait_for(run.task, timeout=1)
    assert run.status == "failed"

    retry_run, retry_created = await manager.get_or_start_stream(
        run_id="curation_flow_run:session-1:turn-1",
        kind="curation_flow_run",
        owner_user_id="user-1",
        session_id="session-1",
        turn_id="turn-1",
        stream_factory=stream_factory,
    )

    assert retry_created is False
    assert retry_run is run
    observer = manager.observe(retry_run)
    failure_event = await asyncio.wait_for(observer.__anext__(), timeout=1)
    assert "turn_failed" in failure_event
    assert "first attempt failed" in failure_event

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(observer.__anext__(), timeout=1)

    assert retry_run.status == "failed"
    assert attempts == 1


@pytest.mark.asyncio
async def test_cancelled_terminal_run_replays_without_restart_for_same_run_id(monkeypatch):
    monkeypatch.setattr(
        "src.lib.executable_runs.get_executable_run_event_replay_limit",
        lambda: 10,
    )
    monkeypatch.setattr(
        "src.lib.executable_runs.get_executable_run_retention_seconds",
        lambda: 60,
    )

    manager = ExecutableRunManager()
    attempts = 0
    continue_stream = asyncio.Event()

    async def stream_factory():
        nonlocal attempts
        attempts += 1
        await continue_stream.wait()
        yield 'data: {"type":"turn_interrupted"}\n\n'

    run, created = await manager.get_or_start_stream(
        run_id="assistant_chat_turn:session-1:turn-1",
        kind="assistant_chat_turn",
        owner_user_id="user-1",
        session_id="session-1",
        turn_id="turn-1",
        stream_factory=stream_factory,
    )

    assert created is True
    cancelled_run = await manager.request_cancel_for_session(
        session_id="session-1",
        owner_user_id="user-1",
    )
    assert cancelled_run is run

    continue_stream.set()
    if run.task is not None:
        await asyncio.wait_for(run.task, timeout=1)
    assert run.status == "cancelled"

    retry_run, retry_created = await manager.get_or_start_stream(
        run_id="assistant_chat_turn:session-1:turn-1",
        kind="assistant_chat_turn",
        owner_user_id="user-1",
        session_id="session-1",
        turn_id="turn-1",
        stream_factory=stream_factory,
    )

    assert retry_created is False
    assert retry_run is run
    observer = manager.observe(retry_run)
    interrupted_event = await asyncio.wait_for(observer.__anext__(), timeout=1)
    assert "turn_interrupted" in interrupted_event

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(observer.__anext__(), timeout=1)

    assert retry_run.status == "cancelled"
    assert attempts == 1
