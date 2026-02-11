"""
Shared utility functions for agent prompt injection.

This module provides common functions used by multiple agents for injecting
document context (hierarchy, abstract) into their prompts.

IMPORTANT: This is the SINGLE SOURCE OF TRUTH for prompt injection utilities.
Do not duplicate these functions in individual agent files.
"""

import logging
from typing import Optional, List, Dict, Any, Type

logger = logging.getLogger(__name__)


# Template for structured output requirement instruction
# NOTE: GPT-5 models with reasoning enabled may output JSON as plain text instead of
# using the structured output mechanism. The explicit JSON instructions ensure the
# model outputs parseable JSON that our text fallback can capture.
STRUCTURED_OUTPUT_INSTRUCTION_TEMPLATE = """
## CRITICAL: ALWAYS PRODUCE STRUCTURED OUTPUT AS VALID JSON
After completing your research/queries, you MUST produce the {output_type_name} structured output.
- Do NOT end your turn without generating this output
- Your final response MUST be valid JSON matching the {output_type_name} schema EXACTLY
- Output the JSON directly - do not wrap it in markdown code blocks or any other formatting
- If you have gathered ANY relevant information, synthesize it into the JSON format
- If no relevant data was found, still produce the JSON output with empty/default values and explain what was searched
- NEVER finish with tool calls only - ALWAYS synthesize into the final JSON response
- The JSON must start with {{ and end with }} - no text before or after
"""


def inject_structured_output_instruction(
    instructions: str,
    output_type: Optional[Type] = None,
    output_type_name: Optional[str] = None,
    insert_after_first_section: bool = True
) -> str:
    """
    Inject the structured output requirement instruction into agent prompts.

    This ensures all agents have explicit instructions to ALWAYS produce their
    structured output after completing tool calls, preventing silent failures
    where the model completes tool calls but doesn't generate the final output.

    Args:
        instructions: The base agent instructions
        output_type: The Pydantic output type class (e.g., GeneExpressionEnvelope)
        output_type_name: Explicit output type name (alternative to output_type)
        insert_after_first_section: If True, insert after the first ## section.
                                    If False, prepend to the beginning.
                                    Note: if no "## " section headers exist,
                                    the instruction is prepended as a fallback.

    Returns:
        Modified instructions with structured output requirement injected.
    """
    # Get the output type name
    if output_type is not None:
        type_name = output_type.__name__
    elif output_type_name is not None:
        type_name = output_type_name
    else:
        logger.warning("inject_structured_output_instruction called without output type")
        return instructions

    # Format the instruction
    output_instruction = STRUCTURED_OUTPUT_INSTRUCTION_TEMPLATE.format(
        output_type_name=type_name
    )

    if not insert_after_first_section:
        # Simple prepend
        return output_instruction + "\n" + instructions

    # Find the first ## section header and insert after the section content
    # Strategy: Find the SECOND ## (start of second section) and insert before it
    lines = instructions.split('\n')
    section_count = 0
    insert_index = 0

    for i, line in enumerate(lines):
        if line.strip().startswith('## '):
            section_count += 1
            if section_count == 2:
                # Found second section, insert before it
                insert_index = i
                break

    if insert_index > 0:
        # Insert before the second section
        lines.insert(insert_index, output_instruction)
        return '\n'.join(lines)
    else:
        # Fallback: just prepend
        return output_instruction + "\n" + instructions


# Template for injecting hierarchical section structure into prompts
HIERARCHY_TEMPLATE = """
## Document Structure

This document has the following hierarchical structure:

{hierarchy_text}

**Top-level sections (in order):** {top_level_list}

### How to use this hierarchy:
- Use `read_section("Methods")` to read an entire top-level section
- Use `read_subsection("Methods", "Fly Strains")` to read a specific subsection
- The subsections are nested under their parent sections above
"""

# Fallback template for flat sections (no hierarchy available)
SECTION_LIST_TEMPLATE = """
## Document Sections

This document has the following sections available:
{section_list}

Use this information to choose the right tool and target your searches effectively.
"""

# Template for abstract injection
ABSTRACT_TEMPLATE = """
## Paper Abstract

{abstract_text}
"""


def format_hierarchy_for_prompt(hierarchy: Dict[str, Any]) -> str:
    """
    Format hierarchical structure for injection into an agent prompt.

    Args:
        hierarchy: Hierarchical structure from get_document_sections_hierarchical.
                   Expected shape: {"sections": [...], "top_level_sections": [...]}

    Returns:
        Formatted string ready for prompt injection, or empty string if no hierarchy.
    """
    if not hierarchy or not hierarchy.get("sections"):
        return ""

    lines = []
    for section in hierarchy.get("sections", []):
        name = section.get("name", "Unknown")
        pages = section.get("page_numbers", [])
        chunk_count = section.get("chunk_count", 0)
        subsections = section.get("subsections", [])

        if pages:
            page_str = f"p.{pages[0]}" if len(pages) == 1 else f"p.{pages[0]}-{pages[-1]}"
        else:
            page_str = ""

        # Top-level section
        lines.append(f"**{name}** ({page_str}, {chunk_count} chunks)")

        # Subsections indented
        for sub in subsections:
            sub_name = sub.get("name", "Unknown")
            sub_pages = sub.get("page_numbers", [])
            sub_chunks = sub.get("chunk_count", 0)

            if sub_pages:
                sub_page_str = f"p.{sub_pages[0]}" if len(sub_pages) == 1 else f"p.{sub_pages[0]}-{sub_pages[-1]}"
            else:
                sub_page_str = ""

            lines.append(f"  └─ {sub_name} ({sub_page_str}, {sub_chunks} chunks)")

    hierarchy_text = "\n".join(lines)
    top_level = hierarchy.get("top_level_sections", [])
    top_level_list = ", ".join(top_level) if top_level else "Unknown"

    return HIERARCHY_TEMPLATE.format(hierarchy_text=hierarchy_text, top_level_list=top_level_list)


def format_sections_for_prompt(sections: List[Dict[str, Any]]) -> str:
    """
    Format a flat sections list for injection into an agent prompt (fallback).

    Used when hierarchical structure is not available.

    Args:
        sections: List of section dicts with title, page_numbers, chunk_count.

    Returns:
        Formatted string ready for prompt injection, or empty string if no sections.
    """
    if not sections:
        return ""

    lines = []
    for section in sections:
        title = section.get("title", "Unknown")
        pages = section.get("page_numbers", [])
        chunk_count = section.get("chunk_count", 0)

        if pages:
            page_str = f"p.{pages[0]}" if len(pages) == 1 else f"p.{pages[0]}-{pages[-1]}"
        else:
            page_str = ""

        lines.append(f"- **{title}** ({page_str}, {chunk_count} chunks)")

    section_list = "\n".join(lines)
    return SECTION_LIST_TEMPLATE.format(section_list=section_list)


def format_abstract_for_prompt(abstract: Optional[str]) -> str:
    """
    Format abstract text for injection into an agent prompt.

    Args:
        abstract: Abstract text, or None if not available.

    Returns:
        Formatted string ready for prompt injection, or empty string if no abstract.
    """
    if not abstract or not abstract.strip():
        return ""

    return ABSTRACT_TEMPLATE.format(abstract_text=abstract.strip())


def format_document_context_for_prompt(
    hierarchy: Optional[Dict[str, Any]] = None,
    sections: Optional[List[Dict[str, Any]]] = None,
    abstract: Optional[str] = None
) -> tuple[str, str]:
    """
    Format all document context for injection into an agent prompt.

    This is the main entry point for document context injection. It combines:
    - Paper hierarchy (or flat sections as fallback)
    - Paper abstract (if available)

    Args:
        hierarchy: Hierarchical structure from get_document_sections_hierarchical
        sections: Flat list of sections (fallback if hierarchy not available)
        abstract: Abstract text from the paper

    Returns:
        Tuple of (context_text, structure_info) where:
        - context_text: Combined formatted text ready for prompt injection
        - structure_info: Short description for logging (e.g., "hierarchy with 5 sections + abstract")
    """
    parts = []
    info_parts = []

    # Add hierarchy or sections (prefer hierarchy)
    if hierarchy and hierarchy.get("sections"):
        hierarchy_text = format_hierarchy_for_prompt(hierarchy)
        parts.append(hierarchy_text)
        info_parts.append(f"hierarchy with {len(hierarchy.get('sections', []))} sections")
    elif sections:
        section_text = format_sections_for_prompt(sections)
        parts.append(section_text)
        info_parts.append(f"{len(sections)} flat sections")
    else:
        info_parts.append("no structure")

    # Add abstract if available
    if abstract and abstract.strip():
        abstract_text = format_abstract_for_prompt(abstract)
        parts.append(abstract_text)
        info_parts.append("abstract")

    context_text = "\n".join(parts)
    structure_info = " + ".join(info_parts)

    return context_text, structure_info


async def _extract_abstract_with_llm(raw_text: str) -> Optional[str]:
    """
    Use a fast LLM to extract just the abstract from raw chunk text.

    This handles cases where chunks contain mixed content (keywords, headers,
    introduction text) alongside the actual abstract.

    Args:
        raw_text: Combined text from chunks that may contain abstract

    Returns:
        Clean extracted abstract text, or None if extraction fails
    """
    import os

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI()

        # Use a fast, cheap model for this extraction
        model = os.getenv("ABSTRACT_EXTRACTION_MODEL", "gpt-5-mini")

        # GPT-5 models use max_completion_tokens, others use max_tokens
        is_gpt5 = model.startswith("gpt-5")
        completion_kwargs = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Extract ONLY the abstract from the provided text. "
                        "The abstract is typically a single paragraph summarizing the paper's "
                        "purpose, methods, key findings, and conclusions. "
                        "Do NOT include: keywords, article info, author affiliations, "
                        "introduction text, or section headers. "
                        "Return ONLY the abstract text, nothing else. "
                        "If no clear abstract is found, return 'NO_ABSTRACT_FOUND'."
                    )
                },
                {
                    "role": "user",
                    "content": f"Extract the abstract from this text:\n\n{raw_text[:4000]}"  # Limit input
                }
            ],
        }

        # GPT-5 models don't support temperature; non-GPT5 models use it
        if not is_gpt5:
            completion_kwargs["temperature"] = 0

        response = await client.chat.completions.create(**completion_kwargs)

        content = response.choices[0].message.content
        if content is None:
            logger.warning("LLM returned None content for abstract extraction")
            return None

        result = content.strip()
        logger.debug(f"LLM abstract extraction result: {len(result)} chars")

        if result == "NO_ABSTRACT_FOUND" or len(result) < 50:
            logger.debug(f"LLM could not extract a clear abstract (result={result[:50] if result else 'empty'})")
            return None

        return result

    except Exception as e:
        logger.warning(f"LLM abstract extraction failed: {type(e).__name__}: {e}")
        return None


async def fetch_document_abstract(
    document_id: str,
    user_id: str,
    hierarchy: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """
    Fetch the abstract section from a document (async version).

    Search strategy (in order):
    1. LLM-identified abstract section (from hierarchy metadata)
    2. Common section names: "Abstract", "Summary"
    3. Keyword search: find chunks containing "abstract" in early pages

    Args:
        document_id: The document UUID
        user_id: The user's ID for tenant isolation
        hierarchy: Optional hierarchy metadata with LLM-identified abstract_section_title

    Returns:
        Abstract text if found, None otherwise.
    """
    from src.lib.weaviate_client.chunks import (
        get_chunks_by_parent_section,
        search_chunks_by_keyword
    )

    # Build list of section names to try
    # Priority: LLM-identified section first, then common names
    abstract_names = []

    # If hierarchy has LLM-identified abstract section, try it first
    if hierarchy and hierarchy.get("abstract_section_title"):
        llm_abstract_section = hierarchy["abstract_section_title"]
        abstract_names.append(llm_abstract_section)
        logger.debug(f"Using LLM-identified abstract section: '{llm_abstract_section}'")

    # Add common fallback names (will be tried if LLM section not found)
    for name in ["Abstract", "Summary"]:
        if name not in abstract_names:
            abstract_names.append(name)

    # Strategy 1 & 2: Try section-based retrieval
    for section_name in abstract_names:
        try:
            chunks = await get_chunks_by_parent_section(
                document_id=document_id,
                parent_section=section_name,
                user_id=user_id
            )
            if chunks:
                # Combine chunk texts in order
                texts = [c.get("text", "") for c in chunks if c.get("text")]
                if texts:
                    abstract_text = " ".join(texts)
                    logger.debug(
                        f"Found abstract in '{section_name}' section: "
                        f"{len(texts)} chunks, {len(abstract_text)} chars"
                    )
                    return abstract_text
        except Exception as e:
            logger.warning(f"Error fetching abstract section '{section_name}': {e}")
            continue

    # Strategy 3: Keyword search fallback
    # Search for "abstract" in early pages (1-3) when no dedicated section exists
    # Try multiple patterns since some PDFs have spaced-out headers like "A B S T R A C T"
    from src.lib.weaviate_client.chunks import get_chunks_from_index

    keywords_to_try = ["abstract", "A B S T R A C T"]

    for keyword in keywords_to_try:
        try:
            logger.debug(f"Trying keyword search for '{keyword}'...")
            keyword_chunks = await search_chunks_by_keyword(
                document_id=document_id,
                keyword=keyword,
                user_id=user_id,
                max_page=3,
                limit=2  # Get a couple matches to find the best one
            )

            if not keyword_chunks:
                continue

            # Find the chunk with the most content AFTER the keyword
            # (The abstract content, not just the header)
            best_chunk = None
            best_content_after = 0

            for chunk in keyword_chunks:
                text = chunk.get("text", "")
                pos = text.lower().find(keyword.lower())
                if pos >= 0:
                    content_after = len(text) - pos - len(keyword)
                    if content_after > best_content_after:
                        best_content_after = content_after
                        best_chunk = chunk

            if not best_chunk:
                continue

            # Get consecutive chunks starting from the best match
            # Stop when section title changes
            start_index = best_chunk.get("chunk_index", 0)
            consecutive_chunks = await get_chunks_from_index(
                document_id=document_id,
                start_index=start_index,
                user_id=user_id,
                max_chunks=3,  # Abstract rarely spans more than 3 chunks
                stop_on_section_change=True
            )

            if consecutive_chunks:
                # Combine all chunk texts
                combined_text = " ".join(
                    c.get("text", "") for c in consecutive_chunks if c.get("text")
                )

                if combined_text:
                    # Try to extract just the abstract using LLM
                    extracted = await _extract_abstract_with_llm(combined_text)
                    if extracted:
                        logger.info(
                            f"Extracted abstract via LLM from {len(consecutive_chunks)} chunks: "
                            f"{len(extracted)} chars"
                        )
                        return extracted

                    # Fallback: return combined text as-is
                    logger.info(
                        f"Found abstract via keyword search ('{keyword}'): "
                        f"{len(consecutive_chunks)} chunks, {len(combined_text)} chars"
                    )
                    return combined_text

        except Exception as e:
            logger.warning(f"Error in keyword search for '{keyword}': {e}")
            continue

    # Strategy 4: Last resort - send first 5 chunks to LLM
    # The abstract is almost always in the first few chunks of any paper
    try:
        logger.debug("Trying last resort: first 5 chunks to LLM...")
        first_chunks = await get_chunks_from_index(
            document_id=document_id,
            start_index=0,
            user_id=user_id,
            max_chunks=5,
            stop_on_section_change=False  # Get all 5 regardless of section
        )

        if first_chunks:
            combined_text = " ".join(
                c.get("text", "") for c in first_chunks if c.get("text")
            )

            if combined_text:
                extracted = await _extract_abstract_with_llm(combined_text)
                if extracted:
                    logger.info(
                        f"Extracted abstract from first {len(first_chunks)} chunks via LLM: "
                        f"{len(extracted)} chars"
                    )
                    return extracted

    except Exception as e:
        logger.warning(f"Error in last-resort abstract extraction: {e}")

    logger.debug(f"No abstract found for document {document_id[:8]}...")
    return None


def fetch_document_abstract_sync(
    document_id: str,
    user_id: str,
    hierarchy: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """
    Fetch the abstract section from a document (sync wrapper).

    This is a synchronous wrapper for use in sync contexts like agent creation.

    Args:
        document_id: The document UUID
        user_id: The user's ID for tenant isolation
        hierarchy: Optional hierarchy metadata with LLM-identified abstract_section_title

    Returns:
        Abstract text if found, None otherwise.
    """
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're in an async context - need to use a different approach
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    fetch_document_abstract(document_id, user_id, hierarchy)
                )
                return future.result(timeout=10)
        else:
            return loop.run_until_complete(
                fetch_document_abstract(document_id, user_id, hierarchy)
            )
    except RuntimeError:
        # No event loop exists, create a new one
        return asyncio.run(fetch_document_abstract(document_id, user_id, hierarchy))
    except Exception as e:
        logger.warning(f"Error in sync abstract fetch: {e}")
        return None
