from copy import deepcopy

import pytest

from src.lib.openai_agents.document_result_view import document_result_view


def test_window_shows_numbered_spans_and_routes_geometry_without_mutating_source():
    text = "α😀 was not expressed.\nSecond sentence."
    split = text.index("\n")
    start = 40
    raw = {"chunk": {"chunk_id": "chunk-1", "chunk_index": 3, "content": text,
                     "content_range": {"start": start, "end": start + len(text), "total_chars": 200},
                     "span_page": {"span_offset": 9, "total_spans": 11},
                     "doc_items": [{"page": 2, "bbox": [1, 2, 3, 4]}],
                     "evidence_spans": [
                         {"span_id": "s9", "span_index": 9, "text": text[:split], "char_start": start,
                          "char_end": start + split},
                         {"span_id": "s10", "span_index": 10, "text": text[split + 1:], "char_start": start + split + 1,
                          "char_end": start + len(text)}]}}
    original = deepcopy(raw)
    view, events = document_result_view("read_chunk", raw)
    chunk = view["chunk"]
    assert chunk["text"] == "[9] α😀 was not expressed.\n[10] Second sentence."
    assert not {"content", "evidence_spans", "content_range", "doc_items", "chunk_id"} & set(chunk)
    assert chunk["span_page"] == raw["chunk"]["span_page"]
    assert events[0]["doc_items"] == raw["chunk"]["doc_items"]
    assert events[0]["chunk_id"] == "chunk-1"
    assert raw == original


def test_search_view_keeps_hit_content_and_routes_geometry():
    record = {"chunk_id": "c1", "chunk_index": 42, "content": "not found in adults",
              "doc_items": [{"page": 1}], "section_title": None}
    view, events = document_result_view("search_document", {"hits": [record], "error_code": None})
    assert view == {"hits": [{"chunk_index": 42, "content": "not found in adults"}]}
    assert events[0]["chunk_id"] == "c1"


@pytest.mark.parametrize("tool,key", [("read_section", "section"), ("read_subsection", "subsection")])
def test_section_view_numbers_every_passage_and_keeps_identity_out_of_the_model_view(tool, key):
    first, second = "Wg was absent. It returned later.", "No change."
    raw = {"summary": "Read all 2 chunks.", "error_code": None, key: {
        "requested_section": "Results", "requested_subsection": None,
        "matched_sections": ["Results"], "matched_subsections": None,
        "page_numbers": [2, 3], "total_chunk_count": 2, "text_contains": None,
        "matched_chunk_count": None, "passages": [
            {"chunk_id": "uuid-a", "chunk_index": 5, "page_number": 2, "section": "Results",
             "subsection": None, "content": first, "doc_items": [{"page": 2}],
             "evidence_spans": [
                 {"span_id": "uuid-a:s0", "span_index": 0, "text": first[:14], "char_start": 0, "char_end": 14},
                 {"span_id": "uuid-a:s1", "span_index": 1, "text": first[15:], "char_start": 15, "char_end": len(first)}]},
            {"chunk_id": "uuid-b", "chunk_index": 6, "page_number": 3, "section": "Results",
             "subsection": None, "content": second, "doc_items": [{"page": 3}],
             "evidence_spans": [
                 {"span_id": "uuid-b:s0", "span_index": 0, "text": second, "char_start": 0, "char_end": len(second)}]},
        ]}}
    original = deepcopy(raw)
    view, events = document_result_view(tool, raw)
    assert view == {"summary": "Read all 2 chunks.", key: {
        "requested_section": "Results", "matched_sections": ["Results"], "page_numbers": [2, 3],
        "total_chunk_count": 2, "passages": [
            {"chunk_index": 5, "page_number": 2, "section": "Results",
             "text": "[0] Wg was absent.\n[1] It returned later."},
            {"chunk_index": 6, "page_number": 3, "section": "Results", "text": "[0] No change."},
        ]}}
    assert events == [{"type": "CHUNK_PROVENANCE", "message_id": events[0]["message_id"],
                       "chunk_id": "section:Results", "doc_items": [{"page": 2}, {"page": 3}],
                       "source_tool": tool}]
    assert raw == original


def test_invalid_offsets_fail_explicitly_instead_of_changing_evidence():
    with pytest.raises(RuntimeError, match="required PDF") as caught:
        document_result_view("read_chunk", {"chunk": {"content": "negative", "evidence_spans": [
            {"span_id": "s1", "text": "positive", "char_start": 0, "char_end": 8}]}})
    assert isinstance(caught.value.__cause__, ValueError)
    assert "Evidence span" in str(caught.value.__cause__)


def test_continuation_lines_of_a_sentence_are_indented_so_only_real_numbers_start_a_line():
    from agr_ai_curation_runtime.evidence_spans import build_evidence_spans

    text = "Primers\n[1] a\n[2] b"
    spans = [span.to_dict() for span in build_evidence_spans(
        chunk_id="c", chunk_text=text, page_number=None, section_title=None)]
    assert [span["span_index"] for span in spans] == [0]
    view, _ = document_result_view("read_chunk", {"chunk": {
        "chunk_id": "c", "chunk_index": 1, "content": text, "evidence_spans": spans}})
    assert view["chunk"]["text"] == "[0] Primers\n    [1] a\n    [2] b"
    assert [line for line in view["chunk"]["text"].split("\n") if line.startswith("[")] == ["[0] Primers"]


def test_large_chunk_has_one_copy_of_scientific_text_in_model_view():
    text = "This reagent was not used in the experiment. " * 200
    raw = {"chunk": {"chunk_id": "c", "content": text, "evidence_spans": [
        {"span_id": "s", "span_index": 0, "text": text.strip(), "char_start": 0, "char_end": len(text) - 1}]}}
    import json
    view, _ = document_result_view("read_chunk", raw)
    assert len(json.dumps(view)) < len(json.dumps(raw)) * 0.6
    assert view["chunk"]["text"] == "[0] " + text.strip()


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


@pytest.mark.parametrize("raw", [{"chunk": None}, "An error occurred while running the tool. Please try again."])
def test_required_chunk_failure_is_terminal_not_a_model_result(raw):
    with pytest.raises(RuntimeError, match="required PDF"):
        document_result_view("read_chunk", raw)


def test_no_search_matches_is_a_valid_result():
    raw = {"hits": [], "summary": "No matches"}
    assert document_result_view("search_document", raw) == (raw, [])


@pytest.mark.parametrize("tool,key", [("read_chunk", "chunk"), ("search_document", "hits")])
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
