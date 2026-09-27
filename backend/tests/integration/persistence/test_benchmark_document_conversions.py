"""Real PostgreSQL semantics for benchmark document conversion records."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
import pytest
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.exc import IntegrityError

from src.lib.benchmarks.document_conversions import (
    ConversionIdempotencyConflict,
    ConversionStateError,
    DocumentConversionRepository,
    DocumentConversionService,
)
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.snapshots import (
    BenchmarkSnapshotError,
    BenchmarkSnapshotRepository,
    FileSystemBenchmarkSnapshotStore,
)
from src.models.sql.benchmark import BenchmarkDocumentConversion, BenchmarkInputSnapshot
from src.models.sql.database import SessionLocal, engine
from src.models.sql.user import User


BACKEND_ROOT = Path(__file__).resolve().parents[3]
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
ABC_REFERENCE = "AGRKB:101000000000001"


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")


@pytest.fixture
def scope():
    owner = f"service:conversion-{uuid4()}"
    other_owner = f"service:conversion-other-{uuid4()}"
    with SessionLocal() as db:
        first = User(auth_sub=f"curator-{uuid4()}", is_active=True)
        second = User(auth_sub=f"curator-{uuid4()}", is_active=True)
        db.add_all([first, second])
        db.commit()
        curators = tuple(
            BenchmarkCuratorContext(
                subject=user.auth_sub, auth_provider="oidc", db_user_id=user.id,
                active_groups=("group-alpha",),
            )
            for user in (first, second)
        )
        user_ids = (first.id, second.id)
    yield {"owner": owner, "other_owner": other_owner, "curators": curators}
    with SessionLocal() as db:
        db.execute(delete(BenchmarkDocumentConversion).where(
            BenchmarkDocumentConversion.owner_subject.in_((owner, other_owner)),
        ))
        db.execute(delete(BenchmarkInputSnapshot).where(
            BenchmarkInputSnapshot.owner_subject.in_((owner, other_owner)),
        ))
        db.execute(delete(User).where(User.id.in_(user_ids)))
        db.commit()


def _pdf(repository, db, scope, *, key="key-1", digest=DIGEST_A, curator=0, owner=None):
    return repository.create_or_get(
        db,
        owner_subject=owner or scope["owner"],
        service_principal="portal-client",
        curator=scope["curators"][curator],
        input_kind="pdf",
        source_digest=digest,
        source_blob_reference="blobs/" + digest.removeprefix("sha256:"),
        abc_reference=None,
        idempotency_key=key,
    )


def _abc(repository, db, scope, *, key="abc-key", reference=ABC_REFERENCE):
    return repository.create_or_get(
        db,
        owner_subject=scope["owner"],
        service_principal="portal-client",
        curator=scope["curators"][0],
        input_kind="abc_reference",
        source_digest=None,
        source_blob_reference=None,
        abc_reference=reference,
        idempotency_key=key,
    )


def _snapshot(db, owner):
    snapshot = BenchmarkInputSnapshot(
        digest="sha256:" + uuid4().hex + uuid4().hex,
        source_version="v1",
        content_type="application/json",
        content_bytes=2,
        resolver_id="document_conversion",
        source_reference='{"schema":"document_conversion/v1"}',
        sanitized_provenance={"resolver": "document_conversion"},
        owner_subject=owner,
        service_principal="portal-client",
        blob_reference="blobs/" + uuid4().hex,
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def test_migration_creates_conversion_table_with_constraints():
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("benchmark_document_conversions")}
    assert columns == {
        "id", "owner_subject", "service_principal", "curator_subject", "curator_db_user_id",
        "input_kind", "source_digest", "source_blob_reference", "abc_reference", "status",
        "error_code", "error_message", "snapshot_id", "conversion_identity",
        "idempotency_key", "created_at", "started_at", "completed_at",
    }
    unique = {c["name"] for c in inspector.get_unique_constraints("benchmark_document_conversions")}
    assert "uq_benchmark_document_conversions_owner_key" in unique
    checks = {c["name"] for c in inspector.get_check_constraints("benchmark_document_conversions")}
    assert {
        "ck_benchmark_document_conversions_input_kind",
        "ck_benchmark_document_conversions_input_fields",
        "ck_benchmark_document_conversions_status",
        "ck_benchmark_document_conversions_status_fields",
    } <= checks
    targets = {
        (fk["referred_table"], tuple(fk["referred_columns"]))
        for fk in inspector.get_foreign_keys("benchmark_document_conversions")
    }
    assert targets == {("users", ("user_id",)), ("benchmark_input_snapshots", ("id",))}


def test_create_or_get_replays_same_input_and_rejects_changed_input(scope):
    repository = DocumentConversionRepository()
    with SessionLocal() as db:
        row, created = _pdf(repository, db, scope)
        db.commit()
        assert created is True
        assert row.status == "queued"
        assert row.curator_subject == scope["curators"][0].subject
        assert row.curator_db_user_id == scope["curators"][0].db_user_id
        assert row.owner_subject == scope["owner"]
        assert row.service_principal == "portal-client"

    with SessionLocal() as db:
        replay, created = _pdf(repository, db, scope)
        assert created is False and replay.id == row.id
        with pytest.raises(ConversionIdempotencyConflict):
            _pdf(repository, db, scope, digest=DIGEST_B)
        with pytest.raises(ConversionIdempotencyConflict):
            _pdf(repository, db, scope, curator=1)
        with pytest.raises(ConversionIdempotencyConflict):
            _abc(repository, db, scope, key="key-1")
        other, created = _pdf(repository, db, scope, key="key-2", digest=DIGEST_B)
        assert created is True and other.id != row.id
        db.commit()


def test_same_key_is_independent_per_owner_and_reads_are_owner_scoped(scope):
    repository = DocumentConversionRepository()
    with SessionLocal() as db:
        mine, _ = _pdf(repository, db, scope)
        theirs, created = _pdf(repository, db, scope, owner=scope["other_owner"])
        db.commit()
        assert created is True and theirs.id != mine.id

    with SessionLocal() as db:
        assert repository.get_for_owner(db, mine.id, scope["owner"]).id == mine.id
        assert repository.get_for_owner(db, mine.id, scope["other_owner"]) is None
        assert repository.get_for_owner(db, uuid4(), scope["owner"]) is None


def test_abc_conversion_records_reference_only(scope):
    repository = DocumentConversionRepository()
    with SessionLocal() as db:
        row, created = _abc(repository, db, scope)
        db.commit()
        assert created is True
        assert (row.input_kind, row.abc_reference, row.source_digest, row.source_blob_reference) == (
            "abc_reference", ABC_REFERENCE, None, None,
        )
        replay, created = _abc(repository, db, scope)
        assert created is False and replay.id == row.id
        with pytest.raises(ConversionIdempotencyConflict):
            _abc(repository, db, scope, reference="AGRKB:101000000000002")


def test_lifecycle_running_then_succeeded_with_owned_snapshot(scope):
    repository = DocumentConversionRepository()
    identity = {"input_kind": "pdf", "parser": "pdfx"}
    with SessionLocal() as db:
        row, _ = _pdf(repository, db, scope)
        db.commit()
        running = repository.mark_running(db, row.id)
        db.commit()
        assert running.status == "running" and running.started_at is not None
        with pytest.raises(ConversionStateError):
            repository.mark_running(db, row.id)

        foreign = _snapshot(db, scope["other_owner"])
        with pytest.raises(ConversionStateError):
            repository.mark_succeeded(db, row.id, snapshot_id=foreign.id, identity=identity)
        owned = _snapshot(db, scope["owner"])
        done = repository.mark_succeeded(db, row.id, snapshot_id=owned.id, identity=identity)
        db.commit()
        assert done.status == "succeeded"
        assert done.snapshot_id == owned.id
        assert done.conversion_identity == identity
        assert done.completed_at is not None
        assert done.error_code is None and done.error_message is None
        with pytest.raises(ConversionStateError):
            repository.mark_failed(db, row.id, code="parse_failed", message="Conversion failed.")


def test_failed_is_terminal_and_never_requeued(scope):
    repository = DocumentConversionRepository()
    with SessionLocal() as db:
        row, _ = _pdf(repository, db, scope)
        repository.mark_running(db, row.id)
        failed = repository.mark_failed(
            db, row.id, code="oversize_payload", message="The converted document is too large.",
        )
        db.commit()
        assert (failed.status, failed.error_code) == ("failed", "oversize_payload")
        assert failed.completed_at is not None and failed.snapshot_id is None
        with pytest.raises(ConversionStateError):
            repository.mark_running(db, row.id)
        replay, created = _pdf(repository, db, scope)
        assert created is False and replay.status == "failed"


def test_fail_stale_running_only_touches_unfinished_rows_created_before_cutoff(scope):
    repository = DocumentConversionRepository()
    with SessionLocal() as db:
        queued, _ = _pdf(repository, db, scope, key="queued")
        running, _ = _pdf(repository, db, scope, key="running")
        repository.mark_running(db, running.id)
        finished, _ = _pdf(repository, db, scope, key="finished")
        repository.mark_failed(db, finished.id, code="parse_failed", message="Conversion failed.")
        db.commit()
        cutoff = db.scalar(text("SELECT now()")) + timedelta(seconds=1)
        db.commit()
        fresh, _ = _pdf(repository, db, scope, key="fresh")
        db.execute(
            BenchmarkDocumentConversion.__table__.update()
            .where(BenchmarkDocumentConversion.id == fresh.id)
            .values(created_at=cutoff + timedelta(minutes=5))
        )
        db.commit()

    with SessionLocal() as db:
        failed_ids = repository.fail_stale_running(
            db, reason="The conversion was interrupted. Start it again.", created_before=cutoff,
        )
        db.commit()
        assert set(failed_ids) >= {queued.id, running.id}
        assert finished.id not in failed_ids and fresh.id not in failed_ids
        for conversion_id in (queued.id, running.id):
            row = repository.get_for_owner(db, conversion_id, scope["owner"])
            assert row.status == "failed"
            assert row.error_code == "interrupted"
            assert row.error_message == "The conversion was interrupted. Start it again."
            assert row.completed_at is not None
        assert repository.get_for_owner(db, finished.id, scope["owner"]).error_code == "parse_failed"
        assert repository.get_for_owner(db, fresh.id, scope["owner"]).status == "queued"


def test_database_rejects_inconsistent_rows(scope):
    curator = scope["curators"][0]
    base = {
        "owner_subject": scope["owner"], "service_principal": "portal-client",
        "curator_subject": curator.subject, "curator_db_user_id": curator.db_user_id,
        "idempotency_key": "raw",
    }
    invalid_rows = [
        {"input_kind": "pdf", "source_digest": DIGEST_A, "source_blob_reference": None,
         "status": "queued"},
        {"input_kind": "pdf", "source_digest": DIGEST_A, "source_blob_reference": "b",
         "abc_reference": ABC_REFERENCE, "status": "queued"},
        {"input_kind": "abc_reference", "abc_reference": "PMID:1", "status": "queued"},
        {"input_kind": "abc_reference", "abc_reference": ABC_REFERENCE, "status": "succeeded",
         "completed_at": datetime.now(timezone.utc)},
        {"input_kind": "abc_reference", "abc_reference": ABC_REFERENCE, "status": "failed",
         "completed_at": datetime.now(timezone.utc)},
        {"input_kind": "abc_reference", "abc_reference": ABC_REFERENCE, "status": "paused"},
    ]
    with SessionLocal() as db:
        for values in invalid_rows:
            with pytest.raises(IntegrityError):
                with db.begin_nested():
                    db.execute(BenchmarkDocumentConversion.__table__.insert().values(
                        id=uuid4(), **base, **values,
                    ))
        db.rollback()


# --- Conversion service against real PostgreSQL ------------------------------


SERVICE_PDF = b"%PDF-1.4\n%synthetic service fixture\n"
SERVICE_ELEMENTS = [
    {"index": 0, "type": "NarrativeText", "text": "Synthetic body.", "metadata": {}},
]


class _StubParser:
    methods = "grobid,marker"
    merge_enabled = True
    download_variant = "merged"

    async def parse_pdf_document(self, file_path, document_id, user_id, *, save_artifacts):
        assert save_artifacts is False and file_path.read_bytes() == SERVICE_PDF
        return {
            "elements": SERVICE_ELEMENTS, "pdfx_json_path": None,
            "processed_json_path": None, "page_provenance": None,
        }


def _queued_pdf_conversion(scope, store):
    digest = "sha256:" + hashlib.sha256(SERVICE_PDF).hexdigest()
    blob_reference = store.put(digest=digest, content=SERVICE_PDF)
    repository = DocumentConversionRepository()
    with SessionLocal() as db:
        row, _ = repository.create_or_get(
            db,
            owner_subject=scope["owner"],
            service_principal="portal-client",
            curator=scope["curators"][0],
            input_kind="pdf",
            source_digest=digest,
            source_blob_reference=blob_reference,
            abc_reference=None,
            idempotency_key=f"service-{uuid4()}",
        )
        db.commit()
        return row.id


@pytest.mark.asyncio
async def test_service_freezes_converted_pdf_as_snapshot_owned_by_the_service(scope, tmp_path):
    store = FileSystemBenchmarkSnapshotStore(tmp_path)
    conversion_id = _queued_pdf_conversion(scope, store)
    service = DocumentConversionService(snapshot_store_factory=lambda: store)

    with patch("src.lib.benchmarks.document_conversions.PDFXParser", _StubParser):
        await service.run(conversion_id, authorized_group_ids=())

    with SessionLocal() as db:
        row = DocumentConversionRepository().get_for_owner(db, conversion_id, scope["owner"])
        assert row.status == "succeeded" and row.error_code is None
        assert row.conversion_identity["parser"] == "pdfx"
        snapshot = db.get(BenchmarkInputSnapshot, row.snapshot_id)
        assert snapshot.owner_subject == scope["owner"]
        assert snapshot.owner_subject != scope["curators"][0].subject
        assert snapshot.service_principal == "portal-client"
        assert snapshot.content_type == "application/json"
        assert snapshot.resolver_id == "document_conversion"
        content = BenchmarkSnapshotRepository(db, store).read_verified(
            snapshot.id, owner_subject=scope["owner"],
        )
        assert json.loads(content) == SERVICE_ELEMENTS
        with pytest.raises(BenchmarkSnapshotError):
            BenchmarkSnapshotRepository(db, store).read_verified(
                snapshot.id, owner_subject=scope["curators"][0].subject,
            )


@pytest.mark.asyncio
async def test_service_records_oversize_as_failed_without_identity_or_snapshot(scope, tmp_path):
    store = FileSystemBenchmarkSnapshotStore(tmp_path)
    conversion_id = _queued_pdf_conversion(scope, store)
    service = DocumentConversionService(
        snapshot_store_factory=lambda: store, max_input_bytes=lambda: len(SERVICE_PDF),
    )

    with patch("src.lib.benchmarks.document_conversions.PDFXParser", _StubParser):
        await service.run(conversion_id, authorized_group_ids=())

    with SessionLocal() as db:
        row = DocumentConversionRepository().get_for_owner(db, conversion_id, scope["owner"])
        assert (row.status, row.error_code) == ("failed", "oversize_payload")
        assert row.snapshot_id is None and row.conversion_identity is None
        assert db.scalar(
            select(BenchmarkInputSnapshot.id).where(
                BenchmarkInputSnapshot.owner_subject == scope["owner"],
            )
        ) is None


@pytest.fixture
def conversion_api(scope, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api import benchmark_document_conversions as api
    from src.api.benchmark_auth import require_benchmark_source_read
    from src.api.benchmark_curator import require_benchmark_source_curator

    principal = {"sub": scope["owner"], "client_id": "portal-client"}
    dispatched = []

    async def run_conversion(conversion_id, *, authorized_group_ids):
        dispatched.append((conversion_id, tuple(authorized_group_ids)))

    monkeypatch.setattr(api, "get_benchmark_enabled", lambda: True)
    monkeypatch.setattr(api, "run_conversion", run_conversion)
    monkeypatch.setenv("BENCHMARK_SNAPSHOT_STORE_BACKEND", "filesystem")
    monkeypatch.setenv("BENCHMARK_SNAPSHOT_STORE_PATH", str(tmp_path))
    application = FastAPI()
    application.dependency_overrides[require_benchmark_source_read] = lambda: principal
    application.dependency_overrides[require_benchmark_source_curator] = (
        lambda: scope["curators"][0]
    )
    application.include_router(api.router)
    with TestClient(application) as client:
        yield client, application, dispatched, tmp_path


def _post_pdf(client, content, key="api-key"):
    return client.post("/api/v1/benchmarks/sources/document-conversions", content=content, headers={
        "Content-Type": "application/pdf", "Idempotency-Key": key,
        "X-Benchmark-Content-Digest": "sha256:" + hashlib.sha256(content).hexdigest(),
    })


def test_api_records_replays_conflicts_and_scopes_conversions(scope, conversion_api):
    from src.api.benchmark_auth import require_benchmark_source_read

    client, application, dispatched, root = conversion_api
    pdf = b"%PDF-1.7\nsynthetic api paper"
    first = _post_pdf(client, pdf)
    assert first.status_code == 202, first.text
    conversion_id = first.json()["conversion_id"]
    with SessionLocal() as db:
        row = db.get(BenchmarkDocumentConversion, conversion_id)
        assert (row.status, row.owner_subject, row.curator_subject) == (
            "queued", scope["owner"], scope["curators"][0].subject,
        )
        assert (root / row.source_blob_reference).read_bytes() == pdf
    assert dispatched == [(row.id, ("group-alpha",))]

    replay = _post_pdf(client, pdf)
    assert replay.status_code == 202 and replay.json()["conversion_id"] == conversion_id
    assert _post_pdf(client, pdf + b" changed").status_code == 409
    assert len(dispatched) == 1

    status = client.get(f"/api/v1/benchmarks/sources/document-conversions/{conversion_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "queued" and status.json()["snapshot"] is None

    application.dependency_overrides[require_benchmark_source_read] = lambda: {
        "sub": scope["other_owner"], "client_id": "other-client",
    }
    foreign = client.get(f"/api/v1/benchmarks/sources/document-conversions/{conversion_id}")
    assert foreign.status_code == 404


def test_api_fails_conversions_older_than_the_stale_window_before_creating(scope, conversion_api):
    client, _, _, _ = conversion_api
    repository = DocumentConversionRepository()
    with SessionLocal() as db:
        stale, _ = _pdf(repository, db, scope, key="stale-key", digest=DIGEST_A)
        fresh, _ = _pdf(repository, db, scope, key="fresh-key", digest=DIGEST_B)
        repository.mark_running(db, stale.id)
        db.execute(
            text("UPDATE benchmark_document_conversions SET created_at = :created WHERE id = :id"),
            {"created": datetime.now(timezone.utc) - timedelta(hours=3), "id": stale.id},
        )
        db.commit()
        stale_id, fresh_id = stale.id, fresh.id

    assert _post_pdf(client, b"%PDF-1.7\nnew paper", key="new-key").status_code == 202

    with SessionLocal() as db:
        stale = db.get(BenchmarkDocumentConversion, stale_id)
        fresh = db.get(BenchmarkDocumentConversion, fresh_id)
        assert (stale.status, stale.error_code, stale.error_message) == (
            "failed", "interrupted", "The conversion was interrupted. Start it again.",
        )
        assert fresh.status == "queued"
