"""Unit tests for the curator field-patch constraint migration."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import sys
import types
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    REPO_ROOT
    / "alembic"
    / "versions"
    / "k0l1m2n3o4p5_allow_curator_field_patch_events.py"
)
REPAIR_EVENT_REMOVAL_MIGRATION_PATH = (
    REPO_ROOT
    / "alembic"
    / "versions"
    / "n1o2p3q4r5s6_remove_repair_history_event_kinds.py"
)


class RecordingOp:
    def __init__(self) -> None:
        self.created_constraints: list[tuple[str, str, str]] = []
        self.dropped_constraints: list[tuple[str, str, str | None]] = []
        self.executed_sql: list[str] = []
        self.operations: list[tuple[str, str]] = []

    def create_check_constraint(self, name, table_name, condition):
        self.created_constraints.append((name, table_name, condition))
        self.operations.append(("create_check_constraint", name))

    def drop_constraint(self, name, table_name, type_=None):
        self.dropped_constraints.append((name, table_name, type_))
        self.operations.append(("drop_constraint", name))

    def execute(self, sql):
        self.executed_sql.append(str(sql))
        self.operations.append(("execute", str(sql)))


def _load_migration_module(monkeypatch, *, module_name: str, path: Path):
    dummy_alembic = types.ModuleType("alembic")
    dummy_alembic.op = object()
    monkeypatch.setitem(sys.modules, "alembic", dummy_alembic)

    spec = spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migration_connection():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL is required for row-level migration coverage")

    engine = create_engine(database_url)
    schema_name = f"repair_event_removal_{uuid4().hex}"
    connection = engine.connect()

    try:
        try:
            connection.execute(text(f"CREATE SCHEMA {schema_name}"))
            connection.commit()
        except OperationalError as exc:
            pytest.skip(f"test database is not reachable: {exc.__class__.__name__}")

        connection.execute(text(f"SET search_path TO {schema_name}"))
        connection.commit()
        yield connection
    finally:
        connection.rollback()
        try:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
            connection.commit()
        finally:
            connection.close()
            engine.dispose()


def test_upgrade_refreshes_history_and_action_log_check_constraints(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="curator_field_patch_constraints_upgrade_test",
        path=MIGRATION_PATH,
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.upgrade()

    assert op_recorder.dropped_constraints == [
        (
            "ck_domain_envelope_history_event_type",
            "domain_envelope_history",
            "check",
        ),
        ("ck_curation_action_log_action_type", "curation_action_log", "check"),
    ]
    assert op_recorder.created_constraints == [
        (
            "ck_domain_envelope_history_event_type",
            "domain_envelope_history",
            module._check_sql("event_type", module.CURRENT_HISTORY_EVENT_KINDS),
        ),
        (
            "ck_curation_action_log_action_type",
            "curation_action_log",
            module._check_sql("action_type", module.CURRENT_ACTION_TYPES),
        ),
    ]
    assert "curator_field_patch_accepted" in module.CURRENT_HISTORY_EVENT_KINDS
    assert "curator_field_patch_rejected" in module.CURRENT_HISTORY_EVENT_KINDS
    assert "repair_requested" in module.CURRENT_HISTORY_EVENT_KINDS
    assert "repair_patch_accepted" in module.CURRENT_HISTORY_EVENT_KINDS
    assert "repair_patch_rejected" in module.CURRENT_HISTORY_EVENT_KINDS
    assert "repair_final_classified" in module.CURRENT_HISTORY_EVENT_KINDS
    assert "envelope_field_patched" in module.CURRENT_ACTION_TYPES


def test_downgrade_restores_previous_check_constraints(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="curator_field_patch_constraints_downgrade_test",
        path=MIGRATION_PATH,
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.downgrade()

    assert op_recorder.dropped_constraints == [
        ("ck_curation_action_log_action_type", "curation_action_log", "check"),
        (
            "ck_domain_envelope_history_event_type",
            "domain_envelope_history",
            "check",
        ),
    ]
    assert op_recorder.created_constraints == [
        (
            "ck_curation_action_log_action_type",
            "curation_action_log",
            module._check_sql("action_type", module.PREVIOUS_ACTION_TYPES),
        ),
        (
            "ck_domain_envelope_history_event_type",
            "domain_envelope_history",
            module._check_sql("event_type", module.PREVIOUS_HISTORY_EVENT_KINDS),
        ),
    ]
    assert "curator_field_patch_accepted" not in module.PREVIOUS_HISTORY_EVENT_KINDS
    assert "curator_field_patch_rejected" not in module.PREVIOUS_HISTORY_EVENT_KINDS
    assert "envelope_field_patched" not in module.PREVIOUS_ACTION_TYPES


def test_repair_event_removal_upgrade_refreshes_history_constraint(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="repair_event_removal_upgrade_test",
        path=REPAIR_EVENT_REMOVAL_MIGRATION_PATH,
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.upgrade()

    assert module.down_revision == "m1n2o3p4q5r6"
    assert [operation[0] for operation in op_recorder.operations[:2]] == [
        "execute",
        "execute",
    ]
    assert "UPDATE domain_envelope_history" in op_recorder.executed_sql[0]
    assert "DELETE FROM domain_envelope_history" in op_recorder.executed_sql[1]
    assert op_recorder.dropped_constraints == [
        (
            "ck_domain_envelope_history_event_type",
            "domain_envelope_history",
            "check",
        ),
    ]
    assert op_recorder.created_constraints == [
        (
            "ck_domain_envelope_history_event_type",
            "domain_envelope_history",
            module._check_sql(module.CURRENT_HISTORY_EVENT_KINDS),
        ),
    ]
    assert "curator_field_patch_accepted" in module.CURRENT_HISTORY_EVENT_KINDS
    assert "curator_field_patch_rejected" in module.CURRENT_HISTORY_EVENT_KINDS
    assert "validation_rerun_requested" in module.CURRENT_HISTORY_EVENT_KINDS
    assert "repair_requested" not in module.CURRENT_HISTORY_EVENT_KINDS
    assert "repair_patch_accepted" not in module.CURRENT_HISTORY_EVENT_KINDS
    assert "repair_patch_rejected" not in module.CURRENT_HISTORY_EVENT_KINDS
    assert "repair_final_classified" not in module.CURRENT_HISTORY_EVENT_KINDS


def test_repair_event_removal_upgrade_normalizes_legacy_rows(
    monkeypatch,
    migration_connection,
):
    module = _load_migration_module(
        monkeypatch,
        module_name="repair_event_removal_row_upgrade_test",
        path=REPAIR_EVENT_REMOVAL_MIGRATION_PATH,
    )
    migration_connection.execute(
        text(
            f"""
            CREATE TABLE domain_envelope_history (
                envelope_id text NOT NULL,
                event_id text NOT NULL,
                envelope_revision integer NOT NULL,
                event_index integer NOT NULL,
                event_type text NOT NULL,
                occurred_at timestamptz NOT NULL DEFAULT now(),
                actor_type text NOT NULL DEFAULT 'system',
                actor_id text,
                object_id text,
                field_path text,
                model_field_ref_json jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                event_json jsonb NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (envelope_id, event_id),
                CONSTRAINT ck_domain_envelope_history_event_type
                    CHECK ({module._check_sql(module.PREVIOUS_HISTORY_EVENT_KINDS)}),
                CONSTRAINT ck_domain_envelope_history_actor_type
                    CHECK (actor_type IN ('system', 'agent', 'human', 'tool')),
                CONSTRAINT ck_domain_envelope_history_revision
                    CHECK (envelope_revision >= 1),
                CONSTRAINT ck_domain_envelope_history_index
                    CHECK (event_index >= 0)
            )
            """
        )
    )
    for event_index, event_type in enumerate(
        (
            "repair_requested",
            "repair_patch_accepted",
            "repair_patch_rejected",
            "repair_final_classified",
            "field_updated",
        )
    ):
        migration_connection.execute(
            text(
                """
                INSERT INTO domain_envelope_history (
                    envelope_id,
                    event_id,
                    envelope_revision,
                    event_index,
                    event_type,
                    occurred_at,
                    actor_type,
                    event_json
                )
                VALUES (
                    'env-legacy',
                    :event_id,
                    1,
                    :event_index,
                    :event_type,
                    now(),
                    'system',
                    jsonb_build_object(
                        'event_id',
                        :event_id,
                        'event_type',
                        :event_type,
                        'details',
                        jsonb_build_object('source', 'legacy-repair')
                    )
                )
                """
            ),
            {
                "event_id": f"event-{event_index}",
                "event_index": event_index,
                "event_type": event_type,
            },
        )

    module.op = Operations(MigrationContext.configure(migration_connection))
    module.upgrade()

    rows = (
        migration_connection.execute(
            text(
                """
                SELECT event_id, event_type, event_json
                FROM domain_envelope_history
                ORDER BY event_index
                """
            )
        )
        .mappings()
        .all()
    )

    assert [row["event_type"] for row in rows] == [
        "curator_field_patch_accepted",
        "curator_field_patch_rejected",
        "field_updated",
    ]
    assert rows[0]["event_json"]["event_type"] == "curator_field_patch_accepted"
    assert rows[0]["event_json"]["details"]["legacy_repair_event_type"] == (
        "repair_patch_accepted"
    )
    assert rows[1]["event_json"]["event_type"] == "curator_field_patch_rejected"
    assert rows[1]["event_json"]["details"]["legacy_repair_event_type"] == (
        "repair_patch_rejected"
    )
    assert rows[2]["event_json"]["event_type"] == "field_updated"


def test_repair_event_removal_downgrade_restores_previous_constraint(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="repair_event_removal_downgrade_test",
        path=REPAIR_EVENT_REMOVAL_MIGRATION_PATH,
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.downgrade()

    assert op_recorder.dropped_constraints == [
        (
            "ck_domain_envelope_history_event_type",
            "domain_envelope_history",
            "check",
        ),
    ]
    assert op_recorder.created_constraints == [
        (
            "ck_domain_envelope_history_event_type",
            "domain_envelope_history",
            module._check_sql(module.PREVIOUS_HISTORY_EVENT_KINDS),
        ),
    ]
    assert "repair_requested" in module.PREVIOUS_HISTORY_EVENT_KINDS
    assert "repair_patch_accepted" in module.PREVIOUS_HISTORY_EVENT_KINDS
    assert "repair_patch_rejected" in module.PREVIOUS_HISTORY_EVENT_KINDS
    assert "repair_final_classified" in module.PREVIOUS_HISTORY_EVENT_KINDS
