"""Batch embedding tests ensuring multi-model service respects batch sizing."""

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
            filename="batch.pdf",
            file_path="/tmp/batch.pdf",
            file_hash=uuid4().hex[:32],
            content_hash_normalized=uuid4().hex[:32],
            file_size=4096,
            page_count=3,
        )
        session.add(pdf)
        session.commit()
        session.refresh(pdf)
        return pdf


def _make_chunks(session_factory, pdf: PDFDocument, count: int) -> List[PDFChunk]:
    chunks: List[PDFChunk] = []
    with session_factory() as session:
        for idx in range(count):
            chunk = PDFChunk(
                pdf_id=pdf.id,
                chunk_index=idx,
                text=f"Chunk {idx}",
                element_type="NarrativeText",
                page_start=1,
                page_end=1,
                chunk_hash=uuid4().hex[:32],
                token_count=10,
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
        default_batch_size=64,
    )
    return EmbeddingService(
        session_factory=session_factory,
        embedding_client=embedding_client,
        models={config.name: config},
    )


def test_batches_split_requests_and_persist_all(session_factory):
    pdf = _make_document(session_factory)
    chunks = _make_chunks(session_factory, pdf, count=5)

    client = MagicMock()
    client.embed_texts.side_effect = [
        [_vector(0.1), _vector(0.2)],
        [_vector(1.1), _vector(1.2)],
        [_vector(2.1)],
    ]

    service = _service(session_factory, client)

    summary = service.embed_pdf(
        pdf_id=pdf.id,
        model_name="text-embedding-3-small",
        version="v1",
        batch_size=2,
        force=True,
    )

    calls = client.embed_texts.call_args_list
    assert len(calls) == 3
    expected_batches = [
        [chunk.text for chunk in chunks[0:2]],
        [chunk.text for chunk in chunks[2:4]],
        [chunk.text for chunk in chunks[4:5]],
    ]
    actual_batches = [list(call.args[0]) for call in calls]
    assert actual_batches == expected_batches

    assert summary == {
        "embedded": 5,
        "skipped": 0,
        "model": "text-embedding-3-small",
        "version": "v1",
    }

    with session_factory() as session:
        stored = session.query(PDFEmbedding).filter_by(pdf_id=pdf.id).all()
        assert len(stored) == 5
        assert all(row.model_version == "v1" for row in stored)


def test_batch_size_over_max_raises(session_factory):
    pdf = _make_document(session_factory)
    _make_chunks(session_factory, pdf, count=2)

    client = MagicMock()
    service = _service(session_factory, client)

    with pytest.raises(ValueError):
        service.embed_pdf(
            pdf_id=pdf.id,
            model_name="text-embedding-3-small",
            version="v1",
            batch_size=1024,
        )
