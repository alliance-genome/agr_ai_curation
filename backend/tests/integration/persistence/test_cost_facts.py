"""Durable sparse facts, history, provenance and transaction safety."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import DBAPIError

from src.lib.cost_ledger.facts import CostFactConflict, RecordedCharge, TokenUsage
from src.lib.cost_ledger.identity import bind_cost_source
from src.lib.cost_ledger.persistence import read_cost_facts, record_cost_facts
from src.models.sql.cost_ledger import CostFactRevision, CostSourceReference
from src.models.sql.database import SessionLocal


@pytest.fixture(scope="module", autouse=True)
def migrate():
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")


@pytest.fixture
def binding():
    return dict(
        deployment_id=uuid4().hex, owner_subject="fixture-owner", attempt_id=uuid4(),
        source_system="benchmark", source_namespace="execution-target", source_id=uuid4().hex,
    )


def read(db, binding, **kwargs):
    return read_cost_facts(db, **{key: binding[key] for key in (
        "deployment_id", "owner_subject", "attempt_id",
    )}, **kwargs)


def rows(db, binding):
    return list(db.scalars(select(CostFactRevision).where(
        CostFactRevision.deployment_id == binding["deployment_id"],
        CostFactRevision.attempt_id == binding["attempt_id"],
    ).order_by(CostFactRevision.revision)))


def test_sparse_history_is_exact_and_replay_does_not_duplicate(binding):
    charge = RecordedCharge(Decimal("0.000000000000000000123"), "credits", "provider")
    with SessionLocal() as db:
        first = record_cost_facts(db, **binding, usage=TokenUsage(input_tokens=100, output_tokens=40))
        second = record_cost_facts(db, **{**binding, "source_id": "late"}, usage=TokenUsage(
            input_tokens=100, total_tokens=140, cache_read_tokens=30,
        ), charge=charge)
        assert (first.revision, second.revision) == (1, 2)
        assert record_cost_facts(db, **binding, usage=first.usage).revision == 2
        one, two = rows(db, binding)
        assert one.input_tokens == 100 and one.billed_amount is None
        assert two.input_tokens is None and two.output_tokens is None
        assert two.cache_read_tokens == 30 and two.billed_amount == charge.amount
        db.commit()
    with SessionLocal() as db:
        assert read(db, binding, revision=1) == first
        assert read(db, binding) == second
        assert read(db, binding, revision=2) == second
        with pytest.raises(LookupError):
            read(db, binding, revision=3)
        with pytest.raises(LookupError):
            read(db, {**binding, "owner_subject": "another-owner"})
        with pytest.raises(LookupError):
            read(db, {**binding, "deployment_id": "another-deployment"})


def test_unknown_and_zero_charge_are_different(binding):
    with SessionLocal() as db:
        missing = record_cost_facts(db, **binding, usage=TokenUsage())
        assert missing.revision is None and missing.charge is None
        assert missing.usage.status == "missing"
        zero = record_cost_facts(db, **binding, usage=TokenUsage(), charge=RecordedCharge(
            Decimal("0"), "USD", "provider",
        ))
        assert zero.revision == 1 and zero.charge.amount == 0
        assert zero.usage.status == "missing"
        assert len(rows(db, binding)) == 1


def test_caught_conflict_cannot_commit_source_or_partial_facts(binding):
    with SessionLocal() as db:
        first = record_cost_facts(db, **binding, usage=TokenUsage(input_tokens=10))
        with pytest.raises(CostFactConflict):
            record_cost_facts(db, **{**binding, "source_id": "rejected"}, usage=TokenUsage(
                input_tokens=11, output_tokens=20,
            ))
        db.commit()
    with SessionLocal() as db:
        assert read(db, binding) == first
        assert db.scalar(select(func.count()).select_from(CostSourceReference).where(
            CostSourceReference.deployment_id == binding["deployment_id"],
        )) == 1


def test_outer_rollback_removes_new_attempt_source_and_facts(binding):
    with SessionLocal() as db:
        record_cost_facts(db, **binding, usage=TokenUsage(input_tokens=10))
        db.rollback()
    with SessionLocal() as db:
        with pytest.raises(LookupError):
            read(db, binding)
        assert not rows(db, binding)


def test_inconsistent_late_subsets_remain_visible(binding):
    with SessionLocal() as db:
        record_cost_facts(db, **binding, usage=TokenUsage(input_tokens=10))
        result = record_cost_facts(db, **binding, usage=TokenUsage(cache_read_tokens=11))
        assert result.usage.status == "inconsistent"
        assert read(db, binding).usage.cache_read_tokens == 11
        assert read(db, binding, revision=1).usage.status == "partial"


@pytest.mark.parametrize("mode", ["complementary", "conflicting", "replay"])
def test_concurrent_different_sources_serialize_without_lost_additions(binding, mode):
    with SessionLocal() as db:
        bind_cost_source(db, **binding)
        db.commit()
    barrier = Barrier(2)

    def deliver(index):
        with SessionLocal() as db:
            # Fail a locking regression promptly, rather than hang the suite.
            from sqlalchemy import text
            db.execute(text("SET LOCAL statement_timeout = '10s'"))
            barrier.wait(timeout=10)
            if mode == "conflicting":
                usage = TokenUsage(input_tokens=10 + index)
            elif mode == "replay":
                usage = TokenUsage(input_tokens=10)
            else:
                usage = TokenUsage(input_tokens=10) if index == 0 else TokenUsage(output_tokens=20)
            try:
                result = record_cost_facts(db, **{**binding, "source_id": str(index)}, usage=usage)
            except CostFactConflict:
                db.commit()
                return None
            db.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(deliver, [0, 1]))
    assert results.count(None) == (1 if mode == "conflicting" else 0)
    with SessionLocal() as db:
        final = read(db, binding)
        assert final.revision == (2 if mode == "complementary" else 1)
        if mode == "complementary":
            assert final.usage == TokenUsage(input_tokens=10, output_tokens=20)


@pytest.mark.parametrize("operation", ["update", "delete", "repeat", "skip", "wrong-source", "wrong-attempt", "invalid-charge", "negative", "empty"])
def test_database_guards_cannot_be_bypassed_by_raw_writes(binding, operation):
    with SessionLocal() as db:
        record_cost_facts(db, **binding, usage=TokenUsage(input_tokens=10))
        key = {name: binding[name] for name in (
            "deployment_id", "attempt_id", "source_system", "source_namespace", "source_id",
        )}
        where = (CostFactRevision.deployment_id == binding["deployment_id"])
        with pytest.raises(DBAPIError):
            with db.begin_nested():
                if operation == "update":
                    db.execute(update(CostFactRevision).where(where).values(input_tokens=11))
                elif operation == "delete":
                    db.execute(delete(CostFactRevision).where(where))
                elif operation == "repeat":
                    db.execute(insert(CostFactRevision).values(**key, revision=2, input_tokens=10))
                elif operation == "skip":
                    db.execute(insert(CostFactRevision).values(**key, revision=3, output_tokens=20))
                elif operation == "wrong-source":
                    db.execute(insert(CostFactRevision).values(**{**key, "source_id": "unbound"}, revision=2, output_tokens=20))
                elif operation == "wrong-attempt":
                    other_id = uuid4()
                    bind_cost_source(db, **{**binding, "attempt_id": other_id, "source_id": "other"})
                    db.execute(insert(CostFactRevision).values(**{**key, "attempt_id": other_id}, revision=1, output_tokens=20))
                elif operation == "negative":
                    db.execute(insert(CostFactRevision).values(**key, revision=2, output_tokens=-1))
                elif operation == "empty":
                    db.execute(insert(CostFactRevision).values(**key, revision=2))
                else:
                    db.execute(insert(CostFactRevision).values(**key, revision=2, billed_amount=Decimal("NaN"), billed_unit="USD", billed_source="provider"))
        assert len(rows(db, binding)) == 1


def test_charge_conflict_cannot_commit_new_usage(binding):
    with SessionLocal() as db:
        first = record_cost_facts(db, **binding, usage=TokenUsage(), charge=RecordedCharge(
            Decimal("1"), "credits", "provider",
        ))
        with pytest.raises(CostFactConflict):
            record_cost_facts(db, **binding, usage=TokenUsage(input_tokens=10), charge=RecordedCharge(
                Decimal("1"), "USD", "provider",
            ))
        db.commit()
    with SessionLocal() as db:
        assert read(db, binding) == first


def test_fact_migration_roundtrip_in_transactional_schema():
    from importlib.util import module_from_spec, spec_from_file_location
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text
    from src.models.sql.database import engine

    versions = Path(__file__).resolve().parents[3] / "alembic/versions"
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            schema = "cost_revision_" + uuid4().hex
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            migrations = []
            for filename in ("b42c05e0925b_add_cost_identity_registry.py", "c53c05e0925c_add_cost_fact_revisions.py"):
                spec = spec_from_file_location(filename, versions / filename)
                assert spec is not None and spec.loader is not None
                migration = module_from_spec(spec)
                spec.loader.exec_module(migration)
                migration.op = Operations(MigrationContext.configure(connection))
                migration.upgrade()
                migrations.append(migration)
            columns = {column["name"] for column in inspect(connection).get_columns("cost_fact_revisions", schema=schema)}
            assert columns == set(CostFactRevision.__table__.columns.keys())
            migrations[-1].downgrade()
            assert not inspect(connection).has_table("cost_fact_revisions", schema=schema)
            assert inspect(connection).has_table("cost_attempts", schema=schema)
            migrations[-1].upgrade()
            assert inspect(connection).has_table("cost_fact_revisions", schema=schema)
        finally:
            transaction.rollback()


@pytest.mark.parametrize("measured", [False, True])
def test_benchmark_backfill_receipt_preserves_facts_and_replays_once(binding, measured):
    from src.lib.cost_ledger.benchmark_migration import plan_benchmark_cost_migration, verify_benchmark_cost_receipt
    from src.models.sql.benchmark import BenchmarkInvocation, BenchmarkInvocationStatus

    invocation = BenchmarkInvocation(
        id=uuid4(), model_request_id=uuid4() if measured else None,
        status=BenchmarkInvocationStatus.FAILED, input_tokens=10, output_tokens=None,
        total_tokens=None, billed_amount=Decimal("0.000000000000123"),
        billed_unit="credits", billed_source="provider",
    )
    entry = plan_benchmark_cost_migration(
        invocation, deployment_id=binding["deployment_id"],
        source_namespace=binding["source_namespace"], owner_subject=binding["owner_subject"],
    )
    scoped_binding = {**binding, "attempt_id": entry.attempt_id, "source_id": str(entry.invocation_id)}
    with SessionLocal() as db:
        for _ in range(2):
            record_cost_facts(db, **scoped_binding, usage=entry.usage, charge=entry.charge)
            verify_benchmark_cost_receipt(entry, read(db, scoped_binding))
        assert len(rows(db, scoped_binding)) == 1
        if measured:
            # Only a verified measured identity permits this cross-source join.
            record_cost_facts(db, **{**scoped_binding, "source_system": "telemetry"}, usage=entry.usage)
            assert len(rows(db, scoped_binding)) == 1
        db.commit()
    with SessionLocal() as db:
        verify_benchmark_cost_receipt(entry, read(db, scoped_binding))
        assert read(db, scoped_binding).charge.unit == "credits"


def test_pinned_consumer_reference_does_not_follow_late_evidence(binding):
    from src.lib.cost_ledger.projections import resolve_cost_reference
    from src.schemas.cost_ledger import CostLedgerReference, CostFactsProjection

    empty_ref = CostLedgerReference(
        schema_version=1, deployment_id=binding["deployment_id"],
        attempt_id=binding["attempt_id"], fact_revision=0,
    )
    with SessionLocal() as db:
        bind_cost_source(db, **binding)
        empty = resolve_cost_reference(db, reference=empty_ref, owner_subject=binding["owner_subject"])
        assert empty.usage_status == "missing" and empty.recorded_charge is None
        record_cost_facts(db, **binding, usage=TokenUsage(input_tokens=10))
        first_ref = empty_ref.model_copy(update={"fact_revision": 1})
        first = resolve_cost_reference(db, reference=first_ref, owner_subject=binding["owner_subject"])
        record_cost_facts(db, **binding, usage=TokenUsage(output_tokens=20), charge=RecordedCharge(
            Decimal("0.000000000000123"), "credits", "provider",
        ))
        db.commit()
    with SessionLocal() as db:
        assert resolve_cost_reference(db, reference=empty_ref, owner_subject=binding["owner_subject"]) == empty
        assert resolve_cost_reference(db, reference=first_ref, owner_subject=binding["owner_subject"]) == first
        current_ref = empty_ref.model_copy(update={"fact_revision": 2})
        latest = resolve_cost_reference(db, reference=current_ref, owner_subject=binding["owner_subject"])
        assert latest.usage.output_tokens == 20
        assert CostFactsProjection.model_validate_json(latest.model_dump_json()) == latest
        for ref, owner in (
            (empty_ref, "wrong-owner"),
            (empty_ref.model_copy(update={"deployment_id": "wrong-deployment"}), binding["owner_subject"]),
            (empty_ref.model_copy(update={"attempt_id": uuid4()}), binding["owner_subject"]),
            (empty_ref.model_copy(update={"fact_revision": 3}), binding["owner_subject"]),
        ):
            with pytest.raises(LookupError):
                resolve_cost_reference(db, reference=ref, owner_subject=owner)
