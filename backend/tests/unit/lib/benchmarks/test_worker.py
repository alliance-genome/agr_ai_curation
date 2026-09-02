"""Unit coverage for durable benchmark worker dispatch controls."""

import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.lib.benchmarks.persistence import BenchmarkLeaseLostError
from src.lib.benchmarks.worker import BenchmarkWorker, _report_failure


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
