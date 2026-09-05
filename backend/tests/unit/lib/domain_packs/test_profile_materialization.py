"""Profile write-back closes every channel before one whole-record commit."""

from dataclasses import replace

import pytest

from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.materialization import ValidatorResultMaterializationInput
from src.lib.domain_packs.profile_materialization import materialize_profile_validator_results
from src.lib.domain_packs.profile_validation import compile_profile_validation
from src.schemas.domain_validator import DomainValidatorResultBase
from .test_profile_validation import example as example, envelope, resolve


def prepared(example, *, attributes=None, per_element=False, active_group_ids=()):
    raw, cap, pack = example
    if per_element:
        raw["fields"] = [{"key": "records", "required": True, "value_schema": {
            "kind": "array", "items": {"kind": "object", "fields": raw["fields"]}}}]
        raw["validator_mappings"][0].update(mode="per_element",
            inputs={"mention": {"field_path": "attributes.records[].paper_name"}},
            outputs={"identifier": "attributes.records[].resolved_id"})
    receipt, profile = resolve(raw)
    context = compile_profile_validation(receipt, profile, pack, capabilities=[cap], active_group_ids=active_group_ids)
    source = envelope(attributes or {"paper_name": "A"})
    source.metadata["execution_receipt"] = receipt.model_dump(mode="json")
    source.metadata["extraction_metadata"] = {"provenance": {
        "produced_by": receipt.agent_key, "execution_receipt": receipt.model_dump(mode="json"),
        "generic_profile_ref": profile.receipt,
    }}
    for obj in source.extracted_objects:
        obj.metadata.update(generic_profile_ref=profile.receipt,
                            generic_extraction={"class_key": "generic:generic_object"})
    return source, context


def results(source, context, values, *, status="resolved", objects=None):
    items = []
    matches = context.registry.match_bindings(source)
    for match, resolved in zip(matches, values, strict=True):
        request = build_domain_validation_request(match).request
        result = DomainValidatorResultBase(
            status=status, request_id=request.request_id, validator_binding_id=request.validator_binding_id,
            validator_agent=request.validator_agent, target=request.target, resolved_values=resolved,
            resolved_objects=objects or [], missing_expected_fields=[], candidates=[],
            lookup_attempts=[{"provider": "fixture", "method": "lookup", "query": {},
                              "outcome": "not_found" if status == "unresolved" else "success"}],
            curator_message=None, explanation="Fixture lookup",
        )
        items.append(ValidatorResultMaterializationInput(match, request, result))
    return items


@pytest.mark.parametrize("audit", [None, "untrusted", {"not": "a list"}])
def test_malformed_source_audit_rejects_writeback_nonfatally(example, audit):
    source, context = prepared(example)
    source.extracted_objects[0].metadata["profile_validator_materialization"] = audit
    output = materialize_profile_validator_results(source, context, results(source, context, [{"identifier": "EX:1"}]))
    assert output.envelope.extracted_objects == source.extracted_objects
    assert "audit must be a list" in output.appended_findings[0].message


def test_complete_commit_preserves_original_and_audits_exact_contract(example, monkeypatch):
    source, context = prepared(example)
    original = source.model_dump(mode="json")
    items = results(source, context, [{"identifier": "EX:1"}])
    calls = []
    patch = context.profile.patch_attributes
    monkeypatch.setattr(context.profile, "patch_attributes", lambda *a, **kw: (calls.append(a), patch(*a, **kw))[1])
    output = materialize_profile_validator_results(source, context, items)
    assert len(calls) == 1
    assert output.envelope.extracted_objects[0].payload["attributes"] == {"paper_name": "A", "resolved_id": "EX:1"}
    assert source.model_dump(mode="json") == original
    assert output.materialized_objects == ()
    finding, = output.appended_findings
    assert finding.details["execution_receipt"] == context.receipt.model_dump(mode="json")
    assert finding.details["profile_validator_mapping"]["mapping_id"] == "lookup"
    assert finding.details["linkml_alignment"] == "not_assessed"


@pytest.mark.parametrize("value", [1, True, [], {}, None])
def test_wrong_result_slot_type_retains_original_record(example, value):
    source, context = prepared(example)
    items = results(source, context, [{"identifier": value}])
    output = materialize_profile_validator_results(source, context, items)
    assert output.envelope.extracted_objects == source.extracted_objects
    assert output.appended_findings[0].code == "domain_pack.validator_materialization_invalid"
    assert output.appended_findings[0].details["materialization"] == "rejected"


@pytest.mark.parametrize("channel", ["extra_slot", "objects", "request_destination", "binding_mirror", "result_identity"])
def test_no_materialization_escape_channel(example, channel):
    source, context = prepared(example)
    item, = results(source, context, [{"identifier": "EX:1"}])
    if channel == "extra_slot":
        item.result.resolved_values["undeclared"] = "injected"
    elif channel == "objects":
        item.result.resolved_objects.append({"object_type": "generic_object", "canonical_id": "EX:1",
                                             "payload": {"attributes": {"injected": True}}})
    elif channel == "request_destination":
        item.request.expected_result_fields["identifier"] = "attributes.paper_name"
    elif channel == "binding_mirror":
        item = replace(item, match=replace(item.match, binding=replace(item.match.binding,
            raw={**item.match.binding.raw, "materializes_to_field_paths": ["attributes.paper_name"]})))
    elif channel == "result_identity":
        item.result.request_id = "other-request"
    output = materialize_profile_validator_results(source, context, [item])
    assert output.envelope.extracted_objects == source.extracted_objects
    assert output.appended_findings[0].details["materialization"] == "rejected"


def test_fanout_has_one_record_transaction_not_partial_element_writes(example):
    source, context = prepared(example, per_element=True,
        attributes={"records": [{"paper_name": "A"}, {"paper_name": "B"}]})
    items = results(source, context, [{"identifier": "EX:1"}, {"identifier": {"wrong": "type"}}])
    output = materialize_profile_validator_results(source, context, items)
    assert output.envelope.extracted_objects == source.extracted_objects
    assert len(output.appended_findings) == 2
    assert all(f.details["materialization"] == "rejected" for f in output.appended_findings)
    items[1].result.resolved_values = {"identifier": "EX:2"}
    output = materialize_profile_validator_results(source, context, items)
    assert output.envelope.extracted_objects[0].payload["attributes"]["records"] == [
        {"paper_name": "A", "resolved_id": "EX:1"}, {"paper_name": "B", "resolved_id": "EX:2"}]


def test_duplicate_destination_has_no_last_write_wins(example):
    source, context = prepared(example)
    item, = results(source, context, [{"identifier": "EX:1"}])
    other = replace(item, result=item.result.model_copy(deep=True, update={"resolved_values": {"identifier": "EX:2"}}))
    output = materialize_profile_validator_results(source, context, [item, other])
    assert output.envelope.extracted_objects == source.extracted_objects
    assert all("Conflicting" in f.message for f in output.appended_findings)


@pytest.mark.parametrize("blocking", [False, True])
def test_unresolved_semantics_preserve_conforming_record_under_pinned_policy(example, blocking):
    example[0]["validator_mappings"][0]["policy"]["blocks_readiness"] = blocking
    source, context = prepared(example)
    items = results(source, context, [{}], status="unresolved")
    output = materialize_profile_validator_results(source, context, items)
    assert output.envelope.extracted_objects == source.extracted_objects
    finding, = output.appended_findings
    assert finding.code == "domain_pack.validator_unresolved"
    assert finding.severity.value == ("blocker" if blocking else "warning")
    assert finding.details["profile_conformance"] == "conforming"


def test_rejects_changed_record_request_before_applying_stale_lookup(example):
    source, context = prepared(example)
    items = results(source, context, [{"identifier": "EX:1"}])
    changed = source.model_copy(deep=True)
    changed.extracted_objects[0].payload["attributes"]["paper_name"] = "B"
    output = materialize_profile_validator_results(changed, context, items)
    assert output.envelope.extracted_objects == changed.extracted_objects
    assert "stale" in output.appended_findings[0].message


def test_existing_dispatcher_uses_profile_transaction(example, monkeypatch):
    from src.lib.domain_packs.validator_dispatch import dispatch_active_validator_bindings
    source, context = prepared(example)
    item, = results(source, context, [{"identifier": "EX:1"}])
    monkeypatch.setattr("src.lib.domain_packs.validator_dispatch.materialize_validator_results_into_envelope",
                        lambda *a, **kw: pytest.fail("Profile entered the packaged sequential writer"))
    output = dispatch_active_validator_bindings(
        source, context.registry.domain_pack, profile_context=context,
        runner=lambda request, **kwargs: item.result.model_dump(mode="json"),
    )
    assert output.envelope.extracted_objects[0].payload["attributes"]["resolved_id"] == "EX:1"
    assert output.validator_agent_run_count == 1


def test_dispatch_resolves_authoritative_profile_without_caller_opt_in(example, monkeypatch):
    from src.lib.domain_packs.validator_dispatch import dispatch_active_validator_bindings
    source, context = prepared(example)
    item, = results(source, context, [{"identifier": "EX:1"}])
    monkeypatch.setattr("src.lib.curation_workspace.execution_contracts.load_receipt_profile",
                        lambda receipt: context.profile if receipt == context.receipt else pytest.fail("Wrong receipt"))
    monkeypatch.setattr("src.lib.domain_packs.profile_validation.capability_catalog",
                        lambda **kwargs: [example[1]])
    monkeypatch.setattr("src.lib.domain_packs.validator_dispatch.materialize_validator_results_into_envelope",
                        lambda *args, **kwargs: pytest.fail("Profile entered the packaged writer"))
    output = dispatch_active_validator_bindings(source, example[2],
        runner=lambda request, **kwargs: item.result.model_dump(mode="json"))
    assert output.envelope.extracted_objects[0].payload["attributes"]["resolved_id"] == "EX:1"


def test_supplied_context_cannot_override_authoritative_receipt(example):
    from src.lib.agent_studio.profile_conformance import ProfileIdentityError
    from src.lib.domain_packs.validator_dispatch import dispatch_active_validator_bindings
    source, context = prepared(example)
    source.metadata["execution_receipt"]["revision"] += 1
    with pytest.raises(ProfileIdentityError, match="authoritative execution receipt"):
        dispatch_active_validator_bindings(source, example[2], profile_context=context,
            runner=lambda *args, **kwargs: pytest.fail("Mismatched context ran validator"))


@pytest.mark.parametrize("identity", ["missing", "wrong_mode", "missing_profile"])
def test_dispatch_never_falls_back_for_broken_profile_identity(example, monkeypatch, identity):
    from src.lib.agent_studio.profile_conformance import ProfileIdentityError
    from src.lib.domain_packs.validator_dispatch import dispatch_active_validator_bindings
    source, _context = prepared(example)
    if identity == "missing":
        source.metadata.pop("execution_receipt")
    elif identity == "wrong_mode":
        source.metadata["execution_receipt"]["output_contract"] = {
            "output_state": "structured_extraction", "output_mode": "unprofiled_generic",
        }
    else:
        monkeypatch.setattr("src.lib.curation_workspace.execution_contracts.load_receipt_profile", lambda receipt: None)
    with pytest.raises(ProfileIdentityError):
        dispatch_active_validator_bindings(source, example[2],
            runner=lambda *args, **kwargs: pytest.fail("Invalid identity ran a validator"))


def test_existing_dispatcher_keeps_unavailable_profile_mapping_visible(example):
    from src.lib.domain_packs.validator_dispatch import dispatch_active_validator_bindings
    from src.lib.domain_packs.profile_validation import compile_profile_validation
    source, context = prepared(example)
    revoked = replace(example[1], available=False, unavailable_reason="Validator access revoked")
    context = compile_profile_validation(context.receipt, context.profile, example[2], capabilities=[revoked])
    output = dispatch_active_validator_bindings(source, context.registry.domain_pack, profile_context=context,
        runner=lambda *args, **kwargs: pytest.fail("Revoked validator was run"))
    assert output.envelope.extracted_objects == source.extracted_objects
    finding, = output.appended_findings
    assert finding.code == "generic_profile.validator_unavailable"
    assert finding.details["profile_validator_mapping"]["mapping_id"] == "lookup"


def test_packaged_dispatch_does_not_enter_profile_transaction(example, monkeypatch):
    from src.lib.domain_packs.validator_dispatch import dispatch_active_validator_bindings
    source, context = prepared(example)
    source.metadata.clear()
    for obj in source.extracted_objects:
        obj.metadata.clear()
    # A packaged binding has no profile-aware execution context, even when a
    # test fixture reuses the same shapes. Keep its existing materializer call.
    item, = results(source, context, [{"identifier": "EX:1"}])
    monkeypatch.setattr("src.lib.domain_packs.profile_materialization.materialize_profile_validator_results",
                        lambda *args, **kwargs: pytest.fail("Packaged path entered profile transaction"))
    output = dispatch_active_validator_bindings(source, context.registry.domain_pack, registry=context.registry,
        runner=lambda request, **kwargs: item.result.model_dump(mode="json"))
    assert output.envelope.extracted_objects[0].payload["attributes"]["resolved_id"] == "EX:1"


@pytest.mark.parametrize("groups", [None, (), ("WB",)])
def test_dispatch_rechecks_current_required_group_and_never_silently_omits(example, groups):
    from src.lib.domain_packs.validator_dispatch import dispatch_active_validator_bindings, ValidatorRuntimeContext
    raw, cap, pack = example
    cap = replace(cap, binding=replace(cap.binding, required_any_active_group=("FB",)))
    source, context = prepared((raw, cap, pack), active_group_ids=("FB",))
    assert context.registry.bindings
    output = dispatch_active_validator_bindings(source, context.registry.domain_pack, profile_context=context,
        runtime_context=ValidatorRuntimeContext(authenticated_groups=groups),
        runner=lambda *args, **kwargs: pytest.fail("Ineligible group ran validator"))
    assert output.validator_agent_run_count == 0
    assert output.appended_findings[0].code == "generic_profile.validator_scope_unavailable"
    assert output.envelope.extracted_objects == source.extracted_objects


@pytest.mark.parametrize("schema,value", [
    ({"kind": "boolean"}, False),
    ({"kind": "integer"}, 0),
    ({"kind": "string"}, None),
    ({"kind": "array", "items": {"kind": "string"}}, ["EX:1", "EX:2"]),
    ({"kind": "object", "fields": [{"key": "identifier", "required": True,
                                    "value_schema": {"kind": "string"}}]}, {"identifier": "EX:1"}),
])
def test_explicit_typed_slots_preserve_json_kinds_and_whole_array(example, schema, value):
    from src.schemas.domain_pack_metadata import CustomProfileValidatorReuse
    raw, cap, pack = example
    reuse = cap.binding.custom_profile_reuse.model_dump(mode="json")
    reuse["outputs"]["identifier"].update(value_schema=schema, nullable=value is None)
    cap = replace(cap, binding=replace(cap.binding,
        custom_profile_reuse=CustomProfileValidatorReuse.model_validate(reuse), raw={"custom_profile_reuse": reuse}))
    raw["fields"][1].update(value_schema=schema, nullable=value is None)
    raw["validator_mappings"][0]["capability_fingerprint"] = cap.fingerprint()
    source, context = prepared((raw, cap, pack))
    assert not context.unavailable
    output = materialize_profile_validator_results(source, context, results(source, context, [{"identifier": value}]))
    attrs = output.envelope.extracted_objects[0].payload["attributes"]
    assert "resolved_id" in attrs
    assert attrs["resolved_id"] == value
    assert type(attrs["resolved_id"]) is type(value)
    assert output.materialized_objects == ()


def test_gillian_style_record_only_runs_explicit_gene_and_reference_mappings(example):
    from src.lib.agent_studio.profile_mapping_service import capability_catalog
    from .test_profile_validation import profile_envelope
    from src.lib.domain_packs.profile_validation import profile_dispatch_matches
    _, _, pack = example
    catalog = {cap.ref.binding_id: cap for cap in capability_catalog(active_group_ids=["FB"])
               if cap.ref.domain_pack_id == "agr.alliance.gene_expression"}
    gene, reference = catalog["subject_gene_validation"], catalog["source_reference_validation"]
    raw = {"name": "Provisional reagent record fixture", "semantic_class": "record", "fields": [
        {"key": key, "required": required, "value_schema": {"kind": "string"}}
        for key, required in [("gene_mention", True), ("gene_id", False), ("paper_title", True),
                              ("reference_id", False), ("reagent_name", True), ("reagent_source", True)]
    ], "validator_mappings": [
        {"mapping_id": name, "capability_ref": cap.ref.model_dump(mode="json"),
         "capability_fingerprint": cap.fingerprint(), "inputs": {input_slot: {"field_path": source}},
         "outputs": {output_slot: destination},
         "policy": {"unresolved": "requires_curator_review", "blocks_readiness": False}}
        for name, cap, input_slot, source, output_slot, destination in [
            ("gene", gene, "gene_symbol", "attributes.gene_mention", "primary_external_id", "attributes.gene_id"),
            ("reference", reference, "title", "attributes.paper_title", "curie", "attributes.reference_id")]
    ]}
    receipt, profile = resolve(raw)
    context = compile_profile_validation(receipt, profile, pack, capabilities=[gene, reference], active_group_ids=["FB"])
    assert not context.unavailable and len(context.registry.bindings) == 2
    attrs = {"gene_mention": "wg", "paper_title": "Fixture paper", "reagent_name": "anti-Wg",
             "reagent_source": "Reported supplier"}
    source = profile_envelope(attrs, receipt, profile)
    matches, findings, _ = profile_dispatch_matches(source, context, authenticated_groups=["FB"])
    assert len(matches) == 2 and not findings
    values = [{slot: "FB:FBgn0004009" if slot == "primary_external_id" else "PMID:12345"
               for slot in match.binding.expected_result_fields} for match in matches]
    output = materialize_profile_validator_results(source, context, results(source, context, values))
    assert output.envelope.extracted_objects[0].payload["attributes"] == {
        **attrs, "gene_id": "FB:FBgn0004009", "reference_id": "PMID:12345"}
    assert len(output.appended_findings) == 2
    assert all(finding.details["linkml_alignment"] == "not_assessed" for finding in output.appended_findings)
    assert output.materialized_objects == ()
