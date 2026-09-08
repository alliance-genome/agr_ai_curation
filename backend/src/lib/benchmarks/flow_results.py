"""Resolve flow completion receipts to owner- and run-scoped extraction envelopes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from src.lib.curation_workspace.domain_envelope_normalization import (
    domain_envelope_from_extraction_result,
)
from src.lib.curation_workspace.extraction_results import list_extraction_results
from src.schemas.curation_workspace import CurationExtractionSourceKind
from src.schemas.domain_envelope import DomainEnvelope

from .models import StrictModel


class BenchmarkFlowExtractions(StrictModel):
    """Keep distinct flow envelopes intact, including their authoritative provenance.

    Flows can produce multiple envelopes from different adapters. They must not
    be flattened into one domain pack or reduced to an arbitrary first result.
    """

    schema_version: Literal["benchmark-flow-extractions/v1"] = "benchmark-flow-extractions/v1"
    envelopes: list[DomainEnvelope] = Field(min_length=1)


def load_flow_extractions(
    completion: dict[str, Any],
    *,
    document_id: str,
    user_id: str,
    run_id: str,
) -> dict[str, Any]:
    """Return only validated envelopes actually persisted by this completed run."""

    if not document_id or not user_id or not run_id:
        raise ValueError("Benchmark flow extraction requires document, owner, and run identity")
    if completion.get("status") != "completed":
        raise ValueError("Benchmark flow did not complete successfully")
    if (
        completion.get("document_id") != document_id
        or completion.get("flow_run_id") != run_id
        or completion.get("origin_session_id") != run_id
    ):
        raise ValueError("Benchmark flow completion scope does not match execution")
    refs = completion.get("extraction_result_refs")
    if not isinstance(refs, list) or not refs:
        raise ValueError("Benchmark flow completed without extraction results")
    ids: list[str] = []
    for ref in refs:
        result_id = ref.get("extraction_result_id") if isinstance(ref, dict) else None
        if not isinstance(result_id, str) or not result_id or result_id in ids:
            raise ValueError("Benchmark flow has invalid extraction result references")
        ids.append(result_id)

    records = list_extraction_results(
        document_id=document_id,
        flow_run_id=run_id,
        origin_session_id=run_id,
        user_id=user_id,
        source_kind=CurationExtractionSourceKind.FLOW,
    )
    by_id = {record.extraction_result_id: record for record in records}
    if not set(ids).issubset(by_id):
        raise ValueError("Benchmark flow extraction receipt does not match persisted results")
    # Receipt order is the executor's completion order, not database query order.
    envelopes = []
    for result_id in ids:
        record = by_id[result_id]
        payload = record.payload_json
        if not isinstance(payload, dict) or not any(
            isinstance(payload.get(key), list)
            for key in ("curatable_objects", "extracted_objects")
        ):
            raise ValueError("Benchmark flow result is not an extraction envelope")
        envelopes.append(domain_envelope_from_extraction_result(record))
    return BenchmarkFlowExtractions(
        envelopes=envelopes
    ).model_dump(mode="json")
