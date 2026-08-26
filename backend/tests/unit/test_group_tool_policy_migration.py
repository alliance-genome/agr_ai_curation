"""Contract checks for the group-tool policy and batch snapshot migration."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from src.models.sql.agent import Agent
from src.models.sql.batch import Batch


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "c925d1e2f3a4_add_group_tool_policy_and_batch_groups.py"
)


def test_group_tool_policy_migration_matches_models(monkeypatch):
    spec = spec_from_file_location("group_tool_policy_migration", MIGRATION_PATH)
    assert spec and spec.loader
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)

    added = []
    dropped = []
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda table_name, column: added.append((table_name, column)),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_column",
        lambda table_name, column_name: dropped.append((table_name, column_name)),
    )

    migration.upgrade()
    migration.downgrade()

    assert [(table, column.name) for table, column in added] == [
        ("agents", "group_tool_policy"),
        ("batches", "active_group_ids"),
    ]
    assert all(column.nullable is False for _, column in added)
    assert dropped == [
        ("batches", "active_group_ids"),
        ("agents", "group_tool_policy"),
    ]
    assert Agent.__table__.c.group_tool_policy.nullable is False
    assert Batch.__table__.c.active_group_ids.nullable is False
