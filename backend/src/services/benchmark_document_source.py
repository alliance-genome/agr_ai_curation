"""AI Curation persisted-document adapter for benchmark input resolution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, RootModel

from src.lib.benchmarks.input_resolvers import (
    BenchmarkSourceError,
    BenchmarkSourceMetadata,
    BenchmarkSourceProvenance,
    MaterializedBenchmarkInput,
)
from src.lib.benchmarks.models import BenchmarkInputReference
from src.models.sql.database import SessionLocal
from src.models.sql.user import User
from src.services.document_access import require_owned_document


class LocalDocumentReference(RootModel[str]):
    """Canonical UUID reference for one persisted AI Curation document."""

    root: Annotated[
        str,
        Field(
            pattern=(
                r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
                r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
            )
        ),
    ]


@dataclass(frozen=True)
class LocalDocumentSourceRecord:
    reference: str
    version: str
    title: str
    content_path: str


LocalDocumentLoader = Callable[[str, str], LocalDocumentSourceRecord]


def load_owned_local_document(
    reference: str,
    principal_subject: str,
) -> LocalDocumentSourceRecord:
    """Load only immutable extracted-artifact metadata owned by the principal."""

    if not principal_subject:
        raise BenchmarkSourceError(
            "forbidden_source", "Authenticated principal cannot access this document"
        )
    with SessionLocal() as session:
        owner = session.query(User).filter(User.auth_sub == principal_subject).one_or_none()
        if owner is None:
            raise BenchmarkSourceError(
                "forbidden_source", "Authenticated principal cannot access this document"
            )
        try:
            document = require_owned_document(session, UUID(reference), owner.id)
        except HTTPException as exc:
            code = "forbidden_source" if exc.status_code == 403 else "source_unavailable"
            message = (
                "Authenticated principal cannot access this document"
                if code == "forbidden_source"
                else "Local benchmark document is unavailable"
            )
            raise BenchmarkSourceError(code, message) from exc
        if document.processing_completed_at is None or not document.processed_json_path:
            raise BenchmarkSourceError(
                "source_unavailable",
                "Local benchmark document has no versioned extracted content",
            )
        return LocalDocumentSourceRecord(
            reference=str(document.id),
            version=document.processing_completed_at.isoformat(),
            title=document.title or document.filename,
            content_path=document.processed_json_path,
        )


class LocalDocumentResolver:
    """Resolve owner-scoped extracted documents without network access."""

    resolver_id = "local_document"
    reference_schema = LocalDocumentReference

    def __init__(
        self,
        *,
        storage_root_provider: Callable[[], Path],
        document_loader: LocalDocumentLoader = load_owned_local_document,
    ) -> None:
        self._storage_root_provider = storage_root_provider
        self._document_loader = document_loader

    async def materialize(
        self,
        reference: BenchmarkInputReference,
        validated_reference: str,
        *,
        max_bytes: int,
        principal_subject: str,
    ) -> MaterializedBenchmarkInput:
        record = self._document_loader(validated_reference, principal_subject)
        storage_root = self._storage_root_provider().expanduser().resolve(strict=False)
        owner_root = (storage_root / principal_subject).resolve(strict=False)
        content_path = (storage_root / record.content_path).resolve(strict=False)
        if not owner_root.is_relative_to(storage_root) or not content_path.is_relative_to(
            owner_root
        ):
            raise BenchmarkSourceError(
                "source_unavailable", "Local benchmark document artifact is unavailable"
            )
        try:
            with content_path.open("rb") as handle:
                payload = handle.read(max_bytes + 1)
        except OSError as exc:
            raise BenchmarkSourceError(
                "source_unavailable", "Local benchmark document artifact is unavailable"
            ) from exc
        if len(payload) > max_bytes:
            raise BenchmarkSourceError(
                "oversize_payload", "Benchmark input exceeds the materialization limit"
            )
        try:
            content = payload.decode("utf-8")
            extracted = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BenchmarkSourceError(
                "source_unavailable", "Local extracted content is not valid UTF-8 JSON"
            ) from exc
        if not isinstance(extracted, list):
            raise BenchmarkSourceError(
                "source_unavailable", "Local extracted content has an invalid shape"
            )
        digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        provenance = BenchmarkSourceProvenance(
            resolver=self.resolver_id,
            reference=record.reference,
            version=record.version,
            digest=digest,
        )
        return MaterializedBenchmarkInput(
            resolver=self.resolver_id,
            reference=record.reference,
            version=record.version,
            digest=digest,
            content=content,
            metadata=BenchmarkSourceMetadata(
                content_type="application/json",
                content_bytes=len(payload),
                title=record.title,
            ),
            provenance=provenance,
        )


__all__ = [
    "LocalDocumentReference",
    "LocalDocumentResolver",
    "LocalDocumentSourceRecord",
    "load_owned_local_document",
]
