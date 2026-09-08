"""Real transaction-lock coverage for pre-acceptance admission outcomes."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import threading
import time
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import with_loader_criteria
from types import SimpleNamespace
from typing import Any, cast
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import RootModel

from src.lib.benchmarks import lifecycle
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.input_resolvers import BenchmarkSourceError
from src.lib.benchmarks.input_resolvers import (
    BenchmarkInputResolverCatalog, BenchmarkSourceMetadata, BenchmarkSourceProvenance,
    DelegatedAuthorizationCapability, MaterializedBenchmarkInput,
)
from src.lib.benchmarks.snapshots import FileSystemBenchmarkSnapshotStore
from src.lib.benchmarks import runtime_catalog
from src.api import benchmark_jobs
from tests.unit.lib.benchmarks.test_runtime_catalog import configured as configured
from src.lib.benchmarks import worker as worker_module
from tests.integration.persistence.test_benchmark_worker import _emit_fake_provider_call
from src.lib.benchmarks.suites import resolve_suite, validate_suite
from src.models.sql.benchmark import (
    BenchmarkJobIdempotency, BenchmarkCell, BenchmarkCellStatus,
    BenchmarkInvocation, BenchmarkInvocationStatus,
    BenchmarkJob, BenchmarkJobStatus, BenchmarkJobInputSnapshot,
)
from src.models.sql.database import SessionLocal
from tests.unit.lib.benchmarks.test_suites import _catalog, _payload
from tests.integration.persistence.test_benchmark_repository import _create_job
from src.api.benchmark_jobs import router
from src.api.benchmark_events import _connections, read_event_batch
from src.api.benchmark_auth import (
    require_benchmark_read, require_benchmark_cancel, require_benchmark_delete,
    require_benchmark_run,
)
from src.api.benchmark_curator import require_benchmark_curator
from src.lib.benchmarks.persistence import BenchmarkRepository


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")


@pytest.mark.asyncio
async def test_concurrent_duplicates_wait_then_replay_one_source_failure(monkeypatch):
    owner = f"lifecycle-lock-{uuid4()}"
    key = str(uuid4())
    suite_value = _payload()
    plan = resolve_suite(
        validate_suite(suite_value), _catalog(), max_cases=100,
        max_configurations=100, max_repetitions=100, max_cells=10000,
    )
    materializing = asyncio.Event()
    release_source = asyncio.Event()
    source_calls = 0

    async def materialize(*args, **kwargs):
        nonlocal source_calls
        source_calls += 1
        materializing.set()
        await release_source.wait()
        raise BenchmarkSourceError("missing_source", "Synthetic source is absent")

    monkeypatch.setattr(lifecycle, "materialize_plan_inputs", materialize)
    arguments: dict[str, Any] = dict(
        owner_subject=owner, service_principal=owner, idempotency_key=key,
        suite_value=suite_value, submitted_plan=plan, route_catalog=_catalog(),
        input_catalog=None, source_context=None, snapshot_store=None,
        curator_context=BenchmarkCuratorContext(
            subject="synthetic-curator", auth_provider="oidc", db_user_id=42,
            active_groups=(),
        ),
    )

    def blocked_insert_exists():
        with SessionLocal() as session:
            return session.scalar(text("""
                SELECT EXISTS (
                  SELECT 1 FROM pg_stat_activity
                  WHERE datname = current_database() AND pid != pg_backend_pid()
                    AND cardinality(pg_blocking_pids(pid)) > 0
                    AND query LIKE '%INSERT INTO benchmark_job_idempotency%'
                )
            """))

    first = asyncio.create_task(lifecycle.submit_job(**arguments))
    await asyncio.wait_for(materializing.wait(), 5)
    second = asyncio.create_task(lifecycle.submit_job(**arguments))
    try:
        async with asyncio.timeout(5):
            while not await asyncio.to_thread(blocked_insert_exists):
                await asyncio.sleep(0.01)
    finally:
        release_source.set()
    results = await asyncio.wait_for(
        asyncio.gather(first, second, return_exceptions=True), 5,
    )
    assert source_calls == 1
    for result in results:
        assert isinstance(result, lifecycle.BenchmarkLifecycleFailure)
        assert (result.code, result.status_code) == ("missing_source", 404)
    with SessionLocal() as session:
        rows = session.scalars(select(BenchmarkJobIdempotency).where(
            BenchmarkJobIdempotency.owner_subject == owner,
        )).all()
        assert len(rows) == 1
        assert rows[0].outcome == "failed"
        assert rows[0].job_id is None
    with pytest.raises(lifecycle.BenchmarkLifecycleFailure) as replay:
        await lifecycle.submit_job(**arguments)
    assert replay.value.code == "missing_source"
    assert source_calls == 1


def test_owner_scoped_api_pagination_and_nonleaking_delete(monkeypatch):
    monkeypatch.setenv("BENCHMARK_API_ENABLED", "true")
    owner = f"api-owner-{uuid4()}"
    other_owner = f"api-other-{uuid4()}"
    with SessionLocal() as session:
        first_id = _create_job(session, owner=owner, cells=2).id
        second_id = _create_job(session, owner=owner, cells=2).id
        other_id = _create_job(session, owner=other_owner, cells=1).id
        session.commit()
    app = FastAPI()
    for dependency in (require_benchmark_read, require_benchmark_cancel, require_benchmark_delete):
        app.dependency_overrides[dependency] = lambda: {"sub": owner}
    app.include_router(router)
    with TestClient(app) as client:
        page = client.get("/api/v1/benchmarks/jobs", params={"limit": 1}).json()
        assert len(page["items"]) == 1
        assert "resolved_plan" not in page["items"][0]
        cursor = page["next_cursor"]
        next_page = client.get("/api/v1/benchmarks/jobs", params={
            "limit": 1, "cursor_created_at": cursor["created_at"],
            "cursor_job_id": cursor["job_id"],
        }).json()
        assert {page["items"][0]["id"], next_page["items"][0]["id"]} == {
            str(first_id), str(second_id),
        }
        path = f"/api/v1/benchmarks/jobs/{first_id}"
        assert client.get(path).json()["summary"]["owner_subject"] == owner
        cells = client.get(path + "/cells", params={"limit": 1}).json()
        assert len(cells["items"]) == 1
        assert "generated_envelope" not in cells["items"][0]
        cursor = cells["next_cursor"]
        later_cells = client.get(path + "/cells", params={
            "cursor_position": cursor["position"], "cursor_cell_id": cursor["cell_id"],
        }).json()
        assert len(later_cells["items"]) == 1
        assert later_cells["items"][0]["id"] != cells["items"][0]["id"]
        cell_id = cells["items"][0]["id"]
        assert client.get(path + f"/cells/{cell_id}").status_code == 200
        assert client.get(f"/api/v1/benchmarks/jobs/{second_id}/cells/{cell_id}").status_code == 404
        assert client.get(f"/api/v1/benchmarks/jobs/{other_id}").status_code == 404
        assert client.get(f"/api/v1/benchmarks/jobs/{other_id}/cells").status_code == 404
        assert client.delete(f"/api/v1/benchmarks/jobs/{other_id}").status_code == 204
        assert client.delete(f"/api/v1/benchmarks/jobs/{uuid4()}").status_code == 204
        assert client.delete(path).status_code == 409
        cancel = client.post(path + "/cancel")
        assert cancel.status_code == 200
        assert client.post(path + "/cancel").json() == cancel.json()
        assert client.delete(path).status_code == 204
        assert client.delete(path).status_code == 204
        assert client.get(path).status_code == 404
        assert client.post(f"/api/v1/benchmarks/jobs/{other_id}/cancel").status_code == 404
        assert client.get(path + "/cells", params={"cursor_position": 1}).status_code == 422
        assert client.get("/api/v1/benchmarks/jobs", params={"cursor_job_id": str(first_id)}).status_code == 422
        with SessionLocal() as session:
            lifecycle.BenchmarkRepository(session).append_event(
                job_id=second_id, event_type="document_preparation.started",
                payload={"synthetic_receipt": True},
            )
            session.commit()
        prepared_path = f"/api/v1/benchmarks/jobs/{second_id}"
        assert client.post(prepared_path + "/cancel").status_code == 200
        deletion = client.delete(prepared_path)
        assert deletion.status_code == 409
        assert deletion.json()["detail"]["code"] == "lifecycle_conflict"
        assert client.get(prepared_path).status_code == 200


def test_event_replay_detects_pruned_holes_and_retains_preparation(monkeypatch):
    monkeypatch.setenv("BENCHMARK_API_ENABLED", "true")
    monkeypatch.setenv("BENCHMARK_EVENT_RETENTION_COUNT", "2")
    owner = f"event-owner-{uuid4()}"
    with SessionLocal() as session:
        job_id = _create_job(session, owner=owner, cells=1).id
        repository = lifecycle.BenchmarkRepository(session)
        for event_type in (
            "document_preparation.started", "ordinary.one", "ordinary.two",
            "document_preparation.completed", "ordinary.three",
        ):
            repository.append_event(job_id=job_id, event_type=event_type, payload={})
        repository.request_cancellation(
            job_id=job_id, owner_subject=owner, requested_at=datetime.now(timezone.utc),
        )
        session.commit()
    # New connections simulate restart/reconnect, not a process-memory replay.
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as expired:
        read_event_batch(job_id, owner, 0)
    assert expired.value.status_code == 410
    error_detail = cast(dict[str, Any], expired.value.detail)
    assert error_detail["resume_after"] == f"{job_id}:5"
    with SessionLocal() as session:
        rows = lifecycle.BenchmarkRepository(session).replay_events(
            job_id=job_id, owner_subject=owner,
        )
        assert [row.sequence for row in rows] == [1, 3, 4, 5]
        assert rows[0].event_type == "document_preparation.started"
        assert rows[2].event_type == "document_preparation.completed"
    app = FastAPI()
    app.dependency_overrides[require_benchmark_read] = lambda: {"sub": owner}
    app.include_router(router)
    path = f"/api/v1/benchmarks/jobs/{job_id}/events"
    with TestClient(app) as client:
        assert client.get(path).status_code == 410
        assert owner not in _connections
        result = client.get(path, headers={"Last-Event-ID": f"{job_id}:4"})
        assert result.status_code == 200
        assert "text/event-stream" in result.headers["content-type"]
        assert f"id: {job_id}:5" in result.text
        assert "ordinary.three" in result.text
        assert "event: job.status" in result.text
        assert '"status":"cancelled"' in result.text
        assert owner not in _connections
        repeated = client.get(path, headers={"Last-Event-ID": f"{job_id}:5"})
        assert "benchmark.event" not in repeated.text
        assert "job.status" in repeated.text
        assert client.get(path, headers={"Last-Event-ID": f"{job_id}:6"}).status_code == 409
        assert client.get(path, headers={"Last-Event-ID": f"{uuid4()}:5"}).status_code == 422
        app.dependency_overrides[require_benchmark_read] = lambda: {"sub": "other-owner"}
        assert client.get(path).status_code == 404



def test_invocation_api_pages_complete_telemetry_and_preserves_unknowns(monkeypatch):
    monkeypatch.setenv("BENCHMARK_API_ENABLED", "true")
    monkeypatch.setenv("BENCHMARK_MAX_PAGE_SIZE", "1")
    owner = f"telemetry-owner-{uuid4()}"
    with SessionLocal() as session:
        job_id = _create_job(session, owner=owner, cells=1).id
        cell_id = session.scalar(select(BenchmarkCell.id).where(BenchmarkCell.job_id == job_id))
        cell = session.get(BenchmarkCell, cell_id)
        cell.status = BenchmarkCellStatus.RUNNING
        cell.started_at = datetime.now(timezone.utc)
        cell.lease_owner = uuid4()
        cell.lease_heartbeat_at = cell.started_at
        cell.lease_expires_at = cell.started_at + timedelta(minutes=5)
        cell.attempt_count = 1
        session.flush()
        for ordinal in range(2):
            session.add(BenchmarkInvocation(
                id=uuid4(), cell_id=cell_id, ordinal=ordinal, attempt=1,
                route_slot="supervisor", request_digest="sha256:" + "a" * 64,
                response_digest="sha256:" + "b" * 64,
                sequence=ordinal + 1, status=BenchmarkInvocationStatus.SUCCEEDED,
                started_at=datetime.now(timezone.utc), completed_at=datetime.now(timezone.utc),
                requested_provider="synthetic", requested_model="requested-model",
                actual_provider="synthetic", actual_model="actual-model",
                input_tokens=None if ordinal == 0 else 0,
                output_tokens=None if ordinal == 0 else 0,
                total_tokens=None if ordinal == 0 else 0,
                billed_amount=None if ordinal == 0 else Decimal("0"),
                billed_unit=None if ordinal == 0 else "USD",
                billed_source=None if ordinal == 0 else "synthetic-measurement",
            ))
        session.commit()
    app = FastAPI()
    app.dependency_overrides[require_benchmark_read] = lambda: {"sub": owner}
    app.include_router(router)
    path = f"/api/v1/benchmarks/jobs/{job_id}/cells/{cell_id}/invocations"
    with TestClient(app) as client:
        first = client.get(path, params={"limit": 100}).json()
        assert len(first["items"]) == 1
        assert first["next_after_ordinal"] == 0
        assert first["items"][0]["total_tokens"] is None
        assert first["items"][0]["billed_amount"] is None
        assert first["items"][0]["actual_model"] == "actual-model"
        second = client.get(path, params={"after_ordinal": first["next_after_ordinal"]}).json()
        assert len(second["items"]) == 1
        assert second["items"][0]["ordinal"] == 1
        assert second["items"][0]["total_tokens"] == 0
        assert Decimal(second["items"][0]["billed_amount"]) == Decimal("0")
        assert second["next_after_ordinal"] is None
        assert client.get(path, params={"after_ordinal": 1}).json()["items"] == []
        app.dependency_overrides[require_benchmark_read] = lambda: {"sub": "other-owner"}
        assert client.get(path).status_code == 404


def test_rerun_reuses_frozen_inputs_and_context_and_replays_without_source_calls(monkeypatch):
    monkeypatch.setenv("BENCHMARK_API_ENABLED", "true")
    monkeypatch.setenv("BENCHMARK_EXECUTION_ENABLED", "true")
    owner = f"rerun-owner-{uuid4()}"
    now = datetime.now(timezone.utc)
    lease = uuid4()
    with SessionLocal() as session:
        job = _create_job(session, owner=owner, cells=2)
        job_id = job.id
        curator = BenchmarkCuratorContext.model_validate_json(json.dumps(job.curator_context))
        job.status = BenchmarkJobStatus.RUNNING
        job.started_at = now
        job.lease_owner = lease
        job.lease_expires_at = now + timedelta(minutes=5)
        job.lease_heartbeat_at = now
        session.flush()
        repo = BenchmarkRepository(session)
        failed_ids = []
        for _ in range(2):
            cell = repo.claim_next_cell(
                job_id=job_id, lease_owner=lease, lease_expires_at=now + timedelta(minutes=5), now=now,
            )
            assert cell is not None
            failed_ids.append(cell.id)
            repo.finish_cell(
                cell_id=cell.id, lease_owner=lease, status=BenchmarkCellStatus.FAILED,
                completed_at=now, failure={"code": "synthetic_failure"}, now=now,
            )
        repo.complete_job(job_id=job_id, lease_owner=lease, completed_at=now, now=now)
        original_context = job.curator_context
        original_plan = job.resolved_plan
        snapshots = dict(session.execute(select(
            BenchmarkJobInputSnapshot.case_id, BenchmarkJobInputSnapshot.snapshot_id,
        ).where(BenchmarkJobInputSnapshot.job_id == job_id)).all())
        session.commit()

    def forbidden_source(*args, **kwargs):
        pytest.fail("Frozen rerun must not materialize sources")
    monkeypatch.setattr(lifecycle, "materialize_plan_inputs", forbidden_source)
    app = FastAPI()
    app.dependency_overrides[require_benchmark_run] = lambda: {"sub": owner}
    app.dependency_overrides[require_benchmark_curator] = lambda: curator.model_copy(
        update={"active_groups": (*curator.active_groups, "newly-granted")},
    )
    app.include_router(router)
    path = f"/api/v1/benchmarks/jobs/{job_id}/rerun"
    key = str(uuid4())
    with TestClient(app) as client:
        parent_locked = threading.Event()
        release_admission = threading.Event()
        original_reserve = BenchmarkRepository.reserve_idempotency

        def hold_admission(repository, **kwargs):
            if kwargs["owner_subject"] == owner:
                parent_locked.set()
                assert release_admission.wait(5)
            return original_reserve(repository, **kwargs)

        def delete_parent():
            with SessionLocal() as session:
                try:
                    BenchmarkRepository(session).delete_terminal_job(job_id=job_id, owner_subject=owner)
                    session.commit()
                    return "deleted"
                except ValueError:
                    return "retained"

        monkeypatch.setattr(BenchmarkRepository, "reserve_idempotency", hold_admission)
        with ThreadPoolExecutor(max_workers=2) as pool:
            submitting = pool.submit(client.post, path, json={"cell_ids": [str(failed_ids[0])]}, headers={"Idempotency-Key": key})
            assert parent_locked.wait(5)
            deleting = pool.submit(delete_parent)
            try:
                deadline = time.monotonic() + 3
                blocked = False
                while time.monotonic() < deadline and not blocked:
                    with SessionLocal() as session:
                        blocked = session.scalar(text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity
                            WHERE datname = current_database() AND pid != pg_backend_pid()
                            AND cardinality(pg_blocking_pids(pid)) > 0
                            AND query LIKE '%benchmark_jobs%' AND query LIKE '%FOR UPDATE%')
                        """))
                    if not blocked:
                        time.sleep(0.01)
                assert blocked, "Deletion did not wait for the rerun's parent lock"
            finally:
                release_admission.set()
            first = submitting.result(timeout=5)
            assert deleting.result(timeout=5) == "retained"
        monkeypatch.setattr(BenchmarkRepository, "reserve_idempotency", original_reserve)
        assert first.status_code == 202, first.text
        result = first.json()
        assert result["replayed"] is False
        assert first.headers["location"].endswith(result["job_id"])
        repeated = client.post(path, json={"cell_ids": [str(failed_ids[0])]}, headers={"Idempotency-Key": key})
        assert repeated.status_code == 202, repeated.text
        assert repeated.json() == {**result, "replayed": True}
        conflict = client.post(path, json={"cell_ids": [str(failed_ids[1])]}, headers={"Idempotency-Key": key})
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["detail"]["code"] == "idempotency_conflict"
        app.dependency_overrides[require_benchmark_curator] = lambda: curator.model_copy(update={"subject": "other"})
        assert client.post(path, json={}, headers={"Idempotency-Key": str(uuid4())}).status_code == 403
    with SessionLocal() as session:
        children = session.scalars(select(BenchmarkJob).where(BenchmarkJob.rerun_of_job_id == job_id)).all()
        assert len(children) == 1
        child = children[0]
        assert str(child.id) == result["job_id"]
        assert child.curator_context == original_context
        assert child.total_cells == 1
        links = session.execute(select(
            BenchmarkJobInputSnapshot.case_id, BenchmarkJobInputSnapshot.snapshot_id,
        ).where(BenchmarkJobInputSnapshot.job_id == child.id)).all()
        assert len(links) == 1
        assert snapshots[links[0].case_id] == links[0].snapshot_id
        original = session.get(BenchmarkJob, job_id)
        assert original.curator_context == original_context
        assert original.resolved_plan == original_plan


def test_rerun_limit_applies_to_new_resolved_work_but_not_accepted_replay(monkeypatch):
    for name in ("API", "EXECUTION"):
        monkeypatch.setenv(f"BENCHMARK_{name}_ENABLED", "true")
    owner = f"rerun-limit-{uuid4()}"
    lease = uuid4()
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        job = _create_job(session, owner=owner, cells=2)
        job_id = job.id
        curator = BenchmarkCuratorContext.model_validate_json(json.dumps(job.curator_context))
        job.status = BenchmarkJobStatus.RUNNING
        job.started_at = now
        job.lease_owner = lease
        job.lease_expires_at = now + timedelta(minutes=5)
        job.lease_heartbeat_at = now
        session.flush()
        repo = BenchmarkRepository(session)
        failed_ids = []
        for _ in range(2):
            cell = repo.claim_next_cell(job_id=job_id, lease_owner=lease,
                                       lease_expires_at=job.lease_expires_at, now=now)
            assert cell is not None
            failed_ids.append(str(cell.id))
            repo.finish_cell(cell_id=cell.id, lease_owner=lease,
                             status=BenchmarkCellStatus.FAILED, completed_at=now,
                             failure={"code": "synthetic_failure"}, now=now)
        repo.complete_job(job_id=job_id, lease_owner=lease, completed_at=now, now=now)
        session.commit()
    app = FastAPI()
    app.dependency_overrides[require_benchmark_run] = lambda: {"sub": owner}
    app.dependency_overrides[require_benchmark_curator] = lambda: curator
    app.include_router(router)
    path = f"/api/v1/benchmarks/jobs/{job_id}/rerun"
    headers = {"Idempotency-Key": str(uuid4())}
    with TestClient(app) as client:
        monkeypatch.setenv("BENCHMARK_MAX_CELLS", "2")
        accepted = client.post(path, json={"cell_ids": failed_ids}, headers=headers)
        assert accepted.status_code == 202, accepted.text
        monkeypatch.setenv("BENCHMARK_MAX_CELLS", "1")
        replay = client.post(path, json={"cell_ids": failed_ids}, headers=headers)
        assert replay.json() == {**accepted.json(), "replayed": True}
        for body in ({}, {"cell_ids": failed_ids}):
            failure_headers = {"Idempotency-Key": str(uuid4())}
            rejected = client.post(path, json=body, headers=failure_headers)
            assert rejected.status_code == 422, rejected.text
            monkeypatch.setenv("BENCHMARK_MAX_CELLS", "2")
            same_failure = client.post(path, json=body, headers=failure_headers)
            assert same_failure.status_code == 422
            assert same_failure.json() == rejected.json()
            monkeypatch.setenv("BENCHMARK_MAX_CELLS", "1")
    with SessionLocal() as session:
        children = session.scalars(select(BenchmarkJob).where(BenchmarkJob.rerun_of_job_id == job_id)).all()
        assert len(children) == 1


@pytest.mark.parametrize("same_key,revoked", [(True, False), (True, True), (False, False)])
def test_concurrent_api_submit_freezes_once_and_replay_needs_no_current_catalog(configured, monkeypatch, tmp_path, same_key, revoked):
    monkeypatch.setenv("BENCHMARK_API_ENABLED", "true")
    monkeypatch.setenv("BENCHMARK_EXECUTION_ENABLED", "true")
    owner = f"submit-owner-{uuid4()}"
    content = '{"paper":"synthetic experimentally relevant gene evidence"}'
    digest = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
    materializing = threading.Event()
    release_source = threading.Event()
    calls = []

    class Resolver:
        resolver_id = "synthetic_source"
        reference_schema = RootModel[str]
        delegated_authorization = DelegatedAuthorizationCapability.UNSUPPORTED

        async def materialize(self, reference, validated_reference, *, max_bytes, request_context):
            calls.append(validated_reference)
            materializing.set()
            assert await asyncio.to_thread(release_source.wait, 5)
            return MaterializedBenchmarkInput(
                resolver=self.resolver_id, reference=reference.reference, version=reference.version,
                digest=digest, content=content,
                metadata=BenchmarkSourceMetadata(content_type="application/json", content_bytes=len(content.encode())),
                provenance=BenchmarkSourceProvenance(
                    resolver=self.resolver_id, reference=reference.reference, version=reference.version, digest=digest,
                ),
            )

    catalog = BenchmarkInputResolverCatalog([Resolver()], timeout_seconds=10, max_input_bytes=10000)
    monkeypatch.setattr(benchmark_jobs, "input_resolver_catalog", lambda request: catalog)
    snapshot_barrier = threading.Barrier(2)
    class Store(FileSystemBenchmarkSnapshotStore):
        def put(self, *, digest, content):
            if not same_key:
                # Both transactions already observed no snapshot row. Without
                # conflict-safe insert, one now violates the unique identity.
                snapshot_barrier.wait(timeout=5)
            return super().put(digest=digest, content=content)
    monkeypatch.setattr(lifecycle, "configured_benchmark_snapshot_store", lambda: Store(tmp_path))
    suite_value = _payload()
    suite_value["cases"][0]["target"] = {"kind": "agent", "id": "extractor"}
    suite_value["cases"][0]["user_query"] = "Extract the genes relevant to this paper's experiments."
    suite_value["cases"][0]["input"] = {
        "resolver": "synthetic_source", "reference": "paper-1", "version": "v1", "digest": digest,
    }
    suite_value["configurations"] = [{"configuration_id": "defaults", "routes": {}}]
    suite_value["repetitions"] = 1
    with SessionLocal() as session:
        routes = runtime_catalog.build_curator_route_catalog(session, configured.curator)
    plan = resolve_suite(validate_suite(suite_value), routes, max_cases=100, max_configurations=100, max_repetitions=100, max_cells=10000)
    body = {"suite": suite_value, "plan": plan.model_dump(mode="json")}
    app = FastAPI()
    app.dependency_overrides[require_benchmark_run] = lambda: {"sub": owner, "client_id": "portal"}
    app.dependency_overrides[require_benchmark_curator] = lambda: configured.curator
    app.include_router(router)
    path = "/api/v1/benchmarks/jobs"
    headers = {"Idempotency-Key": str(uuid4())}
    with TestClient(app) as client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(client.post, path, json=body, headers=headers)
            assert materializing.wait(5)
            second = pool.submit(client.post, path, json=body, headers=headers if same_key else {"Idempotency-Key": str(uuid4())})
            try:
                deadline = time.monotonic() + 3
                blocked = False
                while same_key and time.monotonic() < deadline and not blocked:
                    with SessionLocal() as session:
                        blocked = session.scalar(text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity
                            WHERE datname = current_database() AND pid != pg_backend_pid()
                            AND cardinality(pg_blocking_pids(pid)) > 0
                            AND query LIKE '%INSERT INTO benchmark_job_idempotency%')
                        """))
                    if not blocked:
                        time.sleep(0.01)
                assert blocked or not same_key
            finally:
                release_source.set()
            responses = [first.result(timeout=5), second.result(timeout=5)]
        assert [response.status_code for response in responses] == [202, 202], [response.text for response in responses]
        results = [response.json() for response in responses]
        assert (results[0]["job_id"] == results[1]["job_id"]) == same_key
        assert sorted(result["replayed"] for result in results) == [False, same_key]
        assert calls == ["paper-1"] * (1 if same_key else 2)

        def unavailable(*args, **kwargs):
            pytest.fail("Accepted replay must not reconstruct catalogs or storage")
        monkeypatch.setattr(runtime_catalog, "build_curator_route_catalog", unavailable)
        monkeypatch.setattr(benchmark_jobs, "input_resolver_catalog", unavailable)
        monkeypatch.setattr(lifecycle, "configured_benchmark_snapshot_store", unavailable)
        replay = client.post(path, json=body, headers=headers)
        assert replay.status_code == 202, replay.text
        assert replay.json() == {"job_id": results[0]["job_id"], "replayed": True}
        app.dependency_overrides[require_benchmark_curator] = lambda: configured.curator.model_copy(update={"subject": "different"})
        assert client.post(path, json=body, headers=headers).status_code == 409
    with SessionLocal() as session:
        jobs = session.scalars(select(BenchmarkJob).where(BenchmarkJob.owner_subject == owner)).all()
        assert len(jobs) == (1 if same_key else 2)
        assert jobs[0].status == BenchmarkJobStatus.QUEUED
        assert jobs[0].curator_context == configured.curator.model_dump(mode="json")
        assert len(session.scalars(select(BenchmarkJobInputSnapshot).where(BenchmarkJobInputSnapshot.job_id == jobs[0].id)).all()) == 1
        job_id = jobs[0].id

        if not same_key:
            snapshots = session.scalars(select(BenchmarkJobInputSnapshot.snapshot_id).where(
                BenchmarkJobInputSnapshot.job_id.in_([job.id for job in jobs]),
            )).all()
            assert len(snapshots) == 2 and len(set(snapshots)) == 1
            assert all(job.status == BenchmarkJobStatus.QUEUED for job in jobs)
            return

    # Preserve other tests' retained jobs. Only constrain this worker's job
    # selection; the real repository still owns claim/lease/cell transactions.
    def worker_session():
        session = SessionLocal()
        @event.listens_for(session, "do_orm_execute")
        def restrict_job(execute_state):
            if execute_state.is_select:
                execute_state.statement = execute_state.statement.options(
                    with_loader_criteria(BenchmarkJob, BenchmarkJob.id == job_id),
                )
        return session

    prepared_document = uuid4()
    provider_inputs = []
    authorization_checks = []
    async def prepare(**kwargs):
        assert kwargs["job_id"] == job_id
        return SimpleNamespace(document_id=prepared_document), configured.curator
    async def authorize(context, **kwargs):
        authorization_checks.append(context)
        if revoked:
            raise PermissionError("Synthetic revoked curator")
        return context
    async def execute(cell, runtime_input, run_id):
        provider_inputs.append(runtime_input)
        return await _emit_fake_provider_call()
    monkeypatch.setattr(worker_module, "prepare_job_document", prepare)
    monkeypatch.setattr(worker_module, "authorize_benchmark_curator", authorize)
    monkeypatch.setenv("BENCHMARK_WORKER_ENABLED", "true")
    worker = worker_module.BenchmarkWorker(session_factory=worker_session, agent_executor=execute)
    # Expiry recovery is separately covered; do not recover unrelated retained
    # fixture work while proving this admission-to-execution boundary.
    monkeypatch.setattr(worker, "recover_expired", lambda: ())
    assert asyncio.run(worker.run_once()) is True
    assert authorization_checks == [configured.curator]
    assert len(provider_inputs) == int(not revoked)
    if not revoked:
        assert provider_inputs[0]["user_id"] == configured.curator.subject
        assert provider_inputs[0]["active_groups"] == list(configured.curator.active_groups)
        assert provider_inputs[0]["document_id"] == str(prepared_document)
        assert provider_inputs[0]["messages"] == [{"role": "user", "content": suite_value["cases"][0]["user_query"]}]
        assert content not in json.dumps(provider_inputs[0])
    app.dependency_overrides[require_benchmark_read] = lambda: {"sub": owner}
    with TestClient(app) as client:
        job_path = f"{path}/{job_id}"
        detail = client.get(job_path).json()
        assert detail["summary"]["status"] == ("completed_with_failures" if revoked else "completed")
        cells = client.get(job_path + "/cells").json()["items"]
        assert len(cells) == 1
        cell_path = job_path + f"/cells/{cells[0]['id']}"
        cell_detail = client.get(cell_path).json()
        assert cell_detail["summary"]["status"] == ("failed" if revoked else "succeeded")
        invocations = client.get(cell_path + "/invocations").json()["items"]
        assert len(invocations) == int(not revoked)
        if not revoked:
            assert cell_detail["generated_envelope"] == {"records": [{"ok": True}]}
            assert invocations[0]["total_tokens"] == 5
            assert invocations[0]["billed_amount"] is None
        events = client.get(job_path + "/events")
        assert events.status_code == 200
        assert "job.status" in events.text
        assert content not in events.text
