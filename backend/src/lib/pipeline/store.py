"""Weaviate storage stage of the processing pipeline."""

import logging
import json
import os
import time
from typing import List, Dict, Any, Optional, Sequence, Union
from datetime import datetime, timezone
import asyncio
import uuid
import hashlib
from weaviate.classes.query import Filter

from ..exceptions import StorageError, CollectionNotFoundError, BatchInsertError
from src.lib.observability.runtime import report_runtime_exception, sanitized_runtime_error
from src.models.chunk import DocumentChunk, ChunkMetadata
from ..weaviate_client.documents import update_document_status_detailed

logger = logging.getLogger(__name__)

try:
    import tiktoken
except ImportError:  # pragma: no cover - optional dependency safeguard
    tiktoken = None

def _get_required_env(name: str) -> str:
    """Read required env var and fail if missing."""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip()


def _get_required_int_env(name: str) -> int:
    """Read required integer env var and fail if invalid."""
    raw_value = _get_required_env(name)
    try:
        return int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer, got {raw_value!r}") from exc


def _get_required_bool_env(name: str) -> bool:
    """Read required boolean env var and fail if invalid."""
    raw_value = _get_required_env(name).lower()
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"Environment variable {name} must be true/false, got {raw_value!r}")


def _validate_required_embedding_env_vars() -> None:
    """Validate required embedding env vars are present and coherent."""
    required_names = [
        "EMBEDDING_MODEL",
        "EMBEDDING_TOKEN_PREFLIGHT_ENABLED",
        "EMBEDDING_MODEL_TOKEN_LIMIT",
        "EMBEDDING_TOKEN_SAFETY_MARGIN",
        "CONTENT_PREVIEW_CHARS",
    ]
    missing = [name for name in required_names if os.getenv(name) in (None, "")]
    if missing:
        raise RuntimeError(
            "Missing required embedding environment variables: " + ", ".join(sorted(missing))
        )


def _get_embedding_model_name() -> str:
    """Resolve embedding model from environment."""
    return _get_required_env("EMBEDDING_MODEL")


_validate_required_embedding_env_vars()
EMBEDDING_MODEL_NAME = _get_embedding_model_name()
EMBEDDING_MODEL_TOKEN_LIMIT = _get_required_int_env("EMBEDDING_MODEL_TOKEN_LIMIT")
TOKEN_SAFETY_MARGIN = _get_required_int_env("EMBEDDING_TOKEN_SAFETY_MARGIN")
MAX_PREVIEW_CHARS = _get_required_int_env("CONTENT_PREVIEW_CHARS")  # ~400 tokens for reranking
TOKEN_PREFLIGHT_ENABLED = _get_required_bool_env("EMBEDDING_TOKEN_PREFLIGHT_ENABLED")
WEAVIATE_BATCH_RPM = int(os.getenv("WEAVIATE_BATCH_REQUESTS_PER_MINUTE", "5000"))

if EMBEDDING_MODEL_TOKEN_LIMIT <= 0:
    raise RuntimeError("EMBEDDING_MODEL_TOKEN_LIMIT must be > 0")
if TOKEN_SAFETY_MARGIN < 0:
    raise RuntimeError("EMBEDDING_TOKEN_SAFETY_MARGIN must be >= 0")
if MAX_PREVIEW_CHARS <= 0:
    raise RuntimeError("CONTENT_PREVIEW_CHARS must be > 0")
if WEAVIATE_BATCH_RPM <= 0:
    raise RuntimeError("WEAVIATE_BATCH_REQUESTS_PER_MINUTE must be > 0")
if TOKEN_SAFETY_MARGIN >= EMBEDDING_MODEL_TOKEN_LIMIT:
    raise RuntimeError(
        "EMBEDDING_TOKEN_SAFETY_MARGIN must be less than EMBEDDING_MODEL_TOKEN_LIMIT"
    )

TOKEN_HARD_LIMIT = EMBEDDING_MODEL_TOKEN_LIMIT - TOKEN_SAFETY_MARGIN

if TOKEN_PREFLIGHT_ENABLED and tiktoken is None:
    raise RuntimeError(
        "EMBEDDING_TOKEN_PREFLIGHT_ENABLED=true requires tiktoken to be installed"
    )

def _load_tiktoken_encoder():
    try:
        return tiktoken.encoding_for_model(EMBEDDING_MODEL_NAME)
    except Exception:
        try:
            encoder = tiktoken.get_encoding("cl100k_base")
            logger.warning(
                "No direct tiktoken encoding for model %s; using cl100k_base fallback",
                EMBEDDING_MODEL_NAME,
            )
            return encoder
        except Exception as exc:  # pragma: no cover - encoder init is environment-specific
            raise RuntimeError(
                f"Unable to initialize tiktoken encoder for EMBEDDING_MODEL={EMBEDDING_MODEL_NAME}"
            ) from exc


_tiktoken_encoder = None
if TOKEN_PREFLIGHT_ENABLED and tiktoken is not None:
    _tiktoken_encoder = _load_tiktoken_encoder()
# Loaded for counting only; it never switches the insert preflight on.
_counting_encoder = None


def embedding_token_encoder():
    """The embedding model's token encoder, loaded on first use when preflight is off.

    Callers that measure text against ``TOKEN_HARD_LIMIT`` (the file checks) need
    it whatever ``EMBEDDING_TOKEN_PREFLIGHT_ENABLED`` says.
    """

    global _counting_encoder
    if _tiktoken_encoder is not None:
        return _tiktoken_encoder
    if _counting_encoder is None:
        if tiktoken is None:
            raise RuntimeError("Counting embedding tokens requires tiktoken to be installed")
        _counting_encoder = _load_tiktoken_encoder()
    return _counting_encoder


if not TOKEN_PREFLIGHT_ENABLED:
    logger.warning("Embedding token preflight is disabled via EMBEDDING_TOKEN_PREFLIGHT_ENABLED")


def generate_deterministic_uuid(document_id: str, chunk_index: int, content: str) -> str:
    """Generate deterministic UUID based on content.

    Args:
        document_id: Document ID
        chunk_index: Index of the chunk
        content: Content of the chunk

    Returns:
        Deterministic UUID string
    """
    # UUIDs identify Weaviate objects and canonical evidence internally. Model
    # tools select existing zero-based document-local chunk/span indexes instead:
    # reproducing a long UUID caused a malformed production tool argument. The
    # backend binds source generation and issued spans, and verifies offsets and
    # hashes; numeric references do not remove those provenance requirements.
    unique_string = f"{document_id}:{chunk_index}:{content[:100]}"
    hash_obj = hashlib.sha256(unique_string.encode())
    # Convert first 16 bytes of hash to UUID format
    uuid_bytes = hash_obj.digest()[:16]
    return str(uuid.UUID(bytes=uuid_bytes))


async def store_to_weaviate(
    chunks: Sequence[Union[DocumentChunk, Dict[str, Any]]],
    document_id: str,
    weaviate_client: Optional[Any],
    user_id: str,
    *,
    chunk_range: tuple[int, int],
) -> Dict[str, Any]:
    """Store one file's chunks with embeddings to Weaviate and verify that file's range.

    The paper's document object (status and summed chunk count) is updated
    separately, once all of a new paper's files are stored
    (``update_document_metadata``).

    Args:
        chunks: Sequence of chunks containing text and metadata
        document_id: Document UUID
        weaviate_client: WeaviateConnection instance
        user_id: User identifier for tenant scoping (required, FR-011, FR-014)
        chunk_range: The file's reserved ``chunkIndex`` range, first and last inclusive

    Returns:
        Storage statistics dictionary

    Raises:
        StorageError: If storage fails
        ValueError: If user_id is None (required for tenant scoping)
    """
    return await _store_owned(
        chunks, document_id, weaviate_client, user_id, chunk_range=chunk_range, write_paper_status=True,
    )


async def store_added_file_pieces(
    chunks: Sequence[Union[DocumentChunk, Dict[str, Any]]],
    document_id: str,
    weaviate_client: Optional[Any],
    user_id: str,
    *,
    chunk_range: tuple[int, int],
) -> Dict[str, Any]:
    """Store the pieces of a file added to a finished paper, never touching the paper's own object.

    The paper is and stays completed while files are added to it (KANBAN-1976 /
    ALL-1460, P7c1): its search-index object's processing and embedding status are
    not written, before, during or after, whatever happens to the add. Pieces,
    range verification and cancellation draining are the same as ``store_to_weaviate``.
    """
    return await _store_owned(
        chunks, document_id, weaviate_client, user_id, chunk_range=chunk_range, write_paper_status=False,
    )


async def _store_owned(
    chunks: Sequence[Union[DocumentChunk, Dict[str, Any]]],
    document_id: str,
    weaviate_client: Optional[Any],
    user_id: str,
    *,
    chunk_range: tuple[int, int],
    write_paper_status: bool,
) -> Dict[str, Any]:
    # Executor writes cannot be stopped by cancelling their awaiting coroutine.
    # Keep ownership through chunk verification and metadata finalization so the
    # caller cannot expose a terminal job (and permit retry/delete) too early.
    storage = asyncio.create_task(
        _store_to_weaviate(chunks, document_id, weaviate_client, user_id, chunk_range, write_paper_status)
    )
    try:
        return await asyncio.shield(storage)
    except asyncio.CancelledError:
        async def finish_cancelled_storage() -> None:
            try:
                await storage
            except Exception:
                # The storage path already reports failures. Preserve cancellation
                # while still waiting for its writes and failure handling to end.
                logger.warning("Storage failed while draining cancelled document %s", document_id)
            # Do not advertise a cancelled import as completed. The paper's
            # summed chunk count is written only when a new paper finishes;
            # the caller owns SQL/job terminal status. A file added to a finished
            # paper never changes the paper's status.
            if write_paper_status:
                await update_document_status_detailed(
                    document_id, user_id,
                    processing_status="failed", embedding_status="failed",
                )

        finalizer = asyncio.create_task(finish_cancelled_storage())
        while not finalizer.done():
            try:
                await asyncio.shield(finalizer)
            except asyncio.CancelledError:
                # A second cancellation must not detach the executor writer.
                continue
        finalizer.result()
        raise


async def _store_to_weaviate(
    chunks: Sequence[Union[DocumentChunk, Dict[str, Any]]],
    document_id: str,
    weaviate_client: Optional[Any],
    user_id: str,
    chunk_range: tuple[int, int],
    write_paper_status: bool,
) -> Dict[str, Any]:
    """Own the complete chunk write for one file, including failure handling."""
    if not chunks:
        raise StorageError("No chunks to store")

    # T036: Validate user_id is provided (required for tenant scoping)
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped storage (FR-011, FR-014)")

    # Use provided client or create default
    if weaviate_client is None:
        from ..weaviate_helpers import get_connection
        weaviate_client = get_connection()

    logger.info('Starting Weaviate storage for %s chunks', len(chunks))

    # Normalise chunks to plain dictionaries for storage
    prepared_chunks: List[Dict[str, Any]] = []
    for chunk in chunks:
        if isinstance(chunk, DocumentChunk):
            metadata = chunk.metadata.model_dump() if chunk.metadata else {}
            doc_items = [item.model_dump() for item in chunk.doc_items]
            if doc_items:
                metadata.setdefault("doc_items", doc_items)
            prepared_chunks.append(
                {
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.content,
                    "element_type": chunk.element_type,
                    "page_number": chunk.page_number,
                    "section_title": chunk.section_title,
                    "section_path": chunk.section_path,
                    # Hierarchy fields from LLM-based section resolution
                    "parent_section": chunk.parent_section,
                    "subsection": chunk.subsection,
                    "is_top_level": chunk.is_top_level,
                    "metadata": metadata,
                    "doc_items": doc_items,
                }
            )
        elif isinstance(chunk, dict):
            chunk_copy = dict(chunk)
            metadata = chunk_copy.get("metadata") or {}
            if isinstance(metadata, ChunkMetadata):
                metadata = metadata.model_dump()
                chunk_copy["metadata"] = metadata
            doc_items = chunk_copy.get("doc_items")
            if not doc_items and isinstance(metadata, dict):
                doc_items = metadata.get("doc_items")
            if doc_items and isinstance(metadata, dict):
                metadata.setdefault("doc_items", doc_items)
            prepared_chunks.append(chunk_copy)
        else:
            raise StorageError(f"Unsupported chunk payload type: {type(chunk)!r}")

    # Update document status to indicate embeddings are in progress within Weaviate
    if write_paper_status:
        await update_document_status_detailed(document_id, user_id, embedding_status="processing")

    try:
        # Store chunks using actual Weaviate client
        results = await store_chunks_to_weaviate(
            prepared_chunks,
            document_id,
            weaviate_client,
            user_id,
            chunk_range=chunk_range,
        )

        if results["failed_count"] > 0:
            raise BatchInsertError(
                f"Chunk storage failed for {results['failed_count']} chunks",
                failed_objects=results.get("failed_ids", []),
            )

        stats = {
            "total_chunks": len(prepared_chunks),
            "stored_chunks": results["stored_count"],
            "failed_chunks": results["failed_count"],
            "storage_timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

        logger.info('Successfully stored %s chunks to Weaviate', results['stored_count'])
        return stats

    except Exception as e:
        try:
            if write_paper_status:
                await update_document_status_detailed(document_id, user_id, embedding_status="failed")
        except Exception as status_err:  # pragma: no cover - best-effort status update
            logger.warning("Failed to mark document %s embedding status as failed: %s", document_id, status_err)
        error_msg = f"Weaviate storage failed: {str(e)}"
        logger.error(error_msg)
        raise StorageError(error_msg) from e


async def store_chunks_to_weaviate(
    chunks: Sequence[Dict[str, Any]],
    document_id: str,
    weaviate_client: Any,
    user_id: str,
    *,
    chunk_range: tuple[int, int],
) -> Dict[str, Any]:
    """Store one file's chunks to Weaviate using the v4 client.

    Args:
        chunks: Prepared chunk dictionaries ready for storage
        document_id: Document UUID
        weaviate_client: WeaviateConnection instance
        user_id: User identifier for tenant scoping (required, FR-011, FR-014)
        chunk_range: The file's reserved ``chunkIndex`` range, first and last inclusive

    Returns:
        Storage result statistics

    Raises:
        ValueError: If user_id is None (required for tenant scoping)
        CollectionNotFoundError: If tenant-scoped collection not found
        BatchInsertError: If batch insertion fails
    """
    # T036: Validate user_id is provided (required for tenant scoping)
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped chunk storage (FR-011, FR-014)")

    stored_count = 0
    failed_count = 0
    stored_ids: List[str] = []
    candidate_uuids: List[str] = []

    def _bool_to_str(val: Any) -> Optional[str]:
        """Convert Python bool to string for TEXT schema fields."""
        if val is None:
            return None
        return "true" if val else "false"

    def _prepare_chunk_for_insert(chunk: Dict[str, Any]) -> tuple[int, str, Dict[str, Any]]:
        """Build Weaviate properties and deterministic UUID for one chunk."""
        chunk_index = chunk.get("chunk_index", 0)
        content = chunk.get("content", "")

        if _tiktoken_encoder is not None and content:
            try:
                token_count = len(_tiktoken_encoder.encode(content))
                if token_count > TOKEN_HARD_LIMIT:
                    raise BatchInsertError(
                        (
                            f"Chunk {chunk_index} for document {document_id} has {token_count} tokens "
                            f"(limit: {TOKEN_HARD_LIMIT})"
                        ),
                        failed_objects=[{"chunk_index": chunk_index, "token_count": token_count}],
                    )
            except BatchInsertError:
                raise
            except Exception as token_err:
                logger.warning(
                    "Token preflight check failed for chunk %s. Proceeding without token preflight: %s",
                    chunk_index,
                    token_err,
                )

        content_preview = content[:MAX_PREVIEW_CHARS]
        if len(content) > MAX_PREVIEW_CHARS:
            content_preview = content_preview.rsplit(" ", 1)[0] + "..."

        source_metadata = chunk.get("metadata", {}) or {}
        if isinstance(source_metadata, str):
            try:
                source_metadata = json.loads(source_metadata)
            except json.JSONDecodeError:
                source_metadata = {"raw_metadata": source_metadata}

        doc_items = chunk.get("doc_items")
        if not doc_items and isinstance(source_metadata, dict):
            doc_items = source_metadata.get("doc_items")
        if isinstance(source_metadata, dict) and doc_items:
            source_metadata.setdefault("doc_items", doc_items)

        is_top_level_val = (
            chunk.get("is_top_level")
            if chunk.get("is_top_level") is not None
            else source_metadata.get("is_top_level")
        )

        properties = {
            "documentId": document_id,
            "chunkIndex": chunk_index,
            "content": content,
            "contentPreview": content_preview,
            "elementType": chunk.get("element_type", "unknown"),
            "pageNumber": chunk.get("page_number"),
            "sectionTitle": chunk.get("section_title") or source_metadata.get("section_title"),
            "sectionPath": chunk.get("section_path") or source_metadata.get("section_path"),
            # Hierarchy fields from LLM-based section resolution
            "parentSection": chunk.get("parent_section") or source_metadata.get("parent_section"),
            "subsection": chunk.get("subsection") or source_metadata.get("subsection"),
            "isTopLevel": _bool_to_str(is_top_level_val),
            "contentType": source_metadata.get("content_type"),
            "metadata": json.dumps(source_metadata, default=str),
            "embeddingTimestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

        if doc_items:
            properties["docItemProvenance"] = json.dumps(doc_items, default=str)

        chunk_uuid = generate_deterministic_uuid(document_id, chunk_index, content)
        return chunk_index, chunk_uuid, properties

    range_start, range_end = chunk_range
    if range_start < 0 or range_end < range_start or range_end - range_start + 1 != len(chunks):
        raise StorageError("The file's chunk range does not match its chunk count")
    for chunk in chunks:
        chunk_index = chunk.get("chunk_index")
        if type(chunk_index) is not int or not range_start <= chunk_index <= range_end:
            raise StorageError("A chunk lies outside its file's reserved range")

    def _verify_document_chunks(collection: Any, expected_count: int) -> int:
        """Strictly verify the persisted chunk count inside this file's range."""
        # Weaviate can be eventually consistent right after batch flush.
        time.sleep(2)
        result = collection.aggregate.over_all(
            filters=(
                Filter.by_property("documentId").equal(document_id)
                & Filter.by_property("chunkIndex").greater_or_equal(range_start)
                & Filter.by_property("chunkIndex").less_or_equal(range_end)
            ),
            total_count=True,
        )
        persisted_count = result.total_count
        if type(persisted_count) is not int:
            raise BatchInsertError(
                f"Chunk verification for document {document_id} returned no count",
                failed_objects=[{"expected": expected_count}],
            )
        logger.info(
            "Chunk verification for document %s: expected=%d, persisted=%d",
            document_id,
            expected_count,
            persisted_count,
        )
        if persisted_count != expected_count:
            logger.error(
                "Chunk verification mismatch for document %s: expected=%d, persisted=%d",
                document_id,
                expected_count,
                persisted_count,
            )
            _cleanup_chunk_uuids(collection, candidate_uuids)
            raise BatchInsertError(
                (
                    "Chunk verification failed for document "
                    f"{document_id}: expected={expected_count}, persisted={persisted_count}"
                ),
                failed_objects=[{"expected": expected_count, "persisted": persisted_count}],
            )
        return persisted_count

    def _cleanup_chunk_uuids(collection: Any, candidate_uuids: List[str]) -> None:
        """Best-effort cleanup to avoid partial persistence after batch failures."""
        deleted_count = 0
        for chunk_uuid in candidate_uuids:
            try:
                collection.data.delete_by_id(chunk_uuid)
                deleted_count += 1
            except Exception:
                # Ignore missing IDs or delete failures; this is compensating cleanup.
                continue
        if deleted_count > 0:
            logger.warning(
                "Cleaned up %d potentially partial chunk objects for document %s",
                deleted_count,
                document_id,
            )

    def sync_store():
        """Synchronous function to store chunks in Weaviate."""
        nonlocal stored_count, failed_count, stored_ids, candidate_uuids

        try:
            # Connect to Weaviate
            with weaviate_client.session() as client:
                # Get tenant-scoped collection (FR-011: user-specific data isolation)
                from ..weaviate_helpers import get_user_collections
                try:
                    chunk_collection, _ = get_user_collections(client, user_id)
                    collection = chunk_collection
                except Exception as e:
                    # Fail fast - collection should exist from startup
                    error_msg = f"DocumentChunk collection not found. Database not properly initialized: {e}"
                    logger.error(error_msg)
                    raise CollectionNotFoundError(error_msg) from e

                prepared_batch: List[tuple[int, str, Dict[str, Any]]] = []
                for chunk in chunks:
                    prepared_batch.append(_prepare_chunk_for_insert(chunk))
                candidate_uuids = [chunk_uuid for _, chunk_uuid, _ in prepared_batch]

                with collection.batch.rate_limit(requests_per_minute=WEAVIATE_BATCH_RPM) as batch:
                    for chunk_index, chunk_uuid, properties in prepared_batch:
                        try:
                            batch.add_object(properties=properties, uuid=chunk_uuid)
                        except Exception as add_err:
                            logger.error(
                                "Failed to add chunk %d (uuid=%s) to batch for document %s: %s",
                                chunk_index,
                                chunk_uuid,
                                document_id,
                                add_err,
                            )
                            _cleanup_chunk_uuids(collection, candidate_uuids)
                            raise BatchInsertError(
                                f"Failed to add chunk {chunk_index} to batch",
                                failed_objects=[{"chunk_index": chunk_index, "error": str(add_err)}],
                            ) from add_err

                batch_error_count = 0
                if hasattr(batch, "number_errors"):
                    try:
                        batch_error_count = int(batch.number_errors)
                    except Exception:
                        batch_error_count = 0
                if batch_error_count > 0:
                    logger.error(
                        "Batch reported %d errors for document %s",
                        batch_error_count,
                        document_id,
                    )
                failed_objects = getattr(batch, "failed_objects", None)
                if isinstance(failed_objects, list) and failed_objects:
                    failed_details = []
                    for failed_object in failed_objects[:10]:
                        failed_details.append(str(failed_object))
                    logger.error(
                        "Batch failed objects for document %s (showing first 10): %s",
                        document_id,
                        "; ".join(failed_details),
                    )
                    _cleanup_chunk_uuids(collection, candidate_uuids)
                    raise BatchInsertError(
                        f"Batch insert failed for {len(failed_objects)} objects",
                        failed_objects=list(failed_objects),
                    )

                logger.info("Batch insert sent %d chunks, verifying persistence...", len(prepared_batch))
                persisted_count = _verify_document_chunks(collection, expected_count=len(prepared_batch))
                stored_ids = [chunk_uuid for _, chunk_uuid, _ in prepared_batch]
                stored_count = persisted_count
                failed_count = 0
                logger.info(
                    "Batch insert verified: %d/%d chunks persisted for document %s",
                    persisted_count,
                    len(prepared_batch),
                    document_id,
                )

        except Exception as e:
            logger.error('Error in sync_store: %s', e)
            raise

    # Run synchronous storage in async context
    await asyncio.get_event_loop().run_in_executor(None, sync_store)

    return {
        "stored_count": stored_count,
        "failed_count": failed_count,
        "stored_ids": stored_ids,
        "failed_ids": [],
    }


async def update_document_metadata(
    document_id: str,
    stats: Dict[str, Any],
    weaviate_client: Any,
    user_id: str
) -> None:
    """Update the paper's document object with its summed chunk count and status.

    Called once after every file of a new paper is stored, and after files are
    added to an existing paper.

    Args:
        document_id: Document UUID
        stats: Processing statistics
        weaviate_client: WeaviateConnection instance
        user_id: User identifier for tenant scoping (required, FR-011, FR-014)

    Raises:
        ValueError: If user_id is None (required for tenant scoping)
    """
    # T036: Validate user_id is provided (required for tenant scoping)
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped metadata updates (FR-011, FR-014)")
    logger.info('Updating metadata for document %s', document_id)

    stored_chunks = int(stats.get("stored_chunks", 0) or 0)
    failed_chunks = int(stats.get("failed_chunks", 0) or 0)
    if failed_chunks > 0:
        embedding_status = "failed"
    else:
        embedding_status = "completed"
    processing_status = "completed" if stored_chunks > 0 else "failed"

    def sync_update():
        """Synchronous function to update document metadata."""
        try:
            from ..weaviate_helpers import get_user_collections

            with weaviate_client.session(caller_reports_failure=True) as client:
                _, pdf_collection = get_user_collections(client, user_id)
                pdf_collection.data.update(
                    uuid=document_id,
                    properties={
                        "chunkCount": stored_chunks,
                        "vectorCount": stored_chunks,
                        "processingStatus": processing_status,
                        "embeddingStatus": embedding_status,
                    },
                )
            logger.info('Successfully updated metadata for document %s', document_id)
        except Exception as exc:
            error = sanitized_runtime_error("Final index metadata write failed")
            logger.warning("%s", error, extra={"sentry_skip_event": True})
            try:
                report_runtime_exception(
                    error,
                    component="document_pipeline",
                    operation="pipeline_final_index_metadata_write_failed",
                    context={"exception_type": type(exc).__name__},
                )
            except Exception:
                logger.warning("Final index metadata failure reporting unavailable", extra={"sentry_skip_event": True})

    # Run synchronous update in async context
    await asyncio.get_event_loop().run_in_executor(None, sync_update)


async def update_document_piece_count(
    document_id: str, piece_count: int, weaviate_client: Any, user_id: str,
) -> None:
    """Write only the paper object's piece counts (after files are added to a finished paper).

    The object's processing and embedding status are left as they are. Failures raise.
    """
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped metadata updates")

    def sync_update() -> None:
        from ..weaviate_helpers import get_user_collections

        with weaviate_client.session() as client:
            _, pdf_collection = get_user_collections(client, user_id)
            pdf_collection.data.update(
                uuid=document_id, properties={"chunkCount": piece_count, "vectorCount": piece_count},
            )

    await asyncio.to_thread(sync_update)


async def update_processing_status(
    document_id: str,
    status: str
) -> None:
    """Update the processing status of a document.

    Args:
        document_id: Document UUID
        status: New status value
    """
    logger.info('Updating document %s status to: %s', document_id, status)
    # This will be integrated with tracker module
