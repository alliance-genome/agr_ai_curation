"""Typed result contract for the approved RGD GO evidence policy."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import Field, StrictBool, StrictStr, ValidationInfo, model_validator

from src.schemas.domain_validator import (  # type: ignore[reportMissingImports]
    DomainValidatorResultBase,
)


RGDGOEvidenceBasis = Literal[
    "direct_assay",
    "physical_interaction",
    "mutant_phenotype",
    "genetic_interaction",
    "expression_pattern",
    "ambiguous",
    "insufficient",
]
RGDGOAspect = Literal[
    "molecular_function",
    "biological_process",
    "cellular_component",
]
RGDGOEvidenceLocation = Literal[
    "results",
    "methods",
    "figure",
    "table",
    "supplementary",
    "introduction",
    "discussion",
    "unknown",
]
RGDGOPolicyViolation = Literal[
    "ambiguous_evidence",
    "insufficient_primary_evidence",
    "identity_unresolved",
    "evidence_code_mismatch",
    "eco_mapping_mismatch",
    "with_from_required",
    "with_from_forbidden",
    "with_from_unsupported",
    "iep_non_biological_process",
    "ipi_catalytic_activity_unsupported",
    "qualifier_unsupported",
    "negation_unsupported",
    "negated_binding_disallowed",
    "negated_extension_disallowed",
]

EVIDENCE_POLICY: dict[str, tuple[str, str]] = {
    "direct_assay": ("IDA", "ECO:0000314"),
    "physical_interaction": ("IPI", "ECO:0000353"),
    "mutant_phenotype": ("IMP", "ECO:0000315"),
    "genetic_interaction": ("IGI", "ECO:0000316"),
    "expression_pattern": ("IEP", "ECO:0000270"),
}
PRIMARY_EVIDENCE_LOCATIONS = frozenset(
    {"results", "methods", "figure", "table", "supplementary"}
)
INSUFFICIENT_EVIDENCE_MESSAGE = (
    "Insufficient primary evidence for a submit-ready RGD GO annotation; "
    "curator review is required."
)


class RGDGOEvidencePolicyValidationResult(DomainValidatorResultBase):
    """One typed decision under the approved RGD specialist policy profile."""

    __envelope_class__ = True

    decision: Literal["submit_ready", "curator_review_required"] = Field(
        description="Whether the proposal passes policy or must remain in curator review"
    )
    evidence_basis: RGDGOEvidenceBasis = Field(
        description="Evidence class supported by the cited primary paper evidence"
    )
    proposed_evidence_code: StrictStr = Field(
        description="GO evidence code copied from the candidate"
    )
    proposed_evidence_eco_curie: StrictStr = Field(
        description="ECO CURIE copied from the candidate"
    )
    proposed_aspect: RGDGOAspect = Field(
        description="GO aspect copied from the candidate term"
    )
    proposed_go_term_curie: StrictStr = Field(
        description="GO CURIE copied from the candidate term"
    )
    proposed_with_from: list[StrictStr] = Field(
        description="With/From identifiers copied from the candidate"
    )
    proposed_qualifiers: list[StrictStr] = Field(
        description="Qualifiers copied from the candidate"
    )
    proposed_annotation_extensions: list[StrictStr] = Field(
        description="Annotation extensions copied from the candidate"
    )
    proposed_negated: StrictBool = Field(
        description="Negation copied from the candidate"
    )
    primary_evidence_location: RGDGOEvidenceLocation = Field(
        description="Location category for the evidence that primarily supports the proposal"
    )
    primary_evidence_record_ids: list[StrictStr] = Field(
        description="Exact supplied evidence records that support the policy decision"
    )
    proposed_rationale: StrictStr = Field(
        description="Paper-grounded rationale copied from the candidate"
    )
    proposed_resolution_state: Literal["resolved", "unresolved"] = Field(
        description="Gene-product resolution state copied from the candidate"
    )
    identity_resolution: Literal["resolved", "unresolved", "one_to_many"] = Field(
        description="Validator classification of the candidate gene-product identity"
    )
    ambiguity: StrictStr | None = Field(
        default=None,
        description="Specific unresolved ambiguity when evidence or identity has multiple candidates",
    )
    imp_perturbation: StrictStr | None = Field(
        default=None,
        description="Perturbation explicitly recorded for an IMP proposal",
    )
    imp_phenotype: StrictStr | None = Field(
        default=None,
        description="Phenotype explicitly recorded for an IMP proposal",
    )
    with_from_supported: StrictBool = Field(
        description="Whether every supplied With/From identifier is supported by the paper"
    )
    qualifiers_supported: StrictBool = Field(
        description="Whether every supplied qualifier is explicitly supported"
    )
    negation_supported: StrictBool = Field(
        description="Whether explicit paper evidence supports negation"
    )
    go_term_is_catalytic_activity_or_descendant: StrictBool = Field(
        description="Whether the GO term is catalytic activity or a descendant"
    )
    policy_violations: list[RGDGOPolicyViolation] = Field(
        description="Deterministically ordered policy violations for this proposal"
    )

    @model_validator(mode="after")
    def _enforce_approved_policy(
        self,
        info: ValidationInfo,
    ) -> "RGDGOEvidencePolicyValidationResult":
        self._enforce_canonical_request_copies(info)
        violations: list[str] = []

        if self.evidence_basis == "ambiguous":
            violations.append("ambiguous_evidence")
        elif self.evidence_basis == "insufficient":
            violations.append("insufficient_primary_evidence")
        else:
            expected_code, expected_eco = EVIDENCE_POLICY[self.evidence_basis]
            if self.proposed_evidence_code != expected_code:
                violations.append("evidence_code_mismatch")
            if self.proposed_evidence_eco_curie != expected_eco:
                violations.append("eco_mapping_mismatch")

        if self.primary_evidence_location not in PRIMARY_EVIDENCE_LOCATIONS:
            if "insufficient_primary_evidence" not in violations:
                violations.append("insufficient_primary_evidence")
        if not self.primary_evidence_record_ids and self.evidence_basis not in {
            "ambiguous",
            "insufficient",
        }:
            if "insufficient_primary_evidence" not in violations:
                violations.append("insufficient_primary_evidence")
        if self.proposed_resolution_state != "resolved":
            violations.append("identity_unresolved")

        if self.evidence_basis == "mutant_phenotype" and not self._imp_support_is_recorded(
            info
        ):
            if "insufficient_primary_evidence" not in violations:
                violations.append("insufficient_primary_evidence")

        if self.evidence_basis == "direct_assay" and self.proposed_with_from:
            violations.append("with_from_forbidden")
        elif self.evidence_basis in {"physical_interaction", "genetic_interaction"}:
            if not self.proposed_with_from:
                violations.append("with_from_required")
            elif not self.with_from_supported:
                violations.append("with_from_unsupported")
        elif self.proposed_with_from and not self.with_from_supported:
            violations.append("with_from_unsupported")

        if (
            self.evidence_basis == "expression_pattern"
            and self.proposed_aspect != "biological_process"
        ):
            violations.append("iep_non_biological_process")
        if (
            self.evidence_basis == "physical_interaction"
            and self.go_term_is_catalytic_activity_or_descendant
        ):
            violations.append("ipi_catalytic_activity_unsupported")
        if self.proposed_qualifiers and not self.qualifiers_supported:
            violations.append("qualifier_unsupported")
        if self.proposed_negated and not self.negation_supported:
            violations.append("negation_unsupported")
        if self.proposed_negated and self.proposed_go_term_curie in {
            "GO:0005488",
            "GO:0005515",
        }:
            violations.append("negated_binding_disallowed")
        if self.proposed_negated and self.proposed_annotation_extensions:
            violations.append("negated_extension_disallowed")

        if self.policy_violations != violations:
            raise ValueError(
                "policy_violations must exactly match the approved RGD GO policy: "
                f"{violations}"
            )

        expected_status = "resolved" if not violations else "unresolved"
        expected_decision = (
            "submit_ready" if not violations else "curator_review_required"
        )
        if self.status != expected_status:
            raise ValueError(
                f"status must be {expected_status!r} for the computed policy decision"
            )
        if self.decision != expected_decision:
            raise ValueError(
                f"decision must be {expected_decision!r} for the computed policy decision"
            )
        expected_attempt_outcome = "success" if not violations else "conflict"
        if not any(
            attempt.provider == "agr.alliance.go"
            and attempt.method == "approved_rgd_evidence_policy"
            and attempt.outcome == expected_attempt_outcome
            for attempt in self.lookup_attempts
        ):
            raise ValueError(
                "lookup_attempts must record the approved RGD GO policy evaluation "
                f"with outcome {expected_attempt_outcome!r}"
            )
        if self.resolved_values:
            raise ValueError("policy validation must not rewrite candidate payload values")
        if self.evidence_basis == "ambiguous" and (
            not self.candidates or not self.ambiguity or not self.ambiguity.strip()
        ):
            raise ValueError(
                "ambiguous evidence must return candidates and the unresolved ambiguity"
            )
        if self.identity_resolution == "one_to_many" and (
            self.proposed_resolution_state != "unresolved"
            or not self.candidates
            or not self.ambiguity
            or not self.ambiguity.strip()
            or any(not candidate.value.startswith("RGD:") for candidate in self.candidates)
        ):
            raise ValueError(
                "one-to-many identity must remain unresolved and list supported candidate loci"
            )
        if (self.identity_resolution == "resolved") != (
            self.proposed_resolution_state == "resolved"
        ):
            raise ValueError(
                "identity_resolution must agree with proposed_resolution_state"
            )
        if (
            "insufficient_primary_evidence" in violations
            and self.curator_message != INSUFFICIENT_EVIDENCE_MESSAGE
        ):
            raise ValueError(
                "insufficient primary evidence must use the approved curator finding message"
            )
        return self

    def _enforce_canonical_request_copies(self, info: ValidationInfo) -> None:
        context = info.context
        if not isinstance(context, Mapping):
            return
        request = context.get("domain_validation_request")
        selected_inputs = getattr(request, "selected_inputs", None)
        if not isinstance(selected_inputs, Mapping):
            return

        go_term = selected_inputs.get("go_term")
        if not isinstance(go_term, Mapping):
            raise ValueError("selected_inputs.go_term must be a mapping")
        expected_values = {
            "proposed_evidence_code": selected_inputs.get("evidence_code"),
            "proposed_evidence_eco_curie": selected_inputs.get("evidence_eco_curie"),
            "proposed_aspect": go_term.get("aspect"),
            "proposed_go_term_curie": go_term.get("curie"),
            "proposed_with_from": selected_inputs.get("with_from", []),
            "proposed_qualifiers": selected_inputs.get("qualifiers", []),
            "proposed_annotation_extensions": selected_inputs.get(
                "annotation_extensions", []
            ),
            "proposed_negated": selected_inputs.get("negated"),
            "proposed_rationale": selected_inputs.get("rationale"),
            "proposed_resolution_state": selected_inputs.get("resolution_state"),
        }
        drifted = [
            field_name
            for field_name, expected in expected_values.items()
            if getattr(self, field_name) != expected
        ]
        if drifted:
            raise ValueError(
                "proposal fields must exactly copy selected_inputs: "
                + ", ".join(drifted)
            )

        supplied_bundles = selected_inputs.get("evidence_quotes", [])
        if not isinstance(supplied_bundles, list):
            raise ValueError("selected_inputs.evidence_quotes must be a list")
        supplied_ids = {
            bundle.get("evidence_record_id")
            for bundle in supplied_bundles
            if isinstance(bundle, Mapping)
        }
        if any(
            evidence_record_id not in supplied_ids
            for evidence_record_id in self.primary_evidence_record_ids
        ):
            raise ValueError(
                "primary_evidence_record_ids must reference supplied exact evidence"
            )

    def _imp_support_is_recorded(self, info: ValidationInfo) -> bool:
        perturbation = (self.imp_perturbation or "").strip()
        phenotype = (self.imp_phenotype or "").strip()
        if not perturbation or not phenotype:
            return False
        rationale = self.proposed_rationale.casefold()
        if perturbation.casefold() not in rationale or phenotype.casefold() not in rationale:
            return False

        context = info.context
        if not isinstance(context, Mapping):
            return True
        request = context.get("domain_validation_request")
        selected_inputs = getattr(request, "selected_inputs", None)
        if not isinstance(selected_inputs, Mapping):
            return True
        evidence_quotes = selected_inputs.get("evidence_quotes", [])
        selected_ids = set(self.primary_evidence_record_ids)
        exact_evidence = " ".join(
            str(bundle.get("verified_quote", ""))
            for bundle in evidence_quotes
            if isinstance(bundle, Mapping)
            and bundle.get("evidence_record_id") in selected_ids
        ).casefold()
        return (
            perturbation.casefold() in exact_evidence
            and phenotype.casefold() in exact_evidence
        )


__all__ = [
    "EVIDENCE_POLICY",
    "INSUFFICIENT_EVIDENCE_MESSAGE",
    "PRIMARY_EVIDENCE_LOCATIONS",
    "RGDGOEvidencePolicyValidationResult",
]
