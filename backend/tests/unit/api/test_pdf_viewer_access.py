"""Authorization regression tests for PDF viewer metadata and bytes."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api import pdf_viewer
from src.models.sql.database import get_db


DOCUMENT_ID = UUID("22222222-2222-2222-2222-222222222222")


class _Result:
    def __init__(self, *, record=None, scalar=None, records=None):
        self._record = record
        self._scalar = scalar
        self._records = records or []

    def scalar_one_or_none(self):
        return self._record

    def scalar_one(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._records


class _Db:
    def __init__(self, *results):
        self.results = list(results)
        self.statements = []
        self.commits = 0

    def execute(self, statement):
        self.statements.append(statement)
        return self.results.pop(0)

    def commit(self):
        self.commits += 1

    def refresh(self, _record):
        return None


def _record(*, owner_id=7, viewer_mode="local_pdf", file_path="owner/pdf/paper.pdf"):
    timestamp = datetime(2026, 7, 11, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=DOCUMENT_ID,
        filename="paper.pdf",
        page_count=3,
        file_size=512,
        upload_timestamp=timestamp,
        last_accessed=timestamp,
        file_hash="a" * 64,
        file_path=file_path,
        viewer_mode=viewer_mode,
        user_id=owner_id,
    )


@pytest.fixture(autouse=True)
def _principal(monkeypatch):
    monkeypatch.setattr(pdf_viewer, "principal_from_claims", lambda claims: claims)
    monkeypatch.setattr(
        pdf_viewer,
        "provision_user",
        lambda _db, principal: SimpleNamespace(id=7, auth_sub=principal["sub"]),
    )


@pytest.mark.parametrize(
    "path",
    [
        "/api/pdf-viewer/documents",
        f"/api/pdf-viewer/documents/{DOCUMENT_ID}",
        f"/api/pdf-viewer/documents/{DOCUMENT_ID}/url",
        f"/api/pdf-viewer/documents/{DOCUMENT_ID}/content",
    ],
)
def test_pdf_viewer_document_routes_require_authentication(monkeypatch, path):
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("AUTH_PROVIDER", raising=False)
    app = FastAPI()
    app.include_router(pdf_viewer.router)
    app.dependency_overrides[get_db] = lambda: _Db()

    response = TestClient(app).get(path)

    assert response.status_code == 401


def test_list_is_scoped_to_authenticated_owner_at_query_boundary():
    own_record = _record()
    db = _Db(_Result(scalar=1), _Result(records=[own_record]))

    response = pdf_viewer.list_documents(
        limit=100,
        offset=0,
        db=db,
        user={"sub": "owner"},
    )

    assert response.total == 1
    assert [document.id for document in response.documents] == [DOCUMENT_ID]
    assert all("pdf_documents.user_id" in str(statement) for statement in db.statements)


@pytest.mark.parametrize("owner_id", [8, None])
@pytest.mark.parametrize(
    "operation",
    [
        pdf_viewer.get_document_detail,
        pdf_viewer.get_document_viewer_url,
        pdf_viewer.get_document_pdf_content,
    ],
)
def test_cross_user_and_legacy_unowned_documents_are_consistently_forbidden(
    owner_id,
    operation,
):
    db = _Db(_Result(record=_record(owner_id=owner_id)))

    with pytest.raises(HTTPException) as exc_info:
        operation(document_id=DOCUMENT_ID, db=db, user={"sub": "owner"})

    assert exc_info.value.status_code == 403


@pytest.mark.parametrize(
    "operation",
    [
        pdf_viewer.get_document_detail,
        pdf_viewer.get_document_viewer_url,
        pdf_viewer.get_document_pdf_content,
    ],
)
def test_missing_document_status_is_consistent(operation):
    db = _Db(_Result(record=None))

    with pytest.raises(HTTPException) as exc_info:
        operation(document_id=DOCUMENT_ID, db=db, user={"sub": "owner"})

    assert exc_info.value.status_code == 404


def test_owned_pdf_content_uses_validated_tenant_path(monkeypatch, tmp_path):
    pdf_path = tmp_path / "owner" / "pdf" / "paper.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.7\nowned")
    db = _Db(_Result(record=_record()))
    monkeypatch.setattr(pdf_viewer, "get_pdf_storage_path", lambda: tmp_path)

    response = pdf_viewer.get_document_pdf_content(
        document_id=DOCUMENT_ID,
        db=db,
        user={"sub": "owner"},
    )

    assert response.path == pdf_path
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")


def test_owned_pdf_content_uses_canonical_provisioned_storage_identity(
    monkeypatch,
    tmp_path,
):
    pdf_path = tmp_path / "canonical-owner" / "pdf" / "paper.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.7\nowned")
    db = _Db(_Result(record=_record(file_path="canonical-owner/pdf/paper.pdf")))
    monkeypatch.setattr(pdf_viewer, "get_pdf_storage_path", lambda: tmp_path)
    monkeypatch.setattr(
        pdf_viewer,
        "provision_user",
        lambda _db, _principal: SimpleNamespace(id=7, auth_sub="canonical-owner"),
    )

    response = pdf_viewer.get_document_pdf_content(
        document_id=DOCUMENT_ID,
        db=db,
        user={"sub": "claims-owner"},
    )

    assert response.path == pdf_path


def test_text_only_document_never_exposes_pdf_bytes():
    db = _Db(_Result(record=_record(viewer_mode="text_only")))

    with pytest.raises(HTTPException) as exc_info:
        pdf_viewer.get_document_pdf_content(
            document_id=DOCUMENT_ID,
            db=db,
            user={"sub": "owner"},
        )

    assert exc_info.value.status_code == 404


def test_main_application_has_no_public_uploads_route():
    from main import app

    assert not any(getattr(route, "path", None) == "/uploads" for route in app.routes)
