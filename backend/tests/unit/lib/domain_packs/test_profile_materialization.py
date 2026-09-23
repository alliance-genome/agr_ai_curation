"""Profile write-back closes every channel before one whole-record commit."""

from dataclasses import replace

import pytest

from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.materialization import ValidatorResultMaterializationInput
from src.lib.domain_packs.profile_materialization import materialize_profile_validator_results
from src.lib.domain_packs.validator_dispatch import _apply_validator_evidence_updates_to_envelope
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


# --- KANBAN-1773 / ALL-1252 -------------------------------------------------
# Regression for the production failure in trace 85378ad639863ac89a63b1e0c0d929b4
# (Sentry 2a6c928f92464e14bc0d5b754290814d, v0.9.17 / 249f20c3b).
#
# A validator that re-records existing evidence rewrites the shared request dicts
# in place (record_evidence.py:1144-1147,1203-1204), stamping the workspace keys
# `status`, `span_ids` and `updated_at`. The dispatch write-back then deep-copies
# those raw dicts into the canonical envelope with no projection
# (validator_dispatch.py:835-850), and the strict envelope re-validation rejects
# them because EvidenceRecord sets extra='forbid' (base.py:214).


def _clean_evidence_record():
    """Evidence as the pack materializers write it: declared fields only."""
    return {
        "evidence_record_id": "evidence-24d4a4973cd8d45f",
        "entity": "Adgrl1",
        "verified_quote": "The allele was generated by CRISPR mutagenesis.",
        "page": 3,
        "section": "Results",
        "chunk_id": "chunk-1",
        "source_span_ids": ["span-1", "span-2"],
    }


def _revalidated_workspace_record():
    """The same record after a validator re-recorded it through record_evidence."""
    return {
        "status": "verified",
        "entity": "Adgrl1",
        "span_ids": ["span-1", "span-2"],
        "source_span_ids": ["span-1", "span-2"],
        "verified_quote": "The allele was generated by CRISPR mutagenesis.",
        "page": 3,
        "section": "Results",
        "chunk_id": "chunk-1",
        "evidence_record_id": "evidence-24d4a4973cd8d45f",
        "updated_at": "2026-09-18T12:31:54+00:00",
    }


def _items_with_validator_evidence(source, context, records):
    items = results(source, context, [{"identifier": "EX:1"}])
    return [
        ValidatorResultMaterializationInput(
            item.match,
            item.request.model_copy(update={"evidence": records}, deep=True),
            item.result,
        )
        for item in items
    ]


def test_validator_evidence_writeback_keeps_envelope_canonical(example):
    source, context = prepared(example)
    source.metadata["extraction_metadata"]["evidence_records"] = [_clean_evidence_record()]
    items = _items_with_validator_evidence(source, context, [_revalidated_workspace_record()])

    updated = _apply_validator_evidence_updates_to_envelope(source, items)

    # The canonical envelope must survive the write-back. Today it does not:
    # metadata.evidence_records[0].status/.span_ids/.updated_at are extra_forbidden.
    materialize_profile_validator_results(updated, context, items)


def test_validator_evidence_writeback_preserves_provenance(example):
    source, context = prepared(example)
    source.metadata["extraction_metadata"]["evidence_records"] = [_clean_evidence_record()]
    items = _items_with_validator_evidence(source, context, [_revalidated_workspace_record()])

    updated = _apply_validator_evidence_updates_to_envelope(source, items)

    written = updated.metadata["extraction_metadata"]["evidence_records"][0]
    assert written["source_span_ids"] == ["span-1", "span-2"]
    assert written["verified_quote"] == "The allele was generated by CRISPR mutagenesis."
    assert written["chunk_id"] == "chunk-1"
    assert "span_ids" not in written
    assert "status" not in written
    assert "updated_at" not in written


# --- KANBAN-1773: integrity failures are not curator configuration errors ----


def test_structural_failure_raises_integrity_not_conformance(example):
    """An internal evidence contamination must not be reported as a curator error."""
    from src.lib.agent_studio.profile_conformance import (
        EnvelopeIntegrityError,
        ProfileConformanceError,
    )

    source, context = prepared(example)
    # Contaminate directly, bypassing the write-back, to exercise the classifier.
    source.metadata["extraction_metadata"]["evidence_records"] = [
        _revalidated_workspace_record()
    ]

    with pytest.raises(EnvelopeIntegrityError) as exc:
        materialize_profile_validator_results(
            source, context, results(source, context, [{"identifier": "EX:1"}])
        )

    assert not isinstance(exc.value, ProfileConformanceError)
    # It must not accuse the curator's Output Structure; it must reassure them.
    assert str(exc.value) != "Record does not conform to its saved output structure"
    assert "does not need to change" in str(exc.value)


def test_integrity_error_escapes_value_error_handlers():
    """Broad ValueError/ProfileConformanceError catches must not reclassify it."""
    from src.lib.agent_studio.profile_conformance import (
        EnvelopeIntegrityError,
        ProfileConformanceError,
    )

    assert not issubclass(EnvelopeIntegrityError, ValueError)
    assert not issubclass(EnvelopeIntegrityError, ProfileConformanceError)


def test_integrity_error_carries_structured_diagnostics(example):
    from src.lib.agent_studio.profile_conformance import EnvelopeIntegrityError

    source, context = prepared(example)
    source.metadata["extraction_metadata"]["evidence_records"] = [
        _revalidated_workspace_record()
    ]

    with pytest.raises(EnvelopeIntegrityError) as exc:
        materialize_profile_validator_results(
            source, context, results(source, context, [{"identifier": "EX:1"}])
        )

    paths = {issue["field_path"] for issue in exc.value.issues}
    assert any(path.endswith(".status") for path in paths)
    assert any(path.endswith(".span_ids") for path in paths)
    assert any(path.endswith(".updated_at") for path in paths)


def test_genuine_profile_violation_keeps_curator_wording(example):
    """A real Output Structure violation must keep its existing message."""
    from src.lib.agent_studio.profile_conformance import ProfileConformanceError

    source, context = prepared(example)
    source.extracted_objects[0].payload["attributes"]["paper_name"] = 12345

    with pytest.raises(ProfileConformanceError) as exc:
        materialize_profile_validator_results(
            source, context, results(source, context, [{"identifier": "EX:1"}])
        )

    assert str(exc.value) == "Record does not conform to its saved output structure"


# --- ALL-1302: write-back into a profile's resolvable value --------------------

def resolvable(example):
    raw, cap, pack = example
    raw["fields"] = [{"key": "gene", "required": True, "value_schema": {"kind": "object", "fields": [
        {"key": "mention", "required": True, "value_schema": {"kind": "string"}},
        {"key": "gene_id", "value_schema": {"kind": "string"}},
    ]}}]
    raw["validator_mappings"][0].update(inputs={"mention": {"field_path": "attributes.gene.mention"}},
                                        outputs={"identifier": "attributes.gene.gene_id"})
    staged = {"gene": {"mention": "daf-16", "gene_id": None, "resolution_state": "unresolved",
                       "lookup_outcome": "not_validated", "validator_explanation": "Not validated yet."}}
    return prepared(example, attributes=staged)


def test_resolved_result_marks_the_value_resolved_and_keeps_its_paper_wording(example):
    source, context = resolvable(example)
    output = materialize_profile_validator_results(source, context, results(source, context, [{"identifier": "EX:1"}]))
    assert output.envelope.extracted_objects[0].payload["attributes"]["gene"] == {
        "mention": "daf-16", "gene_id": "EX:1", "resolution_state": "resolved", "lookup_outcome": "matched",
        "validator_explanation": "Fixture lookup", "validator_curator_message": None,
    }
    assert output.appended_findings[0].details["materialization"] == "accepted"


def test_unresolved_result_records_why_without_touching_identity_or_wording(example):
    source, context = resolvable(example)
    output = materialize_profile_validator_results(
        source, context, results(source, context, [{}], status="unresolved"))
    gene = output.envelope.extracted_objects[0].payload["attributes"]["gene"]
    assert gene == {"mention": "daf-16", "gene_id": None, "resolution_state": "unresolved",
                    "lookup_outcome": "not_found", "validator_explanation": "Fixture lookup",
                    "validator_curator_message": None}
    assert output.appended_findings[0].code == "domain_pack.validator_unresolved"


def test_resolved_result_without_its_identity_stays_unresolved(example):
    source, context = resolvable(example)
    output = materialize_profile_validator_results(source, context, results(source, context, [{}]))
    gene = output.envelope.extracted_objects[0].payload["attributes"]["gene"]
    assert (gene["gene_id"], gene["resolution_state"], gene["lookup_outcome"]) == (
        None, "unresolved", "missing_expected_result_field")


def test_profile_write_back_is_the_coverage_the_legacy_rule_reads(example):
    """ALL-1302 with core (h): the audit event profile write-back records covers the value it wrote."""

    from src.lib.domain_packs.resolvable_values import validator_event_covers

    source, context = resolvable(example)
    output = materialize_profile_validator_results(source, context, results(source, context, [{"identifier": "EX:1"}]))
    metadata = output.envelope.extracted_objects[0].metadata
    assert validator_event_covers(metadata, "attributes.gene")
    assert not validator_event_covers(metadata, "attributes.other")


def test_validator_overrules_an_earlier_resolution_keeping_its_identity_as_overruled(example):
    """ALL-1302 with core aadd93a03: re-validation that comes back unresolved demotes the value."""

    source, context = resolvable(example)
    resolved = materialize_profile_validator_results(
        source, context, results(source, context, [{"identifier": "EX:1"}])).envelope
    output = materialize_profile_validator_results(
        resolved, context, results(resolved, context, [{}], status="unresolved"))

    gene = output.envelope.extracted_objects[0].payload["attributes"]["gene"]
    assert gene == {"mention": "daf-16", "gene_id": None, "overruled_gene_id": "EX:1",
                    "resolution_state": "unresolved", "lookup_outcome": "not_found",
                    "validator_explanation": "Fixture lookup", "validator_curator_message": None}
    context.profile.require_attributes(output.envelope.extracted_objects[0].payload["attributes"])


def test_a_curator_override_stands_and_a_disagreeing_validator_adds_a_warning(example):
    """ALL-1302 with core ec1c320c6: profile write-back never changes a curator override."""

    source, context = resolvable(example)
    attributes, _ = context.profile.apply_curator_edit(
        source.extracted_objects[0].payload["attributes"], "attributes.gene.gene_id", "EX:7",
        actor_id="curator-1", at="2026-09-23T20:00:00+00:00",
    )
    source.extracted_objects[0].payload["attributes"] = attributes
    overridden = attributes["gene"]

    output = materialize_profile_validator_results(source, context, results(source, context, [{"identifier": "EX:1"}]))
    assert output.envelope.extracted_objects[0].payload["attributes"]["gene"] == overridden
    codes = [finding.code for finding in output.appended_findings]
    assert codes == ["domain_pack.curator_override", "domain_pack.validator_disagrees_with_curator_override"]
    assert "EX:1" in output.appended_findings[1].message

    agreeing = materialize_profile_validator_results(source, context, results(source, context, [{"identifier": "EX:7"}]))
    assert [finding.code for finding in agreeing.appended_findings] == ["domain_pack.curator_override"]


def test_one_disagreement_per_value_ignoring_empty_slots_with_the_validator_words():
    """ALL-1302 core review 2-4: grouped per value, empty slots skipped, curator message and revision kept."""

    from types import SimpleNamespace

    from src.lib.domain_packs.profile_materialization import _override_disagreements
    from src.schemas.domain_envelope import CuratableObjectEnvelope

    override = {"mention": "daf-16", "gene_id": "EX:7", "symbol": "daf-16x", "resolution_state": "resolved",
                "lookup_outcome": "curator_override"}
    item = SimpleNamespace(
        request=SimpleNamespace(expected_result_fields={
            "curie": "attributes.gene.gene_id", "symbol": "attributes.gene.symbol", "taxon": "attributes.gene.taxon"}),
        result=SimpleNamespace(status="resolved", resolved_values={"curie": "EX:1", "symbol": "daf-16", "taxon": ""},
                               validator_binding_id="lookup", request_id="request-1",
                               explanation="Exact symbol match.", curator_message="Check the symbol."),
    )
    overrides = {"attributes.gene.gene_id": override, "attributes.gene.symbol": override,
                 "attributes.gene.taxon": override}
    target = CuratableObjectEnvelope(object_type="generic_object", object_id="one", payload={})

    finding, = _override_disagreements(item, overrides, target, source_envelope_revision=4)

    assert finding.message == ("Validator disagrees with the curator override: "
                               "it resolved gene_id 'EX:1', symbol 'daf-16'.")
    assert finding.field_ref.field_path == "attributes.gene"
    assert finding.details["validator_curator_message"] == "Check the symbol."
    assert finding.details["source_envelope_revision"] == 4
