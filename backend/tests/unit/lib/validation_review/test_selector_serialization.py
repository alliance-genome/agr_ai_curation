"""Persisted review configuration retains selector presence and scientific nulls."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry, ValidatorRoute
from src.lib.validation_review.validator_adapter import BINDING, REGISTRY, _serialize_binding
from src.schemas.domain_pack_metadata import DomainPackInputSelector


def package_registry(name):
    directory = Path(__file__).resolve().parents[5] / "packages/alliance/domain_packs" / name
    metadata = load_domain_pack_metadata(directory / "domain_pack.yaml")
    pack = LoadedDomainPack(metadata.pack_id, metadata.display_name, metadata.version,
                            directory, directory / "domain_pack.yaml", metadata)
    return DomainPackValidationRegistry.from_domain_pack(pack)


@pytest.mark.parametrize("name", ["allele", "gene_expression"])
def test_real_package_registry_roundtrip_preserves_nested_selector_keys(name):
    registry = package_registry(name)
    dumped = REGISTRY.dump_python(registry, mode="json")
    dumped["bindings"] = [_serialize_binding(binding) for binding in registry.bindings]
    restored = REGISTRY.validate_python(dumped)
    assert restored == registry
    contexts = 0
    for binding, saved in zip(registry.bindings, dumped["bindings"], strict=True):
        assert saved["raw"] == binding.raw
        if binding.custom_profile_reuse:
            for key, specification in binding.custom_profile_reuse.inputs.items():
                selector = specification.context_selector
                if selector is not None:
                    contexts += 1
                    actual = saved["custom_profile_reuse"]["inputs"][key]["context_selector"]
                    # Explicit source fields survive; optional model defaults do not appear.
                    expected_keys = selector.model_fields_set - {"output", "context_only"}
                    if selector.output != "value":
                        expected_keys.add("output")
                    if selector.context_only:
                        expected_keys.add("context_only")
                    assert set(actual) == expected_keys
    assert contexts > 0


@pytest.mark.parametrize("source", [
    {"source": "literal", "value": None},
    {"source": "literal", "value": {"a": None}, "allow_multiple": None},
    {"source": "payload_keyed_literal", "path": "kind", "key_map": {"a": None}},
    {"source": "evidence_record", "path": "quote", "output": "value"},
])
def test_normal_and_routed_selectors_preserve_explicit_values(source):
    original = deepcopy(source)
    selector = DomainPackInputSelector.model_validate(source)
    binding = next(b for b in package_registry("allele").bindings if b.validator_agent)
    binding = replace(binding, input_fields={"input": selector},
                      routes={"route": ValidatorRoute(binding.validator_agent, {"input": selector})},
                      raw={"scientific_value": None})
    saved = _serialize_binding(binding)
    assert BINDING.validate_python(saved) == binding
    for output in (saved["input_fields"]["input"], saved["routes"]["route"]["input_fields"]["input"]):
        assert output == {k: v for k, v in source.items() if k != "output"}
    assert saved["raw"] == {"scientific_value": None}
    assert source == original


def test_explicit_forbidden_null_stays_invalid():
    with pytest.raises(ValidationError, match="do not support"):
        DomainPackInputSelector.model_validate({"source": "literal", "value": None, "path": None})
