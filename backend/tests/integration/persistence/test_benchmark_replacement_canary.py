"""One opt-in, Docker-owned synthetic v2 lifecycle journey.

Run scripts/testing/benchmark-replacement-canary.sh. Never use a shared database:
prepared documents intentionally cannot be deleted by the SQL lifecycle API.
"""

import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
from sqlalchemy import func, select, update
from sqlalchemy.engine import make_url
from src.lib.benchmark_cli.client import BenchmarkClient, ClientError, Credentials
from src.lib.benchmark_cli.commands import build_parser, execute
from src.lib.benchmarks.persistence import BenchmarkRepository
from src.lib.benchmarks.worker import BenchmarkWorker
from src.models.sql.benchmark import (
    BenchmarkCell,
    BenchmarkInvocation,
    BenchmarkJob,
)
from src.models.sql.database import SessionLocal
from tests.integration.persistence.benchmark_canary_handoff import check_handoff
from tests.integration.persistence.benchmark_canary_support import (
    CLIENT,
    CONTENT,
    DIGEST,
    CanaryIdentity,
    CanaryProvider,
    canary_server,
)

pytestmark = pytest.mark.skipif(
    os.getenv("BENCHMARK_REPLACEMENT_CANARY") != "true",
    reason="Opt-in disposable Docker canary; use benchmark-replacement-canary.sh",
)


@pytest.fixture
def isolated_database():
    url = make_url(os.environ["DATABASE_URL"])
    assert url.host == "postgres-test" and url.database == "benchmark_replacement_canary"
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")


def cli(client, *arguments):
    return execute(build_parser().parse_args(arguments), client)


def body_file(tmp_path, name, value):
    path = tmp_path / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return str(path)


def test_replacement_journey(isolated_database, monkeypatch, tmp_path, weaviate_connection, setup_collections):
    identity = CanaryIdentity()
    identity.install(monkeypatch, tmp_path, weaviate_connection)
    provider = CanaryProvider()
    provider.install(monkeypatch)
    credentials = Credentials(identity.token(), identity.token(human=True))
    with canary_server() as origin, BenchmarkClient(origin, credentials) as client:
        # Signed verifier failures must precede planning, storage and execution.
        prefix, signature = credentials.access.rsplit(".", 1)
        bad_signature = prefix + "." + ("A" if signature[0] != "A" else "B") + signature[1:]
        for token in (identity.token(exp=1), identity.token(aud="wrong-audience"), bad_signature, "not-a-jwt"):
            response = client.http.get(origin + "/api/v1/benchmarks/jobs", headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 401
        response = client.http.get(origin + "/api/v1/benchmarks/jobs", headers={
            "Authorization": "Bearer " + identity.token(scope="benchmark:run"),
        })
        assert response.status_code == 403
        assert not provider.calls

        uploaded = client.http.post(origin + "/api/v1/benchmarks/sources/snapshots", content=CONTENT, headers={
            **credentials.headers(), "Content-Type": "text/plain", "X-Benchmark-Content-Digest": DIGEST,
        })
        assert uploaded.status_code == 200, uploaded.text
        receipt = uploaded.json()
        downloaded = client.http.get(origin + f"/api/v1/benchmarks/sources/snapshots/{receipt['snapshot_id']}/content", headers=credentials.headers())
        assert downloaded.content == CONTENT
        assert downloaded.headers["x-benchmark-content-digest"] == DIGEST
        source = {"resolver": "frozen_snapshot", "reference": receipt["snapshot_id"],
                  "version": receipt["source_version"], "digest": receipt["digest"]}
        suite = {
            "schema_version": 2, "suite_id": "synthetic-replacement-canary",
            "cases": [{"case_id": "paper", "target": {"kind": "agent", "id": "extractor"},
                       "input": source, "user_query": "Return synthetic test evidence; do not infer biology."}],
            "configurations": [
                {"configuration_id": "arm-a", "routes": {"agent:extractor": {
                    "provider": "provider-a", "model": "model-a", "reasoning_effort": "high"}}},
                {"configuration_id": "arm-b", "routes": {"agent:extractor": {
                    "provider": "provider-a", "model": "model-b"}}},
            ], "repetitions": 1,
        }
        catalog = cli(client, "catalog", "targets")
        preview = cli(client, "validate", "--request", body_file(tmp_path, "preview.json", {
            "catalog_digest": catalog["catalog_digest"], "suite": suite,
        }))
        assert preview["cell_count"] == 2 and not provider.calls
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(BenchmarkJob)) == 0
        request = body_file(tmp_path, "submit.json", {"suite": suite, "plan": preview["plan"]})
        args = ("submit", "--request", request, "--idempotency-key", "canary-initial")
        monkeypatch.setenv("BENCHMARK_EXECUTION_ENABLED", "false")
        with pytest.raises(ClientError):
            cli(client, *args)
        monkeypatch.setenv("BENCHMARK_EXECUTION_ENABLED", "true")
        admission = cli(client, *args)
        job_id = admission["job_id"]
        assert cli(client, *args) == {"job_id": job_id, "replayed": True}
        assert not provider.calls
        # Another approved machine can authenticate but cannot inherit this owner's data.
        other_headers = {"Authorization": "Bearer " + identity.token(client="canary-other")}
        assert client.http.get(origin + "/api/v1/benchmarks/jobs", headers=other_headers).status_code == 200
        for path in (
            f"/api/v1/benchmarks/jobs/{job_id}",
            f"/api/v1/benchmarks/sources/snapshots/{receipt['snapshot_id']}/content",
        ):
            assert client.http.get(origin + path, headers=other_headers).status_code == 404
        assert client.http.post(origin + f"/api/v1/benchmarks/jobs/{job_id}/cancel",
                                headers=other_headers).status_code == 404
        assert cli(client, "get", job_id)["summary"]["status"] == "queued"
        # Revoked initiating-human authority must fail even on accepted replay.
        identity.current = False
        with pytest.raises(ClientError):
            cli(client, *args)
        identity.current = True

        # See progress while nonterminal, disconnect, then keep observing the
        # same accepted job. Observation/disconnection never submits or cancels.
        stream = client.events(job_id)
        first_event, first_cursor, first_status = next(stream)
        assert first_event == "job.status" and first_cursor is None
        assert first_status["id"] == job_id and first_status["status"] == "queued"
        stream.close()
        assert cli(client, "get", job_id)["summary"]["status"] == "queued"
        monkeypatch.setenv("BENCHMARK_WORKER_ENABLED", "false")
        assert asyncio.run(BenchmarkWorker().run_once()) is False
        monkeypatch.setenv("BENCHMARK_WORKER_ENABLED", "true")

        provider.fail_once = True
        monkeypatch.setenv("BENCHMARK_EVENT_RETENTION_COUNT", "2")
        assert asyncio.run(BenchmarkWorker().run_once())
        status = cli(client, "get", job_id)
        assert status["summary"]["status"] == "completed_with_failures", status
        cells = cli(client, "cells", job_id)["items"]
        assert [cell["status"] for cell in cells] == ["failed", "succeeded"]
        assert {call[2] for call in provider.calls} == {"model-a", "model-b"}
        assert len({call[3] for call in provider.calls}) == 1
        assert identity.lookups >= 4
        success = cells[1]["id"]
        artifact = client.http.get(origin + f"/api/v1/benchmarks/jobs/{job_id}/cells/{success}/result", headers=credentials.headers())
        assert artifact.status_code == 200, artifact.text
        assert artifact.headers["x-benchmark-result-digest"] == "sha256:" + hashlib.sha256(artifact.content).hexdigest()
        assert b"-0.0" in artifact.content
        with SessionLocal() as db:
            rows = list(db.scalars(select(BenchmarkInvocation).join(BenchmarkCell).where(BenchmarkCell.job_id == UUID(job_id))))
            assert len(rows) == 2 and all(row.billed_amount is None for row in rows)
            raw_job = db.get(BenchmarkJob, UUID(job_id))
            assert raw_job.owner_subject == f"service:{CLIENT}"
            assert raw_job.curator_context["subject"] == identity.subject
            durable = json.dumps(raw_job.curator_context)
            assert credentials.curator is not None
            assert credentials.access not in durable and credentials.curator not in durable

        # Reconnect reads durable events and never submits. Force retention via
        # the production event retention setting, preserving prep checkpoints.
        with pytest.raises(ClientError) as expired:
            list(client.events(job_id, f"{job_id}:0"))
        assert expired.value.resume_after
        assert cli(client, "get", job_id)["summary"]["status"] == status["summary"]["status"]
        assert list(client.events(job_id, expired.value.resume_after))
        assert len(provider.calls) == 2

        rerun_args = ("rerun", job_id, "--cell-id", cells[0]["id"], "--idempotency-key", "canary-child")
        child = cli(client, *rerun_args)["job_id"]
        assert cli(client, *rerun_args)["replayed"] is True
        assert asyncio.run(BenchmarkWorker().run_once())
        assert cli(client, "get", child)["summary"]["status"] == "completed"
        assert len(provider.calls) == 3

        # Successful deletion is only valid before external preparation starts.
        queued = cli(client, "submit", "--request", request, "--idempotency-key", "canary-cancel")["job_id"]
        assert cli(client, "cancel", queued)["summary"]["status"] == "cancelled"
        cli(client, "delete", queued, "--confirm", queued)
        cli(client, "delete", queued, "--confirm", queued)
        with pytest.raises(ClientError):
            cli(client, "delete", job_id, "--confirm", job_id)

        # Actual durable started invocation + expired lease, followed by a new
        # worker. The uncertain cell must not be automatically dispatched.
        interrupted = cli(client, "submit", "--request", request, "--idempotency-key", "canary-interrupted")["job_id"]
        now = datetime.now(timezone.utc)
        lease = uuid4()
        with SessionLocal() as db:
            repository = BenchmarkRepository(db)
            claimed = repository.claim_next_job(lease_owner=lease, lease_expires_at=now + timedelta(minutes=1))
            assert claimed is not None
            assert str(claimed.id) == interrupted
            cell = repository.claim_next_cell(job_id=claimed.id, lease_owner=lease, lease_expires_at=now + timedelta(minutes=1))
            assert cell is not None
            repository.append_invocation(cell_id=cell.id, lease_owner=lease, ordinal=0, attempt=1,
                route_slot="agent:extractor", request_digest=DIGEST, requested_provider="provider-a",
                requested_model="model-a", reasoning_effort="high", sequence=1, started_at=now)
            db.execute(update(BenchmarkCell).where(BenchmarkCell.id == cell.id).values(lease_expires_at=now - timedelta(seconds=1)))
            db.execute(update(BenchmarkJob).where(BenchmarkJob.id == claimed.id).values(lease_expires_at=now - timedelta(seconds=1)))
            db.commit()
        before = len(provider.calls)
        assert asyncio.run(BenchmarkWorker().run_once())
        recovered = cli(client, "cells", interrupted)["items"]
        interrupted_cell = cli(client, "get", interrupted, "--cell-id", recovered[0]["id"])
        assert interrupted_cell["failure"]["category"] == "interrupted_uncertain"
        assert interrupted_cell["attempt_count"] == 1
        assert len(provider.calls) == before + 1

        cooperative = cli(client, "submit", "--request", request, "--idempotency-key", "canary-cooperative")["job_id"]
        before = len(provider.calls)
        provider.on_completed_call = lambda: cli(client, "cancel", cooperative)
        try:
            assert asyncio.run(BenchmarkWorker().run_once())
        finally:
            provider.on_completed_call = None
        assert cli(client, "get", cooperative)["summary"]["status"] == "cancelled"
        assert len(provider.calls) == before + 1
        for cancelled in cli(client, "cells", cooperative)["items"]:
            assert cli(client, "get", cooperative, "--cell-id", cancelled["id"])["generated_envelope"] is None

        # A distinct owner's durable fixture is invisible to this verified
        # service. Nonleaking delete must not remove that owner's row.
        from tests.integration.persistence.test_benchmark_repository import _create_job
        with SessionLocal() as db:
            foreign = _create_job(db, owner="service:foreign-canary", cells=1)
            foreign_id = str(foreign.id)
            BenchmarkRepository(db).request_cancellation(job_id=foreign.id, owner_subject=foreign.owner_subject,
                                                         requested_at=datetime.now(timezone.utc))
            db.commit()
        foreign_read = client.http.get(origin + f"/api/v1/benchmarks/jobs/{foreign_id}", headers=credentials.headers())
        assert foreign_read.status_code == 404
        cli(client, "delete", foreign_id, "--confirm", foreign_id)
        with SessionLocal() as db:
            assert db.get(BenchmarkJob, UUID(foreign_id)) is not None

        flow_suite = {
            **suite, "suite_id": "synthetic-flow-canary",
            "cases": [{**suite["cases"][0], "target": {"kind": "flow", "id": "Extraction Flow"}}],
            "configurations": [{"configuration_id": "independent-roles", "routes": {
                "supervisor": {"provider": "provider-a", "model": "model-a", "reasoning_effort": "high"},
                "agent:extractor": {"provider": "provider-a", "model": "model-b"},
                "validator:semantic-check": {"provider": "provider-a", "model": "model-a", "reasoning_effort": "low"},
            }}],
        }
        flow_preview = cli(client, "validate", "--request", body_file(tmp_path, "flow-preview.json", {
            "catalog_digest": catalog["catalog_digest"], "suite": flow_suite,
        }))
        flow_request = body_file(tmp_path, "flow-submit.json", {"suite": flow_suite, "plan": flow_preview["plan"]})
        flow_job = cli(client, "submit", "--request", flow_request, "--idempotency-key", "canary-flow")["job_id"]
        assert asyncio.run(BenchmarkWorker().run_once())
        flow_status = cli(client, "get", flow_job)
        assert flow_status["summary"]["status"] == "completed", flow_status
        flow_cell = cli(client, "cells", flow_job)["items"][0]["id"]
        flow_result = cli(client, "get", flow_job, "--cell-id", flow_cell)
        assert flow_result["generated_envelope"]["schema_version"] == "benchmark-flow-extractions/v1"
        assert len(flow_result["generated_envelope"]["envelopes"]) == 1
        with SessionLocal() as db:
            invocations = list(db.scalars(select(BenchmarkInvocation).where(BenchmarkInvocation.cell_id == UUID(flow_cell))))
            assert {row.route_slot for row in invocations} == set(flow_suite["configurations"][0]["routes"])
            for row in invocations:
                expected = flow_suite["configurations"][0]["routes"][row.route_slot]
                assert row.requested_model == row.actual_model == expected["model"]
                assert row.reasoning_effort == expected.get("reasoning_effort")
        handoff = check_handoff(monkeypatch, client, origin, identity, provider.flow_records[0])
        print(json.dumps({"canary": "passed", "signed_auth": True, "real_preparation": True,
                          "provider_calls": len(provider.calls), "uncertain_replayed": False, "handoff": handoff}))
