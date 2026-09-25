"""Resolve accounting only after verifying benchmark ownership and membership."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.openai_agents.config import (
    get_cost_ledger_benchmark_source_namespace, get_cost_ledger_deployment_id,
)
from src.models.sql.benchmark import BenchmarkCell, BenchmarkInvocation, BenchmarkJob
from src.models.sql.cost_ledger import CostSourceReference
from src.schemas.cost_ledger import CostFactsProjection, CostLedgerReference
from .persistence import read_cost_facts


class BenchmarkAccountingUnavailable(RuntimeError):
    """Deployment not configured or invocation not bound to the shared ledger."""


def read_benchmark_accounting(
    session: Session, *, job_id: UUID, cell_id: UUID, invocation_id: UUID,
    owner_subject: str, revision: int | None = None,
) -> CostFactsProjection:
    """Return a pinned projection; omitted revision resolves latest once.

No client-supplied ledger UUID or deployment label is trusted. A verified source
binding is required even if the invocation has a measured request ID. Existing
inline benchmark cost columns are never consulted as a fallback.
"""
    owned_invocation = session.scalar(select(BenchmarkInvocation.id).join(
        BenchmarkCell, BenchmarkCell.id == BenchmarkInvocation.cell_id,
    ).join(BenchmarkJob, BenchmarkJob.id == BenchmarkCell.job_id).where(
        BenchmarkInvocation.id == invocation_id, BenchmarkCell.id == cell_id,
        BenchmarkJob.id == job_id, BenchmarkJob.owner_subject == owner_subject,
    ))
    if owned_invocation is None:
        raise LookupError("Benchmark invocation not found in owned job and cell")
    deployment = get_cost_ledger_deployment_id()
    namespace = get_cost_ledger_benchmark_source_namespace()
    if not deployment or not namespace:
        raise BenchmarkAccountingUnavailable("Benchmark accounting is not configured")
    attempt_id = session.scalar(select(CostSourceReference.attempt_id).where(
        CostSourceReference.deployment_id == deployment,
        CostSourceReference.source_system == "benchmark",
        CostSourceReference.source_namespace == namespace,
        CostSourceReference.source_id == str(invocation_id),
    ))
    if attempt_id is None:
        raise BenchmarkAccountingUnavailable("Benchmark invocation has no ledger binding")
    facts = read_cost_facts(
        session, deployment_id=deployment, owner_subject=owner_subject,
        attempt_id=attempt_id, revision=revision,
    )
    return CostFactsProjection(
        schema_version=1,
        reference=CostLedgerReference(
            schema_version=1, deployment_id=deployment, attempt_id=attempt_id,
            fact_revision=facts.revision if facts.revision is not None else 0,
        ),
        usage=facts.usage, usage_status=facts.usage.status,
        usage_issues=facts.usage.issues, recorded_charge=facts.charge,
    )
