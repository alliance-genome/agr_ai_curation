"""No text/index matching: all scientific item relationships are model-authored."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.lib.validation_history import store
from src.lib.validation_review.item_relationships import validate
from src.schemas.validation_followup import ProposedSourceValue
from src.lib.validation_review.source_projection import plan


@pytest.fixture
def source():
    return {"payload": {"genes": ["same mention", "same mention"], "other": ["unrelated"]},
        "metadata": {"validation_history": {"target_id": "annotation", "fields": {
            "genes": "genes-list", "other": "other-list"}, "elements": {
            "genes": ["original-A", "original-B"], "other": ["unrelated"]}}}}


def relation(key, predecessors, kind="revise"):
    return {"item_key": key, "predecessor_ids": predecessors, "relationship": kind,
            "reason": "Scientific relationship proposed by the extractor"}


def proposal(values, items, retired=(), path="genes", collection="genes-list"):
    return ProposedSourceValue(field_path=path, value=values, evidence_record_ids=[],
        collection_mappings=[{"collection_path": path, "source_collection_id": collection,
            "items": items, "retired_item_ids": list(retired),
            "retirement_reason": "No longer supported" if retired else ""}])


def test_merge_preserves_two_explicit_predecessors_without_mutating_original(source):
    before = deepcopy(source)
    result = validate(source, proposal(["one clarified mention"], [
        relation("merged", ["original-A", "original-B"], "merge")]))
    assert result["genes"]["items"][0]["predecessor_ids"] == ["original-A", "original-B"]
    assert source == before


def test_split_retains_original_predecessor_for_each_successor(source):
    result = validate(source, proposal(["clarification 1", "clarification 2", "same mention"], [
        relation("split-1", ["original-A"], "split"),
        relation("split-2", ["original-A"], "split"),
        relation("untouched", ["original-B"], "retain")]))
    assert [item["predecessor_ids"] for item in result["genes"]["items"]] == [
        ["original-A"], ["original-A"], ["original-B"]]


def test_identical_text_does_not_override_explicit_reorder(source):
    result = validate(source, proposal(["same mention", "same mention"], [
        relation("B-first", ["original-B"], "retain"),
        relation("A-second", ["original-A"], "retain")]))
    assert result["genes"]["items"][0]["predecessor_ids"] == ["original-B"]


def test_added_item_and_explicit_retirement(source):
    result = validate(source, proposal(["new context", "same mention"], [
        relation("new", [], "add"), relation("still-present", ["original-B"], "retain")],
        retired=["original-A"]))
    assert result["genes"]["retired_item_ids"] == ["original-A"]
    assert result["genes"]["items"][0]["predecessor_ids"] == []


@pytest.mark.parametrize("items,retired", [
    ([relation("missing-B", ["original-A"])], []),
    ([relation("foreign", ["unrelated"])], ["original-A", "original-B"]),
    ([relation("unmarked-split-1", ["original-A"]), relation("unmarked-split-2", ["original-A"])], ["original-B"]),
    ([relation("not-a-split", ["original-A"], "split")], ["original-B"]),
])
def test_missing_foreign_or_ambiguous_coverage_is_rejected(source, items, retired):
    with pytest.raises(store.HistoryConflict):
        validate(source, proposal(["changed"] * len(items), items, retired))


def test_retained_item_cannot_silently_change_value(source):
    with pytest.raises(store.HistoryConflict, match="retained item"):
        validate(source, proposal(["different", "same mention"], [
            relation("A", ["original-A"], "retain"), relation("B", ["original-B"], "retain")]))


def test_scalar_replacement_of_list_requires_explicit_retirement(source):
    value = ProposedSourceValue(field_path="genes", value="no supported mentions", evidence_record_ids=[])
    with pytest.raises(store.HistoryConflict, match="explicit predecessor/retirement"):
        validate(source, value)
    replacement = proposal("no supported mentions", [], retired=["original-A", "original-B"])
    assert validate(source, replacement)["genes"]["retired_item_ids"] == ["original-A", "original-B"]


def test_mapping_cannot_steal_another_collection_in_the_same_annotation(source):
    with pytest.raises(store.HistoryAccessError):
        validate(source, proposal(["unrelated"], [relation("steal", ["unrelated"], "retain")], collection="other-list"))


def test_new_collection_has_explicit_added_items(source):
    result = validate(source, proposal(["Methods context"], [relation("new", [], "add")],
        path="new_information", collection=None))
    assert result["new_information"]["source_collection_id"] is None


def test_mapped_predecessor_cannot_also_be_retired():
    with pytest.raises(ValidationError, match="both mapped and retired"):
        proposal(["x"], [relation("x", ["original-A"])], retired=["original-A"])


def test_materialized_merge_gets_new_identity_and_keeps_exact_ancestry(source):
    change = proposal(["combined"], [relation("merged", ["original-A", "original-B"], "merge")])
    result = plan(source, [change], contribution_id="proposal-1")
    assert result == plan(source, [change], contribution_id="proposal-1")
    tid = result["entry"]["metadata"]["validation_history"]["elements"]["genes"][0]
    assert tid not in {"original-A", "original-B"}
    assert result["additions"][0]["body"]["origin"]["predecessor_ids"] == ["original-A", "original-B"]
    assert result["retired_ids"] == ["original-A", "original-B"]
    assert source["payload"]["genes"] == ["same mention", "same mention"]


def test_materialized_reorder_preserves_explicit_ids_without_retirement(source):
    result = plan(source, [proposal(["same mention", "same mention"], [
        relation("B", ["original-B"], "retain"), relation("A", ["original-A"], "retain")])],
        contribution_id="proposal-1")
    assert result["entry"]["metadata"]["validation_history"]["elements"]["genes"] == ["original-B", "original-A"]
    assert result["retired_ids"] == []
    assert result["additions"] == []


def test_scalar_change_inside_item_preserves_its_explicit_snapshot_identity():
    source = {"payload": {"genes": [{"name": "old"}]}, "metadata": {"validation_history": {
        "target_id": "annotation", "fields": {"genes": "list", "genes[0].name": "name"},
        "elements": {"genes": ["item"]}}}}
    result = plan(source, [ProposedSourceValue(field_path="genes[0].name", value="new", evidence_record_ids=[])],
                  contribution_id="proposal")
    assert result["entry"]["payload"]["genes"][0]["name"] == "new"
    assert result["entry"]["metadata"] == source["metadata"]
    assert not result["additions"] and not result["retired_ids"]


def test_scalar_path_cannot_append_unmapped_item(source):
    with pytest.raises(store.HistoryConflict, match="Changed collection"):
        plan(source, [ProposedSourceValue(field_path="genes[2]", value="new", evidence_record_ids=[])],
             contribution_id="proposal")


def test_nested_collections_follow_explicit_split_without_losing_ancestry():
    source = {'payload': {'genes': [{'aliases': ['original']} ]}, 'metadata': {'validation_history': {
        'target_id': 'annotation', 'fields': {'genes': 'genes', 'genes[0].aliases': 'aliases'},
        'elements': {'genes': ['gene'], 'genes[0].aliases': ['alias']}}}}
    proposed = ProposedSourceValue(field_path='genes', value=[{'aliases': ['first']}, {'aliases': ['second']}],
        evidence_record_ids=[], collection_mappings=[
            {'collection_path': 'genes', 'source_collection_id': 'genes', 'retired_item_ids': [], 'retirement_reason': '', 'items': [
                relation('gene1', ['gene'], 'split'), relation('gene2', ['gene'], 'split')]},
            {'collection_path': 'genes[0].aliases', 'source_collection_id': 'aliases', 'retired_item_ids': [], 'retirement_reason': '', 'items': [relation('alias1', ['alias'], 'split')]},
            {'collection_path': 'genes[1].aliases', 'source_collection_id': 'aliases', 'retired_item_ids': [], 'retirement_reason': '', 'items': [relation('alias2', ['alias'], 'split')]},
        ])
    result = plan(source, [proposed], contribution_id='contribution')
    refs = result['entry']['metadata']['validation_history']
    assert refs['elements']['genes[0].aliases'] != refs['elements']['genes[1].aliases']
    alias_additions = [item for item in result['additions'] if item['body']['origin']['predecessor_ids'] == ['alias']]
    assert len(alias_additions) == 2
    assert {'gene', 'aliases', 'alias'}.issubset(result['retired_ids'])
    assert source['payload']['genes'] == [{'aliases': ['original']}]
