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
        "user_id": 7,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


@pytest.fixture(autouse=True)
def _authenticated_owner(monkeypatch):
    monkeypatch.setattr(
        pdf_viewer,
        "provision_user",
        lambda _db, _principal: SimpleNamespace(id=7),
    )
    monkeypatch.setattr(pdf_viewer, "principal_from_claims", lambda claims: claims)


def test_get_document_viewer_url_returns_null_for_text_only_document():
    db = _FakeDb(
        _record(
            file_path="document_sources/fake_provider/doc.md",
            viewer_mode="text_only",
        )
    )

    response = pdf_viewer.get_document_viewer_url(
        document_id=_DOC_ID,
        db=db,
        user={"sub": "user123"},
    )

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

    response = pdf_viewer.get_document_detail(
        document_id=_DOC_ID,
        db=db,
        user={"sub": "user123"},
    )

    assert response.viewer_url is None
    assert response.viewer_mode == "text_only"
    assert response.filename == "paper.pdf"
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
