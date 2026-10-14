"""Standalone validator originals commit exactly, once, within curator ownership."""
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from src.lib.domain_packs.validator_result_storage import persist_validator_result, read_validator_result, list_validator_results
from src.models.sql.database import SessionLocal
from src.models.sql.validator_result import ValidatorResultRecord


def test_exact_jsonb_original_idempotency_and_owner_session_authorization():
    user = "validator-storage-test-" + uuid4().hex
    session = uuid4().hex
    invocation = uuid4().hex
    payload = {"status": "unresolved", "explanation": "β 😀 / quotation \"exact\" " * 1000,
               "lookup_attempts": [{"query": {str(i): list(range(30)) for i in range(60)}}],
               "unknown_future_field": {"nested": [None, False, 1.25]}}
    kwargs = dict(user_id=user, session_id=session, invocation_id=invocation, agent_key="fixture_validator")
    try:
        reference = persist_validator_result(payload, **kwargs)
        assert persist_validator_result(payload, **kwargs) == reference
        result_id = reference.split(":", 1)[1]
        assert read_validator_result(result_id, user_id=user, session_id=session)["original"]["result"] == payload
        assert read_validator_result(result_id, user_id="different-owner", session_id=None) is None
        assert read_validator_result(result_id, user_id=user, session_id="different-session") is None
        assert read_validator_result(result_id, user_id=user, session_id=None)["result_ref"] == reference
        assert list_validator_results(user_id=user, session_id=session)[0]["decision_status"] == "unresolved"
        assert list_validator_results(user_id="different-owner", session_id=session) == []
        with pytest.raises(ValueError, match="different original"):
            persist_validator_result({**payload, "explanation": "replacement"}, **kwargs)
        # Rejected reuse cannot overwrite the committed original.
        assert read_validator_result(result_id, user_id=user, session_id=session)["original"]["result"] == payload
        with SessionLocal() as db:
            assert len(db.execute(select(ValidatorResultRecord.id).where(ValidatorResultRecord.user_id == user)).all()) == 1
    finally:
        with SessionLocal() as db:
            db.execute(delete(ValidatorResultRecord).where(ValidatorResultRecord.user_id == user))
            db.commit()
