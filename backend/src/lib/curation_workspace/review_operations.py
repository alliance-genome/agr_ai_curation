"""Explicit, durable workspace commands built on shared review admission and history.

HTTP commits the command before waking execution. Providers never run inside an
HTTP/SQL transaction. A lost submitted operation is uncertain, never replayed.
"""

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from sqlalchemy import select, func

from src.lib.validation_history import store
from src.lib.validation_review import admission, reviewer
from src.lib.validation_review.policy import ReviewLimits
from src.models.sql.validation_review import WorkspaceReviewOperation as Operation
from src.lib.curation_workspace.models import CurationCandidate
from src.schemas.domain_envelope import DomainEnvelope
from .review_history_service import candidate_binding
from .review_authorization import require_current_access


def response(row):
    return {
        "operation_id": row.id,
        "session_id": row.session_id,
        "state": row.state,
        "outcome": row.outcome,
        "artifact_count": len(row.artifacts),
    }


def submit(
    db,
    *,
    owner_id,
    session_id,
    request_id,
    candidate_ids,
    guidance,
    groups,
    origin="curator",
):
    from src.lib.validation_review.decisions import bounded_text

    if origin not in {"curator", "bootstrap"}:
        raise ValueError("Unknown workspace review origin")
    if guidance is not None:
        bounded_text(guidance, ReviewLimits.configured().instruction_chars, "Guidance")
    session_id, request_id = str(UUID(session_id)), str(UUID(request_id))
    candidates = sorted(set(str(UUID(value)) for value in candidate_ids))
    identity = {
        "session_id": session_id,
        "candidate_ids": candidates,
        "guidance": guidance,
        "groups": sorted(groups),
        "origin": origin,
    }
    # Serialize concurrent duplicate submissions before any preparation or grant.
    key = store.canonical({"owner_id": owner_id, "request_id": request_id})[0]
    from sqlalchemy import text

    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
    )
    previous = db.scalar(
        select(Operation).where(
            Operation.owner_id == owner_id, Operation.request_id == request_id
        )
    )
    if previous is not None:
        if previous.request != identity:
            raise store.HistoryConflict(
                "This revalidation request ID already names a different action"
            )
        return response(previous)
    if not candidates:
        candidates = [
            str(value)
            for value in db.scalars(
                select(CurationCandidate.id).where(
                    CurationCandidate.session_id == UUID(session_id)
                )
            )
        ]
    if not candidates:
        raise store.HistoryConflict("No records are available for revalidation")
    snapshots = {}
    excluded = []
    operation_id = str(uuid4())
    for candidate_id in candidates:
        candidate = db.get(CurationCandidate, UUID(candidate_id))
        if candidate is not None and candidate.envelope_id is None:
            if (
                candidate.session.created_by_id != owner_id
                or str(candidate.session_id) != session_id
            ):
                raise store.HistoryAccessError(
                    "Manual record belongs to another workspace owner"
                )
            from .session_validation_service import (
                _ensure_manual_profile_envelope,
                _validator_runtime_context_for_candidate,
            )
            from .execution_contracts import require_candidate_conformance

            require_candidate_conformance(db, candidate)
            _ensure_manual_profile_envelope(
                db,
                candidate,
                runtime_context=_validator_runtime_context_for_candidate(
                    candidate, user_id=owner_id, authenticated_groups=groups
                ),
            )
        from fastapi import HTTPException

        try:
            candidate, row, envelope, scope, artifact_id = candidate_binding(
                db, candidate_id, owner_id=owner_id
            )
        except HTTPException as error:
            if candidate_ids or error.status_code != 409:
                raise
            if (
                candidate is None
                or candidate.session.created_by_id != owner_id
                or str(candidate.session_id) != session_id
            ):
                raise store.HistoryAccessError("Workspace record unavailable") from None
            excluded.append(
                {
                    "candidate_id": candidate_id,
                    "reason": "No durable validation source is available for this record.",
                }
            )
            continue
        if str(candidate.session_id) != session_id:
            raise store.HistoryAccessError("Candidate belongs to another workspace")
        store._require_live_artifact(db, artifact_id)
        from src.models.sql.validation_history import ValidationFact

        sequence = db.scalar(
            select(func.max(ValidationFact.sequence)).where(
                ValidationFact.artifact_id == artifact_id
            )
        )
        if artifact_id not in snapshots:
            snapshots[artifact_id] = {
                "artifact_id": artifact_id,
                "document_id": scope.document_id,
                "history_session_id": scope.session_id,
                "envelope": envelope.model_dump(mode="json"),
                "revision": row.revision,
                "candidate_id": candidate_id,
                "episode_key": str(
                    uuid5(NAMESPACE_URL, operation_id + ":" + artifact_id)
                ),
                "origin": origin,
                "intents": [],
            }
        snapshots[artifact_id]["intents"].append(
            {
                "actor_id": owner_id,
                "object_id": candidate.object_id,
                "field_paths": [],
                "guidance": guidance,
                "through_sequence": sequence,
            }
        )
    row = Operation(
        id=operation_id,
        owner_id=owner_id,
        session_id=session_id,
        request_id=request_id,
        request=identity,
        artifacts=list(snapshots.values()),
        state="queued" if snapshots else "needs_review",
        outcome={
            "excluded_records": excluded,
            **({"reason": "no_eligible_records"} if not snapshots else {}),
        },
    )
    db.add(row)
    db.flush()
    return response(row)


def get(db, operation_id, *, owner_id):
    row = db.get(Operation, str(operation_id))
    if row is None or row.owner_id != owner_id:
        raise store.HistoryAccessError("Workspace operation unavailable")
    for item in row.artifacts:
        store._artifact(
            db,
            item["artifact_id"],
            store.HistoryScope(
                owner_id, item["document_id"], item["history_session_id"]
            ),
        )
    return row


def latest_for_candidate(db, candidate_id, *, owner_id):
    """Reopening a record observes its last command without creating a round."""
    candidate = db.get(CurationCandidate, UUID(str(candidate_id)))
    if candidate is None:
        raise store.HistoryAccessError("Workspace record unavailable")
    # A manual record has no captured source until the explicit submit action.
    # Observing an absent command must not require or create that source.
    if candidate.envelope_id is None:
        if candidate.session.created_by_id != owner_id:
            raise store.HistoryAccessError("Workspace record unavailable")
        return None
    candidate, _, _, _, artifact_id = candidate_binding(
        db, candidate_id, owner_id=owner_id
    )
    row = db.scalar(
        select(Operation)
        .where(
            Operation.owner_id == owner_id,
            Operation.session_id == str(candidate.session_id),
            Operation.artifacts.contains(
                [
                    {
                        "artifact_id": artifact_id,
                        "intents": [{"object_id": candidate.object_id}],
                    }
                ]
            ),
        )
        .order_by(Operation.created_at.desc(), Operation.id.desc())
        .limit(1)
    )
    return response(row) if row is not None else None


def latest_for_session(db, session_id, *, owner_id):
    row = db.scalar(
        select(Operation)
        .where(
            Operation.owner_id == owner_id,
            Operation.session_id == str(session_id),
            func.jsonb_array_length(Operation.request["candidate_ids"]) == 0,
        )
        .order_by(Operation.created_at.desc(), Operation.id.desc())
        .limit(1)
    )
    return response(get(db, row.id, owner_id=owner_id)) if row is not None else None


def refresh_candidate_snapshot(db, candidate_id, *, owner_id, groups):
    """Refresh displayed structural/selected-state findings; never call an LLM."""
    from .session_validation_service import validate_candidate
    from src.schemas.curation_workspace import CurationCandidateValidationRequest

    candidate, _, _, _, _ = candidate_binding(db, candidate_id, owner_id=owner_id)
    return validate_candidate(
        db,
        candidate_id,
        CurationCandidateValidationRequest(
            session_id=str(candidate.session_id),
            candidate_id=str(candidate_id),
            force=True,
        ),
        user_id=owner_id,
        active_groups=groups,
    )


def refresh_operation_snapshots(operation_id, *, owner_id, groups):
    with store.SessionLocal.begin() as db:
        row = get(db, operation_id, owner_id=owner_id)
        for item in row.artifacts:
            candidate, _, _, _, _ = candidate_binding(
                db, item["candidate_id"], owner_id=owner_id
            )
            ids = list(
                db.scalars(
                    select(CurationCandidate.id).where(
                        CurationCandidate.session_id == candidate.session_id,
                        CurationCandidate.envelope_id == candidate.envelope_id,
                        CurationCandidate.object_id.in_(
                            [intent["object_id"] for intent in item["intents"]]
                        ),
                    )
                )
            )
            for candidate_id in ids:
                refresh_candidate_snapshot(
                    db, candidate_id, owner_id=owner_id, groups=groups
                )


def checkpoint(owner_id, item, groups):
    from src.lib.domain_envelopes.persistence import DomainEnvelopeCheckpointRequest

    with store.SessionLocal() as db:
        _, row, envelope, _, _ = candidate_binding(
            db, item["candidate_id"], owner_id=owner_id
        )
        envelope = envelope.model_copy(deep=True)
        envelope.metadata["review_episode_id"] = item["episode_id"]
        return DomainEnvelopeCheckpointRequest(
            project_key=row.project_key,
            envelope=envelope,
            expected_revision=row.revision,
            document_id=row.document_id,
            session_id=row.session_id,
            flow_run_id=row.flow_run_id,
            authenticated_groups=groups,
            curator_authority_groups=groups,
            object_model_ref_json=dict(row.object_model_ref_json),
            model_field_ref_json=dict(row.model_field_ref_json),
        )


def prepare(operation_id, lease_token):
    from src.lib.curation_workspace.adapter_registry import (
        resolve_curation_domain_pack_by_id,
    )
    from src.lib.domain_packs.validator_dispatch import (
        prepare_validator_dispatch,
        ValidatorRuntimeContext,
    )
    from src.lib.validation_review.validator_adapter import save_plan
    from src.models.sql.validation_history import ValidationFact

    with store.SessionLocal.begin() as db:
        row = db.scalar(
            select(Operation).where(Operation.id == operation_id).with_for_update()
        )
        if row.state != "preparing" or row.lease_token != lease_token:
            raise admission.ReviewStopped("operation_lease_lost")
        artifacts = []
        for original in row.artifacts:
            item = dict(original)
            if not item.get("prepared_source"):
                _, current, envelope, _, artifact_id = candidate_binding(
                    db, item["candidate_id"], owner_id=row.owner_id
                )
                if artifact_id != item["artifact_id"]:
                    raise store.HistoryConflict(
                        "Record source changed while awaiting revalidation"
                    )
                item.update(
                    envelope=envelope.model_dump(mode="json"),
                    revision=current.revision,
                    prepared_source=True,
                )
            artifacts.append(item)
        row.artifacts = artifacts
        owner, groups = row.owner_id, tuple(row.request["groups"])
    require_current_access(owner, groups)
    # Work identities are deterministic; preparation is safe to recover before
    # provider submission. Existing immutable plans win over recomputation.
    prepared = []
    total_targets = 0
    for item in artifacts:
        scope = store.HistoryScope(
            owner, item["document_id"], item["history_session_id"]
        )
        # Stop uses this same lock. Plans contain no provider execution; once
        # creation commits, cancellation can always find its durable episode key.
        with store.SessionLocal.begin() as planning_db:
            command = planning_db.scalar(
                select(Operation).where(Operation.id == operation_id).with_for_update()
            )
            if command.state != "preparing" or command.lease_token != lease_token:
                raise admission.ReviewStopped(command.state)
            with store.SessionLocal() as db:
                store._artifact(db, item["artifact_id"], scope)
                episode = db.scalar(
                    select(ValidationFact).where(
                        ValidationFact.artifact_id == item["artifact_id"],
                        ValidationFact.operation_id
                        == "review-episode:" + item["episode_key"],
                    )
                )
                prior = (
                    db.scalar(
                        select(ValidationFact).where(
                            ValidationFact.artifact_id == item["artifact_id"],
                            ValidationFact.operation_id
                            == "chat-review-plan:" + episode.id,
                        )
                    )
                    if episode
                    else None
                )
                saved = (prior.id, store._content(db, prior)) if prior else None
            if saved is None:
                envelope = DomainEnvelope.model_validate(item["envelope"])
                pack = resolve_curation_domain_pack_by_id(envelope.domain_pack_id)
                if pack is None:
                    raise store.HistoryConflict("Saved domain pack is unavailable")
                context = ValidatorRuntimeContext(
                    document_id=scope.document_id,
                    user_id=owner,
                    authenticated_groups=groups,
                    curator_revalidation=item["intents"]
                    if item["origin"] == "curator"
                    else None,
                    review_episode_key=item["episode_key"],
                    review_object_ids=tuple(
                        intent["object_id"] for intent in item["intents"]
                    ),
                )
                # Revalidation of a flow result retains its executable binding
                # choices rather than widening to the domain's entire catalog.
                from src.lib.validation_review.validator_adapter import REGISTRY

                with store.SessionLocal() as db:
                    prior_plan = db.scalar(
                        select(ValidationFact)
                        .where(
                            ValidationFact.artifact_id == item["artifact_id"],
                            ValidationFact.kind == "review_chat_plan",
                        )
                        .order_by(ValidationFact.sequence.desc())
                        .limit(1)
                    )
                    registry = (
                        REGISTRY.validate_python(
                            store._content(db, prior_plan)["registry"]
                        )
                        if prior_plan
                        else None
                    )
                plan = prepare_validator_dispatch(
                    envelope, pack, runtime_context=context, registry=registry
                )
                if item["origin"] == "curator":
                    from src.lib.validation_review.audit import append_fact

                    with store.SessionLocal.begin() as db:
                        objects = {
                            key: obj
                            for obj in plan.envelope.extracted_objects
                            for key in (obj.object_id, obj.pending_ref_id)
                            if key is not None
                        }
                        request_fact = append_fact(
                            db,
                            item["artifact_id"],
                            operation_id="workspace-request:"
                            + operation_id
                            + ":"
                            + item["artifact_id"],
                            kind="workspace_revalidation_request",
                            parent_id=plan.envelope.metadata["review_episode_id"],
                            body={
                                "actor": {"type": "curator", "id": owner},
                                "entries": [
                                    {
                                        "target_id": objects[
                                            intent["object_id"]
                                        ].metadata["validation_history"]["target_id"],
                                        "action": "request_revalidation",
                                        "reason": intent["guidance"]
                                        or "Curator requested revalidation",
                                    }
                                    for intent in item["intents"]
                                ],
                                "operation_id": operation_id,
                            },
                        )
                        plan.envelope.metadata["workspace_review_request"] = (
                            request_fact.id
                        )
                plan_ref = save_plan(plan, context=context)
                episode_id = plan.envelope.metadata["review_episode_id"]
            else:
                plan_ref, body = saved
                episode_id = body["envelope"]["metadata"]["review_episode_id"]
            with store.SessionLocal() as db:
                fact = store._fact(db, item["artifact_id"], episode_id)
                from src.lib.validation_review.record_scope import validation_targets

                targets = validation_targets(
                    db, item["artifact_id"], store._content(db, fact)
                )
            total_targets += len(targets)
            prepared.append(
                {
                    **item,
                    "episode_id": episode_id,
                    "plan_ref": plan_ref,
                    "targets": len(targets),
                }
            )
    _save_prepared(operation_id, lease_token, prepared)
    first = prepared[0]
    root_scope = store.HistoryScope(
        owner, first["document_id"], first["history_session_id"]
    )
    with store.SessionLocal.begin() as operation_db:
        command = operation_db.scalar(
            select(Operation).where(Operation.id == operation_id).with_for_update()
        )
        if command.state != "preparing" or command.lease_token != lease_token:
            raise admission.ReviewStopped(command.state)
        parent = admission.create_scope(
            root_scope,
            operation_id="workspace:" + operation_id,
            kind="workspace_operation",
            targets=total_targets,
            episodes=len(prepared),
        )
    for item in prepared:
        scope = store.HistoryScope(
            owner, item["document_id"], item["history_session_id"]
        )
        previous = item["envelope"]["metadata"].get("review_episode_id")
        from src.models.sql.validation_review import ValidationReviewEpisode

        with store.SessionLocal() as db:
            predecessor = (
                previous
                if previous and db.get(ValidationReviewEpisode, previous) is not None
                else None
            )
        # Serialize attachment with Stop. No provider runs under this lock.
        with store.SessionLocal.begin() as operation_db:
            command = operation_db.scalar(
                select(Operation).where(Operation.id == operation_id).with_for_update()
            )
            if command.state != "preparing" or command.lease_token != lease_token:
                raise admission.ReviewStopped(command.state)
            child = admission.create_scope(
                scope,
                operation_id="workspace:" + operation_id + ":" + item["artifact_id"],
                kind="document_run",
                targets=item["targets"],
                episodes=1,
                parent_id=parent,
            )
            admission.attach_episode(
                scope,
                artifact_id=item["artifact_id"],
                episode_id=item["episode_id"],
                scope_id=child,
                predecessor_id=predecessor,
                renewal_actor_id=owner if predecessor else None,
                renewal_reason="Explicit curator Revalidate action"
                if predecessor
                else None,
                curator_round=bool(predecessor),
            )
    with store.SessionLocal.begin() as db:
        row = db.scalar(
            select(Operation).where(Operation.id == operation_id).with_for_update()
        )
        if row.state != "preparing" or row.lease_token != lease_token:
            raise admission.ReviewStopped(row.state)
        row.artifacts = prepared
        row.state = "running"
    return owner, groups, prepared


def _save_prepared(operation_id, lease_token, prepared):
    """Persist episode references before grants; ordinary Stop can win here."""
    with store.SessionLocal.begin() as db:
        row = db.scalar(
            select(Operation).where(Operation.id == operation_id).with_for_update()
        )
        if row.state != "preparing" or row.lease_token != lease_token:
            raise admission.ReviewStopped(row.state)
        row.artifacts = prepared


def _abandon_unadmitted(db, row, *, actor, reason):
    """Keep failed/stopped preparation inspectable without superseding real review.

    The caller holds the operation lock shared with plan creation and attachment.
    Admitted rounds (including cancelled ones) retain their answers and authority.
    """
    from src.models.sql.validation_history import ValidationFact
    from src.models.sql.validation_review import ValidationReviewEpisode
    from src.lib.validation_history.selections import _lock_artifact
    from src.lib.validation_review.audit import append_fact

    abandoned = []
    for item in sorted(row.artifacts, key=lambda value: value["artifact_id"]):
        scope = store.HistoryScope(
            row.owner_id, item["document_id"], item["history_session_id"]
        )
        _lock_artifact(db, scope, item["artifact_id"], write=True)
        episode = db.scalar(
            select(ValidationFact).where(
                ValidationFact.artifact_id == item["artifact_id"],
                ValidationFact.operation_id == "review-episode:" + item["episode_key"],
            )
        )
        if episode is None or db.get(ValidationReviewEpisode, episode.id) is not None:
            continue
        from src.lib.validation_history.episodes import is_abandoned

        if is_abandoned(db, item["artifact_id"], episode.id):
            continue
        append_fact(
            db,
            item["artifact_id"],
            operation_id="workspace-abandoned:" + row.id + ":" + episode.id,
            kind="review_episode_abandoned",
            parent_id=episode.id,
            body={
                "operation_id": row.id,
                "actor": actor,
                "reason": reason,
                "object_ids": [intent["object_id"] for intent in item["intents"]],
            },
        )
        abandoned.append(episode.id)
    return abandoned


async def execute(operation_id, lease_token):
    from .review_observability import workspace_review_trace

    def identity():
        with store.SessionLocal() as db:
            row = db.get(Operation, operation_id)
            return row.session_id, row.owner_id

    session_id, owner_id = await asyncio.to_thread(identity)
    with workspace_review_trace(
        operation_id=operation_id, session_id=session_id, owner_id=owner_id
    ) as trace_id:
        await _execute(operation_id, lease_token, trace_id)


async def _execute(operation_id, lease_token, trace_id):
    from src.lib.validation_review.decisions import ReviewContext
    from src.lib.validation_review.validator_adapter import ValidatorAdapter
    from src.lib.validation_review.extractor_adapter import ExtractorAdapter
    from src.lib.observability.cost_context import get_agent_cost_identity
    from src.lib.validation_review.stops import review_stop, stop_reason
    from src.lib.validation_review.audit import report_operational_failure
    import json

    try:
        owner, groups, artifacts = await asyncio.to_thread(
            prepare, operation_id, lease_token
        )
        supervisor = reviewer.create_direct_supervisor(active_groups=list(groups))
        outcomes = []
        for item in artifacts:
            await asyncio.to_thread(require_current_access, owner, groups)
            scope = store.HistoryScope(
                owner, item["document_id"], item["history_session_id"]
            )
            context = ReviewContext(
                scope,
                item["episode_id"],
                get_agent_cost_identity(supervisor)["agent_id"] + ".validation_review",
            )
            adapter = ValidatorAdapter(
                scope,
                item["artifact_id"],
                item["episode_id"],
                item["plan_ref"],
                authenticated_groups=groups,
            )

            def current_checkpoint():
                return checkpoint(owner, item, groups)

            await adapter.initial()
            await adapter.advance(current_checkpoint)
            extractor = ExtractorAdapter(
                context,
                item["artifact_id"],
                authenticated_groups=groups,
                checkpoint_provider=current_checkpoint,
            )
            agent = reviewer.create_supervisor_phase(
                context,
                supervisor=supervisor,
                checkpoint_provider=current_checkpoint,
                authenticated_groups=groups,
                execute_work=adapter.execute,
                advance_work=lambda: adapter.advance(current_checkpoint),
                prepare_inputs=lambda: adapter.prepare_inputs(current_checkpoint),
                refresh_inputs=adapter.refresh_inputs,
                rebind_receipt=adapter.rebind_receipt,
                request_extraction=extractor.request,
                select_source=lambda **kw: adapter.select_source(
                    context, current_checkpoint, **kw
                ),
                assess_source=lambda **kw: adapter.assess_source(context, **kw),
            )
            outcome = await reviewer.run(
                context,
                agent,
                input=json.dumps(
                    {
                        "instruction": "Review this saved workspace round. Read its origin, exact saved history and any curator request/guidance. Review the answers and record explicit decisions. Preserve later curator edits.",
                        "origin": item["origin"],
                        "episode_id": item["episode_id"],
                    }
                ),
            )
            outcomes.append(outcome)
        await asyncio.to_thread(
            refresh_operation_snapshots, operation_id, owner_id=owner, groups=groups
        )
        state = (
            "complete"
            if all(value["review"]["ready"] for value in outcomes)
            else "needs_review"
        )
        result = {"results": outcomes}
    except Exception as error:
        stopped = review_stop(error)
        from fastapi import HTTPException

        if stopped is not None:
            state, result = "needs_review", {"reason": stop_reason(stopped)}
        elif isinstance(error, (store.HistoryAccessError, store.HistoryConflict)) or (
            isinstance(error, HTTPException) and error.status_code < 500
        ):
            state, result = (
                "needs_review",
                {
                    "reason": "record_or_access_changed",
                    "message": "Refresh the record and inspect its saved history before continuing.",
                },
            )
        else:
            report_operational_failure(error)
            state, result = (
                "failed",
                {"message": "Validation could not finish. Saved history is retained."},
            )
    with store.SessionLocal.begin() as db:
        row = db.scalar(
            select(Operation).where(Operation.id == operation_id).with_for_update()
        )
        if row.state in {"preparing", "running"} and row.lease_token == lease_token:
            if row.state == "preparing":
                abandoned = _abandon_unadmitted(
                    db,
                    row,
                    actor={"type": "system", "id": "workspace_review_worker"},
                    reason="Preparation ended before admission",
                )
                if abandoned:
                    result = {
                        **result,
                        "preparation_reason": result.get("reason"),
                        "reason": "preparation_abandoned",
                        "message": "Preparation ended before execution. Prior saved answers remain available for curator review.",
                    }
            row.state, row.outcome, row.lease_until = (
                state,
                {**(row.outcome or {}), **result, "trace_id": trace_id},
                None,
            )
        terminal_state = row.state
    from src.lib.openai_agents.extraction_trace_events import (
        write_extraction_trace_event,
    )

    write_extraction_trace_event(
        event_type="validation_review.workspace_operation",
        output_summary={"operation_id": operation_id, "state": terminal_state},
    )


def claim():
    from sqlalchemy import update, text

    now = admission.now()
    with store.SessionLocal.begin() as db:
        db.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtextextended('workspace-review-claim', 0))"
            )
        )
        # No provider replay after an expired running lease. Its answers, if any,
        # still belong to the original episode and retain all charges.
        for interrupted in db.scalars(
            select(Operation)
            .where(Operation.state == "running", Operation.lease_until < now)
            .with_for_update()
        ):
            interrupted.state = "uncertain"
            interrupted.outcome = {
                **(interrupted.outcome or {}),
                "message": "Execution was interrupted; inspect saved history before requesting another round.",
            }
        db.execute(
            update(Operation)
            .where(Operation.state == "preparing", Operation.lease_until < now)
            .values(state="queued", lease_until=None)
        )
        db.flush()  # SessionLocal disables autoflush; publish expired running states before overlap checks.
        active_artifacts = {
            item["artifact_id"]
            for artifacts in db.scalars(
                select(Operation.artifacts).where(
                    Operation.state.in_(["preparing", "running"])
                )
            )
            for item in artifacts
        }
        row = next(
            (
                candidate
                for candidate in db.scalars(
                    select(Operation)
                    .where(Operation.state == "queued")
                    .order_by(Operation.created_at, Operation.id)
                    .with_for_update(skip_locked=True)
                )
                if not active_artifacts.intersection(
                    item["artifact_id"] for item in candidate.artifacts
                )
            ),
            None,
        )
        if row is None:
            return None
        row.state = "preparing"
        row.lease_until = now + timedelta(
            seconds=ReviewLimits.configured().lease_seconds
        )
        row.lease_token = str(uuid4())
        return row.id, row.lease_token


def heartbeat(operation_id, lease_token):
    with store.SessionLocal.begin() as db:
        row = db.scalar(
            select(Operation).where(Operation.id == operation_id).with_for_update()
        )
        if row.state not in {"preparing", "running"} or row.lease_token != lease_token:
            return False
        row.lease_until = admission.now() + timedelta(
            seconds=ReviewLimits.configured().lease_seconds
        )
        return True


async def run_claimed(claimed):
    operation_id, lease_token = claimed
    task = asyncio.create_task(execute(operation_id, lease_token))
    try:
        while not task.done():
            await asyncio.wait(
                {task}, timeout=ReviewLimits.configured().lease_seconds / 3
            )
            if not task.done() and not await asyncio.to_thread(
                heartbeat, operation_id, lease_token
            ):
                task.cancel()
                return
        await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def cancel(operation_id, *, owner_id):
    with store.SessionLocal.begin() as db:
        row = get(db, operation_id, owner_id=owner_id)
        db.refresh(row, with_for_update=True)
        if row.state not in {
            "queued",
            "preparing",
            "running",
            "needs_review",
            "uncertain",
        }:
            return response(row)
        row.state = "cancelled"
        _abandon_unadmitted(
            db,
            row,
            actor={"type": "curator", "id": owner_id},
            reason="Curator stopped preparation before admission",
        )
        artifacts = list(row.artifacts)
        result = response(row)
    for item in artifacts:
        if "episode_id" not in item:
            continue
        from src.models.sql.validation_review import ValidationReviewEpisode

        with store.SessionLocal() as db:
            if db.get(ValidationReviewEpisode, item["episode_id"]) is None:
                continue  # Preparation never admitted this child; no grant exists.
        scope = store.HistoryScope(
            owner_id, item["document_id"], item["history_session_id"]
        )
        try:
            admission.cancel(
                scope,
                item["episode_id"],
                actor_id=owner_id,
                reason="Curator stopped workspace revalidation",
            )
        except admission.ReviewStopped:
            pass  # An already terminal episode keeps its recorded state and usage.
    return result


def resume(db, operation_id, *, owner_id, groups):
    row = get(db, operation_id, owner_id=owner_id)
    db.refresh(row, with_for_update=True)
    if not set(row.request["groups"]).issubset(groups):
        raise store.HistoryAccessError(
            "Access groups changed; request a new authorized round"
        )
    if row.state == "needs_review":
        if (row.outcome or {}).get("reason") == "preparation_abandoned":
            raise store.HistoryConflict(
                "Preparation was abandoned; inspect saved history or explicitly request a new round"
            )
        if not row.artifacts:
            raise store.HistoryConflict("No eligible records exist in this round")
        row.state = "queued"
        row.outcome = {
            "excluded_records": (row.outcome or {}).get("excluded_records", [])
        }
    elif row.state not in {"queued", "preparing", "running", "complete"}:
        raise store.HistoryConflict(
            "This operation cannot be replayed; inspect its history and explicitly request a new round"
        )
    return response(row)


def active_for_session(db, session_id, *, owner_id):
    """Small live command set for page-level completion observation."""
    rows = db.scalars(
        select(Operation).where(
            Operation.owner_id == owner_id,
            Operation.session_id == str(session_id),
            Operation.state.in_(["queued", "preparing", "running"]),
        )
    )
    return [response(get(db, row.id, owner_id=owner_id)) for row in rows]
