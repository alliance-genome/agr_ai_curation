"""Document operations library for Weaviate."""

import logging
import time
from collections import Counter
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime
from uuid import UUID
import asyncio
import json

from .connection import get_connection, WeaviateConnection
from ..weaviate_helpers import get_user_collections
from src.models.sql.database import get_db
from src.models.sql.pdf_document import PDFDocument as PdfDocumentModel
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


async def async_list_documents(
    user_id: str,
    filter_obj: Any,
    pagination: Dict[str, Any]
) -> Dict[str, Any]:
    """List documents with pagination and filtering using Weaviate v4 collections API.

    Args:
        user_id: User identifier for tenant scoping (required, FR-011, FR-014)
        filter_obj: Document filter object
        pagination: Pagination parameters

    Returns:
        Dictionary with documents and pagination info (user's documents only)

    Requirements:
        - T030: Filter by user_id from PostgreSQL
        - T030: Include user_id and weaviate_tenant in response

    Raises:
        ValueError: If user_id is None (required for tenant scoping)
    """
    # T037: Validate user_id is provided (required for tenant scoping)
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped document listing (FR-011, FR-014)")

    from ..weaviate_helpers import get_tenant_name
    from ...models.sql.user import User
    from sqlalchemy import select

    connection = get_connection()
    if not connection:
        raise RuntimeError("No Weaviate connection established")

    # T030: Get database user for user_id filtering (FR-014, FR-016)
    db_gen = get_db()
    db = next(db_gen)
    try:
        db_user = db.execute(select(User).where(User.auth_sub == user_id)).scalar_one_or_none()
        if not db_user:
            # User not provisioned - return empty result (contract-compliant)
            return {
                "documents": [],
                "total": 0,
                "limit": pagination.get("page_size", 20),
                "offset": (pagination.get("page", 1) - 1) * pagination.get("page_size", 20)
            }
        # user_id parameter already contains auth_sub, use it for tenant naming
        tenant_name = get_tenant_name(user_id)
    finally:
        db.close()

    with connection.session() as client:
        try:
            # Get page and page_size from pagination
            page = pagination.get("page", 1)
            page_size = pagination.get("page_size", 20)
            sort_by = pagination.get("sort_by", "creationDate")
            sort_order = pagination.get("sort_order", "desc")

            # Get tenant-scoped collection (FR-011: user-specific data isolation)
            chunk_collection, pdf_collection = get_user_collections(client, user_id)
            collection = pdf_collection

            # Calculate offset
            offset = (page - 1) * page_size

            # Build where filter if needed
            where_filter = None
            has_filters = filter_obj and (
                filter_obj.search_term or
                filter_obj.embedding_status or
                filter_obj.min_vector_count is not None or
                filter_obj.max_vector_count is not None
            )
            logger.debug(
                "Filter check: has_filters=%s, min_vector_count=%s, max_vector_count=%s",
                has_filters,
                getattr(filter_obj, "min_vector_count", None),
                getattr(filter_obj, "max_vector_count", None),
            )
            if has_filters:
                from weaviate.classes.query import Filter
                conditions = []

                if filter_obj.search_term:
                    conditions.append(
                        Filter.by_property("filename").like(f"*{filter_obj.search_term}*")
                    )

                if filter_obj.embedding_status:
                    # Build OR condition for multiple statuses
                    status_filters = [
                        Filter.by_property("embeddingStatus").equal(status)
                        for status in filter_obj.embedding_status
                    ]
                    if len(status_filters) > 1:
                        conditions.append(Filter.any_of(status_filters))
                    else:
                        conditions.append(status_filters[0])

                # Filter by chunk count (UI shows "Chunks" but params still use vector naming)
                if filter_obj.min_vector_count is not None:
                    conditions.append(
                        Filter.by_property("chunkCount").greater_or_equal(filter_obj.min_vector_count)
                    )
                if filter_obj.max_vector_count is not None:
                    conditions.append(
                        Filter.by_property("chunkCount").less_or_equal(filter_obj.max_vector_count)
                    )

                if len(conditions) == 1:
                    where_filter = conditions[0]
                elif len(conditions) > 1:
                    where_filter = Filter.all_of(conditions)
                logger.debug(
                    "Built filter with %s conditions, where_filter=%s",
                    len(conditions),
                    where_filter,
                )

            # Query for documents using v4 fetch_objects
            from weaviate.classes.query import Sort

            # Build sort object
            sort_obj = Sort.by_property(sort_by, ascending=(sort_order == "asc"))

            query_start = time.monotonic()
            # Execute query with limit and offset using gRPC filters API
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: collection.query.fetch_objects(
                    limit=page_size,
                    offset=offset,
                    filters=where_filter if where_filter else None,
                    sort=sort_obj,
                    include_vector=False
                )
            )
            logger.debug("Weaviate query returned %s objects", len(response.objects))
            for obj in response.objects[:3]:  # Log first 3
                props = obj.properties
                logger.debug("Sample result: %s chunkCount=%s", props.get("filename"), props.get("chunkCount"))

            # T030: Query PostgreSQL for ownership verification (defense-in-depth, FR-014)
            # Build map of document_id -> PostgreSQL record for ownership filtering
            from ...models.sql.pdf_document import PDFDocument as PGDocument
            from sqlalchemy import select
            from uuid import UUID

            weaviate_doc_ids = [str(obj.uuid) for obj in response.objects]

            # Query PostgreSQL to verify ownership and get additional metadata
            db_gen = get_db()
            db = next(db_gen)
            try:
                pg_docs = db.execute(
                    select(PGDocument).where(
                        PGDocument.id.in_([UUID(doc_id) for doc_id in weaviate_doc_ids]),
                        PGDocument.user_id == db_user.id  # T030: PostgreSQL ownership filter (integer)
                    )
                ).scalars().all()

                # Create lookup map by document ID
                pg_doc_map = {str(doc.id): doc for doc in pg_docs}
            finally:
                db.close()

            # Convert response to contract Document schema format
            documents = []
            for obj in response.objects:
                doc_id = str(obj.uuid)

                # T030: Skip documents not owned by user (defense-in-depth)
                # Weaviate tenant scoping should prevent this, but PostgreSQL is authoritative
                if doc_id not in pg_doc_map:
                    continue

                pg_doc = pg_doc_map[doc_id]
                doc_props = obj.properties

                # Map to contract Document schema (document_endpoints.yaml)
                doc = {
                    "document_id": doc_id,  # Contract field name
                    "user_id": user_id,  # Required by contract
                    "filename": doc_props.get("filename"),
                    "status": doc_props.get("processingStatus", "pending").upper(),  # Contract requires uppercase enum
                    "upload_timestamp": pg_doc.upload_timestamp.isoformat() if pg_doc.upload_timestamp else doc_props.get("creationDate"),
                    "processing_started_at": None,  # TODO: track in PostgreSQL
                    "processing_completed_at": None,  # TODO: track in PostgreSQL
                    "file_size_bytes": pg_doc.file_size,  # Contract field name from PostgreSQL
                    "weaviate_tenant": tenant_name,  # Required by contract
                    "chunk_count": doc_props.get("chunkCount"),
                    "embedding_status": doc_props.get("embeddingStatus", "pending"),  # Frontend expects this field
                    "error_message": None  # TODO: track processing errors
                }
                documents.append(doc)

            # Get total count efficiently
            count_response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: collection.aggregate.over_all(
                    filters=where_filter if where_filter else None,
                    total_count=True
                )
            )

            total_items = count_response.total_count if count_response else 0
            offset = (page - 1) * page_size
            duration_ms = (time.monotonic() - query_start) * 1000
            logger.info(
                "Weaviate document listing query completed",
                extra={"duration_ms": round(duration_ms, 1), "operation": "weaviate_list_documents"},
            )

            # Return contract-compliant structure (document_endpoints.yaml lines 60-72)
            return {
                "documents": documents,
                "total": total_items,
                "limit": page_size,
                "offset": offset
            }

        except Exception as e:
            logger.error("Failed to list documents: %s", e)
            raise


def list_documents(
    user_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
    search_term: Optional[str] = None,
    embedding_status: Optional[List[str]] = None,
    sort_by: str = "creationDate",
    sort_order: str = "desc"
) -> Dict[str, Any]:
    """Synchronous helper for listing documents via CLI and legacy callers.

    This wraps ``async_list_documents`` and normalises basic filter parameters
    into the pydantic models used by the async implementation.

    Args:
        user_id: User identifier for tenant scoping (FR-011, FR-014)
        page: Page number (1-indexed)
        page_size: Number of items per page
        search_term: Optional search term for filtering
        embedding_status: Optional list of embedding statuses to filter by
        sort_by: Field to sort by
        sort_order: Sort order (asc/desc)

    Returns:
        Dictionary with documents and pagination info
    """

    from ...models.api_schemas import DocumentFilter, PaginationParams, SortBy, SortOrder
    from ...models.document import EmbeddingStatus

    filter_model = None
    if any([search_term, embedding_status]):
        status_values = None
        if embedding_status:
            status_values = [EmbeddingStatus(status) for status in embedding_status]
        filter_model = DocumentFilter(
            search_term=search_term,
            embedding_status=status_values
        )

    pagination_model = PaginationParams(
        page=page,
        page_size=page_size,
        sort_by=SortBy(sort_by),
        sort_order=SortOrder(sort_order)
    )

    filter_payload = filter_model if filter_model else DocumentFilter()

    return asyncio.run(
        async_list_documents(
            user_id,
            filter_payload,
            pagination_model.model_dump()
        )
    )


async def get_document(user_id: str, document_id: str) -> Dict[str, Any]:
    """Get detailed information about a specific document.

    Args:
        user_id: User identifier for tenant scoping (required, FR-011, FR-014)
        document_id: Document UUID

    Returns:
        Dictionary with document details (only if document belongs to user)

    Raises:
        ValueError: If user_id is None or document not found or doesn't belong to user

    Requirements:
        - T030/T031: Include user_id and weaviate_tenant in response
    """
    # T037: Validate user_id is provided (required for tenant scoping)
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped document retrieval (FR-011, FR-014)")

    from ..weaviate_helpers import get_tenant_name
    from ...models.sql.user import User
    from sqlalchemy import select

    # T030: Get database user for ownership metadata (FR-014, FR-016)
    db_gen = get_db()
    db = next(db_gen)
    try:
        db_user = db.execute(select(User).where(User.auth_sub == user_id)).scalar_one_or_none()
        if not db_user:
            raise ValueError(f"User with auth_sub {user_id} not found")
        # user_id parameter already contains auth_sub, use it for tenant naming
        tenant_name = get_tenant_name(user_id)
    finally:
        db.close()

    connection = get_connection()
    if not connection:
        raise RuntimeError("No Weaviate connection established")

    with connection.session() as client:
        try:
            # Get tenant-scoped collections (FR-011: user-specific data isolation)
            chunk_collection, pdf_collection = get_user_collections(client, user_id)

            # Get document by ID using v4 API
            doc_response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: pdf_collection.query.fetch_object_by_id(document_id)
            )

            if not doc_response:
                raise ValueError(f"Document {document_id} not found")

            # Map Weaviate camelCase fields to snake_case
            doc_props = doc_response.properties

            document = {
                "id": str(doc_response.uuid),
                "filename": doc_props.get("filename"),
                "file_size": doc_props.get("fileSize"),
                "creation_date": doc_props.get("creationDate"),
                "last_accessed_date": doc_props.get("lastAccessedDate"),
                "processing_status": doc_props.get("processingStatus"),
                "embedding_status": doc_props.get("embeddingStatus"),
                "chunk_count": doc_props.get("chunkCount"),
                "vector_count": doc_props.get("vectorCount"),
                "metadata": doc_props.get("metadata"),
                # T030: Add ownership metadata (FR-014, FR-016)
                "user_id": user_id,
                "weaviate_tenant": tenant_name
            }
            # Parse metadata if it's a JSON string
            if isinstance(document["metadata"], str):
                try:
                    import json
                    document["metadata"] = json.loads(document["metadata"])
                except:
                    pass

            # Get first 10 chunks for preview using v4 API
            from weaviate.classes.query import Filter, Sort

            filter_by_doc = Filter.by_property("documentId").equal(document_id)

            logger.info("Fetching chunks for document %s", document_id)

            # Try different approaches to fetch chunks
            chunks_response = None

            # Try with UUID conversion
            try:
                from uuid import UUID
                doc_uuid = UUID(document_id) if isinstance(document_id, str) else document_id
                filter_by_uuid = Filter.by_property("documentId").equal(str(doc_uuid))

                chunks_response = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: chunk_collection.query.fetch_objects(
                        limit=10,
                        filters=filter_by_uuid,
                        sort=Sort.by_property("chunkIndex", ascending=True),
                        include_vector=False
                    )
                )
                logger.info(
                    "Found %s chunks using UUID filter",
                    len(chunks_response.objects) if chunks_response else 0,
                )
            except Exception as e:
                logger.warning("UUID filter attempt failed: %s", e)

            # If no results, try without filter to see if chunks exist at all
            if not chunks_response or len(chunks_response.objects) == 0:
                all_chunks = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: chunk_collection.query.fetch_objects(
                        limit=100,
                        include_vector=False
                    )
                )

                if all_chunks and all_chunks.objects:
                    # Check if any belong to this document
                    matching_chunks = []
                    for chunk_obj in all_chunks.objects:
                        chunk_doc_id = chunk_obj.properties.get("documentId")
                        if str(chunk_doc_id) == str(document_id):
                            matching_chunks.append(chunk_obj)

                    if matching_chunks:
                        logger.info("Found %s chunks by manual filtering", len(matching_chunks))
                        chunks_response = type('obj', (object,), {'objects': matching_chunks[:10]})()
                    else:
                        logger.warning(
                            "No chunks found for document %s among %s total chunks",
                            document_id,
                            len(all_chunks.objects),
                        )
                else:
                    logger.warning("No chunks found in the entire collection")

            logger.info(
                "Final chunk count for document %s: %s",
                document_id,
                len(chunks_response.objects) if chunks_response else 0,
            )

            chunks = []
            embedding_models = Counter()
            latest_embedding_ts: Optional[datetime] = None

            def _parse_timestamp(raw_ts: Any) -> Optional[datetime]:
                if isinstance(raw_ts, datetime):
                    return raw_ts
                if isinstance(raw_ts, str) and raw_ts:
                    try:
                        if raw_ts.endswith('Z'):
                            raw_ts = raw_ts[:-1] + '+00:00'
                        return datetime.fromisoformat(raw_ts)
                    except ValueError:
                        return None
                return None

            for chunk_obj in chunks_response.objects:
                chunk_props = chunk_obj.properties

                doc_id_value = chunk_props.get("documentId")
                if isinstance(doc_id_value, UUID):
                    doc_id_value = str(doc_id_value)

                metadata_value = chunk_props.get("metadata")
                if isinstance(metadata_value, str):
                    try:
                        metadata_value = json.loads(metadata_value)
                    except json.JSONDecodeError:
                        metadata_value = None
                elif metadata_value is not None and not isinstance(metadata_value, dict):
                    metadata_value = None

                chunk = {
                    "id": str(chunk_obj.uuid),
                    "document_id": doc_id_value,
                    "chunk_index": chunk_props.get("chunkIndex"),
                    "content": chunk_props.get("content"),
                    "element_type": chunk_props.get("elementType"),
                    "page_number": chunk_props.get("pageNumber"),
                    "section_title": chunk_props.get("sectionTitle"),
                    "metadata": metadata_value,
                    "embedding_model": chunk_props.get("embeddingModel"),
                    "embedding_timestamp": chunk_props.get("embeddingTimestamp")
                }
                if chunk.get("embedding_model"):
                    embedding_models.update([chunk["embedding_model"]])

                ts = _parse_timestamp(chunk.get("embedding_timestamp"))
                if ts and (latest_embedding_ts is None or ts > latest_embedding_ts):
                    latest_embedding_ts = ts
                chunks.append(chunk)

            total_chunks = document.get("chunk_count", len(chunks)) or 0
            embedded_chunks = document.get("vector_count", 0) or 0
            coverage = None
            if total_chunks:
                coverage = round((embedded_chunks / total_chunks) * 100, 2)

            embedding_summary = {
                "total_chunks": total_chunks,
                "embedded_chunks": embedded_chunks,
                "coverage_percentage": coverage,
                "last_embedded_at": latest_embedding_ts.isoformat() if latest_embedding_ts else None,
                "primary_model": None,
                "models": [
                    {"model": model, "chunk_count": count}
                    for model, count in embedding_models.most_common()
                ],
            }

            if embedding_summary["models"]:
                embedding_summary["primary_model"] = embedding_summary["models"][0]["model"]

            schema_version = (
                document.get("metadata", {}).get("schema_version")
                if isinstance(document.get("metadata"), dict)
                else None
            ) or doc_props.get("schemaVersion")

            return {
                "document": document,
                "chunks": chunks,
                "chunks_preview": chunks,
                "total_chunks": total_chunks,
                "embedding_summary": embedding_summary,
                "embeddings": {
                    "totalChunks": total_chunks,
                    "embeddedChunks": embedded_chunks,
                    "lastEmbeddedAt": embedding_summary["last_embedded_at"],
                    "primaryModel": embedding_summary["primary_model"],
                    "coveragePercentage": coverage,
                },
                "schema_version": schema_version or "1.0.0",
            }

        except Exception as e:
            logger.error("Failed to get document %s: %s", document_id, e)
            raise


async def delete_document(user_id: str, document_id: str) -> Dict[str, Any]:
    """Delete a document and all its chunks.

    Args:
        user_id: User identifier for tenant scoping (required, FR-011, FR-014)
        document_id: Document UUID

    Returns:
        Operation result dictionary

    Raises:
        ValueError: If user_id is None (required for tenant scoping)
    """
    # T037: Validate user_id is provided (required for tenant scoping)
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped document deletion (FR-011, FR-014)")

    connection = get_connection()
    if not connection:
        raise RuntimeError("No Weaviate connection established")

    with connection.session() as client:
        try:
            # Get tenant-scoped collections (FR-011: user-specific data isolation)
            chunk_collection, pdf_collection = get_user_collections(client, user_id)

            # Delete all chunks for this document using v4 API
            from weaviate.classes.query import Filter

            delete_result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: chunk_collection.data.delete_many(
                    where=Filter.by_property("documentId").equal(document_id)
                )
            )

            chunks_deleted = 0
            if delete_result is not None:
                try:
                    chunks_deleted = int(getattr(delete_result, "successful", 0))
                except Exception:
                    chunks_deleted = 0

            # Then delete the document itself
            doc_deleted = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: pdf_collection.data.delete_by_id(document_id)
            )

            # T031: Also delete from PostgreSQL if it exists there (with ownership verification)
            postgres_deleted = False
            try:
                from src.models.sql.user import User
                from sqlalchemy import select

                # Create a new database session
                db_gen = get_db()
                db = next(db_gen)
                try:
                    # T031: Get database user for ownership verification (FR-014)
                    db_user = db.execute(select(User).where(User.auth_sub == user_id)).scalar_one_or_none()

                    if not db_user:
                        logger.warning(
                            "User with auth_sub %s not found in database during delete",
                            user_id,
                        )
                    else:
                        # Try to find and delete from PostgreSQL with ownership check
                        pdf_doc = db.get(PdfDocumentModel, UUID(document_id))
                        if pdf_doc:
                            # T031: Verify ownership before deletion (FR-014)
                            if pdf_doc.user_id == db_user.id:
                                db.delete(pdf_doc)
                                db.commit()
                                postgres_deleted = True
                                logger.info(
                                    "Deleted document %s from PostgreSQL (user_id=%s)",
                                    document_id,
                                    db_user.id,
                                )
                            else:
                                logger.warning(
                                    "Document %s ownership mismatch in PostgreSQL (doc.user_id=%s, user_id=%s)",
                                    document_id,
                                    pdf_doc.user_id,
                                    db_user.id,
                                )
                        else:
                            logger.info("Document %s not found in PostgreSQL, skipping", document_id)
                finally:
                    db.close()
            except Exception as e:
                logger.warning("Failed to delete from PostgreSQL: %s", e)
                # Don't fail the whole operation if PostgreSQL deletion fails

            logger.info(
                "Deleted document %s and its chunks from Weaviate%s",
                document_id,
                " and PostgreSQL" if postgres_deleted else "",
            )

            return {
                "success": bool(doc_deleted),
                "message": "Document and chunks deleted successfully" if doc_deleted else "Document deletion reported as unsuccessful",
                "documentId": document_id,
                "chunks_deleted": chunks_deleted,
                "chunks_matched": getattr(delete_result, "matches", None) if delete_result is not None else None,
                "postgres_deleted": postgres_deleted
            }

        except Exception as e:
            logger.error("Failed to delete document %s: %s", document_id, e)
            return {
                "success": False,
                "message": f"Failed to delete document: {e}",
                "documentId": document_id,
                "chunks_deleted": 0,
                "error": {
                    "code": "DELETE_FAILED",
                    "details": str(e)
                }
            }


async def re_embed_document(
    document_id: str,
    user_id: str,
    embedding_config: Optional[Dict[str, Any]] = None,
    batch_size: int = 10
) -> Dict[str, Any]:
    """Trigger re-embedding for a document.

    Args:
        document_id: Document UUID
        user_id: User ID for tenant scoping (required, FR-011, FR-014)
        embedding_config: Optional embedding configuration
        batch_size: Batch size for re-embedding

    Returns:
        Operation result dictionary

    Raises:
        ValueError: If user_id is None (required for tenant scoping)
    """
    # T037: Validate user_id is provided (required for tenant scoping)
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped re-embedding (FR-011, FR-014)")

    connection = get_connection()
    if not connection:
        raise RuntimeError("No Weaviate connection established")

    with connection.session() as client:
        try:
            # Get tenant-scoped collection (FR-011: user-specific data isolation)
            from ..weaviate_helpers import get_user_collections
            chunk_collection, pdf_collection = get_user_collections(client, user_id)
            collection = pdf_collection

            # Count chunks for this document
            def count_chunks():
                chunks_result = chunk_collection.query.fetch_objects(
                    filters=chunk_collection.query.where("documentId").equal(document_id),
                    limit=10000  # Large limit to count all chunks
                )
                return len(chunks_result.objects)

            total_chunks = await asyncio.get_event_loop().run_in_executor(None, count_chunks)

            # Update document status to trigger re-embedding
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: collection.data.update(
                    uuid=document_id,
                    properties={
                        "embeddingStatus": "pending",
                        "processingStatus": "embedding"
                    }
                )
            )

            logger.info(
                "Triggered re-embedding for document %s (%s chunks)",
                document_id,
                total_chunks,
            )

            return {
                "success": True,
                "message": "Re-embedding triggered successfully",
                "documentId": document_id,
                "total_chunks": total_chunks
            }

        except Exception as e:
            logger.error("Failed to trigger re-embedding for %s: %s", document_id, e)
            return {
                "success": False,
                "message": f"Failed to trigger re-embedding: {e}",
                "documentId": document_id,
                "error": {
                    "code": "REEMBED_FAILED",
                    "details": str(e)
                }
            }


async def update_document_status_detailed(
    document_id: str,
    user_id: str,
    processing_status: Optional[str] = None,
    embedding_status: Optional[str] = None
) -> Dict[str, Any]:
    """Update the processing and/or embedding status of a document.

    Args:
        document_id: Document UUID
        user_id: User identifier for tenant scoping (required, FR-011, FR-014)
        processing_status: New processing status (pending, parsing, chunking,
                         embedding, storing, completed, failed)
        embedding_status: New embedding status (pending, processing,
                        completed, failed, partial)

    Returns:
        Operation result dictionary

    Raises:
        ValueError: If user_id is None (required for tenant scoping)
    """
    # T037: Validate user_id is provided (required for tenant scoping)
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped status updates (FR-011, FR-014)")

    connection = get_connection()
    if not connection:
        raise RuntimeError("No Weaviate connection established")

    # Valid status enums from data model
    VALID_PROCESSING_STATUS = {
        "pending", "parsing", "chunking", "embedding",
        "storing", "completed", "failed"
    }
    VALID_EMBEDDING_STATUS = {
        "pending", "processing", "completed", "failed", "partial"
    }

    # Validate inputs
    if processing_status is None and embedding_status is None:
        return {
            "success": False,
            "message": "At least one status must be provided",
            "documentId": document_id,
            "error": {
                "code": "INVALID_REQUEST",
                "details": "No status values provided for update"
            }
        }

    if processing_status and processing_status not in VALID_PROCESSING_STATUS:
        return {
            "success": False,
            "message": f"Invalid processing status: {processing_status}",
            "documentId": document_id,
            "error": {
                "code": "INVALID_STATUS",
                "details": f"Valid values: {', '.join(sorted(VALID_PROCESSING_STATUS))}"
            }
        }

    if embedding_status and embedding_status not in VALID_EMBEDDING_STATUS:
        return {
            "success": False,
            "message": f"Invalid embedding status: {embedding_status}",
            "documentId": document_id,
            "error": {
                "code": "INVALID_STATUS",
                "details": f"Valid values: {', '.join(sorted(VALID_EMBEDDING_STATUS))}"
            }
        }

    with connection.session() as client:
        try:
            # Get tenant-scoped collection (FR-011: user-specific data isolation)
            from ..weaviate_helpers import get_user_collections
            chunk_collection, pdf_collection = get_user_collections(client, user_id)
            collection = pdf_collection

            # Build update object
            update_data = {}
            if processing_status:
                update_data["processingStatus"] = processing_status
            if embedding_status:
                update_data["embeddingStatus"] = embedding_status

            # Update document using v4 API
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: collection.data.update(
                    uuid=document_id,
                    properties=update_data
                )
            )

            status_updates = []
            if processing_status:
                status_updates.append(f"processingStatus={processing_status}")
            if embedding_status:
                status_updates.append(f"embeddingStatus={embedding_status}")

            logger.info("Updated document %s: %s", document_id, ", ".join(status_updates))

            return {
                "success": True,
                "message": f"Document status updated: {', '.join(status_updates)}",
                "documentId": document_id,
                "updates": update_data
            }

        except Exception as e:
            logger.error("Failed to update document status for %s: %s", document_id, e)
            return {
                "success": False,
                "message": f"Failed to update document status: {e}",
                "documentId": document_id,
                "error": {
                    "code": "STATUS_UPDATE_FAILED",
                    "details": str(e)
                }
            }




async def update_document_status(document_id: str, user_id: str, status: str) -> Dict[str, Any]:
    """Update document processing status.

    Args:
        document_id: Document UUID
        user_id: User identifier for tenant scoping (required, FR-011, FR-014)
        status: New status

    Returns:
        Operation result

    Raises:
        ValueError: If user_id is None (required for tenant scoping)
    """
    # T037: Validate user_id is provided (required for tenant scoping)
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped status updates (FR-011, FR-014)")

    connection = get_connection()
    if not connection:
        raise RuntimeError("No Weaviate connection established")

    with connection.session() as client:
        try:
            # Get tenant-scoped collection (FR-011: user-specific data isolation)
            from ..weaviate_helpers import get_user_collections
            chunk_collection, pdf_collection = get_user_collections(client, user_id)
            collection = pdf_collection

            # Update using v4 API
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: collection.data.update(
                    uuid=document_id,
                    properties={
                        "processingStatus": status
                    }
                )
            )

            logger.info("Updated document %s status to %s", document_id, status)

            return {
                "success": True,
                "message": f"Document status updated to {status}"
            }

        except Exception as e:
            logger.error("Failed to update document status: %s", e)
            return {
                "success": False,
                "message": f"Failed to update status: {e}",
                "error": str(e)
            }


async def search_similar(document_id: str, user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Find documents similar to the given document.

    Args:
        document_id: Document UUID
        user_id: User identifier for tenant scoping (required, FR-011, FR-014)
        limit: Maximum number of similar documents

    Returns:
        List of similar documents

    Raises:
        ValueError: If user_id is None (required for tenant scoping)
    """
    # T037: Validate user_id is provided (required for tenant scoping)
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped similarity search (FR-011, FR-014)")

    connection = get_connection()
    if not connection:
        raise RuntimeError("No Weaviate connection established")

    def sync_search():
        """Synchronous function to search for similar documents."""
        with connection.session() as client:
            try:
                # Get tenant-scoped collection (FR-011: user-specific data isolation)
                from ..weaviate_helpers import get_user_collections
                chunk_collection, pdf_collection = get_user_collections(client, user_id)
                collection = pdf_collection

                # Get the document's vector using v4 API
                source_doc = collection.query.fetch_objects(
                    filters=collection.query.where("id").equal(document_id),
                    include_vector=True,
                    limit=1
                )

                if not source_doc.objects:
                    raise ValueError(f"Document {document_id} not found")

                vector = source_doc.objects[0].vector

                if not vector:
                    logger.warning("Document %s has no vector", document_id)
                    return []

                # Search for similar documents using near_vector
                similar_results = collection.query.near_vector(
                    near_vector=vector,
                    limit=limit + 1,  # +1 to account for self
                    return_metadata=["distance"]
                )

                # Convert results to dict format and filter out source document
                similar_docs = []
                for obj in similar_results.objects:
                    if str(obj.uuid) != document_id:
                        similar_docs.append({
                            "id": str(obj.uuid),
                            "filename": obj.properties.get("filename"),
                            "fileSize": obj.properties.get("fileSize"),
                            "creationDate": obj.properties.get("creationDate"),
                            "embeddingStatus": obj.properties.get("embeddingStatus"),
                            "vectorCount": obj.properties.get("vectorCount"),
                            "_additional": {
                                "distance": obj.metadata.distance if hasattr(obj.metadata, 'distance') else None
                            }
                        })

                return similar_docs[:limit]

            except Exception as e:
                logger.error("Failed to find similar documents: %s", e)
                raise

    # Run synchronous search in async context
    import asyncio
    return await asyncio.get_event_loop().run_in_executor(None, sync_search)




async def create_document(user_id: str, document: Any) -> Dict[str, Any]:
    """Create a new document in Weaviate using v4 collections API.

    Args:
        user_id: User identifier for tenant scoping (required, FR-011, FR-014)
        document: Document object to create

    Returns:
        Operation result dictionary

    Raises:
        ValueError: If user_id is None (required for tenant scoping)
    """
    # T037: Validate user_id is provided (required for tenant scoping)
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped document creation (FR-011, FR-014)")

    connection = get_connection()
    if not connection:
        raise RuntimeError("No Weaviate connection established")

    with connection.session() as client:
        try:
            # Get tenant-scoped collection (FR-011: user-specific data isolation)
            chunk_collection, pdf_collection = get_user_collections(client, user_id)
            collection = pdf_collection

            # Convert document to properties dict
            # Format dates as RFC3339 (with 'Z' timezone)
            def format_date(dt):
                if dt:
                    # Ensure it's in UTC and format as RFC3339
                    return dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')[:-3] + 'Z'  # Remove microseconds, keep only milliseconds
                return datetime.now().strftime('%Y-%m-%dT%H:%M:%S.%fZ')[:-3] + 'Z'

            properties = {
                "filename": document.filename,
                "fileSize": document.file_size,
                "uploadDate": format_date(datetime.now()),
                "creationDate": format_date(document.creation_date),
                "lastAccessedDate": format_date(document.last_accessed_date),
                "processingStatus": document.processing_status,
                "embeddingStatus": document.embedding_status,
                "chunkCount": document.chunk_count,
                "vectorCount": document.vector_count,
                "metadata": document.metadata.model_dump_json() if hasattr(document.metadata, 'model_dump_json') else str(document.metadata)
            }

            # Create object with specified UUID
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: collection.data.insert(
                    properties=properties,
                    uuid=document.id
                )
            )

            logger.info("Creating document in Weaviate: %s", document.id)

            return {
                "success": True,
                "document_id": str(result),
                "message": "Document created successfully"
            }

        except Exception as e:
            logger.error("Failed to create document: %s", e)
            raise
