"""A saved review plan stores its record set once, whatever the number of matches.

Saving every match whole repeated the whole envelope per match, so a 97-record
paper produced a 2.2 GB plan body that PostgreSQL could not buffer.
"""
import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from src.lib.domain_packs.validation_registry import ValidationBindingState
from src.lib.validation_history import store
from src.lib.validation_review import saved_matches
from src.schemas.domain_envelope import DomainEnvelope
from tests.unit.lib.validation_review.test_selector_serialization import package_registry

FIXTURE = (Path(__file__).resolve().parents[5]
           / "packages/alliance/domain_packs/gene_expression/fixtures/tmem67_multi_annotation_pending.yaml")


def envelope_with(records):
    """The packaged gene-expression fixture, repeated to the requested record count."""
    raw = yaml.safe_load(FIXTURE.read_text())["fixtures"][0]["envelope"]
    templates = raw["extracted_objects"]
    objects = []
    for index in range(records):
        obj = deepcopy(templates[index % len(templates)])
        obj["pending_ref_id"] = f"{obj['pending_ref_id']}-{index}"
        obj["payload"]["unique_id"] = f"{obj['payload']['unique_id']}|{index}"
        objects.append(obj)
    raw["extracted_objects"] = objects
    raw["validation_findings"] = []
    return DomainEnvelope.model_validate(raw)


REGISTRY = package_registry("gene_expression")


def plan_matches(records):
    envelope = envelope_with(records)
    matches = REGISTRY.match_bindings(envelope, states=[ValidationBindingState.ACTIVE])
    return envelope, {f"target-{index}": match for index, match in enumerate(matches)}


def previous_reader(match):
    """What the pre-change plan saved for one match (serialize_match) and read back."""
    value = saved_matches.MATCH.dump_python(match, mode="json")
    value["binding"] = saved_matches.serialize_binding(match.binding)
    return saved_matches.MATCH.validate_python(store.canonical(value)[1])


def assert_reads_as_before(matches, plan_envelope, registry=REGISTRY):
    saved = store.canonical(saved_matches.encode(matches, plan_envelope=plan_envelope))[1]
    restored = saved_matches.decode({**saved, "envelope": plan_envelope}, registry=registry)
    assert restored.keys() == matches.keys()
    for target, match in matches.items():
        before = previous_reader(match)
        assert restored[target] == before
        assert type(restored[target]) is type(before)
        for name in ("binding", "object_definition", "field_definition", "policy"):
            assert type(getattr(restored[target], name)) is type(getattr(before, name))
        assert (restored[target].object_envelope.model_dump(mode="json")
                if restored[target].object_envelope is not None else None) == (
                    before.object_envelope.model_dump(mode="json")
                    if before.object_envelope is not None else None)
        assert (saved_matches.MATCH.dump_python(restored[target], mode="json")
                == saved_matches.MATCH.dump_python(before, mode="json"))
    return restored


def body_size(body):
    return len(json.dumps(body, sort_keys=True, separators=(",", ":")))


def test_saved_matches_read_back_equal_to_the_originals():
    envelope, matches = plan_matches(6)
    assert len(matches) > 6, "every record has more than one configured match"
    plan_envelope = envelope.model_dump(mode="json")
    saved = saved_matches.encode(matches, plan_envelope=plan_envelope)
    # A database round trip returns canonical JSON.
    saved = store.canonical(saved)[1]

    restored = saved_matches.decode({**saved, "envelope": plan_envelope}, registry=REGISTRY)

    assert restored == matches
    for target, match in matches.items():
        assert (saved_matches.MATCH.dump_python(restored[target], mode="json")
                == saved_matches.MATCH.dump_python(match, mode="json"))
    # Every rebuilt match shares one parsed envelope instead of a copy each.
    assert len({id(match.envelope) for match in restored.values()}) == 1
    first = next(iter(restored.values()))
    assert all(match.object_envelope is None
               or any(match.object_envelope is obj for obj in first.envelope.extracted_objects)
               for match in restored.values())


def test_a_many_object_many_binding_plan_reads_back_as_the_previous_reader_did():
    envelope, matches = plan_matches(6)
    assert len({match.binding.binding_id for match in matches.values()}) > 1
    assert len({id(match.object_envelope) for match in matches.values()}) > 1
    assert_reads_as_before(matches, envelope.model_dump(mode="json"))


def test_element_indexed_matches_read_back_as_the_previous_reader_did(tmp_path):
    from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
    from src.schemas.domain_envelope import CuratableObjectEnvelope
    from tests.unit.lib.domain_packs.test_validator_dispatch import _multivalued_dispatch_pack

    pack = _multivalued_dispatch_pack(tmp_path)
    envelope = DomainEnvelope(envelope_id="fanout", domain_pack_id=pack.pack_id, extracted_objects=[
        CuratableObjectEnvelope(object_type="Annotation", pending_ref_id=f"record-{index}",
                                payload={"evidence_code_curies": [f"eco:{index}a", f"eco:{index}b"]})
        for index in range(3)])
    found = DomainPackValidationRegistry.from_domain_pack(pack).match_bindings(
        envelope, states=[ValidationBindingState.ACTIVE])
    matches = {f"target-{index}": match for index, match in enumerate(found)}
    assert sum(match.element_index is not None for match in matches.values()) == 6

    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    restored = assert_reads_as_before(matches, envelope.model_dump(mode="json"), registry)
    assert {match.field_path for match in restored.values()} == {
        "evidence_code_curies[0]", "evidence_code_curies[1]"}


def test_a_match_envelope_that_differs_from_the_plan_envelope_is_stored_once():
    # The chat plan's envelope carries episode metadata the dispatch source lacks.
    envelope, matches = plan_matches(4)
    plan_envelope = envelope.model_copy(update={"metadata": {**envelope.metadata, "review_episode_id": "ep"}})
    plan_envelope = plan_envelope.model_dump(mode="json")

    saved = saved_matches.encode(matches, plan_envelope=plan_envelope)

    assert len(saved["match_parts"]["envelopes"]) == 1
    restored = saved_matches.decode({**saved, "envelope": plan_envelope}, registry=REGISTRY)
    assert restored == matches
    assert all(match.envelope.metadata.get("review_episode_id") is None for match in restored.values())


def test_a_match_on_the_plan_envelope_names_it_instead_of_copying_it():
    envelope, matches = plan_matches(4)
    saved = saved_matches.encode(matches, plan_envelope=envelope.model_dump(mode="json"))

    assert saved["match_parts"]["envelopes"] == {}
    assert {item["envelope"] for item in saved["matches"].values()} == {saved_matches.PLAN_ENVELOPE}


@pytest.mark.parametrize("records", [50, 100])
def test_each_shared_part_is_stored_once(records):
    envelope, matches = plan_matches(records)
    saved = saved_matches.encode(matches, plan_envelope=envelope.model_dump(mode="json"))
    parts = saved["match_parts"]

    object_types = {match.object_definition.object_type for match in matches.values()
                    if match.object_definition is not None}
    assert len(parts["object_definitions"]) == len(object_types)
    assert len(parts["bindings"]) <= len({match.binding.binding_id for match in matches.values()}) * 4
    # No saved match carries a record: only part keys, a position and indexes.
    assert max(body_size(item) for item in saved["matches"].values()) < 1024


def test_plan_size_grows_linearly_with_record_count():
    sizes = {}
    for records in (50, 100):
        envelope, matches = plan_matches(records)
        plan_envelope = envelope.model_dump(mode="json")
        sizes[records] = body_size({"envelope": plan_envelope,
                                    **saved_matches.encode(matches, plan_envelope=plan_envelope)})
    # Doubling the records about doubles the body; repeating the envelope per
    # match would quadruple it.
    assert sizes[100] / sizes[50] < 2.2


def test_a_match_object_outside_its_envelope_is_refused():
    from dataclasses import replace

    envelope, matches = plan_matches(2)
    target, match = next((key, value) for key, value in matches.items() if value.object_envelope is not None)
    copied = replace(match, object_envelope=match.object_envelope.model_copy(deep=True))

    with pytest.raises(store.HistoryConflict, match="not one object of its envelope"):
        saved_matches.encode({target: copied}, plan_envelope=envelope.model_dump(mode="json"))


MAIN_MATCH_KEYS = {"envelope", "object_index", "binding", "object_definition", "field_definition",
                   "policy", "element_index", "resolved_field_path", "element_index_path"}
MAIN_PART_KINDS = {"envelopes", "bindings", "object_definitions", "field_definitions", "policies"}


def test_a_plan_saves_nothing_beyond_mains_format_and_reads_each_records_owners_back():
    # Which of a record's quotes each check receives depends on the field paths
    # every active check on that record reads (ALL-1584). They are derived from
    # the plan's registry on reading, so a plan saved before this change reads
    # the same way as one saved after it.
    envelope, matches = plan_matches(6)
    assert any(match.record_binding_paths for match in matches.values())
    plan_envelope = envelope.model_dump(mode="json")
    saved = store.canonical(saved_matches.encode(matches, plan_envelope=plan_envelope))[1]
    assert set(saved["match_parts"]) == MAIN_PART_KINDS
    assert all(set(item) == MAIN_MATCH_KEYS for item in saved["matches"].values())

    restored = saved_matches.decode({**saved, "envelope": plan_envelope}, registry=REGISTRY)
    for target, match in matches.items():
        assert restored[target].record_binding_paths == match.record_binding_paths



def test_owners_derived_from_a_saved_flow_registry_equal_the_dispatch_owners():
    """No spurious input change: a rebuild from the saved plan assigns evidence as the dispatch did."""
    from dataclasses import replace

    from src.lib.validation_review.validator_adapter import REGISTRY as SAVED, saved_registry

    envelope, _ = plan_matches(6)
    # A flow that runs only some checks saves a narrowed registry.
    flow = replace(REGISTRY, bindings=REGISTRY.bindings[::2])
    restored = SAVED.validate_python(store.canonical(saved_registry(flow))[1])
    dispatch = REGISTRY.record_binding_paths(envelope)
    assert any(dispatch.values())
    assert restored.record_binding_paths(envelope) == dispatch
    assert flow.record_binding_paths(envelope) == dispatch
