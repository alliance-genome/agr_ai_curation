from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.lib.agent_studio import flexible_migration as migration


@pytest.mark.parametrize("variants,merged", [
    (["section_of_paper", "section_of_the_paper"], "section_paper"),
    (["organ_cell_type_of_origin", "organ_or_cell_type_of_origin", "organ_cell_type_origin"],
     "organ_cell_type_origin"),
    (["model_type", "model_types"], "model_type"),
    (["extracted_phrase", "extracted_phrases"], "extracted_phrase"),
])
def test_near_duplicate_keys_merge_to_one_suggestion(variants, merged):
    assert {migration.merge_key(key) for key in variants} == {merged}


def test_attribute_inventory_counts_kinds_classes_keys_and_lists():
    payloads = [
        {"extracted_objects": [
            {"object_type": "generic_object",
             "payload": {"semantic_class": "tumour model",
                         "attributes": {"model_type": "xenograft", "species": ["mouse"]}}},
            {"object_type": "generic_claim", "payload": {"claim_text": "..."}},
        ]},
        {"extracted_objects": [
            {"object_type": "generic_object",
             "payload": {"semantic_class": "tumour model",
                         "attributes": {"model_types": ["GEMM"], "species": "mouse"}}},
        ]},
        {"not": "an envelope"},
    ]
    tally = migration.attribute_inventory(payloads)
    assert tally["results"] == 3
    assert tally["object_types"] == {"generic_object": 2, "generic_claim": 1}
    assert tally["semantic_classes"] == {"tumour model": 2}
    assert tally["attribute_keys"] == [
        {"key": "species", "objects": 2, "list_values": 1},
        {"key": "model_type", "objects": 1, "list_values": 0},
        {"key": "model_types", "objects": 1, "list_values": 1},
    ]


def test_draft_plan_suggests_merges_and_needs_review_before_it_validates():
    tally = migration.attribute_inventory([{"extracted_objects": [
        {"object_type": "generic_object", "payload": {"semantic_class": "tumour model",
                                                      "attributes": {"model_type": "x"}}},
        {"object_type": "generic_object", "payload": {"semantic_class": "tumour model",
                                                      "attributes": {"model_types": ["y"]}}},
    ]}])
    agent = NS(id=uuid4(), execution_revision_id=uuid4(), instructions="Find tumour models.")
    steps = [{"flow_id": str(uuid4()), "node_id": "node_0"}]
    draft = migration.draft_plan(agent, tally, steps)
    assert draft["key_merges"] == {"model_types": "model_type"}
    assert draft["profile"]["semantic_class"] == "tumour model"
    assert draft["profile"]["fields"][0]["source_labels"] == ["model_types"]
    assert draft["steps"] == steps and draft["custom_prompt"] == "Find tumour models."
    # The profile name and the owner review are left for people to fill in.
    with pytest.raises(ValidationError):
        migration.ConversionPlan.model_validate(draft)


def _reviewed_plan_bytes():
    return migration.ConversionPlan.model_validate({
        "schema_version": migration.PLAN_SCHEMA, "agent_id": str(uuid4()),
        "expected_head_revision_id": str(uuid4()), "active_group_ids": [],
        "profile": {"name": "Models", "semantic_class": "model", "fields": [
            {"key": "model_type", "value_schema": {"kind": "string"}}]},
        "custom_prompt": "Find models.", "steps": [{"flow_id": str(uuid4()), "node_id": "node_0"}],
        "owner_review": "Reviewed with the agent's owner.",
    }).model_dump_json(indent=2).encode()


def test_a_reviewed_plan_loads_only_with_its_sha256_digest():
    import hashlib

    raw = _reviewed_plan_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert migration.load_reviewed_plan(raw, digest).custom_prompt == "Find models."
    with pytest.raises(ValueError, match="does not match the reviewed digest"):
        migration.load_reviewed_plan(raw + b"\n", digest)
