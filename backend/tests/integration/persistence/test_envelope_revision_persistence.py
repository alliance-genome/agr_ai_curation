"""Real database linkage of envelope receipts to immutable execution and result."""
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import DBAPIError

from .test_extraction_revision_persistence import extraction_db, insert_result  # noqa: F401
from .test_flow_revision_persistence import flow_db  # noqa: F401
from .test_agent_execution_revision_persistence import execution_db  # noqa: F401
from .test_generic_profile_persistence import profile_db  # noqa: F401


@pytest.fixture
def envelope_db(extraction_db):  # noqa: F811
    db, receipt, legacy_id = extraction_db
    db.execute(sa.text("""CREATE TABLE domain_envelopes(envelope_id text PRIMARY KEY,
        source_extraction_result_id text, envelope_json jsonb NOT NULL)"""))
    path = Path(__file__).resolve().parents[3] / "alembic/versions/k8f9a0b1c2d3_envelope_revision_receipts.py"
    spec = importlib.util.spec_from_file_location("envelope_pin_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(db.connection())):
        module.upgrade()
    result_id = insert_result(db, receipt)
    return db, receipt, result_id, legacy_id


def insert_envelope(db, receipt, result_id, *, metadata_receipt=None):
    envelope_id = str(uuid4())
    payload = receipt.model_dump(mode="json")
    db.execute(sa.text("""INSERT INTO domain_envelopes
        (envelope_id, source_extraction_result_id, envelope_json, agent_revision_id, execution_receipt)
        VALUES (:id, :source, CAST(:envelope AS jsonb), :pin, CAST(:receipt AS jsonb))"""), {
        "id": envelope_id, "source": str(result_id), "pin": receipt.agent_revision_id,
        "receipt": json.dumps(payload), "envelope": json.dumps({"metadata": {
            "execution_receipt": payload if metadata_receipt is None else metadata_receipt}}),
    })
    return envelope_id


def test_envelope_keeps_normalized_execution_reference(envelope_db):
    db, receipt, result_id, _ = envelope_db
    envelope_id = insert_envelope(db, receipt, result_id)
    row = db.execute(sa.text("SELECT agent_revision_id, execution_receipt FROM domain_envelopes WHERE envelope_id=:id"),
                     {"id": envelope_id}).one()
    assert row == (receipt.agent_revision_id, receipt.model_dump(mode="json"))


@pytest.mark.parametrize("change", ["metadata", "source", "erase", "dangling"])
def test_envelope_identity_tampering_fails(envelope_db, change):
    db, receipt, result_id, legacy_id = envelope_db
    envelope_id = insert_envelope(db, receipt, result_id)
    with pytest.raises(DBAPIError), db.begin_nested():
        if change == "metadata":
            insert_envelope(db, receipt, result_id, metadata_receipt={"wrong": True})
        elif change == "source":
            insert_envelope(db, receipt, legacy_id)
        elif change == "erase":
            db.execute(sa.text("""UPDATE domain_envelopes SET agent_revision_id=NULL,
                execution_receipt=NULL, envelope_json='{}'::jsonb WHERE envelope_id=:id"""), {"id": envelope_id})
        else:
            db.execute(sa.text("UPDATE domain_envelopes SET agent_revision_id=:pin WHERE envelope_id=:id"),
                       {"id": envelope_id, "pin": uuid4()})
