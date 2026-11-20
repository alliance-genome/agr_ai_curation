"""Document chunking stage of the processing pipeline."""

import logging
import re
from typing import List, Dict, Any

from src.models.chunk import (
    DocumentChunk,
    ElementType,
    ChunkMetadata,
    ChunkTableSegment,
    ChunkDocItemProvenance,
    ChunkBoundingBox,
)
from src.models.strategy import ChunkingStrategy
from src.lib.pipeline.table_chunking import (
    current_table_header_rule,
    layout_table,
    pack_table_rows,
)

logger = logging.getLogger(__name__)


def _split_oversized_text(text: str, max_chars: int, overlap_chars: int = 0) -> List[str]:
    """Recursively split text that exceeds max_chars."""
    if max_chars <= 0:
        raise ValueError("max_chars must be greater than 0")

    if len(text) <= max_chars:
        return [text]

    segments: List[str] = []

    # Try paragraph split first
    paragraphs = text.split("\n\n")
    if len(paragraphs) > 1:
        current = ""
        for para in paragraphs:
            candidate = f"{current}\n\n{para}" if current else para
            if len(candidate) > max_chars:
                if current:
                    segments.append(current)
                if len(para) > max_chars:
                    segments.extend(_split_oversized_text(para, max_chars, overlap_chars))
                    current = ""
                else:
                    current = para
            else:
                current = candidate
        if current:
            segments.append(current)
        if segments:
            return segments

    # Then sentence split
    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) > 1:
        current = ""
        for sentence in sentences:
            candidate = f"{current} {sentence}" if current else sentence
            if len(candidate) > max_chars:
                if current:
                    segments.append(current)
                if len(sentence) > max_chars:
                    segments.extend(_split_oversized_text(sentence, max_chars, overlap_chars))
                    current = ""
                else:
                    current = sentence
            else:
                current = candidate
        if current:
            segments.append(current)
        if segments:
            return segments

    # Last resort: hard split near word boundaries with optional overlap
    effective_overlap = 0
    if overlap_chars > 0:
        effective_overlap = min(overlap_chars, max_chars - 1)

    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + max_chars, text_len)
        if end < text_len:
            for i in range(end, max(start, end - 200), -1):
                if text[i] in (" ", "\n"):
                    end = i
                    break

        segment = text[start:end]
        if not segment:
            segment = text[start:min(start + max_chars, text_len)]
            end = start + len(segment)
        segments.append(segment)

        if end >= text_len:
            break

        start = end - effective_overlap
        if start < 0:
            start = 0

    return segments


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
        elements: Parsed elements from PDFX
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

    logger.info('Starting chunking with strategy: %s', strategy.strategy_name)

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

        # Large table spacing can yield blank slices even when the source
        # element contains useful text. Filter after splitting so all methods
        # retain substantive content without creating invalid DocumentChunks.
        chunks = [chunk for chunk in chunks if chunk["content"].strip()]
        if not chunks:
            raise ChunkingError("No non-whitespace content to chunk")

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

        logger.info('Created %s chunks', len(document_chunks))
        return document_chunks

    except Exception as e:
        error_msg = f"Chunking failed: {str(e)}"
        logger.error(error_msg)
        raise ChunkingError(error_msg) from e


def _new_chunk(content: str = "", elements: List[Dict[str, Any]] | None = None, metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "content": content,
        "elements": list(elements or []),
        "metadata": dict(metadata or {}),
        "table_segments": [],
    }


def _holds_only_headings(chunk: Dict[str, Any]) -> bool:
    elements = chunk["elements"]
    return bool(chunk["content"]) and bool(elements) and all(
        element.get("type") == "Title" for element in elements
    )


def _joins_heading_stack(chunk: Dict[str, Any], heading: Dict[str, Any], max_characters: int) -> bool:
    """Whether a heading may join a chunk that holds only headings.

    Structural metadata only: the stacked innermost heading must be an ancestor
    of the new one (its section path a proper prefix of the new heading's, in
    the same parent section), so the merged chunk's metadata still reaches every
    stacked heading. A sibling heading starts its own chunk, as does a heading
    that would push the stack past max_characters.
    """
    if not _holds_only_headings(chunk):
        return False
    if len(chunk["content"]) + 2 + len(heading.get("text", "")) > max_characters:
        return False
    stacked = chunk["elements"][-1].get("metadata") or {}
    incoming = heading.get("metadata") or {}
    stacked_path = stacked.get("section_path") or []
    incoming_path = incoming.get("section_path") or []
    return (
        len(stacked_path) < len(incoming_path)
        and incoming_path[: len(stacked_path)] == stacked_path
        and stacked.get("parent_section") == incoming.get("parent_section")
    )


def _chunk_by_title(
    elements: List[Dict[str, Any]],
    strategy: ChunkingStrategy
) -> List[Dict[str, Any]]:
    """Chunk elements by title boundaries.

    Groups content under each title, respecting max_characters limit. Tables
    are cut only between whole rows (``table_chunking``). A heading followed by
    a heading under it stays with it (``_joins_heading_stack``), and a heading
    that does not fit beside its next element is carried into that element's
    first piece, which can then exceed max_characters by the headings' length.
    A heading followed by a sibling heading, or with nothing after it, is
    still a piece of its own, so it keeps its section metadata.
    """
    chunks: List[Dict[str, Any]] = []
    current_chunk = _new_chunk()
    header_rule = current_table_header_rule()
    table_index = 0

    def take_carried_headings() -> Dict[str, Any] | None:
        """Flush the current chunk, or return it when it holds only headings."""
        nonlocal current_chunk
        carried = current_chunk if _holds_only_headings(current_chunk) else None
        if carried is None and current_chunk["content"]:
            chunks.append(current_chunk)
        current_chunk = _new_chunk()
        return carried

    def with_carried(carried: Dict[str, Any] | None, content: str, element: Dict[str, Any]) -> Dict[str, Any]:
        if carried is None:
            return _new_chunk(content, [element], element.get("metadata", {}))
        return _new_chunk(
            f"{carried['content']}\n\n{content}",
            carried["elements"] + [element],
            carried["metadata"],
        )

    for element in elements:
        element_type = element.get("type", "")
        element_text = element.get("text", "")

        if element_type == "Title" and current_chunk["content"]:
            if _joins_heading_stack(current_chunk, element, strategy.max_characters):
                # A heading under the stacked one stays with it; the innermost
                # heading owns the section metadata of the text that follows,
                # and its section path still names every stacked heading.
                current_chunk["content"] = f"{current_chunk['content']}\n\n{element_text}"
                current_chunk["elements"].append(element)
                current_chunk["metadata"] = dict(element.get("metadata", {}))
                continue
            chunks.append(current_chunk)
            # A new section owns its own text/provenance. Borrowing the prior
            # section's tail misattributes it to this heading/page.
            current_chunk = _new_chunk(element_text, [element], element.get("metadata", {}))
            continue

        if element_type == "Table":
            layout = layout_table(element_text, header_rule)
            if not layout.lines:
                continue
            table_index += 1
            table_text = layout.text
            separator = "\n\n" if current_chunk["content"] else ""
            if len(current_chunk["content"]) + len(separator) + len(table_text) <= strategy.max_characters:
                char_start = len(current_chunk["content"]) + len(separator)
                current_chunk["content"] = f"{current_chunk['content']}{separator}{table_text}"
                current_chunk["elements"].append(element)
                if not current_chunk["metadata"]:
                    current_chunk["metadata"] = dict(element.get("metadata", {}))
                current_chunk["table_segments"].append(
                    layout.segment(
                        table_index=table_index,
                        char_start=char_start,
                        piece=layout.whole_table_piece(),
                    ).to_metadata()
                )
                continue

            carried = take_carried_headings()
            pieces = pack_table_rows(layout, strategy.max_characters)
            if len(pieces) > 1 or len(pieces[0].text) > strategy.max_characters:
                logger.info(
                    "Table %d (%d characters) cut into %d whole-row pieces.",
                    table_index,
                    len(table_text),
                    len(pieces),
                )
            for piece_number, piece in enumerate(pieces):
                if piece_number == 0:
                    chunk = with_carried(carried, piece.text, element)
                else:
                    chunk = _new_chunk(piece.text, [element], element.get("metadata", {}))
                chunk["table_segments"].append(
                    layout.segment(
                        table_index=table_index,
                        char_start=len(chunk["content"]) - len(piece.text),
                        piece=piece,
                    ).to_metadata()
                )
                if piece_number < len(pieces) - 1:
                    chunks.append(chunk)
                else:
                    current_chunk = chunk
            continue

        # Add to current chunk if within size limit
        new_content = current_chunk["content"] + "\n\n" + element_text if current_chunk["content"] else element_text

        if len(new_content) <= strategy.max_characters:
            current_chunk["content"] = new_content
            current_chunk["elements"].append(element)
            if not current_chunk["metadata"]:
                current_chunk["metadata"] = dict(element.get("metadata", {}))
            continue

        # Keep source elements independent across chunk boundaries.
        # Overlap remains available within an oversized element below.
        carried = take_carried_headings()
        if len(element_text) > strategy.max_characters:
            logger.warning(
                "Single element exceeds max_characters (%d > %d). Splitting element of type '%s'.",
                len(element_text),
                strategy.max_characters,
                element_type,
            )
            split_contents = _split_oversized_text(
                element_text,
                strategy.max_characters,
                strategy.overlap_characters,
            )
        else:
            split_contents = [element_text]
        for piece_number, split_content in enumerate(split_contents):
            if piece_number == 0:
                chunk = with_carried(carried, split_content, element)
            else:
                chunk = _new_chunk(split_content, [element], element.get("metadata", {}))
            if piece_number < len(split_contents) - 1:
                chunks.append(chunk)
            else:
                current_chunk = chunk

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

            # Do not borrow text whose element provenance is absent here.
            # Oversized single-element splitting still uses configured overlap.
            combined_content = element_text
            if len(combined_content) > strategy.max_characters:
                logger.warning(
                    "Single element exceeds max_characters (%d > %d). Splitting oversized paragraph element.",
                    len(combined_content),
                    strategy.max_characters,
                )
                split_contents = _split_oversized_text(
                    combined_content,
                    strategy.max_characters,
                    strategy.overlap_characters,
                )
                for split_content in split_contents[:-1]:
                    chunks.append(
                        {
                            "content": split_content,
                            "elements": [element],
                            "metadata": dict(element.get("metadata", {})),
                        }
                    )
                current_chunk = {
                    "content": split_contents[-1],
                    "elements": [element],
                    "metadata": dict(element.get("metadata", {})),
                }
            else:
                current_chunk = {
                    "content": combined_content,
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

        if end >= len(full_text):
            break

        # Move start position with overlap
        start = end - strategy.overlap_characters if strategy.overlap_characters > 0 else end

    return chunks


def _chunk_by_sentence(
    elements: List[Dict[str, Any]],
    strategy: ChunkingStrategy
) -> List[Dict[str, Any]]:
    """Chunk elements by sentence boundaries."""

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

            combined_content = overlap_text + sentence
            if len(combined_content) > strategy.max_characters:
                logger.warning(
                    "Single sentence exceeds max_characters (%d > %d). Splitting sentence chunk.",
                    len(combined_content),
                    strategy.max_characters,
                )
                split_contents = _split_oversized_text(
                    combined_content,
                    strategy.max_characters,
                    strategy.overlap_characters,
                )
                for split_content in split_contents[:-1]:
                    chunks.append({
                        "content": split_content,
                        "elements": [],
                        "metadata": {}
                    })
                current_chunk = {
                    "content": split_contents[-1],
                    "sentences": [split_contents[-1]],
                    "metadata": {}
                }
            else:
                current_chunk = {
                    "content": combined_content,
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
    page_number = None
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
        page_number = int(page_number) if page_number is not None else None
        if page_number is not None and page_number <= 0:
            page_number = None
    except (TypeError, ValueError):
        page_number = None

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
            # Fallback to deterministic identifier if PDFX metadata lacks one
            element_id = f"{document_id}_element_{element.get('index', len(doc_items))}"

        doc_item_label = metadata.get("doc_item_label")
        provenance_entries = metadata.get("provenance") or []

        if not provenance_entries:
            # Some PDFX responses include bbox at metadata root
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
            if page is None or page < 1:
                continue

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
        table_segments=[
            ChunkTableSegment(**segment) for segment in chunk_data.get("table_segments", [])
        ],
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
        logger.debug('Stored batch of %s chunks', len(batch))

    logger.info('Stored total of %s chunks', stored_count)
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
    logger.info('Updating document %s status to: %s', document_id, status)
    # Will be integrated with tracker module
