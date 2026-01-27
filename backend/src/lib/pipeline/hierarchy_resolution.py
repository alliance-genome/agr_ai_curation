"""
Document Hierarchy Resolution using LLM.

This module analyzes document elements to reconstruct section hierarchy.
It uses an LLM to classify section titles (from Docling) as top-level
sections or subsections, storing structured metadata for intelligent
section/subsection reading.

The hierarchy is stored both in element metadata and as a document-level
structure for injection into Langfuse traces.

Docling already assigns a `section_title` to every element indicating
which section it belongs to. We extract unique section_titles and ask
the LLM to determine the hierarchy relationships between them. This is
more reliable than trying to find headers in the document.
"""

import logging
import json
import os
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Models for Structured LLM Output
# =============================================================================

class SectionItem(BaseModel):
    """A single section/subsection in the document hierarchy."""
    header: str = Field(description="The original header text from the document")
    parent_section: str = Field(
        description="The top-level section this belongs to. Use the header itself if it IS a top-level section. "
                    "For paper title, use 'TITLE'. Standard sections: Abstract, Introduction, Methods, Results, Discussion, References, Acknowledgements"
    )
    subsection: Optional[str] = Field(
        default=None,
        description="The subsection name if this is a subsection, otherwise null. "
                    "Example: For 'Fly Strains' under Methods, subsection='Fly Strains'"
    )
    is_top_level: bool = Field(
        description="True if this is a major top-level section (Abstract, Introduction, Methods, Results, Discussion, References, etc.), False if it's a subsection"
    )


class HierarchyOutput(BaseModel):
    """Structured output for the hierarchy classification agent."""
    sections: List[SectionItem] = Field(
        description="List of all classified sections from the document"
    )
    abstract_section_title: Optional[str] = Field(
        default=None,
        description="The EXACT original section title that contains the paper's abstract. "
                    "This could be 'Abstract', 'Summary', or another section where abstract content appears. "
                    "Set to null if no abstract is found in the document."
    )


class HierarchyResponse(BaseModel):
    """Complete hierarchy response from LLM."""
    sections: List[SectionItem] = Field(
        description="List of all headers with their classification"
    )


# =============================================================================
# Hierarchy Metadata (for storage and tracing)
# =============================================================================

class HierarchyMetadata(BaseModel):
    """Metadata about the hierarchy resolution process."""
    sections: List[Dict[str, Any]]  # Structured hierarchy
    top_level_sections: List[str]  # List of top-level section names
    abstract_section_title: Optional[str] = None  # LLM-identified abstract section
    created_at: str
    model_used: str
    llm_raw_response: Optional[Dict[str, Any]] = None  # Raw LLM response for debugging


# =============================================================================
# Main Entry Point
# =============================================================================

async def resolve_document_hierarchy(
    elements: List[Dict[str, Any]],
    store_metadata: bool = True
) -> tuple[List[Dict[str, Any]], Optional[HierarchyMetadata]]:
    """
    Analyze document elements to reconstruct section hierarchy using LLM.

    Updates each element with:
    - metadata.parent_section: Top-level section name (e.g., "Methods")
    - metadata.subsection: Subsection name if applicable (e.g., "Fly Strains")
    - metadata.is_top_level: Whether this is a top-level section
    - section_title: Concatenated path for backward compatibility (e.g., "Methods > Fly Strains")

    Args:
        elements: List of document elements from Docling parser
        store_metadata: Whether to return hierarchy metadata for storage/tracing

    Returns:
        Tuple of (updated elements, hierarchy metadata for tracing)
    """
    # 1. Extract unique section_titles from all elements (in order of first appearance)
    # Also capture the first ~100 chars of content to help LLM understand the section
    # Note: section_title is stored in element metadata, not at top level
    section_info_list = []  # List of {"title": str, "preview": str}
    seen_titles = set()

    for elem in elements:
        # section_title is in metadata
        metadata = elem.get("metadata", {})
        section_title = metadata.get("section_title") or ""
        section_title = str(section_title).strip() if section_title else ""

        if not section_title or section_title in seen_titles:
            continue

        # Get a preview of the content under this section (first ~100 chars)
        text = elem.get("text", "").strip()
        preview = text[:100] if text else ""

        seen_titles.add(section_title)
        section_info_list.append({
            "title": section_title,
            "preview": preview
        })

    if not section_info_list:
        logger.info("[HIERARCHY] No section_titles found in elements.")
        return elements, None

    # Extract just the titles for the LLM call
    section_titles = [s["title"] for s in section_info_list]

    logger.info(f"[HIERARCHY] Found {len(section_titles)} unique section titles. Calling LLM...")

    # 2. Call LLM to resolve hierarchy (pass section info with previews)
    hierarchy_result, abstract_section_title, raw_response = await _call_llm_for_hierarchy(section_info_list)

    if not hierarchy_result:
        logger.warning("[HIERARCHY] LLM returned empty hierarchy. Using fallback.")
        return elements, None

    # Log the resolved hierarchy
    logger.info(f"[HIERARCHY] Resolved {len(hierarchy_result)} section classifications")
    for item in hierarchy_result[:5]:  # Log first 5
        logger.info(f"  - '{item.header}' -> parent={item.parent_section}, subsection={item.subsection}, top_level={item.is_top_level}")
    if len(hierarchy_result) > 5:
        logger.info(f"  ... and {len(hierarchy_result) - 5} more")

    # 3. Build lookup map (normalized for matching by section_title)
    hierarchy_map: Dict[str, SectionItem] = {}
    for item in hierarchy_result:
        hierarchy_map[item.header.strip().lower()] = item
        hierarchy_map[item.header.strip()] = item  # Also keep original case

    # 4. Apply hierarchy to elements based on their section_title (in metadata)
    updated_count = 0

    for elem in elements:
        metadata = elem.get("metadata", {})
        section_title = metadata.get("section_title") or ""
        section_title = str(section_title).strip() if section_title else ""

        if not section_title:
            continue

        # Look up the classification for this element's section_title
        section_info = hierarchy_map.get(section_title.lower()) or hierarchy_map.get(section_title)

        if section_info:
            if "metadata" not in elem:
                elem["metadata"] = {}

            # Store structured fields
            elem["metadata"]["parent_section"] = section_info.parent_section
            elem["metadata"]["subsection"] = section_info.subsection
            elem["metadata"]["is_top_level"] = section_info.is_top_level

            # Also store at top level for Weaviate properties
            elem["parent_section"] = section_info.parent_section
            elem["subsection"] = section_info.subsection
            elem["is_top_level"] = section_info.is_top_level

            # Backward compatibility: concatenated section_title
            if section_info.subsection:
                full_path = f"{section_info.parent_section} > {section_info.subsection}"
            else:
                full_path = section_info.parent_section

            elem["section_title"] = full_path
            elem["metadata"]["section_title"] = full_path

            # Keep old field for compatibility
            if section_info.subsection:
                elem["metadata"]["section_path"] = [section_info.parent_section, section_info.subsection]
                elem["section_path"] = [section_info.parent_section, section_info.subsection]
            else:
                elem["metadata"]["section_path"] = [section_info.parent_section]
                elem["section_path"] = [section_info.parent_section]

            updated_count += 1

    logger.info(f"[HIERARCHY] Applied hierarchy to {updated_count} elements.")

    # 5. Build metadata for storage and tracing
    hierarchy_metadata = None
    if store_metadata:
        # Extract unique top-level sections in order
        seen_top_level = set()
        top_level_sections = []
        for item in hierarchy_result:
            if item.is_top_level and item.parent_section not in seen_top_level:
                seen_top_level.add(item.parent_section)
                top_level_sections.append(item.parent_section)

        hierarchy_metadata = HierarchyMetadata(
            sections=[item.model_dump() for item in hierarchy_result],
            top_level_sections=top_level_sections,
            abstract_section_title=abstract_section_title,
            created_at=datetime.now(timezone.utc).isoformat(),
            model_used=os.getenv("HIERARCHY_LLM_MODEL", "gpt-5-mini"),
            llm_raw_response=raw_response
        )

        logger.info(f"[HIERARCHY] Top-level sections: {top_level_sections}")
        if abstract_section_title:
            logger.info(f"[HIERARCHY] Abstract section: '{abstract_section_title}'")

    return elements, hierarchy_metadata


# =============================================================================
# LLM Call
# =============================================================================

async def _call_llm_for_hierarchy(
    section_info_list: List[Dict[str, str]]
) -> tuple[List[SectionItem], Optional[str], Optional[Dict[str, Any]]]:
    """
    Call LLM to classify section titles into top-level sections and subsections.

    Uses the OpenAI Agents SDK for proper gpt-5 reasoning support.

    Args:
        section_info_list: List of dicts with "title" and "preview" keys

    Returns:
        Tuple of (list of SectionItem, abstract_section_title, raw LLM response for debugging)
    """
    from agents import Agent, Runner, ModelSettings
    from openai.types.shared import Reasoning

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("[HIERARCHY] No OpenAI API key. Skipping hierarchy resolution.")
        return [], None, None

    system_prompt = """You are an expert biocurator with deep experience in scientific literature analysis. You specialize in understanding the structure and organization of research papers across the life sciences and related research disciplines.

CONTEXT: You are part of an automated curation pipeline that processes scientific publications for the Alliance of Genome Resources. This pipeline extracts information from PDFs to help curators annotate a wide variety of biological entities, relationships, and data types. Understanding document structure is critical because curators need to quickly navigate to relevant sections (like Methods for experimental details, or Results for key findings).

YOUR TASK: Analyze the section structure of a scientific paper and classify each section as either a TOP-LEVEL SECTION or a SUBSECTION. This hierarchy will be used to help curators efficiently search and navigate the document.

INPUT FORMAT: You will receive a list of section titles extracted from the paper, each with a brief preview of the content (~100 characters). The sections are listed in document order.

CLASSIFICATION GUIDELINES:

TOP-LEVEL SECTIONS (is_top_level=true) - These are the major divisions of a paper:
- TITLE: The paper title (usually the first entry)
- Abstract / Summary
- Introduction / Background
- Methods / Materials and Methods / Experimental Procedures / Experimental Section
- Results
- Discussion
- Results and Discussion (when combined)
- Conclusions / Conclusions and Perspectives
- References / Bibliography
- Acknowledgements
- Author Contributions
- Data Availability
- Supplementary / Supporting Information
- Keywords

SUBSECTIONS (is_top_level=false) - These are nested within top-level sections:
- Anything that logically belongs under a major section
- Example: "Fly Strains" → subsection under "Methods"
- Example: "Statistical Analysis" → subsection under "Methods"
- Example: "Gene Expression Patterns" → subsection under "Results"
- Use the preview text to help determine context if the title is ambiguous

SPECIAL CASES:
- "Significance Statement" is typically a standalone top-level section (common in PNAS, eLife)
- Numbered sections like "2.1. Something" are subsections of the parent numbered section
- Short ambiguous titles like "Notes" or "Data" - use the preview to determine placement

OUTPUT: For each section title, provide:
- header: The EXACT original section title text (do not modify it)
- parent_section: The standardized top-level section name it belongs to
- subsection: The subsection name if this IS a subsection (null if it's top-level)
- is_top_level: true for major sections, false for subsections

ADDITIONAL TASK - IDENTIFY ABSTRACT:
Almost every scientific paper has an abstract. You must ALSO identify which section contains the abstract:

1. Look for sections explicitly titled "Abstract", "Summary", or similar
2. If no explicit abstract section, check the content previews - abstract content typically:
   - Summarizes the paper's purpose, methods, key findings, and conclusions
   - Appears early in the document (often right after TITLE or before Introduction)
   - Is a single cohesive paragraph or short section
3. Set abstract_section_title to the EXACT original section title that contains abstract content
4. Set abstract_section_title to null ONLY if no abstract exists (rare for published papers)

Common abstract locations when not explicitly labeled:
- Embedded in "TITLE" section (abstract follows the title)
- In a section called "Background" that functions as abstract
- In "Significance Statement" (sometimes serves as abstract in certain journals)
"""

    # Format section info with previews for the LLM
    formatted_sections = []
    for info in section_info_list:
        title = info["title"]
        preview = info.get("preview", "")
        if preview:
            formatted_sections.append(f'"{title}" → "{preview}..."')
        else:
            formatted_sections.append(f'"{title}"')

    sections_text = "\n".join(formatted_sections)
    user_prompt = f"Classify these section titles from a scientific paper. Each entry shows the section title followed by a preview of its content:\n\n{sections_text}"

    try:
        model_name = os.getenv("HIERARCHY_LLM_MODEL", "gpt-5-mini")
        reasoning_effort = os.getenv("HIERARCHY_LLM_REASONING", "low")
        logger.info(f"[HIERARCHY] Calling {model_name} (reasoning={reasoning_effort}) for hierarchy resolution...")

        # Build model settings with reasoning for GPT-5 models
        is_gpt5 = model_name.startswith("gpt-5")
        reasoning = None
        if reasoning_effort and is_gpt5:
            reasoning = Reasoning(effort=reasoning_effort)

        model_settings = ModelSettings(
            temperature=None if is_gpt5 else 0.0,
            reasoning=reasoning,
        )

        # Create a one-shot agent for hierarchy classification
        hierarchy_agent = Agent(
            name="Hierarchy Classifier",
            instructions=system_prompt,
            model=model_name,
            model_settings=model_settings,
            output_type=HierarchyOutput,  # Use structured output
        )

        # Run the agent
        result = await Runner.run(hierarchy_agent, user_prompt)

        # Store raw response for debugging
        raw_response = {
            "model": model_name,
            "reasoning_effort": reasoning_effort if is_gpt5 else None,
        }

        # Extract the structured output
        if not result.final_output:
            logger.warning("[HIERARCHY] LLM returned empty output.")
            return [], None, raw_response

        # Extract sections and abstract_section_title from structured output
        sections = result.final_output.sections
        abstract_section_title = result.final_output.abstract_section_title
        raw_response["sections_count"] = len(sections)
        raw_response["abstract_section_title"] = abstract_section_title

        logger.info(f"[HIERARCHY] Successfully parsed {len(sections)} section items")
        if abstract_section_title:
            logger.info(f"[HIERARCHY] LLM identified abstract in section: '{abstract_section_title}'")
        else:
            logger.info("[HIERARCHY] LLM did not identify an abstract section")

        return sections, abstract_section_title, raw_response

    except Exception as e:
        logger.error(f"[HIERARCHY] LLM hierarchy resolution failed: {e}", exc_info=True)
        return [], None, None
