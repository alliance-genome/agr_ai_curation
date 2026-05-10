"""Disease submission adapter blockers."""

from __future__ import annotations

from src.lib.curation_workspace.submission_adapters.base import (
    SubmissionTransportAdapter,
    SubmissionTransportResult,
)
from src.schemas.curation_workspace import SubmissionPayloadContract

from .._submit_utils import blocked_submission_result
from .export import DISEASE_EXPORT_TARGET_ID


DISEASE_SUBMISSION_BLOCKED_OPERATIONS = (
    "insert public.diseaseannotation",
    "insert public.genediseaseannotation",
    "insert public.allelediseaseannotation",
    "insert public.agmdiseaseannotation",
    "insert public.diseaseannotation_conditionrelation",
)
DISEASE_REQUIRED_BEFORE_WRITE = (
    "Resolve disease_annotation_subject to a concrete Gene, Allele, or AGM row.",
    "Resolve single_reference.reference_id to the curation DB reference target.",
    "Resolve evidence_code_curies to curation DB ECO terms.",
    "Verify the concrete disease annotation write service.",
)


class DiseaseAnnotationSubmissionBlockerAdapter(SubmissionTransportAdapter):
    """Return an explicit blocker instead of silently no-oping disease writes."""

    def __init__(self, *, target_key: str = DISEASE_EXPORT_TARGET_ID) -> None:
        super().__init__(
            transport_key="alliance_disease_annotation_submission_blocker",
            supported_target_keys=(target_key,),
        )

    def _submit(
        self,
        *,
        payload: SubmissionPayloadContract,
    ) -> SubmissionTransportResult:
        return blocked_submission_result(
            payload=payload,
            domain_label="Disease annotation",
            reason=(
                "Disease annotation writes require verified concrete subtype, "
                "reference, evidence-code, data-provider, and condition-relation "
                "targets."
            ),
            blocked_operations=DISEASE_SUBMISSION_BLOCKED_OPERATIONS,
            required_before_write=DISEASE_REQUIRED_BEFORE_WRITE,
        )


__all__ = [
    "DISEASE_REQUIRED_BEFORE_WRITE",
    "DISEASE_SUBMISSION_BLOCKED_OPERATIONS",
    "DiseaseAnnotationSubmissionBlockerAdapter",
]
