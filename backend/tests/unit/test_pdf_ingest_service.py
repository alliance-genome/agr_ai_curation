"""Tests for PDF ingestion service."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.database import SessionLocal, engine as db_engine
from app.models import (
    Base,
    PDFDocument,
    PDFChunk,
    ChunkSearch,
    Settings as SettingsModel,
)
from app.services.pdf_ingest_service import PDFIngestService
from lib.chunk_manager import Chunk, ChunkResult, ChunkingStrategy
from lib.pdf_processor import ExtractionResult, UnstructuredElement


class FakePDFProcessor:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def extract(self, path: str, **kwargs):
        self.calls.append(kwargs)
        strategy = kwargs.get("strategy", "fast")
        element = UnstructuredElement(
            type="NarrativeText",
            text="Sample chunk text",
            metadata={"page_number": 1},
            element_id="elem-1",
            page_number=1,
            bbox=None,
            parent_id=None,
            section_path="Intro",
        )
        return ExtractionResult(
            pdf_path=path,
            elements=[element],
            page_count=1,
            full_text="Full text",
            metadata={},
            tables=[],
            figures=[],
            extraction_time_ms=100.0,
            file_size_bytes=100,
            processing_strategy=strategy,
            content_hash="hash1",
            content_hash_normalized="normhash",
        )


class FakeChunkManager:
    def chunk(self, extraction_result, strategy=ChunkingStrategy.BY_TITLE, **kwargs):
        chunk = Chunk(
            chunk_index=0,
            text="Sample chunk text",
            token_count=10,
            char_start=0,
            char_end=10,
            page_start=1,
            page_end=1,
            section_path="Intro",
            is_reference=False,
            is_caption=False,
            is_table=False,
            contains_table=False,
            contains_figure=False,
            contains_caption=False,
            metadata={},
        )
        return ChunkResult(
            chunks=[chunk],
            total_chunks=1,
            avg_chunk_size=10,
            processing_time_ms=1.0,
            strategy=strategy,
            parameters={},
        )


class FakeEmbeddingService:
    def __init__(self):
        self.pdf_calls = []
        self.unified_calls = []

    def embed_pdf(self, *, pdf_id: uuid4, model_name: str, version: str | None = None, batch_size: int | None = None, force: bool = False):  # type: ignore[override]
        self.pdf_calls.append(pdf_id)
        return {"embedded": 1, "skipped": 0, "model": model_name, "version": "1"}

    def embed_unified_chunks(
        self,
        *,
        source_type: str,
        source_id: str,
        model_name: str,
        batch_size: int | None = None,
        force: bool = False,
    ):
        self.unified_calls.append((source_type, source_id))
        return {
            "embedded": 1,
            "skipped": 0,
            "model": model_name,
            "source_type": source_type,
            "source_id": source_id,
        }


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=db_engine)
    session = SessionLocal()
    try:
        session.execute(
            text(
                "TRUNCATE TABLE chunk_search, pdf_embeddings, pdf_chunks, pdf_documents, settings RESTART IDENTITY CASCADE"
            )
        )
        session.commit()
    finally:
        session.close()
    yield


@pytest.fixture
def service():
    return PDFIngestService(
        pdf_processor=FakePDFProcessor(),
        chunk_manager=FakeChunkManager(),
        embedding_service=FakeEmbeddingService(),
    )


@pytest.fixture
def tmp_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"PDF")
    return path


def test_ingest_persists_document_and_chunks(service, tmp_pdf):
    pdf_id, created = service.ingest(file_path=tmp_pdf, original_filename="sample.pdf")
    assert created is True

    session = SessionLocal()
    try:
        document = session.get(PDFDocument, pdf_id)
        assert document is not None
        assert document.chunk_count == 1

        chunks = session.query(PDFChunk).filter(PDFChunk.pdf_id == pdf_id).all()
        assert len(chunks) == 1
        assert chunks[0].text == "Sample chunk text"

        search_entries = (
            session.query(ChunkSearch)
            .filter(ChunkSearch.chunk_id == chunks[0].id)
            .all()
        )
        assert len(search_entries) == 1
    finally:
        session.close()


def test_ingest_skips_duplicate_pdf(service, tmp_pdf):
    first_id, created = service.ingest(
        file_path=tmp_pdf, original_filename="sample.pdf"
    )
    assert created is True

    second_id, created_again = service.ingest(
        file_path=tmp_pdf, original_filename="sample.pdf"
    )

    assert first_id == second_id
    assert created_again is False

    session = SessionLocal()
    try:
        chunks = session.query(PDFChunk).filter(PDFChunk.pdf_id == first_id).all()
        assert len(chunks) == 1
    finally:
        session.close()

    # Embedding service should not be invoked again for duplicates.
    assert len(service._embedding_service.pdf_calls) == 1  # type: ignore[attr-defined]


def test_ingest_uses_configured_extraction_strategy(service, tmp_pdf):
    session = SessionLocal()
    try:
        session.add(SettingsModel(key="pdf_extraction_strategy", value="hi_res"))
        session.commit()
    finally:
        session.close()

    service.ingest(file_path=tmp_pdf, original_filename="sample.pdf")

    calls = getattr(service._pdf_processor, "calls", [])
    assert calls
    assert calls[-1].get("strategy") == "hi_res"


def test_ingest_falls_back_to_default_strategy(service, tmp_pdf):
    session = SessionLocal()
    try:
        session.add(SettingsModel(key="pdf_extraction_strategy", value="not_real"))
        session.commit()
    finally:
        session.close()

    service.ingest(file_path=tmp_pdf, original_filename="sample.pdf")

    calls = getattr(service._pdf_processor, "calls", [])
    assert calls
    assert calls[-1].get("strategy") == "fast"
