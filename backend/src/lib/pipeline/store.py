"""Weaviate storage stage of the processing pipeline."""

import logging
import json
from typing import List, Dict, Any, Optional, Sequence, Union
from datetime import datetime, timezone
import asyncio
import uuid
import hashlib

from ..exceptions import StorageError, CollectionNotFoundError, BatchInsertError
from src.models.chunk import DocumentChunk, ChunkMetadata
from ..weaviate_client.documents import update_document_status_detailed

logger = logging.getLogger(__name__)


def generate_deterministic_uuid(document_id: str, chunk_index: int, content: str) -> str:
    """Generate deterministic UUID based on content.

    Args:
        document_id: Document ID
        chunk_index: Index of the chunk
        content: Content of the chunk

    Returns:
        Deterministic UUID string
    """
    unique_string = f"{document_id}:{chunk_index}:{content[:100]}"
    hash_obj = hashlib.sha256(unique_string.encode())
    # Convert first 16 bytes of hash to UUID format
    uuid_bytes = hash_obj.digest()[:16]
    return str(uuid.UUID(bytes=uuid_bytes))


async def store_to_weaviate(
    chunks: Sequence[Union[DocumentChunk, Dict[str, Any]]],
    document_id: str,
    weaviate_client: Optional[Any] = None,
    user_id: str = None
) -> Dict[str, Any]:
    """Store chunks with embeddings to Weaviate.

    Args:
        chunks: Sequence of chunks containing text and metadata
        document_id: Document UUID
        weaviate_client: WeaviateConnection instance
        user_id: User identifier for tenant scoping (required, FR-011, FR-014)

    Returns:
        Storage statistics dictionary

    Raises:
        StorageError: If storage fails
        ValueError: If user_id is None (required for tenant scoping)
    """
    if not chunks:
        raise StorageError("No chunks to store")

    # T036: Validate user_id is provided (required for tenant scoping)
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped storage (FR-011, FR-014)")

    # Use provided client or create default
    if weaviate_client is None:
        from ..weaviate_helpers import get_connection
        weaviate_client = get_connection()

    logger.info(f"Starting Weaviate storage for {len(chunks)} chunks")

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
    await update_document_status_detailed(document_id, user_id, embedding_status="processing")

    try:
        # Store chunks using actual Weaviate client
        results = await store_chunks_to_weaviate(
            prepared_chunks,
            document_id,
            weaviate_client,
            user_id
        )

        # Update document metadata
        stats = {
            "total_chunks": len(prepared_chunks),
            "stored_chunks": results["stored_count"],
            "failed_chunks": results["failed_count"],
            "storage_timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

        # Update document status
        await update_document_metadata(document_id, stats, weaviate_client, user_id)

        logger.info(f"Successfully stored {results['stored_count']} chunks to Weaviate")
        return stats

    except Exception as e:
        error_msg = f"Weaviate storage failed: {str(e)}"
        logger.error(error_msg)
        raise StorageError(error_msg) from e


async def store_chunks_to_weaviate(
    chunks: Sequence[Dict[str, Any]],
    document_id: str,
    weaviate_client: Any,
    user_id: str
) -> Dict[str, Any]:
    """Store chunks to Weaviate using the v4 client.

    Args:
        chunks: Prepared chunk dictionaries ready for storage
        document_id: Document UUID
        weaviate_client: WeaviateConnection instance
        user_id: User identifier for tenant scoping (required, FR-011, FR-014)

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
    stored_ids = []

    def sync_store():
        """Synchronous function to store chunks in Weaviate."""
        nonlocal stored_count, failed_count, stored_ids

        try:
            # Connect to Weaviate
            with weaviate_client.session() as client:
                # Get tenant-scoped collection (FR-011: user-specific data isolation)
                from ..weaviate_helpers import get_user_collections
                try:
                    chunk_collection, pdf_collection = get_user_collections(client, user_id)
                    collection = chunk_collection
                except Exception as e:
                    # Fail fast - collection should exist from startup
                    error_msg = f"DocumentChunk collection not found. Database not properly initialized: {e}"
                    logger.error(error_msg)
                    raise CollectionNotFoundError(error_msg) from e

                # Store chunks in batch
                batch_errors = []
                batch_response = None
                with collection.batch.dynamic() as batch:
                    for chunk in chunks:
                        chunk_index = chunk.get("chunk_index", 0)

                        # V5: No embedding check - using server-side embeddings
                        # Chunks no longer need client-side embeddings

                        # Prepare properties for Weaviate
                        content = chunk.get("content", "")
                        # V5: Prepare chunk with content preview for reranking
                        # Cross-encoders truncate at ~512 tokens. We create a preview
                        # field with first 400 tokens for reranking while keeping
                        # full content for the LLM.
                        MAX_PREVIEW_CHARS = 1600  # ~400 tokens (4 chars per token)

                        content_preview = content[:MAX_PREVIEW_CHARS]
                        if len(content) > MAX_PREVIEW_CHARS:
                            # Add ellipsis to indicate truncation
                            content_preview = content_preview.rsplit(' ', 1)[0] + '...'

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

                        # Helper to convert is_top_level bool to string for Weaviate text schema
                        def _bool_to_str(val):
                            if val is None:
                                return None
                            return "true" if val else "false"

                        is_top_level_val = chunk.get("is_top_level") if chunk.get("is_top_level") is not None else source_metadata.get("is_top_level")

                        properties = {
                            "documentId": document_id,
                            "chunkIndex": chunk_index,
                            "content": content,  # Full text for LLM
                            "contentPreview": content_preview,  # V5: Shortened for reranker
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
                            # Surface provenance explicitly for downstream hybrid search consumers
                            # Serialize doc_items to JSON string for TEXT field storage
                            properties["docItemProvenance"] = json.dumps(doc_items, default=str)

                        # Add to batch with deterministic UUID
                        try:
                            # Generate deterministic UUID based on content
                            chunk_uuid = generate_deterministic_uuid(
                                document_id,
                                chunk_index,
                                content
                            )
                            # V5: No vector parameter - Weaviate generates embeddings server-side!
                            batch.add_object(
                                properties=properties,
                                # vector=chunk.get("embedding"),  # V5: REMOVED - server-side embeddings
                                uuid=chunk_uuid
                            )
                            stored_ids.append(chunk_uuid)
                            stored_count += 1
                        except Exception as e:
                            # Fail fast with clear error message
                            error_msg = f"Failed to add chunk {chunk_index} to batch: {str(e)}"
                            logger.error(error_msg)
                            batch_errors.append({"chunk_index": chunk_index, "error": str(e)})
                            failed_count += 1
                            # Don't continue on batch errors - fail fast
                            raise BatchInsertError(
                                error_msg,
                                failed_objects=batch_errors
                            ) from e

                    # Store the batch response before context exits
                    batch_response = batch

                # The batch context manager will flush on exit
                # Check batch results after context exit

            # Check for batch errors from the response
            if batch_response and hasattr(batch_response, 'failed_objects') and batch_response.failed_objects:
                error_msg = f"Batch insert failed for {len(batch_response.failed_objects)} objects"
                logger.error(f"{error_msg}: {batch_response.failed_objects}")
                # Don't store metadata since chunks failed
                raise BatchInsertError(
                    error_msg,
                    failed_objects=batch_response.failed_objects
                )

            # If we have any failed chunks from add_object, raise error (fail-fast)
            if batch_errors:
                error_msg = f"Failed to store {len(batch_errors)} chunks"
                raise BatchInsertError(
                    error_msg,
                    failed_objects=batch_errors
                )

            # Verify chunks were actually stored
            logger.info(f"Batch insert completed: {stored_count} chunks added to batch")

            # Quick verification - check if at least one chunk exists
            try:
                test_query = collection.query.fetch_objects(
                    where=collection.query.where("documentId").equal(document_id),
                    limit=1
                )
                if not test_query.objects:
                    error_msg = f"Batch insert verification failed: No chunks found for document {document_id} after insert"
                    logger.error(error_msg)
                    raise BatchInsertError(error_msg, failed_objects=[])
                logger.info(f"Batch insert verified: Chunks successfully stored for document {document_id}")
            except Exception as e:
                logger.warning(f"Could not verify batch insert: {e}")

        except Exception as e:
            logger.error(f"Error in sync_store: {e}")
            raise

    # Run synchronous storage in async context
    await asyncio.get_event_loop().run_in_executor(None, sync_store)

    return {
        "stored_count": stored_count,
        "failed_count": failed_count,
        "stored_ids": stored_ids
    }


async def update_document_metadata(
    document_id: str,
    stats: Dict[str, Any],
    weaviate_client: Any,
    user_id: str
) -> None:
    """Update document metadata with processing statistics.

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
    logger.info(f"Updating metadata for document {document_id}")

    def sync_update():
        """Synchronous function to update document metadata."""
        try:
            with weaviate_client.session() as client:
                # Get tenant-scoped collection (FR-011: user-specific data isolation)
                try:
                    from ..weaviate_helpers import get_user_collections
                    chunk_collection, pdf_collection = get_user_collections(client, user_id)
                    collection = pdf_collection

                    # Update the document
                    collection.data.update(
                        uuid=document_id,
                        properties={
                            "chunkCount": stats["stored_chunks"],
                            "vectorCount": stats["stored_chunks"],
                            "processingStatus": "completed",
                            "embeddingStatus": "completed"
                        }
                    )
                    logger.info(f"Successfully updated metadata for document {document_id}")

                except Exception as e:
                    logger.warning(f"Could not update document metadata: {e}")
                    # Document may not exist in PDFDocument collection yet
                    # This is okay as we've stored the chunks successfully

        except Exception as e:
            logger.error(f"Metadata update failed: {e}")

    # Run synchronous update in async context
    await asyncio.get_event_loop().run_in_executor(None, sync_update)


async def update_processing_status(
    document_id: str,
    status: str
) -> None:
    """Update the processing status of a document.

    Args:
        document_id: Document UUID
        status: New status value
    """
    logger.info(f"Updating document {document_id} status to: {status}")
    # This will be integrated with tracker module
