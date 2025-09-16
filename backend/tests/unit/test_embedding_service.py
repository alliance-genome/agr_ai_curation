"""TDD tests for the embedding service with model versioning and idempotency."""

from __future__ import annotations

import os
from typing import List
from uuid import uuid4
from unittest.mock import MagicMock

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
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as exc:  # pragma: no cover - infrastructure failure
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


def _make_document(session_factory) -> PDFDocument:
    with session_factory() as session:
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/tmp/test.pdf",
            file_hash=uuid4().hex[:32],
            content_hash_normalized=uuid4().hex[:32],
            file_size=4096,
            page_count=2,
        )
        session.add(pdf)
        session.commit()
        session.refresh(pdf)
        return pdf


def _make_chunks(session_factory, pdf: PDFDocument) -> List[PDFChunk]:
    texts = ["This is chunk one.", "This is chunk two."]
    chunks: List[PDFChunk] = []
    with session_factory() as session:
        for idx, text_value in enumerate(texts):
            chunk = PDFChunk(
                pdf_id=pdf.id,
                chunk_index=idx,
                text=text_value,
                element_type="NarrativeText",
                page_start=1,
                page_end=1,
                chunk_hash=uuid4().hex[:32],
                token_count=5,
            )
            session.add(chunk)
            session.flush()
            session.refresh(chunk)
            chunks.append(chunk)
        session.commit()
    return chunks


def _vector(seed: float) -> List[float]:
    base = seed
    return [base + i for i in range(1536)]


def _service(session_factory, embedding_client):
    from lib.embedding_service import (
        EmbeddingService,
        EmbeddingModelConfig,
    )  # noqa: PLC0415

    config = EmbeddingModelConfig(
        name="text-embedding-3-small",
        dimensions=1536,
        default_version="1.0",
        max_batch_size=128,
    )
    return EmbeddingService(
        session_factory=session_factory,
        embedding_client=embedding_client,
        models={config.name: config},
    )


def test_embed_pdf_persists_embeddings_with_version(session_factory):
    pdf = _make_document(session_factory)
    chunks = _make_chunks(session_factory, pdf)

    embeddings = [_vector(0.1), _vector(1.1)]
    client = MagicMock()
    client.embed_texts.return_value = embeddings

    service = _service(session_factory, client)

    summary = service.embed_pdf(
        pdf_id=pdf.id,
        model_name="text-embedding-3-small",
        version="2025-01-01",
        batch_size=32,
    )

    client.embed_texts.assert_called_once_with(
        [chunk.text for chunk in chunks], model="text-embedding-3-small"
    )
    assert summary["embedded"] == 2
    assert summary["skipped"] == 0
    assert summary["model"] == "text-embedding-3-small"
    assert summary["version"] == "2025-01-01"

    with session_factory() as session:
        stored = (
            session.query(PDFEmbedding)
            .filter(PDFEmbedding.pdf_id == pdf.id)
            .order_by(PDFEmbedding.chunk_id)
            .all()
        )
        assert len(stored) == 2
        for idx, row in enumerate(stored):
            assert row.model_name == "text-embedding-3-small"
            assert row.model_version == "2025-01-01"
            assert row.dimensions == 1536
            assert list(row.embedding[:3]) == embeddings[idx][:3]

        doc = session.get(PDFDocument, pdf.id)
        assert doc.embeddings_generated is True
        assert {
            "model": "text-embedding-3-small",
            "version": "2025-01-01",
        } in doc.embedding_models


def test_embed_pdf_skips_existing_embeddings(session_factory):
    pdf = _make_document(session_factory)
    _make_chunks(session_factory, pdf)

    initial_client = MagicMock()
    initial_client.embed_texts.return_value = [_vector(0.5), _vector(1.5)]
    service = _service(session_factory, initial_client)
    service.embed_pdf(pdf_id=pdf.id, model_name="text-embedding-3-small", version="1.0")

    initial_client.embed_texts.reset_mock()

    new_client = MagicMock()
    new_client.embed_texts.return_value = [_vector(2.0), _vector(3.0)]
    service = _service(session_factory, new_client)

    summary = service.embed_pdf(
        pdf_id=pdf.id,
        model_name="text-embedding-3-small",
        version="1.0",
    )

    new_client.embed_texts.assert_not_called()
    assert summary["embedded"] == 0
    assert summary["skipped"] == 2


def test_embed_pdf_force_recreates_embeddings(session_factory):
    pdf = _make_document(session_factory)
    _make_chunks(session_factory, pdf)

    client = MagicMock()
    client.embed_texts.return_value = [_vector(0.2), _vector(0.3)]
    service = _service(session_factory, client)
    service.embed_pdf(pdf_id=pdf.id, model_name="text-embedding-3-small", version="1.0")

    client.embed_texts.reset_mock()
    client.embed_texts.return_value = [_vector(10.0), _vector(20.0)]

    summary = service.embed_pdf(
        pdf_id=pdf.id,
        model_name="text-embedding-3-small",
        version="2.0",
        force=True,
    )

    client.embed_texts.assert_called_once()
    assert summary["embedded"] == 2
    assert summary["skipped"] == 0
    assert summary["version"] == "2.0"

    with session_factory() as session:
        stored_versions = {
            row.model_version
            for row in session.query(PDFEmbedding).filter_by(pdf_id=pdf.id)
        }
        assert stored_versions == {"2.0"}
