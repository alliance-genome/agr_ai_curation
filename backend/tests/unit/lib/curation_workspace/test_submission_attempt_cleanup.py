"""Tests for periodic direct-submission attempt retention cleanup."""

import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.lib.curation_workspace import submission_attempt_cleanup as cleanup
from src.lib.openai_agents.config import get_submission_attempt_cleanup_interval_seconds


def test_cleanup_interval_is_env_configurable(monkeypatch):
    monkeypatch.delenv("SUBMISSION_ATTEMPT_CLEANUP_INTERVAL_SECONDS", raising=False)
    assert get_submission_attempt_cleanup_interval_seconds() == 3600

    monkeypatch.setenv("SUBMISSION_ATTEMPT_CLEANUP_INTERVAL_SECONDS", "75")
    assert get_submission_attempt_cleanup_interval_seconds() == 75


def test_purge_submission_attempts_once_commits_independent_transaction(monkeypatch):
    db = MagicMock()

    @contextmanager
    def session_factory():
        yield db

    purge = MagicMock(return_value=3)
    monkeypatch.setattr(cleanup, "SessionLocal", session_factory)
    monkeypatch.setattr(cleanup, "purge_expired_submission_attempts", purge)

    assert cleanup.purge_submission_attempts_once() == 3

    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()
    assert purge.call_args.args == (db,)
    assert purge.call_args.kwargs["before"].tzinfo is not None


def test_purge_submission_attempts_once_rolls_back_failure(monkeypatch):
    db = MagicMock()

    @contextmanager
    def session_factory():
        yield db

    monkeypatch.setattr(cleanup, "SessionLocal", session_factory)
    monkeypatch.setattr(
        cleanup,
        "purge_expired_submission_attempts",
        MagicMock(side_effect=RuntimeError("database unavailable")),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        cleanup.purge_submission_attempts_once()

    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_connection_checkout_closes_late_result(monkeypatch):
    checkout_started = asyncio.Event()
    finish_checkout = asyncio.Event()
    leader_connection = MagicMock()
    open_connection = MagicMock(return_value=leader_connection)

    async def call_in_thread(function, *args):
        if function is open_connection:
            checkout_started.set()
            await finish_checkout.wait()
        return function(*args)

    monkeypatch.setattr(
        cleanup,
        "_open_cleanup_leadership_connection",
        open_connection,
    )
    monkeypatch.setattr(
        cleanup.asyncio,
        "to_thread",
        AsyncMock(side_effect=call_in_thread),
    )

    checkout_task = asyncio.create_task(
        cleanup._open_cleanup_leadership_connection_in_thread()
    )
    await checkout_started.wait()
    checkout_task.cancel()
    finish_checkout.set()

    with pytest.raises(asyncio.CancelledError):
        await checkout_task

    leader_connection.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_periodic_cleanup_continues_after_a_failed_pass(monkeypatch):
    purge = MagicMock(side_effect=[RuntimeError("temporary failure"), 0])

    async def call_in_thread(function, *args):
        return function(*args)

    to_thread = AsyncMock(side_effect=call_in_thread)
    sleep = AsyncMock(side_effect=[None, asyncio.CancelledError])
    log_exception = MagicMock()
    leader_connection = MagicMock()
    connect = MagicMock()
    connect.return_value.execution_options.return_value = leader_connection
    monkeypatch.setattr(cleanup.asyncio, "to_thread", to_thread)
    monkeypatch.setattr(cleanup.asyncio, "sleep", sleep)
    monkeypatch.setattr(cleanup.engine, "connect", connect)
    monkeypatch.setattr(cleanup, "_try_acquire_cleanup_leadership", MagicMock(return_value=True))
    monkeypatch.setattr(cleanup, "_release_cleanup_leadership", MagicMock())
    monkeypatch.setattr(cleanup, "_verify_cleanup_leadership_connection", MagicMock())
    monkeypatch.setattr(cleanup, "purge_submission_attempts_once", purge)
    monkeypatch.setattr(cleanup, "get_submission_attempt_cleanup_interval_seconds", lambda: 17)
    monkeypatch.setattr(cleanup.logger, "exception", log_exception)

    with pytest.raises(asyncio.CancelledError):
        await cleanup._run_submission_attempt_cleanup()

    assert to_thread.await_count == 8
    assert to_thread.await_args_list[0].args == (
        cleanup._open_cleanup_leadership_connection,
    )
    assert to_thread.await_args_list[-1].args == (leader_connection.close,)
    assert sleep.await_args_list[0].args == (17,)
    log_exception.assert_called_once_with("Submission attempt retention cleanup failed")


@pytest.mark.asyncio
async def test_cleanup_follower_retries_without_purging(monkeypatch):
    leader_connection = MagicMock()
    connect = MagicMock()
    connect.return_value.execution_options.return_value = leader_connection
    acquire = MagicMock(return_value=False)
    purge = MagicMock()

    async def cancel_after_connection_returned(_interval_seconds):
        leader_connection.close.assert_called_once_with()
        raise asyncio.CancelledError

    sleep = AsyncMock(side_effect=cancel_after_connection_returned)
    monkeypatch.setattr(cleanup.engine, "connect", connect)
    monkeypatch.setattr(cleanup, "_try_acquire_cleanup_leadership", acquire)
    monkeypatch.setattr(cleanup, "purge_submission_attempts_once", purge)
    monkeypatch.setattr(cleanup.asyncio, "sleep", sleep)
    monkeypatch.setattr(cleanup, "get_submission_attempt_cleanup_interval_seconds", lambda: 23)

    with pytest.raises(asyncio.CancelledError):
        await cleanup._run_submission_attempt_cleanup()

    acquire.assert_called_once_with(leader_connection)
    purge.assert_not_called()
    sleep.assert_awaited_once_with(23)


@pytest.mark.asyncio
async def test_cleanup_relinquishes_leadership_after_connection_loss(monkeypatch):
    async def call_in_thread(function, *args):
        return function(*args)

    leader_connection = MagicMock()
    connect = MagicMock()
    connect.return_value.execution_options.return_value = leader_connection
    release = MagicMock()
    purge = MagicMock()
    sleep = AsyncMock(side_effect=asyncio.CancelledError)
    log_exception = MagicMock()
    monkeypatch.setattr(cleanup.engine, "connect", connect)
    monkeypatch.setattr(cleanup.asyncio, "to_thread", AsyncMock(side_effect=call_in_thread))
    monkeypatch.setattr(cleanup.asyncio, "sleep", sleep)
    monkeypatch.setattr(cleanup, "_try_acquire_cleanup_leadership", MagicMock(return_value=True))
    monkeypatch.setattr(
        cleanup,
        "_verify_cleanup_leadership_connection",
        MagicMock(side_effect=RuntimeError("connection lost")),
    )
    monkeypatch.setattr(cleanup, "_release_cleanup_leadership", release)
    monkeypatch.setattr(cleanup, "purge_submission_attempts_once", purge)
    monkeypatch.setattr(cleanup, "get_submission_attempt_cleanup_interval_seconds", lambda: 29)
    monkeypatch.setattr(cleanup.logger, "exception", log_exception)

    with pytest.raises(asyncio.CancelledError):
        await cleanup._run_submission_attempt_cleanup()

    release.assert_called_once_with(leader_connection)
    leader_connection.close.assert_called_once_with()
    purge.assert_not_called()
    log_exception.assert_called_once_with(
        "Submission attempt cleanup leadership coordination failed"
    )
    sleep.assert_awaited_once_with(29)


@pytest.mark.asyncio
async def test_cleanup_scheduler_is_singleton_and_stops_cleanly(monkeypatch):
    started = asyncio.Event()

    async def wait_forever():
        started.set()
        await asyncio.Event().wait()

    cleanup._cleanup_task = None
    monkeypatch.setattr(cleanup, "_run_submission_attempt_cleanup", wait_forever)

    first = cleanup.schedule_submission_attempt_cleanup()
    second = cleanup.schedule_submission_attempt_cleanup()
    await started.wait()

    assert first is second
    assert first.get_name() == "submission-attempt-retention-cleanup"

    await cleanup.stop_submission_attempt_cleanup()

    assert first.cancelled()
    assert cleanup._cleanup_task is None
