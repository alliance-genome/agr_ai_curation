"""Typed result contract for the approved RGD GO evidence policy."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictStr, ValidationInfo, model_validator

from src.schemas.domain_validator import (  # type: ignore[reportMissingImports]
    DomainValidatorBaseModel,
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
    "evidence_code_unresolved",
    "go_term_unresolved",
    "reference_unresolved",
    "with_from_unresolved",
    "qualifier_unresolved",
]
ResolutionState = Literal["resolved", "unresolved"]

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


class RGDGOWithFromEntry(DomainValidatorBaseModel):
    """One With/From entry as the candidate stores it: paper wording and, when validated, its identifier."""

    mention: StrictStr = Field(description="With/From entry as the paper words it")
    proposed_curie: StrictStr | None = Field(
        default=None, description="Identifier the paper itself prints; a claim, never the identity"
    )
    taxon_curie: StrictStr | None = Field(
        default=None, description="The partner's species, only when the paper states it"
    )
    curie: StrictStr | None = Field(
        default=None, description="Gene identifier validation confirmed; null while unresolved"
    )
    overruled_curie: StrictStr | None = Field(
        default=None, description="An identity validation or a curator overruled; informational only"
    )
    curator_override: dict[str, Any] | None = Field(
        default=None, description="The curator's override record, when a curator set the identifier"
    )
    resolution_state: Literal["resolved", "unresolved"] | None = Field(
        default=None, description="Whether a lookup matched the entry"
    )
    lookup_outcome: StrictStr | None = Field(
        default=None, description="Lookup result recorded for the entry"
    )
    validator_explanation: StrictStr | None = Field(
        default=None, description="Explanation recorded with the entry's lookup result"
    )
    validator_curator_message: StrictStr | None = Field(
        default=None, description="Curator message recorded with the entry's lookup result"
    )


class RGDGOQualifierEntry(DomainValidatorBaseModel):
    """One qualifier as the candidate stores it: paper wording and, when matched, its GO relation."""

    mention: StrictStr = Field(description="Qualifier as the paper supports it")
    name: StrictStr | None = Field(
        default=None, description="GO relation the builder matched; null while unresolved"
    )
    overruled_name: StrictStr | None = Field(
        default=None, description="A relation a curator overruled; informational only"
    )
    curator_override: dict[str, Any] | None = Field(
        default=None, description="The curator's override record, when a curator set the relation"
    )
    resolution_state: Literal["resolved", "unresolved"] | None = Field(
        default=None, description="Whether the qualifier matched an allowed GO relation"
    )
    lookup_outcome: StrictStr | None = Field(
        default=None, description="Lookup result recorded for the qualifier"
    )
    validator_explanation: StrictStr | None = Field(
        default=None, description="Explanation recorded with the qualifier's lookup result"
    )
    validator_curator_message: StrictStr | None = Field(
        default=None, description="Curator message recorded with the qualifier's lookup result"
    )


def _without_empty_entry_keys(field_name: str, value: object) -> object:
    """List entries compared without their null keys.

    A stored entry omits keys it never had (a table-mapped qualifier carries no
    validator message), while a dumped-and-revalidated copy states them as null;
    both say the same thing.
    """

    if field_name not in ("proposed_with_from", "proposed_qualifiers") or not isinstance(value, list):
        return value
    return [
        {key: item for key, item in entry.items() if item is not None}
        if isinstance(entry, Mapping)
        else entry
        for entry in value
    ]


COMPACT_VALIDATOR_RUNTIME = ("agr.alliance", "agr_ai_curation_alliance.compact_adapter:build_compact_validator_runtime")


class RGDGOEvidencePolicyValidationResult(DomainValidatorResultBase):
    """One typed decision under the approved RGD specialist policy profile."""

    __envelope_class__ = True

    decision: Literal["submit_ready", "curator_review_required"] = Field(
        description="Whether the proposal passes policy or must remain in curator review"
    )
    evidence_basis: RGDGOEvidenceBasis = Field(
        description="Evidence class supported by the cited primary paper evidence"
    )
    proposed_evidence_code: StrictStr | None = Field(
        description="GO evidence code the builder matched for the candidate; null while unresolved"
    )
    proposed_evidence_eco_curie: StrictStr | None = Field(
        description="ECO CURIE the builder matched to the evidence code; null while unresolved"
    )
    proposed_evidence_code_resolution_state: ResolutionState = Field(
        description="Whether the evidence code matched a supported code"
    )
    proposed_aspect: RGDGOAspect = Field(
        description="GO aspect copied from the candidate term"
    )
    proposed_go_term_curie: StrictStr | None = Field(
        description="GO CURIE copied from the candidate term; null while the term is unresolved"
    )
    proposed_go_term_resolution_state: ResolutionState = Field(
        description="Whether a lookup matched the candidate's GO term"
    )
    proposed_reference_resolution_state: ResolutionState = Field(
        description="Whether a lookup matched the candidate's reference"
    )
    proposed_with_from: list[RGDGOWithFromEntry] = Field(
        description="With/From entries copied from the candidate"
    )
    proposed_qualifiers: list[RGDGOQualifierEntry] = Field(
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
        description="Policy findings identified by the LLM for this proposal"
    )

    @model_validator(mode="after")
    def _enforce_structural_contract(self, info: ValidationInfo) -> "RGDGOEvidencePolicyValidationResult":
        self._enforce_canonical_request_copies(info)
        if self.resolved_values:
            raise ValueError("policy validation must not rewrite candidate payload values")
        return self

    @classmethod
    def proposal_facts(cls, selected_inputs: Mapping[str, object]) -> dict[str, object]:
        """The proposal fields as the candidate stores them; the only source for the copies."""

        go_term = selected_inputs.get("go_term")
        evidence_code = selected_inputs.get("evidence_code")
        reference = selected_inputs.get("reference_curie")
        if not isinstance(go_term, Mapping):
            raise ValueError("selected_inputs.go_term must be a mapping")
        if not isinstance(evidence_code, Mapping):
            raise ValueError("selected_inputs.evidence_code must be a mapping")
        if not isinstance(reference, Mapping):
            raise ValueError("selected_inputs.reference_curie must be a mapping")
        return {
            "proposed_evidence_code": evidence_code.get("code"),
            "proposed_evidence_eco_curie": evidence_code.get("eco_curie"),
            "proposed_evidence_code_resolution_state": evidence_code.get("resolution_state"),
            "proposed_aspect": go_term.get("aspect"),
            "proposed_go_term_curie": go_term.get("curie"),
            "proposed_go_term_resolution_state": go_term.get("resolution_state"),
            "proposed_reference_resolution_state": reference.get("resolution_state"),
            "proposed_with_from": deepcopy(selected_inputs.get("with_from", [])),
            "proposed_qualifiers": deepcopy(selected_inputs.get("qualifiers", [])),
            "proposed_annotation_extensions": deepcopy(
                selected_inputs.get("annotation_extensions", [])
            ),
            "proposed_negated": selected_inputs.get("negated"),
            "proposed_rationale": selected_inputs.get("rationale"),
            "proposed_resolution_state": selected_inputs.get("resolution_state"),
        }

    def _enforce_canonical_request_copies(self, info: ValidationInfo) -> None:
        context = info.context
        if not isinstance(context, Mapping):
            return
        request = context.get("domain_validation_request")
        selected_inputs = getattr(request, "selected_inputs", None)
        if not isinstance(selected_inputs, Mapping):
            return

        expected_values = self.proposal_facts(selected_inputs)
        drifted = [
            field_name
            for field_name, expected in expected_values.items()
            if _without_empty_entry_keys(field_name, self._canonical_copy(field_name))
            != _without_empty_entry_keys(field_name, expected)
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

    def _canonical_copy(self, field_name: str) -> object:
        value = getattr(self, field_name)
        if field_name in ("proposed_with_from", "proposed_qualifiers"):
            # Entries compare as the stored candidate objects they were copied from.
            return [
                entry.model_dump(mode="json", exclude_unset=True)
                if isinstance(entry, (RGDGOWithFromEntry, RGDGOQualifierEntry))
                else entry
                for entry in value
            ]
        return value



__all__ = [
    "EVIDENCE_POLICY",
    "RGDGOQualifierEntry",
    "RGDGOWithFromEntry",
    "INSUFFICIENT_EVIDENCE_MESSAGE",
    "PRIMARY_EVIDENCE_LOCATIONS",
    "RGDGOEvidencePolicyValidationResult",
]
