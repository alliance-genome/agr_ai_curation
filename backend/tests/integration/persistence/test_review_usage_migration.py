"""Retiring token policy preserves the complete prior records and late usage."""
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.lib.validation_history import store as h
from src.lib.validation_review import admission as a
from src.models.sql.pdf_document import PDFDocument
from tests.pdf_document_test_support import ensure_test_pdf_owner
from tests.integration.persistence.test_validation_review import artifact, binding, episode


def test_old_policy_archived_without_resetting_history(historical_migration_database):
    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    command.upgrade(config, "d2e6f7a8b9c0")
    doc, owner = uuid4(), "migration-" + uuid4().hex
    with h.SessionLocal.begin() as db:
        uid = ensure_test_pdf_owner(db, auth_sub=owner)
        db.add(PDFDocument(id=doc, user_id=uid, filename="history.pdf",
            file_path=f"{doc}/history.pdf", file_hash=doc.hex * 2,
            file_size=1024, page_count=1, status="processed"))
    scope = h.HistoryScope(owner, str(doc), uuid4().hex)
    aid = artifact(scope)
    ep = episode(scope, aid, binding(scope, aid))
    sid = a.create_scope(scope, operation_id=uuid4().hex, kind="discovered_document_run", targets=0, episodes=0)
    a.attach_episode(scope, artifact_id=aid, episode_id=ep, scope_id=sid)
    rid = a.reserve(scope, ep, operation_id="provider:old-unknown",
        charges={"initial_tokens": 0}, request={"usage_metered": True})
    a.reconcile(scope, ep, rid, actual=None)
    # Seed the exact retired policy shape on the previous schema. Its immutable
    # triggers are disabled only here, to construct a historical fixture.
    with h.SessionLocal.begin() as db:
        db.execute(text("ALTER TABLE validation_review_scopes DISABLE TRIGGER protect_validation_review_scopes"))
        db.execute(text("ALTER TABLE validation_review_episodes DISABLE TRIGGER protect_validation_review_episodes"))
        db.execute(text("""UPDATE validation_review_scopes SET contract =
            jsonb_set(jsonb_set(contract, '{policy}', (contract->'policy') ||
              '{"reviewer_tokens": 1000000,"tokens_per_target": 150000,"tokens_per_extra": 150000}'::jsonb),
              '{allowances}', (contract->'allowances') || '{"initial_tokens": 150000,"reviewer_tokens": 1000000,"reconsideration_tokens": 300000}'::jsonb)
            WHERE id=:id"""), {"id": sid})
        db.execute(text("""UPDATE validation_review_episodes SET policy = policy ||
            '{"reviewer_tokens": 1000000,"tokens_per_target": 150000,"tokens_per_extra": 150000}'::jsonb,
            allowances = allowances || '{"initial_tokens": 150000,"reviewer_tokens": 1000000,"reconsideration_tokens": 300000}'::jsonb
            WHERE id=:id"""), {"id": ep})
        db.execute(text("ALTER TABLE validation_review_reservations DISABLE TRIGGER protect_validation_review_reservations"))
        db.execute(text("""UPDATE validation_review_reservations SET charges='{"initial_tokens": 136331}'::jsonb,
            request='{"input_tokens": 8331,"output_ceiling": 128000}'::jsonb WHERE id=:id"""), {"id": rid})
        db.execute(text("ALTER TABLE validation_review_reservations ENABLE TRIGGER protect_validation_review_reservations"))
        for table, identity in [("validation_review_scopes", sid), ("validation_review_episodes", ep)]:
            db.execute(text(f"UPDATE {table} SET charged=charged || '{{\"initial_tokens\": 136331}}'::jsonb WHERE id=:id"), {"id": identity})
        db.execute(text("ALTER TABLE validation_review_coverage DISABLE TRIGGER protect_validation_review_coverage"))
        db.execute(text("""UPDATE validation_review_coverage SET allowances=allowances ||
            '{"initial_tokens": 150000,"reviewer_tokens": 1000000,"reconsideration_tokens": 0}'::jsonb WHERE artifact_id=:id"""), {"id": aid})
        db.execute(text("ALTER TABLE validation_review_coverage ENABLE TRIGGER protect_validation_review_coverage"))
        coverage = db.execute(text("SELECT to_jsonb(r) FROM validation_review_coverage r WHERE artifact_id=:id"), {"id": aid}).scalar_one()
        db.execute(text("ALTER TABLE validation_review_scopes ENABLE TRIGGER protect_validation_review_scopes"))
        db.execute(text("ALTER TABLE validation_review_episodes ENABLE TRIGGER protect_validation_review_episodes"))
        originals = {table: db.execute(text(f"SELECT to_jsonb(r) FROM {table} r WHERE id=:id"), {"id": identity}).scalar_one()
            for table, identity in [("validation_review_scopes", sid), ("validation_review_episodes", ep)]}
        reservations = db.execute(text("SELECT to_jsonb(r) FROM validation_review_reservations r WHERE id=:id"), {"id": rid}).scalar_one()
    command.upgrade(config, "head")
    with h.SessionLocal() as db:
        assert db.execute(text("SELECT to_jsonb(r) FROM validation_review_coverage r WHERE artifact_id=:id"), {"id": aid}).scalar_one() == coverage
        for table, original in originals.items():
            archived = db.execute(text("SELECT original_record FROM validation_review_policy_transitions WHERE source_table=:table AND source_id=:id"),
                {"table": table, "id": original["id"]}).scalar_one()
            assert archived == original
            current = db.execute(text(f"SELECT to_jsonb(r) FROM {table} r WHERE id=:id"), {"id": original["id"]}).scalar_one()
            for key in original.keys() - {"contract", "policy", "allowances"}:
                assert current[key] == original[key]
        assert db.execute(text("SELECT to_jsonb(r) FROM validation_review_reservations r WHERE id=:id"), {"id": rid}).scalar_one() == reservations
    assert a.status(scope, ep)["budgets"][-1]["provider_usage"]["uncertain_calls"] == 1
    a.reconcile(scope, ep, rid, actual={"initial_tokens": 17123})
    with h.SessionLocal() as db:
        for table, identity in [("validation_review_scopes", sid), ("validation_review_episodes", ep)]:
            assert db.execute(text(f"SELECT charged->>'initial_tokens' FROM {table} WHERE id=:id"), {"id": identity}).scalar_one() == "17123"
    status = a.status(scope, ep)
    for budget in status["budgets"]:
        assert "initial_tokens" not in budget["remaining"]
        assert budget["provider_usage"]["reported_tokens"]["initial_tokens"] == 17123
    with pytest.raises(DBAPIError, match="Original review policies must be retained"):
        with h.SessionLocal.begin() as db:
            db.execute(text("DELETE FROM validation_review_policy_transitions"))
    with pytest.raises(DBAPIError):
        with h.SessionLocal.begin() as db:
            db.execute(text("UPDATE validation_review_episodes SET policy='{}'::jsonb WHERE id=:id"), {"id": ep})
