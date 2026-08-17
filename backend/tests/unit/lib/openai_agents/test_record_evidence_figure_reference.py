"""Tests for stored figure locator resolution in record_evidence."""

import json
from pathlib import Path

import pytest

import src.lib.openai_agents.tools.record_evidence as record_evidence_module
from src.lib.document_sources.figure_metadata import PROVIDER_FIGURE_METADATA_SECTION
from src.lib.openai_agents.evidence_spans import build_evidence_spans
from src.lib.openai_agents.tools.record_evidence import (
    _resolve_stored_figure_reference,
)


_CORPUS_PATH = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "figure_locator"
    / "semantic_cases.json"
)
_AGGREGATION_CASES = json.loads(
    _CORPUS_PATH.read_text(encoding="utf-8")
)["aggregation_cases"]


def _span(chunk_id: str, text: str):
    return build_evidence_spans(chunk_id=chunk_id, chunk_text=text)[0]


def _resolution(*annotations, status="resolved"):
    return {
        "schema_version": 1,
        "prompt_version": "figure-locator-v1",
        "model": "gpt-5.6-terra",
        "reasoning": "low",
        "status": status,
        "annotations": list(annotations),
    }


def _annotation(
    text: str,
    source: str,
    *,
    cardinality="single",
    canonical: str | None = "Figure 1A",
):
    start = source.index(text)
    return {
        "text": text,
        "char_start": start,
        "char_end": start + len(text),
        "cardinality": cardinality,
        "kind": "figure",
        "number": "1",
        "panels": ["A"],
        "canonical_reference": canonical if cardinality == "single" else None,
    }


def test_span_uses_exact_overlapping_singleton_annotation() -> None:
    text = "Fig. 1A shows expression in the tissue."
    chunk = {
        "text": text,
        "metadata": {
            "figure_locator_resolution": _resolution(
                _annotation("Fig. 1A", text)
            )
        },
    }

    result = _resolve_stored_figure_reference(chunk, _span("chunk-1", text))

    assert result.reference == "Figure 1A"
    assert result.blocked is False


def test_multi_panel_annotation_blocks_singleton_provenance() -> None:
    text = "Fig. 1A and Fig. 1B show different patterns."
    chunk = {
        "text": text,
        "metadata": {
            "figure_locator_resolution": _resolution(
                _annotation(
                    "Fig. 1A and Fig. 1B",
                    text,
                    cardinality="multiple",
                    canonical=None,
                )
            )
        },
    }

    result = _resolve_stored_figure_reference(chunk, _span("chunk-2", text))

    assert result.reference is None
    assert result.blocked is True


def test_provider_caption_without_semantic_locator_uses_structured_reference() -> None:
    text = "Legend:\nExpression expands in the wing disc."
    chunk = {
        "text": text,
        "section_path": [
            PROVIDER_FIGURE_METADATA_SECTION,
            "Provider Figure: Figure 1",
        ],
        "metadata": {
            "figure_locator_resolution": _resolution(),
            "provider_figure_reference": {
                "schema_version": 1,
                "raw_label": "Figure 1",
                "raw_number": "1",
                "status": "single",
                "kind": "figure",
                "number": "1",
                "panels": [],
                "canonical_reference": "Figure 1",
                "semantic_ranges": [
                    {
                        "char_start": text.index("Expression"),
                        "char_end": len(text),
                    }
                ],
            },
        },
    }

    result = _resolve_stored_figure_reference(chunk, _span("chunk-3", text))

    assert result.reference == "Figure 1"
    assert result.blocked is False


def test_stale_annotation_offsets_fail_closed() -> None:
    text = "Fig. 1A shows expression."
    annotation = _annotation("Fig. 1A", text)
    annotation["char_end"] += 1
    chunk = {
        "text": text,
        "metadata": {
            "figure_locator_resolution": _resolution(annotation),
        },
    }

    result = _resolve_stored_figure_reference(chunk, _span("chunk-stale", text))

    assert result.reference is None
    assert result.blocked is True


def test_provider_conflict_blocks_semantic_reference() -> None:
    text = "Fig. 1A shows expression in the tissue."
    chunk = {
        "text": text,
        "parent_section": PROVIDER_FIGURE_METADATA_SECTION,
        "subsection": "Provider Figure: Figure 2",
        "metadata": {
            "figure_locator_resolution": _resolution(
                _annotation("Fig. 1A", text)
            ),
            "provider_figure_reference": {
                "schema_version": 1,
                "raw_label": "Figure 2",
                "raw_number": "2",
                "status": "single",
                "kind": "figure",
                "number": "2",
                "panels": [],
                "canonical_reference": "Figure 2",
                "semantic_ranges": [
                    {"char_start": 0, "char_end": len(text)}
                ],
            },
        },
    }

    result = _resolve_stored_figure_reference(chunk, _span("chunk-4", text))

    assert result.reference is None
    assert result.blocked is True


def test_uncertain_classifier_result_blocks_provider_fallback() -> None:
    text = "Fig. 1A,B shows expression."
    chunk = {
        "text": text,
        "parent_section": PROVIDER_FIGURE_METADATA_SECTION,
        "subsection": "Provider Figure: Figure 1",
        "metadata": {
            "figure_locator_resolution": _resolution(status="uncertain"),
            "provider_figure_reference": {
                "schema_version": 1,
                "status": "single",
                "kind": "figure",
                "number": "1",
                "panels": [],
                "canonical_reference": "Figure 1",
                "semantic_ranges": [
                    {"char_start": 0, "char_end": len(text)}
                ],
            },
        },
    }

    result = _resolve_stored_figure_reference(chunk, _span("chunk-5", text))

    assert result.reference is None
    assert result.blocked is True


def test_legacy_chunk_text_is_not_reinterpreted_with_regex() -> None:
    text = "Fig. 1A shows expression."

    result = _resolve_stored_figure_reference(
        {"text": text, "metadata": {}},
        _span("chunk-6", text),
    )

    assert result.reference is None
    assert result.blocked is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    _AGGREGATION_CASES,
    ids=[case["id"] for case in _AGGREGATION_CASES],
)
async def test_record_evidence_corpus_aggregates_only_one_safe_reference(
    monkeypatch,
    case,
) -> None:
    text = case["text"]
    annotations = []
    for item in case["annotations"]:
        start = text.index(item["text"])
        annotations.append(
            {
                **item,
                "char_start": start,
                "char_end": start + len(item["text"]),
                "canonical_reference": (
                    item.get("canonical_reference")
                    if item["cardinality"] == "single"
                    else None
                ),
            }
        )
    chunk = {
        "id": "chunk-aggregate",
        "text": text,
        "parent_section": "Results",
        "metadata": {
            "figure_locator_resolution": _resolution(*annotations),
        },
    }
    spans = build_evidence_spans(
        chunk_id=chunk["id"],
        chunk_text=text,
        section_title="Results",
    )
    selected_span_ids = [
        spans[index].span_id for index in case["span_indexes"]
    ]

    async def fake_get_chunk_by_id(**_kwargs):
        return chunk

    monkeypatch.setattr(
        record_evidence_module,
        "get_chunk_by_id",
        fake_get_chunk_by_id,
    )
    monkeypatch.setattr(
        record_evidence_module,
        "function_tool",
        lambda function: function,
    )
    tool = record_evidence_module.create_record_evidence_tool(
        "doc-aggregate",
        "user-1",
    )

    result = await tool(entity="expression", span_ids=selected_span_ids)

    assert result["status"] == "verified"
    expected_reference = case["expected_aggregate_reference"]
    if expected_reference is None:
        assert "figure_reference" not in result
    else:
        assert result["figure_reference"] == expected_reference
