"""Tests for PDF upload endpoint."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.pdf_ingest_service import get_pdf_ingest_service

client = TestClient(app)


class FakeIngestService:
    def __init__(self):
        self.calls = []

    def ingest(self, *, file_path: Path, original_filename: str):
        self.calls.append((file_path, original_filename))
        return uuid4(), True


@pytest.fixture
def fake_ingestor():
    return FakeIngestService()


@pytest.fixture(autouse=True)
def override_pdf_ingest(fake_ingestor):
    app.dependency_overrides[get_pdf_ingest_service] = lambda: fake_ingestor
    yield
    app.dependency_overrides.pop(get_pdf_ingest_service, None)


def test_upload_pdf_invokes_ingest_service(tmp_path: Path, fake_ingestor):
    sample = tmp_path / "sample.pdf"
    sample.write_bytes(b"PDF")

    with sample.open("rb") as handle:
        response = client.post(
            "/api/pdf/upload",
            files={"file": ("sample.pdf", handle, "application/pdf")},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["pdf_id"]
    assert payload["filename"] == "sample.pdf"
    assert payload["viewer_url"] == "/uploads/sample.pdf"
    assert payload["reused"] is False
    assert fake_ingestor.calls
    stored_path, original_name = fake_ingestor.calls[0]
    assert original_name == "sample.pdf"
    assert stored_path.exists()
