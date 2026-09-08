"""Unit coverage for durable benchmark worker dispatch controls."""

import asyncio
import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.persistence import BenchmarkLeaseLostError
from src.lib.benchmarks.worker import BenchmarkWorker, _report_failure


@pytest.fixture
def startup_runtime(monkeypatch):
    """Replace only startup I/O; the process entrypoint remains real."""
    from src.lib.benchmarks import worker
    from src.lib.config import groups_loader
    from src.lib.openai_agents import langfuse_client
    from src.lib.prompts import cache

    calls = []
    session = MagicMock()
    factory = MagicMock(return_value=session)
    session.__enter__.side_effect = lambda: calls.append("open") or session
    session.__exit__.side_effect = lambda *args: calls.append("close")
    prompts = MagicMock(side_effect=lambda db: calls.append("prompts"))
    groups = MagicMock(side_effect=lambda: calls.append("groups"))
    tracing = MagicMock(side_effect=lambda: calls.append("tracing"))
    flush = MagicMock(side_effect=lambda: calls.append("flush"))
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(cache, "initialize", prompts)
    monkeypatch.setattr(groups_loader, "load_groups", groups)
    monkeypatch.setattr(langfuse_client, "is_langfuse_configured", lambda: True)
    monkeypatch.setattr(langfuse_client, "initialize_langfuse", tracing)
    monkeypatch.setattr(langfuse_client, "flush_langfuse", flush)
    monkeypatch.setattr(worker, "get_benchmark_worker_enabled", lambda: True)
    monkeypatch.setattr(worker, "get_benchmark_execution_enabled", lambda: True)
    monkeypatch.setattr(worker, "get_benchmark_worker_concurrency", lambda: 2)
    return SimpleNamespace(
        worker=worker, calls=calls, factory=factory, session=session,
        prompts=prompts, groups=groups, tracing=tracing, flush=flush,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,execution", [(False, False), (False, True), (True, False)])
async def test_main_disabled_gates_skip_all_startup_and_worker_construction(
    monkeypatch, startup_runtime, enabled, execution,
):
    state = startup_runtime
    monkeypatch.setattr(state.worker, "get_benchmark_worker_enabled", lambda: enabled)
    monkeypatch.setattr(state.worker, "get_benchmark_execution_enabled", lambda: execution)
    constructor = MagicMock(side_effect=AssertionError("must not construct worker"))
    monkeypatch.setattr(state.worker, "BenchmarkWorker", constructor)
    await state.worker._main()
    assert state.calls == []
    state.factory.assert_not_called()
    constructor.assert_not_called()


@pytest.mark.asyncio
async def test_main_initializes_once_before_all_claim_loops(monkeypatch, startup_runtime):
    state = startup_runtime

    async def run(_self):
        assert state.calls[:5] == ["open", "prompts", "close", "groups", "tracing"]
        state.calls.append("claim")

    monkeypatch.setattr(BenchmarkWorker, "run_forever", run)
    await state.worker._main()
    assert state.calls == ["open", "prompts", "close", "groups", "tracing", "claim", "claim", "flush"]
    state.prompts.assert_called_once_with(state.session)
    state.session.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["prompts", "groups"])
async def test_required_startup_failure_closes_session_and_claims_nothing(
    monkeypatch, startup_runtime, failure_stage,
):
    state = startup_runtime
    getattr(state, failure_stage).side_effect = ValueError("private SQL or credential")
    constructor = MagicMock()
    monkeypatch.setattr(state.worker, "BenchmarkWorker", constructor)
    with pytest.raises(RuntimeError, match="Benchmark worker startup failed") as caught:
        await state.worker._main()
    assert "private" not in str(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    state.session.__exit__.assert_called_once()
    state.tracing.assert_not_called()
    state.flush.assert_not_called()
    constructor.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("tracing_outcome", ["unconfigured", "unavailable", "failed", "none"])
async def test_optional_tracing_never_blocks_claim_loops(
    monkeypatch, startup_runtime, tracing_outcome, caplog,
):
    from src.lib.openai_agents import langfuse_client

    state = startup_runtime
    if tracing_outcome == "unconfigured":
        monkeypatch.setattr(langfuse_client, "is_langfuse_configured", lambda: False)
    elif tracing_outcome == "unavailable":
        state.tracing.side_effect = ImportError("private connection")
    elif tracing_outcome == "failed":
        state.tracing.side_effect = RuntimeError("private connection")
    else:
        state.tracing.side_effect = None
        state.tracing.return_value = None
    run = AsyncMock()
    monkeypatch.setattr(BenchmarkWorker, "run_forever", run)
    await state.worker._main()
    assert run.await_count == 2
    assert "private connection" not in caplog.text
    state.flush.assert_called_once()
    if tracing_outcome == "unconfigured":
        state.tracing.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["failure", "cancel"])
async def test_main_stops_siblings_before_best_effort_flush(
    monkeypatch, startup_runtime, stop, caplog,
):
    state = startup_runtime
    started = asyncio.Event()
    waiting = asyncio.Event()
    calls = 0

    async def run(_self):
        nonlocal calls
        calls += 1
        if calls == 1:
            await started.wait()
            if stop == "failure":
                raise ValueError("loop failure")
            await waiting.wait()
        else:
            started.set()
            try:
                await waiting.wait()
            finally:
                state.calls.append("stopped")

    def failing_flush():
        assert state.calls[-1] == "stopped"
        state.calls.append("flush")
        raise RuntimeError("private tracing connection")

    state.flush.side_effect = failing_flush
    monkeypatch.setattr(BenchmarkWorker, "run_forever", run)
    task = asyncio.create_task(state.worker._main())
    if stop == "cancel":
        await started.wait()
        task.cancel()
    with pytest.raises(ValueError if stop == "failure" else asyncio.CancelledError):
        await task
    assert state.calls[-2:] == ["stopped", "flush"]
    assert "private tracing connection" not in caplog.text


@pytest.mark.asyncio
async def test_worker_uses_prepared_identity_and_explicit_query_then_rechecks_authorization(monkeypatch):
    context = BenchmarkCuratorContext(
        subject="verified-curator", auth_provider="oidc", db_user_id=42,
        active_groups=("group-alpha",),
    )
    check = AsyncMock(return_value=context)
    monkeypatch.setattr("src.lib.benchmarks.worker.authorize_benchmark_curator", check)
    prepared = SimpleNamespace(document_id=uuid4())
    prepare = AsyncMock(return_value=(prepared, context))
    monkeypatch.setattr("src.lib.benchmarks.worker.prepare_job_document", prepare)
    executor = AsyncMock(return_value="synthetic-result")
    worker = BenchmarkWorker()
    resolved = MagicMock()
    resolved.user_query = "Extract the requested evidence."
    cell = MagicMock()
    result = await worker._run_authorized_cell(executor, resolved, "run", cell)
    assert result == "synthetic-result"
    check.assert_awaited_once_with(context, session_factory=worker.session_factory)
    assert executor.call_args.args[1] == {
        "user_id": "verified-curator", "db_user_id": 42, "active_groups": ["group-alpha"],
        "document_id": str(prepared.document_id), "document_name": f"benchmark-{prepared.document_id}",
        "user_query": resolved.user_query,
        "messages": [{"role": "user", "content": resolved.user_query}],
    }
    prepare.assert_awaited_once_with(
        job_id=cell.job_id, snapshot_id=cell.input_snapshot_id,
        lease_owner=worker.worker_id, session_factory=worker.session_factory,
    )

    check.side_effect = PermissionError("revoked")
    executor.reset_mock()
    with pytest.raises(PermissionError):
        await worker._run_authorized_cell(executor, resolved, "run", cell)
    executor.assert_not_called()


@pytest.mark.parametrize(
    ("operation_override", "expected_operation"),
    [
        (None, "cell_execution_failed"),
        ("cell_terminalization_failed", "cell_terminalization_failed"),
    ],
)
def test_worker_failure_reporting_is_single_capture_with_non_promoted_log(
    monkeypatch, caplog, operation_override, expected_operation
):
    captured = []
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception",
        lambda exc, **kwargs: captured.append((exc, kwargs)),
    )
    caplog.set_level(logging.ERROR, logger="src.lib.benchmarks.worker")

    kwargs = {"operation": operation_override} if operation_override else {}
    _report_failure(
        RuntimeError("token=distinctive-private-value"),
        job_id=uuid4(),
        cell_id=uuid4(),
        **kwargs,
    )

    assert len(captured) == 1
    captured_exception, captured_context = captured[0]
    assert "distinctive-private-value" not in str(captured_exception)
    assert captured_exception.__context__ is None
    assert captured_exception.__cause__ is None
    assert captured_context["component"] == "benchmark_worker"
    assert captured_context["operation"] == expected_operation
    assert "distinctive-private-value" not in caplog.text
    matching_records = [
        record
        for record in caplog.records
        if record.getMessage()
        == (
            "Benchmark worker operation failed: "
            f"operation={expected_operation} error_type=RuntimeError"
        )
    ]
    assert len(matching_records) == 1
    assert matching_records[0].sentry_skip_event is True


@pytest.mark.asyncio
async def test_worker_claims_nothing_unless_both_gates_are_enabled(monkeypatch):
    monkeypatch.setattr(
        "src.lib.benchmarks.worker.get_benchmark_worker_enabled", lambda: True
    )
    monkeypatch.setattr(
        "src.lib.benchmarks.worker.get_benchmark_execution_enabled", lambda: False
    )
    worker = BenchmarkWorker()
    monkeypatch.setattr(
        worker,
        "recover_expired",
        lambda: pytest.fail("disabled worker must not inspect or claim work"),
    )

    assert await worker.run_once() is False


@pytest.mark.asyncio
async def test_disabled_worker_entrypoint_exits_without_polling(monkeypatch):
    monkeypatch.setattr(
        "src.lib.benchmarks.worker.get_benchmark_worker_enabled", lambda: False
    )
    monkeypatch.setattr(
        "src.lib.benchmarks.worker.get_benchmark_execution_enabled", lambda: True
    )
    worker = BenchmarkWorker()
    monkeypatch.setattr(
        worker,
        "run_once",
        lambda: pytest.fail("disabled entrypoint must not enter its poll loop"),
    )

    await worker.run_forever()


@pytest.mark.asyncio
async def test_worker_continues_with_independent_sibling_cells(monkeypatch):
    monkeypatch.setattr(
        "src.lib.benchmarks.worker.get_benchmark_worker_enabled", lambda: True
    )
    monkeypatch.setattr(
        "src.lib.benchmarks.worker.get_benchmark_execution_enabled", lambda: True
    )
    worker = BenchmarkWorker()
    job_id = uuid4()
    cell_ids = [uuid4(), uuid4()]
    processed = []
    finish_checks = iter((False, False, True))

    monkeypatch.setattr(worker, "recover_expired", lambda: ())
    monkeypatch.setattr(worker, "_claim_job", lambda: job_id)
    monkeypatch.setattr(worker, "_finish_or_cancel_job", lambda _job: next(finish_checks))
    monkeypatch.setattr(worker, "_claim_cell", lambda _job: cell_ids.pop(0))

    async def _execute(cell_id):
        processed.append(cell_id)

    monkeypatch.setattr(worker, "_execute_cell", _execute)

    assert await worker.run_once() is True
    assert len(processed) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("lease_loss_source", ["finish", "claim"])
async def test_worker_yields_job_when_lease_is_lost(monkeypatch, lease_loss_source):
    monkeypatch.setattr(
        "src.lib.benchmarks.worker.get_benchmark_worker_enabled", lambda: True
    )
    monkeypatch.setattr(
        "src.lib.benchmarks.worker.get_benchmark_execution_enabled", lambda: True
    )
    worker = BenchmarkWorker()
    job_id = uuid4()

    monkeypatch.setattr(worker, "recover_expired", lambda: ())
    monkeypatch.setattr(worker, "_claim_job", lambda: job_id)

    def _lease_lost(_job_id):
        raise BenchmarkLeaseLostError("stale worker")

    if lease_loss_source == "finish":
        monkeypatch.setattr(worker, "_finish_or_cancel_job", _lease_lost)
        monkeypatch.setattr(
            worker,
            "_claim_cell",
            lambda _job_id: pytest.fail("cell claim must not run after lease loss"),
        )
    else:
        monkeypatch.setattr(worker, "_finish_or_cancel_job", lambda _job_id: False)
        monkeypatch.setattr(worker, "_claim_cell", _lease_lost)

    assert await worker.run_once() is False


@pytest.mark.asyncio
async def test_worker_reports_terminalization_failure_without_raising(monkeypatch):
    cell = SimpleNamespace(id=uuid4(), job_id=uuid4())
    reported = []

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, _model, _identifier):
            return cell

        def expunge(self, _value):
            return None

        def rollback(self):
            return None

    class _Repository:
        def __init__(self, _session):
            pass

        def finish_cell(self, **_kwargs):
            raise ConnectionError("token=distinctive-terminalization-secret")

    def _invalid_cell(_cell_id):
        raise ValueError("invalid frozen input")

    worker = BenchmarkWorker(session_factory=_Session)
    monkeypatch.setattr(worker, "_load_cell", _invalid_cell)
    monkeypatch.setattr("src.lib.benchmarks.worker.BenchmarkRepository", _Repository)
    monkeypatch.setattr(
        "src.lib.benchmarks.worker._report_failure",
        lambda exc, **kwargs: reported.append((exc, kwargs)),
    )

    await worker._execute_cell(cell.id)

    assert [item[1].get("operation") for item in reported] == [
        "cell_terminalization_failed",
        None,
    ]
    assert isinstance(reported[0][0], ConnectionError)
    assert isinstance(reported[1][0], ValueError)
