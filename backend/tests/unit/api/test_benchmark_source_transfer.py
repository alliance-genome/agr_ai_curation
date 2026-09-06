"""Raw saved-byte transfer uses source authorization, never source/provider calls."""

import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api import benchmark_sources as api
from src.api.benchmark_auth import require_benchmark_source_read
from src.lib.benchmarks.snapshots import BenchmarkSnapshotError, BenchmarkSnapshotRepository


def digest(content):
    return "sha256:" + hashlib.sha256(content).hexdigest()


class Session:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def commit(self):
        pass


@pytest.fixture
def transfer(monkeypatch):
    captured = []

    class Repository:
        def __init__(self, *_args):
            pass

        def freeze_input(self, source, *, owner_subject, service_principal):
            captured.append(source)
            return SimpleNamespace(
                id=uuid4(), digest=source.digest, source_version=source.version,
                content_type=source.metadata.content_type, content_bytes=source.metadata.content_bytes,
                resolver_id=source.resolver, source_reference=source.reference,
                sanitized_provenance=source.provenance.model_dump(mode="json"),
                owner_subject=owner_subject, service_principal=service_principal,
                blob_reference="sha256/synthetic", created_at=datetime.now(timezone.utc),
            )

        receipt = staticmethod(BenchmarkSnapshotRepository.receipt)

    monkeypatch.setattr(api, "SessionLocal", Session)
    monkeypatch.setattr(api, "BenchmarkSnapshotRepository", Repository)
    monkeypatch.setattr(api, "configured_benchmark_snapshot_store", lambda: object())
    monkeypatch.setattr(api, "get_benchmark_enabled", lambda: True)
    monkeypatch.setattr(api, "get_benchmark_max_input_bytes", lambda: 4096)
    monkeypatch.setattr(api, "_catalog", lambda *_: pytest.fail("Source catalog must not be called"))
    app = FastAPI()
    app.dependency_overrides[require_benchmark_source_read] = lambda: {
        "sub": "service:synthetic", "client_id": "synthetic",
    }
    app.include_router(api.router)
    return TestClient(app), captured


@pytest.mark.parametrize("media,content", [
    ("text/plain", "α and β\r\nA synthetic observation.\n".encode()),
    ("text/markdown; charset=utf-8", b"# Results\r\nA synthetic observation."),
    ("application/json", b'[{"text":"Exact\\r\\ncontent","metadata":{}}]'),
    ("application/xml", b"<article><body><sec><title>Results</title>"
                        b"<p>A synthetic observation.</p></sec></body></article>"),
])
def test_upload_preserves_exact_bytes_and_server_generated_provenance(transfer, media, content):
    client, captured = transfer
    response = client.post("/api/v1/benchmarks/sources/snapshots", content=content,
                           headers={"Content-Type": media, "X-Benchmark-Content-Digest": digest(content)})
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert captured[0].content.encode("utf-8") == content
    assert receipt["digest"] == digest(content) and receipt["content_bytes"] == len(content)
    assert receipt["resolver_id"] == "uploaded_document" and receipt["source_version"] == "1"
    assert json.loads(receipt["source_reference"]) == {
        "schema": "uploaded_document/v1", "content_type": media.split(";")[0],
        "digest": digest(content),
    }
    assert receipt["owner_subject"] == "service:synthetic"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("media,content,status", [
    ("text/html", b"<p>paper</p>", 415),
    ("text/plain; charset=latin1", b"paper", 415),
    ("text/plain", b"\xff", 422), ("text/plain", b" \n", 422),
    ("application/json", b'{"authenticated_context":{"subject":"forged"}}', 422),
    ("application/json", b"[", 422),
    ("application/xml", b"not xml", 422),
    ("text/plain", b"x" * 4097, 413),
])
def test_invalid_uploads_do_not_reach_storage(transfer, media, content, status):
    client, captured = transfer
    response = client.post("/api/v1/benchmarks/sources/snapshots", content=content,
                           headers={"Content-Type": media, "X-Benchmark-Content-Digest": digest(content)})
    assert response.status_code == status
    assert captured == [] and response.headers["cache-control"] == "no-store"
    assert "authenticated_context" not in response.text


def test_digest_mismatch_and_delegated_credentials_are_denied(transfer):
    client, captured = transfer
    headers = {"Content-Type": "text/plain", "X-Benchmark-Content-Digest": digest(b"other")}
    response = client.post("/api/v1/benchmarks/sources/snapshots", content=b"paper", headers=headers)
    assert response.status_code == 409 and captured == []
    headers["X-Benchmark-Delegated-Source-Authorization"] = "Bearer synthetic"
    assert client.post("/api/v1/benchmarks/sources/snapshots", content=b"paper",
                       headers=headers).status_code == 400
    assert client.get(f"/api/v1/benchmarks/sources/snapshots/{uuid4()}/content",
                      headers=headers).status_code == 400


def test_source_scope_is_required_before_raw_body_or_storage(transfer, monkeypatch):
    client, captured = transfer

    def deny():
        raise HTTPException(403, "Source capability required")

    async def forbidden_stream(_self):
        pytest.fail("Unauthorized request body must not be read")
        yield b""

    client.app.dependency_overrides[require_benchmark_source_read] = deny
    monkeypatch.setattr(api.Request, "stream", forbidden_stream)
    response = client.post("/api/v1/benchmarks/sources/snapshots", content=b"paper")
    assert response.status_code == 403 and captured == []
    assert response.headers["cache-control"] == "no-store"
    assert client.get(f"/api/v1/benchmarks/sources/snapshots/{uuid4()}/content").status_code == 403


def test_unknown_owner_checked_before_store_and_safe_storage_error(transfer, monkeypatch):
    client, _ = transfer
    monkeypatch.setattr(Session, "scalar", lambda *_: None, raising=False)
    monkeypatch.setattr(api, "configured_benchmark_snapshot_store",
                        lambda: pytest.fail("Missing owner must not read store"))
    response = client.get(f"/api/v1/benchmarks/sources/snapshots/{uuid4()}/content")
    assert response.status_code == 404 and response.headers["cache-control"] == "no-store"


def test_upload_storage_failure_has_no_paper_exception_chain(transfer, monkeypatch):
    def broken():
        raise BenchmarkSnapshotError("private paper material")

    monkeypatch.setattr(api, "configured_benchmark_snapshot_store", broken)
    from src.schemas.benchmark_sources import BenchmarkSnapshotUploadMetadata

    with pytest.raises(HTTPException) as caught:
        api._freeze_uploaded_content(b"synthetic paper", BenchmarkSnapshotUploadMetadata(
            content_type="text/plain", digest=digest(b"synthetic paper")), "owner", "client")
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert "private paper" not in str(caught.value)


def test_transfer_openapi_describes_raw_body_and_binary_download(transfer):
    client, _ = transfer
    paths = client.app.openapi()["paths"]
    upload = paths["/api/v1/benchmarks/sources/snapshots"]["post"]
    assert upload["requestBody"]["content"]["application/json"]["schema"] == {
        "type": "string", "format": "binary",
    }
    assert any(item["name"] == "X-Benchmark-Content-Digest" for item in upload["parameters"])
    download = paths["/api/v1/benchmarks/sources/snapshots/{snapshot_id}/content"]["get"]
    assert "application/octet-stream" in download["responses"]["200"]["content"]
