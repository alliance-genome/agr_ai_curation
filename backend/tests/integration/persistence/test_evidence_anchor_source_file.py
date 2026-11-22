"""The file an evidence anchor quotes, on real PostgreSQL (KANBAN-1976 / ALL-1460, P8b).

A quoted file row cannot be deleted (ON DELETE RESTRICT), deleting the whole
paper still works (anchors are deleted first), and an anchor saved before files
were recorded keeps a NULL file. All values are made up.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from src.lib.curation_workspace.models import (
    CurationCandidate,
    CurationEvidenceRecord,
    CurationReviewSession,
)
from src.lib.document_cleanup import cleanup_document_curation_dependencies
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument, PDFDocumentFile
from src.schemas.curation_workspace import (
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationEvidenceSource,
    CurationSessionStatus,
)
from tests.pdf_document_test_support import ensure_test_pdf_owner, new_paper


BACKEND_ROOT = Path(__file__).resolve().parents[3]
PREFIX = "p8b_anchor_file_test_"
PREVIOUS_HEAD = "a1460f6b2c3d"
REVISION = "b1460f8e9a0c"
NOW = datetime(2026, 11, 22, tzinfo=timezone.utc)


def _config() -> Config:
    return Config(str(BACKEND_ROOT / "alembic.ini"))


@pytest.fixture
def paper_with_anchors():
    command.upgrade(_config(), "head")
    with SessionLocal.begin() as db:
        owner_id = ensure_test_pdf_owner(db, auth_sub=f"p8b-owner-{uuid4()}")
        document_id = uuid4()
        paper = new_paper(
            id=document_id, user_id=owner_id, filename=f"{PREFIX}{document_id}.pdf",
            status="completed", file_path=f"owner/{document_id}/made-up.pdf",
            file_hash=document_id.hex * 2, file_size=100, page_count=2,
            chunk_index_start=0, chunk_index_end=4, chunk_count=5,
        )
        db.add(paper)
        db.flush()
        session_row = CurationReviewSession(
            id=uuid4(), status=CurationSessionStatus.IN_PROGRESS, adapter_key="fixture_adapter",
            document_id=document_id, session_version=1, tags=[], total_candidates=1,
            reviewed_candidates=0, pending_candidates=1, accepted_candidates=0,
            rejected_candidates=0, manual_candidates=0, warnings=[], prepared_at=NOW,
            created_at=NOW, updated_at=NOW,
        )
        db.add(session_row)
        db.flush()
        candidate = CurationCandidate(
            id=uuid4(), session_id=session_row.id, source=CurationCandidateSource.MANUAL,
            status=CurationCandidateStatus.PENDING, order=0, adapter_key="fixture_adapter",
            candidate_metadata={}, normalized_payload={}, created_at=NOW, updated_at=NOW,
        )
        db.add(candidate)
        db.flush()
        main_file_id = paper.main_file.id
        anchors = {
            "quoting": CurationEvidenceRecord(
                candidate_id=candidate.id, source=CurationEvidenceSource.MANUAL,
                anchor={"snippet_text": "Made-up quote."}, source_file_id=main_file_id,
            ),
            "saved_before_files": CurationEvidenceRecord(
                candidate_id=candidate.id, source=CurationEvidenceSource.MANUAL,
                anchor={"snippet_text": "Older made-up quote."},
            ),
        }
        db.add_all(anchors.values())
        db.flush()
        anchor_ids = {name: row.id for name, row in anchors.items()}
    yield document_id, main_file_id, anchor_ids
    with SessionLocal.begin() as db:
        for stored in db.scalars(select(PDFDocument).where(PDFDocument.filename.like(f"{PREFIX}%"))):
            cleanup_document_curation_dependencies(db, stored.id)
            db.delete(stored)


def test_an_anchor_stores_its_file_and_an_older_anchor_has_none(paper_with_anchors):
    _, main_file_id, anchor_ids = paper_with_anchors

    with SessionLocal() as db:
        assert db.get(CurationEvidenceRecord, anchor_ids["quoting"]).source_file_id == main_file_id
        assert db.get(CurationEvidenceRecord, anchor_ids["saved_before_files"]).source_file_id is None


def test_a_quoted_file_row_cannot_be_deleted(paper_with_anchors):
    _, main_file_id, _ = paper_with_anchors

    with pytest.raises(IntegrityError, match="fk_evidence_anchors_source_file_id"):
        with SessionLocal.begin() as db:
            db.execute(delete(PDFDocumentFile).where(PDFDocumentFile.id == main_file_id))

    with SessionLocal() as db:
        assert db.get(PDFDocumentFile, main_file_id) is not None


def test_deleting_the_whole_paper_still_works(paper_with_anchors):
    document_id, main_file_id, anchor_ids = paper_with_anchors

    with SessionLocal.begin() as db:
        cleanup_document_curation_dependencies(db, document_id)
        db.delete(db.get(PDFDocument, document_id))

    with SessionLocal() as db:
        assert db.get(PDFDocument, document_id) is None
        assert db.get(PDFDocumentFile, main_file_id) is None
        assert all(db.get(CurationEvidenceRecord, anchor_id) is None for anchor_id in anchor_ids.values())


def test_the_migration_adds_a_restricting_nullable_column_and_refuses_downgrade(
    historical_migration_database,
):
    command.upgrade(_config(), PREVIOUS_HEAD)
    with SessionLocal() as db:
        assert db.scalar(text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'evidence_anchors' AND column_name = 'source_file_id'"
        )) == 0

    command.upgrade(_config(), REVISION)
    with SessionLocal() as db:
        nullable = db.scalar(text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'evidence_anchors' AND column_name = 'source_file_id'"
        ))
        on_delete = db.scalar(text(
            "SELECT confdeltype FROM pg_constraint WHERE conname = 'fk_evidence_anchors_source_file_id'"
        ))
        index = db.scalar(text(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_evidence_anchors_source_file_id'"
        ))
    assert nullable == "YES"
    assert on_delete == "r"
    assert "source_file_id" in index

    with pytest.raises(RuntimeError, match="forward-only"):
        command.downgrade(_config(), PREVIOUS_HEAD)
