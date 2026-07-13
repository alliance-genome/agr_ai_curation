"""Tests for record_evidence figure locator extraction."""

import pytest

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


@pytest.mark.parametrize(
    "span_text",
    (
        "Fig. 1A,B show different expression patterns.",
        "Fig. 1A and B show different expression patterns.",
        "Fig. 1(A,B) shows different expression patterns.",
        "Figure 1(A and B) shows different expression patterns.",
        "Fig. 1 A,B show different expression patterns.",
        "Fig. 1A (left) and panel B (right) show different patterns.",
        "Figure 1: A and B show different patterns.",
        "Fig. 1[A,B] shows different patterns.",
        "Figure 1 panels A and B show different expression patterns.",
        "Figure 1 panels A-C show different expression patterns.",
    ),
)
def test_provider_figure_metadata_omits_shorthand_multi_panel_locators(
    span_text: str,
) -> None:
    chunk = {
        "text": span_text,
        "parent_section": PROVIDER_FIGURE_METADATA_SECTION,
        "subsection": "Provider Figure: Figure 1",
    }

    assert _extract_figure_reference(chunk, chunk["text"], span_text) is None


@pytest.mark.parametrize(
    "span_text",
    (
        (
            "Fig. 1A (left), whereas panel B (right) shows the opposite "
            "pattern."
        ),
        "Fig. 1A (left), with panel B shown at right.",
        "Fig. 1A (left), compared with panel B (right).",
        "Fig. 1A (left), and panel B (right) show different patterns.",
    ),
)
def test_provider_figure_metadata_omits_later_explicit_panel_in_prose(
    span_text: str,
) -> None:
    chunk = {
        "text": span_text,
        "parent_section": PROVIDER_FIGURE_METADATA_SECTION,
        "subsection": "Provider Figure: Figure 1",
    }

    assert _extract_figure_reference(chunk, chunk["text"], span_text) is None


def test_provider_figure_metadata_does_not_fallback_when_span_is_multi_panel() -> None:
    chunk = {
        "text": "Panels A and B show different expression patterns.",
        "parent_section": PROVIDER_FIGURE_METADATA_SECTION,
        "subsection": "Provider Figure: Figure 1",
    }

    assert _extract_figure_reference(chunk, chunk["text"], chunk["text"]) is None


@pytest.mark.parametrize(
    "span_text",
    (
        "Fig. 1a,b show different expression patterns.",
        "Fig. 1a and b show different expression patterns.",
        "Fig. 1b,a show different expression patterns.",
        "Fig. 1b and a show different expression patterns.",
        "Fig. 1b & a show different expression patterns.",
        "Fig. 1b or a show alternative patterns.",
        "Fig. 1b and a contain distinct signals.",
        "Fig. 1b and a compare distinct conditions.",
        "Fig. 1b and a provide complementary evidence.",
        "Fig. 1b and a provide evidence that supports the model.",
        "Fig. 1b and a correspond to different genotypes.",
        "Unlike Fig. 1b, a clearly shows the complementary signal.",
        "In contrast to Fig. 1b, a independently confirms the result.",
        "Unlike Fig. 1b, a very clearly shows the complementary signal.",
        "In contrast to Fig. 1b, a quite independently confirms the result.",
        "Unlike Fig. 1b, a more clearly shows the complementary signal.",
        "Unlike Fig. 1b, a also shows the complementary signal.",
        "In contrast to Fig. 1b, a again confirms the result.",
        "Unlike Fig. 1b, a better illustrates the contrast.",
        "Fig. 1b and a still shows the complementary signal.",
        "Unlike Fig. 1b, a thus shows the complementary signal.",
        "In contrast to Fig. 1b, a indeed confirms the result.",
        "Unlike Fig. 1b, a always shows the complementary signal.",
        "Fig. 1b and a therefore shows the complementary signal.",
        "Fig. 1b and a maybe shows the complementary signal.",
        "Unlike Fig. 1b, a in the control shows the complementary signal.",
        "In contrast to Fig. 1b, a from the control confirms the result.",
        "Fig. 1b and a under this condition shows the expected pattern.",
        "Figure 1 panels a and b show different expression patterns.",
        "Panels a and b show different expression patterns.",
        "Figure 1 panels a-c show different expression patterns.",
    ),
)
def test_provider_figure_metadata_omits_lowercase_multi_panel_locators(
    span_text: str,
) -> None:
    chunk = {
        "text": span_text,
        "parent_section": PROVIDER_FIGURE_METADATA_SECTION,
        "subsection": "Provider Figure: Figure 1",
    }

    assert _extract_figure_reference(chunk, chunk["text"], span_text) is None


@pytest.mark.parametrize(
    "span_text",
    (
        "Figures 1 and 2 show different results.",
        "Figures 1A and 1B show different expression patterns.",
        "Figs. 1A and 1B show different expression patterns.",
        "Figures 1 and Figure 2 show different results.",
        "Tables 1 and 2 summarize different results.",
    ),
)
def test_provider_figure_metadata_does_not_fallback_for_plural_references(
    span_text: str,
) -> None:
    chunk = {
        "text": span_text,
        "parent_section": PROVIDER_FIGURE_METADATA_SECTION,
        "subsection": "Provider Figure: Figure 1",
    }

    assert _extract_figure_reference(chunk, chunk["text"], span_text) is None


@pytest.mark.parametrize(
    "span_text",
    (
        "Fig. 1A & B show different patterns.",
        "Figures 1 & 2 show different results.",
        "Panel A and panel B show different patterns.",
        "Fig. 1A to C show different patterns.",
        "Fig. 1A and panel B show different patterns.",
        "Figure 1A versus panel B shows the comparison.",
        "Fig. 1A through C show different patterns.",
        "Fig. 1A-B show different patterns.",
        "Fig. 1A–B show different patterns.",
        "Fig. 1A or B show alternative patterns.",
        "Panels A or B show alternative patterns.",
        "Fig. 1A and/or B show alternative patterns.",
        "Fig. 1A and-or B show alternative patterns.",
        "Fig. 1A vs. B shows the comparison.",
        "Fig. 1A + B show different patterns.",
        "Fig. 1A; B show different patterns.",
        "Fig. 1A as well as B show different patterns.",
    ),
)
def test_provider_figure_metadata_does_not_fallback_for_ambiguous_separators(
    span_text: str,
) -> None:
    chunk = {
        "text": span_text,
        "parent_section": PROVIDER_FIGURE_METADATA_SECTION,
        "subsection": "Provider Figure: Figure 1",
    }

    assert _extract_figure_reference(chunk, chunk["text"], span_text) is None


@pytest.mark.parametrize(
    ("span_text", "expected"),
    (
        (
            "Fig. 1A and a second assay confirms the result.",
            "Fig. 1A",
        ),
        (
            "Figure 1 and a model of the pathway are shown.",
            "Figure 1",
        ),
        (
            "Fig. 1A or a replicate from the same experiment supports this.",
            "Fig. 1A",
        ),
        (
            "Fig. 1a and a second assay confirms the result.",
            "Fig. 1a",
        ),
        (
            "Fig. 1b and a second assay confirms the result.",
            "Fig. 1b",
        ),
        (
            "Fig. 1b or a replicate from the same experiment supports this.",
            "Fig. 1b",
        ),
        (
            "Fig. 1b and a model of the pathway are shown.",
            "Fig. 1b",
        ),
        (
            "Fig. 1b and a control confirms the result.",
            "Fig. 1b",
        ),
        (
            "Fig. 1b and a control clearly confirms the result.",
            "Fig. 1b",
        ),
        (
            "Fig. 1b and a very strong control clearly confirms the result.",
            "Fig. 1b",
        ),
        (
            "Fig. 1b and a further experiment shows the effect.",
            "Fig. 1b",
        ),
        (
            "Fig. 1A (left), with panel A enlarged at right.",
            "Fig. 1A",
        ),
    ),
)
def test_provider_figure_metadata_preserves_locator_before_lowercase_prose(
    span_text: str,
    expected: str,
) -> None:
    chunk = {
        "text": span_text,
        "parent_section": PROVIDER_FIGURE_METADATA_SECTION,
        "subsection": "Provider Figure: Figure 1",
    }

    assert _extract_figure_reference(chunk, chunk["text"], span_text) == expected


@pytest.mark.parametrize(
    ("span_text", "expected"),
    (
        (
            "Fig. 1A and B-cell staining confirms the result.",
            "Fig. 1A",
        ),
        (
            "Fig. 1A and C-terminal staining confirms the result.",
            "Fig. 1A",
        ),
        (
            "Figure 1 and T-cell abundance is increased.",
            "Figure 1",
        ),
        (
            "Fig. 1A and B‑cell staining confirms the result.",
            "Fig. 1A",
        ),
        (
            "Fig. 1A and B–cell staining confirms the result.",
            "Fig. 1A",
        ),
        (
            "Fig. 1A contains a panel B-cell marker.",
            "Fig. 1A",
        ),
    ),
)
def test_provider_figure_metadata_preserves_locator_before_hyphenated_prose(
    span_text: str,
    expected: str,
) -> None:
    chunk = {
        "text": span_text,
        "parent_section": PROVIDER_FIGURE_METADATA_SECTION,
        "subsection": "Provider Figure: Figure 1",
    }

    assert _extract_figure_reference(chunk, chunk["text"], span_text) == expected


@pytest.mark.parametrize(
    "structured_fields",
    (
        {"subsection": "Provider Figure: Figure 1"},
        {"metadata": {"figure_label": "Figure 1"}},
        {"figure_number": "1"},
        {
            "subsection": "Provider Figure: Figure 1",
            "metadata": {"figure_label": "Fig. 1", "figure_number": "1"},
        },
    ),
)
def test_provider_figure_metadata_uses_unambiguous_structured_fallback(
    structured_fields: dict[str, object],
) -> None:
    chunk = {
        "text": "The wing disc shows a restricted expression pattern.",
        "parent_section": PROVIDER_FIGURE_METADATA_SECTION,
        **structured_fields,
    }

    assert _extract_figure_reference(chunk, chunk["text"], chunk["text"]) == "Figure 1"


def test_provider_figure_metadata_omits_conflicting_structured_fallbacks() -> None:
    chunk = {
        "text": "The wing disc shows a restricted expression pattern.",
        "parent_section": PROVIDER_FIGURE_METADATA_SECTION,
        "subsection": "Provider Figure: Figure 1",
        "metadata": {"figure_label": "Figure 2"},
    }

    assert _extract_figure_reference(chunk, chunk["text"], chunk["text"]) is None


def test_normal_chunk_still_omits_ambiguous_multiple_locators() -> None:
    chunk = {
        "text": "Figure 1 and Fig. 1A both appear in the same normal chunk.",
        "parent_section": "Results",
    }

    assert _extract_figure_reference(chunk, chunk["text"]) is None
