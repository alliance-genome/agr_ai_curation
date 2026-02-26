"""Contract tests for PDF extraction health endpoint auth requirements."""

import pytest


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("EMBEDDING_TOKEN_PREFLIGHT_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_MODEL_TOKEN_LIMIT", "8191")
    monkeypatch.setenv("EMBEDDING_TOKEN_SAFETY_MARGIN", "500")
    monkeypatch.setenv("CONTENT_PREVIEW_CHARS", "1600")

    from fastapi.testclient import TestClient
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from main import app

    return TestClient(app)


def test_pdf_extraction_health_requires_authentication(client):
    response = client.get("/weaviate/documents/pdf-extraction-health")
    assert response.status_code == 401


def test_pdf_extraction_wake_requires_authentication(client):
    response = client.post("/weaviate/documents/pdf-extraction-wake")
    assert response.status_code == 401
