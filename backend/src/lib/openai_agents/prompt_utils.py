"""
Shared utility functions for agent prompt injection.

This module provides common functions used by multiple agents for injecting
document context (section hierarchy) into their prompts.

IMPORTANT: This is the SINGLE SOURCE OF TRUTH for prompt injection utilities.
Do not duplicate these functions in individual agent files.
"""

import logging
from typing import Optional, List, Dict, Any, Type

from src.schemas.models.domain_envelope_extraction import DomainEnvelopeExtractionResult

logger = logging.getLogger(__name__)


# Template for structured output requirement instruction
# NOTE: reasoning models may output JSON as plain text instead of
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


DOMAIN_ENVELOPE_EXTRACTION_INSTRUCTION_TEMPLATE = """
## DOMAIN ENVELOPE EXTRACTION CONTRACT
For {output_type_name}, `curatable_objects[]` is the only semantic object list.
- Do not emit top-level legacy semantic lists: `items[]`, `annotations[]`, `genes[]`, `alleles[]`, `diseases[]`, `chemicals[]`, or `phenotypes[]`
- Put raw mentions, exclusions, ambiguities, verified evidence records, normalization notes, curator-facing notes, and provenance under `metadata`
- Each `curatable_objects[]` entry must include `object_type`, `payload`, and either `object_id` or `pending_ref_id`
- Add `object_role`, `model_ref` and/or `schema_ref`, `definition_state`/`definition_notes`, `evidence_record_ids`, `metadata_refs`, and field/object refs whenever that information is available
- Represent unresolved validation through validation findings, metadata notes, and validator result fields; do not emit patch DSLs or compatibility-mode fields
"""


def _is_domain_envelope_extraction_output_type(output_type: Optional[Type]) -> bool:
    if output_type is None:
        return False

    try:
        return issubclass(output_type, DomainEnvelopeExtractionResult)
    except TypeError:
        return False


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
                                    the instruction is prepended at the beginning.

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
    if _is_domain_envelope_extraction_output_type(output_type):
        output_instruction = (
            output_instruction
            + DOMAIN_ENVELOPE_EXTRACTION_INSTRUCTION_TEMPLATE.format(
                output_type_name=type_name
            )
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
        # No section boundary found; prepend at the beginning.
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
- `read_section`, `read_subsection` and `read_chunk` all show each passage as numbered sentences; to keep a sentence as evidence, pass the passage's `chunk_index` and the sentence number (`span_index`) to `record_evidence`
- A passage already read with `read_section` or `read_subsection` does not need to be read again with `read_chunk`
- The subsections are nested under their parent sections above
"""

# Fallback template for flat sections (no hierarchy available)
SECTION_LIST_TEMPLATE = """
## Document Sections

This document has the following sections available:
{section_list}

Use this information to choose the right tool and target your searches effectively.
For retained evidence, pass the `chunk_index` and sentence number (`span_index`)
of a numbered sentence shown by `read_section`, `read_subsection` or
`read_chunk(chunk_index)` to `record_evidence`.
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


def format_document_context_for_prompt(
    hierarchy: Optional[Dict[str, Any]] = None,
    sections: Optional[List[Dict[str, Any]]] = None,
) -> tuple[str, str]:
    """
    Format all document context for injection into an agent prompt.

    This is the main entry point for document context injection: the paper
    hierarchy, or the flat section list when no hierarchy is available.

    Args:
        hierarchy: Hierarchical structure from get_document_sections_hierarchical
        sections: Flat list of sections (fallback if hierarchy not available)

    Returns:
        Tuple of (context_text, structure_info) where:
        - context_text: Combined formatted text ready for prompt injection
        - structure_info: Short description for logging (e.g., "hierarchy with 5 sections")
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

    context_text = "\n".join(parts)
    structure_info = " + ".join(info_parts)

    return context_text, structure_info
