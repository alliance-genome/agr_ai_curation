"""Read-only benchmark accounting preflight on the candidate schema.

Run inside the backend environment:
python -m src.lib.cost_ledger.benchmark_audit --deployment-id VERIFIED_ID \
    --source-namespace VERIFIED_EXECUTION_SOURCE

This tool cannot backfill, switch writers, or authorize a deployment. It expects
the candidate schema (including model_request_id). Scope labels are supplied by
the operator's verified inventory; database ownership comes from the job join.
"""

import argparse
from collections import Counter
from decimal import Decimal, localcontext
import hashlib
import json

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from src.models.sql.benchmark import (
    BenchmarkInvocationStatus, BenchmarkJob,
)
from src.models.sql.database import SessionLocal
from .benchmark_migration import (
    TERMINAL_CELLS, TERMINAL_JOBS, iter_benchmark_migration_rows,
    migration_fingerprint_line, plan_benchmark_cost_migration,
)


def _add_exact(left: Decimal, right: Decimal) -> Decimal:
    # RecordedCharge guarantees finite nonnegative operands. Size precision from
    # their decimal positions so the process-wide Decimal context cannot round
    # a tiny recorded charge out of a larger accumulated sum.
    with localcontext() as context:
        context.prec = max(left.adjusted(), right.adjusted()) - min(
            int(left.as_tuple().exponent), int(right.as_tuple().exponent),
        ) + 2
        return left + right


def audit_benchmark_costs(session: Session, *, deployment_id: str, source_namespace: str) -> dict:
    """Inventory a stable snapshot without leaking row IDs or curator identities."""
    if not deployment_id.strip() or not source_namespace.strip():
        raise ValueError("Audit requires verified deployment and execution source scope")
    if session.scalar(text("SHOW transaction_read_only")) != "on" or session.scalar(
        text("SHOW transaction_isolation")
    ) != "repeatable read":
        raise ValueError("Audit requires a repeatable-read, read-only transaction")
    active_jobs = session.scalar(select(func.count()).select_from(BenchmarkJob).where(
        BenchmarkJob.status.not_in(TERMINAL_JOBS),
    ))
    counters = Counter()
    identities = Counter()
    usage_statuses = Counter()
    amounts: dict[tuple[str, str], Decimal] = {}
    charge_counts = Counter()
    digest = hashlib.sha256()
    for invocation, owner, job_status, cell_status in iter_benchmark_migration_rows(session):
        counters["scanned_invocations"] += 1
        if job_status not in TERMINAL_JOBS or cell_status not in TERMINAL_CELLS or invocation.status == BenchmarkInvocationStatus.RUNNING:
            counters["nonterminal_invocations"] += 1
            continue
        try:
            entry = plan_benchmark_cost_migration(
                invocation, deployment_id=deployment_id,
                source_namespace=source_namespace, owner_subject=owner,
            )
        except ValueError:
            counters["invalid_invocations"] += 1
            continue
        counters["planned_invocations"] += 1
        identities[entry.identity_basis] += 1
        usage_statuses[entry.usage.status] += 1
        digest.update(migration_fingerprint_line(entry))
        if entry.charge is None:
            counters["unknown_charge_invocations"] += 1
        else:
            key = (entry.charge.unit, entry.charge.source)
            amounts[key] = _add_exact(amounts.get(key, Decimal(0)), entry.charge.amount)
            charge_counts[key] += 1
    return {
        "schema_version": 1, "dry_run": True, "write_side_enabled": False,
        "deployment_id": deployment_id, "source_namespace": source_namespace,
        "active_jobs": active_jobs,
        **{key: counters[key] for key in (
            "scanned_invocations", "planned_invocations", "nonterminal_invocations",
            "invalid_invocations", "unknown_charge_invocations",
        )},
        "identity_basis_counts": dict(sorted(identities.items())),
        "usage_status_counts": dict(sorted(usage_statuses.items())),
        "planned_facts_sha256": digest.hexdigest(),
        "recorded_charge_totals": [
            {"unit": unit, "source": source, "amount": str(amount), "invocations": charge_counts[(unit, source)]}
            for (unit, source), amount in sorted(amounts.items())
        ],
        "cutover_blockers": {
            "active_jobs": active_jobs, "nonterminal_invocations": counters["nonterminal_invocations"],
            "invalid_invocations": counters["invalid_invocations"],
            "inconsistent_usage": usage_statuses["inconsistent"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--source-namespace", required=True)
    args = parser.parse_args()
    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        report = audit_benchmark_costs(
            session, deployment_id=args.deployment_id, source_namespace=args.source_namespace,
        )
        session.rollback()
    print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
