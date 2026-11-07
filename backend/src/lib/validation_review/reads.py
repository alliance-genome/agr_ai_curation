"""Exact, byte-bounded review snapshots. Continuations never truncate stored data."""

import json
from uuid import uuid4

from sqlalchemy import select

from src.lib.validation_history import store
from .audit import append_fact
from src.lib.validation_history.selections import manifest_in_transaction
from src.models.sql.validation_history import ValidationFact, ValidationBody
from .admission import lock_episode, _allowances, provider_usage
from .policy import TOKEN_USAGE_BUCKETS


def status_reference(scope, episode_id):
    """Return a compact status with an exact reference to arbitrarily large coverage."""
    from .admission import status

    value = status(scope, episode_id)
    with store.SessionLocal.begin() as db:
        ep, _ = lock_episode(db, scope, episode_id, require_open=False)
        fact = append_fact(
            db,
            ep.artifact_id,
            operation_id="review-status:" + str(uuid4()),
            kind="review_status",
            parent_id=ep.id,
            body=value,
        )
        return {
            "state": value["state"],
            "ready": value["ready"],
            "fact_id": fact.id,
            "sha256": fact.body_sha,
        }


def snapshot(scope, episode_id, *, operation_id):
    """Pin mutable review state once; all subsequent reads name its immutable SHA."""
    with store.SessionLocal.begin() as db:
        ep, chain = lock_episode(db, scope, episode_id, require_open=False)
        from .decisions import bounded_text

        bounded_text(operation_id, ep.policy["instruction_chars"], "Operation ID")
        key = "review-packet:" + ep.id + ":" + operation_id
        existing = db.scalar(
            select(ValidationFact).where(
                ValidationFact.artifact_id == ep.artifact_id,
                ValidationFact.operation_id == key,
            )
        )
        if existing is not None:
            if existing.parent_id != episode_id or existing.kind != "review_packet":
                raise store.HistoryConflict("Review packet identity conflict")
            return {"fact_id": existing.id, "sha256": existing.body_sha}
        artifact = store._artifact(db, ep.artifact_id, scope)
        facts = db.scalars(
            select(ValidationFact)
            .where(
                ValidationFact.artifact_id == ep.artifact_id,
                ValidationFact.kind != "review_packet",
            )
            .order_by(ValidationFact.sequence)
        ).all()
        body = {
            "episode_id": ep.id,
            "artifact_id": ep.artifact_id,
            "baseline": {
                "fact_id": "baseline",
                "sha256": artifact.baseline_sha,
                "subject_kind": artifact.subject_kind,
            },
            "manifest": manifest_in_transaction(
                db, scope, ep.artifact_id, episode_id=ep.id
            ),
            "budgets": [
                {
                    "scope_id": row.id,
                    "charged": {key: value for key, value in row.charged.items() if key not in TOKEN_USAGE_BUCKETS},
                    "provider_usage": provider_usage(db, row),
                    "allowances": _allowances(db, row),
                }
                for row in [*chain, ep]
            ],
            "facts": [
                {
                    "id": f.id,
                    "kind": f.kind,
                    "sha256": f.body_sha,
                    "target_id": f.target_id,
                    "parent_id": f.parent_id,
                    "sequence": f.sequence,
                }
                for f in facts
            ],
        }
        fact = append_fact(
            db,
            ep.artifact_id,
            operation_id=key,
            kind="review_packet",
            parent_id=ep.id,
            body=body,
        )
        return {"fact_id": fact.id, "sha256": fact.body_sha}


def _wire_size(value):
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode())


def exact_page(body, *, fact_id, sha256, offset, limit):
    """Offset is in canonical ASCII JSON bytes, so it cannot bisect UTF-8."""
    digest, canonical = store.canonical(body)
    if digest != sha256:
        raise store.HistoryConflict("Exact history SHA mismatch")
    text = json.dumps(
        canonical, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    )
    if type(offset) is not int or not 0 <= offset <= len(text):
        raise ValueError("Invalid history continuation offset")

    def page(end):
        return {
            "fact_id": fact_id,
            "sha256": digest,
            "encoding": "canonical-json-ascii",
            "offset": offset,
            "next_offset": end if end < len(text) else None,
            "total_bytes": len(text),
            "content": text[offset:end],
        }

    low, high = offset, min(len(text), offset + limit)
    while low < high:
        mid = (low + high + 1) // 2
        if _wire_size(page(mid)) <= limit:
            low = mid
        else:
            high = mid - 1
    result = page(low)
    if _wire_size(result) > limit or (low == offset and offset < len(text)):
        raise ValueError(
            "Configured packet size cannot carry history continuation metadata"
        )
    return result


def read(scope, episode_id, *, fact_id, sha256, offset=0):
    from .observations import record_read

    with store.SessionLocal.begin() as db:
        ep, _ = lock_episode(db, scope, episode_id, require_open=False)
        if fact_id == "baseline":
            artifact = store._artifact(db, ep.artifact_id, scope)
            if artifact.baseline_sha != sha256:
                raise store.HistoryConflict("Baseline SHA mismatch")
            return record_read("baseline", exact_page(
                db.get(ValidationBody, sha256).content,
                fact_id="baseline",
                sha256=sha256,
                offset=offset,
                limit=ep.policy["packet_bytes"],
            ))
        fact = store._fact(db, ep.artifact_id, fact_id)
        if fact.body_sha != sha256:
            raise store.HistoryConflict("History reference SHA mismatch")
        body = store._content(db, fact)
        page = exact_page(body, fact_id=fact.id, sha256=sha256,
                          offset=offset, limit=ep.policy["packet_bytes"])
        from . import source_inputs
        if fact.kind == "validation_source_input" and source_inputs.is_applicability(body):
            # Observe scientific content, not fresh prerequisite selection IDs.
            # The caller still receives the untouched hash-pinned history page.
            content = source_inputs.applicability_scientific_content(body)
            digest = store.canonical(content)[0]
            record_read(fact.kind, {**page, "sha256": digest})
            return page
        return record_read(fact.kind, page)


def _outstanding_rows(db, scope, ep, episode_id):
    """Every live outstanding row of the bound episode, in stable keyset order.

    A validator answer the reviewer already decided is offered again, with its
    exact references, when continuation's own test (``continuation.decidable``)
    says it can be decided now: something new arrived since, or a later round
    began. While it waits, continuation runs for it only to use a re-check or
    reconsideration that is still left.
    """
    from . import continuation, source_inputs
    from .record_scope import allowed_units, review_blockers
    from .record_source import evidence_groups
    from src.lib.validation_history.selections import effective_episode

    episode = store._fact(db, ep.artifact_id, episode_id, kind="review_episode")
    coverage = effective_episode(db, episode)
    artifact = store._artifact(db, ep.artifact_id, scope)
    manifest = manifest_in_transaction(db, scope, ep.artifact_id, episode_id=episode_id)
    blockers = review_blockers(manifest)
    units = {unit["target_id"]: unit for unit in manifest["units"]
             if unit["target_id"] in allowed_units(coverage)}
    obligations = {item["target_id"]: item for item in coverage["obligations"]}
    targets = {fact.id: fact for fact in db.scalars(select(ValidationFact).where(
        ValidationFact.artifact_id == ep.artifact_id, ValidationFact.kind == "target"))}
    groups = evidence_groups(db, artifact)
    codes = {}
    for blocker in blockers:
        if blocker["target_id"] is not None:
            codes.setdefault(blocker["target_id"], set()).add(blocker["code"])
    subjects = continuation.review_subjects(
        db, scope, ep, allowed_units(coverage), {tid: obligations[tid]["mode"] for tid in units}).units

    def source_record(target_id):
        while target_id is not None:
            fact = targets[target_id]
            if "object_ref" in store._content(db, fact):
                return target_id
            target_id = fact.parent_id
        return None

    rows = []
    for blocker in blockers:
        unit_id = blocker["target_id"]
        row = dict(blocker)
        group = ""
        if unit_id is not None:
            unit = units[unit_id]
            target = targets[unit_id]
            body = store._content(db, target)
            parent = target.parent_id if body.get("decision_unit") is not None else target.id
            history_known = row["code"] != "historical_review_unknown"
            row.update(target_id=parent, decision_unit_id=unit_id,
                expected_version=unit["version"], mode=obligations[unit_id]["mode"],
                agent_may_decide=obligations[unit_id]["mode"] != "curator" and history_known,
                record_target_id=source_record(unit_id),
                unit_ref={"fact_id": target.id, "sha256": target.body_sha})
            record_target = row["record_target_id"]
            if record_target is not None:
                group = groups.get(store._content(db, targets[record_target])["object_ref"], "")
                if group:
                    row["evidence_group"] = group
            source = source_inputs.latest(db, ep.artifact_id, parent)
            if source is not None and source_inputs.is_applicability(store._content(db, source)):
                row["applicability_ref"] = source_inputs.reference(source)
            if obligations[unit_id]["mode"] == "extraction_only" and history_known:
                row["extraction_ref"] = artifact.baseline_sha
            answer = subjects[unit_id]["answer"]
            if (row["code"] == "unreviewed" and answer is not None
                    and "answer_review_pending" not in codes[unit_id]
                    and continuation.decidable(codes[unit_id], subjects[unit_id])):
                attempt, completion = answer
                row.update(attempt_ref={"fact_id": attempt.id, "sha256": attempt.body_sha},
                           completion_ref={"fact_id": completion.id, "sha256": completion.body_sha})
        # Each answer remains independently reachable, even for one unit.
        # Baseline evidence is immutable, so this keeps a stable keyset order.
        key = json.dumps([group, unit_id or "", blocker["code"],
            blocker.get("attempt_ref", {}).get("fact_id", ""),
            blocker.get("completion_ref", {}).get("fact_id", ""),
            blocker.get("binding_id", "")], separators=(",", ":"))
        rows.append((key, row))
    rows.sort(key=lambda item: item[0])
    return manifest, blockers, rows


def outstanding(scope, episode_id, *, cursor=None):
    """Live, scoped keyset scan; a completed page never substitutes for readiness.

    The cursor is an after-key, not an authorization grant. Every call checks
    the bound scope/episode and computes readiness from the entire live manifest.
    """
    import base64
    from collections import Counter
    from dataclasses import asdict
    from .record_scope import review_ready
    from src.lib.openai_agents.config import get_validation_review_source_group_records

    with store.SessionLocal.begin() as db:
        ep, _ = lock_episode(db, scope, episode_id, require_open=False)
        context_key = store.canonical({"scope": asdict(scope), "episode_id": episode_id})[0]
        after = ""
        if cursor is not None:
            try:
                if not isinstance(cursor, str) or len(cursor) > ep.policy["packet_bytes"]:
                    raise ValueError("Invalid cursor size")
                decoded = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
                if (set(decoded) != {"context", "after"} or decoded["context"] != context_key
                        or not isinstance(decoded["after"], str)):
                    raise ValueError("Cursor belongs to another review scope")
                after = decoded["after"]
            except (ValueError, TypeError, UnicodeError) as error:
                raise ValueError("Invalid outstanding-review cursor for this bound episode") from error

        def encode(key):
            return base64.urlsafe_b64encode(json.dumps(
                {"context": context_key, "after": key}, separators=(",", ":")).encode()).decode()

        manifest, blockers, rows = _outstanding_rows(db, scope, ep, episode_id)
        remaining = [(key, row) for key, row in rows if key > after]
        page = {
            "episode_id": episode_id, "ready": review_ready(manifest),
            "artifact_ready": manifest["ready"], "state": ep.state,
            "source_state_sha256": manifest["source_state_sha256"],
            "decision_entries": ep.policy["decision_entries"],
            "group_limit": get_validation_review_source_group_records(),
            "outstanding_units": len({item["target_id"] for item in blockers if item["target_id"] is not None}),
            "outstanding_rows": len(rows), "remaining_rows": len(remaining),
            "global_blockers": dict(Counter(item["code"] for item in blockers if item["target_id"] is None)),
            "rows": [], "next_cursor": None, "scan_complete": not remaining,
        }
        for index, (key, row) in enumerate(remaining):
            candidate = {**page, "rows": [*page["rows"], row],
                "next_cursor": encode(key) if index + 1 < len(remaining) else None,
                "remaining_rows": len(remaining) - index - 1,
                "scan_complete": index + 1 == len(remaining)}
            if _wire_size(candidate) > ep.policy["packet_bytes"]:
                break
            page = candidate
        if _wire_size(page) > ep.policy["packet_bytes"] or (remaining and not page["rows"]):
            raise ValueError("Configured packet size cannot carry outstanding-review metadata; use exact review history")
        return page


# A compact first-pass view of all outstanding review work. The model reads
# records, evidence and validator answers once and saves by short handle; the
# pinned fact maps each handle to its exact target, answer and version.
PACKET_KIND = "review_decision_packet"


def _filled(value):
    return value not in (None, "", [], {})


class _PacketEvidence:
    """Each quoted text once, under a short handle.

    Copies of one quote share an entry when their place in the paper agrees and
    their citation fields (entity, agent note, supported field paths) are equal;
    missing place fields are filled from the agreeing copy. Any other copy gets
    its own entry that points at the first one instead of repeating the quote.
    """

    def __init__(self):
        self.entries = {}
        self.cited = []
        self._quotes = {}
        self._unquoted = {}
        self._ids = {}

    def handle(self, record):
        from .record_source import PACKET_CITATION_FIELDS, PACKET_LOCATION_FIELDS, packet_evidence, packet_quote

        compact = packet_evidence(record)
        identity = record.get("evidence_record_id")
        quote = packet_quote(compact)
        handle = None
        if quote is None:
            key = store.canonical({"view": compact, "id": identity})[0]
            handle = self._unquoted.get(key)
        else:
            place = {name: compact[name] for name in PACKET_LOCATION_FIELDS if name in compact}
            citation = {name: compact.get(name) for name in PACKET_CITATION_FIELDS}
            for candidate, (known_place, known_citation) in self._quotes.get(quote, {}).items():
                if known_citation == citation and all(known_place[name] == value for name, value in place.items()
                                                      if name in known_place):
                    known_place.update(place)
                    self.entries[candidate].update(place)
                    handle = candidate
                    break
        if handle is None:
            handle = "E" + str(len(self.entries) + 1)
            if quote is None:
                self.entries[handle] = compact
                self._unquoted[key] = handle
            else:
                copies = self._quotes.setdefault(quote, {})
                if copies:
                    compact = {"same_quote_as": next(iter(copies)),
                               **{name: value for name, value in compact.items() if packet_quote({name: value}) is None}}
                self.entries[handle] = compact
                copies[handle] = (place, citation)
        if isinstance(identity, str):
            self._ids.setdefault(identity, handle)
        self.cited.append(handle)
        return handle

    def known(self, identity):
        """Name an evidence ID by handle once its record is in the packet."""
        handle = self._ids.get(identity)
        if handle is not None:
            self.cited.append(handle)
        return handle or identity

    def inline(self, value):
        """Saved evidence records inside values cite their handle instead of repeating quotes."""
        from .record_source import is_packet_evidence

        if isinstance(value, dict):
            if is_packet_evidence(value):
                return self.handle(value)
            return {key: self.inline(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.inline(item) for item in value]
        return value


def _cite(evidence, records):
    return list(dict.fromkeys(evidence.handle(record) for record in records or []))


def _packet_result(result, evidence):
    """The validator's own judgment; lookup logs and provider detail stay in history."""
    from src.schemas.domain_validator import DomainValidatorResultBase

    view = {}
    for key, name in (("status", "status"), ("resolved_values", "values"), ("explanation", "explanation"),
                      ("curator_message", "curator_message"), ("unresolved_questions", "questions"),
                      ("output_issues", "issues"), ("missing_expected_fields", "missing")):
        if _filled(result.get(key)):
            view[name] = result[key]
    components = {key: {name: value for name, value in item.items() if _filled(value)}
                  for key, item in (result.get("field_resolutions") or {}).items()}
    if components:
        view["components"] = components
    resolved = [{key: value for key, value in item.items()
                 if isinstance(value, (str, int, float, bool)) and value != ""}
                for item in result.get("resolved_objects") or []]
    if any(resolved):
        view["resolved"] = [item for item in resolved if item]
    if result.get("status") != "resolved":
        candidates = []
        for candidate in result.get("candidates") or []:
            item = {key: candidate[key] for key in ("value", "label", "object_type", "score", "matched_fields")
                    if _filled(candidate.get(key))}
            assessment = (candidate.get("details") or {}).get("scientific_assessment")
            if isinstance(assessment, dict):
                assessment = {key: value for key, value in assessment.items() if _filled(value)}
                if "evidence_record_ids" in assessment:
                    assessment["evidence"] = [evidence.known(identity)
                                              for identity in assessment.pop("evidence_record_ids")]
                if assessment:
                    item["assessment"] = assessment
            candidates.append(item)
        if candidates:
            view["candidates"] = candidates
    # Validator-specific result fields can hold the whole verdict, so they are
    # shown as returned; only lookup logs stay in exact history, at any depth.
    additional = _without_lookup_logs(evidence.inline({
        key: value for key, value in result.items()
        if key not in DomainValidatorResultBase.model_fields and _filled(value)}), evidence)
    if additional:
        view["additional_fields"] = additional
    return view


def _without_lookup_logs(value, evidence):
    if isinstance(value, dict):
        return {key: ([evidence.known(identity) for identity in item]
                      if key.endswith("evidence_record_ids") and isinstance(item, list)
                      and all(isinstance(identity, str) for identity in item)
                      else _without_lookup_logs(item, evidence))
                for key, item in value.items() if key != "lookup_attempts"}
    if isinstance(value, list):
        return [_without_lookup_logs(item, evidence) for item in value]
    return value


def _packet_request(attempt, evidence, review):
    """What a validation attempt was asked: validator, field and values sent."""
    frozen = attempt["input"]
    request = frozen["request"]
    item = {"review": review, "validator": frozen["binding_id"]}
    if request["target"].get("field_path"):
        item["field"] = request["target"]["field_path"]
    if frozen.get("route_value") is not None:
        item["route"] = frozen["route_value"]
    sent = evidence.inline(request.get("selected_inputs") or {})
    if sent:
        item["sent"] = sent
    cited = _cite(evidence, request.get("evidence"))
    if cited:
        item["evidence"] = cited
    return item


def _packet_answer(attempt, completion, evidence):
    item = _packet_request(attempt, evidence, "validator_answer")
    if completion["state"] != "completed":
        item["outcome"] = completion["state"]
        reason = (completion.get("diagnostics") or {}).get("reason")
        if reason:
            item["outcome_reason"] = reason
    item.update(_packet_result(completion.get("result") or {}, evidence))
    return item


def _packet_applicability(body, evidence):
    content = body["input"]
    item = {"review": "optional_input_absent" if content.get("absent_inputs") else "lookup_not_supported",
            "validator": content["binding_id"]}
    if content.get("absent_inputs"):
        item["absent_inputs"] = content["absent_inputs"]
    if content.get("unsupported_preflight"):
        item["explanation"] = content["unsupported_preflight"]["explanation"]
    sent = evidence.inline(content.get("selected_inputs") or {})
    if sent:
        item["sent"] = sent
    cited = _cite(evidence, content.get("evidence"))
    if cited:
        item["evidence"] = cited
    return item


IDENTITY_RECORD = "Accepting keeps this record's own resolution_state; send it without unresolved_fields."


def _build_packet(db, scope, ep, episode_id):
    from collections import Counter
    from src.lib.domain_packs.resolvable_values import has_resolution_state
    from . import source_inputs
    from .record_scope import review_ready
    from .record_source import _source_fact, packet_record

    manifest, _, rows = _outstanding_rows(db, scope, ep, episode_id)
    artifact = store._artifact(db, ep.artifact_id, scope)
    units = {unit["target_id"]: unit for unit in manifest["units"]}
    unit_order = {unit["target_id"]: index for index, unit in enumerate(manifest["units"])}
    original = db.get(ValidationBody, artifact.baseline_sha).content["normalized"]["extracted_objects"]
    record_order = {item.get("object_id") or item.get("pending_ref_id"): index
                    for index, item in enumerate(original)}
    uncertain = {fact.parent_id for fact in db.scalars(select(ValidationFact).where(
        ValidationFact.artifact_id == ep.artifact_id, ValidationFact.kind == "review_uncertainty"))}
    grouped, global_blockers = {}, Counter()
    for _, row in rows:
        if row.get("decision_unit_id") is None:
            global_blockers[row["code"]] += 1
        else:
            grouped.setdefault(row["record_target_id"], {}).setdefault(row["decision_unit_id"], []).append(row)

    from .extraction_scheduling import followup_available

    # Whether a targeted re-check is still open for each record in this round.
    followups = followup_available(db, ep, artifact.provenance,
                                   [target for target in grouped if target is not None])

    def record_key(target_id):
        if target_id is None:
            return (2, 0, "")
        reference = store._content(db, store._fact(db, ep.artifact_id, target_id, kind="target"))["object_ref"]
        return (0, record_order[reference], target_id) if reference in record_order else (1, 0, target_id)

    def unit_body(unit_id):
        return store._content(db, store._fact(db, ep.artifact_id, unit_id, kind="target"))

    def subjects(unit_id, unit_rows):
        """The exact answer, applicability fact or extraction each row offers for decision.

        A validation still running for the unit means any older answer may be
        superseded, so the whole unit waits without a handle. An attempt that
        stopped with a recorded operational uncertainty is not running: it can be
        acknowledged as a failure.
        """
        if any("attempt_ref" in row and "completion_ref" not in row
               and row["attempt_ref"]["fact_id"] not in uncertain for row in unit_rows):
            return {}
        found = {}
        for row in unit_rows:
            if not row["agent_may_decide"]:
                continue
            if "completion_ref" in row:
                found.setdefault(("answer", row["attempt_ref"]["fact_id"]), row)
            elif "attempt_ref" in row:
                found.setdefault(("uncertain", row["attempt_ref"]["fact_id"]), row)
            elif "applicability_ref" in row:
                found.setdefault(("applicability", row["applicability_ref"]["fact_id"]), row)
            elif "extraction_ref" in row:
                found.setdefault(("record", row["extraction_ref"]), row)
            elif row["code"] == "episode_review_pending" and units[unit_id].get("attempt_id"):
                found.setdefault(("answer", units[unit_id]["attempt_id"]), row)
        return found

    def prerequisite_refs(pins):
        """Each selection a dependent request used and the answer or source it selected.

        Both are exact references for read_validation_history; a curator-selected
        value is named, since its authority is the curator's edit history.
        """
        references = []
        for pin in pins:
            selection = store._fact(db, pin["artifact_id"], pin["selection_id"], kind="selection")
            chosen = store._content(db, selection)["selected"]
            if chosen == "baseline":
                selected = {"fact_id": "baseline", "sha256": artifact.baseline_sha}
            elif chosen == "curator":
                selected = "curator"
            else:
                fact = store._fact(db, pin["artifact_id"], chosen)
                selected = {"fact_id": fact.id, "sha256": fact.body_sha}
            references.append({"selection": {"fact_id": selection.id, "sha256": selection.body_sha},
                               "selected": selected})
        return references

    evidence = _PacketEvidence()
    records, handles, index, record_labels = [], {}, [], {}
    numbered = 0
    for record_target in sorted(grouped, key=record_key):
        evidence.cited = []
        reads_ = []
        label = None
        if record_target is not None:
            numbered += 1
            label = "R" + str(numbered)
        item = {"record": label}
        # A record whose whole value carries a resolution state is an identity:
        # binding refuses every field of it as review uncertainty.
        identity_record = False
        if record_target is not None:
            item["target_id"] = record_target
            item["follow_up_available"] = followups[record_target]
            try:
                fact = _source_fact(db, artifact, record_target)
            except store.HistoryConflict as error:
                item["source_unavailable"] = str(error)
            else:
                source = store._content(db, fact)
                identity_record = has_resolution_state(source["record"]["payload"])
                item.update(packet_record(source["record"]))
                item["values"] = evidence.inline(item["values"])
                for identity in (source["record"].get("object_id"), source["record"].get("pending_ref_id")):
                    if identity is not None:
                        record_labels.setdefault(identity, label)
                cited = _cite(evidence, source["evidence_records"])
                if cited:
                    item["evidence"] = cited
                reads_.append(["review_record_source", fact.body_sha])
        slots = {}
        for unit_id in sorted(grouped[record_target], key=unit_order.__getitem__):
            unit_rows = grouped[record_target][unit_id]
            found = subjects(unit_id, unit_rows)
            if not found:
                body = unit_body(unit_id)
                waiting = {"review": "waiting", "codes": sorted({row["code"] for row in unit_rows}),
                           "target_id": unit_rows[0]["target_id"]}
                if body.get("binding_id"):
                    waiting["validator"] = body["binding_id"]
                if body.get("decision_unit"):
                    waiting["component"] = body["decision_unit"]["key"]
                if units[unit_id]["review"] not in (None, "pending"):
                    waiting["earlier_decision"] = units[unit_id]["review"]
                if any(row["mode"] == "curator" for row in unit_rows):
                    waiting["decided_by"] = "curator"
                slots[("waiting", unit_id)] = {"entry": waiting, "units": []}
                continue
            for key, row in found.items():
                if key not in slots:
                    kind, subject = key
                    if kind == "answer":
                        attempt = store._fact(db, ep.artifact_id, subject, kind="attempt")
                        completion = db.scalar(select(ValidationFact).where(
                            ValidationFact.artifact_id == ep.artifact_id, ValidationFact.kind == "completion",
                            ValidationFact.parent_id == attempt.id))
                        frozen = store._content(db, attempt)
                        entry = _packet_answer(frozen, store._content(db, completion), evidence)
                        if frozen["input"].get("prerequisites"):
                            entry["prerequisites"] = prerequisite_refs(frozen["input"]["prerequisites"])
                        reads_.extend([["attempt", attempt.body_sha], ["completion", completion.body_sha]])
                        ref = {"logical_attempt_id": subject}
                    elif kind == "uncertain":
                        attempt = store._fact(db, ep.artifact_id, subject, kind="attempt")
                        entry = _packet_request(store._content(db, attempt), evidence, "uncertain_outcome")
                        reads_.append(["attempt", attempt.body_sha])
                        ref = {"logical_attempt_id": subject, "uncertain_outcome": True}
                    elif kind == "applicability":
                        declared = store._content(db, store._fact(
                            db, ep.artifact_id, subject, kind="validation_source_input"))
                        entry = _packet_applicability(declared, evidence)
                        if declared["input"].get("prerequisites"):
                            entry["prerequisites"] = prerequisite_refs(declared["input"]["prerequisites"])
                        reads_.append(["validation_source_input", store.canonical(
                            source_inputs.applicability_scientific_content(declared))[0]])
                        ref = {"applicability_ref": row["applicability_ref"]}
                    else:
                        entry = {"review": "record"}
                        if identity_record:
                            entry["identity_record"] = IDENTITY_RECORD
                        ref = {"extraction_ref": subject}
                    slots[key] = {"entry": entry, "units": [], "ref": ref}
                slots[key]["units"].append((unit_id, row))
        review = []
        for slot in slots.values():
            entry = slot["entry"]
            labels = {}
            for unit_id, row in slot["units"]:
                handle = "D" + str(len(handles) + 1)
                handles[handle] = {"record": label, "target_id": row["target_id"], "decision_unit_id": unit_id,
                                   "expected_version": row["expected_version"], **slot["ref"]}
                component = (unit_body(unit_id).get("decision_unit") or {}).get("key")
                name = component or "whole"
                while name in labels:
                    name += "+"
                labels[name] = handle
            earlier = {name: units[unit_id]["review"] for name, (unit_id, _) in zip(labels, slot["units"])
                       if units[unit_id]["review"] not in (None, "pending")}
            if list(labels) == ["whole"]:
                entry["handle"] = labels["whole"]
                if earlier:
                    entry["earlier_decision"] = earlier["whole"]
            elif labels:
                entry["handles"] = labels
                if earlier:
                    entry["earlier_decisions"] = earlier
            review.append(entry)
        item["review"] = review
        records.append(item)
        index.append({"evidence": list(dict.fromkeys(evidence.cited)), "reads": reads_})

    def related(reference):
        return (record_labels.get(reference.get("object_id"))
                or record_labels.get(reference.get("pending_ref_id")))

    # References between records in this packet read as record handles.
    for item in records:
        if "object_refs" in item:
            item["object_refs"] = [related(ref) or ref for ref in item["object_refs"]]
        if "field_refs" in item:
            item["field_refs"] = [{"record": related(ref["object_ref"]), "field_path": ref["field_path"]}
                                  if related(ref["object_ref"]) else ref for ref in item["field_refs"]]
    return {
        "episode_id": ep.id, "artifact_id": ep.artifact_id,
        "manifest_sha256": manifest["sha256"], "source_state_sha256": manifest["source_state_sha256"],
        "ready": review_ready(manifest), "baseline": {"fact_id": "baseline", "sha256": artifact.baseline_sha},
        "decision_entries": ep.policy["decision_entries"], "global_blockers": dict(global_blockers),
        "evidence": evidence.entries, "records": records, "handles": handles, "index": index,
    }


def _pin_packet(db, scope, ep, episode_id):
    """The same live state pins the same packet; any change pins a new one."""
    body = _build_packet(db, scope, ep, episode_id)
    operation_id = "review-decision-packet:" + ep.id + ":" + store.canonical(body)[0]
    existing = db.scalar(select(ValidationFact).where(
        ValidationFact.artifact_id == ep.artifact_id, ValidationFact.operation_id == operation_id))
    if existing is not None:
        if existing.kind != PACKET_KIND or existing.parent_id != ep.id:
            raise store.HistoryConflict("Review packet identity conflict")
        return existing
    return append_fact(db, ep.artifact_id, operation_id=operation_id, kind=PACKET_KIND,
                       parent_id=ep.id, body=body)


def _packet_pages(body, packet_id, limit):
    """Whole records per page; each evidence entry rides with the first record citing it."""
    header = {"packet_id": packet_id, "page": 10**6, "pages": 10**6, "next_page": 10**6,
              "ready": body["ready"],
              "record_count": sum(1 for record in body["records"] if record["record"] is not None),
              "answer_count": sum(1 for record in body["records"] for item in record["review"]
                                  if item["review"] == "validator_answer"),
              "decision_count": len(body["handles"]), "decision_entries": body["decision_entries"],
              "source_state_sha256": body["source_state_sha256"], "baseline": body["baseline"],
              "global_blockers": body["global_blockers"]}

    def view(part):
        return {**header, "evidence": {handle: body["evidence"][handle] for handle in part["evidence"]},
                "records": [body["records"][position] for position in part["records"]],
                "too_large": part["too_large"]}

    def fits(part):
        return _wire_size(view(part)) <= limit

    def empty():
        return {"records": [], "evidence": [], "too_large": []}

    pages, placed, current = [], set(), empty()
    if not fits(current):
        raise ValueError("Configured packet size cannot carry review packet metadata; use exact review history")
    for position, record in enumerate(body["records"]):
        def needed():
            return [handle for handle in body["index"][position]["evidence"]
                    if handle not in placed and handle not in current["evidence"]]
        trial = {**current, "records": [*current["records"], position], "evidence": [*current["evidence"], *needed()]}
        if not fits(trial) and (current["records"] or current["too_large"]):
            pages.append(current)
            placed.update(current["evidence"])
            current = empty()
            trial = {**current, "records": [position], "evidence": needed()}
        if fits(trial):
            current = trial
            continue
        stub = {"record": record["record"], "target_id": record.get("target_id"),
                **({"follow_up_available": record["follow_up_available"]}
                   if "follow_up_available" in record else {}),
                "handles": [handle for handle, pinned in body["handles"].items()
                            if pinned["record"] == record["record"]]}
        trial = {**current, "too_large": [*current["too_large"], stub]}
        if not fits(trial):
            pages.append(current)
            placed.update(current["evidence"])
            current = empty()
            trial = {**current, "too_large": [stub]}
            if not fits(trial):
                raise ValueError("Configured packet size cannot carry review packet metadata; use exact review history")
        current = trial
    pages.append(current)
    return header, view, pages


def review_packet(scope, episode_id, *, packet_id=None, page=None):
    """Pin all outstanding work once; later pages read the same immutable packet."""
    from .observations import record_read

    if packet_id is None and page not in (None, 1):
        raise ValueError("A later packet page needs the packet_id it belongs to")
    with store.SessionLocal.begin() as db:
        ep, _ = lock_episode(db, scope, episode_id, require_open=False)
        if packet_id is None:
            fact = _pin_packet(db, scope, ep, episode_id)
        else:
            fact = store._fact(db, ep.artifact_id, packet_id, kind=PACKET_KIND)
            if fact.parent_id != ep.id:
                raise store.HistoryConflict("Review packet belongs to another review round; read a fresh packet")
        fact_id = fact.id
        body = store._content(db, fact)
        limit = ep.policy["packet_bytes"]
    header, view, pages = _packet_pages(body, fact_id, limit)
    number = 1 if page is None else page
    if type(number) is not int or not 1 <= number <= len(pages):
        raise ValueError(f"This review packet has {len(pages)} page(s)")
    result = {**view(pages[number - 1]), "page": number, "pages": len(pages),
              "next_page": number + 1 if number < len(pages) else None}
    if not result["too_large"]:
        del result["too_large"]
    for position in pages[number - 1]["records"]:
        for kind, sha256 in body["index"][position]["reads"]:
            record_read(kind, {"content": sha256, "sha256": sha256, "offset": 0, "next_offset": None})
    return result
