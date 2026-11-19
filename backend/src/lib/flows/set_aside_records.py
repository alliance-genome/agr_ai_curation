"""Read-only list of the records a finished flow run's review set aside (ALL-1561).

A paper finishes when its review sets records aside (the reviewer deferred them
or did not accept them). This lists those records for one document of one run,
with the values its default export layout shows and the review's stored reason.
Nothing here decides, edits or saves anything; the records stay undecided until
a curator decides them.
"""

from __future__ import annotations

from sqlalchemy import select

from src.lib.curation_workspace.execution_contracts import load_receipt_profile
from src.lib.curation_workspace.extraction_results import list_extraction_results
from src.lib.flows.export_fields import REVIEW_STATUS_REF
from src.lib.flows.output_projection import build_extraction_result_artifact_bundle, set_aside_record_views
from src.lib.validation_history import selections, store
from src.models.sql.validation_history import ValidationFact
from src.schemas.curation_workspace import CurationExtractionSourceKind
from src.schemas.set_aside_records import FlowRunSetAsideRecordsResponse


class FlowRunResultsNotFound(LookupError):
    """The owner has no saved flow results for this run and document."""


def _finished_with_set_aside(user_id: str, document_id: str, reference: dict) -> bool:
    """Whether this result's run finished with records its review set aside.

    Results saved before this release, and runs that stopped, have no such
    fact; they are not listed (forward-only).
    """
    scope = store.HistoryScope(user_id, document_id, reference["history_scope_id"])
    with store.SessionLocal() as db:
        store._artifact(db, reference["artifact_id"], scope)
        return db.scalar(select(ValidationFact.id).where(
            ValidationFact.artifact_id == reference["artifact_id"],
            ValidationFact.kind == selections.SET_ASIDE_KIND,
            ValidationFact.parent_id == reference["episode_id"],
        ).limit(1)) is not None


def list_set_aside_records(*, user_id: str, flow_run_id: str, document_id: str) -> FlowRunSetAsideRecordsResponse:
    """The owner's set-aside records of one run and document, grouped by saved result.

    Only the owner's own results are read (owner, document and run identity
    on every saved row, then the history scope of each result). A record a
    curator has since decided is no longer pending and is not listed.
    """
    records = list_extraction_results(
        document_id=document_id, flow_run_id=flow_run_id, user_id=user_id,
        source_kind=CurationExtractionSourceKind.FLOW,
    )
    if not records:
        raise FlowRunResultsNotFound(f"Flow run {flow_run_id} has no saved results for this document")
    results = []
    for record in records:
        # Results of steps without a configured review carry no review reference.
        reference = record.metadata.get("flow_review")
        if reference is None or not _finished_with_set_aside(user_id, document_id, reference):
            continue
        bundle = build_extraction_result_artifact_bundle(
            user_id=user_id, extraction_results=[record], document_id=document_id,
            profile_resolver=load_receipt_profile,
        )
        artifact, = bundle.artifacts
        views = set_aside_record_views(bundle)
        if not views:
            continue
        results.append({
            "extraction_result_id": str(record.extraction_result_id),
            "node_id": record.metadata.get("flow_step_key"),
            "step": record.metadata.get("step"),
            "agent_name": record.metadata.get("agent_name"),
            # False for general results with no saved output structure: they
            # offer no review fields, so a file can never include these records.
            "review_fields_available": any(field.ref == REVIEW_STATUS_REF for field in artifact.declared_fields),
            "records": views,
        })
    return FlowRunSetAsideRecordsResponse.model_validate({
        "flow_run_id": flow_run_id, "document_id": document_id,
        "set_aside_count": sum(len(item["records"]) for item in results), "results": results,
    })
