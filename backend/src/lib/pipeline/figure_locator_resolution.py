"""Ingestion-time semantic resolution of figure and table locators."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, model_validator

from src.lib.config.env import require_env
from src.lib.document_sources.figure_metadata import (
    PROVIDER_FIGURE_METADATA_SECTION,
    is_provider_figure_subsection,
    ordered_provider_figure_metadata_entries,
    provider_figure_semantic_ranges,
    strip_provider_figure_metadata_wrapper,
)
from src.lib.observability.sentry import (
    gen_ai_invoke_agent_span,
    set_redacted_ai_span_data,
)
from src.models.chunk import (
    DocumentChunk,
    FigureLocatorAnnotation,
    FigureLocatorResolution,
    ProviderFigureReference,
    ProviderSemanticRange,
)

if TYPE_CHECKING:
    from src.lib.openai_agents.config import ReasoningEffort

FIGURE_LOCATOR_PROMPT_VERSION = "figure-locator-v1"

# Candidate selection only. This pattern must never be used to derive semantics.
_LOCATOR_CANDIDATE_PATTERN = re.compile(
    r"\b(?:fig(?:ure)?s?|tables?|panels?)\b",
    re.IGNORECASE,
)
_STRUCTURED_REFERENCE_PATTERN = re.compile(
    r"^\s*(?:(?:(?P<modifier>supplementary|supplemental)\s+)?"
    r"(?P<kind>fig(?:ure)?|table)\.?\s*)?"
    r"(?P<number>[A-Za-z]?\d+(?:\.\d+)*)"
    r"(?P<panels>[A-Za-z](?:\s*[,/&-]\s*[A-Za-z])*)?"
    r"(?:\s*\(\s*continued\s*\))?\s*$",
    re.IGNORECASE,
)
_ATOMIC_NUMBER_PATTERN = re.compile(r"^[A-Za-z]?\d+(?:\.\d+)*$")
_ATOMIC_PANEL_PATTERN = re.compile(r"^[A-Za-z0-9]+$")
_CLASSIFIER_PROMPT_PREFIX = (
    "Classify the locator expressions in every candidate below. Return one "
    "candidate result for every candidate_id.\n\n"
)

FigureLocatorCandidate = tuple[DocumentChunk, str]


class FigureLocatorMentionOutput(BaseModel):
    """One semantic locator expression returned verbatim by the classifier."""

    text: str = Field(..., min_length=1)
    cardinality: Literal["single", "multiple", "uncertain"]
    kind: Literal["figure", "table", "unknown"]
    number: str | None = None
    panels: list[str] = Field(default_factory=list)
    canonical_reference: str | None = None

    @model_validator(mode="after")
    def validate_canonical_reference(self) -> "FigureLocatorMentionOutput":
        if self.cardinality == "single" and self.canonical_reference is None:
            raise ValueError("single locators require canonical_reference")
        if self.cardinality != "single" and self.canonical_reference is not None:
            raise ValueError("only single locators may have canonical_reference")
        return self


class FigureLocatorCandidateOutput(BaseModel):
    """Classifier result for one deterministic chunk candidate ID."""

    candidate_id: str = Field(..., min_length=1)
    mentions: list[FigureLocatorMentionOutput] = Field(default_factory=list)


class FigureLocatorBatchOutput(BaseModel):
    """Structured output for all selected chunks in one ingestion batch."""

    candidates: list[FigureLocatorCandidateOutput]


async def resolve_figure_locators(
    chunks: list[DocumentChunk],
    *,
    provider_figure_metadata: Sequence[Mapping[str, object]] | None = None,
) -> list[DocumentChunk]:
    """Annotate final chunks before storage and return the same ordered list.

    A broad lexical check only chooses which generic chunks are sent to the
    classifier. Provider-caption chunks are selected independently so captions
    without explicit locator words can still use their structured sidecar
    reference. Any missing, malformed, or non-unique model result fails closed.
    """

    model_name = require_env("FIGURE_LOCATOR_LLM_MODEL")
    from src.lib.openai_agents.config import validate_model_reasoning_effort

    reasoning_effort = validate_model_reasoning_effort(
        model_name,
        require_env("FIGURE_LOCATOR_LLM_REASONING"),
    )
    _attach_provider_references(chunks, provider_figure_metadata)

    candidates: list[FigureLocatorCandidate] = []
    for chunk in chunks:
        provider_chunk = _is_provider_figure_chunk(chunk)
        candidate_text = (
            strip_provider_figure_metadata_wrapper(
                chunk.content,
                _provider_subsection(chunk),
            )
            if provider_chunk
            else chunk.content
        )
        should_classify = (
            bool(candidate_text)
            if provider_chunk
            else is_figure_locator_candidate(candidate_text)
        )
        if should_classify:
            candidates.append((chunk, candidate_text))

    if not candidates:
        return chunks

    from src.lib.openai_agents.config import (
        get_figure_locator_resolution_batch_max_chars,
    )

    for batch in _batch_candidates(
        candidates,
        max_prompt_chars=get_figure_locator_resolution_batch_max_chars(),
    ):
        output = await _call_figure_locator_classifier(
            batch,
            model_name=model_name,
            reasoning_effort=reasoning_effort,
        )
        outputs_by_id = _validated_outputs_by_id(output, batch)

        for chunk, _candidate_text in batch:
            result = outputs_by_id[chunk.id]
            annotations = _map_mentions_to_chunk(
                chunk.content,
                result.mentions,
                allowed_ranges=_provider_semantic_ranges(chunk),
            )
            if annotations is None:
                _set_resolution(
                    chunk,
                    status="uncertain",
                    annotations=[],
                    model_name=model_name,
                    reasoning_effort=reasoning_effort,
                )
            else:
                _set_resolution(
                    chunk,
                    status="resolved",
                    annotations=annotations,
                    model_name=model_name,
                    reasoning_effort=reasoning_effort,
                )
    return chunks


def is_figure_locator_candidate(text: str) -> bool:
    """Return whether broad anchor words justify one classifier candidate."""

    return bool(_LOCATOR_CANDIDATE_PATTERN.search(text))


async def _call_figure_locator_classifier(
    candidates: Sequence[FigureLocatorCandidate],
    *,
    model_name: str,
    reasoning_effort: ReasoningEffort,
) -> FigureLocatorBatchOutput:
    from agents import Agent, Runner  # pyright: ignore[reportMissingImports]
    from src.lib.openai_agents.config import (
        build_model_settings,
        get_figure_locator_resolution_max_turns,
        get_model_for_agent,
    )

    settings = build_model_settings(
        model=model_name,
        temperature=0.0,
        reasoning_effort=reasoning_effort,
        parallel_tool_calls=False,
    )
    agent = Agent(
        name="Figure Locator Classifier",
        instructions=_CLASSIFIER_INSTRUCTIONS,
        model=get_model_for_agent(model_name),
        model_settings=settings,
        output_type=FigureLocatorBatchOutput,
    )
    prompt = _classifier_prompt(candidates)

    with gen_ai_invoke_agent_span(
        agent_name=agent.name,
        model=model_name,
        conversation_id=None,
        workflow="figure_locator_resolution",
        agent_key="figure_locator_classifier",
        agent_source="runtime",
        input_preview={
            "candidate_count": len(candidates),
            "candidate_ids": [chunk.id for chunk, _ in candidates],
        },
        finalization_required=False,
    ) as sentry_span:
        try:
            result = await Runner.run(
                agent,
                prompt,
                max_turns=get_figure_locator_resolution_max_turns(),
            )
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
                    "phase": "figure_locator_resolution",
                },
            )
            raise

        output = result.final_output
        if not isinstance(output, FigureLocatorBatchOutput):
            raise ValueError("figure locator classifier returned no structured output")
        set_redacted_ai_span_data(
            sentry_span,
            "ai_curation.validation.status",
            "accepted",
        )
        set_redacted_ai_span_data(
            sentry_span,
            "ai_curation.agent.output",
            {
                "candidate_count": len(output.candidates),
                "mention_count": sum(
                    len(candidate.mentions) for candidate in output.candidates
                ),
            },
        )
        return output


def _map_mentions_to_chunk(
    chunk_text: str,
    mentions: Sequence[FigureLocatorMentionOutput],
    *,
    allowed_ranges: Sequence[tuple[int, int]] | None = None,
) -> list[FigureLocatorAnnotation] | None:
    annotations: list[FigureLocatorAnnotation] = []
    for mention in mentions:
        offsets = _unique_text_offsets(
            chunk_text,
            mention.text,
            allowed_ranges=allowed_ranges,
        )
        if len(offsets) != 1:
            return None
        start = offsets[0]
        cardinality = mention.cardinality
        canonical_reference = (
            _canonical_from_semantic_mention(mention)
            if cardinality == "single"
            else None
        )
        if cardinality == "single" and canonical_reference is None:
            cardinality = "uncertain"
        annotations.append(
            FigureLocatorAnnotation(
                text=mention.text,
                char_start=start,
                char_end=start + len(mention.text),
                cardinality=cardinality,
                kind=mention.kind,
                number=mention.number,
                panels=mention.panels,
                canonical_reference=(
                    canonical_reference if cardinality == "single" else None
                ),
            )
        )
    return annotations


def _canonical_from_semantic_mention(
    mention: FigureLocatorMentionOutput,
) -> str | None:
    """Normalize trusted structured singleton fields, not locator prose."""

    if not mention.number or not _ATOMIC_NUMBER_PATTERN.fullmatch(mention.number):
        return None
    if mention.kind == "figure":
        kind: Literal["figure", "table"] = "figure"
    elif mention.kind == "table":
        kind = "table"
    else:
        return None
    normalized_panels = [panel.strip().upper() for panel in mention.panels if panel.strip()]
    if len(normalized_panels) > 1 or any(
        not _ATOMIC_PANEL_PATTERN.fullmatch(panel) for panel in normalized_panels
    ):
        return None
    return _canonical_reference(
        kind,
        mention.number.strip().upper(),
        normalized_panels,
    )


def _unique_text_offsets(
    source: str,
    target: str,
    *,
    allowed_ranges: Sequence[tuple[int, int]] | None = None,
) -> list[int]:
    offsets: list[int] = []
    ranges = (
        ((0, len(source)),)
        if allowed_ranges is None
        else allowed_ranges
    )
    for range_start, range_end in ranges:
        start = max(0, range_start)
        end = min(len(source), range_end)
        while start < end:
            index = source.find(target, start, end)
            if index < 0:
                break
            if index not in offsets:
                offsets.append(index)
                if len(offsets) > 1:
                    return offsets
            start = index + 1
    return offsets


def _provider_semantic_ranges(
    chunk: DocumentChunk,
) -> list[tuple[int, int]] | None:
    if not _is_provider_figure_chunk(chunk):
        return None
    reference = chunk.metadata.provider_figure_reference
    if reference is None:
        return []
    return [
        (semantic_range.char_start, semantic_range.char_end)
        for semantic_range in reference.semantic_ranges
    ]


def _classifier_prompt(candidates: Sequence[FigureLocatorCandidate]) -> str:
    payload = [
        {"candidate_id": chunk.id, "text": candidate_text}
        for chunk, candidate_text in candidates
    ]
    return _CLASSIFIER_PROMPT_PREFIX + json.dumps(payload, ensure_ascii=False)


def _batch_candidates(
    candidates: Sequence[FigureLocatorCandidate],
    *,
    max_prompt_chars: int,
) -> list[list[FigureLocatorCandidate]]:
    """Partition candidates without truncating any source text."""

    batches: list[list[FigureLocatorCandidate]] = []
    current: list[FigureLocatorCandidate] = []
    for candidate in candidates:
        if len(_classifier_prompt((candidate,))) > max_prompt_chars:
            raise ValueError(
                "figure locator candidate exceeds "
                "FIGURE_LOCATOR_RESOLUTION_BATCH_MAX_CHARS"
            )
        proposed = [*current, candidate]
        if current and len(_classifier_prompt(proposed)) > max_prompt_chars:
            batches.append(current)
            current = [candidate]
        else:
            current = proposed
    if current:
        batches.append(current)
    return batches


def _validated_outputs_by_id(
    output: FigureLocatorBatchOutput,
    candidates: Sequence[FigureLocatorCandidate],
) -> dict[str, FigureLocatorCandidateOutput]:
    outputs_by_id: dict[str, list[FigureLocatorCandidateOutput]] = {}
    for result in output.candidates:
        outputs_by_id.setdefault(result.candidate_id, []).append(result)

    expected_ids = {chunk.id for chunk, _candidate_text in candidates}
    if (
        len(output.candidates) != len(candidates)
        or set(outputs_by_id) != expected_ids
        or any(len(results) != 1 for results in outputs_by_id.values())
    ):
        raise ValueError(
            "figure locator classifier violated the exact candidate_id batch contract"
        )
    return {
        candidate_id: results[0]
        for candidate_id, results in outputs_by_id.items()
    }


def _set_resolution(
    chunk: DocumentChunk,
    *,
    status: Literal["resolved", "uncertain"],
    annotations: list[FigureLocatorAnnotation],
    model_name: str,
    reasoning_effort: str,
) -> None:
    chunk.metadata.figure_locator_resolution = FigureLocatorResolution(
        prompt_version=FIGURE_LOCATOR_PROMPT_VERSION,
        model=model_name,
        reasoning=reasoning_effort,
        status=status,
        annotations=annotations,
    )


def _is_provider_figure_chunk(chunk: DocumentChunk) -> bool:
    section_path = chunk.section_path or chunk.metadata.section_path or []
    return (
        chunk.parent_section == PROVIDER_FIGURE_METADATA_SECTION
        or chunk.subsection == PROVIDER_FIGURE_METADATA_SECTION
        or is_provider_figure_subsection(chunk.subsection)
        or any(
            title == PROVIDER_FIGURE_METADATA_SECTION
            or is_provider_figure_subsection(title)
            for title in section_path
        )
    )


def _provider_subsection(chunk: DocumentChunk) -> str | None:
    if is_provider_figure_subsection(chunk.subsection):
        return chunk.subsection
    section_path = chunk.section_path or chunk.metadata.section_path or []
    return next(
        (
            str(title)
            for title in reversed(section_path)
            if is_provider_figure_subsection(title)
        ),
        None,
    )


def _attach_provider_references(
    chunks: Sequence[DocumentChunk],
    entries: Sequence[Mapping[str, object]] | None,
) -> None:
    ordered_entries = ordered_provider_figure_metadata_entries(entries)
    entry_index = 0
    active_reference: ProviderFigureReference | None = None
    active_subsection: str | None = None

    for chunk in chunks:
        if not _is_provider_figure_chunk(chunk):
            continue
        subsection = _provider_subsection(chunk)
        starts_provider_item = (
            is_provider_figure_subsection(subsection)
            and (
                active_reference is None
                or subsection != active_subsection
                or chunk.element_type == "Title"
            )
        )
        if starts_provider_item:
            active_subsection = subsection
            active_reference = (
                _provider_reference(ordered_entries[entry_index])
                if entry_index < len(ordered_entries)
                else None
            )
            entry_index += 1
        if active_reference is None:
            chunk.metadata.provider_figure_reference = None
            continue
        semantic_ranges = [
            ProviderSemanticRange(char_start=start, char_end=end)
            for start, end in provider_figure_semantic_ranges(
                chunk.content,
                subsection,
            )
        ]
        chunk.metadata.provider_figure_reference = active_reference.model_copy(
            deep=True,
            update={"semantic_ranges": semantic_ranges},
        )


def _provider_reference(entry: Mapping[str, object]) -> ProviderFigureReference:
    raw_label = _clean_optional(entry.get("figure_label"))
    raw_number = _clean_optional(entry.get("figure_number"))
    parsed_label = _parse_structured_reference(raw_label, default_kind="figure")
    parsed_number = _parse_structured_reference(raw_number, default_kind="figure")
    if (raw_label and parsed_label is None) or (
        raw_number and parsed_number is None
    ):
        return ProviderFigureReference(
            raw_label=raw_label,
            raw_number=raw_number,
            status="invalid",
        )
    parsed_values = [value for value in (parsed_label, parsed_number) if value]

    if not parsed_values:
        return ProviderFigureReference(
            raw_label=raw_label,
            raw_number=raw_number,
            status="invalid",
        )

    if len(parsed_values) == 2 and not _provider_values_compatible(
        parsed_values[0], parsed_values[1]
    ):
        return ProviderFigureReference(
            raw_label=raw_label,
            raw_number=raw_number,
            status="conflict",
        )

    kind, number, panels = max(parsed_values, key=lambda value: len(value[2]))
    if len(panels) > 1:
        return ProviderFigureReference(
            raw_label=raw_label,
            raw_number=raw_number,
            status="multiple",
            kind=kind,
            number=number,
            panels=panels,
        )
    canonical = _canonical_reference(kind, number, panels)
    return ProviderFigureReference(
        raw_label=raw_label,
        raw_number=raw_number,
        status="single",
        kind=kind,
        number=number,
        panels=panels,
        canonical_reference=canonical,
    )


def _parse_structured_reference(
    value: str | None,
    *,
    default_kind: Literal["figure", "table"],
) -> tuple[Literal["figure", "table"], str, list[str]] | None:
    if not value:
        return None
    match = _STRUCTURED_REFERENCE_PATTERN.fullmatch(value)
    if not match:
        return None
    raw_kind = (match.group("kind") or "").lower()
    kind: Literal["figure", "table"] = (
        "table" if raw_kind == "table" else default_kind
    )
    number = match.group("number").upper()
    if match.group("modifier") and not number.startswith("S"):
        return None
    panels_text = match.group("panels") or ""
    panels = [
        token.upper()
        for token in re.findall(r"[A-Za-z]", panels_text)
    ]
    return kind, number, panels


def _provider_values_compatible(
    left: tuple[Literal["figure", "table"], str, list[str]],
    right: tuple[Literal["figure", "table"], str, list[str]],
) -> bool:
    if left[0] != right[0] or left[1].casefold() != right[1].casefold():
        return False
    return not left[2] or not right[2] or left[2] == right[2]


def _canonical_reference(
    kind: Literal["figure", "table"],
    number: str,
    panels: Sequence[str],
) -> str:
    prefix = "Figure" if kind == "figure" else "Table"
    panel_suffix = panels[0] if panels else ""
    return f"{prefix} {number}{panel_suffix}"


def _clean_optional(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


_CLASSIFIER_INSTRUCTIONS = """You resolve figure and table locator expressions in source text.

The input is a JSON array of candidates. Analyze each candidate once and return
exactly one result with the same candidate_id. Candidate selection has already
happened; do not assume every candidate contains a semantic locator.

For every locator expression:
- Copy `text` verbatim from the candidate. Include the complete expression,
  especially joined panels or alternatives such as "Fig. 1A,B" or
  "Figure 1A and Figure 1B".
- Set cardinality to `single` only when the expression identifies exactly one
  figure/table location. Set `multiple` for ranges, lists, alternatives, or
  more than one panel. Set `uncertain` when the expression cannot be resolved
  safely. Deictic expressions such as "the upper panel" or "the left panel"
  are uncertain locators, not absent locators.
- Set kind to figure, table, or unknown; include number and panels only when
  explicit in the source.
- Emit canonical_reference only for a safe singleton, using `Figure <id>` or
  `Table <id>`. Never emit it for multiple or uncertain expressions.
- Group a semantically linked multi-part expression into one mention. Keep
  unrelated locator expressions as separate mentions so their exact source
  spans remain distinguishable.
- Return an empty mentions list when the candidate contains no semantic
  figure/table locator. Metadata labels, filenames, and generated headings are
  not source-text locators.

Never invent text, expand shorthand into text not present in the source, or
infer a singleton from an ambiguous or plural expression.
"""
