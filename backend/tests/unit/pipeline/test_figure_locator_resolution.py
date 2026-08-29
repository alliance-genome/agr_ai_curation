"""Unit tests for ingestion-time figure locator resolution."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.lib.document_sources.figure_metadata import PROVIDER_FIGURE_METADATA_SECTION
from src.lib.document_sources.figure_metadata import (
    append_provider_figure_metadata_markdown,
    provider_figure_semantic_ranges,
)
from src.lib.openai_agents.evidence_spans import build_evidence_spans
from src.lib.openai_agents.tools.record_evidence import (
    _resolve_stored_figure_reference,
)
from src.lib.pipeline import figure_locator_resolution as locator
from src.lib.pipeline.chunk import chunk_parsed_document
from src.lib.pipeline.pdfx_parser import markdown_to_pipeline_elements
from src.models.chunk import (
    ChunkMetadata,
    DocumentChunk,
    ElementType,
    FigureLocatorResolution,
    ProviderFigureReference,
)
from src.models.strategy import ChunkingStrategy


_CORPUS_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "figure_locator"
    / "semantic_cases.json"
)
_SEMANTIC_CASES = json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))


def _chunk(
    chunk_id: str,
    content: str,
    *,
    parent_section: str | None = "Results",
    subsection: str | None = None,
    section_path: list[str] | None = None,
    element_type: ElementType = ElementType.NARRATIVE_TEXT,
) -> DocumentChunk:
    return DocumentChunk(
        id=chunk_id,
        document_id="doc-1",
        chunk_index=int(chunk_id.rsplit("-", 1)[-1]),
        content=content,
        element_type=element_type,
        page_number=None,
        parent_section=parent_section,
        subsection=subsection,
        section_path=section_path,
        is_top_level=None,
        metadata=ChunkMetadata(
            character_count=len(content),
            word_count=len(content.split()),
            section_path=section_path,
            figure_locator_resolution=None,
            provider_figure_reference=None,
        ),
    )


def _resolution_for(chunk: DocumentChunk) -> FigureLocatorResolution:
    resolution = chunk.metadata.figure_locator_resolution
    assert resolution is not None
    return resolution


def _provider_reference_for(chunk: DocumentChunk) -> ProviderFigureReference:
    reference = chunk.metadata.provider_figure_reference
    assert reference is not None
    return reference


@pytest.fixture(autouse=True)
def figure_locator_env(monkeypatch):
    monkeypatch.setenv("FIGURE_LOCATOR_LLM_MODEL", "gpt-5.6-terra")
    monkeypatch.setenv("FIGURE_LOCATOR_LLM_REASONING", "low")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_candidate_regex_only_selects_broad_locator_anchors() -> None:
    assert locator.is_figure_locator_candidate("As shown in Fig. 1A, signal rose.")
    assert locator.is_figure_locator_candidate("See panels A and B.")
    assert not locator.is_figure_locator_candidate("Configuration was unchanged.")
    assert not locator.is_figure_locator_candidate("Ordinary result text.")


def test_supplementary_provider_label_requires_explicit_s_number() -> None:
    assert (
        locator._parse_structured_reference(
            "Supplementary Figure 1",
            default_kind="figure",
        )
        is None
    )


@pytest.mark.parametrize(
    "case",
    _SEMANTIC_CASES,
    ids=[case["id"] for case in _SEMANTIC_CASES],
)
@pytest.mark.asyncio
async def test_semantic_adversarial_corpus(case, monkeypatch) -> None:
    chunk = _chunk("chunk-0", case["text"])
    classifier = AsyncMock(
        return_value=locator.FigureLocatorBatchOutput(
            candidates=[
                locator.FigureLocatorCandidateOutput(
                    candidate_id=chunk.id,
                    mentions=[
                        locator.FigureLocatorMentionOutput.model_validate(mention)
                        for mention in case["classifier_mentions"]
                    ],
                )
            ]
        )
    )
    monkeypatch.setattr(locator, "_call_figure_locator_classifier", classifier)

    await locator.resolve_figure_locators([chunk])

    selected = locator.is_figure_locator_candidate(case["text"])
    assert selected is case["expected_candidate"]
    resolution = chunk.metadata.figure_locator_resolution
    if not selected:
        classifier.assert_not_awaited()
        assert resolution is None
    else:
        classifier.assert_awaited_once()
        assert resolution is not None
        assert resolution.status == case["expected_resolution_status"]
        actual_annotations = [
            {
                "text": annotation.text,
                "cardinality": annotation.cardinality,
                "canonical_reference": annotation.canonical_reference,
            }
            for annotation in resolution.annotations
        ]
        assert actual_annotations == case["expected_annotations"]
        assert all(
            chunk.content[annotation.char_start : annotation.char_end]
            == annotation.text
            for annotation in resolution.annotations
        )

    stored_chunk = chunk.model_dump(mode="json")
    stored_chunk["text"] = chunk.content
    spans = build_evidence_spans(chunk_id=chunk.id, chunk_text=chunk.content)
    for expected in case["expected_span_results"]:
        result = _resolve_stored_figure_reference(
            stored_chunk,
            spans[expected["span_index"]],
        )
        assert result.reference == expected["reference"]
        assert result.blocked is expected["blocked"]


@pytest.mark.asyncio
async def test_selected_chunks_are_classified_once_as_a_batch(monkeypatch) -> None:
    chunks = [
        _chunk("chunk-0", "Fig. 1A shows signal."),
        _chunk("chunk-1", "Ordinary result text."),
        _chunk("chunk-2", "Table 2 reports measurements."),
    ]
    classifier = AsyncMock(
        return_value=locator.FigureLocatorBatchOutput(
            candidates=[
                locator.FigureLocatorCandidateOutput(
                    candidate_id="chunk-0",
                    mentions=[
                        locator.FigureLocatorMentionOutput(
                            text="Fig. 1A",
                            cardinality="single",
                            kind="figure",
                            number="1",
                            panels=["A"],
                            canonical_reference="Figure 1A",
                        )
                    ],
                ),
                locator.FigureLocatorCandidateOutput(
                    candidate_id="chunk-2",
                    mentions=[
                        locator.FigureLocatorMentionOutput(
                            text="Table 2",
                            cardinality="single",
                            kind="table",
                            number="2",
                            canonical_reference="Table 2",
                        )
                    ],
                ),
            ]
        )
    )
    monkeypatch.setattr(locator, "_call_figure_locator_classifier", classifier)

    result = await locator.resolve_figure_locators(chunks)

    assert result is chunks
    classifier.assert_awaited_once()
    assert classifier.await_args is not None
    classified_chunks = [chunk.id for chunk, _ in classifier.await_args.args[0]]
    assert classified_chunks == ["chunk-0", "chunk-2"]
    assert chunks[1].metadata.figure_locator_resolution is None
    annotation = _resolution_for(chunks[0]).annotations[0]
    assert chunks[0].content[annotation.char_start : annotation.char_end] == "Fig. 1A"
    assert annotation.canonical_reference == "Figure 1A"


@pytest.mark.asyncio
async def test_selected_chunks_split_at_configured_prompt_char_budget(
    monkeypatch,
) -> None:
    chunks = [
        _chunk("chunk-0", "Figure 1 shows " + ("signal " * 20)),
        _chunk("chunk-1", "Figure 2 shows " + ("signal " * 20)),
    ]
    single_prompt_limit = max(
        len(locator._classifier_prompt(((chunk, chunk.content),)))
        for chunk in chunks
    )
    monkeypatch.setenv(
        "FIGURE_LOCATOR_RESOLUTION_BATCH_MAX_CHARS",
        str(single_prompt_limit),
    )
    classified_batches: list[list[str]] = []

    async def classifier(candidates, **_kwargs):
        classified_batches.append([chunk.id for chunk, _text in candidates])
        return locator.FigureLocatorBatchOutput(
            candidates=[
                locator.FigureLocatorCandidateOutput(
                    candidate_id=chunk.id,
                    mentions=[],
                )
                for chunk, _text in candidates
            ]
        )

    monkeypatch.setattr(locator, "_call_figure_locator_classifier", classifier)

    await locator.resolve_figure_locators(chunks)

    assert classified_batches == [["chunk-0"], ["chunk-1"]]


@pytest.mark.asyncio
async def test_oversized_single_candidate_fails_without_truncation(
    monkeypatch,
) -> None:
    monkeypatch.setenv("FIGURE_LOCATOR_RESOLUTION_BATCH_MAX_CHARS", "1")
    classifier = AsyncMock()
    monkeypatch.setattr(locator, "_call_figure_locator_classifier", classifier)

    with pytest.raises(ValueError, match="candidate exceeds"):
        await locator.resolve_figure_locators(
            [_chunk("chunk-0", "Figure 1 shows signal.")]
        )

    classifier.assert_not_awaited()


@pytest.mark.asyncio
async def test_classifier_failure_propagates(monkeypatch) -> None:
    monkeypatch.setattr(
        locator,
        "_call_figure_locator_classifier",
        AsyncMock(side_effect=RuntimeError("provider unavailable")),
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await locator.resolve_figure_locators(
            [_chunk("chunk-0", "Figure 1 shows signal.")]
        )


@pytest.mark.parametrize(
    "candidate_ids",
    [[], ["chunk-0", "chunk-0"], ["unexpected-chunk"]],
    ids=["missing", "duplicate", "unexpected"],
)
@pytest.mark.asyncio
async def test_invalid_candidate_ids_violate_batch_contract(
    monkeypatch,
    candidate_ids,
) -> None:
    monkeypatch.setattr(
        locator,
        "_call_figure_locator_classifier",
        AsyncMock(
            return_value=locator.FigureLocatorBatchOutput(
                candidates=[
                    locator.FigureLocatorCandidateOutput(
                        candidate_id=candidate_id,
                        mentions=[],
                    )
                    for candidate_id in candidate_ids
                ]
            )
        ),
    )

    with pytest.raises(ValueError, match="exact candidate_id batch contract"):
        await locator.resolve_figure_locators(
            [_chunk("chunk-0", "Figure 1 shows signal.")]
        )


@pytest.mark.asyncio
async def test_multi_panel_shorthand_is_stored_without_singleton(monkeypatch) -> None:
    chunk = _chunk("chunk-0", "Fig. 1A,B shows two panels.")
    monkeypatch.setattr(
        locator,
        "_call_figure_locator_classifier",
        AsyncMock(
            return_value=locator.FigureLocatorBatchOutput(
                candidates=[
                    locator.FigureLocatorCandidateOutput(
                        candidate_id=chunk.id,
                        mentions=[
                            locator.FigureLocatorMentionOutput(
                                text="Fig. 1A,B",
                                cardinality="multiple",
                                kind="figure",
                                number="1",
                                panels=["A", "B"],
                            )
                        ],
                    )
                ]
            )
        ),
    )

    await locator.resolve_figure_locators([chunk])

    annotation = _resolution_for(chunk).annotations[0]
    assert annotation.cardinality == "multiple"
    assert annotation.canonical_reference is None


@pytest.mark.asyncio
async def test_singleton_canonical_is_normalized_from_structured_panel(monkeypatch) -> None:
    chunk = _chunk("chunk-0", "Fig. 1A shows signal.")
    monkeypatch.setattr(
        locator,
        "_call_figure_locator_classifier",
        AsyncMock(
            return_value=locator.FigureLocatorBatchOutput(
                candidates=[
                    locator.FigureLocatorCandidateOutput(
                        candidate_id=chunk.id,
                        mentions=[
                            locator.FigureLocatorMentionOutput(
                                text="Fig. 1A",
                                cardinality="single",
                                kind="figure",
                                number="1",
                                panels=["A"],
                                canonical_reference="Figure 1",
                            )
                        ],
                    )
                ]
            )
        ),
    )

    await locator.resolve_figure_locators([chunk])

    annotation = _resolution_for(chunk).annotations[0]
    assert annotation.canonical_reference == "Figure 1A"


@pytest.mark.asyncio
async def test_panel_only_singleton_without_figure_number_is_downgraded_to_uncertain(
    monkeypatch,
) -> None:
    chunk = _chunk("chunk-0", "Figure 5. (D) Representative image.")
    monkeypatch.setattr(
        locator,
        "_call_figure_locator_classifier",
        AsyncMock(
            return_value=locator.FigureLocatorBatchOutput(
                candidates=[
                    locator.FigureLocatorCandidateOutput(
                        candidate_id=chunk.id,
                        mentions=[
                            locator.FigureLocatorMentionOutput(
                                text="(D)",
                                cardinality="single",
                                kind="figure",
                                number=None,
                                panels=["D"],
                                canonical_reference=None,
                            )
                        ],
                    )
                ]
            )
        ),
    )

    await locator.resolve_figure_locators([chunk])

    annotation = _resolution_for(chunk).annotations[0]
    assert annotation.cardinality == "uncertain"
    assert annotation.canonical_reference is None


@pytest.mark.asyncio
async def test_malformed_singleton_number_is_downgraded_to_uncertain(monkeypatch) -> None:
    chunk = _chunk("chunk-0", "Figures 2-4 summarize the experiments.")
    monkeypatch.setattr(
        locator,
        "_call_figure_locator_classifier",
        AsyncMock(
            return_value=locator.FigureLocatorBatchOutput(
                candidates=[
                    locator.FigureLocatorCandidateOutput(
                        candidate_id=chunk.id,
                        mentions=[
                            locator.FigureLocatorMentionOutput(
                                text="Figures 2-4",
                                cardinality="single",
                                kind="figure",
                                number="2-4",
                                canonical_reference="Figure 2-4",
                            )
                        ],
                    )
                ]
            )
        ),
    )

    await locator.resolve_figure_locators([chunk])

    annotation = _resolution_for(chunk).annotations[0]
    assert annotation.cardinality == "uncertain"
    assert annotation.canonical_reference is None


@pytest.mark.parametrize(
    ("source", "mention_text"),
    [
        ("Fig. 1 appears before Fig. 1.", "Fig. 1"),
        ("Figure 1 shows the result.", "Fig. 1"),
    ],
    ids=["non_unique", "not_verbatim"],
)
@pytest.mark.asyncio
async def test_invalid_verbatim_grounding_marks_candidate_uncertain(
    monkeypatch,
    source,
    mention_text,
) -> None:
    chunk = _chunk("chunk-0", source)
    monkeypatch.setattr(
        locator,
        "_call_figure_locator_classifier",
        AsyncMock(
            return_value=locator.FigureLocatorBatchOutput(
                candidates=[
                    locator.FigureLocatorCandidateOutput(
                        candidate_id=chunk.id,
                        mentions=[
                            locator.FigureLocatorMentionOutput(
                                text=mention_text,
                                cardinality="single",
                                kind="figure",
                                number="1",
                                canonical_reference="Figure 1",
                            )
                        ],
                    )
                ]
            )
        ),
    )

    await locator.resolve_figure_locators([chunk])

    resolution = _resolution_for(chunk)
    assert resolution.status == "uncertain"
    assert resolution.annotations == []


@pytest.mark.asyncio
async def test_provider_caption_without_anchor_is_selected_and_structured(monkeypatch) -> None:
    chunk = _chunk(
        "chunk-0",
        "Provider Figure: Figure 1\nFigure label: Figure 1\nLegend:\nSignal expands.",
        parent_section=None,
        section_path=[
            PROVIDER_FIGURE_METADATA_SECTION,
            "Provider Figure: Figure 1",
        ],
        element_type=ElementType.TITLE,
    )
    classifier = AsyncMock(
        return_value=locator.FigureLocatorBatchOutput(
            candidates=[
                locator.FigureLocatorCandidateOutput(
                    candidate_id=chunk.id,
                    mentions=[],
                )
            ]
        )
    )
    monkeypatch.setattr(locator, "_call_figure_locator_classifier", classifier)

    await locator.resolve_figure_locators(
        [chunk],
        provider_figure_metadata=(
            {
                "figure_index": 0,
                "figure_label": "Figure 1",
                "figure_number": "1",
                "caption_text": "Signal expands.",
            },
        ),
    )

    assert classifier.await_count == 1
    assert classifier.await_args is not None
    assert classifier.await_args.args[0][0][1] == "Signal expands."
    provider = _provider_reference_for(chunk)
    assert provider.status == "single"
    assert provider.raw_label == "Figure 1"
    assert provider.raw_number == "1"
    assert provider.canonical_reference == "Figure 1"
    assert _resolution_for(chunk).status == "resolved"


@pytest.mark.asyncio
async def test_provider_mention_offsets_ignore_repeated_wrapper_label(
    monkeypatch,
) -> None:
    content = (
        "Provider Figure: Figure 1\n"
        "Figure label: Figure 1\n"
        "Legend:\n"
        "Figure 1. Signal expands."
    )
    chunk = _chunk(
        "chunk-0",
        content,
        parent_section=None,
        section_path=[
            PROVIDER_FIGURE_METADATA_SECTION,
            "Provider Figure: Figure 1",
        ],
        element_type=ElementType.TITLE,
    )
    monkeypatch.setattr(
        locator,
        "_call_figure_locator_classifier",
        AsyncMock(
            return_value=locator.FigureLocatorBatchOutput(
                candidates=[
                    locator.FigureLocatorCandidateOutput(
                        candidate_id=chunk.id,
                        mentions=[
                            locator.FigureLocatorMentionOutput(
                                text="Figure 1",
                                cardinality="single",
                                kind="figure",
                                number="1",
                                canonical_reference="Figure 1",
                            )
                        ],
                    )
                ]
            )
        ),
    )

    await locator.resolve_figure_locators(
        [chunk],
        provider_figure_metadata=(
            {
                "figure_label": "Figure 1",
                "figure_number": "1",
                "caption_text": "Figure 1. Signal expands.",
            },
        ),
    )

    resolution = _resolution_for(chunk)
    assert resolution.status == "resolved"
    annotation = resolution.annotations[0]
    assert annotation.char_start == content.rindex("Figure 1")
    assert content[annotation.char_start : annotation.char_end] == "Figure 1"


@pytest.mark.asyncio
async def test_provider_label_number_conflict_is_explicit(monkeypatch) -> None:
    chunk = _chunk(
        "chunk-0",
        "Provider Figure: Figure 1\nLegend:\nSignal expands.",
        parent_section=PROVIDER_FIGURE_METADATA_SECTION,
        subsection="Provider Figure: Figure 1",
        element_type=ElementType.TITLE,
    )
    monkeypatch.setattr(
        locator,
        "_call_figure_locator_classifier",
        AsyncMock(
            return_value=locator.FigureLocatorBatchOutput(
                candidates=[
                    locator.FigureLocatorCandidateOutput(
                        candidate_id=chunk.id,
                        mentions=[],
                    )
                ]
            )
        ),
    )

    await locator.resolve_figure_locators(
        [chunk],
        provider_figure_metadata=(
            {
                "figure_label": "Figure 1",
                "figure_number": "2",
                "caption_text": "Signal expands.",
            },
        ),
    )

    provider = _provider_reference_for(chunk)
    assert provider.status == "conflict"
    assert provider.canonical_reference is None


@pytest.mark.asyncio
async def test_unparsable_populated_provider_label_cannot_promote_number(
    monkeypatch,
) -> None:
    chunk = _chunk(
        "chunk-0",
        "Provider Figure: Figures 1 and 2\nLegend:\nSignal expands.",
        parent_section=PROVIDER_FIGURE_METADATA_SECTION,
        subsection="Provider Figure: Figures 1 and 2",
        element_type=ElementType.TITLE,
    )
    monkeypatch.setattr(
        locator,
        "_call_figure_locator_classifier",
        AsyncMock(
            return_value=locator.FigureLocatorBatchOutput(
                candidates=[
                    locator.FigureLocatorCandidateOutput(
                        candidate_id=chunk.id,
                        mentions=[],
                    )
                ]
            )
        ),
    )

    await locator.resolve_figure_locators(
        [chunk],
        provider_figure_metadata=(
            {
                "figure_label": "Figures 1 and 2",
                "figure_number": "1",
                "caption_text": "Signal expands.",
            },
        ),
    )

    provider = _provider_reference_for(chunk)
    assert provider.status == "invalid"
    assert provider.canonical_reference is None


@pytest.mark.asyncio
async def test_common_supplementary_continuation_label_is_structured(
    monkeypatch,
) -> None:
    chunk = _chunk(
        "chunk-0",
        "Provider Figure: Supplementary Fig. S1 (continued)\nLegend:\nSignal expands.",
        parent_section=PROVIDER_FIGURE_METADATA_SECTION,
        subsection="Provider Figure: Supplementary Fig. S1 (continued)",
        element_type=ElementType.TITLE,
    )
    monkeypatch.setattr(
        locator,
        "_call_figure_locator_classifier",
        AsyncMock(
            return_value=locator.FigureLocatorBatchOutput(
                candidates=[
                    locator.FigureLocatorCandidateOutput(
                        candidate_id=chunk.id,
                        mentions=[],
                    )
                ]
            )
        ),
    )

    await locator.resolve_figure_locators(
        [chunk],
        provider_figure_metadata=(
            {
                "figure_label": "Supplementary Fig. S1 (continued)",
                "figure_number": "S1",
                "caption_text": "Signal expands.",
            },
        ),
    )

    provider = _provider_reference_for(chunk)
    assert provider.status == "single"
    assert provider.canonical_reference == "Figure S1"


@pytest.mark.asyncio
async def test_provider_reference_ranges_exclude_cross_title_overlap(
    monkeypatch,
) -> None:
    first_caption = (
        "Context padding keeps the final sentence inside the configured overlap. "
        "First caption has a locator-free sentence that must not inherit the next reference."
    )
    second_caption = "Second caption has its own locator-free evidence."
    entries = (
        {
            "figure_index": 0,
            "figure_label": "Figure 1",
            "figure_number": "1",
            "caption_text": first_caption,
        },
        {
            "figure_index": 1,
            "figure_label": "Figure 2",
            "figure_number": "2",
            "caption_text": second_caption,
        },
    )
    markdown = append_provider_figure_metadata_markdown(
        "# Results\n\nNative article prose.",
        entries,
    )
    elements = markdown_to_pipeline_elements(markdown)
    chunks = await chunk_parsed_document(
        elements,
        ChunkingStrategy.get_research_strategy(),
        "doc-overlap",
    )
    captured_candidate_text: dict[str, str] = {}

    async def fake_classifier(candidates, **_kwargs):
        captured_candidate_text.update(
            {chunk.id: candidate_text for chunk, candidate_text in candidates}
        )
        return locator.FigureLocatorBatchOutput(
            candidates=[
                locator.FigureLocatorCandidateOutput(
                    candidate_id=chunk.id,
                    mentions=[],
                )
                for chunk, _candidate_text in candidates
            ]
        )

    monkeypatch.setattr(locator, "_call_figure_locator_classifier", fake_classifier)

    await locator.resolve_figure_locators(
        chunks,
        provider_figure_metadata=entries,
    )

    second_chunk = next(
        chunk
        for chunk in chunks
        if "Provider Figure: Figure 2" in chunk.content
    )
    assert "must not inherit the next reference" in second_chunk.content
    assert "must not inherit" not in captured_candidate_text[second_chunk.id]
    provider = _provider_reference_for(second_chunk)
    assert provider.canonical_reference == "Figure 2"
    semantic_text = "\n".join(
        second_chunk.content[item.char_start : item.char_end]
        for item in provider.semantic_ranges
    )
    assert semantic_text == second_caption

    stored_chunk = second_chunk.model_dump()
    stored_chunk["text"] = stored_chunk.pop("content")
    spans = build_evidence_spans(
        chunk_id=second_chunk.id,
        chunk_text=second_chunk.content,
    )
    overlapped_span = next(
        span for span in spans if "must not inherit" in span.text
    )
    second_span = next(
        span for span in spans if "Second caption" in span.text
    )

    overlap_result = _resolve_stored_figure_reference(
        stored_chunk,
        overlapped_span,
    )
    second_result = _resolve_stored_figure_reference(stored_chunk, second_span)

    assert overlap_result.reference is None
    assert overlap_result.blocked is False
    assert second_result.reference == "Figure 2"
    assert second_result.blocked is False


@pytest.mark.parametrize(
    ("malformed_range", "expected_blocked"),
    [(False, False), (True, True)],
    ids=["valid_ranges", "malformed_ranges_fail_closed"],
)
def test_provider_reference_does_not_cross_final_subsection_boundary(
    malformed_range,
    expected_blocked,
) -> None:
    subsection = "Provider Figure: Figure 2"
    caption = "Current caption has locator-free evidence."
    content = (
        "carried overlap without terminal punctuation "
        f"{subsection}\nLegend:\n{caption}"
    )
    semantic_ranges = [
        {"char_start": start, "char_end": end}
        for start, end in provider_figure_semantic_ranges(content, subsection)
    ]
    if malformed_range:
        semantic_ranges[0]["char_end"] = len(content) + 1
    chunk = {
        "text": content,
        "parent_section": PROVIDER_FIGURE_METADATA_SECTION,
        "subsection": subsection,
        "metadata": {
            "figure_locator_resolution": {
                "schema_version": 1,
                "prompt_version": "figure-locator-v1",
                "model": "gpt-5.6-terra",
                "reasoning": "low",
                "status": "resolved",
                "annotations": [],
            },
            "provider_figure_reference": {
                "schema_version": 1,
                "raw_label": "Figure 2",
                "raw_number": "2",
                "status": "single",
                "kind": "figure",
                "number": "2",
                "panels": [],
                "canonical_reference": "Figure 2",
                "semantic_ranges": semantic_ranges,
            },
        },
    }
    span = build_evidence_spans(chunk_id="chunk-boundary", chunk_text=content)[0]
    assert span.char_start < content.rindex(subsection) < span.char_end

    result = _resolve_stored_figure_reference(chunk, span)

    assert result.reference is None
    assert result.blocked is expected_blocked


@pytest.mark.asyncio
async def test_terra_xhigh_reasoning_is_accepted_from_catalog(monkeypatch) -> None:
    monkeypatch.setenv("FIGURE_LOCATOR_LLM_REASONING", "xhigh")
    classifier = AsyncMock(
        return_value=locator.FigureLocatorBatchOutput(
            candidates=[
                locator.FigureLocatorCandidateOutput(
                    candidate_id="chunk-0",
                    mentions=[],
                )
            ]
        )
    )
    monkeypatch.setattr(locator, "_call_figure_locator_classifier", classifier)
    chunk = _chunk("chunk-0", "Figure 1 shows signal.")

    await locator.resolve_figure_locators([chunk])

    classifier.assert_awaited_once()
    assert classifier.await_args is not None
    assert classifier.await_args.kwargs["reasoning_effort"] == "xhigh"
    assert _resolution_for(chunk).reasoning == "xhigh"


@pytest.mark.asyncio
async def test_terra_minimal_reasoning_is_rejected_from_catalog(monkeypatch) -> None:
    monkeypatch.setenv("FIGURE_LOCATOR_LLM_REASONING", "minimal")
    classifier = AsyncMock()
    monkeypatch.setattr(locator, "_call_figure_locator_classifier", classifier)
    chunk = _chunk("chunk-0", "Figure 1 shows signal.")

    with pytest.raises(ValueError, match="not supported by model 'gpt-5.6-terra'"):
        await locator.resolve_figure_locators([chunk])

    classifier.assert_not_awaited()
    assert chunk.metadata.figure_locator_resolution is None


@pytest.mark.asyncio
async def test_catalog_litellm_model_uses_its_provider_without_openai_key(
    monkeypatch,
) -> None:
    model_id = "catalog-gemini"
    monkeypatch.setattr(
        "src.lib.config.models_loader.get_model",
        lambda selected: SimpleNamespace(
            model_id=model_id,
            provider="gemini",
            supports_reasoning=True,
            supports_temperature=True,
            reasoning_options=["low", "high"],
        )
        if selected == model_id
        else None,
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda selected: SimpleNamespace(
            provider_id="gemini",
            driver="litellm",
            api_key_env="GEMINI_API_KEY",
            base_url_env=None,
            default_base_url=None,
            litellm_prefix="gemini",
            drop_params=True,
            supports_parallel_tool_calls=True,
        )
        if selected == "gemini"
        else None,
    )
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    model_object = object()
    litellm_model = MagicMock(return_value=model_object)
    monkeypatch.setattr(
        "agents.extensions.models.litellm_model.LitellmModel",
        litellm_model,
    )
    agent_factory = MagicMock(
        side_effect=lambda **kwargs: SimpleNamespace(name=kwargs["name"])
    )
    monkeypatch.setattr("agents.Agent", agent_factory)

    output = locator.FigureLocatorBatchOutput(
        candidates=[
            locator.FigureLocatorCandidateOutput(
                candidate_id="chunk-0",
                mentions=[],
            )
        ]
    )
    runner = AsyncMock(return_value=SimpleNamespace(final_output=output))
    monkeypatch.setattr("agents.Runner.run", runner)

    result = await locator._call_figure_locator_classifier(
        [(_chunk("chunk-0", "Figure 1 shows signal."), "Figure 1 shows signal.")],
        model_name=model_id,
        reasoning_effort="high",
    )

    assert result is output
    litellm_model.assert_called_once_with(
        model="gemini/catalog-gemini",
        base_url=None,
        api_key="test-gemini-key",
    )
    assert agent_factory.call_args is not None
    assert agent_factory.call_args.kwargs["model"] is model_object
    runner.assert_awaited_once()
