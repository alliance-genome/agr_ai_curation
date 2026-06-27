"""Unit tests for text-only document behavior in PDF viewer metadata APIs."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

from src.api import pdf_viewer


_DOC_ID = UUID("11111111-1111-1111-1111-111111111111")


class _FakeDb:
    def __init__(self, record):
        self.record = record
        self.commits = 0
        self.refreshed = []

    def get(self, _model, document_id):
        if document_id == _DOC_ID:
            return self.record
        return None

    def commit(self):
        self.commits += 1

    def refresh(self, record):
        self.refreshed.append(record)


def _record(**overrides):
    payload = {
        "id": _DOC_ID,
        "filename": "paper.pdf",
        "page_count": 1,
        "file_size": 512,
        "upload_timestamp": datetime(2026, 6, 25, tzinfo=timezone.utc),
        "last_accessed": datetime(2026, 6, 25, tzinfo=timezone.utc),
        "file_hash": "a" * 64,
        "file_path": "user123/paper.pdf",
        "viewer_mode": "local_pdf",
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_get_document_viewer_url_returns_null_for_text_only_document():
    db = _FakeDb(
        _record(
            file_path="document_sources/fake_provider/doc.md",
            viewer_mode="text_only",
        )
    )

    response = pdf_viewer.get_document_viewer_url(document_id=_DOC_ID, db=db)

    assert response.viewer_url is None
    assert response.viewer_mode == "text_only"
    assert db.commits == 1


def test_get_document_detail_includes_text_only_viewer_mode_without_viewer_url():
    db = _FakeDb(
        _record(
            file_path="document_sources/fake_provider/doc.md",
            viewer_mode="text_only",
        )
    )

    response = pdf_viewer.get_document_detail(document_id=_DOC_ID, db=db)

    assert response.viewer_url is None
    assert response.viewer_mode == "text_only"
    assert response.filename == "paper.pdf"
    assert db.commits == 1
    assert db.refreshed == [db.record]


def test_get_document_detail_preserves_local_pdf_viewer_url():
    db = _FakeDb(_record())

    response = pdf_viewer.get_document_detail(document_id=_DOC_ID, db=db)

    assert response.viewer_url == "/uploads/user123/paper.pdf"
    assert response.viewer_mode == "local_pdf"

