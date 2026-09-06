"""Exact profile overlays reuse the registry and ordinary request builders."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from src.lib.agent_studio.profile_conformance import ProfileIdentityError, ResolvedGenericProfile
from src.lib.agent_studio.profile_mapping_service import ReusableCapability
from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.profile_validation import compile_profile_validation
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import ValidatorAgentRef, ValidatorBinding, ValidationBindingState
from src.schemas.agent_execution_revision import AgentExecutionReceipt
from src.schemas.domain_envelope import DomainEnvelope
from src.schemas.domain_pack_metadata import CustomProfileValidatorReuse, DomainPackInputSelector, DomainPackMetadata
from src.schemas.generic_extraction_profile import normalize_profile_contract
from src.schemas.profile_validator_mapping import ValidatorCapabilityRef


@pytest.fixture
def example():
    reuse = CustomProfileValidatorReuse.model_validate({
        "enabled": True, "supports_element_fanout": True,
        "inputs": {"mention": {"value_schema": {"kind": "string"}, "required": True}},
        "outputs": {"identifier": {"value_schema": {"kind": "string"}, "result_path": "identifier"}},
        "policy": {"unresolved_default": "requires_curator_review",
                   "unresolved_allowed": ["informational", "requires_curator_review"],
                   "readiness_default": False, "readiness_allowed": [False, True]},
    })
    binding = ValidatorBinding(
        binding_id="lookup", state=ValidationBindingState.ACTIVE, source_scope="object",
        validator_agent=ValidatorAgentRef("example", "lookup"), custom_profile_reuse=reuse,
        input_fields={"mention": DomainPackInputSelector(source="payload", path="source_name")},
        expected_result_fields={"identifier": "unsafe_source_destination"},
        raw={"custom_profile_reuse": reuse.model_dump(mode="json"),
             "materializes_to_field_paths": ["unsafe_mirror"]},
    )
    cap = ReusableCapability(ValidatorCapabilityRef(package_id="example", package_version="1.0.0",
        domain_pack_id="example.record", domain_pack_version="1.0.0", binding_id="lookup"), binding)
    raw = {"name": "Records", "semantic_class": "record", "fields": [
        {"key": "paper_name", "required": True, "source_labels": ["Published label"], "value_schema": {"kind": "string"}},
        {"key": "resolved_id", "value_schema": {"kind": "string"}},
    ], "validator_mappings": [{"mapping_id": "lookup", "capability_ref": cap.ref.model_dump(),
        "capability_fingerprint": cap.fingerprint(), "inputs": {"mention": {"field_path": "attributes.paper_name"}},
        "outputs": {"identifier": "attributes.resolved_id"},
        "policy": {"unresolved": "requires_curator_review", "blocks_readiness": False}}]}
    metadata = DomainPackMetadata(pack_id="generic", display_name="Generic", version="1.0.0",
                                 metadata_api_version="1.0.0", metadata={"source_mirror": "unsafe"})
    pack = LoadedDomainPack("generic", "Generic", "1.0.0", Path("generic"), Path("generic/domain.yaml"), metadata)
    return raw, cap, pack


def resolve(raw):
    contract = normalize_profile_contract(raw)
    receipt = AgentExecutionReceipt(
        agent_id=uuid4(), agent_key="ca_fixture", agent_revision_id=uuid4(), revision=2,
        fingerprint="sha256:" + "a" * 64,
        output_contract={"output_state": "structured_extraction", "output_mode": "profile_bound_generic",
                         "generic_profile_ref": {"profile_id": uuid4(), "profile_revision_id": uuid4(),
                                                 "revision": 1, "fingerprint": contract.fingerprint()}},
    )
    return receipt, ResolvedGenericProfile(receipt.output_contract.generic_profile_ref, contract)


def envelope(attributes):
    return DomainEnvelope(envelope_id="test", domain_pack_id="generic", extracted_objects=[{
        "object_type": "generic_object", "object_id": "one",
        "payload": {"semantic_class": "record", "attributes": attributes},
    }])


def profile_envelope(attributes, receipt, profile):
    source = envelope(attributes)
    source.metadata["execution_receipt"] = receipt.model_dump(mode="json")
    source.metadata["extraction_metadata"] = {"provenance": {
        "produced_by": receipt.agent_key, "execution_receipt": receipt.model_dump(mode="json"),
        "generic_profile_ref": profile.receipt,
    }}
    for obj in source.extracted_objects:
        obj.metadata.update(generic_profile_ref=profile.receipt,
                            generic_extraction={"class_key": "generic:generic_object"})
    return source


def test_compile_exact_paths_without_mutating_pack_or_capability(example):
    raw, cap, pack = example
    receipt, profile = resolve(raw)
    original = deepcopy(pack.metadata.model_dump())
    context = compile_profile_validation(receipt, profile, pack, capabilities=[cap])
    assert not context.unavailable
    binding, = context.registry.bindings
    assert binding.expected_result_fields == {"identifier": "attributes.resolved_id"}
    assert binding.input_fields["mention"].path == "attributes.paper_name"
    assert set(binding.raw) == {"profile_validation"}
    assert "unsafe" not in str(context.registry.domain_pack.metadata)
    assert pack.metadata.model_dump() == original
    assert cap.binding.expected_result_fields == {"identifier": "unsafe_source_destination"}
    match, = context.registry.match_bindings(envelope({"paper_name": "A"}))
    built = build_domain_validation_request(match)
    assert not built.findings
    assert built.request.selected_inputs == {"mention": "A"}
    assert built.request.expected_result_fields == binding.expected_result_fields
    assert profile.receipt["fingerprint"] in context.identity


def test_per_element_reuses_existing_fanout_and_indexed_destinations(example):
    raw, cap, pack = example
    raw["fields"] = [{"key": "records", "required": True, "value_schema": {
        "kind": "array", "items": {"kind": "object", "fields": raw["fields"]}}}]
    mapping = raw["validator_mappings"][0]
    mapping.update(mode="per_element", inputs={"mention": {"field_path": "attributes.records[].paper_name"}},
                   outputs={"identifier": "attributes.records[].resolved_id"})
    receipt, profile = resolve(raw)
    context = compile_profile_validation(receipt, profile, pack, capabilities=[cap])
    assert not context.unavailable
    matches = context.registry.match_bindings(envelope({"records": [{"paper_name": "A"}, {"paper_name": "B"}]}))
    requests = [build_domain_validation_request(match).request for match in matches]
    assert [r.selected_inputs for r in requests] == [{"mention": "A"}, {"mention": "B"}]
    assert [r.expected_result_fields for r in requests] == [
        {"identifier": "attributes.records[0].resolved_id"}, {"identifier": "attributes.records[1].resolved_id"}]
    assert context.registry.match_bindings(envelope({"records": []})) == ()


@pytest.mark.parametrize("kind", ["missing", "revoked", "changed", "inactive", "group"])
def test_unavailable_mappings_remain_explicit_and_do_not_float(example, kind):
    raw, cap, pack = example
    receipt, profile = resolve(raw)
    if kind == "revoked":
        cap = replace(cap, available=False, unavailable_reason="Access revoked")
    elif kind == "changed":
        cap = replace(cap, binding=replace(cap.binding, raw={"changed": True}))
    elif kind == "inactive":
        cap = replace(cap, binding=replace(cap.binding, state=ValidationBindingState.UNDER_DEVELOPMENT))
    elif kind == "group":
        cap = replace(cap, binding=replace(cap.binding, required_any_active_group=("FB",)))
    context = compile_profile_validation(receipt, profile, pack, capabilities=[] if kind == "missing" else [cap])
    assert not context.registry.bindings
    unavailable, = context.unavailable
    assert unavailable.mapping.mapping_id == "lookup"
    assert unavailable.reasons
    assert unavailable.mapping.policy.unresolved == "requires_curator_review"


def test_reauthorization_is_not_cached(example, monkeypatch):
    raw, cap, pack = example
    receipt, profile = resolve(raw)
    catalog = [cap]
    monkeypatch.setattr("src.lib.domain_packs.profile_validation.capability_catalog", lambda **kwargs: catalog)
    first = compile_profile_validation(receipt, profile, pack)
    catalog.clear()
    second = compile_profile_validation(receipt, profile, pack)
    assert first.registry.bindings
    assert second.unavailable and not second.registry.bindings
    assert first.identity == second.identity


def test_profile_receipt_mismatch_fails_before_compilation(example):
    raw, cap, pack = example
    receipt, profile = resolve(raw)
    other, _ = resolve(raw)
    with pytest.raises(ProfileIdentityError):
        compile_profile_validation(other, profile, pack, capabilities=[cap])


def test_unmapped_fields_get_no_guessed_validators(example):
    raw, cap, pack = example
    raw["validator_mappings"] = []
    receipt, profile = resolve(raw)
    context = compile_profile_validation(receipt, profile, pack, capabilities=[cap])
    assert not context.registry.bindings and not context.unavailable
    assert context.registry.domain_pack.pack_id == "generic"


def test_conflicting_destinations_disable_both_mappings(example):
    raw, cap, pack = example
    raw["validator_mappings"].append({**deepcopy(raw["validator_mappings"][0]), "mapping_id": "second"})
    receipt, profile = resolve(raw)
    context = compile_profile_validation(receipt, profile, pack, capabilities=[cap])
    assert not context.registry.bindings
    assert len(context.unavailable) == 2


@pytest.mark.parametrize("constant", [False, True])
def test_compilation_retains_complete_provider_scope_and_selector(example, constant):
    raw, cap, pack = example
    reuse = cap.binding.custom_profile_reuse.model_copy(deep=True)
    from src.schemas.domain_pack_metadata import ReusableValidatorInput
    reuse.inputs["provider"] = ReusableValidatorInput.model_validate({
        "value_schema": {"kind": "string"}, "required": True, "allow_constant": True})
    reuse.provider_input_slots = {"source.provider": "provider"}
    binding = replace(cap.binding, custom_profile_reuse=reuse, required_any_active_group=("FB",),
                      provider_value_field_paths=("source.provider",), allowed_provider_values=("FB",),
                      allow_cross_provider=False,
                      input_fields={**cap.binding.input_fields,
                                    "provider": DomainPackInputSelector(source="payload", path="source.provider")},
                      raw={"custom_profile_reuse": reuse.model_dump(mode="json")})
    cap = replace(cap, binding=binding)
    raw["fields"].append({"key": "provider", "required": True,
                          "value_schema": {"kind": "enum", "values": ["FB"]}})
    mapping = raw["validator_mappings"][0]
    mapping["capability_fingerprint"] = cap.fingerprint()
    mapping["inputs"]["provider"] = ({"source": "constant", "value": "FB"} if constant else
                                      {"field_path": "attributes.provider"})
    receipt, profile = resolve(raw)
    context = compile_profile_validation(receipt, profile, pack, capabilities=[cap], active_group_ids=["FB"])
    assert not context.unavailable
    compiled, = context.registry.bindings
    assert compiled.required_any_active_group == ("FB",)
    assert compiled.allowed_provider_values == ("FB",)
    assert compiled.allow_cross_provider is False
    policy = compiled.raw["profile_validation"]
    assert policy["source_group_scope"]["provider_value_field_paths"] == ["source.provider"]
    provider = policy["provider_selectors"]["source.provider"]
    assert provider["source"] == ("literal" if constant else "payload")
    assert provider.get("value" if constant else "path") == ("FB" if constant else "attributes.provider")
    match, = context.registry.match_bindings(envelope({"paper_name": "A", "provider": "FB"}))
    assert build_domain_validation_request(match).request.selected_inputs["provider"] == "FB"


def test_real_packaged_gene_mapping_builds_existing_request(example):
    from src.lib.agent_studio.profile_mapping_service import capability_catalog
    raw, _, pack = example
    cap = next(cap for cap in capability_catalog(active_group_ids=["FB"])
               if cap.ref.domain_pack_id == "agr.alliance.gene_expression"
               and cap.ref.binding_id == "subject_gene_validation")
    raw["fields"][1]["nullable"] = True
    raw["validator_mappings"][0].update(
        capability_ref=cap.ref.model_dump(), capability_fingerprint=cap.fingerprint(),
        inputs={"gene_symbol": {"field_path": "attributes.paper_name"}},
        outputs={"primary_external_id": "attributes.resolved_id"})
    receipt, profile = resolve(raw)
    context = compile_profile_validation(receipt, profile, pack, capabilities=[cap], active_group_ids=["FB"])
    assert not context.unavailable
    match, = context.registry.match_bindings(envelope({"paper_name": "wg"}))
    request = build_domain_validation_request(match).request
    assert request.selected_inputs == {"gene_symbol": "wg"}
    assert request.validator_agent.agent_id == cap.binding.validator_agent.agent_id
    assert request.expected_result_fields == {"primary_external_id": "attributes.resolved_id"}


def test_diagnostic_limit_cannot_hide_later_revoked_mappings(example, monkeypatch):
    raw, cap, pack = example
    raw["validator_mappings"].append({**deepcopy(raw["validator_mappings"][0]), "mapping_id": "second"})
    monkeypatch.setattr("src.lib.agent_studio.profile_mapping_service.get_generic_profile_max_issues", lambda: 1)
    cap = replace(cap, available=False, unavailable_reason="Revoked")
    receipt, profile = resolve(raw)
    context = compile_profile_validation(receipt, profile, pack, capabilities=[cap])
    assert not context.registry.bindings
    assert len(context.unavailable) == 2


@pytest.mark.parametrize("case", [
    "eligible", "missing_group", "missing_provider", "ambiguous_provider",
    "disallowed_provider", "cross_denied", "cross_allowed", "unrepresentable_scope",
])
def test_complete_provider_policy_cannot_be_relaxed_by_overlay(example, case):
    from src.lib.domain_packs.profile_validation import profile_dispatch_matches
    raw, cap, pack = example
    reuse = cap.binding.custom_profile_reuse.model_dump(mode="json")
    for name in ("provider", "other_provider"):
        reuse["inputs"][name] = {"value_schema": {"kind": "string"}, "required": True}
    reuse["provider_input_slots"] = {"source.provider": "provider", "source.other": "other_provider"}
    if case == "unrepresentable_scope":
        del reuse["provider_input_slots"]["source.other"]
    typed_reuse = CustomProfileValidatorReuse.model_validate(reuse)
    binding = replace(cap.binding, custom_profile_reuse=typed_reuse,
        required_any_active_group=("FB",), provider_value_field_paths=("source.provider", "source.other"),
        allowed_provider_values=("FB", "WB"), allow_cross_provider=case == "cross_allowed",
        input_fields={**cap.binding.input_fields,
            "provider": DomainPackInputSelector(source="payload", path="source.provider"),
            "other_provider": DomainPackInputSelector(source="payload", path="source.other")},
        raw={"custom_profile_reuse": reuse})
    cap = replace(cap, binding=binding)
    second = "WB" if case in {"cross_denied", "cross_allowed"} else "FB"
    first = "ZFIN" if case == "disallowed_provider" else "FB"
    provider_field = {"key": "provider", "required": case != "missing_provider",
                      "value_schema": {"kind": "enum", "values": [first]}}
    if case == "ambiguous_provider":
        provider_field["value_schema"] = {"kind": "array", "items": {"kind": "string"}}
    raw["fields"].extend([provider_field, {"key": "other_provider", "required": True,
                                         "value_schema": {"kind": "enum", "values": [second]}}])
    mapping = raw["validator_mappings"][0]
    mapping.update(capability_fingerprint=cap.fingerprint())
    mapping["inputs"].update(provider={"field_path": "attributes.provider"},
                              other_provider={"field_path": "attributes.other_provider"})
    receipt, profile = resolve(raw)
    context = compile_profile_validation(receipt, profile, pack, capabilities=[cap],
                                         active_group_ids=[] if case == "missing_group" else ["FB"])
    attrs = {"paper_name": "A", "provider": first, "other_provider": second}
    if case == "missing_provider":
        del attrs["provider"]
    elif case == "ambiguous_provider":
        attrs["provider"] = ["FB", "WB"]
    source = profile_envelope(attrs, receipt, profile)
    eligible, findings, audit = profile_dispatch_matches(source, context, authenticated_groups=["FB"])
    if case in {"eligible", "cross_allowed"}:
        assert len(eligible) == 1 and not findings
        assert audit[0]["eligible"] is True
        request = build_domain_validation_request(eligible[0]).request
        assert request.selected_inputs["provider"] == first
        assert request.selected_inputs["other_provider"] == second
    else:
        assert not eligible and context.unavailable
        assert findings[0].code == "generic_profile.validator_unavailable"
        assert findings[0].details["profile_validator_mapping"] == profile.contract.validator_mappings[0].model_dump(mode="json")
    assert source.extracted_objects[0].payload["attributes"] == attrs


def test_custom_revision_survives_compilation_and_uses_normal_dispatch_finalization(example, monkeypatch):
    """Integration seam: profile pin -> compiled binding -> pinned agent -> result."""
    from types import SimpleNamespace
    from src.lib.domain_packs.validator_dispatch import run_package_scoped_validator_agent, ValidatorRuntimeContext
    from src.lib.agent_studio.diagnostic_tools.tool_definitions import _unwrap_function_tool
    from src.schemas.domain_validator import DomainValidatorResultBase
    from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload
    raw, cap, pack = example
    pin = {"agent_id": str(uuid4()), "agent_key": "ca_my_validator", "revision_id": str(uuid4()), "fingerprint": "sha256:" + "b" * 64}
    cap = replace(cap, ref=cap.ref.model_copy(update={"binding_id": "lookup--custom--" + pin["revision_id"]}),
                  binding=replace(cap.binding, raw={**cap.binding.raw, "custom_validator": pin}))
    raw["validator_mappings"][0].update(capability_ref=cap.ref.model_dump(), capability_fingerprint=cap.fingerprint())
    receipt, profile = resolve(raw)
    context = compile_profile_validation(receipt, profile, pack, capabilities=[cap])
    binding, = context.registry.bindings
    assert binding.raw['custom_validator'] == pin
    match, = context.registry.match_bindings(envelope({"paper_name": "abc"}))
    request = build_domain_validation_request(match).request
    calls = []
    saved_agent = SimpleNamespace(output_type=DomainValidatorResultBase, tools=[], instructions='Saved custom instructions',
                                  model='validator-model', execution_receipt={'fingerprint': pin['fingerprint']})
    def build(agent_key, **kwargs):
        calls.append((agent_key, kwargs))
        assert agent_key == pin['agent_key']  # Package-agent lookup would fail here.
        return saved_agent
    monkeypatch.setattr('src.lib.config.agent_loader.get_agent_definition_for_package', lambda *_: SimpleNamespace())
    monkeypatch.setattr('src.lib.agent_studio.catalog_service.get_agent_by_id', build)
    monkeypatch.setattr('src.lib.domain_packs.validator_dispatch.provider_context_preflight', lambda **_: {})
    monkeypatch.setattr('src.lib.openai_agents.config.resolve_model_provider', lambda _: 'openai')
    def run(agent, **kwargs):
        assert 'Saved custom instructions' in agent.instructions
        finalizer = next(tool for tool in agent.tools if tool.name == 'finalize_validator_result')
        _unwrap_function_tool(finalizer)(result=_result_payload(request, resolved_values={'identifier': 'AGR:123'}))
        return {'status': 'resolved'}
    monkeypatch.setattr('src.lib.openai_agents.runner.run_agent_sync_with_owned_openai_resources', run)
    result = run_package_scoped_validator_agent(request, binding=binding,
        runtime_context=ValidatorRuntimeContext(user_id='7', authenticated_groups=('FB',)))
    assert calls == [(pin['agent_key'], {'execution_revision_id': pin['revision_id'], 'db_user_id': 7,
        'user_id': '7', 'document_id': None, 'authenticated_groups': ['FB']})]
    assert result.accepted_result.resolved_values == {'identifier': 'AGR:123'}
    assert result.accepted_result.validator_binding_id == binding.binding_id
    assert saved_agent.tools == []


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("with_context", [False, True])
def test_packaged_allele_custom_field_does_not_require_paper_quote(example, nested, with_context):
    from src.lib.agent_studio.profile_mapping_service import capability_catalog, capability_issues
    raw, _, pack = example
    cap = next(cap for cap in capability_catalog(active_group_ids=["MGI"])
               if cap.ref.domain_pack_id == "agr.alliance.allele"
               and cap.ref.binding_id == "allele_mention_reference_validation")
    assert not capability_issues(cap, ["MGI"])
    # Optional custom details and parts can supply the lookup input.
    raw["fields"][0]["required"] = False
    raw["fields"][0]["nullable"] = True
    prefix = "attributes.stock" if nested else "attributes"
    if nested:
        raw["fields"] = [{"key": "stock", "value_schema": {"kind": "object", "fields": raw["fields"]}}]
    inputs = {"mention": {"field_path": prefix + ".paper_name"}}
    if with_context:
        inputs.update(taxon={"source": "constant", "value": "NCBITaxon:10090"},
                      evidence_quote={"source": "context"})
    raw["validator_mappings"][0].update(
        capability_ref=cap.ref.model_dump(), capability_fingerprint=cap.fingerprint(),
        inputs=inputs, outputs={"curie": prefix + ".resolved_id"},
        policy={"unresolved": "requires_curator_review", "blocks_readiness": True})
    receipt, profile = resolve(raw)
    context = compile_profile_validation(receipt, profile, pack, capabilities=[cap], active_group_ids=["MGI"])
    assert not context.unavailable
    attributes = {"paper_name": "MGI:2175911"}
    if nested:
        attributes = {"stock": attributes}
    match, = context.registry.match_bindings(envelope(attributes))
    built = build_domain_validation_request(match)
    assert built.request is not None
    assert built.request.selected_inputs["mention"] == "MGI:2175911"
    assert built.request.validator_agent.agent_id == "allele_validation"
    assert built.request.expected_result_fields == {"curie": prefix + ".resolved_id"}
    assert not built.request.selected_inputs.get("evidence_quote")
    if with_context:
        assert built.request.selected_inputs["taxon"] == "NCBITaxon:10090"
