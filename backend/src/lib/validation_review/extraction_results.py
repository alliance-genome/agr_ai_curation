"""Retain extractor proposals without changing source or selected validator values."""
from copy import deepcopy

from sqlalchemy import select
from pydantic import ValidationError

from src.lib.domain_packs.evidence_snapshot import EvidenceSnapshot
from src.lib.validation_history import store
from src.models.sql.validation_review import ValidationReviewWork as Work
from src.schemas.validation_followup import TargetedFollowupSubmission, FollowupEntryResult
from . import admission
from .audit import append_fact
from .specialist_context import reference
from .item_relationships import validate as validate_item_relationships
from .source_projection import apply_value, removed_fields


def saved_structure(db, execution_receipt):
    """The extractor's pinned saved output structure, or None for a built-in pack."""
    if execution_receipt is None:
        return None
    from src.lib.curation_workspace.execution_contracts import resolve_receipt_profile
    from src.schemas.agent_execution_revision import AgentExecutionReceipt
    return resolve_receipt_profile(db, AgentExecutionReceipt.model_validate(execution_receipt))


def _structure_issues(structure, source_entry, proposals):
    """Issues the whole proposal would raise against the saved output structure."""
    payload = deepcopy(source_entry["payload"])
    removed = removed_fields(structure, proposals)
    for proposal in proposals:
        apply_value(payload, proposal, removed)
    return [{key: issue[key] for key in ("field_path", "reason", "expected", "message")}
            for issue in structure.validate_attributes(payload.get("attributes"))]


def _result_receipt(receipt):
    """Name the extractor; its full setup stays in artifact provenance and the work."""
    kept = {key: value for key, value in receipt.items() if key != "contract"}
    return {**kept, "contract_sha256": store.canonical(receipt["contract"])[0]}


def retain(scope, work_id, lease_token, result, *, evidence_records):
    """evidence_records is an authenticated extractor-tool snapshot, never a tool arg.

    A late exact answer can be retained after an uncertain submission. This does
    not authorize publication or another provider call. Exact duplicate delivery
    is idempotent; competing answers with the same invocation identity conflict.
    """
    result = TargetedFollowupSubmission.model_validate(
        result.model_dump(mode="json") if hasattr(result, "model_dump") else result)
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
        structure = saved_structure(db, row.payload["receipt"]["execution_receipt"])
        references = []
        for item in result.entries:
            source_entry = supplied["current_selected_entries"][item.entry_id]
            relationships, evidence = {}, []
            # A shape-valid but unusable entry remains auditable. Its siblings
            # can still be reviewed; no correction call or scientific judgment
            # is made by this structural boundary.
            failure = None
            issues = []
            validated = None
            try:
                validated = FollowupEntryResult.model_validate(item.model_dump(mode="json"))
            except ValidationError:
                failure = "invalid_proposal_structure"
            if validated is not None:
                try:
                    relationships = {
                        value.field_path: validate_item_relationships(source_entry, value)
                        for value in validated.proposed_values
                    }
                except (store.HistoryConflict, store.HistoryAccessError):
                    failure = "invalid_item_relationships"
            ids = set(item.evidence_record_ids)
            for value in item.proposed_values:
                ids.update(value.evidence_record_ids)
            for identity in sorted(ids):
                record, error = snapshot.resolve(identity)
                if error:
                    failure = "invalid_evidence_reference"
                    continue
                evidence.append({"origin": "original" if identity in old_ids else "followup",
                                 "record": record})
            if failure is None and structure is not None and validated.proposed_values:
                # A proposal that could never be applied is not offered. Only the
                # saved declaration is checked; the review still judges meaning.
                try:
                    issues = _structure_issues(structure, source_entry, validated.proposed_values)
                except ValueError:
                    failure = "invalid_proposal_structure"
                if issues:
                    failure = "output_structure_conformance"
            fact = append_fact(db, ep.artifact_id,
                operation_id="extraction-contribution:" + work_id + ":" + item.entry_id,
                kind="extraction_contribution", parent_id=request.id, target_id=item.entry_id,
                body={"work_id": work_id, "request_ref": request_ref,
                      "extractor_receipt": _result_receipt(row.payload["receipt"]),
                      "context_ref": row.payload["specialist_input"]["history"]["records"][0],
                      "proposal": item.model_dump(mode="json"), "evidence": evidence,
                      "item_relationships": relationships,
                      "selectable": failure is None, "structural_failure": failure,
                      "structural_issues": issues})
            references.append(reference(fact))
        return {"contributions": references}
