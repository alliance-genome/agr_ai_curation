"""Persist exact, entry-scoped specialist context independently of supervisor memory.

Readers accept only references issued in this dispatch. An artifact-wide baseline
or a grouped decision containing other entries is never a specialist capability.
"""
from copy import deepcopy

from sqlalchemy import select

from src.lib.validation_history import store
from src.models.sql.validation_history import ValidationFact, ValidationBody
from .audit import append_fact
from .reads import exact_page


def reference(fact):
    return {"fact_id": fact.id, "sha256": fact.body_sha}


def entry_coverage(db, artifact_id, entry_ids):
    if not entry_ids or len(entry_ids) != len(set(entry_ids)):
        raise store.HistoryConflict("Specialist context requires distinct existing entries")
    rows = list(db.scalars(select(ValidationFact).where(
        ValidationFact.artifact_id == artifact_id, ValidationFact.kind == "target",
    )))
    targets = {row.id: row for row in rows}
    for tid in entry_ids:
        if tid not in targets or "object_ref" not in store._content(db, targets[tid]):
            raise store.HistoryAccessError("Follow-up entry is not an original object target")
        store._require_active_target(db, artifact_id, tid)
        if not store._has_baseline_subject(db, artifact_id, tid):
            raise store.HistoryAccessError("Follow-up cannot invent extractor provenance")
    covered = set(entry_ids)
    while True:
        children = {row.id for row in rows if row.parent_id in covered}
        expanded = covered | children
        if expanded == covered:
            return targets, covered
        covered = expanded


def assemble_in_transaction(
    db, scope, episode, *, operation_id, entry_ids, parent_decision_id,
    instructions, current_entries, attempt, purpose, contributions=(),
):
    """Trusted caller provides current selected entries, not model-authored values."""
    artifact = store._artifact(db, episode.artifact_id, scope)
    targets, covered = entry_coverage(db, artifact.id, entry_ids)
    if set(current_entries) != set(entry_ids):
        raise store.HistoryConflict("Current input coverage differs from authorized entries")
    if len(entry_ids) > episode.policy["decision_entries"]:
        raise ValueError("Specialist context exceeds configured entry coverage")
    from .decisions import bounded_text
    bounded_text(instructions, episode.policy["instruction_chars"], "Follow-up instructions")
    parent = store._fact(db, artifact.id, parent_decision_id)
    parent_body = store._content(db, parent)
    if parent.kind == "extraction_source_selection":
        parent_body = {"actor": parent_body["identity"]["actor"],
            "entries": [{"target_id": parent.target_id, "action": "select_extraction_contribution",
                         "reason": parent_body["identity"]["reason"]}]}
    elif parent.kind != "decision":
        raise store.HistoryAccessError("Specialist request requires an exact review/source decision")
    decisions = [entry for entry in parent_body["entries"] if entry["target_id"] in covered]
    if not decisions:
        raise store.HistoryAccessError("Parent decision does not concern authorized entries")
    baseline = db.get(ValidationBody, artifact.baseline_sha).content["normalized"]
    objects = {item.get("object_id") or item.get("pending_ref_id"): item
               for item in baseline["extracted_objects"]}
    facts = list(db.scalars(select(ValidationFact).where(
        ValidationFact.artifact_id == artifact.id,
    ).order_by(ValidationFact.sequence)))
    by_id = {fact.id: fact for fact in facts}
    current_attempts = set(attempt.get("attempt_ids", []))
    attempts = [fact for fact in facts if fact.kind == "attempt" and fact.target_id in covered
                and fact.id not in current_attempts]
    attempt_ids = {fact.id for fact in attempts}
    completions = {fact.parent_id: fact for fact in facts
                   if fact.kind == "completion" and fact.parent_id in attempt_ids}
    decision_records = []
    for fact in facts:
        if fact.kind != "decision":
            continue
        body = store._content(db, fact)
        entries = [entry for entry in body["entries"] if entry["target_id"] in covered]
        if entries:
            # Record the exact relevant entries; do not grant access to siblings
            # merely because the supervisor reviewed several in one command.
            decision_records.append({"reference": reference(fact), "actor": body["actor"], "entries": entries})
    originals = []
    evidence_ids = set()
    for tid in entry_ids:
        original = store._content(db, targets[tid])
        key = original["object_ref"]
        if key not in objects:
            raise store.HistoryConflict("Original entry is missing from its immutable baseline")
        obj = deepcopy(objects[key])
        evidence_ids.update(obj["evidence_record_ids"])
        originals.append({"entry_id": tid, "reference": reference(targets[tid]), "value": obj})
    from src.lib.domain_packs.evidence_snapshot import EvidenceSnapshot
    evidence_snapshot = EvidenceSnapshot.from_payload(
        baseline, [item["value"] for item in originals]
    )
    supplied_evidence = []
    for evidence_id in sorted(evidence_ids):
        record, error = evidence_snapshot.resolve(evidence_id)
        if error:
            raise store.HistoryConflict("Original entry evidence is " + error)
        supplied_evidence.append(record)
    prior = []
    for fact in attempts:
        body = store._content(db, fact)
        completion = completions.get(fact.id)
        prior.append({
            "attempt_reference": reference(fact), "assessed_input": body["input"],
            "answer_reference": reference(completion) if completion is not None else None,
            "answer": store._content(db, completion) if completion is not None else None,
            "uncertainty": [store._content(db, item) for item in facts
                            if item.kind == "review_uncertainty" and item.parent_id == fact.id],
        })
    # Resolve accepted contributions under the same lock as current source and
    # context. Specialist callers cannot accidentally omit the correction that
    # produced the current input, including after a resumed review episode.
    from .source_selection import selected_entries
    selected_refs = list(contributions)
    for selected_fact, selected_body in selected_entries(db, artifact.id).values():
        if selected_fact.target_id in entry_ids:
            selected_refs.append(selected_body["identity"]["contribution"])
    selected_contributions = []
    seen_contributions = set()
    for ref in selected_refs:
        if (ref["fact_id"], ref["sha256"]) in seen_contributions:
            continue
        seen_contributions.add((ref["fact_id"], ref["sha256"]))
        fact = by_id.get(ref["fact_id"])
        if (fact is None or fact.kind != "extraction_contribution"
                or fact.target_id not in entry_ids or fact.body_sha != ref["sha256"]):
            raise store.HistoryAccessError("Contribution is outside supplied entry context")
        selected_contributions.append({"reference": reference(fact), "content": store._content(db, fact)})
    from src.models.sql.validation_review import ValidationReviewWork, ValidationReviewEpisode
    prior_instructions = []
    for work in db.scalars(select(ValidationReviewWork).join(ValidationReviewEpisode,
            ValidationReviewEpisode.id == ValidationReviewWork.episode_id).where(
            ValidationReviewEpisode.artifact_id == artifact.id)):
        payload = work.payload
        named = set(payload.get("invocation", {}).get("attempt_ids", [])) & attempt_ids
        followup = payload.get("followup")
        if named and followup is not None:
            prior_instructions.append({"attempt_ids": sorted(named), "instructions": followup["instructions"]})
    context = {
        "protocol_version": 1, "purpose": purpose, "artifact_id": artifact.id,
        "document_id": scope.document_id, "baseline_sha": artifact.baseline_sha,
        "original_entries": originals, "original_evidence": supplied_evidence,
        "current_selected_entries": deepcopy(current_entries),
        "previous_validation": prior, "decisions": decision_records,
        "previous_reconsideration_instructions": prior_instructions,
        "extractor_provenance": {
            "agent_key": artifact.provenance.get("agent_key"),
            "execution_receipt": deepcopy(artifact.provenance.get("execution_receipt")),
            "source_document": deepcopy(artifact.provenance.get("source_document")),
            "contract_sha256": store.canonical(artifact.provenance["extractor_contract"])[0]
                if artifact.provenance.get("extractor_contract") is not None else None,
        },
        "supervisor_request": {
            "parent_kind": parent.kind,
            "instructions": instructions, "parent_reference": reference(parent),
            "parent_decision": {"actor": parent_body["actor"], "entries": decisions},
        },
        "contributions": selected_contributions, "attempt": deepcopy(attempt),
    }
    fact = append_fact(
        db, artifact.id, operation_id="specialist-context:" + operation_id,
        kind="specialist_context", parent_id=episode.id, body=context,
    )
    ref = reference(fact)
    # Only this entry-scoped immutable record is a readable capability. Original
    # provenance refs inside it are informational, not grants to arbitrary facts.
    return {"context": context, "history": {"records": [ref], "reader": "read_supplied_history"}}


def create_history_reader(scope, artifact_id, *, records, packet_bytes):
    """Resolve exact supplied capabilities; no episode-wide or paper access."""
    from agents import function_tool
    import asyncio

    allowed = {item["fact_id"]: item["sha256"] for item in records}
    if not allowed or len(allowed) != len(records):
        raise ValueError("Supplied history requires distinct exact references")

    def read(fact_id, sha256, offset):
        if allowed.get(fact_id) != sha256:
            raise store.HistoryAccessError("History record was not supplied to this dispatch")
        with store.SessionLocal() as db:
            store._artifact(db, artifact_id, scope)
            fact = store._fact(db, artifact_id, fact_id, kind="specialist_context")
            if fact.body_sha != sha256:
                raise store.HistoryConflict("Supplied history identity changed")
            return exact_page(store._content(db, fact), fact_id=fact_id,
                              sha256=sha256, offset=offset, limit=packet_bytes)

    @function_tool(failure_error_function=None)
    async def read_supplied_history(fact_id: str, sha256: str, offset: int = 0) -> dict:
        """Read exact saved context supplied for this task, using SHA and byte offsets.

        Follow next_offset to null to read all of a record. References inside the
        record describe provenance; only explicitly supplied records are readable.
        This tool cannot search/read the paper or other entries.
        """
        return await asyncio.to_thread(read, fact_id, sha256, offset)

    return read_supplied_history
