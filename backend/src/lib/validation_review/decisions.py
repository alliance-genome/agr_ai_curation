"""Actor-bound grouped decisions; current values and curator authority change atomically."""

from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from sqlalchemy import select
from src.lib.validation_history import store
from .audit import append_fact
from src.schemas.validation_history import UnresolvedFieldAssessment, HistoryFactReference
from src.lib.validation_history.projection import publish_decisions_in_transaction
from src.models.sql.validation_history import ValidationFact
from .admission import lock_episode
from .policy import ReviewLimits
from .record_scope import review_blockers, review_ready


@dataclass(frozen=True)
class ReviewContext:
    """Constructed by authenticated runtime, never deserialized from tool arguments."""

    history_scope: store.HistoryScope
    episode_id: str
    actor_id: str
    actor_type: str = "agent"


class ReviewEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target_id: StrictStr = Field(description=(
        "Binding target: use the frozen attempt input.target_id for an answer; "
        "for extraction-only review use the authorized source target."
    ))
    decision_unit_id: StrictStr = Field(description=(
        "Reviewed unit: use an ID from the frozen attempt coverage and its manifest "
        "unit target_id. With no component units, decision_unit_id equals target_id: "
        "decision_unit: null and decision_units: [] mean whole-target review, not "
        "missing IDs. With components, use the covered child ID, not the parent. "
        "For extraction-only review use the authorized manifest unit target_id."
    ))
    expected_version: StrictInt = Field(description=(
        "Current version of this decision unit in the manifest, not selection_generation."
    ))
    action: Literal["accept", "reject", "defer", "acknowledge_failure", "revoke"]
    reason: StrictStr
    logical_attempt_id: StrictStr | None = Field(default=None, description=(
        "Exact attempt_ref fact_id for the answer, not its completion_ref fact_id. "
        "Name exactly one attempt, authorized extraction_ref or applicability_ref except for revoke."
    ))
    extraction_ref: StrictStr | None = None
    applicability_ref: HistoryFactReference | None = Field(default=None, description=(
        "Exact source-input fact and hash declaring optional-input absence or an unsupported lookup. Use instead of an attempt or extraction reference."
    ))
    absence_assessment: Literal["omission_supported", "unresolved"] | None = Field(default=None, description=(
        "Required when accepting applicability: optional absence permits omission_supported or unresolved; unsupported lookups require unresolved. Explain the reason."
    ))
    unresolved_fields: list[UnresolvedFieldAssessment] | None = Field(default=None, description=(
        "Only with accept of extraction-only content: exact plain payload field paths "
        "and explanations of remaining scientific uncertainty after assessment. Name list "
        "elements by index, e.g. classification_notes[1]. "
        "Acceptance completes review without claiming these fields are resolved. "
        "Do not use for structured validator identities or incomplete executions."
    ))


class PacketDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    handle: StrictStr = Field(description=(
        "A D handle from the review packet named by packet_id, such as D4. The server "
        "supplies its exact target, answer and the version pinned when the packet was read."
    ))
    action: Literal["accept", "retire", "reject", "defer", "acknowledge_failure", "revoke"] = Field(description=(
        "accept: the paper shows this record or answer is right and answers the question. "
        "retire: the record does not answer the question; any of its handles retires the whole record, "
        "which leaves current results, stays in history and a curator can restore it. "
        "reject: records your opinion only; it does not remove an original record, which stays pending. "
        "defer: pending follow-up. acknowledge_failure: only for uncertain_outcome. "
        "revoke: withdraws an earlier acceptance."
    ))
    reason: StrictStr = Field(description=(
        "What establishes or questions this specific record; for retire, why it does not answer the question."
    ))
    evidence: list[StrictStr] | None = Field(default=None, description=(
        "Only with retire: E handles from this packet that show the record does not answer the question."
    ))
    absence_assessment: Literal["omission_supported", "unresolved"] | None = Field(default=None, description=(
        "Only when accepting an optional_input_absent or lookup_not_supported item: optional "
        "absence permits omission_supported or unresolved; unsupported lookups require unresolved."
    ))
    unresolved_fields: list[UnresolvedFieldAssessment] | None = Field(default=None, description=(
        "Only when accepting a review: record item (no validator ran): exact plain payload "
        "field paths and explanations of scientific uncertainty that remains. Name list "
        "elements by index, e.g. classification_notes[1]."
    ))


def _pinned_packet(db, context, packet_id):
    from .reads import PACKET_KIND

    episode, _ = lock_episode(db, context.history_scope, context.episode_id, require_open=False)
    fact = store._fact(db, episode.artifact_id, packet_id, kind=PACKET_KIND)
    if fact.parent_id != episode.id:
        raise store.HistoryConflict("Review packet belongs to another review round; read a fresh packet")
    return store._content(db, fact)


def _expand(packet, decided):
    """Expand handles from the immutable packet into exact entries and retirements.

    Versions are those pinned when the packet was read, so any later change to a
    record is a stale-version conflict rather than a silent decision on new state.
    Retire concerns a whole record: any of its handles names it, and several
    retire handles of one record become one retirement.
    """
    handles = packet["handles"]
    roots = {item["record"]: item["target_id"] for item in packet["records"] if item["record"] is not None}
    entries, retiring = [], {}
    for decision in decided:
        pinned = handles.get(decision.handle)
        if pinned is None:
            raise ValueError(f"{decision.handle}: Unknown handle for this review packet")
        if decision.evidence is not None and decision.action != "retire":
            raise ValueError(f"{decision.handle}: Only retire takes evidence handles")
        if decision.action == "retire":
            root = roots.get(pinned["record"])
            if root is None:
                raise ValueError(f"{decision.handle}: this item belongs to no record, so there is nothing to retire")
            unknown = [handle for handle in decision.evidence or [] if handle not in packet["evidence"]]
            if unknown:
                raise ValueError(f"{decision.handle}: Unknown evidence handle for this review packet: "
                                 + ", ".join(unknown))
            record = retiring.setdefault(root, {"target_id": root, "expected_version": None,
                                                "reasons": [], "evidence": {}})
            if pinned["decision_unit_id"] == root:
                # A record item pins the record's own version; a validator item
                # pins its field, and the packet's source digest pins the record.
                record["expected_version"] = pinned["expected_version"]
            if decision.reason not in record["reasons"]:
                record["reasons"].append(decision.reason)
            for handle in decision.evidence or []:
                record["evidence"][handle] = packet["evidence"][handle]
                shared = record["evidence"][handle].get("same_quote_as")
                if shared is not None:  # Keep the quote a copy points at.
                    record["evidence"][shared] = packet["evidence"][shared]
            entries.append({"target_id": root, "decision_unit_id": pinned["decision_unit_id"],
                            "action": "retire"})
            continue
        if pinned.get("uncertain_outcome") and decision.action != "acknowledge_failure":
            raise ValueError(f"{decision.handle}: this validation stopped without an answer, "
                             "so only acknowledge_failure (or retire for the whole record) can decide it")
        subject = {key: pinned[key] for key in ("logical_attempt_id", "extraction_ref", "applicability_ref")
                   if key in pinned and decision.action != "revoke"}
        entries.append({
            "target_id": pinned["target_id"], "decision_unit_id": pinned["decision_unit_id"],
            "expected_version": pinned["expected_version"], "action": decision.action,
            "reason": decision.reason, **subject,
            "absence_assessment": decision.absence_assessment,
            "unresolved_fields": (None if decision.unresolved_fields is None else
                                  [item.model_dump(mode="json") for item in decision.unresolved_fields]),
        })
    units, mixed = {}, {}
    for decision, entry in zip(decided, entries, strict=True):
        if entry["action"] != "retire":
            units.setdefault(entry["decision_unit_id"], []).append(decision.handle)
            root = roots.get(handles[decision.handle]["record"])
            if root in retiring:
                mixed.setdefault(handles[decision.handle]["record"], []).append(decision.handle)
    for same in units.values():
        if len(same) > 1:
            raise ValueError(", ".join(same) + ": these handles decide the same record field; "
                             "send only one of them in a call")
    for label, named in mixed.items():
        raise ValueError(", ".join(named) + f": retire removes the whole record {label}; "
                         "send no other decision for it in the same call")
    retirements = [{"target_id": record["target_id"], "expected_version": record["expected_version"],
                    "reason": "\n".join(record["reasons"]), "evidence": record["evidence"]}
                   for record in retiring.values()]
    return [decision.handle for decision in decided], entries, retirements


def _handle_names(packet):
    """Every unit of a packet named by its handle(s), for messages and blockers."""
    names = {}
    for handle, pinned in packet["handles"].items():
        names.setdefault(pinned["decision_unit_id"], []).append(handle)
    return {unit: "/".join(handles) for unit, handles in names.items()}


def packet_entries(context, *, packet_id, packet_decisions):
    """Expand handles from the immutable packet into exact decision entries."""
    decided = [PacketDecision.model_validate(item) for item in packet_decisions]
    with store.SessionLocal.begin() as db:
        packet = _pinned_packet(db, context, packet_id)
    handles, entries, retirements = _expand(packet, decided)
    if retirements:
        raise ValueError("Retiring a record is not available in this review; leave it pending for the curator")
    return handles, entries, _handle_names(packet)


def _name_handles(message, handles, entries):
    """Conflicts name the packet handles the model sent, not internal target IDs."""
    names = {}
    for handle, entry in zip(handles, entries, strict=True):
        for identity in (entry["decision_unit_id"], entry["target_id"]):
            names.setdefault(identity, [])
            if handle not in names[identity]:
                names[identity].append(handle)
    for identity, labels in names.items():
        message = message.replace(identity, "/".join(labels))
    return message


def _named_errors(handles, entries):
    """Name records in a failed save by the packet handles the model sent."""
    from contextlib import contextmanager

    @contextmanager
    def naming():
        try:
            yield
        except store.HistoryAccessError:
            raise  # Authorization keeps its own type and never names a record.
        except ValueError as error:
            found = getattr(error, "assessment_refusal", None)
            if found is not None:
                from src.lib.validation_history.assessments import refusal
                decided = [index for index, entry in enumerate(entries) if entry["action"] != "retire"]
                raise refusal(*found, names={position: handles[index]
                                             for position, index in enumerate(decided)}) from error
            kind = store.HistoryConflict if isinstance(error, store.HistoryConflict) else ValueError
            raise kind(_name_handles(str(error), handles, entries)) from error
    return naming()


def record_packet_decisions(context, *, checkpoint_request, operation_id, packet_id, packet_decisions):
    """Save decisions named by packet handle; the decision fact keeps exact targets."""
    handles, entries, names = packet_entries(context, packet_id=packet_id, packet_decisions=packet_decisions)
    with _named_errors(handles, entries):
        response = record_decisions(context, checkpoint_request=checkpoint_request,
                                    operation_id=operation_id, entries=entries, names=names)
    return {**response, "packet_id": packet_id, "applied": [
        {**applied, "handle": handle, "target_id": entry["target_id"]}
        for applied, handle, entry in zip(response["applied"], handles, entries, strict=True)]}


def record_packet_review_in_transaction(db, context, *, checkpoint, retire, operation_id, packet_id,
                                        packet_decisions):
    """Retire records and save every other decision of one packet save atomically.

    The runtime binds retire to the source publication of this same transaction;
    checkpoint returns the envelope request at the revision each publication left.
    """
    decided = [PacketDecision.model_validate(item) for item in packet_decisions]
    key = context.episode_id + ":" + operation_id
    episode, _ = lock_episode(db, context.history_scope, context.episode_id, require_open=False)
    identity = {"packet_id": packet_id, "packet_decisions": [item.model_dump(mode="json") for item in decided],
                "actor": {"type": context.actor_type, "id": context.actor_id}, "episode_id": episode.id}
    previous = db.scalar(select(ValidationFact).where(
        ValidationFact.artifact_id == episode.artifact_id,
        ValidationFact.operation_id == "packet-review-response:" + key))
    if previous is not None:
        saved = store._content(db, previous)
        if saved["identity"] != store.canonical(identity)[1]:
            raise store.HistoryConflict("Review operation identity conflict")
        return saved["response"]
    lock_episode(db, context.history_scope, context.episode_id)
    packet = _pinned_packet(db, context, packet_id)
    handles, entries, retirements = _expand(packet, decided)
    names = _handle_names(packet)
    decisions = [entry for entry in entries if entry["action"] != "retire"]
    with _named_errors(handles, entries):
        retired = {"retirement_ids": {}, "affected_bindings": []}
        if retirements:
            retired = retire(retirements, operation_id=key, expected_source_sha=packet["source_state_sha256"])
        saved = None
        if decisions:
            saved = record_decisions_in_transaction(db, context, checkpoint_request=checkpoint(),
                                                    operation_id=operation_id, entries=decisions, names=names)
    from src.lib.validation_history.selections import manifest_in_transaction
    manifest = manifest_in_transaction(db, context.history_scope, episode.artifact_id, episode_id=episode.id)
    ready = review_ready(manifest)
    if ready:
        episode.state = "complete"
    if saved is not None:
        publication, revision = saved["publication"], saved["envelope_revision"]
    else:
        current = checkpoint()
        publication, revision = current.envelope.metadata["selected_projection"], current.expected_revision
    applied = iter(saved["applied"] if saved is not None else ())
    response = {
        "episode_id": episode.id, "packet_id": packet_id, "ready": ready,
        "publication": publication, "envelope_revision": revision,
        "source_state_sha256": manifest["source_state_sha256"],
        "applied": [
            {"decision_unit_id": entry["decision_unit_id"], "action": "retire", "handle": handle,
             "target_id": entry["target_id"], "retirement_id": retired["retirement_ids"][entry["target_id"]]}
            if entry["action"] == "retire" else {**next(applied), "handle": handle, "target_id": entry["target_id"]}
            for handle, entry in zip(handles, entries, strict=True)],
        **({"decision_id": saved["decision_id"]} if saved is not None else {}),
        # Validator inputs the retirement changed are assessed with this selection_id.
        **({"affected_bindings": retired["affected_bindings"], "selection_id": retired["selection_id"]}
           if retired["affected_bindings"] else {}),
        **({} if ready else {"blocking": blocking(db, episode, manifest, names)}),
    }
    append_fact(db, episode.artifact_id, operation_id="packet-review-response:" + key,
                kind="review_response", parent_id=episode.id, body={"identity": identity, "response": response})
    return response


# What clears each readiness blocker, by review mode where that matters. Mechanics
# only: the codes come from the manifest; the reviewer still decides each record.
_CLEARED_BY = {
    ("unreviewed", "extraction_only"): "accept, or retire if the record does not answer the question",
    ("unreviewed", "validation"): "accept an answer, or request reconsideration",
    ("unreviewed", "curator"): "a curator decision",
    "episode_review_pending": "accept",
    "answer_review_pending": "decide the new validator answer",
    "execution_pending": "wait for the running validation",
    "stale": "validate the changed input, then decide the new answer",
    "incomplete": "acknowledge_failure, or request reconsideration",
    "configured_validator_unavailable": "curator review; the validator is unavailable",
    "required_validator_unavailable": "curator review; the validator is unavailable",
    "historical_review_unknown": "curator review",
    "document_retired": "nothing; the document was removed",
    "retired": "nothing; the record is retired",
}


def blocking(db, episode, manifest, names):
    """Compact list of what still blocks this round: code, handles or targets, and what clears it."""
    from src.lib.validation_history.selections import effective_episode

    modes = {item["target_id"]: item["mode"] for item in effective_episode(
        db, store._fact(db, episode.artifact_id, episode.id, kind="review_episode"))["obligations"]}
    groups = {}
    for blocker in review_blockers(manifest):
        unit, code = blocker["target_id"], blocker["code"]
        by_mode = (code, modes[unit]) if unit is not None else None
        clears = _CLEARED_BY[by_mode] if by_mode in _CLEARED_BY else _CLEARED_BY[code]
        group = groups.setdefault((code, clears), {"code": code, "handles": [], "target_ids": [],
                                                   "clears_with": clears})
        if unit is None:
            continue
        # A unit outside this packet (for example a new answer) is named by its target.
        name = names.get(unit)
        listed, value = (group["handles"], name) if name is not None else (group["target_ids"], unit)
        if value not in listed:
            listed.append(value)
    return [{key: value for key, value in group.items() if value != []} for group in groups.values()]


def bounded_text(value, limit, name):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(
            f"{name} must be nonempty and within its configured character limit"
        )
    return value


def record_decisions(context, *, checkpoint_request, operation_id, entries, names=None):
    with store.SessionLocal.begin() as db:
        return record_decisions_in_transaction(db, context, checkpoint_request=checkpoint_request,
                                               operation_id=operation_id, entries=entries, names=names)


def record_decisions_in_transaction(db, context, *, checkpoint_request, operation_id, entries, names=None):
    """names maps decision units to packet handles so blockers read as handles."""
    operation_id = context.episode_id + ":" + operation_id
    episode, _ = lock_episode(
        db, context.history_scope, context.episode_id, require_open=False
    )
    entries = [
        ReviewEntry.model_validate(e).model_dump(mode="json") for e in entries
    ]
    # The new assessment is genuinely optional; absent annotations do not
    # change the identity of previously recorded requests.
    for entry in entries:
        for optional in ("unresolved_fields", "applicability_ref", "absence_assessment"):
            if entry[optional] is None:
                del entry[optional]
    identity = {
        "entries": entries,
        "actor": {"type": context.actor_type, "id": context.actor_id},
        "episode_id": episode.id,
    }
    previous = db.scalar(
        select(ValidationFact).where(
            ValidationFact.artifact_id == episode.artifact_id,
            ValidationFact.operation_id == "review-response:" + operation_id,
        )
    )
    if previous is not None:
        saved = store._content(db, previous)
        if saved["identity"] != store.canonical(identity)[1]:
            raise store.HistoryConflict("Review operation identity conflict")
        return saved["response"]
    if context.actor_type == "curator":
        if context.actor_id != context.history_scope.owner_id:
            raise store.HistoryAccessError("Curator decisions require the authenticated owner")
        from src.lib.validation_history.episodes import latest_effective_episode
        latest = latest_effective_episode(db, episode.artifact_id)
        if latest is None or latest.id != episode.id:
            raise store.HistoryConflict("A new review round has started; refresh before deciding")
        # Human judgment is not gated by a machine deadline or allowance.
        # Publication still enforces live targets, ownership and exact versions.
    else:
        lock_episode(db, context.history_scope, context.episode_id)
    limits = ReviewLimits(**episode.policy)
    if not entries or len(entries) > limits.decision_entries:
        raise ValueError(
            f"Review entries exceed configured bounds: send 1 to {limits.decision_entries} per call"
        )
    bounded_text(operation_id, limits.instruction_chars, "Operation ID")

    def resolve_entry(entry):
        action = entry["action"]
        if action not in {
            "accept",
            "reject",
            "defer",
            "acknowledge_failure",
            "revoke",
        }:
            raise ValueError("Unsupported supervisor review action")
        bounded_text(entry["reason"], limits.instruction_chars, "Review reason")
        unit = store._fact(
            db, episode.artifact_id, entry["decision_unit_id"], kind="target"
        )
        target = store._fact(
            db, episode.artifact_id, entry["target_id"], kind="target"
        )
        if unit.id != target.id and unit.parent_id != target.id:
            raise store.HistoryConflict("Decision unit is outside the named target")
        attempt_id = entry.get("logical_attempt_id")
        extraction_ref = entry.get("extraction_ref")
        applicability_ref = entry.get("applicability_ref")
        subject = None
        if action == "revoke":
            if attempt_id or extraction_ref or applicability_ref:
                raise ValueError("Revocation does not select an answer")
        elif sum(bool(ref) for ref in (attempt_id, extraction_ref, applicability_ref)) != 1:
            raise ValueError("Name exactly one attempt, extraction or applicability reference")
        elif applicability_ref:
            from . import source_inputs
            if action == "acknowledge_failure":
                raise ValueError("An applicability fact is not an execution failure")
            fact = source_inputs.require_applicability(db, episode.artifact_id, unit.id,
                applicability_ref["fact_id"], scope=context.history_scope, sha256=applicability_ref["sha256"])
            if fact.target_id != target.id:
                raise store.HistoryConflict("Applicability source belongs to another named target")
            subject = fact.id
        elif attempt_id:
            attempt = store._fact(
                db, episode.artifact_id, attempt_id, kind="attempt"
            )
            frozen = store._content(db, attempt)
            if (
                frozen["input"]["target_id"] != target.id
                or unit.id not in frozen["coverage"]
            ):
                raise store.HistoryConflict(
                    "Answer is outside this episode/target/unit"
                )
            completion = db.scalar(
                select(ValidationFact).where(
                    ValidationFact.artifact_id == episode.artifact_id,
                    ValidationFact.kind == "completion",
                    ValidationFact.parent_id == attempt.id,
                )
            )
            if completion is None:
                if action != "acknowledge_failure":
                    raise store.HistoryConflict(
                        "Attempt has no recorded completion"
                    )
                subject = (
                    attempt.id
                )  # Store checks exact durable uncertainty; no invented answer.
            elif (
                action == "reject"
                and store._content(db, completion)["state"] != "completed"
            ):
                raise store.HistoryConflict(
                    "Operationally incomplete answers require acknowledgement or deferral"
                )
            if completion is not None:
                subject = completion.id
        else:
            artifact = store._artifact(
                db, episode.artifact_id, context.history_scope
            )
            if extraction_ref != artifact.baseline_sha:
                raise store.HistoryConflict(
                    "Extraction reference does not match the immutable baseline"
                )
            subject = "baseline"
        absence_assessment = entry.get("absence_assessment")
        if action == "accept" and applicability_ref:
            if absence_assessment is None:
                raise ValueError("Accepting applicability requires an explicit assessment")
        elif absence_assessment is not None:
            raise ValueError("Applicability assessment requires applicability acceptance")
        assessments = entry.get("unresolved_fields")
        if assessments is not None:
            if action != "accept" or subject != "baseline":
                raise ValueError("Unresolved fields require extraction-only acceptance")
            for assessment in assessments:
                bounded_text(assessment["explanation"], limits.instruction_chars, "Field explanation")
        return (
            {
                "target_id": unit.id,
                "expected_version": entry["expected_version"],
                "action": action,
                "reason": entry["reason"],
                **({"subject": subject} if subject else {}),
                **({"absence_assessment": absence_assessment} if absence_assessment is not None else {}),
                **({"unresolved_fields": assessments} if assessments is not None else {}),
            }
        )
    resolved = []
    for entry in entries:
        try:
            resolved.append(resolve_entry(entry))
        except store.HistoryAccessError:
            raise  # Authorization keeps its own type and never names a record.
        except store.HistoryConflict as error:
            raise store.HistoryConflict(f"{entry['decision_unit_id']}: {error}") from error
        except ValueError as error:
            raise ValueError(f"{entry['decision_unit_id']}: {error}") from error
    # The model never chooses the checkpoint/source linkage or the actor.
    if (
        checkpoint_request.envelope.metadata["validation_history"]["artifact_id"]
        != episode.artifact_id
    ):
        raise store.HistoryConflict("Checkpoint belongs to a different artifact")
    result, manifest, checkpoint = publish_decisions_in_transaction(
        db,
        context.history_scope,
        checkpoint_request=checkpoint_request,
        episode_id=episode.id,
        operation_id=operation_id,
        actor={"type": context.actor_type, "id": context.actor_id},
        entries=resolved,
    )
    if review_ready(manifest):
        episode.state = "complete"
    decision = db.scalar(
        select(ValidationFact).where(
            ValidationFact.artifact_id == episode.artifact_id,
            ValidationFact.operation_id == "decision:" + operation_id,
        )
    )
    response = {
        "episode_id": episode.id,
        "decision_id": decision.id,
        "ready": review_ready(manifest),
        "publication": result.metadata["selected_projection"],
        "envelope_revision": checkpoint.revision,
        "applied": [
            {"decision_unit_id": e["target_id"], "action": e["action"]}
            for e in resolved
        ],
    }
    if not response["ready"] and context.actor_type != "curator":
        response["blocking"] = blocking(db, episode, manifest, names or {})

    append_fact(
        db,
        episode.artifact_id,
        operation_id="review-response:" + operation_id,
        kind="review_response",
        parent_id=episode.id,
        body={"identity": identity, "response": response},
    )
    return response
