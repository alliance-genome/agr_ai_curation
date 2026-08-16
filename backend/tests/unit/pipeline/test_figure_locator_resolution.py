"""Unit tests for ingestion-time figure locator resolution."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.lib.document_sources.figure_metadata import PROVIDER_FIGURE_METADATA_SECTION
from src.lib.document_sources.figure_metadata import (
    append_provider_figure_metadata_markdown,
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
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


def test_candidate_regex_only_selects_broad_locator_anchors() -> None:
    assert locator.is_figure_locator_candidate("As shown in Fig. 1A, signal rose.")
    assert locator.is_figure_locator_candidate("See panels A and B.")
    assert not locator.is_figure_locator_candidate("Configuration was unchanged.")
    assert not locator.is_figure_locator_candidate("Ordinary result text.")


def test_labeled_corpus_tracks_candidate_selection_separately() -> None:
    corpus_path = (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "figure_locator"
        / "semantic_cases.json"
    )
    cases = json.loads(corpus_path.read_text(encoding="utf-8"))

    assert [
        case["id"]
        for case in cases
        if locator.is_figure_locator_candidate(case["text"])
        != case["expected_candidate"]
    ] == []


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


@pytest.mark.asyncio
async def test_non_unique_verbatim_text_marks_only_candidate_uncertain(monkeypatch) -> None:
    chunk = _chunk("chunk-0", "Fig. 1 appears before Fig. 1.")
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
                                text="Fig. 1",
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


@pytest.mark.asyncio
async def test_missing_api_key_marks_candidates_uncertain(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY")
    classifier = AsyncMock()
    monkeypatch.setattr(locator, "_call_figure_locator_classifier", classifier)
    chunk = _chunk("chunk-0", "Figure 1 shows signal.")

    await locator.resolve_figure_locators([chunk])

    classifier.assert_not_awaited()
    assert _resolution_for(chunk).status == "uncertain"
