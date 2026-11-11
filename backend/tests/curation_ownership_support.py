"""SQLite fixtures for review-session ownership tests (ALL-1547).

The tables are the real ORM tables. PostgreSQL-only DDL (server defaults,
partial indexes and regex CHECK constraints) is stripped while the in-memory
schema exists and restored afterwards, as the session-service unit tests do.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import CheckConstraint, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.lib.curation_workspace.models import (
    CurationActionLogEntry,
    CurationBenchmarkSnapshot,
    CurationCandidate,
    CurationDraft,
    CurationEvidenceRecord,
    CurationExtractionResultRecord,
    CurationReviewSession,
    CurationSessionAgentRevision,
    CurationSubmissionRecord,
    CurationValidationSnapshot,
    DomainEnvelopeModel,
)
from src.models.sql.database import Base
from src.models.sql.pdf_document import DocumentReferenceLink, PDFDocument
from src.models.sql.user import User
from src.models.sql.validation_review import WorkspaceReviewOperation
from src.schemas.curation_workspace import (
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationExtractionSourceKind,
    CurationSessionStatus,
    CurationSubmissionStatus,
    SubmissionMode,
)
from src.schemas.domain_envelope import DomainEnvelopeStatus


@compiles(PostgresUUID, "sqlite")
def _compile_pg_uuid_for_sqlite(_type, _compiler, **_kwargs):
    return "CHAR(36)"


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


OWNERSHIP_TABLES = [
    User.__table__,
    PDFDocument.__table__,
    DocumentReferenceLink.__table__,
    CurationReviewSession.__table__,
    CurationSessionAgentRevision.__table__,
    CurationExtractionResultRecord.__table__,
    DomainEnvelopeModel.__table__,
    CurationCandidate.__table__,
    CurationEvidenceRecord.__table__,
    CurationDraft.__table__,
    CurationValidationSnapshot.__table__,
    CurationSubmissionRecord.__table__,
    CurationActionLogEntry.__table__,
    CurationBenchmarkSnapshot.__table__,
    WorkspaceReviewOperation.__table__,
]

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def ownership_session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    restored_defaults = []
    restored_indexes = []
    restored_checks = []
    for table in OWNERSHIP_TABLES:
        restored_indexes.append((table, set(table.indexes)))
        table.indexes.clear()
        checks = {item for item in table.constraints if isinstance(item, CheckConstraint)}
        restored_checks.append((table, checks))
        table.constraints.difference_update(checks)
        for column in table.columns:
            restored_defaults.append((column, column.server_default))
            column.server_default = None

    Base.metadata.create_all(bind=engine, tables=OWNERSHIP_TABLES)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(bind=engine, tables=OWNERSHIP_TABLES)
        for table, indexes in restored_indexes:
            table.indexes.update(indexes)
        for table, checks in restored_checks:
            table.constraints.update(checks)
        for column, server_default in restored_defaults:
            column.server_default = server_default


@pytest.fixture
def ownership_db(ownership_session_factory):
    db = ownership_session_factory()
    try:
        yield db
    finally:
        db.close()


def add_user(db, auth_sub: str) -> User:
    user = User(
        auth_sub=auth_sub, email=f"{auth_sub}@example.org", display_name=auth_sub, is_active=True, created_at=NOW
    )
    db.add(user)
    db.flush()
    return user


def add_document(db, *, owner: User | None, viewer_mode: str | None = None) -> PDFDocument:
    document = PDFDocument(
        id=uuid4(),
        user_id=owner.id if owner is not None else None,
        filename="paper.pdf",
        title="Ownership fixture paper",
        file_path=f"/tmp/{uuid4().hex}.pdf",
        file_hash=uuid4().hex + uuid4().hex,
        file_size=1024,
        page_count=1,
        upload_timestamp=NOW,
        last_accessed=NOW,
        status="processed",
        viewer_mode=viewer_mode,
    )
    db.add(document)
    db.flush()
    return document


def add_session(
    db,
    *,
    document_id: UUID,
    created_by_id: str | None,
    assigned_curator_id: str | None = None,
    status: CurationSessionStatus = CurationSessionStatus.NEW,
    adapter_key: str = "fixture",
    flow_run_id: str | None = None,
) -> CurationReviewSession:
    row = CurationReviewSession(
        id=uuid4(),
        status=status,
        adapter_key=adapter_key,
        document_id=document_id,
        flow_run_id=flow_run_id,
        assigned_curator_id=assigned_curator_id,
        created_by_id=created_by_id,
        session_version=1,
        total_candidates=0,
        reviewed_candidates=0,
        pending_candidates=0,
        accepted_candidates=0,
        rejected_candidates=0,
        manual_candidates=0,
        warnings=[],
        tags=[],
        prepared_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(row)
    db.flush()
    return row


def add_extraction_result(
    db,
    *,
    document_id: UUID,
    user_id: str | None,
    agent_key: str = "curation_prep",
    adapter_key: str = "fixture",
    flow_run_id: str | None = None,
) -> CurationExtractionResultRecord:
    row = CurationExtractionResultRecord(
        id=uuid4(),
        document_id=document_id,
        adapter_key=adapter_key,
        agent_key=agent_key,
        source_kind=CurationExtractionSourceKind.FLOW,
        flow_run_id=flow_run_id,
        user_id=user_id,
        candidate_count=0,
        payload_json={"candidates": []},
        extraction_metadata={},
        created_at=NOW,
    )
    db.add(row)
    db.flush()
    return row


def add_envelope(
    db,
    *,
    envelope_id: str,
    document_id: UUID,
    session_id: UUID | None = None,
    source_extraction_result_id: str | None = None,
    metadata: dict | None = None,
) -> DomainEnvelopeModel:
    row = DomainEnvelopeModel(
        envelope_id=envelope_id,
        revision=1,
        project_key="fixture",
        domain_pack_key="fixture.pack",
        adapter_key="fixture",
        source_extraction_result_id=source_extraction_result_id,
        source_payload_hash="0" * 64,
        status=DomainEnvelopeStatus.EXTRACTED,
        document_id=document_id,
        session_id=session_id,
        schema_ref_json={},
        object_model_ref_json={},
        model_field_ref_json={},
        envelope_json={
            "envelope_id": envelope_id,
            "domain_pack_id": "fixture.pack",
            "status": "extracted",
            "metadata": metadata or {},
        },
        created_at=NOW,
        updated_at=NOW,
        checkpointed_at=NOW,
    )
    db.add(row)
    db.flush()
    return row


def add_candidate(db, *, session_id: UUID, envelope_id: str | None = None) -> CurationCandidate:
    row = CurationCandidate(
        id=uuid4(),
        session_id=session_id,
        source=CurationCandidateSource.EXTRACTED,
        status=CurationCandidateStatus.PENDING,
        order=0,
        adapter_key="fixture",
        display_label="Fixture candidate",
        envelope_id=envelope_id,
        object_id="object-1" if envelope_id else None,
        envelope_revision=1 if envelope_id else None,
        normalized_payload={},
        candidate_metadata={},
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(row)
    db.flush()
    return row


def add_submission(db, *, session_id: UUID) -> CurationSubmissionRecord:
    row = CurationSubmissionRecord(
        id=uuid4(),
        session_id=session_id,
        adapter_key="fixture",
        mode=SubmissionMode.PREVIEW,
        target_key="review_export_bundle",
        status=CurationSubmissionStatus.PREVIEW_READY,
        attempt_state_history=[],
        readiness=[],
        validation_errors=[],
        warnings=[],
        submission_state={},
        target_result_history=[],
        requested_at=NOW,
    )
    db.add(row)
    db.flush()
    return row


def add_operation(db, *, session_id: UUID, owner_id: str) -> WorkspaceReviewOperation:
    row = WorkspaceReviewOperation(
        id=str(uuid4()),
        owner_id=owner_id,
        session_id=str(session_id),
        request_id=str(uuid4()),
        request={"candidate_ids": []},
        artifacts=[],
        state="needs_review",
        outcome={},
        created_at=NOW,
    )
    db.add(row)
    db.flush()
    return row


def add_snapshot(db, *, session_id: UUID, envelope_id: str, created_by_id: str) -> CurationBenchmarkSnapshot:
    row = CurationBenchmarkSnapshot(
        id=uuid4(),
        session_id=session_id,
        envelope_id=envelope_id,
        envelope_revision=1,
        envelope_digest="sha256:" + "0" * 64,
        bundle_json="{}",
        created_by_id=created_by_id,
        exported_at=NOW,
    )
    db.add(row)
    db.flush()
    return row
