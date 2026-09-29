import hashlib
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.lib.agent_studio import flexible_migration as migration


@pytest.mark.parametrize("variants,merged", [
    (["site_of_sample", "site_of_the_sample"], "site_sample"),
    (["color_and_shade", "color_or_shade", "Color_Shade"], "color_shade"),
    (["widget_type", "widget_types"], "widget_type"),
    (["label_note", "label_notes"], "label_note"),
])
def test_near_duplicate_keys_merge_to_one_suggestion(variants, merged):
    assert {migration.merge_key(key) for key in variants} == {merged}


def test_attribute_inventory_counts_kinds_classes_keys_and_lists():
    payloads = [
        {"extracted_objects": [
            {"object_type": "generic_object",
             "payload": {"semantic_class": "widget",
                         "attributes": {"widget_type": "round", "color": ["red"]}}},
            {"object_type": "generic_claim", "payload": {"claim_text": "..."}},
        ]},
        {"extracted_objects": [
            {"object_type": "generic_object",
             "payload": {"semantic_class": "widget",
                         "attributes": {"widget_types": ["square"], "color": "blue"}}},
        ]},
        {"not": "an envelope"},
    ]
    tally = migration.attribute_inventory(payloads)
    assert tally["results"] == 3
    assert tally["object_types"] == {"generic_object": 2, "generic_claim": 1}
    assert tally["semantic_classes"] == {"widget": 2}
    assert tally["attribute_keys"] == [
        {"key": "color", "objects": 2, "list_values": 1},
        {"key": "widget_type", "objects": 1, "list_values": 0},
        {"key": "widget_types", "objects": 1, "list_values": 1},
    ]


def test_draft_plan_suggests_merges_and_needs_review_before_it_validates():
    tally = migration.attribute_inventory([{"extracted_objects": [
        {"object_type": "generic_object", "payload": {"semantic_class": "widget",
                                                      "attributes": {"widget_type": "x"}}},
        {"object_type": "generic_object", "payload": {"semantic_class": "widget",
                                                      "attributes": {"widget_types": ["y"]}}},
    ]}])
    agent = NS(id=uuid4(), execution_revision_id=uuid4(), instructions="Find widgets.")
    steps = [{"flow_id": str(uuid4()), "node_id": "node_0"}]
    draft = migration.draft_plan(agent, tally, steps)
    assert draft["key_merges"] == {"widget_types": "widget_type"}
    assert draft["profile"]["semantic_class"] == "widget"
    assert draft["profile"]["fields"][0]["source_labels"] == ["widget_types"]
    assert draft["steps"] == steps and draft["custom_prompt"] == "Find widgets."
    # The profile name and the owner review are left for people to fill in.
    with pytest.raises(ValidationError):
        migration.ConversionPlan.model_validate(draft)
    # Once filled in, the draft's merges agree with its fields' source labels.
    draft["profile"]["name"] = "Widgets"
    draft["owner_review"] = "Reviewed with the agent's owner."
    assert migration.ConversionPlan.model_validate(draft).key_merges == {"widget_types": "widget_type"}


def _plan(**changes):
    plan = {
        "schema_version": migration.PLAN_SCHEMA, "agent_id": str(uuid4()),
        "expected_head_revision_id": str(uuid4()), "active_group_ids": [],
        "profile": {"name": "Widgets", "semantic_class": "widget", "fields": [
            {"key": "widget_type", "source_labels": ["widget_types"],
             "value_schema": {"kind": "string"}},
            {"key": "color", "value_schema": {"kind": "string"}}]},
        "custom_prompt": "Find widgets.", "key_merges": {"widget_types": "widget_type"},
        "steps": [{"flow_id": str(uuid4()), "node_id": "node_0"}],
        "owner_review": "Reviewed with the agent's owner.",
    }
    plan.update(changes)
    return plan


@pytest.mark.parametrize("merges", [
    {"colour": "color"},            # not a source label of its target
    {"widget_types": "shape"},      # the target is not a profile field
    {"widget_types": "color"},      # a source label of another field
])
def test_a_plan_refuses_key_merges_its_fields_do_not_declare(merges):
    with pytest.raises(ValidationError, match="key_merges"):
        migration.ConversionPlan.model_validate(_plan(key_merges=merges))


def test_a_reviewed_plan_loads_only_with_its_sha256_digest():
    raw = migration.ConversionPlan.model_validate(_plan()).model_dump_json(indent=2).encode()
    digest = hashlib.sha256(raw).hexdigest()
    assert migration.load_reviewed_plan(raw, digest).custom_prompt == "Find widgets."
    with pytest.raises(ValueError, match="does not match the reviewed digest"):
        migration.load_reviewed_plan(raw + b"\n", digest)
