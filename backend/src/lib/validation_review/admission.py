"""Trusted scope admission. Lock parent quotas before artifact and episode.

All public operations own short transactions. No model or queue IO occurs here.
Immutable validation facts retain operation history alongside mutable quota indexes.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from src.lib.validation_history import store
from .audit import append_fact
from src.models.sql.validation_history import ValidationFact
from src.models.sql.validation_review import (
    ValidationReviewScope as Scope,
    ValidationReviewCoverage as Coverage,
    ValidationReviewEpisode as Episode,
    ValidationReviewReservation as Reservation,
)
from .policy import ReviewLimits


class ReviewStopped(store.HistoryConflict):
    """Expected pending outcome, not a scientific verdict or Sentry error."""

    def __init__(self, reason, *, resource=None):
        self.reason = reason
        self.resource = resource
        super().__init__(reason)


def now():
    return datetime.now(timezone.utc)


def create_scope(
    history_scope, *, operation_id, kind, targets, episodes, parent_id=None
):
    """Called by authenticated orchestration, never with model-created cardinalities."""
    if kind not in {"batch", "discovered_batch", "workspace_operation", "document_run", "discovered_document_run"}:
        raise ValueError("Unknown review scope kind")
    if (
        type(targets) is not int
        or type(episodes) is not int
        or targets < 0
        or episodes < (0 if kind in {"discovered_document_run", "discovered_batch"} else 1)
    ):
        raise ValueError("Trusted scope requires target and episode cardinalities")
    if kind in {"discovered_document_run", "discovered_batch"} and (targets or episodes):
        raise ValueError("Discovered scopes register coverage at admission")
    if kind == "discovered_batch" and parent_id:
        raise ValueError("A discovered batch is a root review scope")
    limits = ReviewLimits.configured()
    extras = limits.extra_per_batch if kind in {"batch", "discovered_batch"} else limits.extra_per_run
    contract = {
        "document_id": history_scope.document_id if kind in {"document_run", "discovered_document_run"} else None,
        "targets": targets,
        "episodes": episodes,
        "policy": limits.snapshot(),
        "allowances": limits.allowances(
            targets=targets, episodes=episodes, extras=extras
        ),
    }
    with store.SessionLocal.begin() as db:
        store._authorize(db, history_scope)
        parent = None
        if parent_id:
            parent = _scope_chain(db, parent_id, history_scope.owner_id)[-1]
            if parent is None or parent.owner_id != history_scope.owner_id:
                raise store.HistoryAccessError("Review parent scope unavailable")
            ordinary_child = kind == "document_run" and parent.kind in {"batch", "workspace_operation"}
            discovered_child = kind == "discovered_document_run" and parent.kind == "discovered_batch"
            if not (ordinary_child or discovered_child):
                raise store.HistoryConflict("Invalid review scope hierarchy")
            inherited = ReviewLimits(**parent.contract["policy"])
            contract = {
                **contract,
                "policy": inherited.snapshot(),
                "allowances": inherited.allowances(
                    targets=targets, episodes=episodes, extras=inherited.extra_per_run
                ),
            }
        elif kind == "document_run":
            pass  # Standalone document operation is a legitimate root.
        db.execute(
            insert(Scope)
            .values(
                id=str(uuid4()),
                owner_id=history_scope.owner_id,
                operation_id=operation_id,
                kind=kind,
                parent_id=parent_id,
                contract=contract,
                charged={},
            )
            .on_conflict_do_nothing(index_elements=["owner_id", "operation_id"])
        )
        row = db.scalar(
            select(Scope).where(
                Scope.owner_id == history_scope.owner_id,
                Scope.operation_id == operation_id,
            )
        )
        # An operation retains its original policy even if environment settings change.
        identity = {
            key: value
            for key, value in contract.items()
            if key not in {"policy", "allowances"}
        }
        recorded = {
            key: value
            for key, value in row.contract.items()
            if key not in {"policy", "allowances"}
        }
        if row.kind != kind or row.parent_id != parent_id or recorded != identity:
            raise store.HistoryConflict("Review scope operation conflict")
        if parent is not None:
            children = db.scalars(
                select(Scope).where(Scope.parent_id == parent_id)
            ).all()
            for cardinality in ("targets", "episodes"):
                if (
                    sum(child.contract[cardinality] for child in children)
                    > parent.contract[cardinality]
                ):
                    raise store.HistoryConflict(
                        "Document scopes exceed trusted parent coverage"
                    )
        return row.id


def _scope_chain(db, scope_id, owner_id):
    ids = []
    cursor = scope_id
    while cursor:
        if cursor in ids:
            raise store.HistoryConflict("Review scope cycle")
        row = db.get(Scope, cursor)
        if row is None or row.owner_id != owner_id:
            raise store.HistoryAccessError("Review scope unavailable")
        ids.append(cursor)
        cursor = row.parent_id
    return [
        db.scalar(
            select(Scope)
            .where(Scope.id == sid)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        for sid in reversed(ids)
    ]


def attach_episode(
    history_scope,
    *,
    artifact_id,
    episode_id,
    scope_id,
    predecessor_id=None,
    renewal_actor_id=None,
    renewal_reason=None,
):
    """Bind declared obligations before initial work, freezing inherited policy."""
    with store.SessionLocal.begin() as db:
        chain = _scope_chain(db, scope_id, history_scope.owner_id)
        run = chain[-1]
        if (
            run.kind not in {"document_run", "discovered_document_run"}
            or run.contract["document_id"] != history_scope.document_id
        ):
            raise store.HistoryAccessError("Episode requires its document-run scope")
        from src.lib.validation_history.selections import _lock_artifact

        _lock_artifact(db, history_scope, artifact_id, write=True)
        fact = store._fact(db, artifact_id, episode_id, kind="review_episode")
        if predecessor_id:
            if (
                renewal_actor_id != history_scope.owner_id
                or not isinstance(renewal_reason, str)
                or not renewal_reason.strip()
            ):
                raise store.HistoryAccessError(
                    "Later review episode requires explicit authenticated owner renewal"
                )
            if len(renewal_reason) > run.contract["policy"]["instruction_chars"]:
                raise ValueError("Renewal reason exceeds configured character limit")
        existing = db.get(Episode, episode_id)
        if existing:
            if (existing.artifact_id, existing.scope_id, existing.predecessor_id) != (
                artifact_id,
                scope_id,
                predecessor_id,
            ):
                raise store.HistoryConflict("Episode scope identity conflict")
            return existing.id
        if db.scalar(
            select(ValidationFact.id)
            .where(
                ValidationFact.artifact_id == artifact_id,
                ValidationFact.kind == "attempt",
                ValidationFact.sequence > fact.sequence,
            )
            .limit(1)
        ):
            raise store.HistoryConflict("Admission must precede initial validator work")
        if predecessor_id:
            previous = db.get(Episode, predecessor_id)
            if (
                previous is None
                or previous.scope_id != scope_id
                or previous.artifact_id != artifact_id
            ):
                raise store.HistoryConflict(
                    "Human renewal must retain its parent scope"
                )
        targets = set()
        for obligation in store._content(db, fact)["obligations"]:
            if obligation["mode"] != "validation":
                continue
            target = store._fact(
                db, artifact_id, obligation["target_id"], kind="target"
            )
            content = store._content(db, target)
            # Component targets are partitions of one binding invocation.
            targets.add(target.parent_id if "decision_unit" in content else target.id)
        policy = ReviewLimits(**run.contract["policy"])
        discovered = run.kind == "discovered_document_run"
        if discovered and predecessor_id is None:
            coverage = db.get(Coverage, artifact_id)
            if coverage is not None:
                raise store.HistoryConflict("Artifact coverage already belongs to a review operation")
            credits = policy.allowances(targets=len(targets), episodes=1, extras=0)
            db.add(Coverage(
                artifact_id=artifact_id, episode_id=episode_id, scope_id=run.id,
                targets=sorted(targets), allowances=credits,
            ))
            append_fact(
                db, artifact_id, operation_id="review-coverage:" + episode_id,
                kind="review_coverage", parent_id=episode_id,
                body={"scope_id": run.id, "targets": sorted(targets), "credits": credits},
            )
        admitted = db.scalars(select(Episode).where(Episode.scope_id == run.id)).all()
        if (
            not discovered
            and predecessor_id is None
            and sum(row.predecessor_id is None for row in admitted)
            >= run.contract["episodes"]
        ):
            raise store.HistoryConflict(
                "Episode exceeds trusted document episode count"
            )
        previous_targets = sum(
            len(
                store._content(
                    db,
                    db.scalar(
                        select(ValidationFact).where(
                            ValidationFact.artifact_id == row.artifact_id,
                            ValidationFact.operation_id == "review-admission:" + row.id,
                        )
                    ),
                )["targets"]
            )
            for row in admitted
            if row.predecessor_id is None
        )
        if (
            not discovered
            and len(targets) + (previous_targets if predecessor_id is None else 0)
            > run.contract["targets"]
        ):
            raise store.HistoryConflict(
                "Episode exceeds declared document target coverage"
            )
        row = Episode(
            id=episode_id,
            artifact_id=artifact_id,
            scope_id=scope_id,
            predecessor_id=predecessor_id,
            policy=policy.snapshot(),
            allowances=policy.allowances(
                targets=len(targets),
                episodes=1,
                # Explicit split/add contributions may add binding targets.
                # Keep the existing shared document ceiling available; per-target
                # ancestry and parent charges independently prevent any refill.
                extras=policy.extra_per_run if targets else 0,
            ),
            charged={},
            state="open",
        )
        db.add(row)
        append_fact(
            db,
            artifact_id,
            operation_id="review-admission:" + episode_id,
            kind="review_admission",
            parent_id=episode_id,
            body={
                "scope_id": scope_id,
                "predecessor_id": predecessor_id,
                "targets": sorted(targets),
                "policy": policy.snapshot(),
                "renewal": (
                    {"actor_id": renewal_actor_id, "reason": renewal_reason}
                    if predecessor_id
                    else None
                ),
            },
        )
        return row.id


def lock_episode(db, history_scope, episode_id, *, require_open=True):
    row = db.get(Episode, episode_id)
    if row is None:
        raise store.HistoryAccessError("Admitted review episode unavailable")
    chain = _scope_chain(db, row.scope_id, history_scope.owner_id)
    from src.lib.validation_history.selections import _lock_artifact

    _lock_artifact(db, history_scope, row.artifact_id, write=True)
    row = db.scalar(
        select(Episode)
        .where(Episode.id == episode_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if require_open:
        if store._document_retirement(db, row.artifact_id) is not None:
            raise ReviewStopped("retired")
        latest = db.scalar(
            select(ValidationFact.id)
            .where(
                ValidationFact.artifact_id == row.artifact_id,
                ValidationFact.kind == "review_episode",
            )
            .order_by(ValidationFact.sequence.desc())
            .limit(1)
        )
        if latest != row.id:
            raise ReviewStopped("superseded")
        if row.state == "complete":
            from src.lib.validation_history.selections import manifest_in_transaction

            manifest = manifest_in_transaction(
                db, history_scope, row.artifact_id, episode_id=row.id
            )
            if any(
                item["code"] == "answer_review_pending" for item in manifest["blockers"]
            ):
                # A late original answer is a new review obligation, not a new
                # episode or a grant of more time/tokens/attempts. Preserve prior
                # selections until an explicit decision changes them.
                row.state = "open"
                append_fact(
                    db,
                    row.artifact_id,
                    operation_id="review-reopened:" + row.id + ":" + manifest["sha256"],
                    kind="review_reopened",
                    parent_id=row.id,
                    body={
                        "reason": "late_answer_review_pending",
                        "manifest_sha256": manifest["sha256"],
                    },
                )
        if row.state != "open":
            raise ReviewStopped(row.state)
        if row.started_at and now() >= row.started_at + timedelta(
            seconds=row.policy["deadline_seconds"]
        ):
            raise ReviewStopped("expired")
    return row, chain


def _allowances(db, row):
    if isinstance(row, Episode):
        return row.allowances
    allowances = dict(row.contract["allowances"])
    if row.kind in {"discovered_document_run", "discovered_batch"}:
        coverage_scope = (Coverage.scope_id == row.id if row.kind == "discovered_document_run"
                          else Coverage.scope_id.in_(select(Scope.id).where(Scope.parent_id == row.id)))
        for credits in db.scalars(select(Coverage.allowances).where(coverage_scope)):
            for key, value in credits.items():
                allowances[key] += value
    return allowances


def reserve_in_transaction(
    db, history_scope, episode_id, *, operation_id, charges, request, start_work=True
):
    episode, chain = lock_episode(db, history_scope, episode_id)
    if not charges or any(type(v) is not int or v < 0 for v in charges.values()):
        raise ValueError("Charges must be nonnegative integer resource amounts")
    existing = db.scalar(
        select(Reservation).where(
            Reservation.episode_id == episode_id,
            Reservation.operation_id == operation_id,
        )
    )
    request = store.canonical(request)[1]
    if existing:
        if existing.request != request or existing.charges != charges:
            raise store.HistoryConflict("Admission operation conflict")
        return existing
    for row in [*chain, episode]:
        for key, amount in charges.items():
            if key not in _allowances(db, row):
                raise ValueError("Unknown review budget resource")
            if row.charged.get(key, 0) + amount > _allowances(db, row)[key]:
                raise ReviewStopped("budget_exhausted", resource=key)
    for row in [*chain, episode]:
        for key, amount in charges.items():
            before, limit = row.charged.get(key, 0), _allowances(db, row)[key]
            for threshold in (
                episode.policy["warning_percent"],
                episode.policy["critical_percent"],
            ):
                if (
                    limit
                    and before * 100 < limit * threshold <= (before + amount) * 100
                ):
                    append_fact(
                        db,
                        episode.artifact_id,
                        operation_id=f"review-warning:{operation_id}:{row.id}:{key}:{threshold}",
                        kind="review_budget_warning",
                        parent_id=episode.id,
                        body={
                            "scope_id": row.id,
                            "resource": key,
                            "threshold": threshold,
                            "charged": before + amount,
                            "allowance": limit,
                        },
                    )
        row.charged = {
            **row.charged,
            **{
                key: row.charged.get(key, 0) + amount for key, amount in charges.items()
            },
        }
    if start_work and episode.started_at is None:
        episode.started_at = now()
    reservation = Reservation(
        id=str(uuid4()),
        episode_id=episode_id,
        operation_id=operation_id,
        request=request,
        charges=dict(charges),
        state="reserved",
    )
    db.add(reservation)
    append_fact(
        db,
        episode.artifact_id,
        operation_id="review-reservation:" + operation_id,
        kind="review_reservation",
        parent_id=episode.id,
        body={"reservation_id": reservation.id, "charges": charges, "request": request},
    )
    return reservation


def reserve(history_scope, episode_id, *, operation_id, charges, request):
    with store.SessionLocal.begin() as db:
        return reserve_in_transaction(
            db,
            history_scope,
            episode_id,
            operation_id=operation_id,
            charges=charges,
            request=request,
        ).id


def reconcile(history_scope, episode_id, reservation_id, *, actual):
    """Unknown completion keeps the full charge; observed usage is never discarded."""
    with store.SessionLocal.begin() as db:
        episode, chain = lock_episode(db, history_scope, episode_id, require_open=False)
        row = db.get(Reservation, reservation_id)
        if row is None or row.episode_id != episode.id:
            raise store.HistoryAccessError("Reservation unavailable")
        state = "uncertain" if actual is None else "settled"
        if row.state == "settled":
            if actual != row.actual:
                raise store.HistoryConflict("Provider usage reconciliation conflict")
            return
        if row.state == "uncertain" and actual is None:
            return
        if actual is not None:
            if set(actual) != set(row.charges) or any(
                type(v) is not int or v < 0 for v in actual.values()
            ):
                raise ValueError("Actual usage must cover every reserved resource")
            for target in [*chain, episode]:
                target.charged = {
                    **target.charged,
                    **{
                        key: target.charged[key] - row.charges[key] + value
                        for key, value in actual.items()
                    },
                }
        if actual is not None and any(actual[key] > row.charges[key] for key in actual):
            # Keep real usage even when a provider violates its reserved ceiling.
            episode.state = "provider_budget_overrun"
        row.state, row.actual = state, actual
        append_fact(
            db,
            episode.artifact_id,
            operation_id="review-reconciled:" + reservation_id + ":" + state,
            kind="review_usage",
            parent_id=episode.id,
            body={"reservation_id": reservation_id, "state": state, "actual": actual},
        )


def status(history_scope, episode_id):
    with store.SessionLocal.begin() as db:
        episode, chain = lock_episode(db, history_scope, episode_id, require_open=False)
        reason = None
        try:
            lock_episode(db, history_scope, episode_id)
        except ReviewStopped as exc:
            reason = exc.reason
        from src.lib.validation_history.selections import manifest_in_transaction

        manifest = manifest_in_transaction(
            db, history_scope, episode.artifact_id, episode_id=episode.id
        )
        budgets, warnings = [], []
        for row in [*chain, episode]:
            remaining = {
                k: max(0, v - row.charged.get(k, 0))
                for k, v in _allowances(db, row).items()
            }
            budgets.append({"scope_id": row.id, "remaining": remaining})
            for key, limit in _allowances(db, row).items():
                consumed = row.charged.get(key, 0)
                if (
                    limit
                    and consumed * 100 >= limit * episode.policy["warning_percent"]
                ):
                    warnings.append(
                        {
                            "scope_id": row.id,
                            "resource": key,
                            "threshold": (
                                episode.policy["critical_percent"]
                                if consumed * 100
                                >= limit * episode.policy["critical_percent"]
                                else episode.policy["warning_percent"]
                            ),
                        }
                    )
        state = reason or ("complete" if manifest["ready"] else "pending")
        if reason == "complete" and not manifest["ready"]:
            state = "stale"
        return {
            "episode_id": episode.id,
            "state": state,
            "ready": manifest["ready"] and reason in {None, "complete"},
            "uncovered": manifest["blockers"],
            "budgets": budgets,
            "warnings": warnings,
        }


def cancel(history_scope, episode_id, *, actor_id, reason):
    """Trusted explicit cancellation; retain all history, reservations and late answers."""
    if (
        actor_id != history_scope.owner_id
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        raise store.HistoryAccessError(
            "Cancellation requires the authenticated owner and reason"
        )
    with store.SessionLocal.begin() as db:
        episode, _ = lock_episode(db, history_scope, episode_id, require_open=False)
        if episode.state == "cancelled":
            return
        if episode.state != "open":
            raise ReviewStopped(episode.state)
        episode.state = "cancelled"
        append_fact(
            db,
            episode.artifact_id,
            operation_id="review-cancel:" + episode.id,
            kind="review_cancellation",
            parent_id=episode.id,
            body={"actor_id": actor_id, "reason": reason},
        )
