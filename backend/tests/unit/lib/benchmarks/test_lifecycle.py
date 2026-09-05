import asyncio
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import uuid4

import pytest

from src.lib.benchmarks import lifecycle
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.suites import resolve_suite, validate_suite
from tests.unit.lib.benchmarks.test_suites import _catalog, _payload


@pytest.mark.parametrize("query", [None, "   "])
def test_agent_submission_without_curator_query_is_a_stable_validation_failure(query):
    value = _payload()
    value["cases"][0]["target"] = {"kind": "agent", "id": "extractor"}
    value["cases"][0]["user_query"] = query
    value["configurations"] = [{"configuration_id": "defaults", "routes": {}}]
    plan = resolve_suite(validate_suite(value), _catalog(), max_cases=100,
                         max_configurations=100, max_repetitions=100, max_cells=10000)
    with pytest.raises(lifecycle.BenchmarkLifecycleFailure) as error:
        lifecycle.authoritative_plan(suite_value=value, submitted_plan=plan, catalog=_catalog())
    assert error.value.code == "invalid_plan"
    assert error.value.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("created", [False, True])
async def test_catalog_change_does_not_revalidate_accepted_replay(monkeypatch, created):
    suite_value = _payload()
    plan = resolve_suite(
        validate_suite(suite_value), _catalog(),
        max_cases=100, max_configurations=100, max_repetitions=100, max_cells=10000,
    )
    session = MagicMock()
    session.__enter__.return_value = session
    job_id = uuid4()
    reservation = SimpleNamespace(outcome="accepted", job_id=job_id, owner_subject="owner")
    repository = Mock()
    repository.reserve_idempotency.return_value = (reservation, created)
    repository.get_job.return_value = object()
    monkeypatch.setattr(lifecycle, "BenchmarkRepository", Mock(return_value=repository))
    changed_catalog = Mock(side_effect=lifecycle.BenchmarkLifecycleFailure(
        "plan_drift", "Submitted normalized plan does not match the authoritative plan", 409,
    ))
    monkeypatch.setattr(lifecycle, "authoritative_plan", changed_catalog)
    materialize = AsyncMock()
    monkeypatch.setattr(lifecycle, "materialize_plan_inputs", materialize)
    arguments: dict[str, Any] = dict(
        session_factory=Mock(return_value=session), owner_subject="owner", service_principal="owner",
        idempotency_key="same-request", suite_value=suite_value, submitted_plan=plan,
        route_catalog=_catalog(), input_catalog=Mock(), source_context=Mock(), snapshot_store=Mock(),
        curator_context=BenchmarkCuratorContext(
            subject="curator", auth_provider="oidc", db_user_id=1, active_groups=(),
        ),
    )
    if created:
        with pytest.raises(lifecycle.BenchmarkLifecycleFailure) as error:
            await lifecycle.submit_job(**arguments)
        assert error.value.code == "plan_drift"
        repository.fail_idempotency.assert_called_once()
        session.commit.assert_called_once()
    else:
        result = await lifecycle.submit_job(**arguments)
        assert result == lifecycle.BenchmarkAdmissionResult(job_id, True)
        changed_catalog.assert_not_called()
        repository.fail_idempotency.assert_not_called()
    materialize.assert_not_awaited()


@pytest.mark.asyncio
async def test_catalog_failure_reports_sanitized_error_and_keeps_durable_replay(monkeypatch):
    value = _payload()
    plan = resolve_suite(validate_suite(value), _catalog(), max_cases=100,
                         max_configurations=100, max_repetitions=100, max_cells=10000)
    session = MagicMock()
    session.__enter__.return_value = session
    reservation = SimpleNamespace(outcome="pending")
    repository = Mock()
    repository.reserve_idempotency.return_value = (reservation, True)
    monkeypatch.setattr(lifecycle, "BenchmarkRepository", Mock(return_value=repository))
    catalog = Mock(side_effect=ValueError("private-paper-content sql-parameters bearer-value"))
    monkeypatch.setattr("src.lib.benchmarks.runtime_catalog.build_curator_route_catalog", catalog)
    reporter = Mock()
    monkeypatch.setattr(lifecycle, "report_runtime_exception", reporter)
    materialize = AsyncMock()
    monkeypatch.setattr(lifecycle, "materialize_plan_inputs", materialize)
    arguments: dict[str, Any] = dict(
        session_factory=Mock(return_value=session), owner_subject="owner", service_principal="owner",
        idempotency_key="same-request", suite_value=value, submitted_plan=plan,
        route_catalog=None, input_catalog=Mock(), source_context=Mock(), snapshot_store=Mock(),
        curator_context=BenchmarkCuratorContext(
            subject="curator", auth_provider="oidc", db_user_id=1, active_groups=(),
        ),
    )
    with pytest.raises(lifecycle.BenchmarkLifecycleFailure) as error:
        await lifecycle.submit_job(**arguments)
    assert error.value.code == "catalog_unavailable" and error.value.status_code == 503
    failure = repository.fail_idempotency.call_args.kwargs
    assert failure["error_code"] == "catalog_unavailable"
    session.commit.assert_called_once()
    captured = reporter.call_args.args[0]
    assert captured.__traceback__ is not None
    assert captured.__context__ is None and captured.__cause__ is None
    for sensitive in ("private-paper-content", "sql-parameters", "bearer-value"):
        assert sensitive not in str(captured) + str(failure) + str(error.value)
    reservation.outcome = "failed"
    reservation.error_code = failure["error_code"]
    reservation.error_message = failure["error_message"]
    reservation.error_status = failure["error_status"]
    repository.reserve_idempotency.return_value = (reservation, False)
    with pytest.raises(lifecycle.BenchmarkLifecycleFailure) as replay:
        await lifecycle.submit_job(**arguments)
    assert replay.value.code == "catalog_unavailable"
    catalog.assert_called_once()
    reporter.assert_called_once()
    materialize.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_wait_keeps_loop_live_and_session_thread_owned(monkeypatch):
    main_thread = threading.get_ident()
    first_materializing = asyncio.Event()
    release_materializer = asyncio.Event()
    second_waiting = threading.Event()
    committed = threading.Event()
    mutex = threading.Lock()
    reservation_count = 0
    job_id = uuid4()
    suite_value = _payload()
    suite = validate_suite(suite_value)
    plan = resolve_suite(
        suite, _catalog(), max_cases=100, max_configurations=100,
        max_repetitions=100, max_cells=10000,
    )

    def session_factory():
        owner_thread = threading.get_ident()
        assert owner_thread != main_thread
        session = MagicMock()
        session.__enter__.return_value = session
        session.owner_thread = owner_thread
        def commit():
            assert threading.get_ident() == owner_thread
            committed.set()
        session.commit.side_effect = commit
        session.__exit__.side_effect = lambda *args: (
            None if threading.get_ident() == owner_thread else pytest.fail("session moved")
        )
        return session

    def repository_factory(session):
        repository = Mock()
        def reserve(**kwargs):
            nonlocal reservation_count
            assert threading.get_ident() == session.owner_thread
            with mutex:
                reservation_count += 1
                first = reservation_count == 1
            if not first:
                second_waiting.set()
                assert committed.wait(3), "duplicate blocked materializer's event loop"
            return SimpleNamespace(
                outcome="pending" if first else "accepted",
                job_id=job_id, owner_subject="owner",
            ), first
        repository.reserve_idempotency.side_effect = reserve
        repository.create_job.return_value = SimpleNamespace(id=job_id)
        repository.get_job.return_value = object()
        return repository

    async def materialize(*args, **kwargs):
        assert threading.get_ident() == main_thread
        first_materializing.set()
        await release_materializer.wait()
        return object()

    monkeypatch.setattr(lifecycle, "BenchmarkRepository", repository_factory)
    monkeypatch.setattr(lifecycle, "BenchmarkSnapshotRepository", Mock())
    monkeypatch.setattr(lifecycle, "authoritative_plan", Mock(return_value=(suite, plan)))
    materializer = AsyncMock(side_effect=materialize)
    monkeypatch.setattr(lifecycle, "materialize_plan_inputs", materializer)
    arguments: dict[str, Any] = dict(
        session_factory=session_factory, owner_subject="owner", service_principal="owner",
        idempotency_key="same-request", suite_value=suite_value, submitted_plan=plan,
        route_catalog=_catalog(), input_catalog=Mock(), source_context=Mock(), snapshot_store=Mock(),
        curator_context=BenchmarkCuratorContext(
            subject="curator", auth_provider="oidc", db_user_id=1, active_groups=(),
        ),
    )
    first = asyncio.create_task(lifecycle.submit_job(**arguments))
    await asyncio.wait_for(first_materializing.wait(), 3)
    second = asyncio.create_task(lifecycle.submit_job(**arguments))
    try:
        assert await asyncio.to_thread(second_waiting.wait, 3)
    finally:
        release_materializer.set()
    results = await asyncio.wait_for(asyncio.gather(first, second), 3)
    assert results == [
        lifecycle.BenchmarkAdmissionResult(job_id, False),
        lifecycle.BenchmarkAdmissionResult(job_id, True),
    ]
    materializer.assert_awaited_once()
