"""Durable token-free benchmark input snapshots and private blob stores."""

from __future__ import annotations

from collections.abc import Mapping
import asyncio
from datetime import datetime
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Annotated, Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import Field, RootModel

from src.lib.openai_agents.config import (
    get_benchmark_snapshot_s3_bucket,
    get_benchmark_snapshot_s3_prefix,
    get_benchmark_snapshot_store_backend,
    get_benchmark_snapshot_store_path,
)
from src.models.sql.benchmark import BenchmarkInputSnapshot
from src.models.sql.database import SessionLocal

from .input_resolvers import (
    BenchmarkInputResolverCatalog,
    BenchmarkSourceRequestContext,
    BenchmarkSourceError,
    BenchmarkSourceMetadata,
    BenchmarkSourceProvenance,
    DelegatedAuthorizationCapability,
    MaterializedBenchmarkInput,
    MaterializedBenchmarkPlanInputs,
    materialize_plan_inputs,
)
from .models import BenchmarkInputReference, FrozenStrictModel, ResolvedBenchmarkPlan


class BenchmarkSnapshotError(RuntimeError):
    """Content-free snapshot persistence or integrity failure."""


class FrozenBenchmarkInputSnapshot(FrozenStrictModel):
    """Public token-free receipt for one committed immutable input."""

    snapshot_id: UUID
    digest: str
    source_version: str
    content_type: str
    content_bytes: int
    resolver_id: str
    source_reference: str
    sanitized_provenance: dict[str, Any]
    owner_subject: str
    service_principal: str
    blob_reference: str
    created_at: datetime


class FrozenSnapshotReference(RootModel[str]):
    """Internal immutable snapshot UUID accepted by the reuse resolver."""

    root: Annotated[
        str,
        Field(
            pattern=(
                r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
                r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
            )
        ),
    ]


class BenchmarkSnapshotStore(Protocol):
    """Project-neutral private canonical-byte store."""

    def put(self, *, digest: str, content: bytes) -> str: ...

    def read(self, *, blob_reference: str) -> bytes: ...


def _digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _digest_hex(digest: str) -> str:
    prefix, separator, value = digest.partition(":")
    if prefix != "sha256" or separator != ":" or len(value) != 64:
        raise BenchmarkSnapshotError("Snapshot digest is invalid")
    return value


class FileSystemBenchmarkSnapshotStore:
    """Atomic content-addressed store for the Compose durable named volume."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve(strict=False)

    def put(self, *, digest: str, content: bytes) -> str:
        digest_hex = _digest_hex(digest)
        if _digest(content) != digest:
            raise BenchmarkSnapshotError("Snapshot content digest does not match")
        reference = f"sha256/{digest_hex[:2]}/{digest_hex}"
        destination = self.root / reference
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.exists():
            self._verify(destination, digest)
            return reference
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent, prefix=".snapshot-"
        )
        try:
            with os.fdopen(file_descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, destination)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        self._verify(destination, digest)
        return reference

    def read(self, *, blob_reference: str) -> bytes:
        path = (self.root / blob_reference).resolve(strict=False)
        if not path.is_relative_to(self.root):
            raise BenchmarkSnapshotError("Snapshot blob reference is invalid")
        try:
            return path.read_bytes()
        except OSError as exc:
            raise BenchmarkSnapshotError("Snapshot content is unavailable") from exc

    @staticmethod
    def _verify(path: Path, digest: str) -> None:
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise BenchmarkSnapshotError("Snapshot content is unavailable") from exc
        if _digest(content) != digest:
            raise BenchmarkSnapshotError("Stored snapshot failed digest verification")


class S3BenchmarkSnapshotStore:
    """Private versioned object-store implementation selected by deployment."""

    def __init__(self, client: Any, *, bucket: str, prefix: str) -> None:
        if not bucket:
            raise BenchmarkSnapshotError("Snapshot S3 bucket is required")
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")

    def put(self, *, digest: str, content: bytes) -> str:
        digest_hex = _digest_hex(digest)
        if _digest(content) != digest:
            raise BenchmarkSnapshotError("Snapshot content digest does not match")
        key = "/".join(part for part in (self.prefix, "sha256", digest_hex) if part)
        self._require_versioning_enabled()
        existing = self._existing_reference(
            key=key,
            digest=digest,
            digest_hex=digest_hex,
            content_bytes=len(content),
        )
        if existing is not None:
            return existing
        try:
            response = self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType="application/octet-stream",
                Metadata={"sha256": digest_hex},
                IfNoneMatch="*",
            )
        except Exception as exc:
            if self._error_code(exc) != "PreconditionFailed":
                raise BenchmarkSnapshotError(
                    "Snapshot content could not be stored"
                ) from None
            existing = self._existing_reference(
                key=key,
                digest=digest,
                digest_hex=digest_hex,
                content_bytes=len(content),
            )
            if existing is None:
                raise BenchmarkSnapshotError(
                    "Snapshot content could not be stored"
                ) from None
            return existing
        version_id = self._version_id(response)
        return self._reference(key, version_id)

    def _require_versioning_enabled(self) -> None:
        try:
            response = self.client.get_bucket_versioning(Bucket=self.bucket)
        except Exception:
            raise BenchmarkSnapshotError(
                "Snapshot object store versioning could not be verified"
            ) from None
        if response.get("Status") != "Enabled":
            raise BenchmarkSnapshotError(
                "Snapshot object store versioning must be enabled"
            )

    def _existing_reference(
        self,
        *,
        key: str,
        digest: str,
        digest_hex: str,
        content_bytes: int,
    ) -> str | None:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if self._error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise BenchmarkSnapshotError(
                "Snapshot content could not be inspected"
            ) from None
        version_id = self._version_id(response)
        metadata = response.get("Metadata")
        if (
            not isinstance(metadata, Mapping)
            or metadata.get("sha256") != digest_hex
            or response.get("ContentLength") != content_bytes
        ):
            raise BenchmarkSnapshotError(
                "Stored snapshot failed metadata verification"
            )
        reference = self._reference(key, version_id)
        if _digest(self.read(blob_reference=reference)) != digest:
            raise BenchmarkSnapshotError("Stored snapshot failed digest verification")
        return reference

    @staticmethod
    def _error_code(exc: Exception) -> str:
        response = getattr(exc, "response", None)
        if not isinstance(response, Mapping):
            return ""
        error = response.get("Error")
        if isinstance(error, Mapping) and error.get("Code") is not None:
            return str(error["Code"])
        metadata = response.get("ResponseMetadata")
        if isinstance(metadata, Mapping) and metadata.get("HTTPStatusCode") is not None:
            return str(metadata["HTTPStatusCode"])
        return ""

    @staticmethod
    def _version_id(response: Mapping[str, Any]) -> str:
        version_id = response.get("VersionId")
        if not isinstance(version_id, str) or not version_id or version_id == "null":
            raise BenchmarkSnapshotError(
                "Snapshot object store must return an immutable version ID"
            )
        return version_id

    def _reference(self, key: str, version_id: str) -> str:
        return f"s3://{self.bucket}/{key}?versionId={version_id}"

    def read(self, *, blob_reference: str) -> bytes:
        prefix = f"s3://{self.bucket}/"
        if not blob_reference.startswith(prefix) or "?versionId=" not in blob_reference:
            raise BenchmarkSnapshotError("Snapshot blob reference is invalid")
        key_and_version = blob_reference[len(prefix) :]
        key, version_id = key_and_version.rsplit("?versionId=", 1)
        version_id = self._version_id({"VersionId": version_id})
        response = self.client.get_object(
            Bucket=self.bucket, Key=key, VersionId=version_id
        )
        return response["Body"].read()


def configured_benchmark_snapshot_store() -> BenchmarkSnapshotStore:
    """Build only the explicitly selected private storage backend."""

    backend = get_benchmark_snapshot_store_backend()
    if backend == "filesystem":
        return FileSystemBenchmarkSnapshotStore(
            Path(get_benchmark_snapshot_store_path())
        )
    if backend == "s3":
        import boto3

        return S3BenchmarkSnapshotStore(
            boto3.client("s3"),
            bucket=get_benchmark_snapshot_s3_bucket(),
            prefix=get_benchmark_snapshot_s3_prefix(),
        )
    raise BenchmarkSnapshotError("Benchmark snapshot store backend is invalid")


class BenchmarkSnapshotRepository:
    """Transaction-bound metadata owner with verified token-free retrieval."""

    def __init__(self, session: Session, store: BenchmarkSnapshotStore) -> None:
        self.session = session
        self.store = store

    def freeze_input(
        self,
        source: MaterializedBenchmarkInput,
        *,
        owner_subject: str,
        service_principal: str,
    ) -> BenchmarkInputSnapshot:
        if not owner_subject or not service_principal:
            raise ValueError("Snapshot owner and service principal are required")
        content = source.content.encode("utf-8")
        if len(content) != source.metadata.content_bytes or _digest(content) != source.digest:
            raise BenchmarkSnapshotError("Materialized input failed snapshot verification")
        existing = self.session.scalar(
            select(BenchmarkInputSnapshot).where(
                BenchmarkInputSnapshot.owner_subject == owner_subject,
                BenchmarkInputSnapshot.resolver_id == source.resolver,
                BenchmarkInputSnapshot.source_reference == source.reference,
                BenchmarkInputSnapshot.source_version == source.version,
                BenchmarkInputSnapshot.digest == source.digest,
            )
        )
        if existing is not None:
            self.read_verified(existing.id, owner_subject=owner_subject)
            return existing
        blob_reference = self.store.put(digest=source.digest, content=content)
        snapshot = BenchmarkInputSnapshot(
            id=uuid4(),
            digest=source.digest,
            source_version=source.version,
            content_type=source.metadata.content_type,
            content_bytes=len(content),
            resolver_id=source.resolver,
            source_reference=source.reference,
            sanitized_provenance=source.provenance.model_dump(mode="json"),
            owner_subject=owner_subject,
            service_principal=service_principal,
            blob_reference=blob_reference,
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def freeze_plan(
        self,
        materialized: MaterializedBenchmarkPlanInputs,
        *,
        owner_subject: str,
        service_principal: str,
    ) -> Mapping[str, UUID]:
        """Freeze every case in the caller transaction before job creation."""

        snapshots: dict[str, UUID] = {}
        for case in materialized.cases:
            snapshot = self.freeze_input(
                case.source,
                owner_subject=owner_subject,
                service_principal=service_principal,
            )
            snapshots[case.case_id] = snapshot.id
        return snapshots

    def read_verified(self, snapshot_id: UUID, *, owner_subject: str) -> bytes:
        snapshot = self.session.scalar(
            select(BenchmarkInputSnapshot).where(
                BenchmarkInputSnapshot.id == snapshot_id,
                BenchmarkInputSnapshot.owner_subject == owner_subject,
            )
        )
        if snapshot is None:
            raise BenchmarkSnapshotError("Snapshot is unavailable")
        content = self.store.read(blob_reference=snapshot.blob_reference)
        if len(content) != snapshot.content_bytes or _digest(content) != snapshot.digest:
            raise BenchmarkSnapshotError("Stored snapshot failed integrity verification")
        return content

    @staticmethod
    def receipt(snapshot: BenchmarkInputSnapshot) -> FrozenBenchmarkInputSnapshot:
        return FrozenBenchmarkInputSnapshot(
            snapshot_id=snapshot.id,
            digest=snapshot.digest,
            source_version=snapshot.source_version,
            content_type=snapshot.content_type,
            content_bytes=snapshot.content_bytes,
            resolver_id=snapshot.resolver_id,
            source_reference=snapshot.source_reference,
            sanitized_provenance=snapshot.sanitized_provenance,
            owner_subject=snapshot.owner_subject,
            service_principal=snapshot.service_principal,
            blob_reference=snapshot.blob_reference,
            created_at=snapshot.created_at,
        )


class FrozenBenchmarkSnapshotResolver:
    """Reuse owner-scoped frozen bytes without any delegated credential or network."""

    resolver_id = "frozen_snapshot"
    reference_schema = FrozenSnapshotReference
    delegated_authorization = DelegatedAuthorizationCapability.UNSUPPORTED

    async def materialize(
        self,
        reference: BenchmarkInputReference,
        validated_reference: str,
        *,
        max_bytes: int,
        request_context: BenchmarkSourceRequestContext,
    ) -> MaterializedBenchmarkInput:
        return await asyncio.to_thread(
            self._materialize_sync,
            reference,
            validated_reference,
            max_bytes=max_bytes,
            owner_subject=request_context.principal_subject,
        )

    @staticmethod
    def _materialize_sync(
        reference: BenchmarkInputReference,
        validated_reference: str,
        *,
        max_bytes: int,
        owner_subject: str,
    ) -> MaterializedBenchmarkInput:
        with SessionLocal() as session:
            snapshot = session.scalar(
                select(BenchmarkInputSnapshot).where(
                    BenchmarkInputSnapshot.id == UUID(validated_reference),
                    BenchmarkInputSnapshot.owner_subject == owner_subject,
                )
            )
            if snapshot is None:
                raise BenchmarkSourceError(
                    "missing_source", "Frozen benchmark input was not found"
                )
            content_bytes = BenchmarkSnapshotRepository(
                session, configured_benchmark_snapshot_store()
            ).read_verified(snapshot.id, owner_subject=owner_subject)
            if len(content_bytes) > max_bytes:
                raise BenchmarkSourceError(
                    "oversize_payload", "Benchmark input exceeds the materialization limit"
                )
            try:
                content = content_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise BenchmarkSourceError(
                    "source_unavailable", "Frozen benchmark input is unavailable"
                ) from exc
            provenance = BenchmarkSourceProvenance(
                resolver=FrozenBenchmarkSnapshotResolver.resolver_id,
                reference=validated_reference,
                version=snapshot.source_version,
                digest=snapshot.digest,
            )
            return MaterializedBenchmarkInput(
                resolver=provenance.resolver,
                reference=provenance.reference,
                version=provenance.version,
                digest=provenance.digest,
                content=content,
                metadata=BenchmarkSourceMetadata(
                    content_type=snapshot.content_type,
                    content_bytes=snapshot.content_bytes,
                ),
                provenance=provenance,
            )


async def materialize_and_freeze_plan_inputs(
    plan: ResolvedBenchmarkPlan,
    catalog: BenchmarkInputResolverCatalog,
    snapshots: BenchmarkSnapshotRepository,
    *,
    request_context: BenchmarkSourceRequestContext,
    service_principal: str,
    max_submission_bytes: int,
) -> Mapping[str, UUID]:
    """Cross the delegated-to-durable boundary before a job may be created."""

    materialized = await materialize_plan_inputs(
        plan,
        catalog,
        request_context=request_context,
        max_submission_bytes=max_submission_bytes,
    )
    return snapshots.freeze_plan(
        materialized,
        owner_subject=request_context.principal_subject,
        service_principal=service_principal,
    )


__all__ = [
    "BenchmarkSnapshotError",
    "BenchmarkSnapshotRepository",
    "BenchmarkSnapshotStore",
    "FileSystemBenchmarkSnapshotStore",
    "FrozenBenchmarkInputSnapshot",
    "FrozenBenchmarkSnapshotResolver",
    "FrozenSnapshotReference",
    "S3BenchmarkSnapshotStore",
    "configured_benchmark_snapshot_store",
    "materialize_and_freeze_plan_inputs",
]
