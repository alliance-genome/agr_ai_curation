"""A review owner preserves discarded faults and SDK wrapper identity."""
import asyncio
from unittest.mock import Mock

import pytest
from agents import UserError
from src.lib.validation_review import audit, execution
from src.lib.validation_review.admission import ReviewStopped


@pytest.mark.parametrize('link', ['__cause__', '__context__'])
def test_audit_deduplicates_only_transparent_wrappers(monkeypatch, link):
    capture = Mock(return_value=True)
    monkeypatch.setattr('src.lib.observability.runtime.report_runtime_exception', capture)
    error = RuntimeError('provider fault')
    audit.report_operational_failure(error)
    for _ in range(2):
        wrapped = UserError('tool wrapper')
        setattr(wrapped, link, error)
        audit.report_operational_failure(wrapped)
        error = wrapped
    assert capture.call_count == 1
    independent = RuntimeError('outcome persistence failed')
    setattr(independent, link, error)
    audit.report_operational_failure(independent)
    assert capture.call_count == 2


def test_failed_capture_never_marks_original(monkeypatch):
    monkeypatch.setattr('src.lib.observability.runtime.report_runtime_exception', Mock(return_value=False))
    error = RuntimeError('provider fault')
    audit.report_operational_failure(error)
    assert not getattr(error, '_ai_curation_sentry_captured', False)


@pytest.mark.asyncio
@pytest.mark.parametrize('worker_error', [RuntimeError('provider fault'), ReviewStopped('complete')])
async def test_heartbeat_winner_does_not_hide_genuine_worker_error(monkeypatch, worker_error):
    captured = []
    monkeypatch.setattr(audit, 'report_operational_failure', captured.append)
    def heartbeat(*args):
        raise ReviewStopped('worker_lease_lost')
    monkeypatch.setattr(execution.work, 'heartbeat', heartbeat)
    async def both_done(tasks, **kwargs):
        await asyncio.gather(*tasks, return_exceptions=True)
        return set(tasks), set()
    monkeypatch.setattr(execution.asyncio, 'wait', both_done)
    async def worker():
        raise worker_error
    with pytest.raises(ReviewStopped, match='worker_lease_lost'):
        await execution._while_owned(worker(), None, 'work', 'lease', 0)
    assert captured == ([worker_error] if isinstance(worker_error, RuntimeError) else [])


@pytest.mark.asyncio
async def test_parent_cancel_retains_already_failed_worker(monkeypatch):
    captured = []
    ready = asyncio.Event()
    error = RuntimeError('provider fault')
    monkeypatch.setattr(audit, 'report_operational_failure', captured.append)
    async def cancel_after_worker(tasks, **kwargs):
        await ready.wait()
        raise asyncio.CancelledError()
    monkeypatch.setattr(execution.asyncio, 'wait', cancel_after_worker)
    async def worker():
        ready.set()
        raise error
    with pytest.raises(asyncio.CancelledError):
        await execution._while_owned(worker(), None, 'work', 'lease', 3)
    assert captured == [error]


@pytest.mark.asyncio
async def test_propagated_fault_keeps_its_outer_owner(monkeypatch):
    captured = []
    monkeypatch.setattr(audit, 'report_operational_failure', captured.append)
    async def worker():
        raise RuntimeError('provider fault')
    with pytest.raises(RuntimeError, match='provider fault'):
        await execution._while_owned(worker(), None, 'work', 'lease', 3)
    assert captured == []
