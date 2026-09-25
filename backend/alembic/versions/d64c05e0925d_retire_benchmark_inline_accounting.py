"""Retire inline benchmark accounting only after exact ledger parity.

Revision ID: d64c05e0925d
Revises: c53c05e0925c
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session

revision = "d64c05e0925d"
down_revision = "c53c05e0925c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from src.lib.cost_ledger.benchmark_migration import (
        TERMINAL_CELLS, TERMINAL_JOBS, iter_benchmark_migration_rows,
        plan_benchmark_cost_migration, verify_benchmark_cost_receipt,
    )
    from src.lib.cost_ledger.persistence import read_cost_facts
    from src.lib.openai_agents.config import (
        get_cost_ledger_deployment_id, get_cost_ledger_benchmark_source_namespace,
        get_cost_migration_lock_timeout_ms,
    )
    from src.models.sql.cost_ledger import CostSourceReference
    connection = op.get_bind()
    connection.execute(sa.text("SELECT set_config('lock_timeout', :timeout, true)"),
                       {"timeout": f"{get_cost_migration_lock_timeout_ms()}ms"})
    connection.execute(sa.text(
        "LOCK TABLE benchmark_jobs, benchmark_cells, benchmark_invocations, "
        "cost_attempts, cost_source_references, cost_fact_revisions IN ACCESS EXCLUSIVE MODE"
    ))
    with Session(bind=connection) as session:
        active = session.scalar(sa.text(
            "SELECT count(*) FROM benchmark_jobs WHERE status NOT IN "
            "('completed', 'completed_with_failures', 'cancelled', 'failed')"
        ))
        if active:
            raise ValueError("Stop benchmark admission and drain jobs before ledger cutover")
        for invocation, owner, job_status, cell_status in iter_benchmark_migration_rows(session):
            if job_status not in TERMINAL_JOBS or cell_status not in TERMINAL_CELLS:
                raise ValueError("Nonterminal benchmark source prevents ledger cutover")
            entry = plan_benchmark_cost_migration(
                invocation, deployment_id=get_cost_ledger_deployment_id(),
                source_namespace=get_cost_ledger_benchmark_source_namespace(), owner_subject=owner,
            )
            bound = session.scalar(sa.select(CostSourceReference.attempt_id).where(
                CostSourceReference.deployment_id == entry.deployment_id,
                CostSourceReference.source_system == "benchmark",
                CostSourceReference.source_namespace == entry.source_namespace,
                CostSourceReference.source_id == str(entry.invocation_id),
            ))
            if bound != entry.attempt_id or entry.usage.issues:
                raise ValueError("Verified benchmark backfill required before ledger cutover")
            verify_benchmark_cost_receipt(entry, read_cost_facts(
                session, deployment_id=entry.deployment_id, owner_subject=owner,
                attempt_id=entry.attempt_id,
            ))
    op.drop_constraint("ck_benchmark_invocations_billed_cost", "benchmark_invocations", type_="check")
    op.drop_constraint("ck_benchmark_invocations_telemetry_values", "benchmark_invocations", type_="check")
    for name in ("input_tokens", "output_tokens", "total_tokens", "billed_amount", "billed_unit", "billed_source"):
        op.drop_column("benchmark_invocations", name)
    op.create_check_constraint("ck_benchmark_invocations_telemetry_values", "benchmark_invocations",
                               "sequence >= 1 AND (routing_attempt IS NULL OR routing_attempt >= 0) "
                               "AND (latency_ms IS NULL OR latency_ms >= 0)")


def downgrade() -> None:
    raise RuntimeError("Forward-only accounting cutover; restore a verified pre-cutover backup instead")
