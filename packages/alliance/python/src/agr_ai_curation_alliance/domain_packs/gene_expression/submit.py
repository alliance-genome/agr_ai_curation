"""Alliance gene-expression submission transport adapter."""

from __future__ import annotations

from typing import Any, Mapping

from src.lib.curation_workspace.submission_adapters.base import (
    SubmissionTransportAdapter,
    SubmissionTransportResult,
    normalize_submission_transport_result,
)
from src.schemas.curation_workspace import CurationSubmissionStatus, SubmissionPayloadContract

from .export import (
    GENE_EXPRESSION_ADAPTER_KEY,
    GENE_EXPRESSION_TARGET_KEY,
)


class GeneExpressionSubmissionAdapter(SubmissionTransportAdapter):
    """Validate and record a target-shaped gene-expression submission handoff.

    The Symphony-accessible curation DB tunnel is read-only, so this adapter does
    not mutate the Alliance database. It returns deterministic target state that
    the workspace submission framework persists in submission history.
    """

    def __init__(self) -> None:
        super().__init__(
            transport_key="alliance_gene_expression_submission",
            supported_target_keys=(GENE_EXPRESSION_TARGET_KEY,),
        )

    def _submit(
        self,
        *,
        payload: SubmissionPayloadContract,
        idempotency_key: str,
    ) -> SubmissionTransportResult:
        payload_json = _payload_mapping(payload.payload_json)
        annotations = _annotation_payloads(payload_json)
        validation_errors = _payload_validation_errors(payload, payload_json, annotations)
        if validation_errors:
            return normalize_submission_transport_result(
                status=CurationSubmissionStatus.VALIDATION_ERRORS,
                response_message="Gene-expression target payload failed adapter validation.",
                validation_errors=validation_errors,
                submission_state={
                    "idempotency_key": idempotency_key,
                    "target_status": "validation_errors",
                    "target_key": payload.target_key,
                    "annotation_count": len(annotations),
                },
                target_result_history=[
                    {
                        "status": "validation_errors",
                        "target_key": payload.target_key,
                        "validation_error_count": len(validation_errors),
                    }
                ],
            )

        annotation_count = len(annotations)
        envelope_revisions = _envelope_revisions(annotations)
        external_reference = (
            f"alliance:gene_expression:{payload.target_key}:{annotation_count}"
        )
        submission_state = {
            "idempotency_key": idempotency_key,
            "target_status": "manual_review_required",
            "target_key": payload.target_key,
            "target_transport": self.transport_key,
            "external_reference": external_reference,
            "annotation_count": annotation_count,
            "envelope_revisions": envelope_revisions,
            "write_mode": "read_only_handoff",
        }
        return normalize_submission_transport_result(
            status=CurationSubmissionStatus.MANUAL_REVIEW_REQUIRED,
            external_reference=external_reference,
            response_message=(
                "Gene-expression target payload was prepared for curation DB "
                "handoff; live database mutation requires an approved write transport."
            ),
            warnings=(
                "Read-only handoff recorded; no Alliance curation DB rows were mutated.",
            ),
            submission_state=submission_state,
            target_result_history=[
                {
                    "status": "manual_review_required",
                    "target_key": payload.target_key,
                    "annotation_count": annotation_count,
                    "write_mode": "read_only_handoff",
                }
            ],
        )


def _payload_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _annotation_payloads(payload_json: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_annotations = payload_json.get("gene_expression_annotations")
    if not isinstance(raw_annotations, list):
        return []
    return [item for item in raw_annotations if isinstance(item, Mapping)]


def _payload_validation_errors(
    payload: SubmissionPayloadContract,
    payload_json: Mapping[str, Any],
    annotations: list[Mapping[str, Any]],
) -> tuple[str, ...]:
    errors: list[str] = []
    if payload.adapter_key != GENE_EXPRESSION_ADAPTER_KEY:
        errors.append(
            f"Payload adapter_key must be {GENE_EXPRESSION_ADAPTER_KEY}; "
            f"found {payload.adapter_key}."
        )
    if payload.target_key != GENE_EXPRESSION_TARGET_KEY:
        errors.append(
            f"Payload target_key must be {GENE_EXPRESSION_TARGET_KEY}; "
            f"found {payload.target_key}."
        )
    if payload_json.get("bundle_type") != "alliance_gene_expression_curation_db_export":
        errors.append("Payload bundle_type is not alliance_gene_expression_curation_db_export.")
    if not annotations:
        errors.append("Payload must contain at least one gene_expression_annotations item.")

    for index, annotation in enumerate(annotations):
        target_rows = annotation.get("target_rows")
        if not isinstance(target_rows, Mapping):
            errors.append(f"gene_expression_annotations[{index}].target_rows is required.")
            continue
        for table_name in (
            "geneexpressionannotation",
            "geneexpressionexperiment",
            "expressionpattern",
            "temporalcontext",
            "anatomicalsite",
        ):
            if not isinstance(target_rows.get(table_name), Mapping):
                errors.append(
                    "gene_expression_annotations"
                    f"[{index}].target_rows.{table_name} is required."
                )
    return tuple(errors)


def _envelope_revisions(
    annotations: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    revisions: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for annotation in annotations:
        envelope = annotation.get("envelope")
        if not isinstance(envelope, Mapping):
            continue
        envelope_id = envelope.get("envelope_id")
        envelope_revision = envelope.get("envelope_revision")
        if not isinstance(envelope_id, str) or not isinstance(envelope_revision, int):
            continue
        key = (envelope_id, envelope_revision)
        if key in seen:
            continue
        revisions.append(
            {
                "envelope_id": envelope_id,
                "envelope_revision": envelope_revision,
            }
        )
        seen.add(key)
    return revisions


__all__ = ["GeneExpressionSubmissionAdapter"]
