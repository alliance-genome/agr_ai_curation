"""Tests for the PDF data browser API using a fake repository."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.pdf_data import get_repository

client = TestClient(app)


class FakeRepository:
    def __init__(self):
        self.document_id = uuid4()
        self.run_id = uuid4()
        self.deleted_ids: list = []
        self.documents = [
            SimpleNamespace(
                id=self.document_id,
                filename="sample.pdf",
                upload_timestamp="2025-09-17T00:00:00Z",
                last_accessed="2025-09-17T00:00:00Z",
                page_count=10,
                chunk_count=2,
                table_count=0,
                figure_count=0,
                embeddings_generated=True,
                file_size=1234,
                extraction_method="UNSTRUCTURED_FAST",
                preproc_version="v1",
                meta_data={"title": "Sample"},
                file_path="/app/uploads/sample.pdf",
            )
        ]
        self.chunks = [
            SimpleNamespace(
                id=uuid4(),
                chunk_index=0,
                text="Chunk preview text",
                page_start=1,
                page_end=1,
                section_path="Intro",
                element_type="NarrativeText",
                is_reference=False,
                is_caption=False,
                is_table=False,
                is_figure=False,
                token_count=42,
            )
        ]
        self.runs = [
            SimpleNamespace(
                id=self.run_id,
                workflow_name="general_supervisor",
                input_query="What is the summary?",
                status="COMPLETED",
                started_at="2025-09-17T00:00:01Z",
                completed_at="2025-09-17T00:00:02Z",
                latency_ms=1234,
                specialists_invoked=["general"],
            )
        ]
        self.nodes = [
            SimpleNamespace(
                id=uuid4(),
                graph_run_id=self.run_id,
                node_key="intent_router",
                node_type="tool",
                status="COMPLETED",
                started_at="2025-09-17T00:00:01Z",
                completed_at="2025-09-17T00:00:01Z",
                latency_ms=120,
                error=None,
            )
        ]
        self.embeddings = [
            {
                "model_name": "text-embedding-3-small",
                "count": 120,
                "latest_created_at": "2025-09-17T00:00:03Z",
                "model_version": "1.0",
                "dimensions": 1536,
                "total_tokens": 6000,
                "vector_memory_bytes": 737280,
                "estimated_cost_usd": 0.00012,
                "avg_processing_time_ms": 12.5,
            }
        ]

    def list_documents(self, limit: int = 50):
        return self.documents[:limit]

    def get_document(self, pdf_id):
        for doc in self.documents:
            if doc.id == pdf_id:
                return doc
        return None

    def list_chunks(self, pdf_id, limit: int = 1000):
        return self.chunks[:limit]

    def list_langgraph_runs(self, pdf_id):
        return self.runs

    def list_langgraph_node_runs(self, graph_run_id):
        return self.nodes

    def list_embedding_summary(self, pdf_id):
        return self.embeddings

    def delete_document(self, pdf_id):
        if pdf_id == self.document_id:
            self.deleted_ids.append(pdf_id)
            return True
        return False


@pytest.fixture(autouse=True)
def override_repository():
    fake_repo = FakeRepository()
    app.dependency_overrides[get_repository] = lambda: fake_repo
    yield fake_repo
    app.dependency_overrides.pop(get_repository, None)


def test_list_documents_returns_repository_data(override_repository):
    response = client.get("/api/pdf-data/documents")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["filename"] == "sample.pdf"
    assert payload[0]["viewer_url"] == "/uploads/sample.pdf"


def test_document_detail_endpoint(override_repository):
    doc_id = override_repository.document_id
    response = client.get(f"/api/pdf-data/documents/{doc_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["extraction_method"] == "UNSTRUCTURED_FAST"
    assert payload["meta_data"]["title"] == "Sample"
    assert payload["viewer_url"] == "/uploads/sample.pdf"


def test_document_viewer_url_endpoint(override_repository):
    doc_id = override_repository.document_id
    response = client.get(f"/api/pdf-data/documents/{doc_id}/url")
    assert response.status_code == 200
    payload = response.json()
    assert payload["viewer_url"] == "/uploads/sample.pdf"


def test_chunk_listing_endpoint(override_repository):
    doc_id = override_repository.document_id
    response = client.get(f"/api/pdf-data/documents/{doc_id}/chunks")
    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["text_preview"] == "Chunk preview text"


def test_langgraph_runs_and_nodes_endpoints(override_repository):
    doc_id = override_repository.document_id
    run_id = override_repository.run_id

    runs_resp = client.get(f"/api/pdf-data/documents/{doc_id}/langgraph-runs")
    assert runs_resp.status_code == 200
    assert runs_resp.json()[0]["id"] == str(run_id)

    nodes_resp = client.get(f"/api/pdf-data/langgraph-runs/{run_id}/nodes")
    assert nodes_resp.status_code == 200
    assert nodes_resp.json()[0]["node_key"] == "intent_router"


def test_embeddings_endpoint(override_repository):
    doc_id = override_repository.document_id
    response = client.get(f"/api/pdf-data/documents/{doc_id}/embeddings")
    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["model_name"] == "text-embedding-3-small"
    assert payload[0]["estimated_cost_usd"] == 0.00012


def test_delete_document_endpoint(override_repository):
    doc_id = override_repository.document_id
    response = client.delete(f"/api/pdf-data/documents/{doc_id}")
    assert response.status_code == 204
    assert override_repository.deleted_ids == [doc_id]


def test_delete_document_not_found(override_repository):
    response = client.delete(f"/api/pdf-data/documents/{uuid4()}")
    assert response.status_code == 404
