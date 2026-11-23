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


# Template for injecting the paper's outline, file by file, into prompts
HIERARCHY_TEMPLATE = """
## Document Structure

This paper has {file_count}. Each file lists its sections and subsections with their exact names; use these names, written exactly, with `read_section` and `read_subsection`.

{hierarchy_text}
{files_being_added}
### How to use this outline:
- Use `read_section("<a section name from the outline>")` to read an entire top-level section
- Use `read_subsection("<section name>", "<subsection name>")` to read a specific subsection
- When several files have a section of the same name, add `file="<file name>"`
- `read_section`, `read_subsection` and `read_chunk` all show each passage as numbered sentences; to keep a sentence as evidence, pass the passage's `chunk_index` and the sentence number (`span_index`) to `record_evidence`
- A passage already read with `read_section` or `read_subsection` does not need to be read again with `read_chunk`
- The subsections are nested under their parent sections above
"""

_FILE_ROLE_LABELS = {"main": "main paper", "supplement": "supplement"}

# Approved by Chris, 22 Nov 2026 (O4). Shown while the paper has files being added.
FILES_BEING_ADDED_LINE = "\nFiles are still being added to this paper; they cannot be read yet.\n"


def _size_label(pages: List[int], chunk_count: int) -> str:
    """"p.2-3, 15 chunks", or "15 chunks" for a file without pages."""
    if not pages:
        return f"{chunk_count} chunks"
    page_str = f"p.{pages[0]}" if len(pages) == 1 else f"p.{pages[0]}-{pages[-1]}"
    return f"{page_str}, {chunk_count} chunks"


def _section_lines(sections: List[Dict[str, Any]]) -> List[str]:
    lines = []
    for section in sections:
        lines.append(f"**{section['name']}** ({_size_label(section['page_numbers'], section['chunk_count'])})")
        for sub in section["subsections"]:
            lines.append(f"  └─ {sub['name']} ({_size_label(sub['page_numbers'], sub['chunk_count'])})")
    return lines


def format_hierarchy_for_prompt(hierarchy: Dict[str, Any]) -> str:
    """
    Format the paper's outline, file by file, for injection into an agent prompt.

    Every file of the run's readable files is listed in tab order with its role,
    kind and chunk range, then its sections and subsections by their exact
    stored names. Names are never shortened.

    Args:
        hierarchy: Outline from get_document_sections_hierarchical (with ``files``).

    Returns:
        Formatted string ready for prompt injection, or empty string if no hierarchy.
    """
    if not hierarchy or not hierarchy.get("sections"):
        return ""

    files = hierarchy["files"]
    blocks = []
    for number, listed in enumerate(files, start=1):
        role = _FILE_ROLE_LABELS[listed["role"]]
        header = (
            f"File {number} of {len(files)}: {listed['name']} ({role}, {listed['kind']}, "
            f"{listed['chunk_count']} chunks, chunks {listed['chunk_index_start']} to {listed['chunk_index_end']})"
        )
        blocks.append("\n".join([header, *_section_lines(listed["sections"])]))

    return HIERARCHY_TEMPLATE.format(
        file_count="1 file" if len(files) == 1 else f"{len(files)} files",
        hierarchy_text="\n\n".join(blocks),
        files_being_added=FILES_BEING_ADDED_LINE if hierarchy.get("files_being_added") else "",
    )


def format_document_context_for_prompt(
    hierarchy: Optional[Dict[str, Any]] = None,
) -> tuple[str, str]:
    """
    Format all document context for injection into an agent prompt.

    This is the main entry point for document context injection: the paper's
    outline (hierarchy). A paper without one gets no injected structure.

    Args:
        hierarchy: Hierarchical structure from get_document_sections_hierarchical

    Returns:
        Tuple of (context_text, structure_info) where:
        - context_text: Combined formatted text ready for prompt injection
        - structure_info: Short description for logging (e.g., "hierarchy with 5 sections")
    """
    parts = []
    info_parts = []

    if hierarchy and hierarchy.get("sections"):
        hierarchy_text = format_hierarchy_for_prompt(hierarchy)
        parts.append(hierarchy_text)
        info_parts.append(f"hierarchy with {len(hierarchy.get('sections', []))} sections")
    else:
        info_parts.append("no structure")

    context_text = "\n".join(parts)
    structure_info = " + ".join(info_parts)

    return context_text, structure_info
