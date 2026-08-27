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
def _reset_loader_caches():
    agent_loader.reset_cache()
    prompt_loader.reset_cache()
    schema_discovery.reset_cache()
    yield
    agent_loader.reset_cache()
    prompt_loader.reset_cache()
    schema_discovery.reset_cache()


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
        "proposed_aspect": "molecular_function",
        "proposed_with_from": [],
        "proposed_qualifiers": [],
        "proposed_annotation_extensions": [],
        "proposed_negated": False,
        "primary_evidence_location": "results",
        "proposed_resolution_state": "resolved",
        "with_from_supported": True,
        "qualifiers_supported": True,
        "negation_supported": True,
        "go_term_is_catalytic_activity_or_descendant": False,
        "go_term_is_binding_or_descendant": False,
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
        INSUFFICIENT_EVIDENCE_MESSAGE,
    ):
        assert token in prompt
    assert "disease curation" in prompt
    assert "Never guess an RGD identifier" in prompt


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
                "proposed_with_from": ["RGD:partner"],
                "go_term_is_binding_or_descendant": True,
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
                "proposed_with_from": ["RGD:allele"],
            },
        ),
        (
            "supported_genetic_interaction_igi",
            {
                "evidence_basis": "genetic_interaction",
                "proposed_evidence_code": "IGI",
                "proposed_evidence_eco_curie": "ECO:0000316",
                "proposed_aspect": "biological_process",
                "proposed_with_from": ["RGD:interacting-gene"],
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
                "proposed_with_from": ["RGD:partner"],
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
            {"proposed_with_from": ["RGD:partner"]},
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
            {"evidence_basis": "ambiguous"},
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
            {"proposed_resolution_state": "unresolved"},
            ["identity_unresolved"],
        ),
        (
            "unsupported_qualifier_abstains",
            {
                "proposed_qualifiers": ["contributes_to"],
                "qualifiers_supported": False,
            },
            ["qualifier_unsupported"],
        ),
        (
            "unsupported_negated_binding_abstains",
            {
                "proposed_negated": True,
                "negation_supported": False,
                "go_term_is_binding_or_descendant": True,
                "proposed_annotation_extensions": ["occurs_in(CL:0000000)"],
            },
            [
                "negation_unsupported",
                "negated_binding_disallowed",
                "negated_extension_disallowed",
            ],
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
