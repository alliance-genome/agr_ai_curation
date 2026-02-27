"""Unit tests for document file download endpoint."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse

import src.config as app_config
from src.api import documents


_DOC_ID = "11111111-1111-1111-1111-111111111111"


class _FakeQuery:
    def __init__(self, doc):
        self._doc = doc

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._doc


class _FakeSession:
    def __init__(self, doc):
        self._doc = doc
        self.closed = False

    def query(self, _model):
        return _FakeQuery(self._doc)

    def close(self):
        self.closed = True


def _mock_session(monkeypatch, doc, user_id=1):
    session = _FakeSession(doc)
    monkeypatch.setattr(documents, "SessionLocal", lambda: session)
    monkeypatch.setattr(documents, "principal_from_claims", lambda _claims: SimpleNamespace(subject="user123"))
    monkeypatch.setattr(documents, "provision_user", lambda _session, _principal: SimpleNamespace(id=user_id))
    return session


@pytest.mark.asyncio
async def test_download_document_file_rejects_invalid_file_type(monkeypatch):
    _mock_session(monkeypatch, doc=None)

    with pytest.raises(HTTPException, match="Invalid file type") as exc:
        await documents.download_document_file(
            document_id=_DOC_ID,
            file_type="invalid",
            user={"sub": "user123"},
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_download_document_file_returns_404_when_document_missing(monkeypatch):
    _mock_session(monkeypatch, doc=None)

    with pytest.raises(HTTPException, match="not found") as exc:
        await documents.download_document_file(
            document_id=_DOC_ID,
            file_type="pdf",
            user={"sub": "user123"},
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_download_document_file_returns_403_for_cross_user_access(monkeypatch):
    doc = SimpleNamespace(
        id=_DOC_ID,
        user_id=99,
        file_path="user123/original.pdf",
        docling_json_path=None,
        processed_json_path=None,
        filename="paper.pdf",
    )
    _mock_session(monkeypatch, doc=doc, user_id=1)

    with pytest.raises(HTTPException, match="permission") as exc:
        await documents.download_document_file(
            document_id=_DOC_ID,
            file_type="pdf",
            user={"sub": "user123"},
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_download_document_file_returns_404_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(app_config, "get_pdf_storage_path", lambda: str(tmp_path))

    doc = SimpleNamespace(
        id=_DOC_ID,
        user_id=1,
        file_path="user123/original.pdf",
        docling_json_path=None,
        processed_json_path=None,
        filename="paper.pdf",
    )
    _mock_session(monkeypatch, doc=doc, user_id=1)

    with pytest.raises(HTTPException, match="file not available") as exc:
        await documents.download_document_file(
            document_id=_DOC_ID,
            file_type="pdf",
            user={"sub": "user123"},
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_download_document_file_returns_pdf_response(monkeypatch, tmp_path):
    monkeypatch.setattr(app_config, "get_pdf_storage_path", lambda: str(tmp_path))

    user_dir = tmp_path / "user123"
    user_dir.mkdir(parents=True)
    pdf_path = user_dir / "original.pdf"
    pdf_path.write_bytes(b"%PDF-1.7 test")

    doc = SimpleNamespace(
        id=_DOC_ID,
        user_id=1,
        file_path="user123/original.pdf",
        docling_json_path=None,
        processed_json_path=None,
        filename="paper.pdf",
    )
    _mock_session(monkeypatch, doc=doc, user_id=1)

    response = await documents.download_document_file(
        document_id=_DOC_ID,
        file_type="pdf",
        user={"sub": "user123"},
    )

    assert isinstance(response, FileResponse)
    assert response.media_type == "application/pdf"
    assert response.filename == "paper.pdf"
    assert "attachment" in response.headers.get("content-disposition", "").lower()
