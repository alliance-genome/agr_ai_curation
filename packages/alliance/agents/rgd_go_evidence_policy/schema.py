"""Typed result contract for the approved RGD GO evidence policy."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictBool, StrictStr, model_validator

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
    proposed_resolution_state: Literal["resolved", "unresolved"] = Field(
        description="Gene-product resolution state copied from the candidate"
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
    go_term_is_binding_or_descendant: StrictBool = Field(
        description="Whether the GO term is binding or a descendant"
    )
    policy_violations: list[RGDGOPolicyViolation] = Field(
        description="Deterministically ordered policy violations for this proposal"
    )

    @model_validator(mode="after")
    def _enforce_approved_policy(self) -> "RGDGOEvidencePolicyValidationResult":
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
        if self.proposed_resolution_state != "resolved":
            violations.append("identity_unresolved")

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
        if self.proposed_negated and self.go_term_is_binding_or_descendant:
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
        if (
            "insufficient_primary_evidence" in violations
            and self.curator_message != INSUFFICIENT_EVIDENCE_MESSAGE
        ):
            raise ValueError(
                "insufficient primary evidence must use the approved curator finding message"
            )
        return self


__all__ = [
    "EVIDENCE_POLICY",
    "INSUFFICIENT_EVIDENCE_MESSAGE",
    "PRIMARY_EVIDENCE_LOCATIONS",
    "RGDGOEvidencePolicyValidationResult",
]
