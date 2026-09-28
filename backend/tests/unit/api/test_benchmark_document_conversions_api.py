"""Benchmark document conversion API: admission, idempotency and owner-scoped status."""

from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from src.api import benchmark_curator
from src.api import benchmark_document_conversions as api
from src.api.benchmark_auth import require_benchmark_source_read
from src.api.benchmark_curator import require_benchmark_source_curator
from src.lib.benchmarks.document_conversions import ConversionIdempotencyConflict
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext

BASE = "/api/v1/benchmarks/sources/document-conversions"
PDF = b"%PDF-1.7\nsynthetic paper body"
REFERENCE = "EXAMPLE:paper-0001"
PRINCIPAL = {"sub": "service:synthetic", "client_id": "synthetic"}
CURATOR = BenchmarkCuratorContext(
    subject="curator-synthetic", auth_provider="oidc", db_user_id=7,
    active_groups=("group-alpha", "group-beta"),
)


def digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


class Store:
    def __init__(self):
        self.blobs: dict[str, bytes] = {}

    def put(self, *, digest, content):
        self.blobs[digest] = content
        return f"sha256/{digest.removeprefix('sha256:')}"


class Session:
    snapshots: dict[UUID, SimpleNamespace] = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass

    def get(self, _model, key):
        return self.snapshots.get(key)


class Repository:
    """In-memory stand-in for the owner-scoped conversion repository contract."""

    def __init__(self, events):
        self.rows: dict[UUID, SimpleNamespace] = {}
        self.events = events

    def create_or_get(self, _db, *, owner_subject, service_principal, curator, input_kind,
                      source_digest, source_blob_reference, source_reference, idempotency_key):
        self.events.append("create")
        for row in self.rows.values():
            if row.owner_subject == owner_subject and row.idempotency_key == idempotency_key:
                if (
                    row.input_kind, row.source_digest, row.source_reference, row.curator_subject,
                ) != (
                    input_kind, source_digest, source_reference, curator.subject,
                ):
                    raise ConversionIdempotencyConflict("different input")
                return row, False
        row = SimpleNamespace(
            id=uuid4(), owner_subject=owner_subject, service_principal=service_principal,
            curator_subject=curator.subject, curator_db_user_id=curator.db_user_id,
            input_kind=input_kind, source_digest=source_digest,
            source_blob_reference=source_blob_reference, source_reference=source_reference,
            idempotency_key=idempotency_key, status="queued", error_code=None,
            error_message=None, snapshot_id=None, conversion_identity=None,
            created_at=datetime(2026, 9, 27, tzinfo=timezone.utc), completed_at=None,
        )
        self.rows[row.id] = row
        return row, True

    def get_for_owner(self, _db, conversion_id, owner_subject):
        row = self.rows.get(conversion_id)
        return row if row is not None and row.owner_subject == owner_subject else None


def snapshot_row(owner: str) -> SimpleNamespace:
    content = b'[{"text":"Converted","metadata":{}}]'
    reference = json.dumps({"schema": "document_conversion/v1"}, separators=(",", ":"))
    return SimpleNamespace(
        id=uuid4(), digest=digest(content), source_version="a" * 64,
        content_type="application/json", content_bytes=len(content),
        resolver_id="document_conversion", source_reference=reference,
        sanitized_provenance={"resolver": "document_conversion", "reference": reference,
                              "version": "a" * 64, "digest": digest(content)},
        owner_subject=owner, service_principal="synthetic",
        blob_reference="sha256/synthetic", created_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
    )


@pytest.fixture
def harness(monkeypatch):
    events: list[str] = []
    repository = Repository(events)
    store = Store()
    dispatched: list[tuple[UUID, tuple[str, ...], str]] = []
    Session.snapshots = {}

    async def run_conversion(conversion_id, *, authorized_group_ids):
        dispatched.append((conversion_id, tuple(authorized_group_ids),
                           repository.rows[conversion_id].status))

    def reconcile():
        events.append("reconcile")
        return ()

    monkeypatch.setattr(api, "SessionLocal", Session)
    monkeypatch.setattr(api, "DocumentConversionRepository", lambda: repository)
    monkeypatch.setattr(api, "configured_benchmark_snapshot_store", lambda: store)
    monkeypatch.setattr(api, "run_conversion", run_conversion)
    monkeypatch.setattr(api, "reconcile_stale_conversions", reconcile)
    monkeypatch.setattr(api, "get_benchmark_enabled", lambda: True)
    monkeypatch.setattr(api, "get_benchmark_max_input_bytes", lambda: 4096)
    app = FastAPI()
    app.dependency_overrides[require_benchmark_source_read] = lambda: dict(PRINCIPAL)
    app.dependency_overrides[require_benchmark_source_curator] = lambda: CURATOR
    app.include_router(api.router)
    return SimpleNamespace(client=TestClient(app), app=app, repository=repository, store=store,
                           dispatched=dispatched, events=events)


def post_pdf(client, content=PDF, *, key="paper-1", content_digest=None, extra=None):
    headers = {"Content-Type": "application/pdf", "Idempotency-Key": key,
               "X-Benchmark-Content-Digest": content_digest or digest(content)}
    headers.update(extra or {})
    return client.post(BASE, content=content, headers=headers)


def post_reference(client, body, *, key="paper-2", extra=None):
    headers = {"Content-Type": "application/json", "Idempotency-Key": key}
    headers.update(extra or {})
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return client.post(BASE, content=raw, headers=headers)


def test_pdf_conversion_is_accepted_stored_and_dispatched_with_curator_groups(harness):
    response = post_pdf(harness.client)
    assert response.status_code == 202, response.text
    body = response.json()
    assert set(body) == {"conversion_id", "status"} and body["status"] == "queued"
    conversion_id = UUID(body["conversion_id"])
    assert response.headers["location"] == f"{BASE}/{conversion_id}"
    assert response.headers["cache-control"] == "no-store"
    row = harness.repository.rows[conversion_id]
    assert (row.owner_subject, row.service_principal) == ("service:synthetic", "synthetic")
    assert row.curator_subject == CURATOR.subject and row.input_kind == "pdf"
    assert row.source_digest == digest(PDF) and row.source_reference is None
    assert harness.store.blobs == {digest(PDF): PDF}
    assert row.source_blob_reference == f"sha256/{hashlib.sha256(PDF).hexdigest()}"
    assert harness.dispatched == [(conversion_id, ("group-alpha", "group-beta"), "queued")]
    assert harness.events == ["reconcile", "create"]


def test_status_moves_from_queued_to_succeeded_with_the_snapshot_receipt(harness):
    conversion_id = UUID(post_pdf(harness.client).json()["conversion_id"])
    queued = harness.client.get(f"{BASE}/{conversion_id}")
    assert queued.status_code == 200, queued.text
    assert queued.json() == {
        "conversion_id": str(conversion_id), "status": "queued", "error": None,
        "snapshot": None, "conversion_identity": None,
        "created_at": "2026-09-27T00:00:00Z", "completed_at": None,
    }
    assert queued.headers["cache-control"] == "no-store"

    snapshot = snapshot_row("service:synthetic")
    Session.snapshots[snapshot.id] = snapshot
    identity = {"input_kind": "pdf", "parser": "pdfx"}
    harness.repository.rows[conversion_id].__dict__.update(
        status="succeeded", snapshot_id=snapshot.id, conversion_identity=identity,
        completed_at=datetime(2026, 9, 27, 0, 5, tzinfo=timezone.utc),
    )
    succeeded = harness.client.get(f"{BASE}/{conversion_id}").json()
    assert succeeded["status"] == "succeeded" and succeeded["error"] is None
    assert succeeded["conversion_identity"] == identity
    assert succeeded["completed_at"] == "2026-09-27T00:05:00Z"
    assert succeeded["snapshot"] == {
        "snapshot_id": str(snapshot.id), "digest": snapshot.digest,
        "source_version": "a" * 64, "content_type": "application/json",
        "content_bytes": snapshot.content_bytes, "resolver_id": "document_conversion",
        "source_reference": snapshot.source_reference,
        "sanitized_provenance": snapshot.sanitized_provenance,
        "owner_subject": "service:synthetic", "service_principal": "synthetic",
        "blob_reference": "sha256/synthetic", "created_at": "2026-09-27T00:00:00Z",
    }


def test_failed_status_reports_the_recorded_plain_error(harness):
    conversion_id = UUID(post_pdf(harness.client).json()["conversion_id"])
    harness.repository.rows[conversion_id].__dict__.update(
        status="failed", error_code="interrupted",
        error_message="The conversion was interrupted. Start it again.",
        completed_at=datetime(2026, 9, 27, 0, 5, tzinfo=timezone.utc),
    )
    body = harness.client.get(f"{BASE}/{conversion_id}").json()
    assert body["status"] == "failed" and body["snapshot"] is None
    assert body["error"] == {"code": "interrupted",
                             "message": "The conversion was interrupted. Start it again."}


def test_idempotent_replay_returns_the_same_conversion_without_redispatch(harness):
    first = post_pdf(harness.client)
    harness.repository.rows[UUID(first.json()["conversion_id"])].status = "running"
    replay = post_pdf(harness.client)
    assert replay.status_code == 202
    assert replay.json() == {"conversion_id": first.json()["conversion_id"], "status": "running"}
    assert replay.headers["location"] == first.headers["location"]
    assert len(harness.dispatched) == 1 and len(harness.repository.rows) == 1


def test_same_key_with_different_bytes_is_a_conflict(harness):
    post_pdf(harness.client)
    other = b"%PDF-1.7\ndifferent paper body"
    response = post_pdf(harness.client, other)
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "conflict"
    assert len(harness.dispatched) == 1


def test_source_reference_is_accepted_and_dispatched_with_curator_groups(harness):
    response = post_reference(harness.client, {"source_reference": REFERENCE})
    assert response.status_code == 202, response.text
    conversion_id = UUID(response.json()["conversion_id"])
    row = harness.repository.rows[conversion_id]
    assert row.input_kind == "source_reference" and row.source_reference == REFERENCE
    assert row.source_digest is None and row.source_blob_reference is None
    assert harness.store.blobs == {}
    assert harness.dispatched == [(conversion_id, ("group-alpha", "group-beta"), "queued")]


@pytest.mark.parametrize("reference", ["PMID:12345", "paper 12", "x" * 256, "Ä:1"])
def test_any_bounded_provider_identifier_is_accepted_for_the_provider_to_resolve(
    harness, reference,
):
    response = post_reference(harness.client, {"source_reference": reference})
    assert response.status_code == 202, response.text
    [row] = harness.repository.rows.values()
    assert row.input_kind == "source_reference" and row.source_reference == reference


@pytest.mark.parametrize("body", [
    {"source_reference": ""},
    {"source_reference": " EXAMPLE:1"},
    {"source_reference": "EXAMPLE:1 "},
    {"source_reference": "EXAMPLE:\n1"},
    {"source_reference": "EXAMPLE:\u00011"},
    {"source_reference": "x" * 257},
    {"source_reference": 101},
    {"source_reference": REFERENCE, "extra": True},
    {},
    [REFERENCE],
    b"{not json",
])
def test_invalid_source_reference_requests_are_rejected_before_any_record(harness, body):
    response = post_reference(harness.client, body)
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_reference"
    assert harness.repository.rows == {} and harness.dispatched == []


def test_pdf_digest_mismatch_is_rejected_before_storage(harness):
    response = post_pdf(harness.client, content_digest=digest(b"other"))
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_document"
    assert harness.store.blobs == {} and harness.repository.rows == {}


@pytest.mark.parametrize("content", [b"plain text, not a PDF", b"", b" %PDF-1.7"])
def test_non_pdf_bytes_are_rejected_before_storage(harness, content):
    response = post_pdf(harness.client, content)
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_document"
    assert harness.store.blobs == {} and harness.repository.rows == {}


def test_pdf_over_the_input_limit_is_rejected(harness):
    content = b"%PDF-" + b"x" * 4092
    response = post_pdf(harness.client, content)
    assert response.status_code == 413
    assert response.json()["detail"]["error"] == "oversize_payload"
    assert harness.store.blobs == {} and harness.repository.rows == {}
    assert post_pdf(harness.client, content[:4096]).status_code == 202


@pytest.mark.parametrize("headers", [
    {"X-Benchmark-Content-Digest": ""},
    {"X-Benchmark-Content-Digest": "md5:abc"},
])
def test_pdf_requires_a_sha256_content_digest(harness, headers):
    response = post_pdf(harness.client, extra=headers)
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_reference"


@pytest.mark.parametrize("key", [None, "", " padded", "k" * 256])
def test_idempotency_key_is_required_and_bounded(harness, key):
    headers = {"Content-Type": "application/pdf", "X-Benchmark-Content-Digest": digest(PDF)}
    if key is not None:
        headers["Idempotency-Key"] = key
    response = harness.client.post(BASE, content=PDF, headers=headers)
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_reference"
    assert harness.repository.rows == {} and harness.store.blobs == {}
    assert post_pdf(harness.client, key="k" * 255).status_code == 202


@pytest.mark.parametrize("media", ["text/plain", "application/pdf; charset=utf-8", ""])
def test_unsupported_content_types_are_rejected(harness, media):
    response = harness.client.post(BASE, content=PDF, headers={
        "Content-Type": media, "Idempotency-Key": "paper-1",
        "X-Benchmark-Content-Digest": digest(PDF),
    })
    assert response.status_code == 415
    assert harness.repository.rows == {}


def test_delegated_source_credentials_are_rejected(harness):
    delegated = {"X-Benchmark-Delegated-Source-Authorization": "Bearer synthetic"}
    response = post_pdf(harness.client, extra=delegated)
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "unexpected_delegated_authorization"
    response = post_reference(harness.client, {"source_reference": REFERENCE}, extra=delegated)
    assert response.status_code == 400
    assert harness.client.get(f"{BASE}/{uuid4()}", headers=delegated).status_code == 400
    assert harness.repository.rows == {} and harness.store.blobs == {}


def test_disabled_benchmark_api_is_not_found(harness, monkeypatch):
    monkeypatch.setattr(api, "get_benchmark_enabled", lambda: False)
    assert post_pdf(harness.client).status_code == 404
    assert harness.client.get(f"{BASE}/{uuid4()}").status_code == 404
    assert harness.repository.rows == {}


def test_status_is_owner_scoped(harness):
    conversion_id = post_pdf(harness.client).json()["conversion_id"]
    harness.app.dependency_overrides[require_benchmark_source_read] = lambda: {
        "sub": "service:other", "client_id": "other",
    }
    response = harness.client.get(f"{BASE}/{conversion_id}")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "not_found"
    assert harness.client.get(f"{BASE}/{uuid4()}").status_code == 404


def test_idempotency_keys_are_scoped_to_the_calling_service(harness):
    first = post_pdf(harness.client).json()["conversion_id"]
    harness.app.dependency_overrides[require_benchmark_source_read] = lambda: {
        "sub": "service:other", "client_id": "other",
    }
    second = post_pdf(harness.client).json()["conversion_id"]
    assert first != second and len(harness.dispatched) == 2


def test_database_failures_are_sanitized(harness, monkeypatch):
    def broken(*_args, **_kwargs):
        raise OperationalError("SELECT synthetic-secret", {}, Exception("synthetic-secret"))

    monkeypatch.setattr(harness.repository, "create_or_get", broken)
    response = post_pdf(harness.client)
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "source_unavailable"
    assert "synthetic-secret" not in response.text and harness.dispatched == []


def test_post_requires_a_verified_curator_and_get_does_not(harness, monkeypatch):
    seen = []

    async def verify(_request, orchestration, curator_authorization):
        seen.append((orchestration, curator_authorization))
        raise HTTPException(401, {"code": "curator_authorization_required", "message": "x"})

    del harness.app.dependency_overrides[require_benchmark_source_curator]
    monkeypatch.setattr(benchmark_curator, "verify_benchmark_curator", verify)
    response = post_pdf(harness.client, extra={"X-Benchmark-Curator-Authorization": "Bearer human"})
    assert response.status_code == 401
    assert seen == [(PRINCIPAL, "Bearer human")]
    assert harness.repository.rows == {} and harness.store.blobs == {}
    assert harness.client.get(f"{BASE}/{uuid4()}").status_code == 404
    assert len(seen) == 1


def test_source_scope_is_required_before_the_body_is_read(harness, monkeypatch):
    def deny():
        raise HTTPException(403, "Source capability required")

    async def forbidden_stream(_self):
        pytest.fail("Unauthorized request body must not be read")
        yield b""

    harness.app.dependency_overrides[require_benchmark_source_read] = deny
    del harness.app.dependency_overrides[require_benchmark_source_curator]
    monkeypatch.setattr(api.Request, "stream", forbidden_stream)
    assert post_pdf(harness.client).status_code == 403
    assert harness.client.get(f"{BASE}/{uuid4()}").status_code == 403
    assert harness.repository.rows == {}


def test_status_reads_reconcile_stale_conversions_first(harness, monkeypatch):
    conversion_id = UUID(post_pdf(harness.client).json()["conversion_id"])
    harness.repository.rows[conversion_id].status = "running"
    harness.events.clear()

    def reconcile():
        harness.events.append("reconcile")
        harness.repository.rows[conversion_id].__dict__.update(
            status="failed", error_code="interrupted",
            error_message="The conversion was interrupted. Start it again.",
            completed_at=datetime(2026, 9, 27, 3, tzinfo=timezone.utc),
        )
        return (conversion_id,)

    monkeypatch.setattr(api, "reconcile_stale_conversions", reconcile)
    body = harness.client.get(f"{BASE}/{conversion_id}").json()
    assert harness.events == ["reconcile"]
    assert body["status"] == "failed"
    assert body["error"] == {"code": "interrupted",
                             "message": "The conversion was interrupted. Start it again."}
