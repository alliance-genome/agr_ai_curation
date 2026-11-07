from copy import deepcopy

import pytest

from src.lib.openai_agents.document_result_view import document_result_view


def test_window_preserves_exact_evidence_and_routes_geometry_without_mutating_source():
    text = "α😀 was not expressed.\nSecond sentence."
    start = 40
    raw = {"chunk": {"chunk_id": "chunk-1", "content": text,
                     "content_range": {"start": start, "end": start + len(text), "total_chars": 200},
                     "span_page": {"next_span_offset": 9},
                     "doc_items": [{"page": 2, "bbox": [1, 2, 3, 4]}],
                     "evidence_spans": [{"span_id": "s1", "text": text[:20],
                                         "char_start": start, "char_end": start + 20}]}}
    original = deepcopy(raw)
    view, events = document_result_view("read_chunk", raw)
    chunk = view["chunk"]
    span = chunk["evidence_spans"][0]
    assert chunk["content"][span["char_start"] - chunk["content_start"]:span["char_end"] - chunk["content_start"]] == text[:20]
    assert "text" not in span and "doc_items" not in chunk
    assert chunk["span_page"] == raw["chunk"]["span_page"]
    assert events[0]["doc_items"] == raw["chunk"]["doc_items"]
    assert raw == original


@pytest.mark.parametrize("tool,key", [("search_document", "hits"), ("read_section", "section"), ("read_subsection", "subsection")])
def test_other_document_views_preserve_content_navigation_and_provenance(tool, key):
    record = {"chunk_id": "c1", "content": "not found in adults", "next_offset": 4,
              "doc_items": [{"page": 1}], "page_ended_by": None,
              "source_chunks": [{"chunk_id": "c1", "chunk_index": 42, "snippet": None}]}
    raw = {key: [record] if key == "hits" else record, "error_code": None}
    view, events = document_result_view(tool, raw)
    actual = view[key][0] if key == "hits" else view[key]
    assert actual["content"] == record["content"]
    assert actual["source_chunks"] == [{"chunk_index": 42}]
    assert actual["next_offset"] == 4
    assert "doc_items" not in actual
    assert "content_sha256" not in actual
    assert "page_ended_by" not in actual and "error_code" not in view
    assert events[0]["chunk_id"] == "c1"


def test_read_chunk_view_states_shared_span_metadata_once_and_drops_nulls_and_hash():
    text = "First sentence. Second sentence on page four."
    spans = [
        {"span_id": "s0", "span_index": 0, "span_type": "sentence", "text": text[:15],
         "char_start": 0, "char_end": 15, "page_number": 3, "section_title": "Results",
         "spanizer_version": "pdf_sentence_v1"},
        {"span_id": "s1", "span_index": 1, "span_type": "sentence", "text": text[16:],
         "char_start": 16, "char_end": len(text), "page_number": 4, "section_title": None,
         "spanizer_version": "pdf_sentence_v1"},
    ]
    raw = {"summary": "Read chunk 7.", "error_code": None, "result_bounds": None, "chunk": {
        "chunk_id": "c7", "chunk_index": 7, "chunk_number": 8, "previous_chunk_id": None,
        "next_chunk_id": "c8", "previous_chunk_index": None, "next_chunk_index": 8,
        "page_number": 3, "section_title": "Results", "subsection": None, "content": text,
        "evidence_spans": spans, "doc_items": None, "content_range": None, "span_page": None}}
    original = deepcopy(raw)

    view, _ = document_result_view("read_chunk", raw)

    assert view == {"summary": "Read chunk 7.", "chunk": {
        "chunk_index": 7, "next_chunk_index": 8, "page_number": 3, "section_title": "Results",
        "content": text, "content_start": 0, "span_text_access": view["chunk"]["span_text_access"],
        "span_type": "sentence", "spanizer_version": "pdf_sentence_v1",
        "evidence_spans": [
            {"span_index": 0, "char_start": 0, "char_end": 15},
            # Values that differ from the chunk's stay, including an explicit null.
            {"span_index": 1, "char_start": 16, "char_end": len(text), "page_number": 4,
             "section_title": None},
        ]}}
    assert "content_start" in view["chunk"]["span_text_access"]
    assert raw == original


def test_span_values_are_never_attributed_to_a_chunk_that_lacks_them():
    text = "One sentence. Two sentence."
    raw = {"chunk": {"chunk_id": "c", "content": text, "page_number": None, "section_title": None,
                     "evidence_spans": [
        {"span_id": "s0", "span_index": 0, "span_type": "sentence", "text": text[:13], "char_start": 0,
         "char_end": 13, "page_number": 4, "section_title": None, "spanizer_version": None},
        {"span_id": "s1", "span_index": 1, "span_type": "sentence", "text": text[14:], "char_start": 14,
         "char_end": len(text), "page_number": 4, "section_title": None, "spanizer_version": None}]}}
    view, _ = document_result_view("read_chunk", raw)
    chunk = view["chunk"]
    # Only span-only metadata moves to the chunk; the chunk's unknown page stays unknown.
    assert "page_number" not in chunk and "section_title" not in chunk and "spanizer_version" not in chunk
    assert chunk["span_type"] == "sentence"
    assert chunk["evidence_spans"] == [
        {"span_index": 0, "char_start": 0, "char_end": 13, "page_number": 4},
        {"span_index": 1, "char_start": 14, "char_end": len(text), "page_number": 4},
    ]


def test_mixed_span_metadata_stays_on_each_span():
    text = "A sentence. A caption"
    raw = {"chunk": {"chunk_id": "c", "content": text, "evidence_spans": [
        {"span_id": "s0", "span_index": 0, "span_type": "sentence", "text": text[:11],
         "char_start": 0, "char_end": 11},
        {"span_id": "s1", "span_index": 1, "span_type": "caption", "text": text[12:],
         "char_start": 12, "char_end": len(text)}]}}
    view, _ = document_result_view("read_chunk", raw)
    assert "span_type" not in view["chunk"]
    assert [span["span_type"] for span in view["chunk"]["evidence_spans"]] == ["sentence", "caption"]


def test_invalid_offsets_fail_explicitly_instead_of_changing_evidence():
    with pytest.raises(RuntimeError, match="required PDF") as caught:
        document_result_view("read_chunk", {"chunk": {"content": "negative", "evidence_spans": [
            {"span_id": "s1", "text": "positive", "char_start": 0, "char_end": 8}]}})
    assert isinstance(caught.value.__cause__, ValueError)
    assert "Evidence span" in str(caught.value.__cause__)


def test_large_chunk_has_one_copy_of_scientific_text_in_model_view():
    text = "This reagent was not used in the experiment. " * 200
    raw = {"chunk": {"chunk_id": "c", "content": text, "evidence_spans": [
        {"span_id": "s", "text": text, "char_start": 0, "char_end": len(text)}]}}
    import json
    view, _ = document_result_view("read_chunk", raw)
    assert len(json.dumps(view)) < len(json.dumps(raw)) * 0.6
    assert view["chunk"]["content"] == text


def test_catalog_routes_geometry_once_and_keeps_source_capture_complete(monkeypatch):
    from src.lib.agent_studio import catalog_service
    from src.lib.openai_agents import streaming_tools
    from agr_ai_curation_runtime.tool_result_bounds import full_tool_results_requested

    emitted = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", emitted.append)
    source = {"hits": [{"chunk_id": "a", "content": "negative", "page_number": 2},
                       {"chunk_id": "b", "content": "positive", "doc_items": [{"page": 4}]}]}
    view = catalog_service._document_model_result("search_document", source)
    assert len(emitted) == 2
    assert emitted[0]["doc_items"] == [{"page": 2}]
    assert "doc_items" not in view["hits"][1]
    with full_tool_results_requested():
        assert catalog_service._document_model_result("search_document", source) is source
    assert len(emitted) == 2


def test_section_provenance_identity_preserved():
    _, events = document_result_view("read_section", {
        "section": {"section_title": "Methods", "doc_items": [{"page": 3}]}})
    assert events[0]["chunk_id"] == "section:Methods"


@pytest.mark.parametrize("raw", [{"chunk": None}, "An error occurred while running the tool. Please try again."])
def test_required_chunk_failure_is_terminal_not_a_model_result(raw):
    with pytest.raises(RuntimeError, match="required PDF"):
        document_result_view("read_chunk", raw)


def test_no_search_matches_is_a_valid_result():
    raw = {"hits": [], "summary": "No matches"}
    assert document_result_view("search_document", raw) == (raw, [])


@pytest.mark.parametrize("tool,key", [
    ("read_chunk", "chunk"), ("search_document", "hits"),
    ("read_section", "section"), ("read_subsection", "subsection"),
])
@pytest.mark.parametrize("code", ["invalid_result_cursor"])
def test_catalog_keeps_paging_and_size_errors_distinct_from_pdf_failure(tool, key, code):
    from agr_ai_curation_runtime.tool_result_bounds import full_tool_results_requested
    from src.lib.agent_studio import catalog_service
    raw = {key: [] if key == "hits" else None, "error_code": code,
           "summary": "Use the continuation instructions", "result_bounds": {"complete": False}}
    # The model view drops an absent (null) record but keeps the paging error intact.
    assert catalog_service._document_model_result(tool, raw) == {
        name: value for name, value in raw.items() if value is not None}
    with full_tool_results_requested():
        assert catalog_service._document_model_result(tool, raw) is raw
