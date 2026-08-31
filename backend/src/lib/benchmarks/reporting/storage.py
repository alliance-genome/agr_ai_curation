"""Explicit, resumable S3 persistence for private benchmark artifacts."""

from __future__ import annotations

import base64
import hashlib
import time
from collections.abc import Callable
from typing import Any

from .artifacts import canonical_json_bytes
from .models import ArtifactBundle, StoredArtifactReceipt


class ArtifactStorageError(RuntimeError):
    """Normalized storage failure that never includes provider exception bodies."""


class DuplicateLogicalRunError(ArtifactStorageError):
    """The logical run already has a different immutable manifest."""


class S3ArtifactStore:
    """Persist content-addressed artifacts and one immutable logical manifest."""

    def __init__(
        self,
        client: Any,
        *,
        bucket: str,
        prefix: str,
        part_size_bytes: int,
        max_artifact_bytes: int,
        retries: int,
        retry_backoff_seconds: float,
        secret_patterns: tuple[str, ...] = (),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not bucket:
            raise ValueError("bucket is required")
        if part_size_bytes < 1 or max_artifact_bytes < 1:
            raise ValueError("artifact and part sizes must be positive")
        if retries < 0 or retry_backoff_seconds < 0:
            raise ValueError("retries and retry backoff must be non-negative")
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.part_size_bytes = part_size_bytes
        self.max_artifact_bytes = max_artifact_bytes
        self.retries = retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.secret_patterns = secret_patterns
        self._sleep = sleep

    def upload_bundle(self, bundle: ArtifactBundle) -> list[StoredArtifactReceipt]:
        """Upload report then atomically claim the logical run with its manifest."""

        logical_run_id = bundle.manifest.provenance.logical_run_id
        root = "/".join(
            item for item in (self.prefix, "runs", logical_run_id) if item
        )
        report_bytes = canonical_json_bytes(
            bundle.report, secret_patterns=self.secret_patterns
        )
        report_sha = hashlib.sha256(report_bytes).hexdigest()
        report_key = f"{root}/artifacts/{report_sha}/report.json"
        report = self._upload(
            name="report.json",
            key=report_key,
            data=report_bytes,
            media_type="application/json",
            immutable=False,
        )
        stored_manifest = bundle.manifest.model_copy(
            update={"storage_receipts": [report]}
        )
        manifest_bytes = canonical_json_bytes(
            stored_manifest, secret_patterns=self.secret_patterns
        )
        manifest = self._upload(
            name="manifest.json",
            key=f"{root}/manifest.json",
            data=manifest_bytes,
            media_type="application/json",
            immutable=True,
        )
        return [report, manifest]

    def _upload(
        self,
        *,
        name: str,
        key: str,
        data: bytes,
        media_type: str,
        immutable: bool,
    ) -> StoredArtifactReceipt:
        if len(data) > self.max_artifact_bytes:
            raise ArtifactStorageError("artifact exceeds configured maximum size")
        digest_hex = hashlib.sha256(data).hexdigest()
        existing = self._head(key)
        if existing is not None:
            if not self._object_matches(existing, data):
                if immutable:
                    raise DuplicateLogicalRunError(
                        "logical run already has a different immutable manifest"
                    )
                raise ArtifactStorageError("existing artifact checksum does not match")
            return self._receipt(name, key, len(data), digest_hex, existing)

        if len(data) <= self.part_size_bytes:
            kwargs = {
                "Bucket": self.bucket,
                "Key": key,
                "Body": data,
                "ContentType": media_type,
                "Metadata": {"sha256": digest_hex},
                "ChecksumSHA256": base64.b64encode(
                    hashlib.sha256(data).digest()
                ).decode(),
            }
            if immutable:
                kwargs["IfNoneMatch"] = "*"
            try:
                self._call("put_object", **kwargs)
            except ArtifactStorageError:
                existing = self._head(key)
                if existing is None:
                    raise
                if not self._object_matches(existing, data):
                    if immutable:
                        raise DuplicateLogicalRunError(
                            "logical run already has a different immutable manifest"
                        ) from None
                    raise
        else:
            if immutable:
                raise ArtifactStorageError(
                    "immutable manifest exceeds multipart threshold"
                )
            self._multipart_upload(key, data, media_type, digest_hex)

        verified = self._head(key)
        if verified is None or not self._object_matches(verified, data):
            raise ArtifactStorageError("uploaded artifact checksum verification failed")
        return self._receipt(name, key, len(data), digest_hex, verified)

    def _multipart_upload(
        self, key: str, data: bytes, media_type: str, digest_hex: str
    ) -> None:
        response = self._call(
            "list_multipart_uploads", Bucket=self.bucket, Prefix=key
        )
        matching = [
            item for item in response.get("Uploads", []) if item.get("Key") == key
        ]
        if matching:
            upload_id = sorted(matching, key=lambda item: item["UploadId"])[0][
                "UploadId"
            ]
        else:
            created = self._call(
                "create_multipart_upload",
                Bucket=self.bucket,
                Key=key,
                ContentType=media_type,
                Metadata={"sha256": digest_hex},
                ChecksumAlgorithm="SHA256",
            )
            upload_id = created["UploadId"]

        listed = self._call(
            "list_parts", Bucket=self.bucket, Key=key, UploadId=upload_id
        )
        listed_parts = {part["PartNumber"]: part for part in listed.get("Parts", [])}
        completed: dict[int, dict[str, Any]] = {}
        part_count = (len(data) + self.part_size_bytes - 1) // self.part_size_bytes
        for part_number in range(1, part_count + 1):
            start = (part_number - 1) * self.part_size_bytes
            body = data[start : start + self.part_size_bytes]
            checksum = self._sha256_base64(body)
            listed_part = listed_parts.get(part_number)
            if listed_part is not None and listed_part.get("ChecksumSHA256") == checksum:
                completed[part_number] = {
                    "PartNumber": part_number,
                    "ETag": listed_part["ETag"],
                    "ChecksumSHA256": checksum,
                }
                continue
            uploaded = self._call(
                "upload_part",
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
                PartNumber=part_number,
                Body=body,
                ChecksumSHA256=checksum,
            )
            if uploaded.get("ChecksumSHA256") != checksum:
                raise ArtifactStorageError("uploaded part checksum verification failed")
            completed[part_number] = {
                "PartNumber": part_number,
                "ETag": uploaded["ETag"],
                "ChecksumSHA256": checksum,
            }
        completed_upload = self._call(
            "complete_multipart_upload",
            Bucket=self.bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={
                "Parts": [completed[number] for number in range(1, part_count + 1)]
            },
        )
        if completed_upload.get("ChecksumSHA256") != self._object_checksum(data):
            raise ArtifactStorageError("multipart object checksum verification failed")

    @staticmethod
    def _sha256_base64(data: bytes) -> str:
        return base64.b64encode(hashlib.sha256(data).digest()).decode()

    def _object_checksum(self, data: bytes) -> str:
        if len(data) <= self.part_size_bytes:
            return self._sha256_base64(data)
        part_digests = [
            hashlib.sha256(data[start : start + self.part_size_bytes]).digest()
            for start in range(0, len(data), self.part_size_bytes)
        ]
        composite = base64.b64encode(hashlib.sha256(b"".join(part_digests)).digest())
        return f"{composite.decode()}-{len(part_digests)}"

    def _object_matches(self, head: dict[str, Any], data: bytes) -> bool:
        return (
            head.get("ContentLength") == len(data)
            and head.get("ChecksumSHA256") == self._object_checksum(data)
        )

    def _head(self, key: str) -> dict[str, Any] | None:
        for attempt in range(self.retries + 1):
            try:
                return self.client.head_object(
                    Bucket=self.bucket, Key=key, ChecksumMode="ENABLED"
                )
            except Exception as exc:
                response = getattr(exc, "response", {})
                status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                code = response.get("Error", {}).get("Code")
                if status == 404 or code in {"404", "NoSuchKey", "NotFound"}:
                    return None
                if attempt == self.retries:
                    raise ArtifactStorageError("S3 artifact lookup failed") from None
                self._sleep(self.retry_backoff_seconds * (2**attempt))
        raise AssertionError("unreachable")

    def _call(self, method: str, **kwargs: Any) -> dict[str, Any]:
        for attempt in range(self.retries + 1):
            try:
                return getattr(self.client, method)(**kwargs)
            except Exception:
                if attempt == self.retries:
                    raise ArtifactStorageError("S3 artifact operation failed") from None
                self._sleep(self.retry_backoff_seconds * (2**attempt))
        raise AssertionError("unreachable")

    def _receipt(
        self,
        name: str,
        key: str,
        size_bytes: int,
        digest_hex: str,
        head: dict[str, Any],
    ) -> StoredArtifactReceipt:
        version_id = head.get("VersionId")
        etag = str(head.get("ETag", "")).strip('"')
        if not version_id or not etag:
            raise ArtifactStorageError(
                "artifact destination must return object version and ETag receipts"
            )
        return StoredArtifactReceipt(
            name=name,
            bucket=self.bucket,
            key=key,
            version_id=version_id,
            etag=etag,
            sha256=f"sha256:{digest_hex}",
            size_bytes=size_bytes,
        )


def create_configured_s3_artifact_store(
    *, bucket: str, prefix: str
) -> S3ArtifactStore:
    """Build the explicit upload client through environment/role resolution only."""

    from src.lib.openai_agents.config import (
        get_benchmark_artifact_max_bytes,
        get_benchmark_artifact_part_size_bytes,
        get_benchmark_artifact_retry_backoff_seconds,
        get_benchmark_artifact_secret_patterns,
        get_benchmark_artifact_upload_concurrency,
        get_benchmark_artifact_upload_enabled,
        get_benchmark_artifact_upload_retries,
        get_benchmark_artifact_upload_timeout_seconds,
    )

    if not get_benchmark_artifact_upload_enabled():
        raise ArtifactStorageError(
            "benchmark artifact upload is disabled; local report generation remains available"
        )

    import boto3
    from botocore.config import Config

    timeout = get_benchmark_artifact_upload_timeout_seconds()
    client = boto3.client(
        "s3",
        config=Config(
            connect_timeout=timeout,
            read_timeout=timeout,
            max_pool_connections=get_benchmark_artifact_upload_concurrency(),
            retries={"max_attempts": 0},
        ),
    )
    return S3ArtifactStore(
        client,
        bucket=bucket,
        prefix=prefix,
        part_size_bytes=get_benchmark_artifact_part_size_bytes(),
        max_artifact_bytes=get_benchmark_artifact_max_bytes(),
        retries=get_benchmark_artifact_upload_retries(),
        retry_backoff_seconds=get_benchmark_artifact_retry_backoff_seconds(),
        secret_patterns=get_benchmark_artifact_secret_patterns(),
    )
