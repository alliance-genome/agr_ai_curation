"""Explicit benchmark backfill planning, not a runtime compatibility reader.

No writer/API uses this module. A coordinated migration must obtain the owner
from the invocation's job, verify deployment/source scope, freeze execution, and
verify persisted receipts before removing the old accounting columns. Immutable
result artifacts are not rewritten. Historical source identity is not evidence
of a join to independently retained telemetry.
"""

from dataclasses import asdict, dataclass
from decimal import Decimal
import json
from typing import Literal
from uuid import UUID

from sqlalchemy import MetaData, Table, select
from sqlalchemy.orm import Session

from src.models.sql.benchmark import (
    BenchmarkCell, BenchmarkCellStatus, BenchmarkInvocationStatus,
    BenchmarkJob, BenchmarkJobStatus,
)
from src.lib.openai_agents.config import get_cost_migration_audit_page_size
from .facts import RecordedCharge, TokenUsage
from .benchmark_identity import benchmark_attempt_id
from .persistence import CostFacts


TERMINAL_JOBS = (
    BenchmarkJobStatus.COMPLETED, BenchmarkJobStatus.COMPLETED_WITH_FAILURES,
    BenchmarkJobStatus.CANCELLED, BenchmarkJobStatus.FAILED,
)
TERMINAL_CELLS = (BenchmarkCellStatus.SUCCEEDED, BenchmarkCellStatus.FAILED, BenchmarkCellStatus.CANCELLED)


@dataclass(frozen=True)
class HistoricalBenchmarkInvocation:
    """Explicit pre-cutover schema record; never used by live execution."""

    id: UUID
    status: str
    model_request_id: UUID | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    billed_amount: Decimal | None
    billed_unit: str | None
    billed_source: str | None


def iter_benchmark_migration_rows(session: Session):
    """Bounded keyset traversal for a dedicated audit/backfill session."""
    cursor = None
    size = get_cost_migration_audit_page_size()
    source = Table("benchmark_invocations", MetaData(), autoload_with=session.connection())
    names = tuple(HistoricalBenchmarkInvocation.__dataclass_fields__)
    if not set(names) <= set(source.c.keys()):
        raise ValueError("Benchmark backfill requires the pre-cutover schema")
    while True:
        query = select(*(source.c[name] for name in names), BenchmarkJob.owner_subject,
                       BenchmarkJob.status.label("job_status"), BenchmarkCell.status.label("cell_status")).select_from(source).join(
            BenchmarkCell, BenchmarkCell.id == source.c.cell_id,
        ).join(BenchmarkJob, BenchmarkJob.id == BenchmarkCell.job_id).order_by(source.c.id).limit(size)
        if cursor is not None:
            query = query.where(source.c.id > cursor)
        page = session.execute(query).mappings().all()
        if not page:
            return
        for row in page:
            yield (HistoricalBenchmarkInvocation(**{name: row[name] for name in names}),
                   row["owner_subject"], row["job_status"], row["cell_status"])
        cursor = page[-1]["id"]


def migration_fingerprint_line(entry: "BenchmarkCostMigrationEntry") -> bytes:
    return json.dumps(asdict(entry), default=str, sort_keys=True, separators=(",", ":")).encode() + b"\n"


@dataclass(frozen=True)
class BenchmarkCostMigrationEntry:
    deployment_id: str
    owner_subject: str
    source_namespace: str
    invocation_id: UUID
    attempt_id: UUID
    model_request_id: UUID | None
    identity_basis: Literal["measured_request", "historical_benchmark_source"]
    usage: TokenUsage
    charge: RecordedCharge | None


def plan_benchmark_cost_migration(
    invocation: HistoricalBenchmarkInvocation, *, deployment_id: str,
    source_namespace: str, owner_subject: str,
) -> BenchmarkCostMigrationEntry:
    """Map one frozen invocation without inferring missing accounting facts.

Scope must come from verified deployment inventory and job ownership, never from
artifact-supplied user metadata. RUNNING rows cannot be backfilled mid-execution.
Terminal failure/cancellation does not imply zero cost or absence of usage.
"""
    for scope in (deployment_id, source_namespace, owner_subject):
        if not isinstance(scope, str) or not scope.strip():
            raise ValueError("Benchmark migration requires verified nonempty scope")
    if invocation.status not in (
        BenchmarkInvocationStatus.SUCCEEDED,
        BenchmarkInvocationStatus.FAILED,
        BenchmarkInvocationStatus.CANCELLED,
    ):
        raise ValueError("Only frozen terminal benchmark invocations can be backfilled")
    if not isinstance(invocation.id, UUID):
        raise ValueError("Benchmark migration requires the durable invocation UUID")
    measured = invocation.model_request_id
    if measured is not None and not isinstance(measured, UUID):
        raise ValueError("Measured request identity must be a UUID")
    # This stable surrogate names a historical source, not a measured request.
    # Tuple JSON avoids delimiter collisions. Keep this algorithm/version fixed
    # once any migration uses it; changing it would manufacture duplicate calls.
    attempt_id = benchmark_attempt_id(invocation_id=invocation.id, model_request_id=measured,
                                      deployment_id=deployment_id, source_namespace=source_namespace)
    usage = TokenUsage(
        input_tokens=invocation.input_tokens, output_tokens=invocation.output_tokens,
        total_tokens=invocation.total_tokens,
    )
    charge_parts = (invocation.billed_amount, invocation.billed_unit, invocation.billed_source)
    charge = None
    if any(value is not None for value in charge_parts):
        if any(value is None for value in charge_parts):
            raise ValueError("Historical benchmark charge has incomplete provenance")
        assert invocation.billed_amount is not None
        assert invocation.billed_unit is not None and invocation.billed_source is not None
        charge = RecordedCharge(invocation.billed_amount, invocation.billed_unit, invocation.billed_source)
    return BenchmarkCostMigrationEntry(
        deployment_id=deployment_id, owner_subject=owner_subject,
        source_namespace=source_namespace, invocation_id=invocation.id,
        attempt_id=attempt_id, model_request_id=measured,
        identity_basis="measured_request" if measured is not None else "historical_benchmark_source",
        usage=usage, charge=charge,
    )


def verify_benchmark_cost_receipt(entry: BenchmarkCostMigrationEntry, facts: CostFacts) -> None:
    """Require exact pre-cutover fact parity, not just equal grand totals.

The migration runner must obtain facts through the entry's verified scoped read.
Additional telemetry enrichment belongs after baseline verification. Historical
cache/reasoning unknowns cannot be silently filled during this verification.
"""
    if facts.usage != entry.usage or facts.charge != entry.charge:
        raise ValueError("Benchmark migration changed usage or recorded charge facts")
    has_facts = entry.usage.status != "missing" or entry.charge is not None
    if has_facts != (facts.revision is not None):
        raise ValueError("Benchmark migration receipt has inconsistent revision coverage")
