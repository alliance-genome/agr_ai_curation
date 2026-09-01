"""Canonical benchmark snapshot store integrity tests."""

import hashlib

import pytest

from src.lib.benchmarks.snapshots import (
    BenchmarkSnapshotError,
    FileSystemBenchmarkSnapshotStore,
)
from src.models.sql.benchmark import (
    BenchmarkCell,
    BenchmarkInputSnapshot,
    BenchmarkJob,
    BenchmarkJobInputSnapshot,
)


def _digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def test_filesystem_store_deduplicates_and_survives_reopen(tmp_path):
    content = b'{"canonical":true}'
    digest = _digest(content)
    store = FileSystemBenchmarkSnapshotStore(tmp_path)

    first = store.put(digest=digest, content=content)
    second = store.put(digest=digest, content=content)

    assert first == second
    assert FileSystemBenchmarkSnapshotStore(tmp_path).read(
        blob_reference=first
    ) == content
    assert len(tuple(tmp_path.rglob(digest.removeprefix("sha256:") + "*"))) == 1


def test_filesystem_store_rejects_digest_mismatch_and_tampering(tmp_path):
    content = b"canonical"
    store = FileSystemBenchmarkSnapshotStore(tmp_path)
    with pytest.raises(BenchmarkSnapshotError, match="does not match"):
        store.put(digest=_digest(b"different"), content=content)

    reference = store.put(digest=_digest(content), content=content)
    (tmp_path / reference).write_bytes(b"tampered")
    with pytest.raises(BenchmarkSnapshotError, match="digest verification"):
        store.put(digest=_digest(content), content=content)


def test_durable_benchmark_models_have_no_delegated_secret_fields():
    column_names = {
        column.name
        for model in (
            BenchmarkInputSnapshot,
            BenchmarkJobInputSnapshot,
            BenchmarkJob,
            BenchmarkCell,
        )
        for column in model.__table__.columns
    }
    assert not any(
        marker in column_name
        for column_name in column_names
        for marker in ("authorization", "bearer", "credential", "token")
    )
