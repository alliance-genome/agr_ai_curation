"""Unit tests for text-only document behavior in PDF viewer metadata APIs."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.api import pdf_viewer


_DOC_ID = UUID("11111111-1111-1111-1111-111111111111")


class _FakeDb:
    def __init__(self, record):
        self.record = record
        self.commits = 0
        self.refreshed = []

    def execute(self, _statement):
        record = self.record

        class _Result:
            def scalar_one_or_none(self):
                return record

        return _Result()

    def commit(self):
        self.commits += 1

    def refresh(self, record):
        self.refreshed.append(record)


def _record(**file_overrides):
    main_file = {
        "page_count": 1,
        "file_size": 512,
        "file_hash": "a" * 64,
        "file_path": "user123/paper.pdf",
        "display_mode": "local_pdf",
    }
    main_file.update(file_overrides)
    return SimpleNamespace(
        id=_DOC_ID,
        filename="paper.pdf",
        upload_timestamp=datetime(2026, 6, 25, tzinfo=timezone.utc),
        last_accessed=datetime(2026, 6, 25, tzinfo=timezone.utc),
        user_id=7,
        document_kind="library",
        main_file_or_none=SimpleNamespace(**main_file),
    )


@pytest.fixture(autouse=True)
def _authenticated_owner(monkeypatch):
    monkeypatch.setattr(
        pdf_viewer,
        "provision_user",
        lambda _db, principal: SimpleNamespace(id=7, auth_sub=principal["sub"]),
    )
    monkeypatch.setattr(pdf_viewer, "principal_from_claims", lambda claims: claims)


def test_get_document_viewer_url_returns_null_for_text_document():
    db = _FakeDb(
        _record(
            file_path="document_sources/fake_provider/doc.md",
            display_mode="text",
            page_count=None,
        )
    )

    response = pdf_viewer.get_document_viewer_url(
        document_id=_DOC_ID,
        db=db,
        user={"sub": "user123"},
    )

    assert response.viewer_url is None
    assert response.viewer_mode == "text"
    assert db.commits == 1


def test_get_document_detail_includes_text_viewer_mode_without_viewer_url():
    db = _FakeDb(
        _record(
            file_path="document_sources/fake_provider/doc.md",
            display_mode="text",
            page_count=None,
        )
    )

    response = pdf_viewer.get_document_detail(
        document_id=_DOC_ID,
        db=db,
        user={"sub": "user123"},
    )

    assert response.viewer_url is None
    assert response.viewer_mode == "text"
    assert response.filename == "paper.pdf"
    assert response.page_count is None
    assert db.commits == 1
    assert db.refreshed == [db.record]


def test_get_document_detail_preserves_local_pdf_viewer_url():
    db = _FakeDb(_record())

    response = pdf_viewer.get_document_detail(
        document_id=_DOC_ID,
        db=db,
        user={"sub": "user123"},
    )

    assert response.viewer_url == f"/api/pdf-viewer/documents/{_DOC_ID}/content"
    assert response.viewer_mode == "local_pdf"


@pytest.mark.parametrize("page_count", [51, 100, 121])
def test_viewer_response_schema_serializes_stored_page_counts_above_legacy_limit(
    page_count,
):
    response = pdf_viewer.PDFDocumentSummary.model_validate(
        {
            "id": _DOC_ID,
            "filename": "paper.pdf",
            "page_count": page_count,
            "file_size": 512,
            "upload_timestamp": datetime(2026, 6, 25, tzinfo=timezone.utc),
            "viewer_url": f"/api/pdf-viewer/documents/{_DOC_ID}/content",
            "viewer_mode": "local_pdf",
            "has_main_file": True,
        }
    )

    assert response.page_count == page_count


def test_viewer_response_schema_serializes_size_above_current_admission_limit():
    stored_size = 550 * 1024 * 1024

    response = pdf_viewer.PDFDocumentSummary.model_validate(
        {
            "id": _DOC_ID,
            "filename": "paper.pdf",
            "page_count": 1,
            "file_size": stored_size,
            "upload_timestamp": datetime(2026, 6, 25, tzinfo=timezone.utc),
            "viewer_url": f"/api/pdf-viewer/documents/{_DOC_ID}/content",
            "viewer_mode": "local_pdf",
            "has_main_file": True,
        }
    )

    assert response.file_size == stored_size


# --- What the viewer API returns since P5, from real paper and file models ----------
# Frontend fixtures should copy these shapes: a text paper has no pages and no viewer
# URL, and a plain upload reports ``local_pdf`` (never a missing viewer mode).


def _paper(*, display_mode: str, page_count, file_path: str):
    from src.models.sql.pdf_document import PDFDocument, PDFDocumentFile

    paper = PDFDocument(
        id=_DOC_ID,
        user_id=7,
        filename="paper-name.pdf",
        upload_timestamp=datetime(2026, 11, 21, tzinfo=timezone.utc),
        last_accessed=datetime(2026, 11, 21, tzinfo=timezone.utc),
        status="completed",
        document_kind="library",
    )
    paper.files.append(PDFDocumentFile(
        role="main",
        display_name="paper-name.pdf",
        status="ready",
        tab_order=0,
        display_mode=display_mode,
        file_path=file_path,
        file_hash="b" * 64,
        file_size=2048,
        page_count=page_count,
    ))
    return paper


@pytest.mark.parametrize(
    "display_mode,page_count,file_path,expected_url",
    [
        ("text", None, "document_sources/fake_provider/doc.md", None),
        ("local_pdf", 3, "user123/paper.pdf", f"/api/pdf-viewer/documents/{_DOC_ID}/content"),
    ],
    ids=["text_paper", "plain_upload"],
)
def test_viewer_api_shapes_for_a_text_paper_and_a_plain_upload(
    display_mode, page_count, file_path, expected_url
):
    db = _FakeDb(_paper(display_mode=display_mode, page_count=page_count, file_path=file_path))
    user = {"sub": "user123"}

    detail = pdf_viewer.get_document_detail(document_id=_DOC_ID, db=db, user=user).model_dump(mode="json")
    url = pdf_viewer.get_document_viewer_url(document_id=_DOC_ID, db=db, user=user).model_dump(mode="json")

    assert {key: detail[key] for key in ("page_count", "viewer_url", "viewer_mode")} == {
        "page_count": page_count,
        "viewer_url": expected_url,
        "viewer_mode": display_mode,
    }
    assert url == {"viewer_url": expected_url, "viewer_mode": display_mode, "has_main_file": True}
    assert detail["has_main_file"] is True


def test_viewer_api_shape_for_a_paper_without_a_main_file():
    from tests.pdf_document_test_support import new_supplements_only_paper

    paper = new_supplements_only_paper(
        id=_DOC_ID, user_id=7, filename="supplements-only-paper",
        upload_timestamp=datetime(2026, 6, 25, tzinfo=timezone.utc),
        last_accessed=datetime(2026, 6, 25, tzinfo=timezone.utc), document_kind="library",
    )
    user = {"sub": "user123"}

    detail = pdf_viewer.get_document_detail(
        document_id=_DOC_ID, db=_FakeDb(paper), user=user
    ).model_dump(mode="json")
    url = pdf_viewer.get_document_viewer_url(
        document_id=_DOC_ID, db=_FakeDb(paper), user=user
    ).model_dump(mode="json")

    # The shape the frontend fixtures copy: an explicit has_main_file, every main-file field null.
    assert {key: detail[key] for key in (
        "has_main_file", "viewer_mode", "viewer_url", "page_count", "file_size", "file_hash"
    )} == {
        "has_main_file": False, "viewer_mode": None, "viewer_url": None,
        "page_count": None, "file_size": None, "file_hash": None,
    }
    assert url == {"viewer_url": None, "viewer_mode": None, "has_main_file": False}
