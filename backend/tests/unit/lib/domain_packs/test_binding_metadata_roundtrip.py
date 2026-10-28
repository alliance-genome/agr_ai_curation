"""Authored bindings retain shape and scientific literal data across persistence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.schemas.domain_pack_metadata import DomainPackValidatorBindings


def _buckets(value):
    if isinstance(value, dict):
        if "validator_bindings" in value:
            yield value["validator_bindings"]
        for child in value.values():
            yield from _buckets(child)
    elif isinstance(value, list):
        for child in value:
            yield from _buckets(child)


def _packs():
    return Path(__file__).resolve().parents[5] / "packages/alliance/domain_packs"


def test_all_shipped_binding_shapes_roundtrip_and_direct_contracts_unchanged():
    expected = json.loads((Path(__file__).parent / "fixtures/direct_binding_contract_hashes.json").read_text())
    actual = {}
    routed_count = 0
    for path in _packs().glob("*/domain_pack.yaml"):
        for raw in _buckets(yaml.safe_load(path.read_text())):
            normalized = DomainPackValidatorBindings.model_validate(raw).model_dump(mode="json", exclude_none=True)
            reloaded = DomainPackValidatorBindings.model_validate(normalized).model_dump(mode="json", exclude_none=True)
            assert reloaded == normalized
            for bucket, bindings in normalized.items():
                for binding in bindings:
                    if "routes" in binding:
                        routed_count += 1
                        assert not {"validator_agent", "input_fields", "expected_result_fields", "max_tool_calls", "custom_profile_reuse"}.intersection(binding)
                    else:
                        key = f"{path.parent.name}:{bucket}:{binding['binding_id']}"
                        actual[key] = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert actual == expected
    assert len(actual) == 37
    assert routed_count == 2


def _routed():
    return {
        "binding_id": "fixture.lookup",
        "applies_to": {"domain_pack_id": "fixture.pack", "object_types": ["Claim"]},
        "route_by": {"source": "payload", "path": "kind"},
        "routes": {"one": {
            "validator_agent": {"package_id": "fixture.validators", "agent_id": "lookup"},
            "input_fields": {"mention": {"source": "payload", "path": "mention"}},
            "expected_result_fields": {"label": "label"},
        }},
    }


@pytest.mark.parametrize("literal", [None, False, 0, "", [], {}, {"nested": [None, {"null": None}]}])
@pytest.mark.parametrize("routed", [False, True])
def test_explicit_literal_values_survive_binding_normalization(literal, routed):
    binding = _routed()
    inputs = binding["routes"]["one"]["input_fields"]
    inputs["literal"] = {"source": "literal", "value": literal}
    inputs["keyed"] = {"source": "payload_keyed_literal", "path": "kind", "key_map": {"one": None, "two": {"nested": None}}}
    if not routed:
        route = binding.pop("routes")["one"]
        binding.pop("route_by")
        binding.update(route)
    dumped = DomainPackValidatorBindings.model_validate({"active": [binding]}).model_dump(mode="json", exclude_none=True)
    assert DomainPackValidatorBindings.model_validate(dumped).model_dump(mode="json", exclude_none=True) == dumped
    saved = dumped["active"][0]
    actual_inputs = saved["routes"]["one"]["input_fields"] if routed else saved["input_fields"]
    assert actual_inputs["literal"]["value"] == literal
    assert actual_inputs["keyed"]["key_map"] == inputs["keyed"]["key_map"]


@pytest.mark.parametrize("field,value", [("input_fields", {}), ("expected_result_fields", {}), ("validator_agent", None), ("max_tool_calls", None), ("custom_profile_reuse", None)])
@pytest.mark.parametrize("bucket", ["active", "under_development"])
def test_routed_binding_rejects_top_level_execution_fields(field, value, bucket):
    binding = deepcopy(_routed())
    if bucket == "under_development":
        binding.update(display_name="Future lookup", state_explanation="Not implemented")
    binding[field] = value
    with pytest.raises(ValidationError):
        DomainPackValidatorBindings.model_validate({bucket: [binding]})
