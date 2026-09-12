"""Opt-in, real PostgreSQL upgrades from both original release histories.

Requires an explicitly isolated loopback PostgreSQL control database and
read-only source exports of both original parents. Never builds a baseline by
stamping or create_all. Each test owns and drops only its newly created DB.
"""

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

BACKEND = Path(__file__).resolve().parents[2]
CONTROL_URL = os.environ.get("MIGRATION_CONVERGENCE_TEST_DATABASE_URL")
PARENT_HEADS = {"main": "7c9e2a4b6d80", "production": "p3e4f5a6b7c8"}
MERGED_HEAD = "f54e2c6f6848"
pytestmark = pytest.mark.skipif(
    not CONTROL_URL, reason="isolated convergence database not configured"
)


def _command(backend, url, *args):
    env = {
        **os.environ,
        "DATABASE_URL": url,
        "PYTHONPATH": str(backend),
        "PYTHONDONTWRITEBYTECODE": "1",
        "AWS_EC2_METADATA_DISABLED": "true",
    }
    result = subprocess.run(
        [sys.executable, *args],
        cwd=backend,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _seed_main():
    # Reuse the normal repository fixture and state transitions. No worker or
    # provider runs: the owning helper records deterministic synthetic results.
    from src.lib.curation_workspace import benchmark_snapshots as sender
    from src.lib.curation_workspace.models import DomainEnvelopeModel
    from src.models.sql.database import SessionLocal
    from src.models.sql.pdf_document import PDFDocument
    from tests.integration.persistence.test_benchmark_repository import (
        _create_job,
        _run_to_terminal,
    )
    from tests.pdf_document_test_support import ensure_test_pdf_owner

    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        job = _create_job(db, owner="service:convergence-fixture", cells=1)
        _run_to_terminal(db, job.id)
        owner = ensure_test_pdf_owner(db, auth_sub="convergence-fixture")
        document_id, session_id, snapshot_id = uuid4(), uuid4(), uuid4()
        envelope_id = str(uuid4())
        db.add(
            PDFDocument(
                id=document_id,
                user_id=owner,
                filename="synthetic.pdf",
                title="Synthetic",
                file_path="synthetic/not-a-real-paper.pdf",
                file_hash="a" * 64,
                file_size=1,
                page_count=1,
                upload_timestamp=now,
                last_accessed=now,
                status="processed",
            )
        )
        db.flush()
        db.add(
            sender.CurationReviewSession(
                id=session_id,
                document_id=document_id,
                adapter_key="example",
                prepared_at=now,
                created_by_id="convergence-fixture",
            )
        )
        db.flush()
        db.add(
            DomainEnvelopeModel(
                envelope_id=envelope_id,
                project_key="example",
                domain_pack_key="example",
                adapter_key="example",
                source_payload_hash="a" * 64,
                status="extracted",
                document_id=document_id,
                session_id=session_id,
                envelope_json={"synthetic": True},
            )
        )
        db.flush()
        db.add(
            sender.CurationBenchmarkSnapshot(
                id=snapshot_id,
                session_id=session_id,
                envelope_id=envelope_id,
                envelope_revision=1,
                envelope_digest="sha256:" + "a" * 64,
                bundle_json='{"immutable":"synthetic-convergence"}',
                created_by_id="convergence-fixture",
                exported_at=now,
            )
        )
        db.flush()
        db.add(
            sender.CurationBenchmarkHandoffAttempt(
                id=uuid4(),
                snapshot_id=snapshot_id,
                destination_id="synthetic",
                replay_key="sha256:" + "b" * 64,
                idempotency_key="sha256:" + "c" * 64,
                status="unknown",
                failure_code="delivery_transport_error",
                sender_version="1",
                sender_issuer="https://identity.example",
                sender_subject="convergence-fixture",
            )
        )
        db.commit()


def _seed_production():
    from src.lib.agent_studio.generic_profile_service import create_profile
    from src.models.sql.agent import Agent
    from src.models.sql.curation_flow import CurationFlow
    from src.models.sql.database import SessionLocal
    from tests.integration.persistence.test_agent_execution_revision_persistence import (
        insert_revision,
    )
    from tests.pdf_document_test_support import ensure_test_pdf_owner

    with SessionLocal() as db:
        owner = ensure_test_pdf_owner(db, auth_sub="convergence-fixture")
        assert owner == 1  # The original-parent fixture started empty.
        _, profile_revision = create_profile(
            db,
            owner,
            {
                "name": "Synthetic retained profile",
                "semantic_class": "example",
                "fields": [],
            },
        )
        agent = Agent(
            id=uuid4(),
            agent_key="ca_convergence_fixture",
            user_id=owner,
            name="Synthetic retained agent",
            instructions="Synthetic only",
            model_id="synthetic-model",
            model_temperature=0.0,
            visibility="private",
        )
        db.add(agent)
        db.flush()
        revision_id = insert_revision(
            db,
            agent.id,
            mode="profile_bound_generic",
            state="structured_extraction",
            profile=profile_revision,
        )
        agent.execution_revision_id = revision_id
        db.flush()
        receipt = db.scalar(
            sa.text("SELECT flow_execution_receipt(:id)"), {"id": revision_id}
        )
        db.add(
            CurationFlow(
                user_id=owner,
                name="Synthetic pinned flow",
                flow_definition={
                    "nodes": [
                        {
                            "id": "extract",
                            "type": "agent",
                            "data": {
                                "agent_id": agent.agent_key,
                                "agent_revision_id": str(revision_id),
                                "execution_receipt": receipt,
                            },
                        }
                    ],
                    "edges": [],
                },
            )
        )
        db.commit()


def _inventory(connection, tables):
    # Only known fixture table names, never input-generated SQL identifiers.
    return {
        table: connection.execute(
            sa.text(
                f"SELECT row_to_json(t)::text FROM {table} t ORDER BY row_to_json(t)::text"
            )
        )
        .scalars()
        .all()
        for table in tables
    }


@pytest.mark.parametrize("parent", ["main", "production", "fresh"])
def test_original_parent_to_combined_head_preserves_data(parent):
    assert CONTROL_URL
    control = sa.engine.make_url(CONTROL_URL)
    assert control.get_backend_name() == "postgresql"
    assert control.host in {"127.0.0.1", "localhost"}
    assert (
        control.database == "migration_control" and control.username == "migration_test"
    )
    database = "convergence_test_" + uuid4().hex
    admin = sa.create_engine(control, isolation_level="AUTOCOMMIT")
    url = control.set(database=database).render_as_string(hide_password=False)
    engine = sa.create_engine(url)
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
    try:
        retained_tables = []
        if parent != "fresh":
            parent_root = Path(os.environ[f"MIGRATION_{parent.upper()}_PARENT_ROOT"])
            _command(parent_root / "backend", url, "-m", "alembic", "upgrade", "head")
            with engine.connect() as connection:
                assert (
                    connection.scalar(
                        sa.text("SELECT version_num FROM alembic_version")
                    )
                    == PARENT_HEADS[parent]
                )
            _command(parent_root / "backend", url, str(Path(__file__)), "seed", parent)
            retained_tables = (
                [
                    "benchmark_jobs",
                    "benchmark_cells",
                    "benchmark_invocations",
                    "benchmark_events",
                    "benchmark_input_snapshots",
                    "curation_benchmark_snapshots",
                    "curation_benchmark_handoff_attempts",
                ]
                if parent == "main"
                else [
                    "generic_extraction_profiles",
                    "generic_extraction_profile_revisions",
                    "agent_execution_revisions",
                    "curation_flow_agent_revisions",
                ]
            )
        with engine.connect() as connection:
            before = _inventory(connection, retained_tables)
            assert all(before.values())
            flows_before = (
                connection.execute(
                    sa.text(
                        "SELECT id, flow_definition FROM curation_flows ORDER BY id"
                    )
                ).all()
                if parent == "production"
                else []
            )
        _command(BACKEND, url, "-m", "alembic", "upgrade", "head")
        _command(BACKEND, url, "-m", "alembic", "upgrade", "head")
        with engine.connect() as connection:
            assert (
                connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
                == MERGED_HEAD
            )
            assert _inventory(connection, retained_tables) == before
            for table in [
                "benchmark_jobs",
                "benchmark_input_snapshots",
                "curation_benchmark_snapshots",
                "generic_extraction_profiles",
                "agent_execution_revisions",
                "profile_validator_capabilities",
                "curation_flow_agent_revisions",
                "curation_session_agent_revisions",
                "flow_shortcut_preferences",
            ]:
                assert sa.inspect(connection).has_table(table)
            assert {"visibility", "project_id", "shared_at"} <= {
                column["name"]
                for column in sa.inspect(connection).get_columns("curation_flows")
            }
            if parent == "production":
                assert (
                    connection.execute(
                        sa.text(
                            "SELECT id, flow_definition FROM curation_flows ORDER BY id"
                        )
                    ).all()
                    == flows_before
                )
            if parent != "fresh":
                statement = (
                    "UPDATE benchmark_invocations SET response_digest = response_digest"
                    if parent == "main"
                    else "UPDATE agent_execution_revisions SET fingerprint = fingerprint"
                )
                with pytest.raises(sa.exc.DBAPIError):
                    with connection.begin_nested():
                        connection.execute(sa.text(statement))
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE "{database}"')
        admin.dispose()


if __name__ == "__main__":
    assert sys.argv[1:] in (["seed", "main"], ["seed", "production"])
    {"main": _seed_main, "production": _seed_production}[sys.argv[2]]()
