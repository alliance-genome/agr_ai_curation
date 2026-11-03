"""Exact per-record views of immutable source; never a scientific selection."""

from copy import deepcopy
import json

from sqlalchemy import select

from src.lib.domain_packs.evidence_snapshot import EvidenceSnapshot
from src.lib.validation_history import store
from src.models.sql.validation_history import ValidationBody, ValidationFact
from .admission import lock_episode
from .audit import append_fact


def _object_target(db, artifact_id, target_id):
    seen = set()
    while target_id is not None and target_id not in seen:
        seen.add(target_id)
        target = store._fact(db, artifact_id, target_id, kind="target")
        content = store._content(db, target)
        if "object_ref" in content:
            return target, content
        # A binding names its object explicitly; field/element targets use parents.
        target_id = content["object_target"] if "object_target" in content else target.parent_id
    raise store.HistoryConflict("Target does not identify an original source record")


def snapshot(scope, episode_id, *, target_id):
    """Pin one original normalized object and its exact attached evidence records.

    Retired records remain readable. This grants no selection/revalidation rights.
    Added/replacement objects must use their contribution facts, not an invented
    extraction original. The full typed source remains available by baseline SHA.
    """
    with store.SessionLocal.begin() as db:
        episode, _ = lock_episode(db, scope, episode_id, require_open=False)
        artifact = store._artifact(db, episode.artifact_id, scope)
        fact = _source_fact(db, artifact, target_id)
        return {"fact_id": fact.id, "sha256": fact.body_sha}


def _source_fact(db, artifact, target_id):
    target, original = _object_target(db, artifact.id, target_id)
    if not all(store._has_baseline_subject(db, artifact.id, identity)
               for identity in (target_id, target.id)):
        raise store.HistoryConflict(
            "Record has no baseline original; inspect its exact source contribution history"
        )
    operation_id = "review-record-source:" + artifact.baseline_sha + ":" + target.id
    existing = db.scalar(select(ValidationFact).where(
        ValidationFact.artifact_id == artifact.id,
        ValidationFact.operation_id == operation_id,
    ))
    if existing is not None:
        if existing.kind != "review_record_source" or existing.target_id != target.id:
            raise store.HistoryConflict("Source record reference identity conflict")
        return existing
    baseline = db.get(ValidationBody, artifact.baseline_sha).content
    normalized = baseline["normalized"]
    matches = [item for item in normalized["extracted_objects"]
               if (item.get("object_id") or item.get("pending_ref_id")) == original["object_ref"]]
    if len(matches) != 1:
        raise store.HistoryConflict("Original source record is missing or ambiguous")
    obj = matches[0]
    evidence_index = EvidenceSnapshot.from_payload(normalized, [obj])
    evidence = []
    for identity in dict.fromkeys(obj["evidence_record_ids"]):
        record, error = evidence_index.resolve(identity)
        if error is not None:
            raise store.HistoryConflict("Original record evidence is " + error)
        evidence.append(record)
    return append_fact(
        db, artifact.id,
        operation_id=operation_id,
        kind="review_record_source", target_id=target.id,
        body={
            "artifact_id": artifact.id,
            "object_target": {"fact_id": target.id, "sha256": target.body_sha},
            "baseline": {"fact_id": "baseline", "sha256": artifact.baseline_sha},
            "subject_kind": artifact.subject_kind,
            "original_available": baseline["typed"] is not None,
            "representation": "normalized_source_record",
            "record": deepcopy(obj),
            "evidence_records": evidence,
            "source_document": deepcopy(artifact.provenance["source_document"]),
            "provenance_sha256": store.canonical(artifact.provenance)[0],
        },
    )


def _review_record(record):
    """Omit repeated bookkeeping only; the exact fact keeps all of it.

    Payloads stay untouched, including explicit nulls. A draft definition and any
    reference to mentions, exclusions, ambiguities or notes stays visible.
    """
    omitted = {"model_ref", "schema_ref"}
    if record.get("definition_state") == "stable":
        omitted |= {"definition_notes", "definition_state"}
    view = {key: value for key, value in record.items() if key not in omitted}
    references = [ref for ref in record.get("metadata_refs") or [] if ref.get("role") != "verified_evidence"]
    if references:
        view["metadata_refs"] = references
    else:
        view.pop("metadata_refs", None)
    return view


def _review_evidence(record):
    """Omit empty provenance fields and fragment text already in the verified quote."""
    quote = record.get("verified_quote") or ""
    compact = {key: value for key, value in record.items() if value is not None}
    if isinstance(compact.get("source_fragments"), list):
        compact["source_fragments"] = [
            {key: value for key, value in fragment.items()
             if value is not None and not (key == "text" and isinstance(value, str) and value in quote)}
            for fragment in compact["source_fragments"]
        ]
    return compact


def _wire_size(value):
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode())


def group_snapshot(scope, episode_id, *, target_ids):
    """Return related original records inline, with each shared evidence record once.

    Every returned record names its exact immutable fact, the same one the
    single-record tool returns. A response never exceeds the review packet size
    and never truncates a record: the rest are returned for another call. This
    grants no selection or revalidation rights.
    """
    from src.lib.openai_agents.config import get_validation_review_source_group_records
    from .observations import record_read

    limit = get_validation_review_source_group_records()
    requested = list(dict.fromkeys(target_ids))
    with store.SessionLocal.begin() as db:
        episode, _ = lock_episode(db, scope, episode_id, require_open=False)
        artifact = store._artifact(db, episode.artifact_id, scope)
        packet_bytes = episode.policy["packet_bytes"]
        page = {
            "representation": "normalized_source_record",
            "baseline": {"fact_id": "baseline", "sha256": artifact.baseline_sha},
            "source_document": deepcopy(artifact.provenance["source_document"]),
            "group_limit": limit,
            "records": [], "evidence": {}, "too_large": [], "unavailable": [], "not_returned": [],
        }
        returned = {}
        read_shas = []
        for index, target_id in enumerate(requested):
            if len(page["records"]) >= limit:
                page["not_returned"] = requested[index:]
                break
            remaining = requested[index + 1:]

            def fits():
                return _wire_size({**page, "not_returned": remaining}) <= packet_bytes

            try:
                fact = _source_fact(db, artifact, target_id)
            except store.HistoryConflict as error:
                page["unavailable"].append({"target_id": target_id, "reason": str(error)})
                if not fits():
                    page["unavailable"].pop()
                    page["not_returned"] = requested[index:]
                    break
                continue
            reference = {"fact_id": fact.id, "sha256": fact.body_sha}
            if fact.id in returned:
                aliases = returned[fact.id].setdefault("also_requested_as", [])
                aliases.append(target_id)
                if not fits():
                    aliases.pop()
                    if not aliases:
                        del returned[fact.id]["also_requested_as"]
                    page["not_returned"] = requested[index:]
                    break
                continue
            body = store._content(db, fact)
            item = {"target_id": target_id, "ref": reference, "subject_kind": body["subject_kind"],
                    "original_available": body["original_available"], "record": _review_record(body["record"])}
            evidence = dict(page["evidence"])
            for record in body["evidence_records"]:
                identity, compact = record["evidence_record_id"], _review_evidence(record)
                if evidence.setdefault(identity, compact) != compact:
                    item.setdefault("own_evidence", {})[identity] = compact
            if _wire_size({**page, "records": [*page["records"], item], "evidence": evidence,
                           "not_returned": remaining}) > packet_bytes:
                alone = {**page, "records": [item], "too_large": [], "unavailable": [], "not_returned": [],
                         "evidence": {key: evidence[key] for key in evidence
                                      if key in item["record"].get("evidence_record_ids", ())}}
                if _wire_size(alone) <= packet_bytes:
                    if not page["records"]:
                        raise ValueError("Too many record IDs for one grouped source read; request fewer records")
                    page["not_returned"] = requested[index:]
                    break
                page["too_large"].append({"target_id": target_id, "ref": reference})
                if not fits():
                    page["too_large"].pop()
                    page["not_returned"] = requested[index:]
                    break
                continue
            page["records"].append(item)
            page["evidence"] = evidence
            returned[fact.id] = item
            read_shas.append(fact.body_sha)
        if _wire_size(page) > packet_bytes:
            raise ValueError("Configured packet size cannot carry this grouped source read; request fewer records")
    for sha256 in read_shas:
        record_read("review_record_source", {"content": sha256, "sha256": sha256, "offset": 0, "next_offset": None})
    return page


def evidence_groups(db, artifact):
    """Short stable key per original record for the exact evidence it cites."""
    normalized = db.get(ValidationBody, artifact.baseline_sha).content["normalized"]
    return {
        item.get("object_id") or item.get("pending_ref_id"):
            store.canonical({"evidence": sorted(set(item.get("evidence_record_ids") or []))})[0][:12]
        for item in normalized.get("extracted_objects", [])
    }
