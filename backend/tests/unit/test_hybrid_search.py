"""Tests for the hybrid search orchestrator merging vector and lexical signals."""

from __future__ import annotations

import os
from uuid import UUID, uuid4

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
                "TRUNCATE TABLE chunk_search, pdf_embeddings, pdf_chunks, pdf_documents RESTART IDENTITY CASCADE"
            )
        )
    yield


def _make_vector(seed: float) -> list[float]:
    return [seed + (index * 0.001) for index in range(1536)]


@pytest.fixture
def hybrid_document(session_factory):
    with session_factory() as session:
        doc = PDFDocument(
            filename="hybrid.pdf",
            file_path="/tmp/hybrid.pdf",
            file_hash=uuid4().hex[:32],
            content_hash_normalized=uuid4().hex[:32],
            file_size=4096,
            page_count=2,
        )
        session.add(doc)
        session.flush()

        texts = [
            "BRCA1 is associated with breast cancer and DNA repair.",
            "TP53 cancer cancer progression involves tumor suppressors.",
            "EGFR inhibitors target epidermal growth factor receptor pathways.",
        ]

        chunk_ids: list[UUID] = []
        for index, body in enumerate(texts):
            chunk = PDFChunk(
                pdf_id=doc.id,
                chunk_index=index,
                text=body,
                element_type="NarrativeText",
                page_start=1,
                page_end=1,
                chunk_hash=uuid4().hex[:32],
                token_count=len(body.split()),
                section_path="Introduction" if index == 0 else "Results",
            )
            session.add(chunk)
            session.flush()
            chunk_ids.append(chunk.id)

            session.execute(
                text(
                    """
                    INSERT INTO chunk_search (id, chunk_id, search_vector, text_length, lang)
                    VALUES (:id, :chunk_id, to_tsvector('english', :body), :length, 'english')
                    """
                ),
                {
                    "id": str(uuid4()),
                    "chunk_id": str(chunk.id),
                    "body": body,
                    "length": len(body),
                },
            )

            embedding = PDFEmbedding(
                pdf_id=doc.id,
                chunk_id=chunk.id,
                embedding=_make_vector(0.1 + index * 0.1),
                model_name="text-embedding-3-small",
                model_version="1.0",
                dimensions=1536,
            )
            session.add(embedding)

        session.commit()

        return {
            "document_id": doc.id,
            "chunk_ids": chunk_ids,
        }


def test_hybrid_search_merges_candidates(engine: Engine, hybrid_document):
    """Hybrid search should blend vector and lexical rankings into a single list."""

    from lib.hybrid_search import HybridSearch  # noqa: PLC0415

    search = HybridSearch(engine=engine, vector_model="text-embedding-3-small")

    response = search.query(
        pdf_id=hybrid_document["document_id"],
        embedding=_make_vector(0.1),
        query="cancer",
        vector_top_k=3,
        lexical_top_k=3,
        max_results=3,
        vector_weight=0.7,
    )

    assert response.metrics.vector_candidates == 3
    assert response.metrics.lexical_candidates == 2
    assert response.metrics.overlap_count == 2
    assert response.metrics.final_count == len(response.results) == 3

    ordered_ids = [result.chunk_id for result in response.results]
    assert ordered_ids[0] == hybrid_document["chunk_ids"][0]
    assert response.results[0].source == "both"
    assert response.results[2].source == "vector"
    assert response.results[0].page == 1
    assert all(result.text for result in response.results)
    assert response.results[0].score >= response.results[1].score >= 0
