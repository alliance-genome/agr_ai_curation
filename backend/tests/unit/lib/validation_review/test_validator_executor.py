"""Validator threads must not starve leases or lose request-local accounting."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event
from types import SimpleNamespace

import pytest

from src.lib.validation_history.store import HistoryScope
from src.lib.validation_review import execution, provider, validator_adapter


# Failure guards only; ordering is established by events/barriers, never sleeps.
_WAIT = 5
_SCOPE = HistoryScope("fixture-owner", "00000000-0000-0000-0000-000000000001", "fixture-session")


def _adapter(monkeypatch, run):
    """Replace saved-plan/DB seams, retaining the actual async call boundary."""
    adapter = object.__new__(validator_adapter.ValidatorAdapter)
    adapter.plan_ref = "fixture-plan"
    job = SimpleNamespace(match=SimpleNamespace(binding=SimpleNamespace(binding_id="fixture")))
    adapter.require_current_invocation = lambda payload: None
    adapter.job = lambda request: job
    adapter.receipt = lambda jobs, **kwargs: {"contracts": {"fixture": "frozen"}}
    adapter._call = run
    monkeypatch.setattr(validator_adapter, "binding_contract", lambda match: "frozen")
    return adapter


def _payload(name):
    return {
        "name": name,
        "receipt": {"plan_ref": "fixture-plan", "batch_key": None,
                    "contracts": {"fixture": "frozen"}},
        "requests": [{"fixture": name}],
        "invocation": {"attempt_ids": [name]},
        "token_pool": "initial_tokens",
    }


def test_blocked_validators_leave_default_executor_available_for_lease_heartbeat(monkeypatch):
    started = {name: Event() for name in ("one", "two")}
    release = Event()
    heartbeats = []

    def run(payload, jobs):
        started[payload["name"]].set()
        assert release.wait(_WAIT), "The heartbeat could not run while validators were blocked"
        return payload["name"]

    def heartbeat(scope, work_id, token):
        assert (scope, work_id, token) == (_SCOPE, "work", "lease")
        # Early pulses are harmless. Only a pulse while both validators occupy
        # their lanes may release them; the old shared executor cannot do this.
        if all(event.is_set() for event in started.values()):
            heartbeats.append(work_id)
            release.set()

    adapter = _adapter(monkeypatch, run)
    monkeypatch.setattr(execution.work, "heartbeat", heartbeat)
    with ThreadPoolExecutor(max_workers=2) as pool:
        monkeypatch.setattr(validator_adapter, "_VALIDATOR_POOL", pool)

        async def scenario():
            asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
            try:
                result = await asyncio.wait_for(execution._while_owned(
                    asyncio.gather(adapter.call(_payload("one")), adapter.call(_payload("two"))),
                    _SCOPE, "work", "lease", 0,
                ), _WAIT)
                assert result == ["one", "two"] and heartbeats
            finally:
                release.set()

        asyncio.run(scenario())


def test_each_validator_submission_copies_its_own_real_budget_context(monkeypatch):
    barrier = Barrier(2)
    budgets = {name: provider.ExecutionBudget(_SCOPE, name, "initial_tokens", reviewer=False)
               for name in ("one", "two")}

    def run(payload, jobs):
        name = payload["name"]
        if name == "later":
            return provider.ACTIVE_BUDGET.get()
        observed = provider.ACTIVE_BUDGET.get()
        assert observed is budgets[name]
        # Both copies are live simultaneously; modifying one thread's binding
        # must not replace the other thread's or the submitting task's binding.
        token = provider.ACTIVE_BUDGET.set(None)
        try:
            barrier.wait(timeout=_WAIT)
            assert provider.ACTIVE_BUDGET.get() is None
        finally:
            provider.ACTIVE_BUDGET.reset(token)
        return observed

    adapter = _adapter(monkeypatch, run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        monkeypatch.setattr(validator_adapter, "_VALIDATOR_POOL", pool)

        async def scenario():
            baseline = provider.ACTIVE_BUDGET.get()

            async def submit(name):
                with provider.budget_scope(budgets[name]):
                    result = await adapter.call(_payload(name))
                    assert provider.ACTIVE_BUDGET.get() is budgets[name]
                    return result

            try:
                results = await asyncio.wait_for(asyncio.gather(submit("one"), submit("two")), _WAIT)
                assert all(result is budgets[name] for result, name in zip(results, ("one", "two")))
                assert provider.ACTIVE_BUDGET.get() is baseline
                assert await adapter.call(_payload("later")) is baseline
            finally:
                barrier.abort()

        asyncio.run(scenario())


def test_cancelled_queued_validator_never_starts_and_execution_records_uncertainty(monkeypatch):
    release = Event()
    provider_calls = []
    transitions = []
    payload = _payload("queued")

    @contextmanager
    def database():
        yield SimpleNamespace(get=lambda model, work_id: SimpleNamespace(episode_id="episode"))

    monkeypatch.setattr(execution.store, "SessionLocal", database)
    monkeypatch.setattr(execution.admission, "lock_episode", lambda *args: (
        SimpleNamespace(id="episode", artifact_id="artifact", policy={"lease_seconds": 30}), []))
    monkeypatch.setattr(provider, "remaining_seconds", lambda budget: None)
    monkeypatch.setattr(execution.work, "claim", lambda *args: {"lease_token": "lease", "payload": payload})
    monkeypatch.setattr(execution.work, "mark_submitted", lambda *args: transitions.append("submitted"))
    monkeypatch.setattr(execution.work, "mark_uncertain", lambda *args, reason: transitions.append(reason))
    monkeypatch.setattr(execution.work, "finish", lambda *args, **kwargs: pytest.fail("Cancelled work finished"))
    monkeypatch.setattr(execution.store, "complete_attempt", lambda *args, **kwargs: pytest.fail("Queued provider produced an answer"))

    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(validator_adapter, "_VALIDATOR_POOL", pool)

        async def scenario():
            loop = asyncio.get_running_loop()
            loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
            started, queued = asyncio.Event(), asyncio.Event()
            original_submit = pool.submit
            submissions = []

            def submit(function, *args, **kwargs):
                future = original_submit(function, *args, **kwargs)
                submissions.append(future)
                if len(submissions) == 2:
                    queued.set()
                return future

            monkeypatch.setattr(pool, "submit", submit)

            def run(invocation, jobs):
                provider_calls.append(invocation["name"])
                loop.call_soon_threadsafe(started.set)
                assert release.wait(_WAIT), "Test did not release running provider"
                return []

            adapter = _adapter(monkeypatch, run)
            occupying = asyncio.create_task(adapter.call(_payload("occupying")))
            cancelled = None
            try:
                await asyncio.wait_for(started.wait(), _WAIT)
                cancelled = asyncio.create_task(execution.execute(_SCOPE, "queued-work", call=adapter.call))
                await asyncio.wait_for(queued.wait(), _WAIT)
                assert not submissions[1].running()
                cancelled.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(cancelled, _WAIT)
                assert transitions == ["submitted", "cancelled"]
                assert submissions[1].cancelled()
                release.set()
                await asyncio.wait_for(occupying, _WAIT)
                # A sentinel behind the cancelled future proves the queue drained.
                await asyncio.wait_for(loop.run_in_executor(pool, lambda: None), _WAIT)
                assert provider_calls == ["occupying"]
            finally:
                release.set()
                for task in (occupying, cancelled):
                    if task is not None and not task.done():
                        task.cancel()
                await asyncio.gather(*(task for task in (occupying, cancelled) if task is not None),
                                     return_exceptions=True)

        asyncio.run(scenario())
