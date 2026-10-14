"""Persist/read standalone originals with an immutable invocation and owner scope."""
from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from src.models.sql.database import SessionLocal
from src.models.sql.validator_result import ValidatorResultRecord
from .validator_result_records import validator_result_snapshot


def persist_validator_result(
    payload: Mapping[str, Any], *, user_id: str, session_id: str,
    invocation_id: str, agent_key: str,
) -> str:
    """Commit the exact original before advertising its reference to a model."""
    if not all(isinstance(value, str) and value.strip() for value in (user_id, session_id, invocation_id, agent_key)):
        raise ValueError("A saved validator result requires authenticated ownership and invocation identity")
    digest_ref, snapshot = validator_result_snapshot(payload)
    digest = digest_ref.split(":", 1)[1]
    with SessionLocal() as db:
        db.execute(insert(ValidatorResultRecord).values(
            id=uuid4(), user_id=user_id, origin_session_id=session_id,
            invocation_id=invocation_id, agent_key=agent_key,
            payload_sha256=digest, payload_json=snapshot,
        ).on_conflict_do_nothing(constraint="uq_validator_result_invocation"))
        record = db.execute(select(ValidatorResultRecord).where(
            ValidatorResultRecord.user_id == user_id,
            ValidatorResultRecord.origin_session_id == session_id,
            ValidatorResultRecord.invocation_id == invocation_id,
        )).scalar_one()
        if record.payload_sha256 != digest:
            raise ValueError("Validator invocation identity was reused for a different original")
        ref = f"validator-result:{record.id}"
        db.commit()
        return ref


def read_validator_result(result_id: str, *, user_id: str, session_id: str | None) -> dict[str, Any] | None:
    conditions = [ValidatorResultRecord.id == UUID(result_id), ValidatorResultRecord.user_id == user_id]
    if session_id is not None:
        conditions.append(ValidatorResultRecord.origin_session_id == session_id)
    with SessionLocal() as db:
        record = db.execute(select(ValidatorResultRecord).where(*conditions)).scalar_one_or_none()
        if record is None:
            return None
        return {"result_ref": f"validator-result:{record.id}", "agent_key": record.agent_key,
                "original": record.payload_json, "original_sha256": record.payload_sha256}


def list_validator_results(*, user_id: str, session_id: str) -> list[dict[str, Any]]:
    """Read metadata only; complete originals are fetched only for an exact read."""
    with SessionLocal() as db:
        rows = db.execute(select(
            ValidatorResultRecord.id, ValidatorResultRecord.agent_key, ValidatorResultRecord.created_at,
            ValidatorResultRecord.payload_json["result"]["status"].astext,
        ).where(ValidatorResultRecord.user_id == user_id,
                ValidatorResultRecord.origin_session_id == session_id).order_by(
                    ValidatorResultRecord.created_at.desc(), ValidatorResultRecord.id,
                )).all()
        return [{"result_ref": f"validator-result:{row[0]}", "agent_key": row[1],
                 "created_at": row[2].isoformat(), "decision_status": row[3]} for row in rows]
