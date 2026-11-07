"""Structural continuation eligibility and scoped scientific progress, not acceptance."""

from sqlalchemy import select

from src.lib.validation_history import store, selections
from src.models.sql.validation_history import ValidationFact, ValidationHead
from src.models.sql.validation_review import ValidationReviewWork
from . import admission, source_inputs
from .attempt_lineage import counts
from .record_scope import allowed_units, review_blockers


REMINDER = (
    "The recorded review is not complete. Read a fresh packet with "
    "read_review_packet, including every page, and decide what remains; in a "
    "curator-requested round, scan with get_outstanding_validation_reviews and "
    "read the exact references you need. Continue authorized work and record "
    "your decisions. Do not infer readiness from prose or an empty page, invent "
    "a scientific disposition, or reset any allowance."
)
# Decisions that record the reviewer's view yet leave a record pending.
PENDING_DECISIONS = frozenset({"reject", "defer", "acknowledge_failure"})
KNOWN_BLOCKERS = frozenset({
    "unreviewed", "episode_review_pending", "answer_review_pending", "execution_pending",
    "stale", "incomplete", "retired", "historical_review_unknown",
    "required_validator_unavailable", "document_retired", "configured_validator_unavailable",
})


def classify(blockers, units):
    """Authorize opportunity to assess/repair, never whether that science is justified.

    Units are trusted runtime facts about authority, existing subjects, the latest
    decision and installed repair capabilities. A per-unit outage does not
    suppress independent units. A unit whose latest decision in this episode left
    it pending, with nothing new since and no re-check left, cannot change in
    another run; it stops with a typed cause instead of a reminder.
    """
    eligible, causes = set(), []
    by_target = {}
    for blocker in blockers:
        code = blocker["code"]
        target = blocker.get("target_id")
        if code not in KNOWN_BLOCKERS:
            return set(), [{"code": "unsupported_review_state", "observed_code": code,
                            "target_id": target}]
        if code == "document_retired":
            return set(), [dict(blocker)]
        by_target.setdefault(target, []).append(blocker)
    for target, pending in by_target.items():
        unit = units.get(target)
        if unit is None:
            causes.extend(dict(item) for item in pending)
            continue
        # A curator's record, or a curator's pending decision in this episode,
        # is the curator's to change: another reviewer run cannot override it.
        if unit["mode"] == "curator" or unit["curator_decided"]:
            causes.append({"target_id": target, "code": "curator_authority_required"})
            continue
        codes = {item["code"] for item in pending}
        blocked = codes & {"configured_validator_unavailable", "required_validator_unavailable",
                           "retired", "historical_review_unknown", "execution_pending"}
        if blocked:
            for item in pending:
                if item["code"] in blocked:
                    causes.append({**item, **({"work_states": unit["work_states"]}
                                             if item["code"] == "execution_pending" else {})})
            continue
        reviewable = ("episode_review_pending" in codes
                      or ("unreviewed" in codes and unit["has_subject"])
                      or ("stale" in codes and unit["has_current_absence"]))
        if ("answer_review_pending" in codes or (reviewable and unit["settled_by"] is None)
                or unit["can_repair"]):
            eligible.add(target)
        elif reviewable:
            causes.append({"target_id": target, "code": "reviewer_left_pending",
                           "decision": unit["settled_by"]})
        else:
            causes.append({"target_id": target, "code": unit["repair_blocker"]})
    return eligible, causes


def inspect(context, tool_names):
    """Read current scope once; no listing facts or durable continuation counters."""
    scope = context.history_scope
    status = admission.status(scope, context.episode_id)
    with store.SessionLocal.begin() as db:
        ep, chain = admission.lock_episode(db, scope, context.episode_id, require_open=False)
        episode = selections.effective_episode(db, store._fact(db, ep.artifact_id, ep.id))
        permitted = allowed_units(episode)
        obligations = {item["target_id"]: item for item in episode["obligations"]
                       if item["target_id"] in permitted}
        manifest = selections.manifest_in_transaction(db, scope, ep.artifact_id, episode_id=ep.id)
        facts = list(db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == ep.artifact_id,
            ValidationFact.kind.in_(["target", "attempt", "completion", "validation_source_input",
                                     "extraction_contribution", "extraction_followup_request"]),
        ).order_by(ValidationFact.sequence)))
        targets = {fact.id: fact for fact in facts if fact.kind == "target"}
        bodies = {fact.id: store._content(db, fact) for fact in facts}

        def ancestry(target_id):
            seen = set()
            while target_id in targets:
                if target_id in seen:
                    raise store.HistoryConflict("Cyclic review target ancestry")
                seen.add(target_id)
                target_id = targets[target_id].parent_id
            return seen

        families = {tid: ancestry(tid) for tid in permitted}
        relevant = set().union(*families.values()) if families else set()
        roots = {tid: targets[tid].parent_id if bodies[tid].get("decision_unit") is not None else tid
                 for tid in permitted}
        validation_roots = {roots[tid] for tid, obligation in obligations.items()
                            if obligation["mode"] == "validation"}
        attempted = counts(db, ep, validation_roots) if validation_roots else {}
        extra_available = all(admission._allowances(db, row).get("extra_attempts") is None
                              or row.charged.get("extra_attempts", 0) < admission._allowances(db, row)["extra_attempts"]
                              for row in [*chain, ep])
        sources = {}
        attempts = {fact.id: fact for fact in facts if fact.kind == "attempt"
                    and permitted.intersection(bodies[fact.id]["coverage"])}
        completions = {fact.parent_id: fact for fact in facts
                       if fact.kind == "completion" and fact.parent_id in attempts}
        for fact in facts:
            if fact.kind == "validation_source_input" and fact.target_id in relevant:
                sources[fact.target_id] = fact
        works = list(db.scalars(select(ValidationReviewWork).where(
            ValidationReviewWork.episode_id == ep.id)))
        from .extraction_scheduling import followup_available
        from .extractor_adapter import followup_blocker
        provenance = store._artifact(db, ep.artifact_id, scope).provenance
        followup_ok = followup_blocker(
            provenance.get("agent_key"), provenance.get("extractor_contract")) is None
        def source_entry(tid):
            while tid in targets:
                if "object_ref" in bodies[tid]:
                    return tid
                tid = targets[tid].parent_id
            return None

        entry_roots = {tid: source_entry(tid) for tid in permitted}
        followups = followup_available(db, ep, provenance,
                                       sorted({entry for entry in entry_roots.values() if entry}))
        decided_here = set()
        for fact in db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == ep.artifact_id,
            ValidationFact.kind == "episode_disposition",
            ValidationFact.parent_id == ep.id,
        )):
            decided_here.update(store._content(db, fact)["selection_ids"])
        heads = {head.target_id: store._fact(db, ep.artifact_id, head.fact_id, kind="selection")
                 for head in db.scalars(select(ValidationHead).where(
                     ValidationHead.artifact_id == ep.artifact_id,
                     ValidationHead.target_id.in_(permitted)))}

        def latest_input(tid):
            """Newest saved answer, request, contribution or source for this unit's lineage."""
            family = families[tid]
            sequences = [fact.sequence for aid, fact in attempts.items() if tid in bodies[aid]["coverage"]]
            sequences += [completions[aid].sequence for aid in attempts
                          if aid in completions and tid in bodies[aid]["coverage"]]
            sequences += [fact.sequence for fact in facts
                          if (fact.kind in {"extraction_contribution", "validation_source_input"}
                              and fact.target_id in family)
                          or (fact.kind == "extraction_followup_request"
                              and family.intersection(bodies[fact.id]["entry_ids"]))]
            return max(sequences, default=0)

        def pending_decision(tid):
            """Who left the unit pending with this episode's latest decision, and how."""
            head = heads.get(tid)
            if head is None or head.id not in decided_here:
                return None, None
            body = store._content(db, head)
            if body["action"] not in PENDING_DECISIONS:
                return None, None
            return body["actor"]["type"], body["action"]

        def settled_by(tid):
            """The reviewer's latest decision of this episode that left the unit pending,
            if nothing arrived since. A curator's decision is held separately (curator_decided)."""
            actor, action = pending_decision(tid)
            if action is None or actor == "curator" or heads[tid].sequence < latest_input(tid):
                return None
            return action

        units = {}
        for unit in manifest["units"]:
            tid = unit["target_id"]
            if tid not in permitted:
                continue
            root = roots[tid]
            mode = obligations[tid]["mode"]
            source = sources.get(root)
            absent = False
            if source is not None and source_inputs.is_applicability(bodies[source.id]):
                try:
                    source_inputs.require_applicability(db, ep.artifact_id, tid, source.id, scope=scope)
                    absent = True
                except store.HistoryConflict:
                    pass
            used = attempted.get(root, 0)
            can_attempt = used < ep.policy["attempts_per_target"] and (not used or extra_available)
            repair = can_attempt and (
                (followup_ok and "validate_selected_extraction_source" in tool_names)
                or (used > 0 and "request_validation_reconsideration" in tool_names))
            entry = entry_roots[tid]
            can_investigate = (entry is not None and "request_targeted_extraction" in tool_names
                               and followups[entry])
            units[tid] = {
                "mode": mode,
                "record": entry,
                "has_current_absence": absent,
                "has_subject": mode == "extraction_only" or absent or any(
                    tid in bodies[aid]["coverage"] and bodies[completion.id]["state"] == "completed"
                    for aid, completion in completions.items()),
                "settled_by": settled_by(tid),
                "curator_decided": pending_decision(tid)[0] == "curator",
                # Validator reconsideration and source assessment repair only
                # validation units; an extraction-only record changes through
                # the extractor follow-up alone.
                "can_repair": (repair and mode == "validation") or can_investigate,
                "repair_blocker": "scientific_attempts_exhausted" if not can_attempt else "required_inputs_unavailable",
                "work_states": sorted({work.state for work in works if any(
                    request.get("target_id") == root for request in work.payload.get("requests", []))}),
            }
        blockers = review_blockers(manifest)
        eligible, causes = classify(blockers, units)
        pending = pending_records(blockers, units, eligible, causes)
        # An operational stop is not a reviewer outcome: it keeps the plain
        # "paused" notice instead of naming records the reviewer left pending.
        operational = any(item["code"] in {"document_retired", "unsupported_review_state"}
                          for item in causes)
        if any(work.state == "uncertain" for work in works):
            eligible, operational = set(), True
            causes.append({"code": "execution_uncertain", "target_id": None})
        if status["state"] not in {"pending", "complete"}:
            eligible, operational = set(), True
            causes.append({"code": status["state"], "target_id": None})
        if operational:
            pending = []
        from .source_selection import selected_entries
        selected_sources = selected_entries(db, ep.artifact_id)
        assessments = {}
        for unit in manifest["units"]:
            if unit["target_id"] not in permitted or unit["selection_id"] is None:
                continue
            selection = store._fact(db, ep.artifact_id, unit["selection_id"], kind="selection")
            decision = store._content(db, store._fact(db, ep.artifact_id, selection.parent_id, kind="decision"))
            for entry in decision["entries"]:
                if entry["target_id"] == unit["target_id"]:
                    assessments[unit["target_id"]] = {key: entry.get(key) for key in
                                                     ("absence_assessment", "unresolved_fields")}
        def scientific_subject(unit):
            selected = unit["selected"]
            body = bodies.get(selected)
            if body is not None and source_inputs.is_applicability(body):
                # Prerequisite reacceptance must refresh exact stored references,
                # but that alone is not new scientific work for continuation.
                return source_inputs.applicability_scientific_content(body)
            return selected

        # Exclude head versions, selection IDs/generations, operation IDs and
        # audit facts. Bounded real attempts/contributions remain exact identities.
        scientific = {
            "units": [(u["target_id"], scientific_subject(u), u["review"], u["reason"])
                      for u in manifest["units"] if u["target_id"] in permitted],
            "blockers": blockers,
            "assessments": assessments,
            "selected_sources": [(tid, {key: body.get(key) for key in
                                         ("entry", "evidence", "relationships", "retired_ids")})
                                 for tid, (_, body) in sorted(selected_sources.items()) if tid in relevant],
            "followups": [(f.id, f.body_sha) for f in facts if f.kind == "extraction_followup_request"
                          and relevant.intersection(bodies[f.id]["entry_ids"])],
            "sources": [(tid, {key: value for key, value in (bodies[f.id].get("input") or {}).items()
                               if key != "prerequisites"}) for tid, f in sorted(sources.items())],
            "attempts": [(f.id, f.body_sha) for f in attempts.values()],
            "completions": [(f.id, f.body_sha) for f in completions.values()],
            "contributions": [(f.id, f.body_sha) for f in facts
                              if f.kind == "extraction_contribution" and f.target_id in relevant],
        }
        return {"signature": store.canonical(scientific)[0], "eligible": eligible,
                "causes": causes, "pending": pending, "review": status, "families": families}


def pending_records(blockers, units, eligible, causes):
    """Each pending record once, with a plain structural reason; never a scientific verdict.

    Records are named by their saved source entry. The reason says only who
    holds the record now: the curator, an operational block, the reviewer's own
    pending decision, or no decision yet.
    """
    blocked = {item["target_id"] for item in causes
               if item["code"] not in {"reviewer_left_pending", "curator_authority_required"}}
    records = {}
    for blocker in blockers:
        tid = blocker["target_id"]
        if tid is None:
            continue
        unit = units[tid]
        if unit["mode"] == "curator" or unit["curator_decided"]:
            why = "curator_decision"
        elif tid in blocked or unit["settled_by"] == "acknowledge_failure":
            why = "cannot_check"
        elif unit["settled_by"] is not None:
            why = "not_accepted"
        else:
            why = "not_decided"
        item = records.setdefault(unit["record"], {"record_target_id": unit["record"], "why": why,
                                                   "recheck_left": False})
        if WHY_ORDER.index(why) < WHY_ORDER.index(item["why"]):
            item["why"] = why
        item["recheck_left"] = item["recheck_left"] or (tid in eligible and unit["can_repair"])
    return list(records.values())


class ReviewerStalled(RuntimeError):
    """The model exhausted corrective nudges without novel scientific work."""


def terminal(view, refusals, output, *, stalled=False):
    """Keep typed causes and the shared non-complete public outcome shape.

    The current review state explains the stop before any refusal seen during
    the run: a refused follow-up is history, not the reason a record is pending.
    """
    ignored = {"HistoryConflict", "ValueError"}
    current = sorted({item["code"] for item in view["causes"] if item["code"] not in ignored})
    observed = sorted({item["code"] for item in refusals if item["code"] not in ignored})
    reason = (current or observed or ["reviewer_stalled" if stalled else "review_pending"])[0]
    if stalled:
        from .audit import report_operational_failure
        report_operational_failure(ReviewerStalled())
    return {"state": "needs_curator_review", "reason": reason, "resource": None,
            "review": view["review"], "causes": [*view["causes"], *refusals],
            "pending": view["pending"], "output": output}


# Plain curator wording for each pending reason, most binding first.
WHY_ORDER = ("curator_decision", "cannot_check", "not_accepted", "not_decided")
WHY_TEXT = {
    "curator_decision": "it needs a curator's decision",
    "cannot_check": "it could not be checked",
    "not_accepted": "the reviewer did not accept it",
    "not_decided": "the reviewer did not decide it",
}


def pending_message(pending):
    """Curator text: how many records still need a decision, which ones and why.

    Each pending item carries the record label saved with the outcome, so the
    text never depends on the live source.
    """
    parts = []
    for item in pending:
        why = WHY_TEXT[item["why"]]
        if item["why"] in {"not_accepted", "not_decided"} and not item["recheck_left"]:
            why += ", and no re-check is left"
        parts.append(f"{item['label']} ({why})")
    noun = "record" if len(pending) == 1 else "records"
    return (f"Review finished with {len(pending)} {noun} still pending: " + "; ".join(parts)
            + ". Saved results and history are retained.")
