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
from .source_projection import apply_value


def saved_structure(db, execution_receipt):
    """The extractor's pinned saved output structure, or None for a built-in pack."""
    if execution_receipt is None:
        return None
    from src.lib.curation_workspace.execution_contracts import resolve_receipt_profile
    from src.schemas.agent_execution_revision import AgentExecutionReceipt
    return resolve_receipt_profile(db, AgentExecutionReceipt.model_validate(execution_receipt))


ISSUE_KEYS = ("candidate_id", "field_path", "reason", "expected", "message")


def _at_or_below(path, prefix):
    return path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "[")


def structure_check(structure, entry_id, source_entry, proposals):
    """Which proposed fields could never apply, from the saved structure alone.

    All proposed values are applied together to the entry's payload, the same
    way a selection applies them, and the payload is checked as the record will
    be when published. Each issue belongs to the proposed field at or above its
    path (paths only, never values); an issue below none of them, such as a
    required sibling the change leaves missing, belongs to the proposed fields
    in the same object. Returns ``({field_path: {structural_failure, issues}},
    issues no proposed field explains)``.
    """
    payload = deepcopy(source_entry["payload"])
    fields = {}

    def refuse(path, failure, issue):
        entry = fields.setdefault(path, {"structural_failure": failure, "issues": []})
        entry["issues"].append({key: issue.get(key) for key in ISSUE_KEYS})

    for proposal in proposals:
        try:
            apply_value(payload, proposal, structure)
        except ValueError:
            refuse(proposal.field_path, "unwritable_path", {
                "candidate_id": entry_id, "field_path": proposal.field_path, "reason": "unwritable_path",
                "expected": "a path that exists in the record",
                "message": "This path cannot be written in the record; its list item or parent is missing."})
    unexplained = []
    for issue in structure.validate_payload(payload, candidate_id=entry_id):
        owners = [item.field_path for item in proposals if _at_or_below(issue["field_path"], item.field_path)]
        if not owners:
            parent = issue["field_path"].rpartition(".")[0]
            owners = [item.field_path for item in proposals if parent and _at_or_below(item.field_path, parent)]
        for owner in owners:
            refuse(owner, "output_structure_conformance", issue)
        if not owners:
            unexplained.append({key: issue.get(key) for key in ISSUE_KEYS})
    return fields, unexplained


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
        references, refused = [], []
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
            unselectable = {}
            if failure is None and structure is not None and validated.proposed_values:
                # A field that could never be applied is not offered; its siblings
                # stay selectable. Only the saved declaration is checked; the
                # review still judges meaning.
                unselectable, issues = structure_check(
                    structure, item.entry_id, source_entry, validated.proposed_values)
                if issues or len(unselectable) == len(validated.proposed_values):
                    failure = "output_structure_conformance"
                    issues = issues or [issue for field in unselectable.values() for issue in field["issues"]]
            fact = append_fact(db, ep.artifact_id,
                operation_id="extraction-contribution:" + work_id + ":" + item.entry_id,
                kind="extraction_contribution", parent_id=request.id, target_id=item.entry_id,
                body={"work_id": work_id, "request_ref": request_ref,
                      "extractor_receipt": _result_receipt(row.payload["receipt"]),
                      "context_ref": row.payload["specialist_input"]["history"]["records"][0],
                      "proposal": item.model_dump(mode="json"), "evidence": evidence,
                      "item_relationships": relationships,
                      "selectable": failure is None, "structural_failure": failure,
                      "structural_issues": issues, "unselectable_fields": unselectable})
            references.append(reference(fact))
            if failure is not None or unselectable:
                refused.append({"contribution": reference(fact), "entry_id": item.entry_id,
                                "structural_failure": failure, "structural_issues": issues,
                                "unselectable_fields": unselectable})
        # The review sees, in this result, which proposals or fields it cannot select and why.
        return {"contributions": references, **({"not_selectable": refused} if refused else {})}
