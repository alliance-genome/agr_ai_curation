"""Tests for record_evidence figure locator extraction."""

from src.lib.document_sources.figure_metadata import PROVIDER_FIGURE_METADATA_SECTION
from src.lib.openai_agents.tools.record_evidence import _extract_figure_reference


def test_provider_figure_metadata_prefers_span_locator_over_generated_wrapper() -> None:
    chunk = {
        "text": (
            "Provider Figure: Figure 1\n"
            "Figure label: Figure 1\n"
            "Legend:\n"
            "Fig. 1A shows wg expression in the wing disc."
        ),
        "parent_section": PROVIDER_FIGURE_METADATA_SECTION,
        "subsection": "Provider Figure: Figure 1",
    }

    assert (
        _extract_figure_reference(
            chunk,
            chunk["text"],
            "Fig. 1A shows wg expression in the wing disc.",
        )
        == "Fig. 1A"
    )


def test_provider_figure_metadata_keeps_multi_panel_ambiguity() -> None:
    chunk = {
        "text": (
            "Provider Figure: Figure 1\n"
            "Figure label: Figure 1\n"
            "Legend:\n"
            "Fig. 1A and Fig. 1B show different expression patterns."
        ),
        "parent_section": PROVIDER_FIGURE_METADATA_SECTION,
        "subsection": "Provider Figure: Figure 1",
    }

    assert (
        _extract_figure_reference(
            chunk,
            chunk["text"],
            "Fig. 1A and Fig. 1B show different expression patterns.",
        )
        is None
    )


def test_normal_chunk_still_omits_ambiguous_multiple_locators() -> None:
    chunk = {
        "text": "Figure 1 and Fig. 1A both appear in the same normal chunk.",
        "parent_section": "Results",
    }

    assert _extract_figure_reference(chunk, chunk["text"]) is None
