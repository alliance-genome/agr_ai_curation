"""Tests for record_evidence figure locator extraction."""

import pytest

import src.lib.openai_agents.tools.record_evidence as record_evidence
from src.lib.document_sources.figure_metadata import PROVIDER_FIGURE_METADATA_SECTION
from src.lib.openai_agents.evidence_spans import build_evidence_spans
from src.lib.openai_agents.tools.record_evidence import _extract_figure_reference


@pytest.fixture(autouse=True)
def identity_function_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(record_evidence, "function_tool", lambda fn: fn)


def _provider_chunk(text: str) -> dict[str, object]:
    return {
        "id": "provider-figure-1",
        "text": text,
        "page_number": 3,
        "parent_section": PROVIDER_FIGURE_METADATA_SECTION,
        "subsection": "Provider Figure: Figure 1",
        "metadata": {},
    }


def _provider_span_ids(text: str) -> list[str]:
    return [
        span.span_id
        for span in build_evidence_spans(
            chunk_id="provider-figure-1",
            chunk_text=text,
            page_number=3,
            section_title=PROVIDER_FIGURE_METADATA_SECTION,
        )
    ]


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


def test_provider_figure_metadata_omits_later_scoped_panel_in_prose() -> None:
    span_text = "Panel A shows signal, whereas B shows the control."
    chunk = {
        "text": span_text,
        "parent_section": PROVIDER_FIGURE_METADATA_SECTION,
        "subsection": "Provider Figure: Figure 1",
    }

    assert _extract_figure_reference(chunk, chunk["text"], span_text) is None


@pytest.mark.parametrize(
    "span_text",
    (
        "Panels A (left) and B (right) show different patterns.",
        "Panels (A) and (B) show different patterns.",
        "Panels labeled A and B show different patterns.",
        "The panels labeled as A and B show different patterns.",
        "Panels denoted A and B show different patterns.",
        "Panels denoted as A and B show different patterns.",
        "Panels marked A and B show different patterns.",
        "Panels marked as A and B show different patterns.",
        "Panels designated A and B show different patterns.",
        "Panels designated as A and B show different patterns.",
        "Panels identified as A and B show different patterns.",
        "Panels called A and B show different patterns.",
        "Panels known as A and B show different patterns.",
        "Panels referred to as A and B show different patterns.",
        "Subpanels A and B show different patterns.",
        "Subfigures A and B show different patterns.",
        "Figure 1 contains subfigures A and B.",
        "Figure 1 panels A, and B show different patterns.",
        "The A and B panels show different patterns.",
        "The two panels, A and B, show different patterns.",
        "Figure 1 shows the A and B panels.",
        "Figure 1(A) and (B) show different patterns.",
        "Figure 1 panel A (left) and B (right) show different patterns.",
        "Panels A (left), B (center), and C (right) show different patterns.",
    ),
)
def test_provider_figure_metadata_omits_described_panel_lists(
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
        "Fig. 1b, and a show different expression patterns.",
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
        "Fig. 1A and subfigure B show different patterns.",
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
        "Fig. 1A compared with B shows the difference.",
        "Fig. 1A compared to B shows the difference.",
        "Fig. 1A is compared with B.",
        "Fig. 1A together with B shows the difference.",
        "Fig. 1A alongside B shows the difference.",
        "Fig. 1A and B. Results are shown separately.",
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
            "Fig. 1b, and a second assay confirms the result.",
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
            "Fig. 1b and a questionnaire is shown.",
            "Fig. 1b",
        ),
        (
            "Fig. 1b and a survey confirms the result.",
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
    "span_text",
    (
        "Fig. 1A and B cells were quantified.",
        "Fig. 1A and T cells were quantified.",
        "Fig. 1A and C. elegans embryos were analyzed.",
        "Fig. 1A compared with B cells shows the difference.",
        "Fig. 1A compared to B cells shows the difference.",
        "Fig. 1A is compared with T cells.",
        "Fig. 1A together with C. elegans embryos supports the result.",
        "Fig. 1A alongside B cells shows the expression pattern.",
        "Fig. 1A and B samples were analyzed.",
        "Fig. 1A compared with T cohorts shows the difference.",
    ),
)
def test_provider_figure_metadata_preserves_locator_before_uppercase_prose(
    span_text: str,
) -> None:
    chunk = {
        "text": span_text,
        "parent_section": PROVIDER_FIGURE_METADATA_SECTION,
        "subsection": "Provider Figure: Figure 1",
    }

    assert _extract_figure_reference(chunk, chunk["text"], span_text) == "Fig. 1A"


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


@pytest.mark.parametrize(
    "chunk_text",
    (
        "Panel (A and B) shows different expression patterns.",
        "Panels (A and B) show different expression patterns.",
        "Panels (A/B) show different expression patterns.",
        "Panels [A and B] show different expression patterns.",
        "Subpanels (A, B) show different expression patterns.",
        "Subfigure [A/B] shows different expression patterns.",
        "Panels A (left), with B shown at right.",
        "Panels A with B shown at right.",
        "Panel A, with B shown at right.",
        "Panels A relative to B show the contrast.",
        "Panels A in comparison with B show the contrast.",
    ),
)
@pytest.mark.asyncio
async def test_record_evidence_omits_fallback_for_ambiguous_panel_lists(
    monkeypatch: pytest.MonkeyPatch,
    chunk_text: str,
) -> None:
    chunk = _provider_chunk(chunk_text)

    async def _fake_get_chunk_by_id(**_kwargs: object) -> dict[str, object]:
        return chunk

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    result = await tool(entity="wg", span_ids=_provider_span_ids(chunk_text))

    assert result["status"] == "verified"
    assert all(
        "figure_reference" not in fragment
        for fragment in result["source_fragments"]
    )
    assert "figure_reference" not in result


@pytest.mark.parametrize(
    "chunk_text",
    (
        "Panel A with signal restricted to the wing disc.",
        "Panel A with B cells showing the expression pattern.",
    ),
)
@pytest.mark.asyncio
async def test_record_evidence_preserves_fallback_for_non_panel_with_prose(
    monkeypatch: pytest.MonkeyPatch,
    chunk_text: str,
) -> None:
    chunk = _provider_chunk(chunk_text)

    async def _fake_get_chunk_by_id(**_kwargs: object) -> dict[str, object]:
        return chunk

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    result = await tool(entity="wg", span_ids=_provider_span_ids(chunk_text))

    assert result["status"] == "verified"
    assert all(
        fragment["figure_reference"] == "Figure 1"
        for fragment in result["source_fragments"]
    )
    assert result["figure_reference"] == "Figure 1"


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


@pytest.mark.asyncio
async def test_record_evidence_multi_span_prefers_span_panel_over_structured_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk_text = (
        "Fig. 1A shows the pattern. "
        "The wing disc has restricted expression."
    )
    chunk = _provider_chunk(chunk_text)
    span_ids = _provider_span_ids(chunk_text)

    async def _fake_get_chunk_by_id(**_kwargs: object) -> dict[str, object]:
        return chunk

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    result = await tool(entity="wg", span_ids=span_ids)

    assert result["status"] == "verified"
    assert [
        fragment.get("figure_reference")
        for fragment in result["source_fragments"]
    ] == ["Fig. 1A", "Figure 1"]
    assert result["figure_reference"] == "Fig. 1A"


@pytest.mark.asyncio
async def test_record_evidence_multi_span_omits_panel_for_incompatible_structured_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    figure_1_text = "Fig. 1A shows the first pattern."
    figure_2_text = "The wing disc has restricted expression."
    chunks = {
        "provider-figure-1": _provider_chunk(figure_1_text),
        "provider-figure-2": {
            **_provider_chunk(figure_2_text),
            "id": "provider-figure-2",
            "subsection": "Provider Figure: Figure 2",
        },
    }
    span_ids = [
        build_evidence_spans(
            chunk_id=chunk_id,
            chunk_text=chunk["text"],
            page_number=chunk["page_number"],
            section_title=chunk["parent_section"],
        )[0].span_id
        for chunk_id, chunk in chunks.items()
    ]

    async def _fake_get_chunk_by_id(**kwargs: object) -> dict[str, object]:
        return chunks[str(kwargs["chunk_id"])]

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    result = await tool(entity="wg", span_ids=span_ids)

    assert result["status"] == "verified"
    assert [
        fragment.get("figure_reference")
        for fragment in result["source_fragments"]
    ] == ["Fig. 1A", "Figure 2"]
    assert "figure_reference" not in result


@pytest.mark.asyncio
async def test_record_evidence_multi_span_omits_conflicting_span_panels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk_text = "Fig. 1A shows one pattern. Fig. 1B shows another pattern."
    chunk = _provider_chunk(chunk_text)
    span_ids = _provider_span_ids(chunk_text)

    async def _fake_get_chunk_by_id(**_kwargs: object) -> dict[str, object]:
        return chunk

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    result = await tool(entity="wg", span_ids=span_ids)

    assert result["status"] == "verified"
    assert [
        fragment.get("figure_reference")
        for fragment in result["source_fragments"]
    ] == ["Fig. 1A", "Fig. 1B"]
    assert "figure_reference" not in result


@pytest.mark.asyncio
async def test_record_evidence_multi_span_does_not_replace_ambiguous_span_with_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk_text = (
        "Fig. 1A,B show different patterns. "
        "The wing disc has restricted expression."
    )
    chunk = _provider_chunk(chunk_text)
    span_ids = _provider_span_ids(chunk_text)

    async def _fake_get_chunk_by_id(**_kwargs: object) -> dict[str, object]:
        return chunk

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    result = await tool(entity="wg", span_ids=span_ids)

    assert result["status"] == "verified"
    assert [
        fragment.get("figure_reference")
        for fragment in result["source_fragments"]
    ] == [None, "Figure 1"]
    assert "figure_reference" not in result
