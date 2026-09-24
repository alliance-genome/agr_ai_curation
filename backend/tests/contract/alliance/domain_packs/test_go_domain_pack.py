"""Contract tests for the review-only Alliance GO domain pack."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

from src.lib.curation_workspace import pipeline as workspace_pipeline
from src.lib.curation_workspace.adapter_registry import CurationAdapterRegistry
from src.lib.domain_packs.loader import (
    load_domain_fixture_pack,
    load_domain_pack_metadata,
)
from src.lib.domain_packs.materialization import (
    DomainPackMetadataReviewRowMaterializer,
    project_evidence_anchor_projections,
)
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
from src.lib.domain_packs.resolvable_values import (
    LEGACY_UNVERIFIED_SUFFIX,
    OUTCOME_LEGACY_UNVERIFIED,
    effective_payload,
    resolvable_spec_from_display,
)
from src.lib.domain_packs.validator_dispatch import (
    ValidatorRuntimeContext,
    dispatch_active_validator_bindings,
)


REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.curation_adapters import register_curation_adapters  # noqa: E402
from agr_ai_curation_alliance.domain_packs.go.legacy import (  # noqa: E402
    PREVIOUS_FORMAT_FINDING_CODE,
    previous_format_display_payload,
    validate_go_envelope,
)
from agr_ai_curation_alliance.domain_packs import (  # noqa: E402
    load_alliance_domain_pack_registry,
)


GO_PACK_DIR = REPO_ROOT / "packages" / "alliance" / "domain_packs" / "go"
GO_PACK_PATH = GO_PACK_DIR / "domain_pack.yaml"
GO_FIXTURE_PATH = GO_PACK_DIR / "fixtures" / "rgd_curator_review.yaml"


def _contracts():
    metadata = load_domain_pack_metadata(GO_PACK_PATH)
    fixtures = load_domain_fixture_pack(GO_FIXTURE_PATH)
    return metadata, fixtures


def _validator_result(request, *, resolved: bool = True):
    return {
        "status": "resolved" if resolved else "unresolved",
        "request_id": request.request_id,
        "validator_binding_id": request.validator_binding_id,
        "validator_agent": request.validator_agent.model_dump(mode="json"),
        "target": request.target.model_dump(mode="json"),
        "resolved_values": {},
        "resolved_objects": [],
        "missing_expected_fields": [],
        "candidates": [],
        "lookup_attempts": [
            {
                "provider": "agr.alliance.go",
                "method": "approved_rgd_evidence_policy",
                "query": {"profile": "ALL-862"},
                "result_count": 1,
                "outcome": "success" if resolved else "conflict",
            }
        ],
        "curator_message": (
            "RGD GO evidence policy passed."
            if resolved
            else (
                "Insufficient primary evidence for a submit-ready RGD GO "
                "annotation; curator review is required."
            )
        ),
        "explanation": (
            "The approved RGD evidence policy accepted the proposal."
            if resolved
            else "The candidate lacks primary experimental evidence."
        ),
    }


def test_go_pack_is_auto_discovered_and_review_only():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack("agr.alliance.go")

    assert pack is not None
    assert pack.metadata_path == GO_PACK_PATH
    assert pack.metadata.metadata["provider_neutral_payload"] is True

    object_definition = pack.metadata.object_definitions[0]
    assert object_definition.object_type == "GOCuratableObject"
    assert object_definition.metadata["confidence_policy"]["status"] == "omitted"
    assert "confidence" not in {
        field.field_path for field in object_definition.fields
    }
    assert object_definition.metadata["export_behavior"]["status"] == "unsupported"
    assert object_definition.metadata["submission_behavior"]["status"] == "unsupported"


def test_go_pack_owns_the_authenticated_rgd_policy_binding():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack("agr.alliance.go")
    validation_registry = DomainPackValidationRegistry.from_domain_pack(pack)

    assert pack.metadata.version == "0.2.0"
    binding = next(
        item
        for item in validation_registry.bindings
        if item.binding_id == "rgd_go_evidence_policy_validation"
    )
    assert binding.validator_agent is not None
    assert binding.validator_agent.package_id == "agr.alliance"
    assert binding.validator_agent.agent_id == "rgd_go_evidence_policy_validation"
    assert binding.required_any_active_group == ("RGD",)
    assert binding.provider_value_field_paths == ("provider_context.provider_key",)
    assert binding.allowed_provider_values == ("RGD",)
    assert binding.required is True
    assert binding.blocking is True
    assert binding.allow_opt_out is False
    assert binding.object_types == ("GOCuratableObject",)
    assert binding.field_paths == ()


def test_rgd_policy_dispatch_materializes_established_finding_and_group_trace():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack("agr.alliance.go")
    _, fixtures = _contracts()
    envelope = fixtures.fixtures[0].envelope
    requests = []

    def runner(request, *, binding):
        requests.append(request)
        return _validator_result(request)

    result = dispatch_active_validator_bindings(
        envelope,
        pack,
        runner=runner,
        runtime_context=ValidatorRuntimeContext(authenticated_groups=("RGD",)),
    )

    assert len(requests) == 1, [
        finding.model_dump(mode="json") for finding in result.appended_findings
    ]
    request = requests[0]
    assert request.validator_binding_id == "rgd_go_evidence_policy_validation"
    assert request.selected_inputs["evidence_code"]["code"] == "IDA"
    assert request.selected_inputs["go_term"]["aspect"] == "cellular_component"
    assert request.selected_inputs["provider_context"]["provider_key"] == "RGD"
    assert request.selected_inputs["evidence_quotes"][0]["verified_quote"] == (
        "Lta protein was detected in the extracellular fraction."
    )
    finding = result.appended_findings[0]
    assert finding.code == "domain_pack.validator_resolved"
    assert finding.status.value == "resolved"
    assert finding.object_ref is not None
    assert finding.object_ref.object_id == "go-candidate-rgd-lta-1"
    assert finding.details["validation_metadata"]["dispatch_context"] == {
        "authenticated_groups": ["RGD"],
        "group_context_identity": '["RGD"]',
    }
    assert result.binding_audit[0]["eligibility_reason"] == "group_scope_satisfied"


@pytest.mark.parametrize("active_groups", [(), ("MGI",), ("WB", "ZFIN")])
def test_rgd_policy_does_not_run_for_non_rgd_authenticated_groups(active_groups):
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack("agr.alliance.go")
    _, fixtures = _contracts()

    result = dispatch_active_validator_bindings(
        fixtures.fixtures[0].envelope,
        pack,
        runner=lambda *_args, **_kwargs: pytest.fail("RGD policy binding ran"),
        runtime_context=ValidatorRuntimeContext(authenticated_groups=active_groups),
    )

    assert result.validator_agent_run_count == 0
    assert result.appended_findings == ()
    assert result.binding_audit[0]["eligibility_reason"] == "group_not_satisfied"


def test_insufficient_evidence_materializes_the_approved_blocker_finding():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack("agr.alliance.go")
    _, fixtures = _contracts()

    result = dispatch_active_validator_bindings(
        fixtures.fixtures[0].envelope,
        pack,
        runner=lambda request, *, binding: _validator_result(
            request,
            resolved=False,
        ),
        runtime_context=ValidatorRuntimeContext(authenticated_groups=("RGD",)),
    )

    finding = result.appended_findings[0]
    assert finding.code == "domain_pack.validator_unresolved"
    assert finding.severity.value == "blocker"
    assert finding.status.value == "open"
    assert finding.message == (
        "Insufficient primary evidence for a submit-ready RGD GO annotation; "
        "curator review is required."
    )


def test_rgd_go_policy_binding_is_absent_from_the_disease_pack():
    registry = load_alliance_domain_pack_registry()
    disease_pack = registry.get_pack("agr.alliance.disease")
    disease_registry = DomainPackValidationRegistry.from_domain_pack(disease_pack)

    assert "rgd_go_evidence_policy_validation" not in {
        binding.binding_id for binding in disease_registry.bindings
    }


def test_go_adapter_materializes_review_rows_without_export_or_submission():
    registry = CurationAdapterRegistry()
    register_curation_adapters(registry)

    pack = registry.get_domain_pack_by_id("agr.alliance.go")
    assert pack is not None
    assert registry.get_review_row_materializer_for_domain_pack("agr.alliance.go") is not None
    assert registry.get_candidate_normalizer("go") is not None
    assert all(adapter.adapter_key != "go" for adapter in registry.export_adapters())
    assert all(
        getattr(adapter, "adapter_key", None) != "go"
        for adapter in registry.submission_transport_adapters()
    )


def test_protein_fixture_survives_review_row_candidate_and_evidence_projection():
    metadata, fixtures = _contracts()
    envelope = fixtures.fixtures[0].envelope
    payload = envelope.extracted_objects[0].payload

    rows = DomainPackMetadataReviewRowMaterializer(metadata).materialize(
        envelope,
        envelope_revision=3,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.projection_type == "go_curator_review"
    assert row.display_label == "Lta"
    assert row.secondary_label == "extracellular space"
    assert row.validation_state == "clear"
    assert payload["gene_product"]["resolution_state"] == "resolved"
    assert payload["gene_product"]["lookup_outcome"] == "matched"

    candidate_fields = {
        field.field_key: field.value
        for field in workspace_pipeline._draft_fields_from_review_row(row)
    }
    assert candidate_fields["gene_product.curie"] == "RGD:3020"
    assert candidate_fields["go_term.curie"] == "GO:0005615"
    assert candidate_fields["evidence_code.code"] == "IDA"
    assert candidate_fields["evidence_code.eco_curie"] == "ECO:0000314"
    assert candidate_fields["reference_curie.curie"] == "AGRKB:101000000400377"
    assert candidate_fields["with_from"] == []
    assert candidate_fields["qualifiers"] == []
    assert candidate_fields["annotation_extensions"] == []
    assert candidate_fields["negated"] is False
    provider_context = candidate_fields["provider_context"]
    assert isinstance(provider_context, dict)
    assert provider_context["provider_key"] == "RGD"
    assert "confidence" not in payload

    anchors = project_evidence_anchor_projections(envelope, envelope_revision=3)
    assert len(anchors) == 3
    assert {anchor.field_path for anchor in anchors} == {
        "gene_product",
        "go_term",
        "evidence_code",
    }
    assert {anchor.quote for anchor in anchors} == {
        "Lta protein was detected in the extracellular fraction."
    }


def test_ambiguous_mature_mirna_remains_unresolved_and_blocking_in_projection():
    metadata, fixtures = _contracts()
    envelope = fixtures.fixtures[1].envelope
    payload = envelope.extracted_objects[0].payload

    assert payload["gene_product"]["mention"] == "rno-miR-21-5p"
    assert payload["gene_product"]["curie"] is None
    assert payload["gene_product"]["resolution_state"] == "unresolved"
    assert payload["gene_product"]["lookup_outcome"] == "not_validated"
    assert len(payload["blocking_reasons"]) == 2
    assert envelope.validation_findings[0].severity.value == "blocker"

    rows = DomainPackMetadataReviewRowMaterializer(metadata).materialize(
        envelope,
        envelope_revision=2,
    )
    row = rows[0]
    assert row.validation_state == "blocked"
    # The unresolved gene product is labelled as paper wording, never as the item.
    assert row.display_label == "rno-miR-21-5p (paper wording)"
    candidate_fields = {
        field.field_key: field.value
        for field in workspace_pipeline._draft_fields_from_review_row(row)
    }
    assert candidate_fields["gene_product.mention"] == "rno-miR-21-5p"
    assert candidate_fields["gene_product.resolution_state"] == "unresolved"
    assert candidate_fields["gene_product.lookup_outcome"] == "not_validated"
    assert "gene_product.curie" in candidate_fields
    assert candidate_fields["gene_product.curie"] is None
    assert candidate_fields["blocking_reasons"] == payload["blocking_reasons"]
    provider_context = candidate_fields["provider_context"]
    assert isinstance(provider_context, dict)
    assert provider_context["provider_key"] == "RGD"

    anchors = project_evidence_anchor_projections(envelope, envelope_revision=2)
    assert len(anchors) == 3
    assert {anchor.field_path for anchor in anchors} == {
        "gene_product.mention",
        "go_term",
        "rationale",
    }


def _legacy_go_payload() -> dict:
    """A GO proposal stored before ALL-1283: values without paper wording or contract state."""

    _, fixtures = _contracts()
    payload = copy.deepcopy(fixtures.fixtures[0].envelope.extracted_objects[0].payload)
    payload["gene_product"] = {
        "mention": "Lta protein",
        "label": "Lta",
        "curie": "RGD:3020",
        "entity_type": "protein",
        "taxon_curie": "NCBITaxon:10116",
    }
    payload["go_term"] = {
        "curie": "GO:0005615",
        "label": "extracellular space",
        "aspect": "cellular_component",
    }
    payload["evidence_code"] = "IDA"
    payload["evidence_eco_curie"] = "ECO:0000314"
    payload["reference_curie"] = "AGRKB:101000000400377"
    payload["resolution_state"] = "resolved"
    return payload


def test_legacy_gene_product_reads_as_legacy_unverified_without_a_validator_event():
    metadata, fixtures = _contracts()
    envelope = fixtures.fixtures[0].envelope
    legacy_object = envelope.extracted_objects[0].model_copy(
        update={"payload": _legacy_go_payload()}
    )
    legacy_envelope = envelope.model_copy(update={"extracted_objects": [legacy_object]})

    rows = DomainPackMetadataReviewRowMaterializer(metadata).materialize(
        legacy_envelope, envelope_revision=1
    )

    assert rows[0].display_label == f"Lta protein {LEGACY_UNVERIFIED_SUFFIX}"
    field = next(
        item
        for item in metadata.object_definitions[0].fields
        if item.field_path == "gene_product"
    )
    spec = resolvable_spec_from_display(field.metadata["display"])
    effective = effective_payload(
        legacy_object.payload, {"gene_product": spec}, object_metadata=legacy_object.metadata
    )
    assert effective["gene_product"]["resolution_state"] == "unresolved"
    assert effective["gene_product"]["lookup_outcome"] == OUTCOME_LEGACY_UNVERIFIED
    assert effective["gene_product"]["curie"] is None


def _registered_go_materializer():
    registry = CurationAdapterRegistry()
    register_curation_adapters(registry)
    return registry.get_review_row_materializer_for_domain_pack("agr.alliance.go")


def test_previous_format_record_shows_its_stored_values_as_legacy_in_review():
    """ALL-1302 review #2: old evidence code, ECO CURIE, reference and With/From are not blank."""

    metadata, fixtures = _contracts()
    envelope = fixtures.fixtures[0].envelope
    stored = {**_legacy_go_payload(), "with_from": ["RGD:619839"], "qualifiers": ["colocalizes_with"]}
    legacy_object = envelope.extracted_objects[0].model_copy(update={"payload": stored})
    legacy_envelope = envelope.model_copy(update={"extracted_objects": [legacy_object]})

    rows = _registered_go_materializer().materialize(legacy_envelope, envelope_revision=1)

    fields = {
        field.field_key: field.value
        for field in workspace_pipeline._draft_fields_from_review_row(rows[0])
    }
    assert fields["evidence_code.mention"] == f"IDA (ECO:0000314) {LEGACY_UNVERIFIED_SUFFIX}"
    assert fields["evidence_code.code"] is None
    assert fields["evidence_code.resolution_state"] == "unresolved"
    assert fields["reference_curie.mention"] == f"AGRKB:101000000400377 {LEGACY_UNVERIFIED_SUFFIX}"
    assert fields["reference_curie.curie"] is None
    assert fields["with_from"][0]["mention"] == f"RGD:619839 {LEGACY_UNVERIFIED_SUFFIX}"
    assert fields["with_from"][0]["lookup_outcome"] == OUTCOME_LEGACY_UNVERIFIED
    assert fields["qualifiers"][0]["mention"] == f"colocalizes_with {LEGACY_UNVERIFIED_SUFFIX}"
    assert fields["qualifiers"][0]["name"] is None
    # The row label names the legacy wording; the core header rule adds its own suffix.
    assert rows[0].display_label.startswith(f"Lta protein {LEGACY_UNVERIFIED_SUFFIX}")
    assert legacy_object.payload == stored  # The stored record is never rewritten.
    assert "evidence_eco_curie" not in previous_format_display_payload(stored)


def test_previous_format_record_gets_one_clear_revalidation_finding():
    """ALL-1302 review #2: re-validating an old record says to re-run extraction."""

    _, fixtures = _contracts()
    envelope = fixtures.fixtures[0].envelope
    legacy_object = envelope.extracted_objects[0].model_copy(update={"payload": _legacy_go_payload()})
    legacy_envelope = envelope.model_copy(update={"extracted_objects": [legacy_object]})

    finding, = validate_go_envelope(legacy_envelope)
    assert finding.code == PREVIOUS_FORMAT_FINDING_CODE
    assert finding.message == "Recorded in the previous GO format; re-run extraction to validate."
    assert finding.severity.value == "blocker"
    assert validate_go_envelope(envelope) == ()


def test_previous_format_record_gets_exactly_the_one_clear_finding_on_revalidation():
    """ALL-1302 review #2 with core (g): no selector or missing-field findings beside it."""

    from src.lib.domain_packs.structural_checks import run_domain_envelope_structural_checks
    from src.lib.domain_packs.validation_findings import append_validation_findings_to_envelope

    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack("agr.alliance.go")
    _, fixtures = _contracts()
    envelope = fixtures.fixtures[0].envelope
    legacy_object = envelope.extracted_objects[0].model_copy(update={"payload": _legacy_go_payload()})
    legacy_envelope = envelope.model_copy(
        update={"extracted_objects": [legacy_object], "validation_findings": []}
    )

    # The workspace hook order: package validator, structural checks, binding dispatch.
    flagged, package_findings = append_validation_findings_to_envelope(
        legacy_envelope, validate_go_envelope(legacy_envelope)
    )
    structural = run_domain_envelope_structural_checks(flagged, pack)
    dispatched = dispatch_active_validator_bindings(
        structural.envelope,
        pack,
        runner=lambda *_args, **_kwargs: pytest.fail("A previous-format record was sent to a validator"),
        runtime_context=ValidatorRuntimeContext(authenticated_groups=("RGD",)),
    )

    findings = [*package_findings, *structural.appended_findings, *dispatched.appended_findings]
    assert [finding.code for finding in findings] == [PREVIOUS_FORMAT_FINDING_CODE]
    assert findings[0].message == "Recorded in the previous GO format; re-run extraction to validate."
    assert dispatched.validator_agent_run_count == 0


def test_every_go_value_the_builder_stages_is_declared_resolvable():
    """ALL-1302 declaration guard: core writes only into declared resolvable values (H1/F2)."""

    from agr_ai_curation_alliance.domain_packs.go.values import (
        RESOLVABLE_LIST_FIELDS,
        RESOLVABLE_VALUE_FIELDS,
    )
    from src.lib.domain_packs.resolvable_values import declared_resolvable_fields

    metadata, fixtures = _contracts()
    declared = declared_resolvable_fields(metadata, "GOCuratableObject")
    assert set(declared) == {*RESOLVABLE_VALUE_FIELDS, *RESOLVABLE_LIST_FIELDS}

    def contract_paths(node, path=""):
        if isinstance(node, list):
            for item in node:
                yield from contract_paths(item, path)
        elif isinstance(node, dict):
            if "resolution_state" in node and "lookup_outcome" in node:
                yield path
            for key, item in node.items():
                yield from contract_paths(item, f"{path}.{key}" if path else key)

    for fixture in fixtures.fixtures:
        for obj in fixture.envelope.extracted_objects:
            assert set(contract_paths(obj.payload)) <= set(declared)


def _curator_patch(envelope, object_id, field_path, *, before, value, operation="replace"):
    from src.lib.domain_envelopes.patches import EnvelopeFieldPatch, EnvelopeFieldPatchOperation

    return EnvelopeFieldPatch(
        patch_id=f"curator-field-patch:{operation}:{field_path}",
        envelope_id=envelope.envelope_id,
        expected_revision=1,
        object_id=object_id,
        operation=EnvelopeFieldPatchOperation(operation),
        field_path=field_path,
        before=before,
        value=value,
        reason="Curator override.",
    )


def _mirna_envelope():
    _, fixtures = _contracts()
    envelope = fixtures.fixtures[1].envelope
    return envelope, envelope.extracted_objects[0]


@pytest.mark.parametrize(
    ("field_path", "identity", "operation"),
    [
        ("gene_product.curie", {"curie": "RGD:2325", "label": "Mir21"}, "replace_identity"),
        ("go_term.curie", {"curie": "GO:0005654", "label": "nucleoplasm"}, "replace_identity"),
        ("evidence_code.code", {"code": "IMP", "eco_curie": "ECO:0000315"}, "replace_identity"),
        ("reference_curie.curie", {"curie": "AGRKB:101000000999999"}, "replace"),
    ],
)
def test_a_curator_identity_edit_on_a_go_value_is_a_curator_override(field_path, identity, operation):
    """ALL-1302 override note: GO identity leaves are curator-editable, and an edit is an audited override.

    Two-key values take both keys in one ``replace_identity`` patch (core 89e74a356).
    """

    from src.lib.domain_envelopes.patches import apply_curator_field_patch
    from src.lib.domain_packs.resolvable_values import CURATOR_OVERRIDE_METADATA_KEY

    pack = load_alliance_domain_pack_registry().get_pack("agr.alliance.go")
    envelope, obj = _mirna_envelope()
    value_path, _, key = field_path.rpartition(".")
    current = obj.payload[value_path]
    if operation == "replace_identity":
        before, value = {name: current.get(name) for name in identity}, identity
    else:
        before, value = current.get(key), identity[key]

    result = apply_curator_field_patch(
        envelope, pack,
        _curator_patch(envelope, obj.object_id, field_path, before=before, value=value, operation=operation),
        current_revision=1, actor_id="curator-1",
    )

    assert result.accepted, result.errors
    edited = result.envelope.extracted_objects[0]
    changed = edited.payload[value_path]
    assert {name: changed[name] for name in identity} == identity
    assert (changed["resolution_state"], changed["lookup_outcome"]) == ("resolved", "curator_override")
    assert changed["curator_override"]["actor_id"] == "curator-1"
    assert changed["mention"] == current["mention"]
    audit, = edited.metadata[CURATOR_OVERRIDE_METADATA_KEY]
    assert audit["field_path"] == field_path


def test_a_go_curator_override_needs_both_the_identifier_and_the_name():
    """Core 7df09b2c2: a single-leaf first override leaves the name empty and is rejected."""

    from src.lib.domain_envelopes.patches import apply_curator_field_patch

    pack = load_alliance_domain_pack_registry().get_pack("agr.alliance.go")
    envelope, obj = _mirna_envelope()

    result = apply_curator_field_patch(
        envelope, pack, _curator_patch(envelope, obj.object_id, "gene_product.curie", before=None, value="RGD:2325"),
        current_revision=1, actor_id="curator-1",
    )

    assert not result.accepted
    assert "Enter both the identifier and the name" in result.errors[0]


def test_a_go_whole_value_override_may_not_change_other_keys():
    """Core cbb92a769: a whole-value edit that changes gene_product.entity_type is rejected."""

    from src.lib.domain_envelopes.patches import apply_curator_field_patch

    pack = load_alliance_domain_pack_registry().get_pack("agr.alliance.go")
    envelope, obj = _mirna_envelope()
    current = obj.payload["gene_product"]

    result = apply_curator_field_patch(
        envelope, pack,
        _curator_patch(envelope, obj.object_id, "gene_product", before=current,
                       value={**current, "curie": "RGD:2325", "label": "Mir21", "entity_type": "gene"}),
        current_revision=1, actor_id="curator-1",
    )

    assert not result.accepted
    assert "only the identifier and name can be changed" in result.errors[0]


@pytest.mark.parametrize(
    ("field_path", "value"),
    [
        ("gene_product.mention", "a different wording"),
        ("go_term.resolution_state", "resolved"),
        ("evidence_code.lookup_outcome", "matched"),
        ("gene_product.entity_type", "gene"),
    ],
)
def test_go_paper_wording_and_validation_state_are_not_curator_editable(field_path, value):
    from src.lib.domain_envelopes.patches import apply_curator_field_patch

    pack = load_alliance_domain_pack_registry().get_pack("agr.alliance.go")
    _, fixtures = _contracts()
    envelope = fixtures.fixtures[1].envelope
    obj = envelope.extracted_objects[0]
    value_path, _, key = field_path.rpartition(".")

    result = apply_curator_field_patch(
        envelope, pack,
        _curator_patch(envelope, obj.object_id, field_path, before=obj.payload[value_path].get(key), value=value),
        current_revision=1, actor_id="curator-1",
    )

    assert not result.accepted


def test_go_identity_leaves_are_the_editable_fields_and_the_catalog_ignores_it():
    """Only identity leaves are editable; editable metadata is not in the export catalog."""

    from src.lib.flows.export_fields import _pack_export_fields

    metadata, _ = _contracts()
    fields = {field.field_path: field for field in metadata.object_definitions[0].fields}
    editable = {path for path, field in fields.items() if field.metadata.get("editable")}
    assert editable == {
        "gene_product.curie", "gene_product.label", "go_term.curie", "go_term.label",
        "evidence_code.code", "evidence_code.eco_curie", "reference_curie.curie",
        "with_from.curie", "qualifiers.name",
    }
    pack = load_alliance_domain_pack_registry().get_pack("agr.alliance.go")
    assert all("editable" not in str(entry) for entry in _pack_export_fields(pack))


def test_previous_format_go_records_export_through_the_registered_mapper():
    """The GO legacy display mapper is registered, so flow exports read old records like review."""

    from src.lib.flows.export_fields import PackagedExportSource

    registry = CurationAdapterRegistry()
    register_curation_adapters(registry)
    assert registry.get_legacy_display_mapper_by_id("agr.alliance.go") is not None
    mapper = registry.get_legacy_display_mapper_by_id("agr.alliance.go")
    stored = {**_legacy_go_payload(), "qualifiers": ["colocalizes_with"]}
    # The mapper only reshapes; it writes no state.
    reshaped = mapper("GOCuratableObject", stored)
    assert reshaped["evidence_code"] == {"code": "IDA", "eco_curie": "ECO:0000314"}
    assert reshaped["qualifiers"] == [{"name": "colocalizes_with"}]

    source = PackagedExportSource(load_alliance_domain_pack_registry().get_pack("agr.alliance.go"))
    source.legacy_display_mapper = mapper
    exported = source.effective_item({"object_type": "GOCuratableObject", "payload": stored, "metadata": {}})
    assert exported["payload"]["evidence_code"]["mention"] == f"IDA (ECO:0000314) {LEGACY_UNVERIFIED_SUFFIX}"
    assert exported["payload"]["reference_curie"]["lookup_outcome"] == OUTCOME_LEGACY_UNVERIFIED
    assert exported["payload"]["qualifiers"][0]["mention"] == f"colocalizes_with {LEGACY_UNVERIFIED_SUFFIX}"


def test_a_curator_edit_never_makes_a_current_go_record_the_previous_format():
    from agr_ai_curation_alliance.domain_packs.go.legacy import is_previous_format

    _, fixtures = _contracts()
    current = copy.deepcopy(fixtures.fixtures[0].envelope.extracted_objects[0].payload)
    current["with_from"] = ["RGD:619839"]  # e.g. a hand-typed list entry
    assert not is_previous_format(current)
    assert is_previous_format(_legacy_go_payload())


def test_no_overridable_go_value_has_a_protected_container():
    """Core 73c6805fb: a protected container blocks curator overrides; GO's resolvable values stay overridable."""

    from src.lib.domain_packs.resolvable_values import declared_resolvable_fields

    metadata, _ = _contracts()
    fields = {field.field_path: field for field in metadata.object_definitions[0].fields}
    for value_path in declared_resolvable_fields(metadata, "GOCuratableObject"):
        assert not fields[value_path].metadata.get("protected"), value_path


def test_a_legacy_go_value_is_overridden_from_its_review_row_stored_identity():
    """B5: the review row's stored identity is the stored one, so its override `before` matches."""

    from src.lib.domain_envelopes.patches import apply_curator_field_patch

    pack = load_alliance_domain_pack_registry().get_pack("agr.alliance.go")
    _, fixtures = _contracts()
    envelope = fixtures.fixtures[0].envelope
    legacy_object = envelope.extracted_objects[0].model_copy(update={"payload": _legacy_go_payload()})
    legacy_envelope = envelope.model_copy(update={"extracted_objects": [legacy_object]})

    [row] = _registered_go_materializer().materialize(legacy_envelope, envelope_revision=1)
    [reading] = [
        value
        for field in row.metadata["workspace_fields"]
        if field.get("resolution")
        for value in field["resolution"]["values"]
        if value["value_path"] == "gene_product"
    ][:1]
    assert reading["lookup_outcome"] == OUTCOME_LEGACY_UNVERIFIED
    assert (reading["stored_identity"]["curie"], reading["stored_identity"]["label"]) == ("RGD:3020", "Lta")

    identity = {**reading["stored_identity"], "curie": "RGD:3020", "label": "Lta"}
    result = apply_curator_field_patch(
        legacy_envelope, pack,
        _curator_patch(legacy_envelope, legacy_object.object_id, "gene_product.curie",
                       before=reading["stored_identity"], value=identity, operation="replace_identity"),
        current_revision=1, actor_id="curator-1",
    )
    assert result.accepted, result.errors
    gene_product = result.envelope.extracted_objects[0].payload["gene_product"]
    assert (gene_product["lookup_outcome"], gene_product["mention"]) == ("curator_override", "Lta protein")
