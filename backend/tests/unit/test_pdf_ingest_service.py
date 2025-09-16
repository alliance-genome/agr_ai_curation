"""Tests for PDF ingestion service."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.database import SessionLocal, engine as db_engine
from app.models import Base, PDFDocument, PDFChunk, ChunkSearch
from app.services.pdf_ingest_service import PDFIngestService
from lib.chunk_manager import Chunk, ChunkResult, ChunkingStrategy
from lib.pdf_processor import ExtractionResult, UnstructuredElement


class FakePDFProcessor:
    def extract(self, path: str, **kwargs):
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
            processing_strategy="fast",
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
        self.calls = []

    def embed_pdf(self, *, pdf_id: uuid4, model_name: str, version: str | None = None, batch_size: int | None = None, force: bool = False):  # type: ignore[override]
        self.calls.append(pdf_id)
        return {"embedded": 1, "skipped": 0, "model": model_name, "version": "1"}


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=db_engine)
    session = SessionLocal()
    try:
        session.execute(
            text(
                "TRUNCATE TABLE chunk_search, pdf_embeddings, pdf_chunks, pdf_documents RESTART IDENTITY CASCADE"
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
    pdf_id = service.ingest(file_path=tmp_pdf, original_filename="sample.pdf")

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
