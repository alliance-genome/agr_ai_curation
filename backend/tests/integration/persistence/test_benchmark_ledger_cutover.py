"""Real DDL must prove ledger parity before removing historical source facts."""

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from src.lib.cost_ledger.benchmark_backfill import backfill_benchmark_costs
from src.lib.cost_ledger.benchmark_migration import iter_benchmark_migration_rows
from src.models.sql.database import SessionLocal, engine
from src.models.sql.cost_ledger import CostFactRevision
from src.lib.benchmarks.persistence import BenchmarkRepository
from src.models.sql.benchmark import BenchmarkCell
from tests.integration.persistence.test_benchmark_repository import (
    BACKEND_ROOT, cost_backfill_case as cost_backfill_case,
    _create_job,
    historical_cost_columns as historical_cost_columns, migrated_database as migrated_database,
)


@pytest.mark.parametrize("condition", ["verified", "unbound", "wrong_scope", "changed_facts", "active_job"])
def test_retirement_is_guarded_and_transactional(cost_backfill_case, monkeypatch, condition):
    scope, audit, job_id = cost_backfill_case
    if condition != "unbound":
        with SessionLocal() as db:
            backfill_benchmark_costs(db, **scope, expected_planned_facts_sha256=audit["planned_facts_sha256"])
            db.commit()
    if condition == "wrong_scope":
        monkeypatch.setenv("COST_LEDGER_BENCHMARK_SOURCE_NAMESPACE", "wrong")
    migration = ScriptDirectory.from_config(Config(str(BACKEND_ROOT / "alembic.ini"))).get_revision("d64c05e0925d").module
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            if condition == "active_job":
                with Session(bind=connection) as db:
                    _create_job(db, owner="queued-cutover-blocker", cells=1)
                    db.flush()
            before = connection.execute(select(
                BenchmarkCell.id, BenchmarkCell.result_artifact, BenchmarkCell.result_digest,
            ).where(BenchmarkCell.job_id == job_id).order_by(BenchmarkCell.id)).all()
            if condition == "changed_facts":
                # Additional evidence after the reviewed baseline must not
                # silently pass exact pre-cutover parity verification.
                first = connection.execute(select(CostFactRevision).where(
                    CostFactRevision.deployment_id == scope["deployment_id"],
                )).mappings().first()
                connection.execute(text("INSERT INTO cost_fact_revisions "
                    "(deployment_id,attempt_id,revision,source_system,source_namespace,source_id,input_tokens) "
                    "VALUES (:deployment_id,:attempt_id,2,:source_system,:source_namespace,:source_id,1)"), dict(first))
            with Operations.context(MigrationContext.configure(connection)):
                if condition == "verified":
                    migration.upgrade()
                else:
                    with pytest.raises((ValueError, LookupError)):
                        with connection.begin_nested():
                            migration.upgrade()
            columns = {column["name"] for column in inspect(connection).get_columns("benchmark_invocations")}
            assert ("billed_amount" not in columns) == (condition == "verified")
            assert connection.execute(select(
                BenchmarkCell.id, BenchmarkCell.result_artifact, BenchmarkCell.result_digest,
            ).where(BenchmarkCell.job_id == job_id).order_by(BenchmarkCell.id)).all() == before
            if condition == "verified":
                with Session(bind=connection) as db:
                    artifact = BenchmarkRepository(db).get_result_artifact(
                        cell_id=before[0].id, job_id=job_id, owner_subject="backfill-owner",
                    )
                    assert artifact.version == "1" and artifact.content == before[0].result_artifact
                    with pytest.raises(ValueError, match="pre-cutover schema"):
                        list(iter_benchmark_migration_rows(db))
        finally:
            transaction.rollback()
