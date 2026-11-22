"""An imported paper's listing reads the last provider observation from its own jobs (P6f).

The job metadata is the shape P7b writes (interface I1). All values are made up.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete

from src.lib.document_sources.file_listing_service import _paper_lookup
from src.lib.document_sources.file_reasons import FileReason
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.pdf_processing_job import PdfJobStatus, PdfProcessingJob
from tests.pdf_document_test_support import ensure_test_pdf_owner, new_paper

PREFIX = "p6f_observation_test_"
START = datetime(2026, 11, 22, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def paper():
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")
    document_id = uuid4()
    with SessionLocal.begin() as db:
        owner_id = ensure_test_pdf_owner(db, auth_sub=f"p6f-owner-{uuid4()}")
        db.add(new_paper(
            id=document_id, user_id=owner_id, filename=f"{PREFIX}{document_id}.pdf", status="completed",
            file_path=f"owner/{document_id}/made-up.pdf", file_hash=document_id.hex * 2, file_size=100,
            page_count=2, source_provider="made_up_provider", source_provider_reference_id="101",
            source_provider_reference_curie="MADE:101", source_provider_source_file_id="10",
        ))
    yield document_id, owner_id
    with SessionLocal.begin() as db:
        db.execute(delete(PDFDocument).where(PDFDocument.filename.like(f"{PREFIX}%")))


def _job(document_id, user_id, minutes: int, supplements=None) -> PdfProcessingJob:
    return PdfProcessingJob(
        document_id=document_id, user_id=user_id, status=PdfJobStatus.COMPLETED.value,
        created_at=START + timedelta(minutes=minutes),
        metadata_json=None if supplements is None else {"supplements": supplements},
    )


def _entry(file_id: str, state: str, reason_code: str | None, minutes: int) -> dict:
    return {
        "provider_file_id": file_id, "display_name": f"Made_up_S{file_id}", "state": state,
        "reason_code": reason_code, "observed_at": (START + timedelta(minutes=minutes)).isoformat(),
        "file_id": None,
    }


def test_observations_come_from_this_papers_jobs_newest_first(paper):
    document_id, owner_id = paper
    with SessionLocal.begin() as db:
        db.add_all([
            _job(document_id, owner_id, 0, [
                _entry("30", "left_out", "provider_not_converted", 5),
                _entry("40", "left_out", "not_converted_in_time", 5),
            ]),
            _job(document_id, owner_id, 60, [_entry("40", "left_out", "provider_conversion_failed", 65)]),
            _job(document_id, owner_id, 120),  # a job written before P7b: no supplements
        ])

    source = _paper_lookup(document_id, owner_id)

    assert source.file_ids == ("10",)
    assert {file_id: observation.code for file_id, observation in source.provider_observations.items()} == {
        "30": FileReason.PROVIDER_NOT_CONVERTED,
        "40": FileReason.PROVIDER_CONVERSION_FAILED,
    }
    assert source.provider_observations["40"].observed_at == START + timedelta(minutes=65)


def test_a_paper_without_supplement_progress_has_no_observations(paper):
    document_id, owner_id = paper
    with SessionLocal.begin() as db:
        db.add(_job(document_id, owner_id, 0))
    assert _paper_lookup(document_id, owner_id).provider_observations == {}
