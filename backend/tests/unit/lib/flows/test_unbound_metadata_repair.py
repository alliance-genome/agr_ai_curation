from copy import deepcopy

import pytest

from src.lib.flows.unbound_metadata_repair import repair_metadata_groups
from src.lib.flows.validation_attachments import _resolved_validation_groups
from src.schemas.flows import FlowValidationAttachmentSelection, FlowValidationAttachmentGroup


def fixture(enabled=False):
    attachment = FlowValidationAttachmentSelection(
        attachment_id="example:metadata:lookup:pack:*:*", domain_pack_id="example",
        validator_id="lookup", state="active", scope="pack", enabled=enabled,
        label="Description", validator_package_id="example", validator_agent_id="lookup",
    )
    group = FlowValidationAttachmentGroup(
        group_id=attachment.attachment_id, attachment_id=attachment.attachment_id,
        state="automatic" if enabled else "skipped", label=attachment.label,
    ).model_dump(mode="json")
    definition = {"nodes": [{"id": "node", "data": {
        "validation_attachments": [attachment.model_dump(mode="json")],
        "validation_groups": [group], "instructions": "Keep this instruction",
    }}], "edges": [{"source": "node", "target": "next"}]}
    return attachment, definition


@pytest.mark.parametrize("enabled", [False, True])
def test_producer_omits_unbound_metadata_even_with_agent_reference(enabled):
    attachment, _ = fixture(enabled)
    bound = attachment.model_copy(update={"attachment_id": "real", "validator_binding_id": "real"})
    groups = _resolved_validation_groups([attachment, bound])
    assert len(groups) == 1
    assert groups[0].binding_id == "real"
    assert groups[0].state == ("automatic" if enabled else "skipped")


@pytest.mark.parametrize("enabled", [False, True])
def test_exact_generated_group_repair_preserves_every_other_field(enabled):
    _, original = fixture(enabled)
    snapshot = deepcopy(original)
    result = repair_metadata_groups(original)
    expected = deepcopy(original)
    expected["nodes"][0]["data"]["validation_groups"] = []
    assert result.definition == expected
    assert len(result.removed) == 1 and not result.unmatched
    assert original == snapshot
    assert not repair_metadata_groups(result.definition).removed


@pytest.mark.parametrize("change", [
    {"label": "Custom choice"}, {"state": "supplemental"}, {"required": True},
    {"validator_node_id": "custom"}, {"edge_id": "edge"}, {"group_id": "other"},
    {"blocking": 0}, {"extra": "do not discard"},
])
def test_unknown_unbound_groups_are_reported_and_preserved(change):
    _, definition = fixture()
    definition["nodes"][0]["data"]["validation_groups"][0].update(change)
    result = repair_metadata_groups(definition)
    assert result.definition == definition
    assert not result.removed and len(result.unmatched) == 1


def test_ambiguous_attachment_provenance_is_not_repaired():
    _, definition = fixture()
    attachments = definition["nodes"][0]["data"]["validation_attachments"]
    attachments.append(deepcopy(attachments[0]))
    result = repair_metadata_groups(definition)
    assert not result.removed and len(result.unmatched) == 1


def test_real_allele_hydration_keeps_metadata_but_dispatches_only_real_bindings(monkeypatch):
    from tests.unit.lib.validation_review.test_selector_serialization import package_registry
    from tests.unit.lib.flows.test_validation_attachments import _flow_definition
    from src.lib.flows import validation_attachments as attachments
    from src.lib.validation_review.flow_plan import configured_registry

    registry = package_registry("allele")
    options = registry.validation_attachment_options()
    assert any(o.state.value == "active" and not o.validator_binding_id for o in options)
    monkeypatch.setattr(attachments, "validation_attachment_options_for_agent",
                        lambda agent_id, **kwargs: options if agent_id == "fixture_extractor" else ())
    flow = attachments.apply_flow_validation_attachment_defaults(_flow_definition("fixture_extractor"))
    data = flow.nodes[1].data
    assert any(not a.validator_binding_id for a in data.validation_attachments)
    groups = [g.model_dump(mode="json") for g in data.validation_groups]
    assert groups and all(g["binding_id"] for g in groups)
    selected = configured_registry(registry, groups, {})
    assert {b.binding_id for b in selected.bindings} == {g["binding_id"] for g in groups}
    schedule = attachments.validation_schedule_from_node_data(data.model_dump(mode="json"))
    assert schedule["inactive_metadata"]


def test_metadata_only_selection_cannot_create_effective_validation_groups():
    from src.lib.domain_packs.flow_validator_selection import effective_flow_validation_groups
    attachment, _ = fixture(True)
    groups = _resolved_validation_groups([attachment])
    assert effective_flow_validation_groups([g.model_dump(mode="json") for g in groups]) == []


@pytest.mark.parametrize("label", ["", None])
def test_empty_attachment_label_matches_old_producer_null(label):
    _, definition = fixture()
    data = definition["nodes"][0]["data"]
    data["validation_attachments"][0]["label"] = label
    data["validation_groups"][0]["label"] = None
    result = repair_metadata_groups(definition)
    assert len(result.removed) == 1 and not result.unmatched
