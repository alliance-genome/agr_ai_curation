"""The import record: one row per imported version, never changed or deleted."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.models.sql import BenchmarkFlowImport, CurationFlow
from .test_generic_profile_persistence import profile_db  # noqa: F401


def run_flow_import_migration(db) -> None:
    path = (Path(__file__).resolve().parents[3]
            / "alembic/versions/a7f1c2e3d4b5_add_benchmark_flow_imports.py")
    spec = importlib.util.spec_from_file_location("flow_import_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(db.connection())):
        module.upgrade()


@pytest.fixture
def import_db(profile_db):  # noqa: F811
    db, _ = profile_db
    CurationFlow.__table__.create(db.connection())
    run_flow_import_migration(db)
    flow = CurationFlow(user_id=2, name="Copy", flow_definition={"nodes": []})
    db.add(flow)
    db.flush()
    return db, flow


def _row(flow, version=1):
    return BenchmarkFlowImport(
        user_id=2, export_issuer="https://ai-curation-dev.example.org", source_flow_id=SOURCE,
        source_version="sha256:" + "1" * 64, flow_id=flow.id, version=version,
        flow_revision="sha256:" + "2" * 64, pins=[], bundle_sha256="sha256:" + "3" * 64,
        source_app_version="0.10.1",
    )


SOURCE = uuid4()


def test_versions_are_unique_per_curator_issuer_and_source_flow(import_db):
    db, flow = import_db
    db.add(_row(flow))
    db.flush()
    with db.begin_nested():
        db.add(_row(flow))
        with pytest.raises(IntegrityError):
            db.flush()
    db.add(_row(flow, version=2))
    db.flush()


def test_rows_are_append_only(import_db):
    db, flow = import_db
    db.add(_row(flow))
    db.flush()
    for statement in ("UPDATE benchmark_flow_imports SET version = 5",
                      "DELETE FROM benchmark_flow_imports"):
        with pytest.raises(DBAPIError, match="append-only"):
            with db.begin_nested():
                db.execute(sa.text(statement))
