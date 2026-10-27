"""Real PostgreSQL audit, rollback and idempotence for generated flow repair."""
from copy import deepcopy
from pathlib import Path
from uuid import UUID, uuid4
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from src.models.sql.database import engine
from src.lib.flows.unbound_metadata_repair import migrate_metadata_groups
from tests.unit.lib.flows.test_unbound_metadata_repair import fixture


@pytest.fixture(scope="module", autouse=True)
def migrated():
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")


def test_repair_audits_exact_changes_and_preserves_unknown_and_inactive_flows():
    ids = [uuid4() for _ in range(3)]
    _, original = fixture()
    unknown = deepcopy(original)
    unknown["nodes"][0]["data"]["validation_groups"][0]["label"] = "Custom unmatched selection"
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            insert = sa.text("""INSERT INTO curation_flows
                (id,user_id,name,flow_definition,is_active,updated_at)
                VALUES (:id,991234567,:name,:definition,:active,'2020-01-01')
            """).bindparams(sa.bindparam("definition", type_=JSONB))
            for flow_id, definition, active in zip(ids, [original, original, unknown], [True, False, True]):
                connection.execute(insert, {"id": flow_id, "name": f"metadata repair {flow_id}",
                                            "definition": definition, "active": active})
            snapshot = dict(connection.execute(sa.text(
                "SELECT id,flow_definition FROM curation_flows WHERE id=ANY(:ids)"), {"ids": ids}).all())
            dry = migrate_metadata_groups(connection)
            assert sum(len(r["removed"]) for r in dry["flows"] if r["flow_id"] in map(str, ids)) == 2
            assert dict(connection.execute(sa.text(
                "SELECT id,flow_definition FROM curation_flows WHERE id=ANY(:ids)"), {"ids": ids}).all()) == snapshot
            savepoint = connection.begin_nested()
            migrate_metadata_groups(connection, apply=True)
            savepoint.rollback()
            assert dict(connection.execute(sa.text(
                "SELECT id,flow_definition FROM curation_flows WHERE id=ANY(:ids)"), {"ids": ids}).all()) == snapshot
            report = migrate_metadata_groups(connection, apply=True)
            assert connection.scalar(sa.text("""SELECT EXISTS(SELECT 1 FROM pg_locks
                WHERE pid=pg_backend_pid() AND relation='curation_flows'::regclass
                AND mode='ShareRowExclusiveLock' AND granted)"""))
            from src.lib.benchmarks.saved_flows import flow_summary
            for item in report["flows"]:
                if item["flow_id"] not in map(str, ids):
                    continue
                identity = dict(id=UUID(item["flow_id"]), name=f"metadata repair {item['flow_id']}", description=None)
                original_definition = snapshot[next(key for key in snapshot if str(key) == item["flow_id"])]
                expected_revision = flow_summary(SimpleNamespace(**identity, flow_definition=original_definition)).revision
                assert item["before_revision"] == expected_revision
                current_definition = connection.scalar(sa.text("SELECT flow_definition FROM curation_flows WHERE id=:id"), {"id": item["flow_id"]})
                assert item["after_revision"] == flow_summary(SimpleNamespace(**identity, flow_definition=current_definition)).revision
            assert len([r for r in report["flows"] if r["applied"] and r["flow_id"] in map(str, ids)]) == 2
            rows = connection.execute(sa.text("SELECT id,flow_definition,is_active,updated_at FROM curation_flows WHERE id=ANY(:ids)"), {"ids": ids}).mappings().all()
            expected = deepcopy(original)
            expected["nodes"][0]["data"]["validation_groups"] = []
            for row in rows:
                assert row["flow_definition"] == (unknown if row["id"] == ids[2] else expected)
                assert row["is_active"] == (row["id"] != ids[1])
                assert (row["updated_at"].year == 2020) == (row["id"] == ids[2])
            audit_sql = sa.text("""SELECT old_data,new_data,application_name FROM audit_log
                WHERE table_name='curation_flows' AND operation='UPDATE' AND row_id=ANY(:ids)""")
            audit = connection.execute(audit_sql, {"ids": list(map(str, ids))}).mappings().all()
            assert len(audit) == 2
            for row in audit:
                assert row["old_data"]["flow_definition"] == original
                assert row["new_data"]["flow_definition"] == expected
                assert row["application_name"] == "migration:unbound-metadata-groups"
            second = migrate_metadata_groups(connection, apply=True)
            assert not any(r["removed"] for r in second["flows"] if r["flow_id"] in map(str, ids))
            assert len(connection.execute(audit_sql, {"ids": list(map(str, ids))}).all()) == 2
        finally:
            transaction.rollback()


def test_apply_refuses_disabled_audit_trigger():
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(sa.text("ALTER TABLE curation_flows DISABLE TRIGGER audit_curation_flows_update"))
            with pytest.raises(RuntimeError, match="audit trigger"):
                migrate_metadata_groups(connection, apply=True)
        finally:
            transaction.rollback()


def test_stale_definition_aborts_and_rolls_back(monkeypatch):
    """Exercise the actual SQL compare-and-swap guard with a changed definition."""
    from src.lib.flows import unbound_metadata_repair as repair_module

    flow_id = uuid4()
    _, original = fixture()
    changed = deepcopy(original)
    changed["concurrent_edit"] = True
    real_repair = repair_module.repair_metadata_groups
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(sa.text("""INSERT INTO curation_flows
                (id,user_id,name,flow_definition,is_active)
                VALUES (:id,991234567,:name,:definition,true)
            """).bindparams(sa.bindparam("definition", type_=JSONB)),
                {"id": flow_id, "name": f"stale metadata repair {flow_id}", "definition": original})
            audit_sql = sa.text("SELECT count(*) FROM audit_log WHERE table_name='curation_flows' AND row_id=:id")
            audit_before = connection.execute(audit_sql, {"id": str(flow_id)}).scalar_one()
            injected = False

            def change_after_snapshot(definition):
                nonlocal injected
                result = real_repair(definition)
                if definition == original and not injected:
                    # Same-transaction injection models a stale snapshot; row locks
                    # normally prevent a separate writer reaching this point.
                    injected = True
                    connection.execute(sa.text("UPDATE curation_flows SET flow_definition=:definition WHERE id=:id")
                        .bindparams(sa.bindparam("definition", type_=JSONB)),
                        {"id": flow_id, "definition": changed})
                return result

            monkeypatch.setattr(repair_module, "repair_metadata_groups", change_after_snapshot)
            with pytest.raises(RuntimeError, match="Saved flow changed"):
                with connection.begin_nested():
                    migrate_metadata_groups(connection, apply=True)
            assert injected
            assert connection.execute(sa.text("SELECT flow_definition FROM curation_flows WHERE id=:id"),
                                      {"id": flow_id}).scalar_one() == original
            assert connection.execute(audit_sql, {"id": str(flow_id)}).scalar_one() == audit_before
        finally:
            transaction.rollback()
