"""Database integrity for extraction execution receipts; no invented history."""
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import DBAPIError

from src.lib.agent_studio.execution_revision_service import current_execution_receipt
from .test_flow_revision_persistence import flow_db, migrate  # noqa: F401
from .test_agent_execution_revision_persistence import execution_db  # noqa: F401
from .test_generic_profile_persistence import profile_db  # noqa: F401


@pytest.fixture
def extraction_db(flow_db):  # noqa: F811
    db, agent, _, _ = flow_db
    migrate(db)
    db.execute(sa.text("CREATE TABLE extraction_results(id uuid PRIMARY KEY, agent_key text NOT NULL)"))
    legacy_id = uuid4()
    db.execute(sa.text("INSERT INTO extraction_results VALUES (:id, :key)"), {"id": legacy_id, "key": agent.agent_key})
    path = Path(__file__).resolve().parents[3] / "alembic/versions/j7e8f9a0b1c2_extraction_revision_receipts.py"
    spec = importlib.util.spec_from_file_location("extraction_pin_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(db.connection())):
        module.upgrade()
    receipt = current_execution_receipt(db, agent.agent_key, 1, active_group_ids=[])
    return db, receipt, legacy_id


def insert_result(db, receipt, *, pin=None, payload=None, key=None):
    result_id = uuid4()
    db.execute(sa.text("""INSERT INTO extraction_results(id, agent_key, agent_revision_id, execution_receipt)
        VALUES (:id, :key, :pin, CAST(:receipt AS jsonb))"""), {
        "id": result_id, "key": key or receipt.agent_key,
        "pin": pin or receipt.agent_revision_id,
        "receipt": json.dumps(payload if payload is not None else receipt.model_dump(mode="json")),
    })
    return result_id


def test_exact_receipt_retained_without_fabricating_legacy_history(extraction_db):
    db, receipt, legacy_id = extraction_db
    assert db.execute(sa.text("SELECT agent_revision_id, execution_receipt FROM extraction_results WHERE id=:id"),
                      {"id": legacy_id}).one() == (None, None)
    result_id = insert_result(db, receipt)
    row = db.execute(sa.text("SELECT agent_revision_id, execution_receipt FROM extraction_results WHERE id=:id"),
                     {"id": result_id}).one()
    assert row == (receipt.agent_revision_id, receipt.model_dump(mode="json"))


def test_system_result_orm_receipt_binding_uses_sql_null(extraction_db):
    from src.lib.curation_workspace.models import CurationExtractionResultRecord
    db, _, _ = extraction_db
    result_id = uuid4()
    statement = sa.text("""INSERT INTO extraction_results(id, agent_key, agent_revision_id, execution_receipt)
        VALUES (:id, 'system_extractor', NULL, :receipt)""").bindparams(
        sa.bindparam("receipt", type_=CurationExtractionResultRecord.__table__.c.execution_receipt.type))
    db.execute(statement, {"id": result_id, "receipt": None})
    assert db.scalar(sa.text("SELECT execution_receipt IS NULL FROM extraction_results WHERE id=:id"), {"id": result_id})


@pytest.mark.parametrize("change", ["fingerprint", "profile", "agent", "dangling"])
def test_database_rejects_invalid_result_receipts(extraction_db, change):
    db, receipt, _ = extraction_db
    payload = receipt.model_dump(mode="json")
    if change == "fingerprint":
        payload["fingerprint"] = "sha256:" + "b" * 64
    if change == "profile":
        payload["output_contract"]["output_mode"] = "unprofiled_generic"
    with pytest.raises(DBAPIError), db.begin_nested():
        insert_result(db, receipt, payload=payload,
                      key="ca_other" if change == "agent" else None,
                      pin=uuid4() if change == "dangling" else None)


def test_result_receipt_cannot_be_erased_or_retargeted(extraction_db):
    db, receipt, _ = extraction_db
    result_id = insert_result(db, receipt)
    with pytest.raises(DBAPIError), db.begin_nested():
        db.execute(sa.text("UPDATE extraction_results SET agent_revision_id=NULL, execution_receipt=NULL WHERE id=:id"),
                   {"id": result_id})
    with pytest.raises(DBAPIError), db.begin_nested():
        db.execute(sa.text("UPDATE extraction_results SET agent_revision_id=:pin WHERE id=:id"),
                   {"id": result_id, "pin": uuid4()})
