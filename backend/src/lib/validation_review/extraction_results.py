"""Retain extractor proposals without changing source or selected validator values."""
from sqlalchemy import select

from src.lib.domain_packs.evidence_snapshot import EvidenceSnapshot
from src.lib.validation_history import store
from src.models.sql.validation_review import ValidationReviewWork as Work
from src.schemas.validation_followup import TargetedFollowupResult
from . import admission
from .audit import append_fact
from .specialist_context import reference
from .item_relationships import validate as validate_item_relationships


def retain(scope, work_id, lease_token, result, *, evidence_records):
    """evidence_records is an authenticated extractor-tool snapshot, never a tool arg.

    A late exact answer can be retained after an uncertain submission. This does
    not authorize publication or another provider call. Exact duplicate delivery
    is idempotent; competing answers with the same invocation identity conflict.
    """
    result = TargetedFollowupResult.model_validate(result)
    with store.SessionLocal.begin() as db:
        row = db.get(Work, work_id)
        if row is None:
            raise store.HistoryAccessError("Follow-up work unavailable")
        ep, _ = admission.lock_episode(db, scope, row.episode_id, require_open=False)
        row = db.scalar(select(Work).where(Work.id == work_id).with_for_update()
                        .execution_options(populate_existing=True))
        if row.kind != "extraction_followup" or row.lease_token != lease_token:
            raise store.HistoryAccessError("Outcome does not belong to this extractor invocation")
        if row.state not in {"submitted", "uncertain", "completed"}:
            raise store.HistoryConflict("Follow-up was not submitted")
        if {item.entry_id for item in result.entries} != set(row.payload["entry_ids"]):
            raise store.HistoryConflict("Follow-up outcome must cover exactly the requested entries")
        request_ref = row.payload["request_ref"]
        request = store._fact(db, ep.artifact_id, request_ref["fact_id"], kind="extraction_followup_request")
        if request.body_sha != request_ref["sha256"]:
            raise store.HistoryConflict("Follow-up request identity changed")
        supplied = row.payload["specialist_input"]["context"]
        old_evidence = supplied["original_evidence"]
        snapshot = EvidenceSnapshot([*old_evidence, *evidence_records])
        old_ids = {record["evidence_record_id"] for record in old_evidence}
        references = []
        for item in result.entries:
            source_entry = supplied["current_selected_entries"][item.entry_id]
            relationships = {
                value.field_path: validate_item_relationships(source_entry, value)
                for value in item.proposed_values
            }
            ids = set(item.evidence_record_ids)
            for value in item.proposed_values:
                ids.update(value.evidence_record_ids)
            evidence = []
            for identity in sorted(ids):
                record, error = snapshot.resolve(identity)
                if error:
                    raise store.HistoryConflict("Follow-up evidence is " + error)
                evidence.append({"origin": "original" if identity in old_ids else "followup",
                                 "record": record})
            fact = append_fact(db, ep.artifact_id,
                operation_id="extraction-contribution:" + work_id + ":" + item.entry_id,
                kind="extraction_contribution", parent_id=request.id, target_id=item.entry_id,
                body={"work_id": work_id, "request_ref": request_ref,
                      "extractor_receipt": row.payload["receipt"],
                      "context_ref": row.payload["specialist_input"]["history"]["records"][0],
                      "proposal": item.model_dump(mode="json"), "evidence": evidence,
                      "item_relationships": relationships})
            references.append(reference(fact))
        return {"contributions": references}
