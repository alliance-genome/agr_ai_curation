"""Chemical-condition submission adapter blockers."""

from __future__ import annotations

from src.lib.curation_workspace.submission_adapters.base import (
    SubmissionTransportAdapter,
    SubmissionTransportResult,
)
from src.schemas.curation_workspace import SubmissionPayloadContract

from .._submit_utils import blocked_submission_result
from .export import CHEMICAL_CONDITION_EXPORT_TARGET_ID


CHEMICAL_CONDITION_SUBMISSION_BLOCKED_OPERATIONS = (
    "insert public.conditionrelation",
    "insert public.experimentalcondition",
    "insert public.conditionrelation_experimentalcondition",
    "insert public.diseaseannotation_conditionrelation",
    "insert public.phenotypeannotation_conditionrelation",
)
CHEMICAL_CONDITION_REQUIRED_BEFORE_WRITE = (
    "Resolve host_annotation_type and host_annotation_id to an existing annotation row.",
    "Resolve source_reference.reference_id to the curation DB reference target.",
    "Resolve condition_class and condition_chemical to ontologyterm rows.",
    "Verify the condition-relation write service.",
)


class ChemicalConditionSubmissionBlockerAdapter(SubmissionTransportAdapter):
    """Return an explicit blocker instead of silently no-oping condition writes."""

    def __init__(self, *, target_key: str = CHEMICAL_CONDITION_EXPORT_TARGET_ID) -> None:
        super().__init__(
            transport_key="alliance_chemical_condition_submission_blocker",
            supported_target_keys=(target_key,),
        )

    def _submit(
        self,
        *,
        payload: SubmissionPayloadContract,
    ) -> SubmissionTransportResult:
        return blocked_submission_result(
            payload=payload,
            domain_label="Chemical condition",
            reason=(
                "Chemical condition writes require verified host annotation, "
                "reference, ontology-term, and condition-relation targets."
            ),
            blocked_operations=CHEMICAL_CONDITION_SUBMISSION_BLOCKED_OPERATIONS,
            required_before_write=CHEMICAL_CONDITION_REQUIRED_BEFORE_WRITE,
        )


__all__ = [
    "CHEMICAL_CONDITION_REQUIRED_BEFORE_WRITE",
    "CHEMICAL_CONDITION_SUBMISSION_BLOCKED_OPERATIONS",
    "ChemicalConditionSubmissionBlockerAdapter",
]
