"""Unit tests for deterministic PDF evidence spans."""

import pytest

from src.lib.openai_agents.evidence_spans import (
    EVIDENCE_SPAN_HASH_POLICY,
    EVIDENCE_SPANIZER_VERSION,
    EvidenceSpanResolutionError,
    build_evidence_spans,
    parse_evidence_span_id,
    resolve_evidence_span_id,
)


def test_build_evidence_spans_is_deterministic_and_preserves_exact_offsets():
    text = (
        "First sentence.  Second sentence mentions crb mutants.\n"
        "Fig. 2 shows the result. Final sentence"
    )

    first = build_evidence_spans(
        chunk_id="chunk-abc",
        chunk_text=text,
        page_number=9,
        section_title="Results",
    )
    second = build_evidence_spans(
        chunk_id="chunk-abc",
        chunk_text=text,
        page_number=9,
        section_title="Results",
    )

    assert [span.span_id for span in first] == [span.span_id for span in second]
    assert [span.text for span in first] == [
        "First sentence.",
        "Second sentence mentions crb mutants.",
        "Fig. 2 shows the result.",
        "Final sentence",
    ]
    assert first[1].text == text[first[1].char_start:first[1].char_end]
    assert first[1].page_number == 9
    assert first[1].section_title == "Results"
    assert first[1].spanizer_version == EVIDENCE_SPANIZER_VERSION
    assert EVIDENCE_SPANIZER_VERSION in EVIDENCE_SPAN_HASH_POLICY
    assert "sha256" in EVIDENCE_SPAN_HASH_POLICY


def test_resolve_evidence_span_id_parses_offsets_and_validates_hash():
    text = "Alpha sentence. Beta sentence supports evidence."
    spans = build_evidence_spans(chunk_id="chunk-abc", chunk_text=text)
    selected = spans[1]

    parsed = parse_evidence_span_id(selected.span_id)
    assert parsed.chunk_id == "chunk-abc"
    assert parsed.span_index == 1
    assert parsed.char_start == selected.char_start
    assert parsed.char_end == selected.char_end

    resolved = resolve_evidence_span_id(
        span_id=selected.span_id,
        chunk_text=text,
        expected_chunk_id="chunk-abc",
        page_number=4,
        section_title="Results",
    )

    assert resolved.text == "Beta sentence supports evidence."
    assert resolved.text == text[selected.char_start:selected.char_end]
    assert resolved.page_number == 4
    assert resolved.section_title == "Results"


def test_parse_evidence_span_id_accepts_more_than_four_span_digits():
    parsed = parse_evidence_span_id("chunk-abc:s12345:c0000-c0005:deadbeef")

    assert parsed.chunk_id == "chunk-abc"
    assert parsed.span_index == 12345
    assert parsed.char_start == 0
    assert parsed.char_end == 5
    assert parsed.text_hash == "deadbeef"


def test_parse_evidence_span_id_rejects_non_string_values():
    with pytest.raises(TypeError, match="span_id must be a string"):
        parse_evidence_span_id(None)  # type: ignore[arg-type]


def test_resolve_evidence_span_id_rejects_wrong_chunk_or_changed_text():
    text = "Alpha sentence. Beta sentence supports evidence."
    selected = build_evidence_spans(chunk_id="chunk-abc", chunk_text=text)[1]

    with pytest.raises(EvidenceSpanResolutionError, match="chunk ID"):
        resolve_evidence_span_id(
            span_id=selected.span_id,
            chunk_text=text,
            expected_chunk_id="other-chunk",
        )

    changed_text = text.replace("Beta", "Gamma")
    with pytest.raises(EvidenceSpanResolutionError, match="hash"):
        resolve_evidence_span_id(
            span_id=selected.span_id,
            chunk_text=changed_text,
            expected_chunk_id="chunk-abc",
        )
