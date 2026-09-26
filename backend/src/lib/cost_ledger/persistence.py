"""Caller-transaction-owned accounting additions with reproducible history.

Internal repository, not producer authorization. Callers supply verified scope
and maintain execution lease fencing. This repository never commits independently.
"""

from dataclasses import dataclass, fields
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.sql.cost_ledger import CostAttempt, CostFactRevision
from .facts import RecordedCharge, TokenUsage, enrich_charge, enrich_usage
from .identity import bind_cost_source


@dataclass(frozen=True)
class CostFacts:
    revision: int | None
    usage: TokenUsage
    charge: RecordedCharge | None


def read_cost_facts(
    session: Session, *, deployment_id: str, owner_subject: str, attempt_id: UUID,
    revision: int | None = None,
) -> CostFacts:
    """Read history or latest facts; revision zero pins the empty snapshot.

None means latest. Zero never follows late evidence and still requires the
attempt to exist in the supplied owner/deployment scope.
"""
    if revision is not None and (type(revision) is not int or revision < 0):
        raise ValueError("Revision must be a nonnegative integer")
    attempt = session.scalar(select(CostAttempt.id).where(
        CostAttempt.deployment_id == deployment_id, CostAttempt.id == attempt_id,
        CostAttempt.owner_subject == owner_subject,
    ))
    if attempt is None:
        raise LookupError("Accounting attempt not found in the supplied scope")
    if revision == 0:
        return CostFacts(None, TokenUsage(), None)
    query = select(CostFactRevision).where(
        CostFactRevision.deployment_id == deployment_id, CostFactRevision.attempt_id == attempt_id,
    ).order_by(CostFactRevision.revision)
    if revision is not None:
        query = query.where(CostFactRevision.revision <= revision)
    usage, charge, latest = TokenUsage(), None, None
    for row in session.scalars(query):
        usage = enrich_usage(usage, TokenUsage(**{
            field.name: getattr(row, field.name) for field in fields(TokenUsage)
        }))
        incoming_charge = None
        if row.billed_amount is not None:
            assert row.billed_unit is not None and row.billed_source is not None
            incoming_charge = RecordedCharge(row.billed_amount, row.billed_unit, row.billed_source)
        charge = enrich_charge(charge, incoming_charge)
        latest = row.revision
    if revision is not None and latest != revision:
        raise LookupError("Accounting fact revision not found")
    return CostFacts(latest, usage, charge)


def record_cost_facts(
    session: Session, *, deployment_id: str, owner_subject: str, attempt_id: UUID,
    source_system: str, source_namespace: str, source_id: str,
    usage: TokenUsage, charge: RecordedCharge | None = None,
) -> CostFacts:
    """Enrich facts once; savepoint rolls back source binding on conflict.

Attempt-row locking serializes additions across different source references.
Revisions contain only newly known quantities. Identical replay returns the
existing revision; contradictory facts never overwrite a previous revision.
"""
    with session.begin_nested():
        bind_cost_source(
            session, deployment_id=deployment_id, owner_subject=owner_subject,
            attempt_id=attempt_id, source_system=source_system,
            source_namespace=source_namespace, source_id=source_id,
        )
        session.execute(select(CostAttempt.id).where(
            CostAttempt.deployment_id == deployment_id, CostAttempt.id == attempt_id,
        ).with_for_update(key_share=True)).scalar_one()
        previous = read_cost_facts(
            session, deployment_id=deployment_id, owner_subject=owner_subject, attempt_id=attempt_id,
        )
        combined = enrich_usage(previous.usage, usage)
        combined_charge = enrich_charge(previous.charge, charge)
        if combined == previous.usage and combined_charge == previous.charge:
            return previous
        additions = {
            field.name: getattr(combined, field.name)
            for field in fields(TokenUsage) if getattr(previous.usage, field.name) is None
        }
        added_charge = charge if previous.charge is None else None
        next_revision = (previous.revision or 0) + 1
        session.add(CostFactRevision(
            deployment_id=deployment_id, attempt_id=attempt_id, revision=next_revision,
            source_system=source_system, source_namespace=source_namespace, source_id=source_id,
            **additions,
            billed_amount=added_charge.amount if added_charge else None,
            billed_unit=added_charge.unit if added_charge else None,
            billed_source=added_charge.source if added_charge else None,
        ))
        session.flush()
        return CostFacts(next_revision, combined, combined_charge)
