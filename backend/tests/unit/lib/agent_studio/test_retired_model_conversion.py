from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from src.lib.agent_studio import retired_model_conversion as conversion
from src.lib.flows.export_fields import catalog_fingerprint, source_catalog

MAP = {"xhigh": "high"}


@pytest.mark.parametrize("saved,expected", [
    ("xhigh", "high"), (" XHigh ", "high"), ("high", "high"), ("medium", "medium"),
    ("low", "low"), (None, None),
])
def test_reasoning_follows_the_migration_mapping(saved, expected):
    assert conversion.mapped_reasoning(saved, MAP) == expected


def test_catalog_fingerprint_is_the_source_catalog_identity():
    fields = [{"ref": "object.profile.widget_type", "label": "Widget type"}]
    catalog = source_catalog(fields, {"agent_revision_id": "r1"})
    assert catalog["schema_fingerprint"] == catalog_fingerprint(catalog["fields"], {"agent_revision_id": "r1"})
    assert catalog["schema_fingerprint"] != catalog_fingerprint(catalog["fields"], {"agent_revision_id": "r2"})


FIELDS = [{"ref": "object.profile.widget_type"}]
OLD, NEW = {"agent_revision_id": "old"}, {"agent_revision_id": "new"}


def _output_node(fingerprint, source="node_0", mode="selected_fields"):
    plan = {"selection_mode": mode, "selected_sources": [{"node_id": source, "schema_fingerprint": fingerprint}]}
    return NS(id="output", data=NS(projection_plan=plan))


def test_a_current_layout_follows_its_repinned_source():
    new_catalog = source_catalog(FIELDS, NEW)
    node = _output_node(source_catalog(FIELDS, OLD)["schema_fingerprint"])
    moved = conversion._move_layouts(NS(nodes=[node]), {"node_0": new_catalog}, {"node_0": OLD})
    assert moved == [{"output_node_id": "output", "source_node_id": "node_0",
                      "from": source_catalog(FIELDS, OLD)["schema_fingerprint"],
                      "to": new_catalog["schema_fingerprint"]}]
    assert node.data.projection_plan["selected_sources"][0]["schema_fingerprint"] == new_catalog["schema_fingerprint"]


def test_a_layout_that_was_already_out_of_date_is_refused():
    node = _output_node(source_catalog([{"ref": "object.profile.other"}], OLD)["schema_fingerprint"])
    with pytest.raises(ValueError, match="choose the output fields again"):
        conversion._move_layouts(NS(nodes=[node]), {"node_0": source_catalog(FIELDS, NEW)}, {"node_0": OLD})


def test_layouts_of_other_sources_and_modes_are_left_alone():
    other = _output_node("sha256:x", source="node_9")
    agent_written = _output_node("sha256:y", mode="agent")
    assert conversion._move_layouts(NS(nodes=[other, agent_written]), {}, {"node_0": OLD}) == []
    assert other.data.projection_plan["selected_sources"][0]["schema_fingerprint"] == "sha256:x"


def _flow(revision="old", receipt=None, instructions="Extract"):
    return {"entry_node_id": "task", "edges": [{"id": "e0", "source": "task", "target": "node_0"}], "nodes": [
        {"id": "task", "type": "task_input", "position": {"x": 0, "y": 0},
         "data": {"agent_id": "task_input", "agent_display_name": "Task", "output_key": "task",
                  "task_instructions": instructions}},
        {"id": "node_0", "type": "agent", "position": {"x": 1, "y": 1},
         "data": {"agent_id": "ca_x", "agent_display_name": "X", "output_key": "result_0",
                  "agent_revision_id": revision, "execution_receipt": receipt}},
    ]}


def test_only_the_moved_pin_may_change(monkeypatch):
    # Receipt validation is the resolver's job; compare plain data here.
    monkeypatch.setattr(conversion, "_canonical", deepcopy)
    after = _flow("new", {"agent_revision_id": "new"})
    conversion._require_only_pins_changed(_flow(), after, {"node_0": "new"}, [])
    with pytest.raises(ValueError, match="not re-pinned"):
        conversion._require_only_pins_changed(_flow(), _flow("new", {"agent_revision_id": "old"}),
                                              {"node_0": "new"}, [])
    with pytest.raises(ValueError, match="step task beyond its pinned revision"):
        conversion._require_only_pins_changed(_flow(), _flow("new", {"agent_revision_id": "new"}, "Other"),
                                              {"node_0": "new"}, [])
