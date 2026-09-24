"""RGD GO evidence-policy validator bundle and matrix tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.lib.config import agent_loader, prompt_loader, schema_discovery
from src.lib.domain_packs.validator_dispatch import (
    _validator_result_finalization_feedback,
)
from src.schemas.domain_validator import (
    DomainValidationRequest,
    DomainValidatorResultBase,
)

from ..packages import find_repo_root


REPO_ROOT = find_repo_root(Path(__file__))
REPO_PACKAGES_DIR = REPO_ROOT / "packages"
AGENT_DIR = REPO_PACKAGES_DIR / "alliance" / "agents" / "rgd_go_evidence_policy"
INSUFFICIENT_EVIDENCE_MESSAGE = (
    "Insufficient primary evidence for a submit-ready RGD GO annotation; "
    "curator review is required."
)


@pytest.fixture(autouse=True)
def _reset_loader_caches(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO_PACKAGES_DIR / "alliance" / "python" / "src"))
    agent_loader.reset_cache()
    prompt_loader.reset_cache()
    schema_discovery.reset_cache()
    yield
    agent_loader.reset_cache()
    prompt_loader.reset_cache()
    schema_discovery.reset_cache()


def _with_from_entry(curie):
    """A With/From entry as the GO candidate stores it (ALL-1283)."""

    return {
        "mention": curie.removeprefix("RGD:"),
        "curie": curie,
        "resolution_state": "resolved",
        "lookup_outcome": "matched",
        "validator_explanation": None,
    }


def _qualifier_entry(name):
    """A qualifier as the GO candidate stores it once the builder matched it (ALL-1302)."""

    return {
        "mention": name.replace("_", " "),
        "name": name,
        "resolution_state": "resolved",
        "lookup_outcome": "matched",
        "validator_explanation": None,
    }


def _result_payload(**overrides):
    violations = list(overrides.pop("policy_violations", []))
    insufficient = "insufficient_primary_evidence" in violations
    payload = {
        "status": "unresolved" if violations else "resolved",
        "request_id": "rgd-go-policy-request-1",
        "validator_binding_id": "rgd_go_evidence_policy_validation",
        "validator_agent": {
            "package_id": "agr.alliance",
            "agent_id": "rgd_go_evidence_policy_validation",
        },
        "target": {
            "domain_pack_id": "agr.alliance.go",
            "object_type": "GOCuratableObject",
            "expected_fields": [],
            "input_values": {},
        },
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
                "outcome": "conflict" if violations else "success",
            }
        ],
        "curator_message": (
            INSUFFICIENT_EVIDENCE_MESSAGE
            if insufficient
            else ("Curator review is required." if violations else "Policy passed.")
        ),
        "explanation": "Table-driven approved RGD GO policy fixture.",
        "decision": (
            "curator_review_required" if violations else "submit_ready"
        ),
        "evidence_basis": "direct_assay",
        "proposed_evidence_code": "IDA",
        "proposed_evidence_eco_curie": "ECO:0000314",
        "proposed_evidence_code_resolution_state": "resolved",
        "proposed_aspect": "molecular_function",
        "proposed_go_term_curie": "GO:0003674",
        "proposed_go_term_resolution_state": "resolved",
        "proposed_reference_resolution_state": "resolved",
        "proposed_with_from": [],
        "proposed_qualifiers": [],
        "proposed_annotation_extensions": [],
        "proposed_negated": False,
        "primary_evidence_location": "results",
        "primary_evidence_record_ids": ["evidence-1"],
        "proposed_rationale": "A direct assay supports the proposed annotation.",
        "proposed_resolution_state": "resolved",
        "identity_resolution": "resolved",
        "ambiguity": None,
        "imp_perturbation": None,
        "imp_phenotype": None,
        "with_from_supported": True,
        "qualifiers_supported": True,
        "negation_supported": True,
        "go_term_is_catalytic_activity_or_descendant": False,
        "policy_violations": violations,
    }
    payload.update(overrides)
    return payload


def test_rgd_go_evidence_policy_agent_bundle_loads_as_typed_non_routable_validator(
    monkeypatch,
):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))

    agents = agent_loader.load_agent_definitions(force_reload=True)
    schemas = schema_discovery.discover_agent_schemas(force_reload=True)

    agent = agents["rgd_go_evidence_policy_validation"]
    assert agent.folder_name == "rgd_go_evidence_policy"
    assert agent.category == "Validation"
    assert agent.tools == []
    assert agent.supervisor_routing.enabled is False
    assert agent.output_schema == "RGDGOEvidencePolicyValidationResult"

    schema = schemas["RGDGOEvidencePolicyValidationResult"]
    assert any(
        base.__qualname__ == DomainValidatorResultBase.__qualname__
        for base in type.mro(schema)
    )
    assert "policy_violations" in schema.model_fields


def test_rgd_go_evidence_policy_prompt_encodes_only_the_approved_profile():
    prompt = yaml.safe_load(
        (AGENT_DIR / "prompt.yaml").read_text(encoding="utf-8")
    )["content"]

    for token in (
        "direct_assay` -> IDA / ECO:0000314",
        "physical_interaction` -> IPI / ECO:0000353",
        "mutant_phenotype` -> IMP / ECO:0000315",
        "genetic_interaction` -> IGI / ECO:0000316",
        "expression_pattern` -> IEP / ECO:0000270",
        "authenticated-group dispatcher",
        "Introduction and Discussion",
        "must not carry an annotation extension",
        "this restriction does not extend to their child terms",
        INSUFFICIENT_EVIDENCE_MESSAGE,
    ):
        assert token in prompt
    assert "disease curation" in prompt
    assert "Never guess an RGD identifier" in prompt
    # The identity validators run first; the policy reads what they confirmed.
    assert "the matched identity when one was confirmed" not in prompt
    assert "`proposed_curie`, a claim and never the identity" in prompt
    assert "what the identity validators confirmed before you run" in prompt


@pytest.mark.parametrize(
    ("fixture_name", "overrides"),
    [
        (
            "ago1_ago2_direct_activity_ida",
            {},
        ),
        (
            "ago1_ago2_physical_interaction_ipi",
            {
                "evidence_basis": "physical_interaction",
                "proposed_evidence_code": "IPI",
                "proposed_evidence_eco_curie": "ECO:0000353",
                "proposed_with_from": [_with_from_entry("RGD:partner")],
                "proposed_go_term_curie": "GO:0005515",
            },
        ),
        (
            "cttn_direct_localization_ida",
            {"proposed_aspect": "cellular_component"},
        ),
        (
            "cttn_perturbation_imp",
            {
                "evidence_basis": "mutant_phenotype",
                "proposed_evidence_code": "IMP",
                "proposed_evidence_eco_curie": "ECO:0000315",
                "proposed_aspect": "biological_process",
                "proposed_with_from": [_with_from_entry("RGD:allele")],
                "proposed_rationale": (
                    "Cttn knockdown caused the reduced migration phenotype."
                ),
                "imp_perturbation": "Cttn knockdown",
                "imp_phenotype": "reduced migration phenotype",
            },
        ),
        (
            "supported_genetic_interaction_igi",
            {
                "evidence_basis": "genetic_interaction",
                "proposed_evidence_code": "IGI",
                "proposed_evidence_eco_curie": "ECO:0000316",
                "proposed_aspect": "biological_process",
                "proposed_with_from": [_with_from_entry("RGD:interacting-gene")],
            },
        ),
        (
            "marker_expression_biological_process_iep",
            {
                "evidence_basis": "expression_pattern",
                "proposed_evidence_code": "IEP",
                "proposed_evidence_eco_curie": "ECO:0000270",
                "proposed_aspect": "biological_process",
            },
        ),
        (
            "explicit_supported_negation",
            {
                "proposed_aspect": "biological_process",
                "proposed_negated": True,
            },
        ),
        (
            "supported_negation_to_binding_descendant",
            {
                "proposed_aspect": "biological_process",
                "proposed_go_term_curie": "GO:0044877",
                "proposed_negated": True,
            },
        ),
    ],
)
def test_approved_submit_ready_rows_validate(fixture_name, overrides, monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema = schema_discovery.discover_agent_schemas(force_reload=True)[
        "RGDGOEvidencePolicyValidationResult"
    ]

    result = schema.model_validate(_result_payload(**overrides))

    assert result.status == "resolved", fixture_name
    assert result.decision == "submit_ready", fixture_name
    assert result.policy_violations == [], fixture_name


@pytest.mark.parametrize(
    ("fixture_name", "overrides", "violations"),
    [
        (
            "ago1_ago2_interaction_does_not_support_catalysis",
            {
                "evidence_basis": "physical_interaction",
                "proposed_evidence_code": "IPI",
                "proposed_evidence_eco_curie": "ECO:0000353",
                "proposed_with_from": [_with_from_entry("RGD:partner")],
                "go_term_is_catalytic_activity_or_descendant": True,
            },
            ["ipi_catalytic_activity_unsupported"],
        ),
        (
            "ipi_requires_resolvable_partner",
            {
                "evidence_basis": "physical_interaction",
                "proposed_evidence_code": "IPI",
                "proposed_evidence_eco_curie": "ECO:0000353",
            },
            ["with_from_required"],
        ),
        (
            "ida_forbids_with_from",
            {"proposed_with_from": [_with_from_entry("RGD:partner")]},
            ["with_from_forbidden"],
        ),
        (
            "igi_requires_interacting_gene",
            {
                "evidence_basis": "genetic_interaction",
                "proposed_evidence_code": "IGI",
                "proposed_evidence_eco_curie": "ECO:0000316",
                "proposed_aspect": "biological_process",
            },
            ["with_from_required"],
        ),
        (
            "marker_expression_molecular_function_abstains",
            {
                "evidence_basis": "expression_pattern",
                "proposed_evidence_code": "IEP",
                "proposed_evidence_eco_curie": "ECO:0000270",
            },
            ["iep_non_biological_process"],
        ),
        (
            "marker_expression_cellular_component_abstains",
            {
                "evidence_basis": "expression_pattern",
                "proposed_evidence_code": "IEP",
                "proposed_evidence_eco_curie": "ECO:0000270",
                "proposed_aspect": "cellular_component",
            },
            ["iep_non_biological_process"],
        ),
        (
            "multiple_plausible_codes_abstain",
            {
                "evidence_basis": "ambiguous",
                "ambiguity": "IDA and IMP are both supported by the supplied evidence.",
                "candidates": [
                    {"value": "IDA", "label": "direct assay"},
                    {"value": "IMP", "label": "mutant phenotype"},
                ],
            },
            ["ambiguous_evidence"],
        ),
        (
            "discussion_only_is_insufficient",
            {
                "evidence_basis": "insufficient",
                "primary_evidence_location": "discussion",
            },
            ["insufficient_primary_evidence"],
        ),
        (
            "mature_product_multiple_loci_abstains",
            {
                "proposed_resolution_state": "unresolved",
                "identity_resolution": "one_to_many",
                "ambiguity": "The mature product maps to two supported precursor loci.",
                "candidates": [
                    {"value": "RGD:1001", "label": "precursor locus 1"},
                    {"value": "RGD:1002", "label": "precursor locus 2"},
                ],
            },
            ["identity_unresolved"],
        ),
        (
            "unsupported_qualifier_abstains",
            {
                "proposed_qualifiers": [_qualifier_entry("contributes_to")],
                "qualifiers_supported": False,
            },
            ["qualifier_unsupported"],
        ),
        (
            "unsupported_negated_binding_abstains",
            {
                "proposed_negated": True,
                "negation_supported": False,
                "proposed_go_term_curie": "GO:0005515",
                "proposed_annotation_extensions": ["occurs_in(CL:0000000)"],
            },
            [
                "negation_unsupported",
                "negated_binding_disallowed",
                "negated_extension_disallowed",
            ],
        ),
        (
            "supported_not_is_forbidden_on_direct_binding_root",
            {
                "proposed_negated": True,
                "proposed_go_term_curie": "GO:0005488",
            },
            ["negated_binding_disallowed"],
        ),
    ],
)
def test_approved_abstention_rows_require_curator_review(
    fixture_name, overrides, violations, monkeypatch
):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema = schema_discovery.discover_agent_schemas(force_reload=True)[
        "RGDGOEvidencePolicyValidationResult"
    ]

    result = schema.model_validate(
        _result_payload(policy_violations=violations, **overrides)
    )

    assert result.status == "unresolved", fixture_name
    assert result.decision == "curator_review_required", fixture_name
    assert result.policy_violations == violations, fixture_name
    if "insufficient_primary_evidence" in violations:
        assert result.curator_message == INSUFFICIENT_EVIDENCE_MESSAGE


def test_policy_schema_rejects_a_permissive_or_misordered_decision(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema = schema_discovery.discover_agent_schemas(force_reload=True)[
        "RGDGOEvidencePolicyValidationResult"
    ]

    with pytest.raises(ValidationError, match="policy_violations must exactly match"):
        schema.model_validate(
            _result_payload(
                evidence_basis="physical_interaction",
                proposed_evidence_code="IPI",
                proposed_evidence_eco_curie="ECO:0000353",
                policy_violations=[],
            )
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"evidence_basis": "ambiguous", "policy_violations": ["ambiguous_evidence"]},
        {
            "proposed_resolution_state": "unresolved",
            "identity_resolution": "one_to_many",
            "policy_violations": ["identity_unresolved"],
        },
    ],
)
def test_candidate_bearing_abstentions_reject_missing_candidates(
    overrides, monkeypatch
):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema = schema_discovery.discover_agent_schemas(force_reload=True)[
        "RGDGOEvidencePolicyValidationResult"
    ]

    with pytest.raises(ValidationError, match="candidate"):
        schema.model_validate(_result_payload(**overrides))


def test_imp_requires_perturbation_and_phenotype_in_rationale(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema = schema_discovery.discover_agent_schemas(force_reload=True)[
        "RGDGOEvidencePolicyValidationResult"
    ]
    payload = _result_payload(
        evidence_basis="mutant_phenotype",
        proposed_evidence_code="IMP",
        proposed_evidence_eco_curie="ECO:0000315",
        proposed_aspect="biological_process",
        imp_perturbation="Cttn knockdown",
        imp_phenotype="reduced migration phenotype",
        policy_violations=["insufficient_primary_evidence"],
    )

    result = schema.model_validate(payload)

    assert result.status == "unresolved"
    assert result.curator_message == INSUFFICIENT_EVIDENCE_MESSAGE


def _selected_inputs_for_result(payload):
    return {
        "go_term": {
            "mention": "the proposed process",
            "curie": payload["proposed_go_term_curie"],
            "aspect": payload["proposed_aspect"],
            "resolution_state": payload["proposed_go_term_resolution_state"],
        },
        "evidence_code": {
            "mention": "ida",
            "code": payload["proposed_evidence_code"],
            "eco_curie": payload["proposed_evidence_eco_curie"],
            "resolution_state": payload["proposed_evidence_code_resolution_state"],
        },
        "reference_curie": {
            "mention": "PMID:12345678",
            "curie": "AGRKB:101000000400377",
            "resolution_state": payload["proposed_reference_resolution_state"],
        },
        "with_from": payload["proposed_with_from"],
        "qualifiers": payload["proposed_qualifiers"],
        "annotation_extensions": payload["proposed_annotation_extensions"],
        "negated": payload["proposed_negated"],
        "rationale": payload["proposed_rationale"],
        "resolution_state": payload["proposed_resolution_state"],
        "evidence_quotes": [
            {
                "evidence_record_id": "evidence-1",
                "verified_quote": "A direct assay supports the proposed annotation.",
            }
        ],
    }


@pytest.mark.parametrize("basis, expected", [
    ("direct_assay", []),
    ("insufficient", ["insufficient_primary_evidence"]),
    ("physical_interaction", ["evidence_code_mismatch", "eco_mapping_mismatch", "with_from_required"]),
])
def test_compact_policy_assembles_request_facts_and_computes_consequences(monkeypatch, basis, expected):
    from agr_ai_curation_alliance.compact_policy import policy_decision_contract, _SCIENTIFIC_FIELDS
    from src.lib.domain_packs.compact_decisions import ValidatorDecisionWorkspace

    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema = schema_discovery.discover_agent_schemas(force_reload=True)["RGDGOEvidencePolicyValidationResult"]
    original = _result_payload()
    request = DomainValidationRequest(
        request_id=original["request_id"], validator_binding_id=original["validator_binding_id"],
        validator_agent=original["validator_agent"], target=original["target"],
        selected_inputs=_selected_inputs_for_result(original),
    )
    contract = policy_decision_contract(request, schema)
    scientific = {name: original[name] for name in _SCIENTIFIC_FIELDS}
    scientific["evidence_basis"] = basis
    decision = contract.decision_schema(
        request_id=request.request_id, status="unresolved" if expected else "resolved",
        explanation="Assessment of the supplied evidence.", scientific=scientific,
    )
    result = ValidatorDecisionWorkspace([contract]).assemble(decision)
    assert result.policy_violations == expected
    assert result.status == ("unresolved" if expected else "resolved")
    assert result.proposed_go_term_curie == original["proposed_go_term_curie"]
    assert result.proposed_rationale == original["proposed_rationale"]
    assert result.primary_evidence_record_ids == ["evidence-1"]
    assert result.lookup_attempts[0].method == "approved_rgd_evidence_policy"
    assert result.lookup_attempts[0].outcome == ("conflict" if expected else "success")
    if basis == "insufficient":
        assert result.curator_message == INSUFFICIENT_EVIDENCE_MESSAGE


def test_compact_policy_reads_with_from_entries_and_an_unmatched_evidence_code(monkeypatch):
    """ALL-1283: With/From entries are stored objects; an unknown code has no ECO class."""

    from agr_ai_curation_alliance.compact_policy import policy_decision_contract, _SCIENTIFIC_FIELDS
    from src.lib.domain_packs.compact_decisions import ValidatorDecisionWorkspace

    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema = schema_discovery.discover_agent_schemas(force_reload=True)["RGDGOEvidencePolicyValidationResult"]
    partner = {
        "mention": "Ago2 partner",
        "curie": None,
        "resolution_state": "unresolved",
        "lookup_outcome": "not_validated",
        "validator_explanation": "Not validated yet.",
    }
    original = _result_payload(
        proposed_evidence_code=None,
        proposed_evidence_eco_curie=None,
        proposed_evidence_code_resolution_state="unresolved",
        proposed_with_from=[partner],
    )
    request = DomainValidationRequest(
        request_id=original["request_id"], validator_binding_id=original["validator_binding_id"],
        validator_agent=original["validator_agent"], target=original["target"],
        selected_inputs=_selected_inputs_for_result(original),
    )
    contract = policy_decision_contract(request, schema)
    scientific = {name: original[name] for name in _SCIENTIFIC_FIELDS}
    decision = contract.decision_schema(
        request_id=request.request_id, status="unresolved",
        explanation="Assessment of the supplied evidence.", scientific=scientific,
    )

    result = ValidatorDecisionWorkspace([contract]).assemble(decision)

    assert result.policy_violations == [
        "evidence_code_mismatch", "eco_mapping_mismatch", "with_from_forbidden",
        "evidence_code_unresolved", "with_from_unresolved",
    ]
    assert result.proposed_evidence_code is None
    assert result.proposed_evidence_eco_curie is None
    assert [entry.model_dump(mode="json", exclude_unset=True) for entry in result.proposed_with_from] == [partner]


def test_compact_policy_cannot_supply_proposal_copies_or_foreign_evidence(monkeypatch):
    from agr_ai_curation_alliance.compact_policy import policy_decision_contract, _SCIENTIFIC_FIELDS
    from src.lib.domain_packs.compact_decisions import ValidatorDecisionWorkspace

    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema = schema_discovery.discover_agent_schemas(force_reload=True)["RGDGOEvidencePolicyValidationResult"]
    original = _result_payload()
    request = DomainValidationRequest(
        request_id=original["request_id"], validator_binding_id=original["validator_binding_id"],
        validator_agent=original["validator_agent"], target=original["target"],
        selected_inputs=_selected_inputs_for_result(original),
    )
    contract = policy_decision_contract(request, schema)
    scientific = {name: original[name] for name in _SCIENTIFIC_FIELDS}
    with pytest.raises(ValidationError, match="extra_forbidden"):
        contract.decision_schema(
            request_id=request.request_id, status="resolved", explanation="Assessed.",
            scientific={**scientific, "proposed_evidence_code": "IPI"},
        )
    scientific["primary_evidence_record_ids"] = ["foreign-evidence"]
    decision = contract.decision_schema(
        request_id=request.request_id, status="resolved", explanation="Assessed.", scientific=scientific,
    )
    with pytest.raises(ValidationError, match="supplied exact evidence"):
        ValidatorDecisionWorkspace([contract]).assemble(decision)


def test_validator_finalization_applies_the_typed_policy_schema(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema = schema_discovery.discover_agent_schemas(force_reload=True)[
        "RGDGOEvidencePolicyValidationResult"
    ]
    raw_result = _result_payload()
    request = DomainValidationRequest(
        request_id=raw_result["request_id"],
        validator_binding_id=raw_result["validator_binding_id"],
        validator_agent=raw_result["validator_agent"],
        target=raw_result["target"],
        selected_inputs=_selected_inputs_for_result(raw_result),
    )

    base_feedback = _validator_result_finalization_feedback(
        raw_result,
        request=request,
    )
    typed_feedback = _validator_result_finalization_feedback(
        raw_result,
        request=request,
        result_schema=schema,
    )

    assert base_feedback.accepted_result is None
    assert typed_feedback.accepted_result is not None


@pytest.mark.parametrize(
    ("field_name", "drifted_value"),
    [
        ("proposed_evidence_code", "IPI"),
        ("proposed_evidence_eco_curie", "ECO:0000353"),
        ("proposed_aspect", "cellular_component"),
        ("proposed_go_term_curie", "GO:0005515"),
        ("proposed_with_from", [_with_from_entry("RGD:partner")]),
        ("proposed_qualifiers", [_qualifier_entry("contributes_to")]),
        ("proposed_annotation_extensions", ["occurs_in(CL:0000000)"]),
        ("proposed_negated", True),
        ("proposed_rationale", "A different rationale."),
        ("proposed_resolution_state", "unresolved"),
    ],
)
def test_typed_finalization_rejects_proposal_drift_from_canonical_request(
    field_name, drifted_value, monkeypatch
):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema = schema_discovery.discover_agent_schemas(force_reload=True)[
        "RGDGOEvidencePolicyValidationResult"
    ]
    canonical_result = _result_payload()
    request = DomainValidationRequest(
        request_id=canonical_result["request_id"],
        validator_binding_id=canonical_result["validator_binding_id"],
        validator_agent=canonical_result["validator_agent"],
        target=canonical_result["target"],
        selected_inputs=_selected_inputs_for_result(canonical_result),
    )
    drifted_result = {**canonical_result, field_name: drifted_value}

    feedback = _validator_result_finalization_feedback(
        drifted_result,
        request=request,
        result_schema=schema,
    )

    assert feedback.accepted_result is None
    assert "proposal fields must exactly copy selected_inputs" in feedback.message


def test_typed_finalization_rejects_unsupplied_exact_evidence_location(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema = schema_discovery.discover_agent_schemas(force_reload=True)[
        "RGDGOEvidencePolicyValidationResult"
    ]
    raw_result = _result_payload(primary_evidence_record_ids=["invented-evidence"])
    request = DomainValidationRequest(
        request_id=raw_result["request_id"],
        validator_binding_id=raw_result["validator_binding_id"],
        validator_agent=raw_result["validator_agent"],
        target=raw_result["target"],
        selected_inputs=_selected_inputs_for_result(_result_payload()),
    )

    feedback = _validator_result_finalization_feedback(
        raw_result,
        request=request,
        result_schema=schema,
    )

    assert feedback.accepted_result is None
    assert "primary_evidence_record_ids" in feedback.message


def test_typed_finalization_requires_imp_facts_in_selected_exact_evidence(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema = schema_discovery.discover_agent_schemas(force_reload=True)[
        "RGDGOEvidencePolicyValidationResult"
    ]
    raw_result = _result_payload(
        evidence_basis="mutant_phenotype",
        proposed_evidence_code="IMP",
        proposed_evidence_eco_curie="ECO:0000315",
        proposed_aspect="biological_process",
        proposed_rationale="Cttn knockdown caused a reduced migration phenotype.",
        imp_perturbation="Cttn knockdown",
        imp_phenotype="reduced migration phenotype",
    )
    selected_inputs = _selected_inputs_for_result(raw_result)
    selected_inputs["evidence_quotes"][0]["verified_quote"] = (
        "Cttn knockdown was performed."
    )
    request = DomainValidationRequest(
        request_id=raw_result["request_id"],
        validator_binding_id=raw_result["validator_binding_id"],
        validator_agent=raw_result["validator_agent"],
        target=raw_result["target"],
        selected_inputs=selected_inputs,
    )

    feedback = _validator_result_finalization_feedback(
        raw_result,
        request=request,
        result_schema=schema,
    )

    assert feedback.accepted_result is None
    assert "insufficient_primary_evidence" in feedback.message


def test_typed_finalization_accepts_imp_facts_in_rationale_and_exact_evidence(
    monkeypatch,
):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema = schema_discovery.discover_agent_schemas(force_reload=True)[
        "RGDGOEvidencePolicyValidationResult"
    ]
    raw_result = _result_payload(
        evidence_basis="mutant_phenotype",
        proposed_evidence_code="IMP",
        proposed_evidence_eco_curie="ECO:0000315",
        proposed_aspect="biological_process",
        proposed_rationale="Cttn knockdown caused a reduced migration phenotype.",
        imp_perturbation="Cttn knockdown",
        imp_phenotype="reduced migration phenotype",
    )
    selected_inputs = _selected_inputs_for_result(raw_result)
    selected_inputs["evidence_quotes"][0]["verified_quote"] = (
        "Cttn knockdown caused a reduced migration phenotype."
    )
    request = DomainValidationRequest(
        request_id=raw_result["request_id"],
        validator_binding_id=raw_result["validator_binding_id"],
        validator_agent=raw_result["validator_agent"],
        target=raw_result["target"],
        selected_inputs=selected_inputs,
    )

    feedback = _validator_result_finalization_feedback(
        raw_result,
        request=request,
        result_schema=schema,
    )

    assert feedback.accepted_result is not None


@pytest.mark.parametrize(
    ("unresolved", "violation"),
    [
        ({"proposed_go_term_curie": None, "proposed_go_term_resolution_state": "unresolved"},
         "go_term_unresolved"),
        ({"proposed_reference_resolution_state": "unresolved"}, "reference_unresolved"),
        ({"proposed_evidence_code": None, "proposed_evidence_eco_curie": None,
          "proposed_evidence_code_resolution_state": "unresolved"}, "evidence_code_unresolved"),
    ],
)
def test_compact_policy_never_passes_a_proposal_with_an_unresolved_value(monkeypatch, unresolved, violation):
    """ALL-1302 review #1: an unresolved GO term (null CURIE) is valid input, and blocks submit_ready."""

    from agr_ai_curation_alliance.compact_policy import policy_decision_contract, _SCIENTIFIC_FIELDS
    from src.lib.domain_packs.compact_decisions import ValidatorDecisionWorkspace

    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema = schema_discovery.discover_agent_schemas(force_reload=True)["RGDGOEvidencePolicyValidationResult"]
    original = _result_payload(**unresolved)
    request = DomainValidationRequest(
        request_id=original["request_id"], validator_binding_id=original["validator_binding_id"],
        validator_agent=original["validator_agent"], target=original["target"],
        selected_inputs=_selected_inputs_for_result(original),
    )
    contract = policy_decision_contract(request, schema)
    decision = contract.decision_schema(
        request_id=request.request_id, status="unresolved",
        explanation="Assessment of the supplied evidence.",
        scientific={name: original[name] for name in _SCIENTIFIC_FIELDS},
    )

    result = ValidatorDecisionWorkspace([contract]).assemble(decision)

    assert violation in result.policy_violations
    assert (result.status, result.decision) == ("unresolved", "curator_review_required")


def test_policy_compares_the_matched_code_not_the_paper_wording(monkeypatch):
    """ALL-1302 review #7: a lower-case mention of IDA matches through its normalised code."""

    from agr_ai_curation_alliance.compact_policy import policy_decision_contract, _SCIENTIFIC_FIELDS
    from src.lib.domain_packs.compact_decisions import ValidatorDecisionWorkspace

    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema = schema_discovery.discover_agent_schemas(force_reload=True)["RGDGOEvidencePolicyValidationResult"]
    original = _result_payload()
    selected = _selected_inputs_for_result(original)
    assert selected["evidence_code"]["mention"] == "ida"
    request = DomainValidationRequest(
        request_id=original["request_id"], validator_binding_id=original["validator_binding_id"],
        validator_agent=original["validator_agent"], target=original["target"],
        selected_inputs=selected,
    )
    contract = policy_decision_contract(request, schema)
    decision = contract.decision_schema(
        request_id=request.request_id, status="resolved",
        explanation="Assessment of the supplied evidence.",
        scientific={name: original[name] for name in _SCIENTIFIC_FIELDS},
    )

    result = ValidatorDecisionWorkspace([contract]).assemble(decision)

    assert result.proposed_evidence_code == "IDA"
    assert result.policy_violations == []


def test_policy_never_passes_a_proposal_with_an_unresolved_qualifier(monkeypatch):
    """ALL-1302: a qualifier outside the GO relation vocabulary keeps the proposal in review."""

    from agr_ai_curation_alliance.compact_policy import policy_decision_contract, _SCIENTIFIC_FIELDS
    from src.lib.domain_packs.compact_decisions import ValidatorDecisionWorkspace

    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema = schema_discovery.discover_agent_schemas(force_reload=True)["RGDGOEvidencePolicyValidationResult"]
    unmatched = {
        "mention": "strongly required for",
        "name": None,
        "resolution_state": "unresolved",
        "lookup_outcome": "not_found",
        "validator_explanation": "Not a GO relation qualifier in this workflow's qualifier vocabulary.",
    }
    original = _result_payload(proposed_qualifiers=[unmatched])
    request = DomainValidationRequest(
        request_id=original["request_id"], validator_binding_id=original["validator_binding_id"],
        validator_agent=original["validator_agent"], target=original["target"],
        selected_inputs=_selected_inputs_for_result(original),
    )
    contract = policy_decision_contract(request, schema)
    decision = contract.decision_schema(
        request_id=request.request_id, status="unresolved",
        explanation="Assessment of the supplied evidence.",
        scientific={name: original[name] for name in _SCIENTIFIC_FIELDS},
    )

    result = ValidatorDecisionWorkspace([contract]).assemble(decision)

    assert result.policy_violations == ["qualifier_unresolved"]
    assert (result.status, result.decision) == ("unresolved", "curator_review_required")


@pytest.mark.parametrize(
    "entry",
    [
        {**_with_from_entry("RGD:621255"), "proposed_curie": "RGD:621255"},
        {
            **_with_from_entry("RGD:621255"),
            "lookup_outcome": "curator_override",
            "overruled_curie": "RGD:1304619",
            "curator_override": {"actor_id": "curator-1", "previous": {}},
        },
    ],
)
def test_policy_reads_with_from_entries_as_validation_leaves_them(monkeypatch, entry):
    """A With/From entry carries the paper's ID claim and any curator override beside the identity."""

    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema = schema_discovery.discover_agent_schemas(force_reload=True)[
        "RGDGOEvidencePolicyValidationResult"
    ]

    result = schema.model_validate(
        _result_payload(
            evidence_basis="physical_interaction",
            proposed_evidence_code="IPI",
            proposed_evidence_eco_curie="ECO:0000353",
            proposed_with_from=[entry],
            proposed_go_term_curie="GO:0005515",
        )
    )

    assert result.proposed_with_from[0].curie == "RGD:621255"
    assert result.policy_violations == []


def test_a_dumped_and_revalidated_policy_result_still_copies_its_entries(monkeypatch):
    """Regression (TLC #3 eval): the compact path re-validates the assembled result from its
    JSON dump, which states every entry key as null; a table-mapped qualifier never had them."""

    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    schema = schema_discovery.discover_agent_schemas(force_reload=True)[
        "RGDGOEvidencePolicyValidationResult"
    ]
    canonical_result = _result_payload(
        proposed_qualifiers=[_qualifier_entry("located_in")],
        proposed_aspect="cellular_component",
        proposed_go_term_curie="GO:0005739",
    )
    request = DomainValidationRequest(
        request_id=canonical_result["request_id"],
        validator_binding_id=canonical_result["validator_binding_id"],
        validator_agent=canonical_result["validator_agent"],
        target=canonical_result["target"],
        selected_inputs=_selected_inputs_for_result(canonical_result),
    )
    first = schema.model_validate(canonical_result, context={"domain_validation_request": request})

    feedback = _validator_result_finalization_feedback(
        first.model_dump(mode="json"), request=request, result_schema=schema,
    )

    assert feedback.accepted_result is not None, feedback.message
