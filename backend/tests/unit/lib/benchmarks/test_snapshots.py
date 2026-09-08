"""Canonical benchmark snapshot store integrity tests."""

import hashlib
from io import BytesIO

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
        blob_reference=first, max_bytes=len(content)
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

    class S3Error(Exception):
        def __init__(self, code):
            self.response = {"Error": {"Code": code}}

    class Body:
        def read(self, maximum):
            assert maximum == len(content) + 1
            return content

        def close(self):
            pass

    class Client:
        put_requests = []
        get_request = None
        version_id = None

        def get_bucket_versioning(self, **kwargs):
            return {"Status": "Enabled"}

        def head_object(self, **kwargs):
            if self.version_id is None:
                raise S3Error("NotFound")
            return {
                "VersionId": self.version_id,
                "Metadata": {"sha256": _digest(content).removeprefix("sha256:")},
                "ContentLength": len(content),
            }

        def put_object(self, **kwargs):
            self.put_requests.append(kwargs)
            self.version_id = "private-version-1"
            return {"VersionId": self.version_id}

        def get_object(self, **kwargs):
            self.get_request = kwargs
            return {"Body": Body()}

    client = Client()
    store = S3BenchmarkSnapshotStore(
        client, bucket="private-bucket", prefix="benchmark-inputs"
    )
    first = store.put(digest=_digest(content), content=content)
    second = store.put(digest=_digest(content), content=content)

    assert first == second
    assert first.endswith("?versionId=private-version-1")
    assert len(client.put_requests) == 1
    assert client.put_requests[0]["Bucket"] == "private-bucket"
    assert client.put_requests[0]["IfNoneMatch"] == "*"
    assert store.read(blob_reference=first, max_bytes=len(content)) == content
    assert client.get_request is not None
    assert client.get_request["VersionId"] == "private-version-1"


@pytest.mark.parametrize(
    ("versioning_status", "version_id", "message"),
    [
        (None, "private-version-1", "versioning must be enabled"),
        ("Suspended", "null", "versioning must be enabled"),
        ("Enabled", None, "immutable version ID"),
        ("Enabled", "null", "immutable version ID"),
    ],
)
def test_s3_store_fails_closed_without_real_enabled_versioning(
    versioning_status, version_id, message
):
    content = b"canonical"

    class S3Error(Exception):
        response = {"Error": {"Code": "NotFound"}}

    class Client:
        def get_bucket_versioning(self, **kwargs):
            return {"Status": versioning_status} if versioning_status else {}

        def head_object(self, **kwargs):
            raise S3Error

        def put_object(self, **kwargs):
            return {} if version_id is None else {"VersionId": version_id}

    store = S3BenchmarkSnapshotStore(
        Client(), bucket="private-bucket", prefix="benchmark-inputs"
    )
    with pytest.raises(BenchmarkSnapshotError, match=message):
        store.put(digest=_digest(content), content=content)


def test_stores_bound_reads_and_close_s3_body(tmp_path):
    store = FileSystemBenchmarkSnapshotStore(tmp_path)
    content = b"synthetic saved input"
    reference = store.put(digest=_digest(content), content=content)
    with pytest.raises(BenchmarkSnapshotError, match="read limit"):
        store.read(blob_reference=reference, max_bytes=3)

    class Body(BytesIO):
        requested = []

        def read(self, size=-1):
            self.requested.append(size)
            assert size == 4  # The store may read only cap+1, not the entire blob.
            return super().read(size)

    body = Body(content)

    class Client:
        def get_object(self, **kwargs):
            return {"Body": body}

    s3 = S3BenchmarkSnapshotStore(Client(), bucket="private", prefix="inputs")
    with pytest.raises(BenchmarkSnapshotError, match="read limit"):
        s3.read(blob_reference="s3://private/inputs/sha256/synthetic?versionId=one", max_bytes=3)
    assert body.closed and body.requested == [4]
