"""Explicit benchmark backfill planning, not a runtime compatibility reader.

No writer/API uses this module. A coordinated migration must obtain the owner
from the invocation's job, verify deployment/source scope, freeze execution, and
verify persisted receipts before removing the old accounting columns. Immutable
result artifacts are not rewritten. Historical source identity is not evidence
of a join to independently retained telemetry.
"""

from dataclasses import dataclass
import json
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from src.models.sql.benchmark import BenchmarkInvocation, BenchmarkInvocationStatus
from .facts import RecordedCharge, TokenUsage
from .persistence import CostFacts


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
    invocation: BenchmarkInvocation, *, deployment_id: str,
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
    attempt_id = measured if measured is not None else uuid5(NAMESPACE_URL, json.dumps(
        ["agr-ai-curation:benchmark-cost-source:v1", deployment_id,
         source_namespace, str(invocation.id)], separators=(",", ":"), ensure_ascii=True,
    ))
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
