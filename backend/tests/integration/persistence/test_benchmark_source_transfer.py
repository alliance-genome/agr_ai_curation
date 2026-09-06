"""Real SQL/blob upload, download and frozen reuse without source/provider work."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import threading
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, func, select

from src.api import benchmark_sources as api
from src.api.benchmark_auth import require_benchmark_source_read
from src.lib.benchmarks.input_resolvers import BenchmarkSourceRequestContext
from src.lib.benchmarks.models import BenchmarkInputReference
from src.lib.benchmarks.snapshots import FileSystemBenchmarkSnapshotStore, FrozenBenchmarkSnapshotResolver
from src.models.sql.benchmark import BenchmarkInputSnapshot, BenchmarkJob
from src.models.sql.database import SessionLocal


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")


@pytest.fixture
def transfer(tmp_path, monkeypatch):
    principal = {"sub": f"service:transfer-{uuid4()}", "client_id": "synthetic-transfer"}
    monkeypatch.setattr(api, "get_benchmark_enabled", lambda: True)
    monkeypatch.setattr(api, "_catalog", lambda *_: pytest.fail("No original source lookup"))
    monkeypatch.setenv("BENCHMARK_SNAPSHOT_STORE_BACKEND", "filesystem")
    monkeypatch.setenv("BENCHMARK_SNAPSHOT_STORE_PATH", str(tmp_path))
    application = FastAPI()
    application.dependency_overrides[require_benchmark_source_read] = lambda: principal
    application.include_router(api.router)
    with TestClient(application) as client:
        yield client, principal, tmp_path
    with SessionLocal() as db:
        db.execute(delete(BenchmarkInputSnapshot).where(
            BenchmarkInputSnapshot.owner_subject == principal["sub"],
        ))
        db.commit()


def upload(client, content, media="text/plain"):
    return client.post("/api/v1/benchmarks/sources/snapshots", content=content, headers={
        "Content-Type": media,
        "X-Benchmark-Content-Digest": "sha256:" + hashlib.sha256(content).hexdigest(),
    })


def test_exact_round_trip_frozen_reuse_and_other_owner_denial(transfer, monkeypatch):
    client, principal, root = transfer
    content = "# Results\r\nα and β are experimentally relevant.\n".encode()
    with SessionLocal() as db:
        jobs_before = db.scalar(select(func.count()).select_from(BenchmarkJob))
    response = upload(client, content, "text/markdown")
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert upload(client, content, "text/markdown").json() == receipt
    path = f"/api/v1/benchmarks/sources/snapshots/{receipt['snapshot_id']}/content"
    downloaded = client.get(path)
    assert downloaded.status_code == 200 and downloaded.content == content
    assert downloaded.headers["x-benchmark-content-digest"] == receipt["digest"]
    assert downloaded.headers["content-length"] == str(len(content))
    assert downloaded.headers["cache-control"] == "no-store"
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert receipt["blob_reference"] not in str(downloaded.headers)
    assert str(root) not in str(downloaded.headers)
    frozen = BenchmarkInputReference(
        resolver="frozen_snapshot", reference=receipt["snapshot_id"],
        version=receipt["source_version"], digest=receipt["digest"],
    )
    reused = asyncio.run(FrozenBenchmarkSnapshotResolver().materialize(
        frozen, frozen.reference, max_bytes=len(content),
        request_context=BenchmarkSourceRequestContext(principal_subject=principal["sub"]),
    ))
    assert reused.content.encode("utf-8") == content and reused.version == "1"
    assert reused.digest == receipt["digest"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(BenchmarkJob)) == jobs_before

    client.app.dependency_overrides[require_benchmark_source_read] = lambda: {
        "sub": f"service:other-{uuid4()}", "client_id": "other",
    }
    monkeypatch.setattr(api, "configured_benchmark_snapshot_store",
                        lambda: pytest.fail("Wrong owner must not reach blob storage"))
    assert client.get(path).status_code == 404


def test_preparation_freezes_original_provenance_and_downloads_exact_bytes(transfer, monkeypatch):
    from pydantic import RootModel
    from src.lib.benchmarks.input_resolvers import (
        BenchmarkInputResolverCatalog, DelegatedAuthorizationCapability, MaterializedBenchmarkInput,
    )

    client, principal, _ = transfer
    content = '[{"text":"Synthetic α observation\\r\\n"}]\r\n'
    digest = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
    calls = []

    class Resolver:
        resolver_id = "synthetic_preparable"
        reference_schema = RootModel[str]
        delegated_authorization = DelegatedAuthorizationCapability.REQUIRED

        async def prepare(self, reference, *, max_bytes, request_context):
            calls.append((reference, request_context.principal_subject))
            assert request_context.delegated_authorization is not None
            assert max_bytes >= len(content.encode())
            provenance = {"resolver": self.resolver_id, "reference": reference,
                          "version": "source-v3", "digest": digest}
            return MaterializedBenchmarkInput(
                **provenance, content=content, provenance=provenance,
                metadata={"content_type": "application/json", "content_bytes": len(content.encode())},
            )

        async def materialize(self, *args, **kwargs):
            pytest.fail("No duplicate download through materialize")

    catalog = BenchmarkInputResolverCatalog([Resolver()], timeout_seconds=1, max_input_bytes=4096)
    monkeypatch.setattr(api, "_catalog", lambda *_: catalog)
    response = client.post("/api/v1/benchmarks/sources/prepare",
                           json={"resolver": "synthetic_preparable", "reference": "artifact-1"},
                           headers={"X-Benchmark-Delegated-Source-Authorization": "Bearer synthetic"})
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["digest"] == digest and receipt["source_version"] == "source-v3"
    assert receipt["sanitized_provenance"] == {
        "resolver": "synthetic_preparable", "reference": "artifact-1",
        "version": "source-v3", "digest": digest,
    }
    path = f"/api/v1/benchmarks/sources/snapshots/{receipt['snapshot_id']}/content"
    downloaded = client.get(path)
    assert downloaded.status_code == 200 and downloaded.content == content.encode()
    assert calls == [("artifact-1", principal["sub"])]
    with SessionLocal() as db:
        saved = db.scalar(select(BenchmarkInputSnapshot).where(
            BenchmarkInputSnapshot.owner_subject == principal["sub"],
        ))
        assert saved.source_reference == "artifact-1"
        assert saved.service_principal == principal["client_id"]
    client.app.dependency_overrides[require_benchmark_source_read] = lambda: {
        "sub": "service:different-service", "client_id": "different-service",
    }
    assert client.get(path).status_code == 404


def test_upload_identity_includes_media_and_corruption_fails_closed(transfer, monkeypatch):
    client, principal, root = transfer
    content = b'[{"text":"A synthetic scientific observation."}]'
    first = upload(client, content, "application/json").json()
    second = upload(client, content, "text/plain").json()
    assert first["snapshot_id"] != second["snapshot_id"]
    assert first["source_reference"] != second["source_reference"]
    assert first["blob_reference"] == second["blob_reference"]
    assert first["digest"] == second["digest"]
    path = f"/api/v1/benchmarks/sources/snapshots/{first['snapshot_id']}/content"
    monkeypatch.setattr(api, "get_benchmark_max_input_bytes", lambda: len(content) - 1)
    assert client.get(path).status_code == 413
    monkeypatch.setattr(api, "get_benchmark_max_input_bytes", lambda: len(content) + 10)
    (root / first["blob_reference"]).write_bytes(content + b"private-corruption")
    response = client.get(path)
    assert response.status_code == 503 and "private-corruption" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_concurrent_equivalent_uploads_reuse_verified_receipt(transfer, monkeypatch):
    client, principal, root = transfer
    barrier = threading.Barrier(2, timeout=10)

    class RacingStore(FileSystemBenchmarkSnapshotStore):
        def put(self, **kwargs):
            # Both transactions have missed the metadata SELECT before either
            # inserts, exercising the actual ON CONFLICT winner readback.
            barrier.wait()
            return super().put(**kwargs)

    monkeypatch.setattr(api, "configured_benchmark_snapshot_store", lambda: RacingStore(root))
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: upload(client, b"Concurrent synthetic paper"), range(2)))
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(BenchmarkInputSnapshot).where(
            BenchmarkInputSnapshot.owner_subject == principal["sub"],
        )) == 1
