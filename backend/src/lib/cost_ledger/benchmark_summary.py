"""Owner-scoped job summaries derived from ledger facts, never persisted totals."""

from uuid import UUID

from sqlalchemy import String, and_, cast, func, select, text
from sqlalchemy.orm import Session

from src.lib.openai_agents.config import (
    get_cost_ledger_benchmark_source_namespace, get_cost_ledger_deployment_id,
)
from src.models.sql.benchmark import BenchmarkCell, BenchmarkInvocation, BenchmarkJob
from src.models.sql.cost_ledger import CostAttempt, CostFactRevision, CostSourceReference
from src.schemas.cost_ledger import BenchmarkJobAccounting
from .benchmark_reads import BenchmarkAccountingUnavailable
from .summary import summarize_fact_rows


def read_benchmark_job_accounting(session: Session, *, job_id: UUID, owner_subject: str) -> BenchmarkJobAccounting:
    """Read all contributing attempts once in a caller-owned consistent snapshot.

    Missing bindings are accounting unavailability, not zero/unknown charges.
    Bound attempts lacking reported facts are explicitly unknown. No inline
    benchmark usage, artifact copies, or pricing estimates are consulted.
    """
    if session.scalar(text("SHOW transaction_read_only")) != "on" or session.scalar(
        text("SHOW transaction_isolation")
    ) != "repeatable read":
        raise ValueError("Accounting summary requires a repeatable-read, read-only transaction")
    if session.scalar(select(BenchmarkJob.id).where(
        BenchmarkJob.id == job_id, BenchmarkJob.owner_subject == owner_subject,
    )) is None:
        raise LookupError("Benchmark job not found")
    deployment, namespace = get_cost_ledger_deployment_id(), get_cost_ledger_benchmark_source_namespace()
    if not deployment or not namespace:
        raise BenchmarkAccountingUnavailable("Benchmark accounting is not configured")

    sources = select(BenchmarkInvocation.id.label("invocation_id"), CostAttempt.id.label("attempt_id")).join(
        BenchmarkCell, BenchmarkCell.id == BenchmarkInvocation.cell_id,
    ).outerjoin(CostSourceReference, and_(
        CostSourceReference.deployment_id == deployment,
        CostSourceReference.source_system == "benchmark",
        CostSourceReference.source_namespace == namespace,
        CostSourceReference.source_id == cast(BenchmarkInvocation.id, String),
    )).outerjoin(CostAttempt, and_(
        CostAttempt.deployment_id == deployment,
        CostAttempt.id == CostSourceReference.attempt_id,
        CostAttempt.owner_subject == owner_subject,
    )).where(BenchmarkCell.job_id == job_id).subquery()
    invocation_count, bound_count = session.execute(select(
        func.count(sources.c.invocation_id), func.count(sources.c.attempt_id),
    )).one()
    if invocation_count != bound_count:
        raise BenchmarkAccountingUnavailable("Benchmark invocation accounting is incomplete")
    attempts = select(sources.c.attempt_id).distinct().subquery()
    query = select(attempts.c.attempt_id, CostFactRevision).outerjoin(CostFactRevision, and_(
        CostFactRevision.deployment_id == deployment,
        CostFactRevision.attempt_id == attempts.c.attempt_id,
    )).order_by(attempts.c.attempt_id, CostFactRevision.revision)

    return BenchmarkJobAccounting(
        job_id=job_id, invocation_count=invocation_count,
        **summarize_fact_rows(session, query),
    )
