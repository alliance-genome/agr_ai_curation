"""Unit tests for curation workspace session-service helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.lib.curation_workspace import session_service as module
from src.lib.curation_workspace.models import (
    CurationActionLogEntry as SessionActionLogModel,
    CurationCandidate,
    CurationDraft as DraftModel,
    CurationEvidenceRecord as EvidenceRecordModel,
    CurationExtractionResultRecord as ExtractionResultModel,
    CurationReviewSession as ReviewSessionModel,
    CurationSubmissionRecord as SubmissionModel,
    CurationValidationSnapshot as ValidationSnapshotModel,
)
from src.models.sql.database import Base
from src.models.sql.pdf_document import PDFDocument
from src.schemas.curation_workspace import (
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationExtractionSourceKind,
    CurationSessionStatus,
)


@compiles(PostgresUUID, "sqlite")
def _compile_pg_uuid_for_sqlite(_type, _compiler, **_kwargs):
    return "CHAR(36)"


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


TEST_TABLES = [
    PDFDocument.__table__,
    ReviewSessionModel.__table__,
    ExtractionResultModel.__table__,
    CurationCandidate.__table__,
    EvidenceRecordModel.__table__,
    DraftModel.__table__,
    SubmissionModel.__table__,
    ValidationSnapshotModel.__table__,
    SessionActionLogModel.__table__,
]


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    restored_defaults = []
    restored_indexes = []
    for table in TEST_TABLES:
        restored_indexes.append((table, set(table.indexes)))
        table.indexes.clear()
        for column in table.columns:
            restored_defaults.append((column, column.server_default))
            column.server_default = None

    Base.metadata.create_all(bind=engine, tables=TEST_TABLES)
    session_local = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    session = session_local()

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=TEST_TABLES)
        for table, indexes in restored_indexes:
            table.indexes.update(indexes)
        for column, server_default in restored_defaults:
            column.server_default = server_default


def _now() -> datetime:
    return datetime(2026, 3, 21, 15, 30, tzinfo=timezone.utc)


def _create_document(db_session):
    now = _now()
    document = PDFDocument(
        id=uuid4(),
        filename="paper.pdf",
        title="Paper Title",
        file_path="/tmp/paper.pdf",
        file_hash="a" * 64,
        file_size=1024,
        page_count=5,
        upload_timestamp=now,
        last_accessed=now,
        status="processed",
    )
    db_session.add(document)
    db_session.commit()
    return document


def _create_extraction_result(db_session, *, document_id: str, domain_key: str) -> ExtractionResultModel:
    record = ExtractionResultModel(
        id=uuid4(),
        document_id=UUID(document_id),
        adapter_key="reference_adapter",
        profile_key="primary",
        domain_key=domain_key,
        agent_key="curation_prep",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id="chat-session-1",
        trace_id=f"trace-{domain_key}",
        flow_run_id="flow-1",
        user_id="user-1",
        candidate_count=1,
        conversation_summary=f"Prep summary for {domain_key}.",
        payload_json={
            "candidates": [],
            "run_metadata": {
                "model_name": "placeholder",
                "token_usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
                "processing_notes": [],
                "warnings": [],
            },
        },
        extraction_metadata={"final_run_metadata": {"model_name": "gpt-5-mini"}},
        created_at=_now(),
    )
    db_session.add(record)
    db_session.commit()
    return record


def _create_session_for_extraction(
    db_session,
    *,
    document_id: str,
    extraction_result_id: UUID,
) -> ReviewSessionModel:
    now = _now()
    session_row = ReviewSessionModel(
        id=uuid4(),
        status=CurationSessionStatus.NEW,
        adapter_key="reference_adapter",
        profile_key="primary",
        document_id=UUID(document_id),
        flow_run_id="flow-1",
        session_version=1,
        notes="Prepared session.",
        tags=["triage"],
        total_candidates=1,
        reviewed_candidates=0,
        pending_candidates=1,
        accepted_candidates=0,
        rejected_candidates=0,
        manual_candidates=0,
        warnings=[],
        prepared_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(session_row)
    db_session.flush()
    db_session.add(
        CurationCandidate(
            id=uuid4(),
            session_id=session_row.id,
            source=CurationCandidateSource.EXTRACTED,
            status=CurationCandidateStatus.PENDING,
            order=0,
            adapter_key="reference_adapter",
            profile_key="primary",
            display_label="Candidate",
            extraction_result_id=extraction_result_id,
            candidate_metadata={},
            normalized_payload={"entity": {"label": "Example"}},
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()
    return session_row


def test_find_reusable_prepared_session_returns_only_matching_extraction_result(db_session):
    document = _create_document(db_session)
    document_id = str(document.id)
    extraction_alpha = _create_extraction_result(
        db_session,
        document_id=document_id,
        domain_key="alpha",
    )
    extraction_beta = _create_extraction_result(
        db_session,
        document_id=document_id,
        domain_key="beta",
    )
    session_alpha = _create_session_for_extraction(
        db_session,
        document_id=document_id,
        extraction_result_id=extraction_alpha.id,
    )

    reusable = module.find_reusable_prepared_session(
        db_session,
        document_id=document_id,
        adapter_key="reference_adapter",
        profile_key="primary",
        flow_run_id="flow-1",
        prep_extraction_result_id=str(extraction_beta.id),
    )

    assert reusable is None

    matching = module.find_reusable_prepared_session(
        db_session,
        document_id=document_id,
        adapter_key="reference_adapter",
        profile_key="primary",
        flow_run_id="flow-1",
        prep_extraction_result_id=str(extraction_alpha.id),
    )

    assert matching is not None
    assert matching.session_id == str(session_alpha.id)
