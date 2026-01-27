"""Integration test covering end-to-end metadata exposure for PDF viewer."""

from uuid import uuid4
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import importlib.util

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument


@pytest.fixture(scope="module")
def viewer_app() -> FastAPI:
    module_path = Path(__file__).resolve().parents[2] / 'src' / 'api' / 'pdf_viewer.py'
    spec = importlib.util.spec_from_file_location('tests.pdf_viewer', module_path)
    if spec is None or spec.loader is None:
        pytest.fail("Unable to load pdf_viewer module for integration test")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    app = FastAPI()
    app.include_router(module.router)
    return app


@pytest.fixture
def client(viewer_app: FastAPI) -> TestClient:
    return TestClient(viewer_app)


@pytest.fixture
def seeded_document():
    session = SessionLocal()
    document_id = uuid4()
    record = PDFDocument(
        id=document_id,
        filename="integration.pdf",
        file_path=f"{document_id}/integration.pdf",
        file_hash="f" * 32,
        file_size=4096,
        page_count=7,
    )
    session.add(record)
    session.commit()

    try:
        yield record
    finally:
        session.delete(record)
        session.commit()
        session.close()


def test_upload_flow_exposes_metadata_via_api(client: TestClient, seeded_document: PDFDocument):
    """A persisted PDF document should surface through list/detail/url endpoints."""
    list_response = client.get("/api/pdf-viewer/documents")
    assert list_response.status_code == 200

    list_payload = list_response.json()
    documents = list_payload.get("documents", [])

    matching = next(
        (doc for doc in documents if doc.get("id") == str(seeded_document.id)),
        None,
    )
    assert matching is not None, "Seeded document must appear in list endpoint"
    assert matching.get("viewer_url", "").startswith("/uploads/")

    detail_response = client.get(f"/api/pdf-viewer/documents/{seeded_document.id}")
    assert detail_response.status_code == 200

    detail = detail_response.json()
    assert detail.get("filename") == seeded_document.filename
    assert detail.get("file_hash") == seeded_document.file_hash
    assert detail.get("viewer_url", "").endswith("integration.pdf")

    url_response = client.get(f"/api/pdf-viewer/documents/{seeded_document.id}/url")
    assert url_response.status_code == 200
    url_payload = url_response.json()
    assert url_payload.get("viewer_url") == detail.get("viewer_url")
