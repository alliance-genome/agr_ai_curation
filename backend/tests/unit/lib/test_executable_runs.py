import asyncio
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
