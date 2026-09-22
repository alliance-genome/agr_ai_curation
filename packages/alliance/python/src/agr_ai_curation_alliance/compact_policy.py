"""Compact scientific judgments for the existing approved RGD GO policy."""

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

from pydantic import create_model

from src.lib.domain_packs.compact_decisions import CompactValidatorDecision, DecisionContract
from src.schemas.domain_validator import DomainValidatorBaseModel


_SCIENTIFIC_FIELDS = (
    "evidence_basis", "primary_evidence_location", "primary_evidence_record_ids",
    "identity_resolution", "ambiguity", "imp_perturbation", "imp_phenotype",
    "with_from_supported", "qualifiers_supported", "negation_supported",
    "go_term_is_catalytic_activity_or_descendant",
)


def policy_decision_contract(request, result_schema):
    """Reuse exact scientific types and the existing deterministic policy evaluator."""
    fields: dict[str, Any] = {name: (result_schema.model_fields[name].annotation,
                  deepcopy(result_schema.model_fields[name])) for name in _SCIENTIFIC_FIELDS}
    scientific = create_model("RGDGOScientificJudgment", __base__=DomainValidatorBaseModel, **fields)
    decision_schema = create_model(
        "RGDGOCompactDecision", __base__=CompactValidatorDecision,
        scientific=(scientific, ...),
    )

    def assemble_domain(payload, decision, _workspace):
        selected = request.selected_inputs
        term = selected.get("go_term")
        if not isinstance(term, dict):
            raise ValueError("Policy request requires the supplied GO term")
        facts = {
            "proposed_evidence_code": selected.get("evidence_code"),
            "proposed_evidence_eco_curie": selected.get("evidence_eco_curie"),
            "proposed_aspect": term.get("aspect"),
            "proposed_go_term_curie": term.get("curie"),
            "proposed_with_from": deepcopy(selected.get("with_from", [])),
            "proposed_qualifiers": deepcopy(selected.get("qualifiers", [])),
            "proposed_annotation_extensions": deepcopy(selected.get("annotation_extensions", [])),
            "proposed_negated": selected.get("negated"),
            "proposed_rationale": selected.get("rationale"),
            "proposed_resolution_state": selected.get("resolution_state"),
        }
        judgments = decision.scientific.model_dump()
        info = SimpleNamespace(context={"domain_validation_request": request})
        # Scientific values were validated by decision_schema. The final full
        # schema below still validates all source copies and policy consequences.
        provisional = result_schema.model_construct(**payload, **facts, **judgments)
        violations = provisional.computed_policy_violations(info)
        if decision.status != ("unresolved" if violations else "resolved"):
            raise ValueError("Scientific decision status disagrees with the computed policy outcome")
        outcome = "conflict" if violations else "success"
        additions = {
            **facts, **judgments,
            "status": "unresolved" if violations else "resolved",
            "decision": "curator_review_required" if violations else "submit_ready",
            "policy_violations": violations,
            "lookup_attempts": [{
                "provider": "agr.alliance.go", "method": "approved_rgd_evidence_policy",
                "query": {"request_id": request.request_id}, "result_count": 1,
                "outcome": outcome,
            }],
        }
        if "insufficient_primary_evidence" in violations:
            additions["curator_message"] = (
                "Insufficient primary evidence for a submit-ready RGD GO annotation; "
                "curator review is required."
            )
        return additions

    return DecisionContract(request=request, result_schema=result_schema,
                            decision_schema=decision_schema, assemble_domain=assemble_domain)
