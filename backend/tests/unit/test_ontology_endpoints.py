"""Tests for ontology management API endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import List

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import IngestionState
from app.repositories.ontology_repository import OntologyStatusRow
from app.routers.ontology import get_repository
import app.routers.ontology as ontology_router


client = TestClient(app)


class FakeOntologyRepository:
    def __init__(self) -> None:
        now = datetime.utcnow()
        self.status_row = OntologyStatusRow(
            ontology_type="disease",
            source_id="all",
            state=IngestionState.READY,
            created_at=now,
            updated_at=now,
            message={"stage": "ready"},
            term_count=10,
            relation_count=9,
            chunk_count=8,
            embedded_count=8,
        )
        self.list_calls: int = 0
        self.status_calls: int = 0

    def list_statuses(self) -> List[OntologyStatusRow]:
        self.list_calls += 1
        return [self.status_row]

    def get_status(
        self, ontology_type: str, source_id: str
    ) -> OntologyStatusRow | None:
        self.status_calls += 1
        if (
            ontology_type == self.status_row.ontology_type
            and source_id == self.status_row.source_id
        ):
            return self.status_row
        return None


@pytest.fixture(autouse=True)
def override_dependencies(monkeypatch):
    fake_repo = FakeOntologyRepository()

    app.dependency_overrides[get_repository] = lambda: fake_repo

    async def fake_run_in_threadpool(func, *args, **kwargs):
        return func(*args, **kwargs)

    def fake_ingest_ontology(**kwargs):
        fake_repo.status_row.state = IngestionState.READY
        fake_repo.status_row.updated_at = datetime.utcnow()
        fake_repo.status_row.term_count = 12
        fake_repo.status_row.relation_count = 11
        fake_repo.status_row.chunk_count = 10
        fake_repo.status_row.embedded_count = 10
        fake_repo.status_row.message = {
            "stage": "ready",
            "inserted": {"terms": 2, "relations": 1, "chunks": 2},
        }
        return {
            "inserted": 2,
            "relations": 1,
            "deleted_chunks": 0,
            "deleted_terms": 0,
            "deleted_relations": 0,
            "embedded": 2,
            "file_info": {
                "path": "fake.obo",
                "size_bytes": 10,
                "modified_at": datetime.utcnow().isoformat() + "Z",
                "sha256": "deadbeef",
            },
            "embedding_summary": {"embedded": 2},
            "insertion_summary": {"terms": 2, "relations": 1, "chunks": 2},
            "deletion_summary": {"terms": 0, "relations": 0, "chunks": 0},
        }

    monkeypatch.setattr(ontology_router, "run_in_threadpool", fake_run_in_threadpool)
    monkeypatch.setattr(ontology_router, "ingest_ontology", fake_ingest_ontology)

    yield fake_repo

    app.dependency_overrides.pop(get_repository, None)


def test_list_ingestions_returns_status_rows():
    response = client.get("/api/ontology/ingestions")
    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["ontology_type"] == "disease"
    assert payload[0]["term_count"] == 10
    assert payload[0]["message"]["stage"] == "ready"


def test_get_single_ingestion_returns_status():
    response = client.get("/api/ontology/ingestions/disease/all")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source_id"] == "all"
    assert payload["relation_count"] == 9


def test_trigger_ingestion_runs_job_and_returns_summary():
    response = client.post(
        "/api/ontology/ingestions",
        json={"ontology_type": "disease", "source_id": "all"},
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["summary"]["inserted"] == 2
    assert payload["summary"]["embedded"] == 2
    assert payload["status"]["state"] == IngestionState.READY.value
    assert payload["status"]["term_count"] == 12
    assert payload["status"]["embedded_count"] == 10


def test_run_embeddings_endpoint(override_dependencies):
    response = client.post("/api/ontology/ingestions/disease/all/embeddings")
    assert response.status_code == 202
    payload = response.json()
    assert payload["summary"]["queued"] is True
