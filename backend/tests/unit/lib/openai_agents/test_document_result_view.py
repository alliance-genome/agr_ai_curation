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
              "doc_items": [{"page": 1}], "source_chunks": [{"chunk_id": "c1"}]}
    raw = {key: [record] if key == "hits" else record}
    view, events = document_result_view(tool, raw)
    actual = view[key][0] if key == "hits" else view[key]
    assert actual["content"] == record["content"]
    assert actual["source_chunks"] == record["source_chunks"]
    assert actual["next_offset"] == 4
    assert "doc_items" not in actual
    assert events[0]["chunk_id"] == "c1"


def test_invalid_offsets_fail_explicitly_instead_of_changing_evidence():
    with pytest.raises(ValueError, match="Evidence span"):
        document_result_view("read_chunk", {"chunk": {"content": "negative", "evidence_spans": [
            {"span_id": "s1", "text": "positive", "char_start": 0, "char_end": 8}]}})


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
