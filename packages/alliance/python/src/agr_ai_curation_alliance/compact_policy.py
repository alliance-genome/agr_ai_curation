"""Compact scientific judgments for the existing approved RGD GO policy."""

from copy import deepcopy
from typing import Any

from pydantic import create_model

from src.lib.domain_packs.compact_decisions import CompactValidatorDecision, DecisionContract
from src.schemas.domain_validator import DomainValidatorBaseModel


_SCIENTIFIC_FIELDS = (
    "evidence_basis", "primary_evidence_location", "primary_evidence_record_ids",
    "identity_resolution", "ambiguity", "imp_perturbation", "imp_phenotype",
    "with_from_supported", "qualifiers_supported", "negation_supported",
    "go_term_is_catalytic_activity_or_descendant", "policy_violations",
)


def policy_decision_contract(request, result_schema):
    """Preserve typed model judgments while copying canonical proposal facts."""
    fields: dict[str, Any] = {name: (result_schema.model_fields[name].annotation,
                  deepcopy(result_schema.model_fields[name])) for name in _SCIENTIFIC_FIELDS}
    scientific = create_model("RGDGOScientificJudgment", __base__=DomainValidatorBaseModel, **fields)
    decision_schema = create_model(
        "RGDGOCompactDecision", __base__=CompactValidatorDecision,
        scientific=(scientific, ...),
    )

    def assemble_domain(payload, decision, _workspace):
        selected = request.selected_inputs
        facts = result_schema.proposal_facts(selected)
        judgments = decision.scientific.model_dump()
        # The workspace owns the real lookup audit; a scientific judgment is not a lookup.
        additions = {
            **facts, **judgments,
            "decision": "submit_ready" if decision.status == "resolved" else "curator_review_required",
        }
        return additions

    return DecisionContract(request=request, result_schema=result_schema,
                            decision_schema=decision_schema, assemble_domain=assemble_domain)
