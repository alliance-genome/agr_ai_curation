"""Prepare an isolated frozen paper using the normal document ingestion path.

This is an internal preparation operation, not a job admission API. The caller
must authorize the curator and durably record the fresh document ID before
calling it, fence its lease, and account for preparation separately from target
invocations. An uncertain/failed preparation must not be automatically replayed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import Field

from src.config import get_pdf_storage_path
from src.lib.benchmarks.document_inputs import decode_frozen_document
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.models import FrozenStrictModel
from src.lib.document_sources.ingestion import (
    _sync_sql_document_status,
    index_owned_document_elements,
)
from src.lib.storage_permissions import ensure_writable_directory
from src.lib.weaviate_client.documents import create_document
from src.models.document import DocumentMetadata, PDFDocument as VectorDocument
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.user import User
from src.models.strategy import ChunkingStrategy


class PreparedBenchmarkDocument(FrozenStrictModel):
    document_id: UUID
    snapshot_digest: str
    source_path: str
    processed_json_path: str
    processed_json_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    chunk_count: int = Field(gt=0)


def _write_artifacts(
    root: Path, document_id: UUID, content: bytes, elements: list[dict[str, Any]],
) -> tuple[str, str, str]:
    # Paths contain only a fixed namespace and server-generated UUID, never a
    # source filename, provider username, or subject from document contents.
    directory = ensure_writable_directory(root / "benchmark_documents" / str(document_id))
    source = directory / "source"
    processed = directory / "elements.json"
    with source.open("xb") as stream:
        stream.write(content)
    encoded = json.dumps(elements, ensure_ascii=False).encode("utf-8")
    with processed.open("xb") as stream:
        stream.write(encoded)
    return (
        str(source.relative_to(root)), str(processed.relative_to(root)),
        f"sha256:{hashlib.sha256(encoded).hexdigest()}",
    )


def verify_prepared_document(
    receipt: PreparedBenchmarkDocument, curator: BenchmarkCuratorContext,
) -> None:
    """Reject missing, replaced or changed prepared artifacts; never repair them.

    This verifies SQL ownership/status and file integrity. Vector-library
    mutation isolation is a separate boundary, not established by file hashes.
    """
    prefix = Path("benchmark_documents") / str(receipt.document_id)
    if (
        receipt.source_path != str(prefix / "source")
        or receipt.processed_json_path != str(prefix / "elements.json")
    ):
        raise ValueError("Prepared document artifact paths do not match its identity")
    with SessionLocal() as session:
        user = session.get(User, curator.db_user_id)
        document = session.get(PDFDocument, receipt.document_id)
        if (
            user is None or user.is_active is not True or user.auth_sub != curator.subject
            or document is None or document.user_id != curator.db_user_id
            or document.status != "completed" or document.viewer_mode != "benchmark_frozen"
            or document.file_path != receipt.source_path
            or document.processed_json_path != receipt.processed_json_path
        ):
            raise PermissionError("Prepared document is unavailable for its curator")
        source_size = document.file_size
    root = Path(get_pdf_storage_path()).resolve(strict=True)
    for relative_path, digest in (
        (receipt.source_path, receipt.snapshot_digest),
        (receipt.processed_json_path, receipt.processed_json_digest),
    ):
        path = root / relative_path
        if path.resolve(strict=True) != path or not path.is_file():
            raise ValueError("Prepared document artifact is not an isolated regular file")
        with path.open("rb") as stream:
            actual = f"sha256:{hashlib.file_digest(stream, 'sha256').hexdigest()}"
        if actual != digest or (relative_path == receipt.source_path and path.stat().st_size != source_size):
            raise ValueError("Prepared document artifact failed integrity verification")


async def prepare_frozen_document(
    *,
    document_id: UUID,
    content: bytes,
    content_type: str,
    snapshot_digest: str,
    curator: BenchmarkCuratorContext,
    weaviate_client: Any,
    stage_checkpoint: Callable[[str], Awaitable[None]],
) -> PreparedBenchmarkDocument:
    """Create a new owned runtime copy; never fetch or reuse a source document.

    Hierarchy, figure-location and embedding calls are the existing normal
    preparation pipeline. This function must run under the caller's durable
    preparation accounting, not a comparison-arm routing override.
    """
    checksum = hashlib.sha256(content).hexdigest()
    if snapshot_digest != f"sha256:{checksum}":
        raise ValueError("Frozen document digest does not match preparation input")
    elements = decode_frozen_document(content, content_type=content_type)
    pages = [element.get("metadata", {}).get("page_number") for element in elements]
    page_count = max((page for page in pages if type(page) is int and page > 0), default=1)
    with SessionLocal() as session:
        user = session.get(User, curator.db_user_id)
        if user is None or user.is_active is not True or user.auth_sub != curator.subject:
            raise PermissionError("Frozen document requires its active curator owner")
        if session.get(PDFDocument, document_id) is not None:
            raise ValueError("Prepared document identity already exists; do not replay preparation")

    root = Path(get_pdf_storage_path())
    await stage_checkpoint("artifacts")
    source_path, processed_path, processed_digest = await asyncio.to_thread(
        _write_artifacts, root, document_id, content, elements,
    )
    filename = f"benchmark-{document_id}"
    # file_hash is a scoped duplicate-detection key (as in normal provider
    # imports), not the content checksum. The raw checksum is preserved below.
    scoped_file_hash = hashlib.sha256(
        f"benchmark:{curator.db_user_id}:{document_id}:{checksum}".encode()
    ).hexdigest()
    with SessionLocal() as session:
        session.add(PDFDocument(
            id=document_id, user_id=curator.db_user_id, filename=filename,
            file_path=source_path, file_hash=scoped_file_hash, file_size=len(content),
            page_count=page_count, processed_json_path=processed_path, source_payload_path=source_path,
            viewer_mode="benchmark_frozen", status="pending",
        ))
        session.commit()

    now = datetime.now(timezone.utc)
    try:
        await stage_checkpoint("vector_document")
        created = await create_document(curator.subject, VectorDocument(
            id=str(document_id), filename=filename, file_size=len(content),
            creation_date=now, last_accessed_date=now,
            metadata=DocumentMetadata(
                # Text inputs need not have physical pagination. Original page
                # provenance remains on each decoded element and normal chunk.
                page_count=page_count, checksum=checksum, document_type="benchmark_frozen",
                last_processed_stage="parsing",
            ),
        ))
        if not created.get("success"):
            raise RuntimeError("Frozen runtime document creation failed")
        chunk_count = await index_owned_document_elements(
            elements, document_id=str(document_id), user_id=curator.subject,
            owner_user_id=curator.db_user_id, weaviate_client=weaviate_client,
            strategy=ChunkingStrategy.get_research_strategy(),
            stage_checkpoint=stage_checkpoint,
        )
        receipt = PreparedBenchmarkDocument(
            document_id=document_id, snapshot_digest=snapshot_digest,
            source_path=source_path, processed_json_path=processed_path,
            processed_json_digest=processed_digest,
            chunk_count=chunk_count,
        )
        await stage_checkpoint("ready")
        await _sync_sql_document_status(
            str(document_id), user_id=curator.subject, owner_user_id=curator.db_user_id,
            status="completed",
        )
        return receipt
    except Exception:
        # Keep partial state at the already-recorded document identity for
        # explicit cleanup; never silently repeat paid preparation calls.
        await _sync_sql_document_status(
            str(document_id), user_id=curator.subject, owner_user_id=curator.db_user_id,
            status="failed", error_message="Frozen benchmark preparation failed",
        )
        raise
