"""PostgreSQL metadata and private blob boundary for benchmark snapshots."""

import hashlib
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
import pytest
from sqlalchemy import delete
from sqlalchemy.exc import DBAPIError

from src.lib.benchmarks.input_resolvers import (
    BenchmarkSourceRequestContext,
    BenchmarkSourceMetadata,
    BenchmarkSourceProvenance,
    MaterializedBenchmarkInput,
)
from src.lib.benchmarks.snapshots import (
    BenchmarkSnapshotError,
    BenchmarkSnapshotRepository,
    FileSystemBenchmarkSnapshotStore,
    FrozenBenchmarkSnapshotResolver,
)
from src.lib.benchmarks.models import BenchmarkInputReference
from src.models.sql.benchmark import BenchmarkInputSnapshot
from src.models.sql.database import SessionLocal


BACKEND_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")


def _materialized(content: bytes = b"{}") -> MaterializedBenchmarkInput:
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    provenance = BenchmarkSourceProvenance(
        resolver="private_source",
        reference="approved-paper",
        version="v1",
        digest=digest,
    )
    return MaterializedBenchmarkInput(
        resolver=provenance.resolver,
        reference=provenance.reference,
        version=provenance.version,
        digest=digest,
        content=content.decode(),
        metadata=BenchmarkSourceMetadata(
            content_type="application/json", content_bytes=len(content)
        ),
        provenance=provenance,
    )


@pytest.mark.asyncio
async def test_local_resolver_snapshot_reaches_normal_chunker_without_source_reread(tmp_path):
    from src.lib.benchmarks.document_inputs import decode_frozen_document
    from src.lib.pipeline.chunk import chunk_parsed_document
    from src.models.strategy import ChunkingStrategy
    from src.services.benchmark_document_source import LocalDocumentResolver, LocalDocumentSourceRecord

    owner = f"benchmark-source-test:{uuid4()}"
    source_id = str(uuid4())
    owner_directory = tmp_path / owner
    owner_directory.mkdir()
    source_path = owner_directory / "extracted.json"
    content = json.dumps([{
        "index": 0,
        "type": "NarrativeText",
        "text": "A synthetic frozen observation that the normal document tools must read.",
        "metadata": {"page_number": 3, "section_title": "Results"},
    }]).encode()
    source_path.write_bytes(content)
    reads = []

    def source_record(reference, principal_subject):
        reads.append((reference, principal_subject))
        return LocalDocumentSourceRecord(
            reference=reference, version="v1", title="Synthetic paper",
            content_path=str(source_path.relative_to(tmp_path)),
        )

    resolver = LocalDocumentResolver(
        storage_root_provider=lambda: tmp_path, document_loader=source_record
    )
    materialized = await resolver.materialize(
        BenchmarkInputReference(
            resolver="local_document", reference=source_id, version="v1",
            digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
        ),
        source_id,
        max_bytes=4096,
        request_context=BenchmarkSourceRequestContext(principal_subject=owner),
    )
    with SessionLocal() as db:
        repository = BenchmarkSnapshotRepository(
            db, FileSystemBenchmarkSnapshotStore(tmp_path / "snapshots")
        )
        snapshot = repository.freeze_input(
            materialized, owner_subject=owner, service_principal="synthetic-portal"
        )
        db.flush()
        source_path.unlink()
        frozen = repository.read_verified(snapshot.id, owner_subject=owner)
        elements = decode_frozen_document(frozen, content_type=snapshot.content_type)
        isolated_document_id = str(uuid4())
        chunks = await chunk_parsed_document(
            elements, ChunkingStrategy.get_research_strategy(), isolated_document_id
        )
        assert len(chunks) == 1
        assert "synthetic frozen observation" in chunks[0].content
        assert str(chunks[0].document_id) == isolated_document_id
        assert reads == [(source_id, owner)]
        with pytest.raises(BenchmarkSnapshotError):
            repository.read_verified(snapshot.id, owner_subject="another-owner")
        # Metadata rows are synthetic and rolled back when this session closes.


def test_snapshot_metadata_deduplicates_per_owner_and_blob_across_owners(
    tmp_path, monkeypatch
):
    db = SessionLocal()
    snapshot_ids = []
    try:
        repository = BenchmarkSnapshotRepository(
            db, FileSystemBenchmarkSnapshotStore(tmp_path)
        )
        first = repository.freeze_input(
            _materialized(), owner_subject="owner-a", service_principal="portal"
        )
        repeated = repository.freeze_input(
            _materialized(), owner_subject="owner-a", service_principal="portal"
        )
        other_owner = repository.freeze_input(
            _materialized(), owner_subject="owner-b", service_principal="portal"
        )
        db.commit()
        snapshot_ids = [first.id, other_owner.id]

        assert repeated.id == first.id
        assert other_owner.id != first.id
        assert other_owner.blob_reference == first.blob_reference
        assert repository.read_verified(first.id, owner_subject="owner-a") == b"{}"
        assert not hasattr(first, "delegated_authorization")

        monkeypatch.setenv("BENCHMARK_SNAPSHOT_STORE_BACKEND", "filesystem")
        monkeypatch.setenv("BENCHMARK_SNAPSHOT_STORE_PATH", str(tmp_path))
        reused = asyncio.run(
            FrozenBenchmarkSnapshotResolver().materialize(
                BenchmarkInputReference(
                    resolver="frozen_snapshot",
                    reference=str(first.id),
                    version="v1",
                    digest=first.digest,
                ),
                str(first.id),
                max_bytes=100,
                request_context=BenchmarkSourceRequestContext(
                    principal_subject="owner-a"
                ),
            )
        )
        assert reused.content == "{}"
        assert reused.digest == first.digest

        first.content_type = "text/plain"
        with pytest.raises(DBAPIError, match="immutable"):
            db.flush()
        db.rollback()

        (tmp_path / first.blob_reference).write_bytes(b"tampered")
        with pytest.raises(BenchmarkSnapshotError, match="integrity"):
            repository.read_verified(first.id, owner_subject="owner-a")
    finally:
        db.rollback()
        if snapshot_ids:
            db.execute(
                delete(BenchmarkInputSnapshot).where(
                    BenchmarkInputSnapshot.id.in_(snapshot_ids)
                )
            )
            db.commit()
        db.close()
