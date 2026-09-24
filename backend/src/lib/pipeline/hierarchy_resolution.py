"""
Document Hierarchy Resolution using LLM.

This module analyzes document elements to reconstruct section hierarchy.
It uses an LLM to classify section titles (from PDFX) as top-level
sections or subsections, storing structured metadata for intelligent
section/subsection reading.

The hierarchy is stored both in element metadata and as a document-level
structure for injection into Langfuse traces.

PDFX already assigns a `section_title` to every element indicating
which section it belongs to. We extract unique section_titles and ask
the LLM to determine the hierarchy relationships between them. This is
more reliable than trying to find headers in the document.
"""

import logging
import os
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from src.lib.document_sources.figure_metadata import (
    PROVIDER_FIGURE_METADATA_SECTION,
    is_provider_figure_metadata_section,
    is_provider_figure_subsection,
)
from src.lib.config.env import require_env
from src.lib.observability.cost_context import current_cost_context
from src.lib.observability.payload_contracts import (
    PayloadContractViolation,
    report_payload_contract_violation,
)
from src.lib.observability.sentry import (
    gen_ai_invoke_agent_span,
    set_redacted_ai_span_data,
)

logger = logging.getLogger(__name__)

# Element types never used as a section preview: headings repeat their own
# title, and tables arrive as markdown pipe rows rather than prose.
_NON_PREVIEW_ELEMENT_TYPES = frozenset({"Title", "Table"})


# =============================================================================
# Pydantic Models for Structured LLM Output
# =============================================================================

class SectionItem(BaseModel):
    """A resolved section/subsection, built by the application from indexes."""
    header: str = Field(description="The original section title from the document")
    parent_section: str = Field(
        description="Title of the top-level section this belongs to; the header itself for a top-level section"
    )
    subsection: Optional[str] = Field(
        default=None,
        description="The header when this is a subsection, otherwise null"
    )
    is_top_level: bool = Field(
        description="True for a top-level section, False for a subsection"
    )


class SectionClassification(BaseModel):
    """Classifier result for one numbered input section title."""
    idx: int = Field(description="The [n] number of the input section title")
    is_top_level: bool = Field(
        description="True if this is a major top-level section, False if it is a subsection"
    )
    parent_idx: Optional[int] = Field(
        default=None,
        description="For a subsection: the [n] number of the section it is nested under "
                    "(its top-level section, or its direct parent subsection). "
                    "Null for top-level sections."
    )


class HierarchyOutput(BaseModel):
    """Structured output for the hierarchy classification agent."""
    sections: List[SectionClassification] = Field(
        description="One classification for every numbered input section title"
    )
    abstract_idx: Optional[int] = Field(
        default=None,
        description="The [n] number of the section that contains the paper's abstract. "
                    "Null if no abstract is found in the document."
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
    - metadata.parent_section: The paper's own top-level heading (e.g., "Materials and Methods")
    - metadata.subsection: Subsection name if applicable (e.g., "Fly Strains")
    - metadata.is_top_level: Whether this is a top-level section
    - section_title: Concatenated path for backward compatibility
      (e.g., "Materials and Methods > Fly Strains")

    Args:
        elements: List of document elements from PDFX parser
        store_metadata: Whether to return hierarchy metadata for storage/tracing

    Returns:
        Tuple of (updated elements, hierarchy metadata for tracing)
    """
    from src.lib.openai_agents.config import get_hierarchy_resolution_preview_max_chars

    # 1. Extract unique section_titles from all elements (in order of first appearance)
    # Also capture a bounded preview of the first body text under each heading to
    # help the LLM understand the section. Heading ("Title") elements carry their
    # own title as section_title and tables are pipe rows, so neither is used.
    # Note: section_title is stored in element metadata, not at top level
    preview_max_chars = get_hierarchy_resolution_preview_max_chars()
    section_info_list = []  # List of {"title": str, "preview": str}
    info_by_title: Dict[str, Dict[str, str]] = {}

    for elem in elements:
        # section_title is in metadata
        metadata = elem.get("metadata", {})
        section_title = metadata.get("section_title") or ""
        section_title = str(section_title).strip() if section_title else ""

        if not section_title:
            continue

        info = info_by_title.get(section_title)
        if info is None:
            info = {"title": section_title, "preview": ""}
            info_by_title[section_title] = info
            section_info_list.append(info)

        if (
            preview_max_chars == 0
            or info["preview"]
            or elem.get("type") in _NON_PREVIEW_ELEMENT_TYPES
        ):
            continue
        info["preview"] = _section_body_preview(
            elem.get("text", ""), section_title, preview_max_chars
        )

    if not section_info_list:
        logger.info("[HIERARCHY] No section_titles found in elements.")
        return elements, None

    deterministic_sections = _deterministic_provider_figure_sections(
        section_info_list
    )
    llm_section_info_list = [
        info
        for info in section_info_list
        if info["title"] not in deterministic_sections
    ]

    logger.info(
        '[HIERARCHY] Found %s unique section titles (%s deterministic provider metadata). Calling LLM for %s...',
        len(section_info_list),
        len(deterministic_sections),
        len(llm_section_info_list),
    )

    # 2. Call LLM to resolve hierarchy (pass section info with previews)
    if llm_section_info_list:
        hierarchy_result, abstract_section_title, raw_response = await _call_llm_for_hierarchy(llm_section_info_list)
    else:
        hierarchy_result, abstract_section_title, raw_response = [], None, {
            "model": "deterministic_provider_metadata_only",
            "sections_count": 0,
            "abstract_section_title": None,
        }

    hierarchy_result = _merge_deterministic_sections_in_document_order(
        section_info_list,
        hierarchy_result,
        deterministic_sections,
    )

    if not hierarchy_result:
        logger.warning("[HIERARCHY] LLM returned empty hierarchy. Using fallback.")
        return elements, None

    # Log the resolved hierarchy
    logger.info('[HIERARCHY] Resolved %s section classifications', len(hierarchy_result))
    for item in hierarchy_result[:5]:  # Log first 5
        logger.info("  - '%s' -> parent=%s, subsection=%s, top_level=%s", item.header, item.parent_section, item.subsection, item.is_top_level)
    if len(hierarchy_result) > 5:
        logger.info('  ... and %s more', len(hierarchy_result) - 5)

    # 3. Build lookup map. Headers are the application's own stripped titles.
    hierarchy_map: Dict[str, SectionItem] = {
        item.header: item for item in hierarchy_result
    }

    # 4. Apply hierarchy to elements based on their section_title (in metadata)
    updated_count = 0

    for elem in elements:
        metadata = elem.get("metadata", {})
        section_title = metadata.get("section_title") or ""
        section_title = str(section_title).strip() if section_title else ""

        if not section_title:
            continue

        # Look up the classification for this element's section_title
        section_info = hierarchy_map.get(section_title)

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

    logger.info('[HIERARCHY] Applied hierarchy to %s elements.', updated_count)

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
            model_used=require_env("HIERARCHY_LLM_MODEL"),
            llm_raw_response=raw_response
        )

        logger.info('[HIERARCHY] Top-level sections: %s', top_level_sections)
        if abstract_section_title:
            logger.info("[HIERARCHY] Abstract section: '%s'", abstract_section_title)

    return elements, hierarchy_metadata


def _section_body_preview(text: str, section_title: str, max_chars: int) -> str:
    """Return the bounded opening of a body element's text for the classifier.

    PDFX body elements often begin with their heading on its own line; that
    line is dropped so the preview shows content, and a title-only element
    yields an empty preview. Whitespace is collapsed so each section stays on
    one prompt line. Text longer than ``max_chars`` is cut and marked with
    "..."; the stored element text is unchanged.
    """
    lines = str(text or "").strip().splitlines()
    if lines and " ".join(lines[0].split()) == " ".join(section_title.split()):
        lines = lines[1:]
    body = " ".join(" ".join(lines).split())
    if len(body) <= max_chars:
        return body
    return body[:max_chars].rstrip() + "..."


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
    from agents import Agent, ModelSettings
    from openai.types.shared import Reasoning
    from src.lib.openai_agents.config import (
        PromptCacheIdentity,
        get_hierarchy_resolution_contract_retries,
        get_hierarchy_resolution_max_turns,
        prompt_cache_extra_args,
    )
    from src.lib.openai_agents.runner import run_agent_with_owned_openai_resources

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("[HIERARCHY] No OpenAI API key. Skipping hierarchy resolution.")
        return [], None, None

    system_prompt = """You are an expert biocurator with deep experience in scientific literature analysis. You specialize in understanding the structure and organization of research papers across the life sciences and related research disciplines.

CONTEXT: You are part of an automated curation pipeline that processes scientific publications for the Alliance of Genome Resources. This pipeline extracts information from PDFs to help curators annotate a wide variety of biological entities, relationships, and data types. Understanding document structure is critical because curators need to quickly navigate to relevant sections (like Methods for experimental details, or Results for key findings).

YOUR TASK: Analyze the section structure of a scientific paper and classify each section as either a TOP-LEVEL SECTION or a SUBSECTION. This hierarchy will be used to help curators efficiently search and navigate the document.

INPUT FORMAT: You will receive a numbered list of section titles extracted from the paper. Each line starts with its number, like [0], followed by the title and, when the section has body text of its own, a short preview of the opening of that text ("..." marks where the preview was cut). Some titles have no preview, for example a top-level heading followed immediately by its first subsection, or when previews are turned off; judge those from the title and its position in the list. The sections are listed in document order.

CLASSIFICATION GUIDELINES:

TOP-LEVEL SECTIONS (is_top_level=true) - These are the major divisions of a paper:
- The paper title (usually the first entry)
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
- Nested subsections (for example "2.1.1") belong to their outermost top-level section; their parent_idx may be that top-level section or their direct parent subsection (for example "2.1")
- Short ambiguous titles like "Notes" or "Data" - use the preview to determine placement
- A subsection can only point to a section in the list (its top-level section or its direct parent subsection). If the heading of the section it belongs to is not in the list, classify it as top-level

OUTPUT: Refer to sections only by their [n] numbers; never repeat title text. Return exactly one entry for every number in the input, each number once:
- idx: the section's number
- is_top_level: true for major sections, false for subsections
- parent_idx: for a subsection, the number of the section it is nested under (its top-level section or its direct parent subsection); null for a top-level section. Following parent_idx from any subsection must reach a top-level section

ADDITIONAL TASK - IDENTIFY ABSTRACT:
Almost every scientific paper has an abstract. You must ALSO identify which section contains the abstract:

1. Look for sections explicitly titled "Abstract", "Summary", or similar
2. If no explicit abstract section, check the content previews - abstract content typically:
   - Summarizes the paper's purpose, methods, key findings, and conclusions
   - Appears early in the document (often right after the paper title or before Introduction)
   - Is a single cohesive paragraph or short section
3. Set abstract_idx to the number of the section that contains abstract content
4. Set abstract_idx to null ONLY if no abstract exists (rare for published papers)

Common abstract locations when not explicitly labeled:
- Embedded in the paper title section (abstract follows the title)
- In a section called "Background" that functions as abstract
- In "Significance Statement" (sometimes serves as abstract in certain journals)
"""

    # Format numbered section info with previews for the LLM
    formatted_sections = []
    for idx, info in enumerate(section_info_list):
        title = info["title"]
        preview = info.get("preview", "")
        if preview:
            formatted_sections.append(f'[{idx}] "{title}" → "{preview}"')
        else:
            formatted_sections.append(f'[{idx}] "{title}"')

    sections_text = "\n".join(formatted_sections)
    user_prompt = f"Classify these numbered section titles from a scientific paper. Each entry shows the section number and title, followed by a preview of its opening body text when it has any:\n\n{sections_text}"

    try:
        from src.lib.openai_agents.config import (
            require_model_reasoning_effort,
            supports_temperature,
        )

        model_name = require_env("HIERARCHY_LLM_MODEL")
        # The catalog decides the request shape: the effort must be one the
        # model accepts, and reasoning models reject a temperature parameter.
        reasoning_effort = require_model_reasoning_effort(
            model_name, require_env("HIERARCHY_LLM_REASONING")
        )
        logger.info('[HIERARCHY] Calling %s (reasoning=%s) for hierarchy resolution...', model_name, reasoning_effort)

        model_settings = ModelSettings(
            temperature=0.0 if supports_temperature(model_name) else None,
            reasoning=Reasoning(effort=reasoning_effort),
            # A string model always runs on the native OpenAI client (owned
            # resources), catalogued or not, so the provider is fixed here.
            extra_args=prompt_cache_extra_args(
                PromptCacheIdentity(
                    agent_key="hierarchy_classifier",
                    static_prompt=system_prompt,
                ),
                model=model_name,
                provider_override="openai",
            ),
        )

        # Create a one-shot agent for hierarchy classification
        hierarchy_agent = Agent(
            name="Hierarchy Classifier",
            instructions=system_prompt,
            model=model_name,
            model_settings=model_settings,
            output_type=HierarchyOutput,  # Use structured output
        )

        from src.lib.observability.cost_context import agent_identity, attach_agent_cost_identity
        attach_agent_cost_identity(hierarchy_agent, agent_identity(
            "hierarchy_classifier", hierarchy_agent.name, "classifier",
        ))

        # Run the agent
        with gen_ai_invoke_agent_span(
            agent_name=hierarchy_agent.name,
            model=model_name,
            conversation_id=None,
            workflow="hierarchy_resolution",
            agent_key="hierarchy_classifier",
            agent_source="runtime",
            input_preview={
                "section_count": len(section_info_list),
                "sections": section_info_list,
            },
            finalization_required=False,
        ) as sentry_span:
            try:
                contract_retries = get_hierarchy_resolution_contract_retries()
                attempt = 0
                last_contract_error: Optional[str] = None
                while True:
                    result = await run_agent_with_owned_openai_resources(
                        hierarchy_agent,
                        user_prompt,
                        max_turns=get_hierarchy_resolution_max_turns(),
                    )
                    if not result.final_output:
                        resolved = None
                        break
                    try:
                        resolved = _resolve_section_indexes(
                            result.final_output,
                            section_info_list,
                        )
                    except ValueError as contract_error:
                        last_contract_error = str(contract_error)
                        if attempt == contract_retries:
                            set_redacted_ai_span_data(
                                sentry_span,
                                "ai_curation.validation.retry_count",
                                attempt,
                            )
                            if _report_section_index_contract(
                                last_contract_error,
                                outcome="failed",
                                retries=attempt,
                                contract_retries=contract_retries,
                                model_name=model_name,
                                section_count=len(section_info_list),
                            ):
                                setattr(
                                    contract_error, "_ai_curation_sentry_captured", True
                                )
                            raise
                        set_redacted_ai_span_data(
                            sentry_span, "ai_curation.validation.status", "retrying"
                        )
                        hierarchy_agent.instructions = system_prompt + (
                            "\nCorrection required: the previous response failed the "
                            "section number contract. Return exactly one entry for "
                            "every input number, each number once, with no other "
                            "numbers. Top-level sections have parent_idx null; every "
                            "subsection has the parent_idx of an input section, and "
                            "following parent_idx must reach a top-level section "
                            "without looping. abstract_idx must be an input number "
                            "or null."
                        )
                        attempt += 1
                        continue
                    break
            except Exception as exc:
                set_redacted_ai_span_data(
                    sentry_span,
                    "ai_curation.validation.status",
                    "error",
                )
                set_redacted_ai_span_data(
                    sentry_span,
                    "ai_curation.error.detail",
                    {
                        "message": str(exc),
                        "error_type": type(exc).__name__,
                        "phase": "hierarchy_resolution",
                    },
                )
                raise

            set_redacted_ai_span_data(
                sentry_span, "ai_curation.validation.retry_count", attempt
            )
            if last_contract_error is not None and resolved is None:
                # The correction retry returned nothing, so the contract
                # failure was never recovered.
                _report_section_index_contract(
                    f"{last_contract_error}; the correction retry returned no output",
                    outcome="failed",
                    retries=attempt,
                    contract_retries=contract_retries,
                    model_name=model_name,
                    section_count=len(section_info_list),
                )
            elif last_contract_error is not None:
                _report_section_index_contract(
                    last_contract_error,
                    outcome="recovered",
                    retries=attempt,
                    contract_retries=contract_retries,
                    model_name=model_name,
                    section_count=len(section_info_list),
                )

            # Record the resolved output while the Sentry span is still active so
            # Tier 2 captures the classifier result details on the span.
            if resolved is not None:
                set_redacted_ai_span_data(
                    sentry_span,
                    "ai_curation.validation.status",
                    "accepted",
                )
                set_redacted_ai_span_data(
                    sentry_span,
                    "ai_curation.agent.output",
                    {
                        "sections_count": len(resolved[0]),
                        "abstract_section_title": resolved[1],
                        "sections": [
                            section.model_dump()
                            for section in resolved[0]
                        ],
                    },
                )

        # Store raw response for debugging
        raw_response = {
            "model": model_name,
            "reasoning_effort": reasoning_effort,
            "contract_retries": attempt,
        }

        # Extract the structured output
        if resolved is None:
            logger.warning("[HIERARCHY] LLM returned empty output.")
            return [], None, raw_response

        sections, abstract_section_title = resolved
        raw_response["sections_count"] = len(sections)
        raw_response["abstract_section_title"] = abstract_section_title

        logger.info('[HIERARCHY] Successfully parsed %s section items', len(sections))
        if abstract_section_title:
            logger.info("[HIERARCHY] LLM identified abstract in section: '%s'", abstract_section_title)
        else:
            logger.info("[HIERARCHY] LLM did not identify an abstract section")

        return sections, abstract_section_title, raw_response

    except Exception as e:
        logger.error('[HIERARCHY] LLM hierarchy resolution failed: %s', e, exc_info=True)
        return [], None, None


def _resolve_section_indexes(
    output: HierarchyOutput,
    section_info_list: List[Dict[str, str]],
) -> tuple[List[SectionItem], Optional[str]]:
    """Map index-only classifier output back to titles in input order.

    A subsection's parent chain is followed to its top-level ancestor, so a
    nested heading such as "2.1.1" may name its direct parent "2.1". Raises
    ValueError when any index is missing, duplicated, or out of range, or when
    a parent chain loops or ends without reaching a top-level section.
    """
    titles = [info["title"] for info in section_info_list]
    count = len(titles)
    by_idx: Dict[int, SectionClassification] = {}
    for item in output.sections:
        if not 0 <= item.idx < count or item.idx in by_idx:
            raise ValueError(
                "hierarchy classifier violated the section index contract: "
                f"invalid or duplicate idx {item.idx}"
            )
        by_idx[item.idx] = item
    if len(by_idx) != count:
        missing = sorted(set(range(count)) - set(by_idx))
        raise ValueError(
            "hierarchy classifier violated the section index contract: "
            f"missing idx {missing}"
        )
    if output.abstract_idx is not None and not 0 <= output.abstract_idx < count:
        raise ValueError(
            "hierarchy classifier violated the section index contract: "
            f"invalid abstract_idx {output.abstract_idx}"
        )

    resolved: List[SectionItem] = []
    for idx, title in enumerate(titles):
        item = by_idx[idx]
        if item.is_top_level:
            if item.parent_idx is not None:
                raise ValueError(
                    "hierarchy classifier violated the section index contract: "
                    f"top-level idx {idx} has parent_idx {item.parent_idx}"
                )
            resolved.append(SectionItem(
                header=title,
                parent_section=title,
                subsection=None,
                is_top_level=True,
            ))
            continue
        resolved.append(SectionItem(
            header=title,
            parent_section=titles[_top_level_ancestor(idx, by_idx)],
            subsection=title,
            is_top_level=False,
        ))

    abstract_title = (
        titles[output.abstract_idx] if output.abstract_idx is not None else None
    )
    return resolved, abstract_title


def _top_level_ancestor(
    idx: int,
    by_idx: Dict[int, SectionClassification],
) -> int:
    """Follow a subsection's parent_idx chain to its top-level section number."""
    chain = [idx]
    current = by_idx[idx]
    while not current.is_top_level:
        parent = (
            by_idx.get(current.parent_idx)
            if current.parent_idx is not None
            else None
        )
        if parent is None:
            raise ValueError(
                "hierarchy classifier violated the section index contract: "
                f"subsection idx {idx} parent chain {chain} ends at parent_idx "
                f"{current.parent_idx}, which is not an input section"
            )
        if parent.idx in chain:
            raise ValueError(
                "hierarchy classifier violated the section index contract: "
                f"subsection idx {idx} parent chain {chain + [parent.idx]} loops"
            )
        chain.append(parent.idx)
        current = parent
    return current.idx


def _report_section_index_contract(
    detail: str,
    *,
    outcome: str,
    retries: int,
    contract_retries: int,
    model_name: str,
    section_count: int,
) -> bool:
    """Report a section-number contract failure once per classification.

    ``failed`` means the correction budget was spent, or a correction retry
    returned no output, and the document keeps its unclassified titles;
    ``recovered`` means a correction retry succeeded. Recovered retries report
    under their own component so they group apart from real failures in Sentry
    and a real failure still opens its own issue. The detail names section
    numbers only, never titles or previews. Returns whether Sentry accepted the
    report.
    """
    cost_context = current_cost_context()
    return report_payload_contract_violation(
        PayloadContractViolation(
            category="contract_serialization_failure",
            component=(
                "hierarchy_resolution"
                if outcome == "failed"
                else "hierarchy_resolution.recovered"
            ),
            message=(
                f"Hierarchy classifier section-number contract {outcome} after "
                f"{retries} correction retr{'y' if retries == 1 else 'ies'}: {detail}"
            ),
            measured=retries,
            unit="correction_retries",
            limit=contract_retries,
            setting="HIERARCHY_RESOLUTION_CONTRACT_RETRIES",
            field="sections",
        ),
        phase="hierarchy_resolution",
        provider="openai",
        model=model_name,
        agent="hierarchy_classifier",
        correlation={
            "outcome": outcome,
            "contract_retries": retries,
            "section_count": section_count,
            "document_id": cost_context.get("document_id"),
            "job_id": cost_context.get("job_id"),
            "run_id": cost_context.get("run_id"),
        },
        level="error" if outcome == "failed" else "warning",
    )


def _deterministic_provider_figure_sections(
    section_info_list: List[Dict[str, str]],
) -> Dict[str, SectionItem]:
    section_titles = [info["title"] for info in section_info_list]
    if not any(is_provider_figure_metadata_section(title) for title in section_titles):
        return {}

    deterministic: Dict[str, SectionItem] = {}
    for title in section_titles:
        if is_provider_figure_metadata_section(title):
            deterministic[title] = SectionItem(
                header=title,
                parent_section=PROVIDER_FIGURE_METADATA_SECTION,
                subsection=None,
                is_top_level=True,
            )
        elif is_provider_figure_subsection(title):
            deterministic[title] = SectionItem(
                header=title,
                parent_section=PROVIDER_FIGURE_METADATA_SECTION,
                subsection=title,
                is_top_level=False,
            )
    return deterministic


def _merge_deterministic_sections_in_document_order(
    section_info_list: List[Dict[str, str]],
    hierarchy_result: List[SectionItem],
    deterministic_sections: Dict[str, SectionItem],
) -> List[SectionItem]:
    if not deterministic_sections:
        return hierarchy_result

    llm_by_header: Dict[str, SectionItem] = {
        item.header.strip(): item for item in hierarchy_result
    }
    merged: List[SectionItem] = []
    seen: set[str] = set()
    for info in section_info_list:
        title = info["title"].strip()
        item = deterministic_sections.get(title) or llm_by_header.get(title)
        if item is None or item.header in seen:
            continue
        merged.append(item)
        seen.add(item.header)
    for item in hierarchy_result:
        if item.header not in seen:
            merged.append(item)
            seen.add(item.header)
    return merged
