"""TDD-RED: Tests for lexical search latency and ordering."""

from __future__ import annotations

import os
import time
from typing import List
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.models import Base, PDFDocument, PDFChunk, PDFEmbedding


@pytest.fixture(scope="module")
def test_database_url() -> str:
    return os.getenv(
        "TEST_DATABASE_URL",
        "postgresql://curation_user:curation_pass@postgres-test:5432/ai_curation_test",  # pragma: allowlist secret
    )


@pytest.fixture(scope="module")
def engine(test_database_url: str) -> Engine:
    try:
        engine = create_engine(test_database_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:  # pragma: no cover - infrastructure issue
        pytest.skip(f"Test database not available: {exc}")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine: Engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def clean_tables(engine: Engine):
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE pdf_embeddings, pdf_chunks, pdf_documents, chunk_search RESTART IDENTITY CASCADE"
            )
        )
    yield


def _seed_document(session_factory) -> UUID:
    with session_factory() as session:
        doc = PDFDocument(
            filename="lexical.pdf",
            file_path="/tmp/lexical.pdf",
            file_hash=uuid4().hex[:32],
            content_hash_normalized=uuid4().hex[:32],
            file_size=2048,
            page_count=2,
        )
        session.add(doc)
        session.flush()

        chunks = [
            (
                "BRCA1 is associated with breast cancer.",
                "BRCA1",
            ),
            (
                "TP53 mutations are common in many cancers.",
                "TP53",
            ),
            (
                "EGFR inhibitors target epidermal growth factor receptor.",
                "EGFR",
            ),
        ]

        for idx, (text_body, keyword) in enumerate(chunks):
            chunk = PDFChunk(
                pdf_id=doc.id,
                chunk_index=idx,
                text=text_body,
                element_type="NarrativeText",
                page_start=1,
                page_end=1,
                chunk_hash=uuid4().hex[:32],
                token_count=len(text_body.split()),
            )
            session.add(chunk)
            session.flush()

            search_entry = text(
                """
                INSERT INTO chunk_search (chunk_id, search_vector, search_text)
                VALUES (:chunk_id, to_tsvector('simple', :text_body), :text_body)
                """
            )
            session.execute(
                search_entry,
                {
                    "chunk_id": str(chunk.id),
                    "text_body": text_body,
                },
            )

        session.commit()
        return doc.id


def test_lexical_search_returns_top_results_under_latency(
    engine: Engine, session_factory
):
    """Lexical search should respect ordering and latency targets."""

    document_id = _seed_document(session_factory)

    from lib.lexical_search import LexicalSearch  # noqa: PLC0415

    search = LexicalSearch(engine)

    start = time.perf_counter()
    results = search.query(
        pdf_id=document_id,
        query="TP53",
        top_k=2,
    )
    duration_ms = (time.perf_counter() - start) * 1000

    assert duration_ms < 50
    assert len(results) == 2
    assert "TP53" in results[0].snippet
    assert "BRCA1" in results[1].snippet
