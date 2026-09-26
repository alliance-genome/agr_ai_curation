"""Transactional, replay-safe registration of verified accounting identities.

Internal storage contract only: callers must authenticate the producer and derive
deployment/owner scope before calling. Possession of a UUID is not authorization.
No commit is performed here; benchmark callers must retain their lease fencing in
the same transaction. Do not derive identity from timestamps or content digests.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.models.sql.cost_ledger import CostAttempt, CostSourceReference


class CostIdentityConflict(ValueError):
    """A verified source or attempt identity is already bound differently."""


def bind_cost_source(
    session: Session,
    *,
    deployment_id: str,
    owner_subject: str,
    attempt_id: UUID,
    source_system: str,
    source_namespace: str,
    source_id: str,
) -> UUID:
    """Bind a verified source to an attempt, accepting only identical replays.

An inner savepoint prevents a rejected binding from leaving an orphan attempt
even if the caller catches the conflict and commits its outer transaction.
PostgreSQL unique constraints serialize competing deliveries. This contract uses
the application's default READ COMMITTED isolation; callers retry serialization
errors at their transaction boundary if they use a stricter isolation level.
"""
    for value in (deployment_id, owner_subject, source_system, source_namespace, source_id):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Accounting identity scope and source fields must be nonempty")
    if not isinstance(attempt_id, UUID):
        raise ValueError("Accounting attempt identity must be a verified UUID")

    attempt_key = {"deployment_id": deployment_id, "id": attempt_id}
    source_key = {
        "deployment_id": deployment_id,
        "source_system": source_system,
        "source_namespace": source_namespace,
        "source_id": source_id,
    }
    with session.begin_nested():
        session.execute(
            insert(CostAttempt).values(**attempt_key, owner_subject=owner_subject)
            .on_conflict_do_nothing(index_elements=["deployment_id", "id"])
        )
        owner = session.scalar(select(CostAttempt.owner_subject).filter_by(**attempt_key))
        if owner != owner_subject:
            raise CostIdentityConflict("Accounting attempt belongs to a different owner")
        session.execute(
            insert(CostSourceReference).values(**source_key, attempt_id=attempt_id)
            .on_conflict_do_nothing(index_elements=list(source_key))
        )
        bound_id = session.scalar(select(CostSourceReference.attempt_id).filter_by(**source_key))
        if bound_id != attempt_id:
            raise CostIdentityConflict("Accounting source already identifies a different attempt")
    return attempt_id
