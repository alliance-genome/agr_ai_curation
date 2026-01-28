"""Chunk operations library for Weaviate."""

import logging
import os
from typing import List, Dict, Any, Optional
from uuid import uuid4
import asyncio
import json

from weaviate.classes.query import Filter, HybridFusion, MetadataQuery
from openai import OpenAI

from .connection import get_connection

logger = logging.getLogger(__name__)


def store_chunks(document_id: str, chunks: List[Dict[str, Any]], user_id: str) -> Dict[str, Any]:
    """Store document chunks in Weaviate.

    Args:
        document_id: Parent document UUID
        chunks: List of chunk dictionaries with content and metadata
        user_id: User identifier for tenant scoping (required, FR-011, FR-014)

    Returns:
        Operation result dictionary with stored chunk count

    Raises:
        ValueError: If user_id is None (required for tenant scoping)
    """
    # T038: Validate user_id is provided (required for tenant scoping)
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped chunk storage (FR-011, FR-014)")

    connection = get_connection()
    if not connection:
        raise RuntimeError("No Weaviate connection established")

    with connection.session() as client:
        try:
            # T038: Get tenant-scoped collection (FR-011: user-specific data isolation)
            from ..weaviate_helpers import get_user_collections
            chunk_collection, pdf_collection = get_user_collections(client, user_id)
            collection = chunk_collection

            # Prepare batch data for v4 insert_many
            batch_data = []
            chunk_ids = []

            for i, chunk in enumerate(chunks):
                chunk_id = str(uuid4())
                chunk_ids.append(chunk_id)

                raw_content = chunk.get("content", "")
                metadata = chunk.get("metadata", {}) or {}
                doc_items = chunk.get("doc_items") or metadata.get("doc_items") or []

                base_metadata = {
                    "characterCount": len(raw_content),
                    "wordCount": len(raw_content.split()),
                    "hasTable": chunk.get("has_table", False),
                    "hasImage": chunk.get("has_image", False),
                }
                if isinstance(metadata, dict):
                    base_metadata.update(metadata)
                if doc_items:
                    base_metadata.setdefault("doc_items", doc_items)

                content_preview = chunk.get("contentPreview")
                if not content_preview and raw_content:
                    MAX_PREVIEW_CHARS = 1600
                    content_preview = raw_content[:MAX_PREVIEW_CHARS]
                    if len(raw_content) > MAX_PREVIEW_CHARS:
                        content_preview = content_preview.rsplit(' ', 1)[0] + '...'

                chunk_properties = {
                    "documentId": document_id,
                    "chunkIndex": i,
                    "content": raw_content,
                    "contentPreview": content_preview,
                    "elementType": chunk.get("element_type", "NarrativeText"),
                    "pageNumber": chunk.get("page_number", 1),
                    "sectionTitle": chunk.get("section_title"),
                    "metadata": json.dumps(base_metadata),  # Serialize metadata to JSON string
                }

                if doc_items:
                    # Serialize doc_items to JSON string for storage in TEXT field
                    chunk_properties["docItemProvenance"] = json.dumps(doc_items, default=str)

                # Add to batch data for v4 insert_many
                from weaviate.classes.data import DataObject
                batch_data.append(DataObject(
                    properties=chunk_properties,
                    uuid=chunk_id
                ))

            # Execute batch insertion using v4 API
            if batch_data:
                result = collection.data.insert_many(batch_data)

                # Check for any errors in the batch result
                if hasattr(result, 'errors') and result.errors:
                    logger.error(f"Batch insertion had errors: {result.errors}")

            # T038: Update document chunk count using tenant-scoped collection
            try:
                # pdf_collection is already tenant-scoped from get_user_collections above
                pdf_collection.data.update(
                    uuid=document_id,
                    properties={
                        "chunkCount": len(chunks)
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to update document chunk count: {e}")

            logger.info(f"Stored {len(chunks)} chunks for document {document_id}")

            return {
                "success": True,
                "message": f"Stored {len(chunks)} chunks successfully",
                "documentId": document_id,
                "chunkCount": len(chunks),
                "chunkIds": chunk_ids
            }

        except Exception as e:
            logger.error(f"Failed to store chunks: {e}")
            return {
                "success": False,
                "message": f"Failed to store chunks: {e}",
                "documentId": document_id,
                "error": {
                    "code": "CHUNK_STORE_FAILED",
                    "details": str(e)
                }
            }




async def hybrid_search_chunks(
    document_id: str,
    query: str,
    user_id: str,                   # T038: Required for tenant scoping (FR-011, FR-014)
    limit: int = 10,                # V5: Final results after all processing
    initial_limit: int = 50,        # V5: Candidates for reranker
    alpha: float = 0.7,             # V5: 70% vector, 30% keyword
    apply_reranking: bool = True,
    apply_mmr: bool = True,
    mmr_lambda: float = 0.5,
    use_bm25_boost: bool = False,   # V5: Boost exact matches/acronyms
    section_keywords: Optional[List[str]] = None,  # Section filtering
    strategy: str = "hybrid",       # NEW: hybrid | lexical | hybrid_lexical_first
) -> List[Dict[str, Any]]:
    """
    Production-ready hybrid search with server-side embeddings (V5).

    Args:
        document_id: Document UUID to search within
        query: Search query text
        user_id: User identifier for tenant scoping (required, FR-011, FR-014)
        limit: Final number of results after all processing
        initial_limit: Number of candidates for reranker
        alpha: Hybrid search weight (0-1, higher = more vector emphasis)
        apply_reranking: Enable cross-encoder reranking
        apply_mmr: Enable MMR diversification
        mmr_lambda: MMR diversity parameter
        use_bm25_boost: Boost exact matches/acronyms

    Flow:
    1. Weaviate embeds query server-side (no manual vectors)
    2. Hybrid search gets 50 candidates
    3. Reranker scores them (if enabled)
    4. MMR diversifies top 20 → final 10 (if enabled)

    Raises:
        ValueError: If user_id is None (required for tenant scoping)
    """

    # T038: Validate user_id is provided (required for tenant scoping)
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped chunk search (FR-011, FR-014)")

    # Environment-based configuration
    explain_scores = os.getenv("RETRIEVAL_EXPLAIN", "false").lower() == "true"
    weaviate_version = os.getenv("WEAVIATE_VERSION", "1.30")  # For feature gates

    connection = get_connection()
    if not connection:
        raise RuntimeError("No Weaviate connection established")

    # Normalize query for short tokens (helps BM25 exact match)
    norm_query = query.strip()
    short_token = len(norm_query.split()) <= 3 or len(norm_query) < 15
    if short_token:
        # push lexical by default for short/symbol-like inputs
        logger.info(f"⚙️ Detected short/symbol-like query; enabling BM25 boost and disabling MMR/rerank")
        use_bm25_boost = True
        apply_reranking = False
        apply_mmr = False

    def _search(alpha_override: Optional[float] = None,
                rerank_override: Optional[bool] = None,
                mmr_override: Optional[bool] = None) -> List[Dict[str, Any]]:
        try:
            # V5: Log search parameters
            logger.info(
                f"V5 Hybrid Search Starting - "
                f"document_id={document_id}, query='{norm_query[:50]}...', "
                f"alpha={alpha_override or alpha}, initial_limit={initial_limit}, final_limit={limit}, "
                f"reranking={rerank_override if rerank_override is not None else apply_reranking}, "
                f"MMR={mmr_override if mmr_override is not None else apply_mmr}, MMR_lambda={mmr_lambda}"
            )

            with connection.session() as client:
                # T038: Get tenant-scoped collection (FR-011: user-specific data isolation)
                from ..weaviate_helpers import get_user_collections
                chunk_collection, pdf_collection = get_user_collections(client, user_id)
                collection = chunk_collection

                # Build query params - NO manual vector! (V5 change)
                # Build base document filter
                base_filter = Filter.by_property("documentId").equal(document_id)

                # Add section filtering if keywords provided (Option A: active filtering)
                if section_keywords:
                    # Input validation: handle string input (LLM might pass "method" instead of ["method"])
                    # Use new local variable to avoid UnboundLocalError
                    validated_keywords = section_keywords
                    if isinstance(validated_keywords, str):
                        logger.warning(f"⚠️ section_keywords received as string, converting to list: {validated_keywords}")
                        validated_keywords = [validated_keywords]

                    # Filter out empty strings and strip whitespace
                    validated_keywords = [kw.strip() for kw in validated_keywords if kw and kw.strip()]

                    if not validated_keywords:
                        logger.warning("⚠️ section_keywords was empty after validation, skipping section filtering")
                        combined_filter = base_filter
                    else:
                        # Build OR filter for section title matching
                        # Searches for any chunk where sectionTitle contains ANY of the keywords
                        section_filter = None
                        for keyword in validated_keywords:
                            keyword_filter = Filter.by_property("sectionTitle").like(f"*{keyword}*")
                            if section_filter is None:
                                section_filter = keyword_filter
                            else:
                                section_filter = section_filter | keyword_filter

                        # Combine document filter AND section filter
                        combined_filter = base_filter & section_filter
                        logger.info(f"🔍 Section filtering active: {validated_keywords}")
                else:
                    combined_filter = base_filter

                query_params = {
                    "query": norm_query,
                    "alpha": alpha_override or alpha,
                    "limit": initial_limit,  # Get candidates for reranker
                    "fusion_type": HybridFusion.RELATIVE_SCORE,
                    "return_properties": [
                        "contentPreview",
                        "content",
                        "pageNumber",
                        "chunkIndex",
                        "sectionTitle",
                        "elementType",
                        "documentId",
                        "metadata",
                        "docItemProvenance",
                    ],
                    "filters": combined_filter,  # Use combined filter (document + optional section)
                    "return_metadata": MetadataQuery(
                        score=True,
                        explain_score=explain_scores  # Gated by env
                    ),
                    "include_vector": apply_mmr,  # Only if needed for MMR
                    "auto_limit": 2  # Use autocut to find natural result groupings (2 jumps)
                }

                # BM25 operator for acronyms (requires Weaviate 1.31+)
                if use_bm25_boost:
                    try:
                        # Parse semantic version properly
                        version_parts = weaviate_version.split('.')
                        major = int(version_parts[0]) if len(version_parts) > 0 else 1
                        minor = int(version_parts[1]) if len(version_parts) > 1 else 0
                        # Check if version >= 1.31
                        if major > 1 or (major == 1 and minor >= 31):
                            from weaviate.classes.query import BM25Operator
                            # Prefer exact/term precision: at least 2 terms must match
                            query_params["bm25_operator"] = BM25Operator.or_(minimum_match=2)
                            # (or strict: BM25Operator.and_() for all terms to match)
                    except Exception as e:
                        logger.debug(f"BM25 boost not available (v{weaviate_version}): {e}")

                # Add reranking if available
                if rerank_override if rerank_override is not None else apply_reranking:
                    logger.info("V5: Enabling local transformer reranking on contentPreview field")
                    from weaviate.classes.query import Rerank
                    query_params["rerank"] = Rerank(
                        prop="contentPreview",  # Use shortened field for reranker
                        query=query
                    )
                else:
                    logger.info("V5: Reranking disabled")

                # Group by page (COMMENTED OUT - materials/methods often same page)
                # if os.getenv("ENABLE_GROUPING", "false").lower() == "true":
                #     from weaviate.classes.query import GroupBy
                #     query_params["group_by"] = GroupBy(
                #         prop="pageNumber",
                #         objects_per_group=2,
                #         max_groups=25
                #     )

                # Execute query
                response = collection.query.hybrid(**query_params)

                # V5: Log retrieval results
                logger.info(f"V5: Retrieved {len(response.objects)} chunks from Weaviate")

                # Process results with correct format
                chunks = []
                for obj in response.objects:
                    chunk_uuid = str(obj.uuid) if hasattr(obj, "uuid") else None
                    metadata_dict = {
                        "page_number": obj.properties.get("pageNumber"),
                        "chunk_index": obj.properties.get("chunkIndex"),
                        "section_title": obj.properties.get("sectionTitle"),
                        "element_type": obj.properties.get("elementType"),
                        "document_id": document_id,
                    }

                    raw_metadata = obj.properties.get("metadata")
                    if isinstance(raw_metadata, dict):
                        metadata_dict.update(raw_metadata)
                    elif isinstance(raw_metadata, str):
                        try:
                            import json as _json
                            metadata_dict.update(_json.loads(raw_metadata))
                        except _json.JSONDecodeError:
                            logger.debug("Failed to decode chunk metadata JSON from Weaviate")

                    # Parse docItemProvenance (stored as JSON string in Weaviate)
                    doc_items = []
                    doc_items_str = obj.properties.get("docItemProvenance")
                    if doc_items_str:
                        try:
                            # docItemProvenance is stored as JSON string, need to parse it
                            import json as _json
                            doc_items = _json.loads(doc_items_str) if isinstance(doc_items_str, str) else doc_items_str
                            metadata_dict.setdefault("doc_items", doc_items)
                        except (_json.JSONDecodeError, TypeError):
                            doc_items = []

                    if chunk_uuid:
                        metadata_dict.setdefault("chunk_id", chunk_uuid)

                    chunk = {
                        "id": chunk_uuid,
                        # ✅ CRITICAL: Use 'text' to match chat.py
                        "text": obj.properties.get("content") if obj.properties else None,
                        "metadata": metadata_dict,
                        "score": obj.metadata.score if obj.metadata else 0.0,
                    }

                    # Include vector only if needed for MMR
                    if apply_mmr and hasattr(obj, 'vector') and obj.vector:
                        chunk["_vector"] = obj.vector  # Prefix with _ for internal use

                    # Log score explanation in debug mode
                    if explain_scores and obj.metadata and hasattr(obj.metadata, 'explain_score'):
                        logger.debug(f"Score breakdown: {obj.metadata.explain_score}")

                    chunks.append(chunk)

                # Apply MMR if enabled and we have enough results
                if (mmr_override if mmr_override is not None else apply_mmr) and len(chunks) > limit:
                    logger.info(f"V5: Applying MMR diversification (lambda={mmr_lambda}, candidates={len(chunks)}, target={limit})")
                    from .mmr_diversifier import mmr_diversify
                    pre_mmr_count = len(chunks)
                    chunks = mmr_diversify(
                        chunks,
                        lambda_param=mmr_lambda,
                        top_k=limit,
                        vector_field="_vector"  # Tell MMR where vectors are
                    )
                    logger.info(f"V5: MMR reduced {pre_mmr_count} chunks to {len(chunks)} diverse results")
                    # Clean up vectors after MMR
                    for chunk in chunks:
                        chunk.pop("_vector", None)
                else:
                    if not (mmr_override if mmr_override is not None else apply_mmr):
                        logger.info(f"V5: MMR disabled, returning top {limit} chunks")
                    else:
                        logger.info(f"V5: Not enough chunks for MMR ({len(chunks)} <= {limit})")
                    chunks = chunks[:limit]

                logger.info(
                    f"V5 Search Complete: Final {len(chunks)} results "
                    f"(α={alpha}, rerank={apply_reranking}, mmr={apply_mmr})"
                )

                return chunks

        except Exception as e:
            logger.error(f"Search failed: {e}", exc_info=True)
            raise

    # Retry strategy: lexical-first fallbacks for short/symbol queries or explicit strategy
    results = await hybrid_search_chunks_retry_adapter(_search, strategy=strategy, short_token=short_token)
    return results


async def hybrid_search_chunks_retry_adapter(
    search_fn,
    strategy: str,
    short_token: bool,
) -> List[Dict[str, Any]]:
    """
    Retry wrapper to avoid false negatives:
    - If strategy == 'lexical': force alpha=0.0, no rerank/MMR
    - If strategy == 'hybrid_lexical_first': try with given alpha, then alpha=0.0, then alpha=0.3
    - If short_token: automatically apply the lexical-first sequence
    """
    # normalize strategy
    strategy = strategy or "hybrid"

    async def run(alpha_override=None, rerank=False, mmr=False):
        return await asyncio.to_thread(search_fn, alpha_override, rerank, mmr)

    # For short tokens, treat as hybrid_lexical_first regardless of requested strategy
    effective_strategy = "hybrid_lexical_first" if short_token and strategy == "hybrid" else strategy

    if effective_strategy == "lexical":
        results = await run(alpha_override=0.0, rerank=False, mmr=False)
        return results

    if effective_strategy == "hybrid_lexical_first":
        results = await run(alpha_override=None, rerank=None, mmr=None)
        if not results:
            results = await run(alpha_override=0.0, rerank=False, mmr=False)
        if not results:
            results = await run(alpha_override=0.3, rerank=False, mmr=False)
        return results

    # default: original behavior
    return await run(alpha_override=None, rerank=None, mmr=None)


async def get_chunks_by_section(
    document_id: str,
    section_title: str,
    user_id: str,
    max_chunks: int = 50
) -> List[Dict[str, Any]]:
    """
    Retrieve chunks starting from a specific section header.
    
    Strategy:
    1. Find the first chunk matching the section title (the header).
    2. Fetch subsequent chunks by index to capture the body.
    3. Stop if a new section clearly starts (heuristic).
    
    Args:
        document_id: Document UUID
        section_title: Exact or partial title of the section
        user_id: User identifier for tenant scoping
        max_chunks: Maximum number of chunks to retrieve (safety limit)
        
    Returns:
        List of chunks sorted by index
    """
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped chunk retrieval")

    connection = get_connection()
    if not connection:
        raise RuntimeError("No Weaviate connection established")

    def _fetch():
        with connection.session() as client:
            from ..weaviate_helpers import get_user_collections
            chunk_collection, _ = get_user_collections(client, user_id)
            
            from weaviate.classes.query import Filter, Sort
            
            # Step 1: Find the start chunk
            section_filter = Filter.by_property("sectionTitle").like(f"*{section_title}*")
            doc_filter = Filter.by_property("documentId").equal(document_id)
            
            # Get just the first matching chunk to establish the start index
            start_response = chunk_collection.query.fetch_objects(
                filters=doc_filter & section_filter,
                sort=Sort.by_property("chunkIndex", ascending=True),
                limit=1,
                return_properties=["chunkIndex", "sectionTitle"]
            )
            
            if not start_response.objects:
                logger.info(f"No section header found for '{section_title}'")
                return []
                
            start_chunk = start_response.objects[0]
            start_index = start_chunk.properties.get("chunkIndex")
            found_title = start_chunk.properties.get("sectionTitle")
            
            logger.info(f"Found section '{found_title}' at index {start_index}. Reading forward...")
            
            # Step 2: Fetch range of chunks starting from start_index
            range_filter = Filter.by_property("chunkIndex").greater_or_equal(start_index)
            
            # Fetch a bit more than needed to detect next section
            fetch_limit = max_chunks + 5
            
            range_response = chunk_collection.query.fetch_objects(
                filters=doc_filter & range_filter,
                sort=Sort.by_property("chunkIndex", ascending=True),
                limit=fetch_limit,
                return_properties=[
                    "content", 
                    "chunkIndex", 
                    "sectionTitle", 
                    "pageNumber",
                    "metadata"
                ]
            )
            
            chunks = []
            for obj in range_response.objects:
                props = obj.properties
                current_title = props.get("sectionTitle")
                
                # REMOVED HEURISTIC: Do not stop on title change.
                # Subsections (e.g. "2.1 Genetics") often have different titles 
                # that don't contain the main section name ("Materials and Methods").
                # It is better to over-fetch and let the LLM filter than to miss content.
                
                chunks.append({
                    "text": props.get("content"),
                    "chunk_index": props.get("chunkIndex"),
                    "section_title": props.get("sectionTitle"),
                    "page_number": props.get("pageNumber"),
                    "metadata": props.get("metadata")
                })
                
                if len(chunks) >= max_chunks:
                    break
                
            return chunks

    # Async execution
    try:
        if hasattr(asyncio, 'to_thread'):
            return await asyncio.to_thread(_fetch)
        else:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _fetch)
    except RuntimeError as e:
        if "no running event loop" in str(e):
            return _fetch()
        raise



async def search_chunks_by_keyword(
    document_id: str,
    keyword: str,
    user_id: str,
    max_page: int = 3,
    limit: int = 3
) -> List[Dict[str, Any]]:
    """
    Simple keyword search in chunk content, filtered to early pages.

    This is a lightweight fallback for finding content like abstracts
    when they aren't in dedicated sections. Uses BM25 (lexical) search.

    Args:
        document_id: Document UUID
        keyword: Keyword to search for in content (case-insensitive)
        user_id: User identifier for tenant scoping
        max_page: Only search chunks on pages <= this value (default: 3)
        limit: Maximum number of chunks to return

    Returns:
        List of matching chunks sorted by page/index
    """
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped search")

    connection = get_connection()
    if not connection:
        raise RuntimeError("No Weaviate connection established")

    def _search():
        with connection.session() as client:
            from ..weaviate_helpers import get_user_collections
            chunk_collection, _ = get_user_collections(client, user_id)

            from weaviate.classes.query import Filter, Sort

            # Filter: document + early pages
            doc_filter = Filter.by_property("documentId").equal(document_id)
            page_filter = Filter.by_property("pageNumber").less_or_equal(max_page)

            # BM25 keyword search on content
            response = chunk_collection.query.bm25(
                query=keyword,
                filters=doc_filter & page_filter,
                limit=limit * 2,  # Get extra to filter
                return_properties=[
                    "content",
                    "pageNumber",
                    "chunkIndex",
                    "sectionTitle",
                    "parentSection"
                ]
            )

            if not response.objects:
                logger.debug(f"No chunks found with keyword '{keyword}' in pages 1-{max_page}")
                return []

            # Filter to chunks that actually contain the keyword
            chunks = []
            for obj in response.objects:
                props = obj.properties
                content = props.get("content", "")

                # Case-insensitive check for keyword in content
                if keyword.lower() in content.lower():
                    chunks.append({
                        "text": content,
                        "page_number": props.get("pageNumber"),
                        "chunk_index": props.get("chunkIndex"),
                        "section_title": props.get("sectionTitle"),
                        "parent_section": props.get("parentSection")
                    })

                if len(chunks) >= limit:
                    break

            if chunks:
                logger.info(f"Found {len(chunks)} chunks with keyword '{keyword}' in pages 1-{max_page}")

            return chunks

    try:
        if hasattr(asyncio, 'to_thread'):
            return await asyncio.to_thread(_search)
        else:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _search)
    except RuntimeError as e:
        if "no running event loop" in str(e):
            return _search()
        raise


async def get_chunks_from_index(
    document_id: str,
    start_index: int,
    user_id: str,
    max_chunks: int = 5,
    stop_on_section_change: bool = True
) -> List[Dict[str, Any]]:
    """
    Get consecutive chunks starting from a specific index.

    Useful for getting context around a found keyword - start at the keyword's
    chunk and grab subsequent chunks until section changes or limit reached.

    Args:
        document_id: Document UUID
        start_index: Starting chunk index
        user_id: User identifier for tenant scoping
        max_chunks: Maximum number of chunks to return
        stop_on_section_change: Stop when sectionTitle changes from the first chunk

    Returns:
        List of consecutive chunks starting from start_index
    """
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped chunk retrieval")

    connection = get_connection()
    if not connection:
        raise RuntimeError("No Weaviate connection established")

    def _fetch():
        with connection.session() as client:
            from ..weaviate_helpers import get_user_collections
            chunk_collection, _ = get_user_collections(client, user_id)

            from weaviate.classes.query import Filter, Sort

            doc_filter = Filter.by_property("documentId").equal(document_id)
            index_filter = Filter.by_property("chunkIndex").greater_or_equal(start_index)

            response = chunk_collection.query.fetch_objects(
                filters=doc_filter & index_filter,
                sort=Sort.by_property("chunkIndex", ascending=True),
                limit=max_chunks + 2,  # Get a few extra for section boundary detection
                return_properties=[
                    "content",
                    "pageNumber",
                    "chunkIndex",
                    "sectionTitle",
                    "parentSection"
                ]
            )

            if not response.objects:
                return []

            chunks = []
            first_section = None

            for obj in response.objects:
                props = obj.properties
                section = props.get("sectionTitle") or props.get("parentSection")

                # Track the first section
                if first_section is None:
                    first_section = section

                # Stop if section changes (and we have at least one chunk)
                if stop_on_section_change and chunks and section != first_section:
                    logger.debug(f"Stopping at chunk {props.get('chunkIndex')} - section changed from '{first_section}' to '{section}'")
                    break

                chunks.append({
                    "text": props.get("content"),
                    "page_number": props.get("pageNumber"),
                    "chunk_index": props.get("chunkIndex"),
                    "section_title": props.get("sectionTitle"),
                    "parent_section": props.get("parentSection")
                })

                if len(chunks) >= max_chunks:
                    break

            return chunks

    try:
        if hasattr(asyncio, 'to_thread'):
            return await asyncio.to_thread(_fetch)
        else:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _fetch)
    except RuntimeError as e:
        if "no running event loop" in str(e):
            return _fetch()
        raise


async def get_document_sections(
    document_id: str,
    user_id: str
) -> List[Dict[str, Any]]:
    """
    Get a list of all unique sections in a document.

    Returns sections with their page numbers and chunk counts,
    useful for showing the agent what sections are available.

    Args:
        document_id: Document UUID
        user_id: User identifier for tenant scoping

    Returns:
        List of section info dicts: [{"title": str, "page_number": int, "chunk_count": int}]
    """
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped section listing")

    connection = get_connection()
    if not connection:
        raise RuntimeError("No Weaviate connection established")

    def _fetch():
        with connection.session() as client:
            from ..weaviate_helpers import get_user_collections
            chunk_collection, _ = get_user_collections(client, user_id)

            from weaviate.classes.query import Filter, Sort
            from collections import defaultdict

            doc_filter = Filter.by_property("documentId").equal(document_id)

            # Fetch all chunks to extract unique sections
            # Using aggregate would be better but Weaviate v4 aggregate is limited
            response = chunk_collection.query.fetch_objects(
                filters=doc_filter,
                sort=Sort.by_property("chunkIndex", ascending=True),
                limit=500,  # Safety limit - most papers have < 500 chunks
                return_properties=["sectionTitle", "pageNumber", "chunkIndex"]
            )

            # Group by section title
            sections = defaultdict(lambda: {"pages": set(), "chunks": 0, "first_index": float('inf')})

            for obj in response.objects:
                props = obj.properties
                title = props.get("sectionTitle") or "Untitled"
                page = props.get("pageNumber", 1)
                idx = props.get("chunkIndex", 0)

                sections[title]["pages"].add(page)
                sections[title]["chunks"] += 1
                sections[title]["first_index"] = min(sections[title]["first_index"], idx)

            # Convert to sorted list
            result = []
            for title, info in sorted(sections.items(), key=lambda x: x[1]["first_index"]):
                pages = sorted(info["pages"])
                result.append({
                    "title": title,
                    "page_numbers": pages,
                    "start_page": pages[0] if pages else 1,
                    "chunk_count": info["chunks"]
                })

            return result

    # Async execution
    try:
        if hasattr(asyncio, 'to_thread'):
            return await asyncio.to_thread(_fetch)
        else:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _fetch)
    except RuntimeError as e:
        if "no running event loop" in str(e):
            return _fetch()
        raise


async def get_document_hierarchy(
    document_id: str,
    user_id: str
) -> Optional[Dict[str, Any]]:
    """
    Get the LLM-resolved document hierarchy from the PostgreSQL database.

    This retrieves the hierarchy_metadata column which contains:
    - sections: List of all headers with parent_section, subsection, is_top_level
    - top_level_sections: List of top-level section names in order
    - created_at: When hierarchy was resolved
    - model_used: LLM model used for resolution
    - llm_raw_response: Raw LLM response for debugging

    Args:
        document_id: Document UUID
        user_id: User identifier (for logging/validation)

    Returns:
        Hierarchy metadata dict or None if not available
    """
    from src.models.sql.database import SessionLocal
    from src.models.sql.pdf_document import PDFDocument
    from uuid import UUID

    def _fetch():
        session = SessionLocal()
        try:
            doc = session.query(PDFDocument).filter(
                PDFDocument.id == UUID(document_id)
            ).first()

            if doc and doc.hierarchy_metadata:
                logger.info(f"Retrieved hierarchy metadata for document {document_id}")
                return doc.hierarchy_metadata
            else:
                logger.info(f"No hierarchy metadata found for document {document_id}")
                return None
        except Exception as e:
            logger.error(f"Error retrieving hierarchy metadata: {e}")
            return None
        finally:
            session.close()

    try:
        if hasattr(asyncio, 'to_thread'):
            return await asyncio.to_thread(_fetch)
        else:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _fetch)
    except RuntimeError as e:
        if "no running event loop" in str(e):
            return _fetch()
        raise


async def get_chunks_by_parent_section(
    document_id: str,
    parent_section: str,
    user_id: str,
    max_chunks: int = 50
) -> List[Dict[str, Any]]:
    """
    Retrieve all chunks belonging to a top-level section (e.g., Methods, Results).

    Uses the LLM-resolved parentSection field for accurate section boundaries.
    Unlike get_chunks_by_section, this respects the hierarchical structure.

    Args:
        document_id: Document UUID
        parent_section: Top-level section name (e.g., "Methods", "Results", "Abstract")
        user_id: User identifier for tenant scoping
        max_chunks: Maximum number of chunks to retrieve

    Returns:
        List of chunks sorted by index
    """
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped chunk retrieval")

    connection = get_connection()
    if not connection:
        raise RuntimeError("No Weaviate connection established")

    def _fetch():
        with connection.session() as client:
            from ..weaviate_helpers import get_user_collections
            chunk_collection, _ = get_user_collections(client, user_id)

            from weaviate.classes.query import Filter, Sort

            # Filter by document AND parentSection (case-insensitive like match)
            doc_filter = Filter.by_property("documentId").equal(document_id)
            section_filter = Filter.by_property("parentSection").like(f"*{parent_section}*")

            response = chunk_collection.query.fetch_objects(
                filters=doc_filter & section_filter,
                sort=Sort.by_property("chunkIndex", ascending=True),
                limit=max_chunks,
                return_properties=[
                    "content",
                    "chunkIndex",
                    "sectionTitle",
                    "parentSection",
                    "subsection",
                    "isTopLevel",
                    "pageNumber",
                    "metadata",
                    "docItemProvenance"
                ]
            )

            if not response.objects:
                logger.info(f"No chunks found for parent section '{parent_section}'")
                return []

            logger.info(f"Found {len(response.objects)} chunks for parent section '{parent_section}'")

            chunks = []
            for obj in response.objects:
                props = obj.properties

                # Parse docItemProvenance
                doc_items = []
                doc_items_str = props.get("docItemProvenance")
                if doc_items_str:
                    try:
                        doc_items = json.loads(doc_items_str) if isinstance(doc_items_str, str) else doc_items_str
                    except json.JSONDecodeError:
                        pass

                chunks.append({
                    "text": props.get("content"),
                    "chunk_index": props.get("chunkIndex"),
                    "section_title": props.get("sectionTitle"),
                    "parent_section": props.get("parentSection"),
                    "subsection": props.get("subsection"),
                    "is_top_level": props.get("isTopLevel"),
                    "page_number": props.get("pageNumber"),
                    "metadata": props.get("metadata"),
                    "doc_items": doc_items
                })

            return chunks

    try:
        if hasattr(asyncio, 'to_thread'):
            return await asyncio.to_thread(_fetch)
        else:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _fetch)
    except RuntimeError as e:
        if "no running event loop" in str(e):
            return _fetch()
        raise


async def get_chunks_by_subsection(
    document_id: str,
    parent_section: str,
    subsection: str,
    user_id: str,
    max_chunks: int = 30
) -> List[Dict[str, Any]]:
    """
    Retrieve chunks for a specific subsection within a parent section.

    Uses the LLM-resolved subsection field for accurate targeting.
    Example: get_chunks_by_subsection(doc_id, "Methods", "Fly Strains", user_id)

    Args:
        document_id: Document UUID
        parent_section: Top-level section name (e.g., "Methods")
        subsection: Subsection name (e.g., "Fly Strains", "Cell Culture")
        user_id: User identifier for tenant scoping
        max_chunks: Maximum number of chunks to retrieve

    Returns:
        List of chunks sorted by index
    """
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped chunk retrieval")

    connection = get_connection()
    if not connection:
        raise RuntimeError("No Weaviate connection established")

    def _fetch():
        with connection.session() as client:
            from ..weaviate_helpers import get_user_collections
            chunk_collection, _ = get_user_collections(client, user_id)

            from weaviate.classes.query import Filter, Sort

            # Filter by document AND parentSection AND subsection
            doc_filter = Filter.by_property("documentId").equal(document_id)
            parent_filter = Filter.by_property("parentSection").like(f"*{parent_section}*")
            sub_filter = Filter.by_property("subsection").like(f"*{subsection}*")

            response = chunk_collection.query.fetch_objects(
                filters=doc_filter & parent_filter & sub_filter,
                sort=Sort.by_property("chunkIndex", ascending=True),
                limit=max_chunks,
                return_properties=[
                    "content",
                    "chunkIndex",
                    "sectionTitle",
                    "parentSection",
                    "subsection",
                    "isTopLevel",
                    "pageNumber",
                    "metadata",
                    "docItemProvenance"
                ]
            )

            if not response.objects:
                logger.info(f"No chunks found for subsection '{subsection}' in '{parent_section}'")
                return []

            logger.info(f"Found {len(response.objects)} chunks for subsection '{subsection}' in '{parent_section}'")

            chunks = []
            for obj in response.objects:
                props = obj.properties

                # Parse docItemProvenance
                doc_items = []
                doc_items_str = props.get("docItemProvenance")
                if doc_items_str:
                    try:
                        doc_items = json.loads(doc_items_str) if isinstance(doc_items_str, str) else doc_items_str
                    except json.JSONDecodeError:
                        pass

                chunks.append({
                    "text": props.get("content"),
                    "chunk_index": props.get("chunkIndex"),
                    "section_title": props.get("sectionTitle"),
                    "parent_section": props.get("parentSection"),
                    "subsection": props.get("subsection"),
                    "is_top_level": props.get("isTopLevel"),
                    "page_number": props.get("pageNumber"),
                    "metadata": props.get("metadata"),
                    "doc_items": doc_items
                })

            return chunks

    try:
        if hasattr(asyncio, 'to_thread'):
            return await asyncio.to_thread(_fetch)
        else:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _fetch)
    except RuntimeError as e:
        if "no running event loop" in str(e):
            return _fetch()
        raise


async def get_document_sections_hierarchical(
    document_id: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Get document sections organized hierarchically (top-level -> subsections).

    Returns a structured hierarchy useful for display and navigation:
    {
        "sections": [
            {
                "name": "Methods",
                "is_top_level": True,
                "page_numbers": [2, 3],
                "chunk_count": 15,
                "subsections": [
                    {"name": "Fly Strains", "page_numbers": [2], "chunk_count": 3},
                    {"name": "Cell Culture", "page_numbers": [2, 3], "chunk_count": 5}
                ]
            },
            ...
        ],
        "top_level_sections": ["TITLE", "Abstract", "Introduction", "Methods", "Results", "Discussion", "References"]
    }

    Args:
        document_id: Document UUID
        user_id: User identifier for tenant scoping

    Returns:
        Hierarchical structure of document sections
    """
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped section listing")

    connection = get_connection()
    if not connection:
        raise RuntimeError("No Weaviate connection established")

    def _fetch():
        with connection.session() as client:
            from ..weaviate_helpers import get_user_collections
            chunk_collection, _ = get_user_collections(client, user_id)

            from weaviate.classes.query import Filter, Sort
            from collections import defaultdict

            doc_filter = Filter.by_property("documentId").equal(document_id)

            response = chunk_collection.query.fetch_objects(
                filters=doc_filter,
                sort=Sort.by_property("chunkIndex", ascending=True),
                limit=500,
                return_properties=[
                    "parentSection",
                    "subsection",
                    "isTopLevel",
                    "pageNumber",
                    "chunkIndex"
                ]
            )

            # Build hierarchical structure
            sections = defaultdict(lambda: {
                "subsections": defaultdict(lambda: {"pages": set(), "chunks": 0, "first_index": float('inf')}),
                "pages": set(),
                "chunks": 0,
                "first_index": float('inf'),
                "is_top_level": True
            })

            top_level_order = []

            for obj in response.objects:
                props = obj.properties
                parent = props.get("parentSection") or "Unknown"
                subsection = props.get("subsection")
                page = props.get("pageNumber", 1)
                idx = props.get("chunkIndex", 0)

                # Track order of first occurrence
                if parent not in top_level_order:
                    top_level_order.append(parent)

                sections[parent]["pages"].add(page)
                sections[parent]["chunks"] += 1
                sections[parent]["first_index"] = min(sections[parent]["first_index"], idx)

                if subsection:
                    sections[parent]["subsections"][subsection]["pages"].add(page)
                    sections[parent]["subsections"][subsection]["chunks"] += 1
                    sections[parent]["subsections"][subsection]["first_index"] = min(
                        sections[parent]["subsections"][subsection]["first_index"], idx
                    )

            # Convert to output format
            result = []
            for parent in top_level_order:
                info = sections[parent]
                pages = sorted(info["pages"])

                # Sort subsections by first_index
                subs = []
                for sub_name, sub_info in sorted(
                    info["subsections"].items(),
                    key=lambda x: x[1]["first_index"]
                ):
                    sub_pages = sorted(sub_info["pages"])
                    subs.append({
                        "name": sub_name,
                        "page_numbers": sub_pages,
                        "chunk_count": sub_info["chunks"]
                    })

                result.append({
                    "name": parent,
                    "is_top_level": True,
                    "page_numbers": pages,
                    "chunk_count": info["chunks"],
                    "subsections": subs
                })

            # Also fetch abstract_section_title from hierarchy_metadata in PostgreSQL
            # This was identified by LLM during document processing
            abstract_section_title = None
            try:
                from src.models.sql.database import SessionLocal
                from src.models.sql.pdf_document import PDFDocument
                from uuid import UUID

                session = SessionLocal()
                try:
                    doc = session.query(PDFDocument).filter(
                        PDFDocument.id == UUID(document_id)
                    ).first()
                    if doc and doc.hierarchy_metadata:
                        abstract_section_title = doc.hierarchy_metadata.get("abstract_section_title")
                finally:
                    session.close()
            except Exception as e:
                logger.warning(f"Could not fetch abstract_section_title from database: {e}")

            return {
                "sections": result,
                "top_level_sections": top_level_order,
                "abstract_section_title": abstract_section_title
            }

    try:
        if hasattr(asyncio, 'to_thread'):
            return await asyncio.to_thread(_fetch)
        else:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _fetch)
    except RuntimeError as e:
        if "no running event loop" in str(e):
            return _fetch()
        raise


def delete_chunks(document_id: str, user_id: str) -> Dict[str, Any]:
    """Delete all chunks for a document.

    Args:
        document_id: Parent document UUID
        user_id: User identifier for tenant scoping (required, FR-011, FR-014)

    Returns:
        Operation result dictionary

    Raises:
        ValueError: If user_id is None (required for tenant scoping)
    """
    # T038: Validate user_id is provided (required for tenant scoping)
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped chunk deletion (FR-011, FR-014)")

    connection = get_connection()
    if not connection:
        raise RuntimeError("No Weaviate connection established")

    with connection.session() as client:
        try:
            # T038: Get tenant-scoped collections (FR-011: user-specific data isolation)
            from ..weaviate_helpers import get_user_collections
            chunk_collection, pdf_collection = get_user_collections(client, user_id)

            # Delete all chunks for this document using v4 API
            from weaviate.classes.query import Filter
            result = chunk_collection.data.delete_many(
                where=Filter.by_property("documentId").equal(document_id)
            )

            # Update document chunk count using v4 API
            pdf_collection.data.update(
                uuid=document_id,
                properties={
                    "chunkCount": 0,
                    "vectorCount": 0
                }
            )

            deleted_count = result.get("results", {}).get("successful", 0)
            logger.info(f"Deleted {deleted_count} chunks for document {document_id}")

            return {
                "success": True,
                "message": f"Deleted {deleted_count} chunks successfully",
                "documentId": document_id,
                "deletedCount": deleted_count
            }

        except Exception as e:
            logger.error(f"Failed to delete chunks: {e}")
            return {
                "success": False,
                "message": f"Failed to delete chunks: {e}",
                "documentId": document_id,
                "error": {
                    "code": "CHUNK_DELETE_FAILED",
                    "details": str(e)
                }
            }


def update_chunk_embeddings(chunk_id: str, vector: List[float], user_id: str) -> Dict[str, Any]:
    """Update the embedding vector for a chunk.

    Args:
        chunk_id: Chunk UUID
        vector: Embedding vector
        user_id: User identifier for tenant scoping (required, FR-011, FR-014)

    Returns:
        Operation result dictionary

    Raises:
        ValueError: If user_id is None (required for tenant scoping)
    """
    # T038: Validate user_id is provided (required for tenant scoping)
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped chunk embedding updates (FR-011, FR-014)")

    connection = get_connection()
    if not connection:
        raise RuntimeError("No Weaviate connection established")

    with connection.session() as client:
        try:
            # T038: Get tenant-scoped collection (FR-011: user-specific data isolation)
            from ..weaviate_helpers import get_user_collections
            chunk_collection, pdf_collection = get_user_collections(client, user_id)
            collection = chunk_collection

            # Update chunk with vector using v4 API
            collection.data.update(
                uuid=chunk_id,
                properties={},  # Empty properties since we're only updating the vector
                vector=vector
            )

            logger.info(f"Updated embedding for chunk {chunk_id}")

            return {
                "success": True,
                "message": "Chunk embedding updated successfully",
                "chunkId": chunk_id
            }

        except Exception as e:
            logger.error(f"Failed to update chunk embedding: {e}")
            return {
                "success": False,
                "message": f"Failed to update chunk embedding: {e}",
                "chunkId": chunk_id,
                "error": {
                    "code": "EMBEDDING_UPDATE_FAILED",
                    "details": str(e)
                }
            }


# Async wrapper functions for FastAPI endpoints
async def get_chunks(document_id: str, pagination: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    """Get chunks for a document using Weaviate v4 collections API.

    Args:
        document_id: Parent document UUID
        pagination: Dict with page, page_size, include_metadata
        user_id: User identifier for tenant scoping (required, FR-011, FR-014)

    Returns:
        Dictionary with chunks and total count

    Raises:
        ValueError: If user_id is None (required for tenant scoping)
    """
    # T038: Validate user_id is provided (required for tenant scoping)
    if not user_id:
        raise ValueError("user_id is required for tenant-scoped chunk retrieval (FR-011, FR-014)")
    import asyncio
    from weaviate.classes.query import Filter, Sort

    # Extract pagination parameters
    page = pagination.get("page", 1)
    page_size = pagination.get("page_size", 50)
    include_metadata = pagination.get("include_metadata", True)

    # Calculate offset for pagination
    offset = (page - 1) * page_size

    # Define the sync function to run in executor
    def _fetch_page():
        connection = get_connection()
        if not connection:
            raise RuntimeError("No Weaviate connection established")

        with connection.session() as client:
            try:
                # T038: Get tenant-scoped collection (FR-011: user-specific data isolation)
                from ..weaviate_helpers import get_user_collections
                chunk_collection, pdf_collection = get_user_collections(client, user_id)
                collection = chunk_collection

                # Main page query - fetch_objects uses filters parameter
                response = collection.query.fetch_objects(
                    filters=Filter.by_property("documentId").equal(document_id),
                    sort=Sort.by_property("chunkIndex", ascending=True),
                    limit=page_size,
                    offset=offset,
                    include_vector=False,
                    return_properties=[
                        "chunkIndex",
                        "content",
                        "contentPreview",
                        "elementType",
                        "pageNumber",
                        "sectionTitle",
                        "metadata",
                        "documentId",
                        "docItemProvenance"  # Add provenance field
                    ]
                )

                # Extract chunks from response and map to expected field names
                chunks = []
                for obj in response.objects:
                    # Parse metadata if it's a string
                    metadata = obj.properties.get("metadata", {})
                    if isinstance(metadata, str):
                        try:
                            import json
                            metadata = json.loads(metadata)
                        except:
                            metadata = {}

                    # Ensure required metadata fields exist
                    if not isinstance(metadata, dict):
                        metadata = {}

                    # Add default values for required fields if missing
                    content = obj.properties.get("content", "")
                    metadata.setdefault("character_count", len(content))
                    metadata.setdefault("word_count", len(content.split()))

                    # Convert UUID to string - Weaviate returns _WeaviateUUIDInt objects
                    chunk_id = str(obj.uuid) if hasattr(obj, 'uuid') else str(obj.properties.get("chunkIndex", 0))

                    # Parse docItemProvenance if it exists
                    doc_items = []
                    doc_items_str = obj.properties.get("docItemProvenance")
                    if doc_items_str:
                        try:
                            doc_items = json.loads(doc_items_str)
                        except:
                            logger.warning(f"Failed to parse docItemProvenance for chunk {chunk_id}")
                            doc_items = []

                    chunk_data = {
                        "id": chunk_id,
                        "document_id": document_id,  # We know this from the filter
                        "chunk_index": obj.properties.get("chunkIndex"),
                        "content": content,
                        "contentPreview": obj.properties.get("contentPreview"),
                        "element_type": obj.properties.get("elementType"),
                        "page_number": obj.properties.get("pageNumber"),
                        "sectionTitle": obj.properties.get("sectionTitle"),
                        "metadata": metadata,  # Now properly parsed with required fields
                        "doc_items": doc_items  # Include provenance data
                    }
                    chunks.append(chunk_data)

                # Get total count for pagination using aggregate
                # Note: aggregate.over_all() doesn't take where/filter directly
                # We need to count manually from the filtered results
                total_response = collection.query.fetch_objects(
                    filters=Filter.by_property("documentId").equal(document_id),
                    limit=1000,  # Get max to count
                    include_vector=False,
                    return_properties=["chunkIndex"]  # Minimal data for counting
                )
                total = len(total_response.objects)

                return {
                    "chunks": chunks,
                    "total": total if total else 0
                }

            except Exception as e:
                logger.error(f"Failed to get chunks: {e}", exc_info=True)
                raise

    # Use asyncio.to_thread for Python 3.9+ or fall back to run_in_executor
    try:
        if hasattr(asyncio, 'to_thread'):
            result = await asyncio.to_thread(_fetch_page)
        else:
            # Fallback for Python < 3.9
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _fetch_page)
    except Exception as e:
        logger.error(f"Error fetching chunks: {e}")
        raise

    return result
