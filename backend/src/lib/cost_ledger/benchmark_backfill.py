"""Explicit offline benchmark accounting backfill; dry-run unless --apply.

Not a deployment or writer-cutover command. Keep maintenance active throughout
the coordinated migration/consumer switch. Source columns and immutable artifacts
are retained here; no live process invokes this runner or dual-writes costs.
"""

import argparse
import hashlib
import json
import re
import sys

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from src.lib.openai_agents.config import (
    get_cost_ledger_benchmark_source_namespace, get_cost_ledger_deployment_id,
    get_cost_migration_lock_timeout_ms,
)
from src.models.sql.benchmark import BenchmarkJob
from src.models.sql.database import SessionLocal
from .benchmark_audit import audit_benchmark_costs
from .benchmark_migration import (
    TERMINAL_CELLS, TERMINAL_JOBS, iter_benchmark_migration_rows,
    migration_fingerprint_line, plan_benchmark_cost_migration, verify_benchmark_cost_receipt,
)
from .persistence import read_cost_facts, record_cost_facts


class BenchmarkBackfillError(ValueError):
    """A content-free migration precondition or parity failure."""


def backfill_benchmark_costs(
    session: Session, *, deployment_id: str, source_namespace: str,
    expected_planned_facts_sha256: str,
) -> dict:
    """Stage verified backfill atomically; caller owns final commit/rollback.

Requires a dedicated fresh session. Savepoint rollback removes all additions if
any row, binding, receipt or final fingerprint fails, even when caller catches
the exception and commits. Table locks freeze source and destination writes;
ordinary readers remain allowed. Maintenance must prevent new work after commit.
"""
    if session.in_transaction() or session.new or session.dirty or session.deleted:
        raise BenchmarkBackfillError("fresh_session_required")
    if (not deployment_id or not source_namespace
            or deployment_id != get_cost_ledger_deployment_id()
            or source_namespace != get_cost_ledger_benchmark_source_namespace()):
        raise BenchmarkBackfillError("verified_scope_configuration_required")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_planned_facts_sha256):
        raise BenchmarkBackfillError("audit_fingerprint_required")
    digest = hashlib.sha256()
    verified = 0
    with session.begin_nested():
        # A pre-lock REPEATABLE READ snapshot could miss a writer that commits
        # while we wait for locks. READ COMMITTED observes it after acquisition.
        if session.scalar(text("SHOW transaction_isolation")) != "read committed":
            raise BenchmarkBackfillError("read_committed_required")
        session.execute(text("SELECT set_config('lock_timeout', :timeout, true)"), {
            "timeout": f"{get_cost_migration_lock_timeout_ms()}ms",
        })
        session.execute(text(
            "LOCK TABLE benchmark_jobs, benchmark_cells, benchmark_invocations, "
            "cost_attempts, cost_source_references, cost_fact_revisions "
            "IN SHARE ROW EXCLUSIVE MODE"
        ))
        active = session.scalar(select(func.count()).select_from(BenchmarkJob).where(
            BenchmarkJob.status.not_in(TERMINAL_JOBS),
        ))
        if active:
            raise BenchmarkBackfillError("active_benchmark_jobs")
        for invocation, owner, job_status, cell_status in iter_benchmark_migration_rows(session):
            if job_status not in TERMINAL_JOBS or cell_status not in TERMINAL_CELLS:
                raise BenchmarkBackfillError("nonterminal_benchmark_source")
            entry = plan_benchmark_cost_migration(
                invocation, deployment_id=deployment_id,
                source_namespace=source_namespace, owner_subject=owner,
            )
            if entry.usage.issues:
                raise BenchmarkBackfillError("inconsistent_source_usage")
            record_cost_facts(
                session, deployment_id=entry.deployment_id, owner_subject=entry.owner_subject,
                attempt_id=entry.attempt_id, source_system="benchmark",
                source_namespace=entry.source_namespace, source_id=str(entry.invocation_id),
                usage=entry.usage, charge=entry.charge,
            )
            facts = read_cost_facts(
                session, deployment_id=entry.deployment_id,
                owner_subject=entry.owner_subject, attempt_id=entry.attempt_id,
            )
            verify_benchmark_cost_receipt(entry, facts)
            digest.update(migration_fingerprint_line(entry))
            verified += 1
        actual = digest.hexdigest()
        if actual != expected_planned_facts_sha256:
            raise BenchmarkBackfillError("source_fingerprint_changed")
    return {
        "schema_version": 1, "committed": False,
        "deployment_id": deployment_id, "source_namespace": source_namespace,
        "verified_invocations": verified, "planned_facts_sha256": actual,
        "source_columns_removed": False, "writer_cutover_complete": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--source-namespace", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-planned-facts-sha256")
    args = parser.parse_args()
    if args.apply and not args.expected_planned_facts_sha256:
        parser.error("--apply requires the reviewed audit's --expected-planned-facts-sha256")
    try:
        with SessionLocal() as session:
            if args.apply:
                report = backfill_benchmark_costs(
                    session, deployment_id=args.deployment_id, source_namespace=args.source_namespace,
                    expected_planned_facts_sha256=args.expected_planned_facts_sha256,
                )
                session.commit()
                report["committed"] = True
            else:
                session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                report = audit_benchmark_costs(session, deployment_id=args.deployment_id, source_namespace=args.source_namespace)
                session.rollback()
    except Exception as exc:
        # SQL exceptions can contain credentials, SQL parameters or source data.
        code = str(exc) if isinstance(exc, BenchmarkBackfillError) else type(exc).__name__
        print(json.dumps({"error": "benchmark_backfill_failed", "code": code}), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
