import hashlib
from datetime import datetime, timezone

import pytest

from src.lib.benchmarks.models import BenchmarkCaseRun, BenchmarkRoute, BenchmarkTarget
from src.lib.benchmarks.reporting import (
    ArtifactStorageError,
    DuplicateLogicalRunError,
    ReportProvenance,
    S3ArtifactStore,
    build_artifact_bundle,
    build_benchmark_report,
    create_configured_s3_artifact_store,
)


class FakeS3Error(Exception):
    def __init__(self, status, code="Error"):
        self.response = {
            "ResponseMetadata": {"HTTPStatusCode": status},
            "Error": {"Code": code},
        }


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.uploads = {}
        self.next_upload = 1
        self.next_version = 1
        self.fail_part_once = False
        self.fail_head_once = False
        self.deny_head = False

    def head_object(self, *, Bucket, Key):
        if self.fail_head_once:
            self.fail_head_once = False
            raise FakeS3Error(503, "SlowDown")
        if self.deny_head:
            raise FakeS3Error(403, "AccessDenied")
        if Key not in self.objects:
            raise FakeS3Error(404, "NoSuchKey")
        return self.objects[Key]

    def put_object(self, **kwargs):
        key = kwargs["Key"]
        if kwargs.get("IfNoneMatch") == "*" and key in self.objects:
            raise FakeS3Error(412, "PreconditionFailed")
        self._store(key, kwargs["Body"], kwargs["Metadata"])
        return {}

    def list_multipart_uploads(self, *, Bucket, Prefix):
        return {
            "Uploads": [
                {"Key": upload["key"], "UploadId": upload_id}
                for upload_id, upload in self.uploads.items()
                if upload["key"].startswith(Prefix)
            ]
        }

    def create_multipart_upload(self, **kwargs):
        upload_id = f"upload-{self.next_upload}"
        self.next_upload += 1
        self.uploads[upload_id] = {
            "key": kwargs["Key"],
            "metadata": kwargs["Metadata"],
            "parts": {},
        }
        return {"UploadId": upload_id}

    def list_parts(self, *, Bucket, Key, UploadId):
        upload = self.uploads[UploadId]
        return {
            "Parts": [
                {"PartNumber": number, "ETag": value[0]}
                | {"ChecksumSHA256": value[2]}
                for number, value in sorted(upload["parts"].items())
            ]
        }

    def upload_part(self, **kwargs):
        if self.fail_part_once:
            self.fail_part_once = False
            raise FakeS3Error(503, "SlowDown")
        etag = hashlib.md5(kwargs["Body"], usedforsecurity=False).hexdigest()
        self.uploads[kwargs["UploadId"]]["parts"][kwargs["PartNumber"]] = (
            etag,
            kwargs["Body"],
            kwargs["ChecksumSHA256"],
        )
        return {"ETag": etag, "ChecksumSHA256": kwargs["ChecksumSHA256"]}

    def complete_multipart_upload(self, **kwargs):
        upload = self.uploads.pop(kwargs["UploadId"])
        body = b"".join(value[1] for _, value in sorted(upload["parts"].items()))
        self._store(upload["key"], body, upload["metadata"])
        return {}

    def _store(self, key, body, metadata):
        version = f"version-{self.next_version}"
        self.next_version += 1
        self.objects[key] = {
            "Metadata": metadata,
            "ContentLength": len(body),
            "VersionId": version,
            "ETag": f'"{hashlib.md5(body, usedforsecurity=False).hexdigest()}"',
            "Body": body,
        }


def _bundle(logical_run_id="logical-1", case_count=1):
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    runs = [
        BenchmarkCaseRun(
            run_id=f"run-{index:03d}",
            profile_id="profile-1",
            case_id=f"case-{index:03d}",
            target=BenchmarkTarget(kind="agent", id="agent-1"),
            requested_route=BenchmarkRoute(provider="router", model="model-a"),
            started_at=now,
            completed_at=now,
            latency_ms=1,
            status="succeeded",
            fixture_digest=f"sha256:{index:064x}",
        )
        for index in range(case_count)
    ]
    report = build_benchmark_report(
        runs,
        [],
        ReportProvenance(
            logical_run_id=logical_run_id,
            generated_at=now,
            profile_revision="profile:1",
            config_revision="config:1",
            code_revision="code:1",
        ),
    )
    return build_artifact_bundle(report)


def _store(client, *, part_size=10_000, retries=1):
    return S3ArtifactStore(
        client,
        bucket="private-versioned-bucket",
        prefix="approved/benchmarks",
        part_size_bytes=part_size,
        max_artifact_bytes=1_000_000,
        retries=retries,
        retry_backoff_seconds=0,
        sleep=lambda _seconds: None,
    )


def test_upload_is_version_receipted_and_idempotent():
    client = FakeS3()
    store = _store(client)
    bundle = _bundle()

    first = store.upload_bundle(bundle)
    second = store.upload_bundle(bundle)

    assert first == second
    assert len(client.objects) == 2
    assert all(receipt.version_id.startswith("version-") for receipt in first)
    assert all(receipt.sha256.startswith("sha256:") for receipt in first)
    assert first[1].key.endswith("/logical-1/manifest.json")
    stored_manifest = client.objects[first[1].key]["Body"].decode()
    assert first[0].version_id in stored_manifest
    assert first[0].sha256 in stored_manifest


def test_interrupted_multipart_upload_resumes_without_duplicate_logical_run():
    client = FakeS3()
    store = _store(client, part_size=2_000, retries=0)
    bundle = _bundle(case_count=12)
    assert len(bundle.report_bytes) > 2_000
    assert len(bundle.manifest_bytes) < 2_000
    client.fail_part_once = True

    with pytest.raises(ArtifactStorageError, match="operation failed"):
        store.upload_bundle(bundle)
    assert len(client.uploads) == 1

    receipts = store.upload_bundle(bundle)
    assert len(receipts) == 2
    assert client.uploads == {}
    assert len(client.objects) == 2


def test_different_manifest_cannot_overwrite_a_logical_run():
    client = FakeS3()
    store = _store(client)
    store.upload_bundle(_bundle(case_count=1))

    with pytest.raises(DuplicateLogicalRunError, match="different immutable manifest"):
        store.upload_bundle(_bundle(case_count=2))
    manifest_keys = [key for key in client.objects if key.endswith("manifest.json")]
    assert len(manifest_keys) == 1


def test_denied_permission_is_normalized_without_provider_exception_body():
    client = FakeS3()
    client.deny_head = True
    with pytest.raises(ArtifactStorageError) as raised:
        _store(client).upload_bundle(_bundle())
    assert "AccessDenied" not in str(raised.value)


def test_transient_lookup_retries_with_bounded_backoff():
    client = FakeS3()
    client.fail_head_once = True
    delays = []
    store = S3ArtifactStore(
        client,
        bucket="private-versioned-bucket",
        prefix="approved",
        part_size_bytes=10_000,
        max_artifact_bytes=1_000_000,
        retries=1,
        retry_backoff_seconds=0.25,
        sleep=delays.append,
    )
    receipts = store.upload_bundle(_bundle())
    assert len(receipts) == 2
    assert delays == [0.25]


def test_artifact_size_limit_fails_before_upload():
    client = FakeS3()
    store = S3ArtifactStore(
        client,
        bucket="private-versioned-bucket",
        prefix="approved",
        part_size_bytes=100,
        max_artifact_bytes=10,
        retries=0,
        retry_backoff_seconds=0,
    )
    with pytest.raises(ArtifactStorageError, match="maximum size"):
        store.upload_bundle(_bundle())
    assert client.objects == {}


def test_upload_boundary_reapplies_configured_secret_patterns():
    client = FakeS3()
    store = S3ArtifactStore(
        client,
        bucket="private-versioned-bucket",
        prefix="approved",
        part_size_bytes=10_000,
        max_artifact_bytes=1_000_000,
        retries=0,
        retry_backoff_seconds=0,
        secret_patterns=("logical-1",),
    )
    with pytest.raises(ValueError, match="secret"):
        store.upload_bundle(_bundle())
    assert client.objects == {}


def test_configured_store_is_fail_closed_without_upload_switch(monkeypatch):
    monkeypatch.setenv("BENCHMARK_ARTIFACT_UPLOAD_ENABLED", "false")
    with pytest.raises(ArtifactStorageError, match="upload is disabled"):
        create_configured_s3_artifact_store(bucket="private", prefix="approved")
