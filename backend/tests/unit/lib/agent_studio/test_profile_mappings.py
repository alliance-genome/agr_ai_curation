"""Explicit mapping compatibility; no semantic dispatch or inferred aliases."""

from copy import deepcopy
from dataclasses import replace

import pytest

from src.lib.agent_studio.profile_mapping_service import (
    ProfileMappingError, ReusableCapability, declared_profile_path, validate_profile_mappings,
)
from src.lib.agent_studio.profile_compatibility import profile_compatibility
from src.lib.domain_packs.validation_registry import ValidatorBinding, ValidationBindingState
from src.schemas.domain_pack_metadata import CustomProfileValidatorReuse, DomainPackInputSelector
from src.schemas.generic_extraction_profile import normalize_profile_contract, canonical_json
from src.schemas.profile_validator_mapping import ValidatorCapabilityRef


def fixture():
    reuse = CustomProfileValidatorReuse.model_validate({
        "enabled": True,
        "inputs": {"mention": {"value_schema": {"kind": "string"}, "required": True}},
        "outputs": {"identifier": {"value_schema": {"kind": "string"}, "result_path": "identifier"}},
        "policy": {"unresolved_default": "requires_curator_review",
                   "unresolved_allowed": ["informational", "requires_curator_review"],
                   "readiness_default": False, "readiness_allowed": [False]},
    })
    binding = ValidatorBinding(binding_id="lookup", state=ValidationBindingState.ACTIVE,
        source_scope="object", custom_profile_reuse=reuse,
        input_fields={"mention": DomainPackInputSelector(source="payload", path="mention")},
        expected_result_fields={"identifier": "identifier"},
        raw={"custom_profile_reuse": reuse.model_dump(mode="json")})
    capability = ReusableCapability(ValidatorCapabilityRef(package_id="example", package_version="1.0.0",
        domain_pack_id="example.record", domain_pack_version="1.0.0", binding_id="lookup"), binding)
    contract = {"name": "Records", "semantic_class": "record", "fields": [
        {"key": "paper_name", "required": True, "source_labels": ["Paper label"], "value_schema": {"kind": "string"}},
        {"key": "resolved_id", "nullable": True, "value_schema": {"kind": "string"}},
    ], "validator_mappings": [{"mapping_id": "lookup", "capability_ref": capability.ref.model_dump(),
        "capability_fingerprint": capability.fingerprint(),
        "inputs": {"mention": {"field_path": "attributes.paper_name"}},
        "outputs": {"identifier": "attributes.resolved_id"},
        "policy": {"unresolved": "requires_curator_review", "blocks_readiness": False}}]}
    return contract, capability


def test_typed_mapping_roundtrip_fingerprint_and_diff():
    raw, cap = fixture()
    parsed = normalize_profile_contract(raw)
    assert normalize_profile_contract(parsed.model_dump(mode="json")).fingerprint() == parsed.fingerprint()
    assert validate_profile_mappings(parsed, capabilities=[cap]) == [cap]
    empty = {**raw, "validator_mappings": []}
    assert normalize_profile_contract(empty).fingerprint() != parsed.fingerprint()
    diffs = profile_compatibility(empty, parsed)
    assert diffs[0]["path"] == "validator_mappings"
    canonical_json(diffs)


@pytest.mark.parametrize("path", ["attributes.Paper label", "attributes.paper_label", "paper_name", "payload.paper_name",
                                  "attributes.paper_name.foo", "attributes.paper_name[0]", "attributes.paper_name[]"])
def test_only_canonical_declared_paths(path):
    raw, cap = fixture()
    raw["validator_mappings"][0]["inputs"]["mention"]["field_path"] = path
    with pytest.raises(ProfileMappingError) as error:
        validate_profile_mappings(raw, capabilities=[cap])
    assert any(i["code"] == "path" for i in error.value.issues)


@pytest.mark.parametrize("change,code", [
    (lambda m: m.update(capability_fingerprint="sha256:" + "0" * 64), "identity_mismatch"),
    (lambda m: m["capability_ref"].update(package_version="2.0.0"), "unavailable"),
    (lambda m: m.update(inputs={}), "missing_slot"),
    (lambda m: m["inputs"].update(unknown={"field_path": "attributes.paper_name"}), "unknown_slot"),
    (lambda m: m["policy"].update(blocks_readiness=True), "policy"),
    (lambda m: m["policy"].update(unresolved="error"), "policy"),
    (lambda m: m.update(mode="per_element"), "cardinality"),
    (lambda m: m["outputs"].update(identifier="attributes.missing"), "path"),
    (lambda m: m["outputs"].update(unknown="attributes.resolved_id"), "unknown_slot"),
    (lambda m: m["inputs"].update(mention={"source": "constant", "value": "invented"}), "constant"),
    (lambda m: m["inputs"].update(mention={"source": "context"}), "context"),
])
def test_mapping_rejections_are_addressed(change, code):
    raw, cap = fixture()
    change(raw["validator_mappings"][0])
    with pytest.raises(ProfileMappingError) as error:
        validate_profile_mappings(raw, capabilities=[cap])
    assert any(i["code"] == code and i["path"].startswith("validator_mappings[0]") for i in error.value.issues)


def test_nonactive_disabled_unavailable_and_missing_groups():
    raw, cap = fixture()
    for candidate in [replace(cap, available=False),
                      replace(cap, binding=replace(cap.binding, state=ValidationBindingState.UNDER_DEVELOPMENT)),
                      replace(cap, binding=replace(cap.binding, required_any_active_group=("FB",)))]:
        with pytest.raises(ProfileMappingError, match="incompatible"):
            validate_profile_mappings(raw, capabilities=[candidate])


def test_type_nullable_and_required_inputs():
    raw, cap = fixture()
    for change in [{"value_schema": {"kind": "integer"}}, {"required": False}, {"nullable": True}]:
        changed = deepcopy(raw)
        changed["fields"][0].update(change)
        with pytest.raises(ProfileMappingError):
            validate_profile_mappings(changed, capabilities=[cap])


def test_duplicate_writes_and_no_guessed_mappings():
    raw, cap = fixture()
    raw["validator_mappings"].append({**deepcopy(raw["validator_mappings"][0]), "mapping_id": "second"})
    with pytest.raises(ProfileMappingError) as error:
        validate_profile_mappings(raw, capabilities=[cap])
    assert any(i["code"] == "write_conflict" for i in error.value.issues)
    raw["validator_mappings"] = []
    assert validate_profile_mappings(raw, capabilities=[cap]) == []


def test_nested_array_path_requires_explicit_single_fanout():
    raw, cap = fixture()
    raw["fields"] = [{"key": "records", "required": True, "value_schema": {"kind": "array", "items": {
        "kind": "object", "fields": raw["fields"]}}}]
    mapping = raw["validator_mappings"][0]
    mapping["inputs"]["mention"]["field_path"] = "attributes.records[].paper_name"
    mapping["outputs"]["identifier"] = "attributes.records[].resolved_id"
    reuse = cap.binding.custom_profile_reuse.model_copy(update={"supports_element_fanout": True})
    cap = replace(cap, binding=replace(cap.binding, custom_profile_reuse=reuse))
    with pytest.raises(ProfileMappingError):
        validate_profile_mappings(raw, capabilities=[cap])
    mapping["mode"] = "per_element"
    assert validate_profile_mappings(raw, capabilities=[cap]) == [cap]
    _, arrays = declared_profile_path(normalize_profile_contract(raw), "attributes.records[].paper_name")
    assert arrays == ("attributes.records[]",)


def test_packaged_capabilities_are_explicit_and_use_real_implementations():
    from src.lib.agent_studio.profile_mapping_service import capability_catalog, capability_issues
    capabilities = capability_catalog(active_group_ids=["FB"])
    keyed = {(cap.ref.domain_pack_id, cap.ref.binding_id): cap for cap in capabilities}
    for key in [("agr.alliance.gene_expression", "subject_gene_validation"),
                ("agr.alliance.gene_expression", "source_reference_validation")]:
        assert key in keyed
        cap = keyed[key]
        assert not capability_issues(cap, ["FB"])
        assert cap.ref.package_id == "agr.alliance"
        assert cap.fingerprint().startswith("sha256:")
    assert ("gene", "alliance_gene_reference_lookup") not in keyed
    gene = keyed[("agr.alliance.gene_expression", "subject_gene_validation")]
    reference = keyed[("agr.alliance.gene_expression", "source_reference_validation")]
    # Gillian-style inventory: structural fields have no guessed semantic validator.
    inventory = {"name": "Provisional reagent inventory", "semantic_class": "reagent_inventory",
        "fields": [
            {"key": "paper_labels", "required": True, "value_schema": {"kind": "array", "items": {"kind": "string"}}},
            {"key": "source_status", "required": True, "value_schema": {"kind": "enum", "values": ["new_in_paper", "external", "not_stated"]}},
            *[{"key": key, "nullable": True, "value_schema": {"kind": "string"}}
              for key in ("gene_symbol", "gene_id", "reference_curie", "confirmed_reference")]],
        "validator_mappings": [
            {"mapping_id": "gene", "capability_ref": gene.ref.model_dump(), "capability_fingerprint": gene.fingerprint(),
             "inputs": {"gene_symbol": {"field_path": "attributes.gene_symbol"}},
             "outputs": {"primary_external_id": "attributes.gene_id"},
             "policy": {"unresolved": "requires_curator_review", "blocks_readiness": False}},
            {"mapping_id": "reference", "capability_ref": reference.ref.model_dump(), "capability_fingerprint": reference.fingerprint(),
             "inputs": {"curie": {"field_path": "attributes.reference_curie"}},
             "outputs": {"curie": "attributes.confirmed_reference"},
             "policy": {"unresolved": "requires_curator_review", "blocks_readiness": False}}]}
    assert validate_profile_mappings(inventory, capabilities=capabilities, active_group_ids=["FB"]) == [gene, reference]


@pytest.mark.parametrize("provider,allowed,cross,groups,valid", [
    (["FB"], ("FB",), False, ("FB",), True),
    (["WB"], ("FB",), False, ("FB",), False),
    (["FB", "WB"], ("FB", "WB"), False, ("FB",), False),
    (["FB", "WB"], ("FB", "WB"), True, ("FB",), True),
    (["FB"], ("FB",), False, (), False),
])
def test_scope_bounds_are_not_relaxed(provider, allowed, cross, groups, valid):
    from src.schemas.domain_pack_metadata import ReusableValidatorInput
    raw, cap = fixture()
    raw["fields"].append({"key": "provider", "required": True,
                          "value_schema": {"kind": "enum", "values": provider}})
    raw["validator_mappings"][0]["inputs"]["provider"] = {"field_path": "attributes.provider"}
    reuse = cap.binding.custom_profile_reuse.model_copy(update={
        "provider_input_slots": {"provider": "provider"},
        "inputs": {**cap.binding.custom_profile_reuse.inputs, "provider": ReusableValidatorInput(
            value_schema={"kind": "string"}, required=True)}})
    binding = replace(cap.binding, custom_profile_reuse=reuse, required_any_active_group=("FB",),
        provider_value_field_paths=("provider",), allowed_provider_values=allowed, allow_cross_provider=cross,
        input_fields={**cap.binding.input_fields, "provider": DomainPackInputSelector(source="payload", path="provider")})
    cap = replace(cap, binding=binding)
    if valid:
        assert validate_profile_mappings(raw, capabilities=[cap], active_group_ids=groups) == [cap]
    else:
        with pytest.raises(ProfileMappingError):
            validate_profile_mappings(raw, capabilities=[cap], active_group_ids=groups)


def test_permitted_constant_uses_recursive_conformance():
    raw, cap = fixture()
    reuse = cap.binding.custom_profile_reuse.model_copy(deep=True)
    reuse.inputs["mention"].allow_constant = True
    cap = replace(cap, binding=replace(cap.binding, custom_profile_reuse=reuse))
    raw["validator_mappings"][0]["inputs"]["mention"] = {"source": "constant", "value": "supplied"}
    assert validate_profile_mappings(raw, capabilities=[cap]) == [cap]
    raw["validator_mappings"][0]["inputs"]["mention"]["value"] = 99
    with pytest.raises(ProfileMappingError) as error:
        validate_profile_mappings(raw, capabilities=[cap])
    assert any(i["code"] == "type" for i in error.value.issues)


def test_fixed_package_selectors_cannot_be_replaced_by_profile_input():
    from src.lib.agent_studio.profile_mapping_service import capability_issues
    raw, cap = fixture()
    fixed = DomainPackInputSelector(source="literal", value="fixed", required=True)
    cap = replace(cap, binding=replace(cap.binding, input_fields={"mention": fixed}))
    assert any("exact package selector" in issue for issue in capability_issues(cap, ()))
    reuse = cap.binding.custom_profile_reuse.model_copy(deep=True)
    reuse.inputs["mention"].allow_field = False
    reuse.inputs["mention"].context_selector = fixed
    cap = replace(cap, binding=replace(cap.binding, custom_profile_reuse=reuse))
    raw["validator_mappings"][0]["inputs"]["mention"] = {"source": "context"}
    assert validate_profile_mappings(raw, capabilities=[cap]) == [cap]


@pytest.mark.parametrize("pack_status,available", [("active", True), ("in_development", True), ("deprecated", False)])
def test_reusable_active_validator_lifecycle_is_separate_from_envelope(pack_status, available):
    from dataclasses import replace
    from src.lib.agent_studio.profile_mapping_service import capability_catalog, capability_issues
    from src.lib.flows.validation_attachments import domain_pack_validation_registries
    from src.schemas.domain_pack_metadata import DomainPackStatus
    registry = next(registry for registry in domain_pack_validation_registries().values()
                    if registry.domain_pack.pack_id == "agr.alliance.allele")
    pack = replace(registry.domain_pack, metadata=registry.domain_pack.metadata.model_copy(
        update={"status": DomainPackStatus(pack_status)}))
    cap, = capability_catalog([replace(registry, domain_pack=pack)], active_group_ids=["MGI"])
    assert cap.available is available
    assert bool(capability_issues(cap, ["MGI"])) is not available
    # Pack lifecycle alone never activates an under-development binding.
    inactive = replace(cap.binding, state=ValidationBindingState.UNDER_DEVELOPMENT)
    cap, = capability_catalog([replace(registry, domain_pack=pack, bindings=(inactive,))], active_group_ids=["MGI"])
    assert not cap.available
