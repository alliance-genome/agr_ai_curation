"""TDD-RED: Tests for vector search latency and ordering."""

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
                "TRUNCATE TABLE pdf_embeddings, pdf_chunks, pdf_documents RESTART IDENTITY CASCADE"
            )
        )
    yield


def _make_vector(seed: float) -> List[float]:
    return [seed + (i * 0.001) for i in range(1536)]


@pytest.fixture
def sample_embeddings(session_factory):
    with session_factory() as session:
        doc = PDFDocument(
            filename="vector.pdf",
            file_path="/tmp/vector.pdf",
            file_hash=uuid4().hex[:32],
            content_hash_normalized=uuid4().hex[:32],
            file_size=1024,
            page_count=1,
        )
        session.add(doc)
        session.flush()

        chunks = []
        for idx in range(3):
            chunk = PDFChunk(
                pdf_id=doc.id,
                chunk_index=idx,
                text=f"Chunk {idx}",
                element_type="NarrativeText",
                page_start=1,
                page_end=1,
                chunk_hash=uuid4().hex[:32],
                token_count=50,
            )
            session.add(chunk)
            session.flush()
            chunks.append(chunk)

            embedding = PDFEmbedding(
                pdf_id=doc.id,
                chunk_id=chunk.id,
                embedding=_make_vector(0.1 + idx),
                model_name="text-embedding-3-small",
                model_version="1.0",
                dimensions=1536,
            )
            session.add(embedding)

        session.commit()

        return {
            "document_id": doc.id,
            "chunk_ids": [chunk.id for chunk in chunks],
        }


def test_vector_search_returns_top_k_under_latency(engine: Engine, sample_embeddings):
    """Vector search should return ordered chunks quickly."""

    from lib.vector_search import VectorSearch  # noqa: PLC0415

    search = VectorSearch(engine, model_name="text-embedding-3-small")

    start = time.perf_counter()
    results = search.query(
        pdf_id=sample_embeddings["document_id"],
        embedding=_make_vector(0.1),
        top_k=2,
    )
    duration_ms = (time.perf_counter() - start) * 1000

    assert duration_ms < 100
    assert len(results) == 2
    assert results[0].chunk_id == sample_embeddings["chunk_ids"][0]
    assert results[1].chunk_id == sample_embeddings["chunk_ids"][1]
