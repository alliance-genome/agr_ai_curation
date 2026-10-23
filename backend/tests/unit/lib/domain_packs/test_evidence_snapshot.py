"""Evidence authority is structural, exact, and confined to the supplied snapshot."""
from copy import deepcopy

import pytest

from src.lib.domain_packs.evidence_snapshot import EvidenceSnapshot
from src.lib.flows.output_projection import _attach_source_linked_evidence


@pytest.fixture
def source():
    return {"evidence_record_id": "E1", "verified_quote": "Exact first.\n\nExact second.",
            "page": 3, "section": None, "document_id": "D1",
            "source_span_ids": ["s2", "s1"],
            "source_fragments": [{"text": "Exact first."}, {"text": "Exact second."}]}


def test_rich_source_and_projection_resolve_without_mutation(source):
    subset = {k: source[k] for k in ("evidence_record_id", "verified_quote", "page")}
    before = deepcopy(source)
    items = [{"evidence_record_ids": ["E1"], "payload": {}}, {"payload": subset}]
    rows = [{}, {}]
    _attach_source_linked_evidence(rows, items, {"metadata": {"evidence_records": [source]}})
    assert rows[0]["object.supporting_evidence"] == [source]
    assert rows[0]["object.evidence_link_issues"] == []
    rows[0]["object.supporting_evidence"][0]["source_span_ids"].reverse()
    assert source == before


@pytest.mark.parametrize("field,value", [("verified_quote", "Other"), ("page", 8),
                                         ("source_span_ids", ["s1", "s2"]), ("document_id", "D2")])
def test_known_source_conflicts_never_merged(source, field, value):
    changed = {**source, field: value}
    assert EvidenceSnapshot([source, changed]).resolve("E1") == (None, "ambiguous")


def test_no_cross_snapshot_or_text_join(source):
    assert EvidenceSnapshot([]).resolve("E1") == (None, "missing")
    assert EvidenceSnapshot([{**source, "evidence_record_id": "E2"}]).resolve("E1") == (None, "missing")
    assert EvidenceSnapshot([{k: v for k, v in source.items() if k != "evidence_record_id"}]).resolve("E1") == (None, "missing")


def test_disjoint_partial_sources_do_not_invent_merged_record():
    index = EvidenceSnapshot([{"evidence_record_id": "E1", "page": 3},
                              {"evidence_record_id": "E1", "verified_quote": "Q"}])
    assert index.resolve("E1") == (None, "ambiguous")


@pytest.mark.parametrize("field,first,second", [
    ("page_number", 3, 8),
    ("anchor", {"page_number": 3}, {"page_number": 8}),
    ("pdf_document_id", "D1", "D2"),
    ("source_chunk_id", "chunk-1", "chunk-2"),
    ("source_url", "https://example.org/1", "https://example.org/2"),
    ("section_title", "Results", "Discussion"),
    ("evidence_quote", "First", "Different"),
])
def test_all_consumed_source_locations_participate_in_conflict_detection(source, field, first, second):
    left, right = {**source, field: first}, {**source, field: second}
    index = EvidenceSnapshot([left, right])
    assert index.resolve("E1") == (None, "ambiguous")
    # Faithful subset views still choose the complete saved source unchanged.
    assert EvidenceSnapshot([source, left]).resolve("E1") == (left, None)
