"""Service for ingesting PDFs: extraction, chunking, embeddings."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import ExtractionMethod, PDFDocument, PDFChunk
from lib.pdf_processor import PDFProcessor, ExtractionResult
from lib.chunk_manager import ChunkManager, ChunkResult, ChunkingStrategy
from lib.embedding_service import EmbeddingService
from app.config import get_settings
from app.database import SessionLocal


class PDFIngestService:
    """Coordinates PDF extraction, chunking, and embedding."""

    def __init__(
        self,
        *,
        session_factory=SessionLocal,
        pdf_processor: PDFProcessor,
        chunk_manager: ChunkManager,
        embedding_service: EmbeddingService,
    ) -> None:
        self._session_factory = session_factory
        self._pdf_processor = pdf_processor
        self._chunk_manager = chunk_manager
        self._embedding_service = embedding_service
        self._settings = get_settings()

    def ingest(self, *, file_path: Path, original_filename: str) -> UUID:
        extraction = self._pdf_processor.extract(
            str(file_path), strategy="fast", extract_tables=True, extract_figures=True
        )
        pdf_id = self._store_document(file_path, original_filename, extraction)
        self._chunk_pdf(pdf_id=pdf_id, extraction=extraction)
        self._embedding_service.embed_pdf(
            pdf_id=pdf_id, model_name=self._settings.embedding_model_name
        )
        return pdf_id

    def _store_document(
        self, file_path: Path, original_filename: str, extraction: ExtractionResult
    ) -> UUID:
        session: Session = self._session_factory()
        try:
            normalized_hash = extraction.content_hash_normalized or self._hash_text(
                extraction.full_text
            )
            existing = (
                session.query(PDFDocument)
                .filter(PDFDocument.content_hash_normalized == normalized_hash)
                .first()
            )
            if existing:
                return existing.id

            file_hash = extraction.content_hash or self._hash_file(file_path)

            extraction_method = self._map_strategy(extraction.processing_strategy)

            document = PDFDocument(
                filename=original_filename,
                file_path=str(file_path),
                file_hash=file_hash,
                content_hash_normalized=normalized_hash,
                file_size=extraction.file_size_bytes,
                page_count=extraction.page_count,
                extracted_text=extraction.full_text,
                extraction_method=extraction_method,
                is_ocr=False,
                table_count=len(extraction.tables),
                figure_count=len(extraction.figures),
                meta_data=extraction.metadata,
            )
            session.add(document)
            session.commit()
            session.refresh(document)
            return document.id
        finally:
            session.close()

    def _chunk_pdf(self, *, pdf_id: UUID, extraction: ExtractionResult) -> None:
        session: Session = self._session_factory()
        try:
            chunk_result: ChunkResult = self._chunk_manager.chunk(
                extraction, strategy=ChunkingStrategy.BY_TITLE
            )
            document = session.get(PDFDocument, pdf_id)
            if not document:
                raise ValueError("PDF document not found during chunking")

            document.chunk_count = chunk_result.total_chunks

            for chunk in chunk_result.chunks:
                db_chunk = PDFChunk(
                    pdf_id=pdf_id,
                    chunk_index=chunk.chunk_index,
                    text=chunk.text,
                    element_type=None,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    section_path=chunk.section_path,
                    is_reference=chunk.is_reference,
                    is_caption=chunk.is_caption,
                    is_table=chunk.is_table,
                    is_figure=chunk.contains_figure,
                    token_count=chunk.token_count,
                    chunk_hash=chunk.chunk_hash,
                    meta_data=chunk.metadata,
                )
                session.add(db_chunk)
                session.flush()

                session.execute(
                    text(
                        """
                        INSERT INTO chunk_search (id, chunk_id, search_vector, text_length, lang)
                        VALUES (:id, :chunk_id, to_tsvector(:lang, :text), :length, :lang)
                        """
                    ),
                    {
                        "id": str(uuid4()),
                        "chunk_id": str(db_chunk.id),
                        "text": chunk.text,
                        "length": len(chunk.text),
                        "lang": "english",
                    },
                )

            session.commit()
        finally:
            session.close()

    @staticmethod
    def _hash_file(path: Path) -> str:
        md5 = hashlib.md5()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(8192), b""):
                md5.update(block)
        return md5.hexdigest()

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _map_strategy(strategy: str | None) -> str:
        mapping = {
            "fast": ExtractionMethod.UNSTRUCTURED_FAST.value,
            "hi_res": ExtractionMethod.UNSTRUCTURED_HI_RES.value,
            "ocr_only": ExtractionMethod.UNSTRUCTURED_OCR_ONLY.value,
        }
        if not strategy:
            return ExtractionMethod.UNSTRUCTURED_FAST.value
        return mapping.get(strategy.lower(), ExtractionMethod.UNSTRUCTURED_FAST.value)


def get_pdf_ingest_service() -> PDFIngestService:
    from app.services.embedding_service_factory import get_embedding_service

    return PDFIngestService(
        pdf_processor=PDFProcessor(default_strategy="fast"),
        chunk_manager=ChunkManager(),
        embedding_service=get_embedding_service(),
    )


__all__ = ["PDFIngestService", "get_pdf_ingest_service"]
