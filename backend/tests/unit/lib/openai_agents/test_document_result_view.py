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
              "doc_items": [{"page": 1}], "source_chunks": [{"chunk_id": "c1", "chunk_index": 42}]}
    raw = {key: [record] if key == "hits" else record}
    view, events = document_result_view(tool, raw)
    actual = view[key][0] if key == "hits" else view[key]
    assert actual["content"] == record["content"]
    assert actual["source_chunks"] == [{"chunk_index": 42}]
    assert actual["next_offset"] == 4
    assert "doc_items" not in actual
    assert events[0]["chunk_id"] == "c1"


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
@pytest.mark.parametrize("code", ["invalid_result_cursor", "tool_result_budget_unmet"])
def test_catalog_keeps_paging_and_size_errors_distinct_from_pdf_failure(tool, key, code):
    from agr_ai_curation_runtime.tool_result_bounds import full_tool_results_requested
    from src.lib.agent_studio import catalog_service
    raw = {key: [] if key == "hits" else None, "error_code": code,
           "summary": "Use the continuation instructions", "result_bounds": {"complete": False}}
    assert catalog_service._document_model_result(tool, raw) == raw
    with full_tool_results_requested():
        assert catalog_service._document_model_result(tool, raw) is raw
