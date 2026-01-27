"""Document chunking stage of the processing pipeline."""

import logging
from typing import List, Dict, Any

from src.models.chunk import (
    DocumentChunk,
    ElementType,
    ChunkMetadata,
    ChunkDocItemProvenance,
    ChunkBoundingBox,
)
from src.models.strategy import ChunkingStrategy

logger = logging.getLogger(__name__)


class ChunkingError(Exception):
    """Exception raised during document chunking."""
    pass


async def chunk_parsed_document(
    elements: List[Dict[str, Any]],
    strategy: ChunkingStrategy,
    document_id: str
) -> List[DocumentChunk]:
    """Chunk parsed document elements according to strategy.

    Args:
        elements: Parsed elements from Docling
        strategy: Chunking strategy to apply
        document_id: Document UUID

    Returns:
        List of DocumentChunk objects

    Raises:
        ChunkingError: If chunking fails
    """
    filtered_elements = [
        element
        for element in elements
        if element.get("metadata", {}).get("doc_item_label") != "page_footer"
    ]

    if not filtered_elements:
        raise ChunkingError("No elements to chunk")

    if len(filtered_elements) != len(elements):
        logger.debug(
            "Filtered %d footer elements from chunking input",
            len(elements) - len(filtered_elements),
        )

    elements = filtered_elements

    if not elements:
        raise ChunkingError("No elements to chunk")

    logger.info(f"Starting chunking with strategy: {strategy.strategy_name}")

    try:
        # Group elements by chunking method
        if strategy.chunking_method == "by_title":
            chunks = _chunk_by_title(elements, strategy)
        elif strategy.chunking_method == "by_paragraph":
            chunks = _chunk_by_paragraph(elements, strategy)
        elif strategy.chunking_method == "by_character":
            chunks = _chunk_by_character(elements, strategy)
        elif strategy.chunking_method == "by_sentence":
            chunks = _chunk_by_sentence(elements, strategy)
        else:
            raise ChunkingError(f"Unknown chunking method: {strategy.chunking_method}")

        # Convert to DocumentChunk objects
        document_chunks = []
        for idx, chunk_data in enumerate(chunks):
            chunk = _create_document_chunk(
                chunk_data=chunk_data,
                chunk_index=idx,
                document_id=document_id,
                strategy=strategy
            )
            document_chunks.append(chunk)

        # Assign chunk indices
        document_chunks = assign_chunk_indices(document_chunks)

        logger.info(f"Created {len(document_chunks)} chunks")
        return document_chunks

    except Exception as e:
        error_msg = f"Chunking failed: {str(e)}"
        logger.error(error_msg)
        raise ChunkingError(error_msg) from e


def _chunk_by_title(
    elements: List[Dict[str, Any]],
    strategy: ChunkingStrategy
) -> List[Dict[str, Any]]:
    """Chunk elements by title boundaries.

    Groups content under each title, respecting max_characters limit.
    """
    chunks = []
    current_chunk = {
        "content": "",
        "elements": [],
        "metadata": {}
    }

    for element in elements:
        element_type = element.get("type", "")
        element_text = element.get("text", "")

        # Start new chunk on title
        if element_type == "Title" and current_chunk["content"]:
            chunks.append(current_chunk)
            # Create overlap if configured
            overlap_text = ""
            if strategy.overlap_characters > 0:
                overlap_text = current_chunk["content"][-strategy.overlap_characters:]

            current_chunk = {
                "content": overlap_text + element_text,
                "elements": [element],
                "metadata": dict(element.get("metadata", {}))
            }
        else:
            # Add to current chunk if within size limit
            new_content = current_chunk["content"] + "\n" + element_text if current_chunk["content"] else element_text

            if len(new_content) > strategy.max_characters:
                # Save current chunk and start new one
                if current_chunk["content"]:
                    chunks.append(current_chunk)

                # Create overlap
                overlap_text = ""
                if strategy.overlap_characters > 0 and current_chunk["content"]:
                    overlap_text = current_chunk["content"][-strategy.overlap_characters:]

                current_chunk = {
                    "content": overlap_text + element_text,
                    "elements": [element],
                    "metadata": dict(element.get("metadata", {}))
                }
            else:
                current_chunk["content"] = new_content
                current_chunk["elements"].append(element)
                if not current_chunk["metadata"]:
                    current_chunk["metadata"] = dict(element.get("metadata", {}))

    # Add final chunk
    if current_chunk["content"]:
        chunks.append(current_chunk)

    return chunks


def _chunk_by_paragraph(
    elements: List[Dict[str, Any]],
    strategy: ChunkingStrategy
) -> List[Dict[str, Any]]:
    """Chunk elements by paragraph boundaries."""
    chunks = []
    current_chunk = {
        "content": "",
        "elements": [],
        "metadata": {}
    }

    for element in elements:
        element_text = element.get("text", "")

        # Check if adding this element exceeds max_characters
        new_content = current_chunk["content"] + "\n\n" + element_text if current_chunk["content"] else element_text

        if len(new_content) > strategy.max_characters:
            # Save current chunk
            if current_chunk["content"]:
                chunks.append(current_chunk)

            # Create overlap
            overlap_text = ""
            if strategy.overlap_characters > 0 and current_chunk["content"]:
                overlap_text = current_chunk["content"][-strategy.overlap_characters:]

            current_chunk = {
                "content": overlap_text + element_text,
                "elements": [element],
                "metadata": dict(element.get("metadata", {}))
            }
        else:
            current_chunk["content"] = new_content
            current_chunk["elements"].append(element)
            if not current_chunk["metadata"]:
                current_chunk["metadata"] = dict(element.get("metadata", {}))

    # Add final chunk
    if current_chunk["content"]:
        chunks.append(current_chunk)

    return chunks


def _chunk_by_character(
    elements: List[Dict[str, Any]],
    strategy: ChunkingStrategy
) -> List[Dict[str, Any]]:
    """Chunk elements by character count."""
    # Combine all text
    full_text = "\n\n".join(element.get("text", "") for element in elements)

    chunks = []
    start = 0

    while start < len(full_text):
        # Calculate end position
        end = min(start + strategy.max_characters, len(full_text))

        # Try to break at a natural boundary (space, newline)
        if end < len(full_text):
            for i in range(end, max(start, end - 100), -1):
                if full_text[i] in (' ', '\n', '.', '!', '?'):
                    end = i + 1
                    break

        chunk_content = full_text[start:end]

        chunks.append({
            "content": chunk_content,
            "elements": [],  # Character chunking doesn't preserve element boundaries
            "metadata": {"start_char": start, "end_char": end}
        })

        # Move start position with overlap
        start = end - strategy.overlap_characters if strategy.overlap_characters > 0 else end

    return chunks


def _chunk_by_sentence(
    elements: List[Dict[str, Any]],
    strategy: ChunkingStrategy
) -> List[Dict[str, Any]]:
    """Chunk elements by sentence boundaries."""
    import re

    # Combine all text
    full_text = "\n\n".join(element.get("text", "") for element in elements)

    # Split into sentences (simple regex approach)
    sentences = re.split(r'(?<=[.!?])\s+', full_text)

    chunks = []
    current_chunk = {
        "content": "",
        "sentences": [],
        "metadata": {}
    }

    for sentence in sentences:
        new_content = current_chunk["content"] + " " + sentence if current_chunk["content"] else sentence

        if len(new_content) > strategy.max_characters:
            # Save current chunk
            if current_chunk["content"]:
                chunks.append({
                    "content": current_chunk["content"],
                    "elements": [],
                    "metadata": current_chunk["metadata"]
                })

            # Create overlap
            overlap_text = ""
            if strategy.overlap_characters > 0 and current_chunk["sentences"]:
                # Use last few sentences for overlap
                overlap_sentences = []
                overlap_len = 0
                for sent in reversed(current_chunk["sentences"]):
                    overlap_sentences.insert(0, sent)
                    overlap_len += len(sent)
                    if overlap_len >= strategy.overlap_characters:
                        break
                overlap_text = " ".join(overlap_sentences) + " "

            current_chunk = {
                "content": overlap_text + sentence,
                "sentences": [sentence],
                "metadata": {}
            }
        else:
            current_chunk["content"] = new_content
            current_chunk["sentences"].append(sentence)

    # Add final chunk
    if current_chunk["content"]:
        chunks.append({
            "content": current_chunk["content"],
            "elements": [],
            "metadata": current_chunk["metadata"]
        })

    return chunks


def _create_document_chunk(
    chunk_data: Dict[str, Any],
    chunk_index: int,
    document_id: str,
    strategy: ChunkingStrategy
) -> DocumentChunk:
    """Create a DocumentChunk object from chunk data."""
    # Generate chunk ID
    chunk_id = f"{document_id}_chunk_{chunk_index:04d}"

    # Extract page number from elements if available
    page_number = 1  # Default
    if chunk_data.get("elements"):
        for element in chunk_data["elements"]:
            metadata = element.get("metadata", {})
            if metadata.get("page_number") is not None:
                page_number = metadata.get("page_number")
                break
            provenance = metadata.get("provenance")
            if provenance:
                page_number = provenance[0].get("page_no")
                if page_number is not None:
                    break
    try:
        page_number = int(page_number)
        if page_number <= 0:
            page_number = 1
    except (TypeError, ValueError):  # pragma: no cover - fallback path
        page_number = 1

    # Determine element type
    element_type = ElementType.NARRATIVE_TEXT  # Default
    if chunk_data.get("elements"):
        first_element_type = chunk_data["elements"][0].get("type", "")
        if first_element_type == "Title":
            element_type = ElementType.TITLE
        elif first_element_type == "Table":
            element_type = ElementType.TABLE
        elif first_element_type == "ListItem":
            element_type = ElementType.LIST_ITEM

    # Build provenance list from contributing elements
    doc_items: List[ChunkDocItemProvenance] = []
    elements = chunk_data.get("elements", [])

    for idx, element in enumerate(elements):
        metadata = element.get("metadata") or {}

        element_id = metadata.get("element_id") or metadata.get("doc_item_id")
        if not element_id:
            # Fallback to deterministic identifier if Docling metadata lacks one
            element_id = f"{document_id}_element_{element.get('index', len(doc_items))}"

        doc_item_label = metadata.get("doc_item_label")
        provenance_entries = metadata.get("provenance") or []

        if not provenance_entries:
            # Some Docling responses include bbox at metadata root
            if metadata.get("bbox"):
                provenance_entries = [{
                    "page_no": metadata.get("page_number", page_number),
                    "bbox": metadata["bbox"],
                }]

        for prov_idx, prov in enumerate(provenance_entries):
            page_no = prov.get("page_no", metadata.get("page_number", page_number))
            try:
                page = int(page_no)
            except (TypeError, ValueError):
                page = page_number
            if page < 1:
                page = 1

            bbox_data = prov.get("bbox") or metadata.get("bbox")
            if not bbox_data:
                continue

            try:
                bbox = ChunkBoundingBox(
                    left=float(bbox_data.get("left", 0.0)),
                    top=float(bbox_data.get("top", 0.0)),
                    right=float(bbox_data.get("right", 0.0)),
                    bottom=float(bbox_data.get("bottom", 0.0)),
                    coord_origin=bbox_data.get("coord_origin", 'BOTTOMLEFT')
                )
            except (TypeError, ValueError):
                continue

            doc_item = ChunkDocItemProvenance(
                element_id=element_id,
                page=page,
                doc_item_label=doc_item_label,
                bbox=bbox,
            )
            doc_items.append(doc_item)

    # Create metadata
    elements_meta = chunk_data.get("elements", [])
    has_table = any(el.get("type") == "Table" for el in elements_meta)
    has_image = any(
        (el.get("metadata") or {}).get("content_type") in {"figure", "image", "picture"}
        for el in elements_meta
    )

    chunk_metadata = chunk_data.get("metadata") or {}
    section_title = chunk_metadata.get("section_title")
    section_path = chunk_metadata.get("section_path")
    content_type_meta = chunk_metadata.get("content_type")

    # Extract hierarchy fields from LLM-based section resolution
    parent_section = chunk_metadata.get("parent_section")
    subsection = chunk_metadata.get("subsection")
    is_top_level = chunk_metadata.get("is_top_level")

    metadata = ChunkMetadata(
        character_count=len(chunk_data["content"]),
        word_count=len(chunk_data["content"].split()),
        has_table=has_table,
        has_image=has_image,
        chunking_strategy=strategy.strategy_name,
        section_path=section_path,
        content_type=content_type_meta,
        doc_items=doc_items,
    )

    chunk_obj = DocumentChunk(
        id=chunk_id,
        document_id=document_id,
        chunk_index=chunk_index,
        content=chunk_data["content"],
        element_type=element_type,
        page_number=page_number,
        section_title=section_title,
        section_path=section_path,
        parent_section=parent_section,
        subsection=subsection,
        is_top_level=is_top_level,
        doc_items=doc_items,
        metadata=metadata,
    )
    return chunk_obj


def assign_chunk_indices(chunks: List[DocumentChunk]) -> List[DocumentChunk]:
    """Assign sequential indices to chunks.

    Args:
        chunks: List of DocumentChunk objects

    Returns:
        List of chunks with updated indices
    """
    for idx, chunk in enumerate(chunks):
        chunk.chunk_index = idx
        # Update chunk ID to reflect correct index
        chunk.id = f"{chunk.document_id}_chunk_{idx:04d}"

    return chunks


async def store_chunks_batch(
    chunks: List[DocumentChunk],
    batch_size: int = 100
) -> int:
    """Store chunks in batches (placeholder for database storage).

    Args:
        chunks: List of DocumentChunk objects
        batch_size: Number of chunks per batch

    Returns:
        Number of chunks stored
    """
    stored_count = 0

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        # This would store to database
        # For now, just count
        stored_count += len(batch)
        logger.debug(f"Stored batch of {len(batch)} chunks")

    logger.info(f"Stored total of {stored_count} chunks")
    return stored_count


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
    # Will be integrated with tracker module
