"""Structural continuation eligibility and scoped scientific progress, not acceptance."""

from sqlalchemy import select

from src.lib.validation_history import store, selections
from src.models.sql.validation_history import ValidationFact
from src.models.sql.validation_review import ValidationReviewWork
from . import admission, source_inputs
from .attempt_lineage import counts
from .record_scope import allowed_units, review_blockers


REMINDER = (
    "The recorded review is not complete. Start a fresh scan with "
    "get_outstanding_validation_reviews, follow its pages, and read the exact "
    "source/answer references needed for your assessment. Continue authorized "
    "work and record your decisions. Do not infer readiness from prose or an "
    "empty page, invent a scientific disposition, or reset any allowance."
)
KNOWN_BLOCKERS = frozenset({
    "unreviewed", "episode_review_pending", "answer_review_pending", "execution_pending",
    "stale", "incomplete", "retired", "historical_review_unknown",
    "required_validator_unavailable", "document_retired", "configured_validator_unavailable",
})


def classify(blockers, units):
    """Authorize opportunity to assess/repair, never whether that science is justified.

    Units are trusted runtime facts about authority, existing subjects and installed
    repair capabilities. A per-unit outage does not suppress independent units.
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
        if unit["mode"] == "curator":
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
        if ("answer_review_pending" in codes or "episode_review_pending" in codes
                or ("unreviewed" in codes and unit["has_subject"])
                or ("stale" in codes and unit["has_current_absence"])):
            eligible.add(target)
        elif unit["can_repair"]:
            eligible.add(target)
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
        from .extraction_scheduling import attempt_counts
        def source_entry(tid):
            while tid in targets:
                if "object_ref" in bodies[tid]:
                    return tid
                tid = targets[tid].parent_id
            return None

        entry_roots = {tid: source_entry(tid) for tid in permitted}
        extraction_counts = attempt_counts(db, ep.artifact_id, {entry for entry in entry_roots.values() if entry})
        units = {}
        for unit in manifest["units"]:
            tid = unit["target_id"]
            if tid not in permitted:
                continue
            root = roots[tid]
            mode = obligations[tid]["mode"]
            source = sources.get(root)
            absent = False
            if source is not None and source_inputs.is_absence(bodies[source.id]):
                try:
                    source_inputs.require_absence(db, ep.artifact_id, tid, source.id, scope=scope)
                    absent = True
                except store.HistoryConflict:
                    pass
            used = attempted.get(root, 0)
            can_attempt = used < ep.policy["attempts_per_target"] and (not used or extra_available)
            repair = can_attempt and (
                "validate_selected_extraction_source" in tool_names
                or (used > 0 and "request_validation_reconsideration" in tool_names))
            entry = entry_roots[tid]
            can_investigate = (entry is not None and "request_targeted_extraction" in tool_names
                               and extraction_counts[entry] < ep.policy["extraction_attempts_per_entry"])
            units[tid] = {
                "mode": mode,
                "has_current_absence": absent,
                "has_subject": mode == "extraction_only" or absent or any(
                    tid in bodies[aid]["coverage"] and bodies[completion.id]["state"] == "completed"
                    for aid, completion in completions.items()),
                "can_repair": repair or can_investigate,
                "repair_blocker": "scientific_attempts_exhausted" if not can_attempt else "required_inputs_unavailable",
                "work_states": sorted({work.state for work in works if any(
                    request.get("target_id") == root for request in work.payload.get("requests", []))}),
            }
        blockers = review_blockers(manifest)
        eligible, causes = classify(blockers, units)
        if any(work.state == "uncertain" for work in works):
            eligible = set()
            causes.append({"code": "execution_uncertain", "target_id": None})
        if status["state"] not in {"pending", "complete"}:
            eligible = set()
            causes.append({"code": status["state"], "target_id": None})
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
            if body is not None and source_inputs.is_absence(body):
                # Prerequisite reacceptance must refresh exact stored references,
                # but that alone is not new scientific work for continuation.
                return source_inputs.absence_scientific_content(body)
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
                "causes": causes, "review": status, "families": families}


class ReviewerStalled(RuntimeError):
    """The model exhausted corrective nudges without novel scientific work."""


def terminal(view, refusals, output, *, stalled=False):
    """Keep typed causes and the shared non-complete public outcome shape."""
    causes = [*view["causes"], *refusals]
    structural = sorted({item["code"] for item in causes
                         if item["code"] not in {"HistoryConflict", "ValueError"}})
    reason = structural[0] if structural else ("reviewer_stalled" if stalled else "review_pending")
    if reason == "reviewer_stalled":
        from .audit import report_operational_failure
        report_operational_failure(ReviewerStalled())
    return {"state": "needs_curator_review", "reason": reason, "resource": None,
            "review": view["review"], "causes": causes, "output": output}
