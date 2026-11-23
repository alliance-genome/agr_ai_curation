"""Read-only list of the records a finished flow run's review set aside (ALL-1561).

A paper finishes when its review sets records aside (the reviewer deferred them
or did not accept them). This lists those records for one document of one run,
with the values its default export layout shows and the review's stored reason.
Nothing here decides, edits or saves anything; the records stay undecided until
a curator decides them.
"""

from __future__ import annotations


from src.lib.curation_workspace.execution_contracts import load_receipt_profile
from src.lib.curation_workspace.extraction_results import list_extraction_results
from src.lib.flows.export_fields import REVIEW_STATUS_REF
from src.lib.flows.output_projection import build_extraction_result_artifact_bundle, set_aside_record_views
from src.lib.validation_history import selections, store
from src.schemas.curation_workspace import CurationExtractionSourceKind
from src.schemas.set_aside_records import FlowRunSetAsideRecordsResponse


class FlowRunResultsNotFound(LookupError):
    """The owner has no saved flow results for this run and document."""


def _set_aside_state(user_id: str, document_id: str, reference: dict) -> str | None:
    """Where this result's set-aside records stand, read from stored history only.

    None: the run did not finish with records set aside (results saved before
    this release, runs that stopped; forward-only). "covered": the run's fact
    still covers the current state, so the records still pending are set aside.
    "held": a curator has since deferred, rejected or otherwise changed one of
    them (or started a new round); the result waits for the curator. "done":
    nothing is pending any more.
    """
    from src.lib.validation_history.episodes import latest_effective_episode

    scope = store.HistoryScope(user_id, document_id, reference["history_scope_id"])
    artifact_id = reference["artifact_id"]
    with store.SessionLocal.begin() as db:
        store._artifact(db, artifact_id, scope)
        if selections.set_aside_fact_in_transaction(db, artifact_id, reference["episode_id"]) is None:
            return None
        manifest = selections.manifest_in_transaction(
            db, scope, artifact_id, episode_id=latest_effective_episode(db, artifact_id).id)
        if manifest["ready"]:
            return "done"
        return "covered" if selections.set_aside_in_transaction(db, artifact_id, manifest) else "held"


def list_set_aside_records(
    *, user_id: str, flow_run_id: str, document_id: str, included_keys: set[str] | None = None,
) -> FlowRunSetAsideRecordsResponse:
    """The owner's set-aside records of one run and document, grouped by saved result.

    Only the owner's own results are read (owner, document and run identity
    on every saved row, then the history scope of each result). A record a
    curator has since accepted or removed is no longer listed. A result whose
    set-aside records a curator holds (deferred, rejected) is returned with
    ``held_by_curator: true`` and no records; its exports wait for the curator.
    """
    records = list_extraction_results(
        document_id=document_id, flow_run_id=flow_run_id, user_id=user_id,
        source_kind=CurationExtractionSourceKind.FLOW,
    )
    if not records:
        raise FlowRunResultsNotFound(f"Flow run {flow_run_id} has no saved results for this document")
    if included_keys is not None and not included_keys:
        return FlowRunSetAsideRecordsResponse(
            flow_run_id=flow_run_id, document_id=document_id, set_aside_count=0, results=[],
        )
    results = []
    for record in records:
        # Results of steps without a configured review carry no review reference.
        reference = record.metadata.get("flow_review")
        state = None if reference is None else _set_aside_state(user_id, document_id, reference)
        if state is None or state == "done":
            continue
        bundle = build_extraction_result_artifact_bundle(
            user_id=user_id, extraction_results=[record], document_id=document_id,
            profile_resolver=load_receipt_profile,
        )
        artifact, = bundle.artifacts
        views = set_aside_record_views(bundle, included_keys=included_keys) if state == "covered" else []
        if included_keys is not None and not views:
            continue
        results.append({
            "extraction_result_id": str(record.extraction_result_id),
            "node_id": record.metadata.get("flow_step_key"),
            "step": record.metadata.get("step"),
            "agent_name": record.metadata.get("agent_name"),
            # False for general results with no saved output structure: they
            # offer no review fields, so a file can never include these records.
            "review_fields_available": any(field.ref == REVIEW_STATUS_REF for field in artifact.declared_fields),
            "held_by_curator": state == "held",
            "records": views,
        })
    return FlowRunSetAsideRecordsResponse.model_validate({
        "flow_run_id": flow_run_id, "document_id": document_id,
        "set_aside_count": sum(len(item["records"]) for item in results), "results": results,
    })
