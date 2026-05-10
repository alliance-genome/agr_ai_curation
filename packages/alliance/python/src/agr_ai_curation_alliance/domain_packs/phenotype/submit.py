"""Phenotype submission adapter blockers."""

from __future__ import annotations

from src.lib.curation_workspace.submission_adapters.base import (
    SubmissionTransportAdapter,
    SubmissionTransportResult,
)
from src.schemas.curation_workspace import SubmissionPayloadContract

from .._submit_utils import blocked_submission_result
from .export import PHENOTYPE_EXPORT_TARGET_ID


PHENOTYPE_SUBMISSION_BLOCKED_OPERATIONS = (
    "insert public.phenotypeannotation",
    "insert public.genephenotypeannotation",
    "insert public.allelephenotypeannotation",
    "insert public.agmphenotypeannotation",
    "insert public.phenotypeannotation_ontologyterm",
    "insert public.phenotypeannotation_conditionrelation",
)
PHENOTYPE_REQUIRED_BEFORE_WRITE = (
    "Resolve phenotype_annotation_subject to a concrete Gene, Allele, or AGM row.",
    "Resolve single_reference.reference_id to the curation DB reference target.",
    "Resolve phenotype_terms[] to public.ontologyterm rows.",
    "Verify the concrete phenotype annotation write service.",
)


class PhenotypeAnnotationSubmissionBlockerAdapter(SubmissionTransportAdapter):
    """Return an explicit blocker instead of silently no-oping phenotype writes."""

    def __init__(self, *, target_key: str = PHENOTYPE_EXPORT_TARGET_ID) -> None:
        super().__init__(
            transport_key="alliance_phenotype_annotation_submission_blocker",
            supported_target_keys=(target_key,),
        )

    def _submit(
        self,
        *,
        payload: SubmissionPayloadContract,
    ) -> SubmissionTransportResult:
        return blocked_submission_result(
            payload=payload,
            domain_label="Phenotype annotation",
            reason=(
                "Phenotype annotation writes require verified concrete subtype, "
                "reference, ontology-term, data-provider, and condition-relation "
                "targets."
            ),
            blocked_operations=PHENOTYPE_SUBMISSION_BLOCKED_OPERATIONS,
            required_before_write=PHENOTYPE_REQUIRED_BEFORE_WRITE,
        )


__all__ = [
    "PHENOTYPE_REQUIRED_BEFORE_WRITE",
    "PHENOTYPE_SUBMISSION_BLOCKED_OPERATIONS",
    "PhenotypeAnnotationSubmissionBlockerAdapter",
]
