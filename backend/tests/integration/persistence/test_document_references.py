"""Durable source generation and concurrent span issuance on real PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select

from agr_ai_curation_runtime.evidence_spans import build_evidence_spans
from src.lib.openai_agents import document_references as refs
from src.lib.openai_agents.run_history import HistoryScope, HistoryStore
from src.models.sql.agent_run_history import AgentHistoryEvent, AgentHistoryPayload
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.user import User


@pytest.fixture
def source_record():
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")
    owner, document_id = "reference-test-" + uuid4().hex, uuid4()
    with SessionLocal() as db, db.begin():
        user = User(auth_sub=owner)
        db.add(user)
        db.flush()
        user_id = user.id
        db.add(PDFDocument(id=document_id, user_id=user_id, filename="fixture.pdf",
            file_path="/test/" + document_id.hex, file_hash=document_id.hex * 2,
            file_size=1, page_count=1, status="completed",
            processing_completed_at=datetime.now(timezone.utc)))
    scope = HistoryScope(owner, uuid4().hex, uuid4().hex, "extractor", uuid4().hex + "/", uuid4().hex)
    try:
        yield str(document_id), owner, scope
    finally:
        with SessionLocal() as db, db.begin():
            hashes = list(db.scalars(select(AgentHistoryEvent.payload_sha256).where(AgentHistoryEvent.owner_id == owner)))
            db.execute(delete(AgentHistoryEvent).where(AgentHistoryEvent.owner_id == owner))
            db.execute(delete(AgentHistoryPayload).where(AgentHistoryPayload.sha256.in_(hashes)))
            db.execute(delete(PDFDocument).where(PDFDocument.id == document_id))
            db.execute(delete(User).where(User.id == user_id))


def test_concurrent_issuance_and_restart_share_exact_definition(source_record):
    doc, owner, scope = source_record
    chunk = {"id": str(uuid4()), "chunk_index": 42, "text": "First.\n\nSecond.\n\nThird."}
    spans = [s.to_dict() for s in build_evidence_spans(chunk_id=chunk["id"], chunk_text=chunk["text"])]
    def issue(index):
        binding = refs._load_source_binding(doc, owner, scope, HistoryStore())
        binding.retain(chunk, spans, [index % len(spans)])
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(issue, range(12)))
    restarted = refs._load_source_binding(doc, owner, replace(scope, run_id=uuid4().hex), HistoryStore())
    assert len([r for r in restarted.records if r["kind"] == refs._SOURCE_KIND]) == 1
    assert len([r for r in restarted.records if r["kind"] == refs._SPAN_KIND]) == 1
    coverage = [i for r in restarted.records if r["kind"] == refs._WINDOW_KIND for i in r["issued_indexes"]]
    assert sorted(coverage) == list(range(len(spans)))
    refs._verify_source_generation(restarted)


def test_generation_and_owner_checks_use_authoritative_sql(source_record):
    from uuid import UUID
    doc, owner, scope = source_record
    binding = refs._load_source_binding(doc, owner, scope, HistoryStore())
    with pytest.raises(refs.DocumentReferenceError, match="unavailable"):
        refs._load_source_binding(doc, "someone-else", replace(scope, owner_id="someone-else"), HistoryStore())
    with SessionLocal() as db, db.begin():
        record = db.get(PDFDocument, UUID(doc))
        record.status = "processing"
    with pytest.raises(refs.DocumentReferenceError, match="unavailable"):
        refs._verify_source_generation(binding)
    with SessionLocal() as db, db.begin():
        record = db.get(PDFDocument, UUID(doc))
        record.status = "completed"
        record.processing_completed_at += timedelta(seconds=1)
    with pytest.raises(refs.DocumentReferenceError, match="changed"):
        refs._verify_source_generation(binding)
    with pytest.raises(refs.DocumentReferenceError, match="source changed"):
        refs._load_source_binding(doc, owner, scope, HistoryStore())
