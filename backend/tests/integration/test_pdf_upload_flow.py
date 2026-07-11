"""Integration test covering end-to-end metadata exposure for PDF viewer."""

from uuid import uuid4
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import importlib.util

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.lib.pdf_limits import MAX_PDF_FILE_SIZE_BYTES
from src.api import auth
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.user import User

INTEGRATION_AUTH_SUB = "integration-pdf-viewer-user"

DEBBIE_PDF_FILE_SIZE_BYTES = 77_585_577
PREVIOUS_LIMIT_REGRESSION_PDF_FILE_SIZE_BYTES = 120 * 1024 * 1024


@pytest.fixture(scope="module")
def viewer_app() -> FastAPI:
    module_path = Path(__file__).resolve().parents[2] / 'src' / 'api' / 'pdf_viewer.py'
    spec = importlib.util.spec_from_file_location('tests.pdf_viewer', module_path)
    if spec is None or spec.loader is None:
        pytest.fail("Unable to load pdf_viewer module for integration test")
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    app = FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[auth.auth.get_user] = lambda: {
        "sub": INTEGRATION_AUTH_SUB
    }
    setattr(
        module,
        "provision_user",
        lambda db, _principal: db.query(User).filter_by(
            auth_sub=INTEGRATION_AUTH_SUB
        ).one(),
    )
    return app


@pytest.fixture
def client(viewer_app: FastAPI) -> TestClient:
    return TestClient(viewer_app)


@pytest.fixture(params=[DEBBIE_PDF_FILE_SIZE_BYTES, PREVIOUS_LIMIT_REGRESSION_PDF_FILE_SIZE_BYTES])
def seeded_document(request):
    session = SessionLocal()
    owner = session.query(User).filter_by(auth_sub=INTEGRATION_AUTH_SUB).one_or_none()
    if owner is None:
        owner = User(auth_sub=INTEGRATION_AUTH_SUB, email="pdf-viewer@example.test")
        session.add(owner)
        session.commit()
        session.refresh(owner)
    document_id = uuid4()
    file_size = request.param
    record = PDFDocument(
        id=document_id,
        filename=f"integration-{file_size}.pdf",
        file_path=f"{document_id}/integration-{file_size}.pdf",
        file_hash=(uuid4().hex * 2)[:64],
        file_size=file_size,
        page_count=7,
        user_id=owner.id,
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
    assert seeded_document.file_size < MAX_PDF_FILE_SIZE_BYTES

    list_response = client.get("/api/pdf-viewer/documents")
    assert list_response.status_code == 200

    list_payload = list_response.json()
    documents = list_payload.get("documents", [])

    matching = next(
        (doc for doc in documents if doc.get("id") == str(seeded_document.id)),
        None,
    )
    assert matching is not None, "Seeded document must appear in list endpoint"
    assert matching.get("file_size") == seeded_document.file_size
    assert matching.get("viewer_url") == (
        f"/api/pdf-viewer/documents/{seeded_document.id}/content"
    )

    detail_response = client.get(f"/api/pdf-viewer/documents/{seeded_document.id}")
    assert detail_response.status_code == 200

    detail = detail_response.json()
    assert detail.get("filename") == seeded_document.filename
    assert detail.get("file_hash") == seeded_document.file_hash
    assert detail.get("file_size") == seeded_document.file_size
    assert detail.get("viewer_url") == (
        f"/api/pdf-viewer/documents/{seeded_document.id}/content"
    )

    url_response = client.get(f"/api/pdf-viewer/documents/{seeded_document.id}/url")
    assert url_response.status_code == 200
    url_payload = url_response.json()
    assert url_payload.get("viewer_url") == detail.get("viewer_url")
