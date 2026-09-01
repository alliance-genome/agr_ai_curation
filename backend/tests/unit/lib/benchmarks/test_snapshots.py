"""Canonical benchmark snapshot store integrity tests."""

import hashlib

import pytest

from src.lib.benchmarks.snapshots import (
    BenchmarkSnapshotError,
    FileSystemBenchmarkSnapshotStore,
    S3BenchmarkSnapshotStore,
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


def test_s3_store_requires_and_reuses_exact_private_object_version():
    content = b"canonical"

    class Body:
        def read(self):
            return content

    class Client:
        put_request = None
        get_request = None

        def put_object(self, **kwargs):
            self.put_request = kwargs
            return {"VersionId": "private-version-1"}

        def get_object(self, **kwargs):
            self.get_request = kwargs
            return {"Body": Body()}

    client = Client()
    store = S3BenchmarkSnapshotStore(
        client, bucket="private-bucket", prefix="benchmark-inputs"
    )
    reference = store.put(digest=_digest(content), content=content)

    assert reference.endswith("?versionId=private-version-1")
    assert client.put_request["Bucket"] == "private-bucket"
    assert store.read(blob_reference=reference) == content
    assert client.get_request["VersionId"] == "private-version-1"
