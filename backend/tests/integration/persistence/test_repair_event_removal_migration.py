"""Row-level coverage for removing obsolete repair-history event kinds."""

from __future__ import annotations

import os
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

BACKEND_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = (
    BACKEND_ROOT
    / "alembic"
    / "versions"
    / "n1o2p3q4r5s6_remove_repair_history_event_kinds.py"
)


def _load_migration_module():
    spec = spec_from_file_location(
        "repair_event_removal_row_upgrade_test", MIGRATION_PATH
    )
    assert spec is not None
    assert spec.loader is not None

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migration_connection():
    engine = create_engine(os.environ["DATABASE_URL"])
    schema_name = f"repair_event_removal_{uuid4().hex}"

    try:
        with engine.connect() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
            connection.commit()
            try:
                connection.execute(text(f'SET search_path TO "{schema_name}"'))
                connection.commit()
                yield connection
            finally:
                connection.rollback()
                connection.execute(
                    text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
                )
                connection.commit()
    finally:
        engine.dispose()


def test_upgrade_normalizes_legacy_rows(migration_connection):
    module = _load_migration_module()
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
