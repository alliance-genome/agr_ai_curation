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
async def test_periodic_cleanup_continues_after_a_failed_pass(monkeypatch):
    to_thread = AsyncMock(side_effect=[RuntimeError("temporary failure"), 0])
    sleep = AsyncMock(side_effect=[None, asyncio.CancelledError])
    log_exception = MagicMock()
    monkeypatch.setattr(cleanup.asyncio, "to_thread", to_thread)
    monkeypatch.setattr(cleanup.asyncio, "sleep", sleep)
    monkeypatch.setattr(cleanup, "get_submission_attempt_cleanup_interval_seconds", lambda: 17)
    monkeypatch.setattr(cleanup.logger, "exception", log_exception)

    with pytest.raises(asyncio.CancelledError):
        await cleanup._run_submission_attempt_cleanup()

    assert to_thread.await_count == 2
    assert sleep.await_args_list[0].args == (17,)
    log_exception.assert_called_once_with("Submission attempt retention cleanup failed")


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
