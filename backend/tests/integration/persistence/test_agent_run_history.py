"""Concurrent immutable history commits against PostgreSQL, with restart recovery."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4
import hashlib
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from sqlalchemy import delete, func, select

from src.lib.openai_agents.run_history import HistoryScope, HistoryStore, exact_json
from src.models.sql.agent_run_history import AgentHistoryEvent, AgentHistoryPayload
from src.models.sql.database import SessionLocal


def test_history_downgrade_cannot_remove_retained_originals():
    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    command.upgrade(config, "head")
    with pytest.raises(RuntimeError, match="retained"):
        command.downgrade(config, "b8a2d3f4e5c6")


def test_concurrent_identical_payloads_have_distinct_events_and_one_durable_body():
    owner = "history-test-" + uuid4().hex
    scope = HistoryScope(owner, uuid4().hex, uuid4().hex, "extractor", uuid4().hex + "/")
    payload = {"fixture": owner, "negative": True, "quote": "Not observed β😀. " * 2000,
               "validators": [{"id": "one", "findings": [None, False]}, {"id": "two", "findings": []}]}
    content = exact_json(payload)
    digest = hashlib.sha256(content.encode()).hexdigest()
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            refs = list(pool.map(lambda i: HistoryStore().append(scope, "tool_result", content, call_id=str(i)), range(8)))
        assert len(set(refs)) == 8
        restarted = HistoryStore()
        assert all(restarted.read(scope, ref)["payload"] == payload for ref in refs)
        assert restarted.search(replace(scope, owner_id="someone-else"), "Not observed", after=0, limit=20) == []
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(AgentHistoryPayload).where(AgentHistoryPayload.sha256 == digest)) == 1
            assert db.scalar(select(func.count()).select_from(AgentHistoryEvent).where(AgentHistoryEvent.owner_id == owner)) == 8
        quoted = 'No "eve" signal.\nNot observed. Path A\\B.'
        quoted_ref = restarted.append(scope, "tool_result", exact_json({"output": exact_json({"quote": quoted})}))
        assert restarted.search(scope, quoted, after=0, limit=20)[0]["event_id"] == quoted_ref
    finally:
        with SessionLocal() as db, db.begin():
            hashes = list(db.scalars(select(AgentHistoryEvent.payload_sha256).where(AgentHistoryEvent.owner_id == owner)))
            db.execute(delete(AgentHistoryEvent).where(AgentHistoryEvent.owner_id == owner))
            db.execute(delete(AgentHistoryPayload).where(AgentHistoryPayload.sha256.in_(hashes)))
