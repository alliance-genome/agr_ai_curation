"""Unit tests for document file download endpoint."""

import importlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse

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


def _patch_runtime_pdf_storage_path(monkeypatch, path):
    """Patch whichever src.config module instance is currently active."""
    runtime_config = importlib.import_module("src.config")
    monkeypatch.setattr(runtime_config, "get_pdf_storage_path", lambda: str(path))


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
        pdfx_json_path=None,
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
    _patch_runtime_pdf_storage_path(monkeypatch, tmp_path)

    doc = SimpleNamespace(
        id=_DOC_ID,
        user_id=1,
        file_path="user123/original.pdf",
        pdfx_json_path=None,
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
    _patch_runtime_pdf_storage_path(monkeypatch, tmp_path)

    user_dir = tmp_path / "user123"
    user_dir.mkdir(parents=True)
    pdf_path = user_dir / "original.pdf"
    pdf_path.write_bytes(b"%PDF-1.7 test")

    doc = SimpleNamespace(
        id=_DOC_ID,
        user_id=1,
        file_path="user123/original.pdf",
        pdfx_json_path=None,
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


@pytest.mark.asyncio
async def test_download_document_file_returns_pdfx_json_response(monkeypatch, tmp_path):
    _patch_runtime_pdf_storage_path(monkeypatch, tmp_path)

    user_dir = tmp_path / "user123" / "pdfx_json"
    user_dir.mkdir(parents=True)
    (user_dir / "doc.json").write_text('{"raw": true}')

    doc = SimpleNamespace(
        id=_DOC_ID,
        user_id=1,
        file_path=None,
        pdfx_json_path="user123/pdfx_json/doc.json",
        processed_json_path=None,
        filename="paper.pdf",
    )
    _mock_session(monkeypatch, doc=doc, user_id=1)

    response = await documents.download_document_file(
        document_id=_DOC_ID,
        file_type="pdfx_json",
        user={"sub": "user123"},
    )

    assert isinstance(response, FileResponse)
    assert response.media_type == "application/json"
    assert response.filename == "paper_pdfx.json"


@pytest.mark.asyncio
async def test_download_document_file_returns_processed_json_response(monkeypatch, tmp_path):
    _patch_runtime_pdf_storage_path(monkeypatch, tmp_path)

    user_dir = tmp_path / "user123" / "processed_json"
    user_dir.mkdir(parents=True)
    (user_dir / "doc.json").write_text('{"processed": true}')

    doc = SimpleNamespace(
        id=_DOC_ID,
        user_id=1,
        file_path=None,
        pdfx_json_path=None,
        processed_json_path="user123/processed_json/doc.json",
        filename="paper.pdf",
    )
    _mock_session(monkeypatch, doc=doc, user_id=1)

    response = await documents.download_document_file(
        document_id=_DOC_ID,
        file_type="processed_json",
        user={"sub": "user123"},
    )

    assert isinstance(response, FileResponse)
    assert response.media_type == "application/json"
    assert response.filename == "paper_processed.json"


@pytest.mark.asyncio
async def test_download_document_file_blocks_path_traversal(monkeypatch, tmp_path):
    _patch_runtime_pdf_storage_path(monkeypatch, tmp_path)

    doc = SimpleNamespace(
        id=_DOC_ID,
        user_id=1,
        file_path="../outside.pdf",
        pdfx_json_path=None,
        processed_json_path=None,
        filename="paper.pdf",
    )
    _mock_session(monkeypatch, doc=doc, user_id=1)

    with pytest.raises(HTTPException, match="Access denied") as exc:
        await documents.download_document_file(
            document_id=_DOC_ID,
            file_type="pdf",
            user={"sub": "user123"},
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_download_document_file_invalid_uuid_returns_400(monkeypatch):
    _mock_session(monkeypatch, doc=None)

    with pytest.raises(HTTPException, match="Invalid document ID format") as exc:
        await documents.download_document_file(
            document_id="not-a-uuid",
            file_type="pdf",
            user={"sub": "user123"},
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_get_download_info_returns_404_when_document_missing(monkeypatch):
    _mock_session(monkeypatch, doc=None)

    with pytest.raises(HTTPException, match="not found") as exc:
        await documents.get_download_info(
            document_id=_DOC_ID,
            user={"sub": "user123"},
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_download_info_invalid_uuid_returns_400(monkeypatch):
    _mock_session(monkeypatch, doc=None)

    with pytest.raises(HTTPException, match="Invalid document ID format") as exc:
        await documents.get_download_info(
            document_id="not-a-uuid",
            user={"sub": "user123"},
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_get_download_info_returns_403_for_cross_user_access(monkeypatch):
    doc = SimpleNamespace(
        id=_DOC_ID,
        user_id=99,
        file_path="user123/original.pdf",
        pdfx_json_path="user123/pdfx_json/doc.json",
        processed_json_path="user123/processed_json/doc.json",
        filename="paper.pdf",
    )
    _mock_session(monkeypatch, doc=doc, user_id=1)

    with pytest.raises(HTTPException, match="permission") as exc:
        await documents.get_download_info(
            document_id=_DOC_ID,
            user={"sub": "user123"},
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_download_info_reports_file_availability_and_sizes(monkeypatch, tmp_path):
    _patch_runtime_pdf_storage_path(monkeypatch, tmp_path)

    user_dir = tmp_path / "user123"
    (user_dir / "pdfx_json").mkdir(parents=True)
    (user_dir / "processed_json").mkdir(parents=True)
    (user_dir / "original.pdf").write_bytes(b"%PDF-1.7")
    (user_dir / "pdfx_json" / "doc.json").write_text('{"raw": true}')
    (user_dir / "processed_json" / "doc.json").write_text('{"processed": true}')

    doc = SimpleNamespace(
        id=_DOC_ID,
        user_id=1,
        file_path="user123/original.pdf",
        pdfx_json_path="user123/pdfx_json/doc.json",
        processed_json_path="user123/processed_json/doc.json",
        filename="paper.pdf",
    )
    _mock_session(monkeypatch, doc=doc, user_id=1)

    payload = await documents.get_download_info(
        document_id=_DOC_ID,
        user={"sub": "user123"},
    )

    assert payload["pdf_available"] is True
    assert payload["pdfx_json_available"] is True
    assert payload["processed_json_available"] is True
    assert payload["pdf_size"] > 0
    assert payload["pdfx_json_size"] > 0
    assert payload["processed_json_size"] > 0
    assert payload["filename"] == "paper.pdf"


@pytest.mark.asyncio
async def test_get_download_info_handles_missing_optional_paths(monkeypatch, tmp_path):
    _patch_runtime_pdf_storage_path(monkeypatch, tmp_path)

    doc = SimpleNamespace(
        id=_DOC_ID,
        user_id=1,
        file_path=None,
        pdfx_json_path=None,
        processed_json_path=None,
        filename="paper.pdf",
    )
    _mock_session(monkeypatch, doc=doc, user_id=1)

    payload = await documents.get_download_info(
        document_id=_DOC_ID,
        user={"sub": "user123"},
    )

    assert payload["pdf_available"] is False
    assert payload["pdfx_json_available"] is False
    assert payload["processed_json_available"] is False
    assert payload["pdf_size"] is None
    assert payload["pdfx_json_size"] is None
    assert payload["processed_json_size"] is None


@pytest.mark.asyncio
async def test_get_download_info_blocks_path_traversal(monkeypatch, tmp_path):
    _patch_runtime_pdf_storage_path(monkeypatch, tmp_path)

    doc = SimpleNamespace(
        id=_DOC_ID,
        user_id=1,
        file_path="../outside.pdf",
        pdfx_json_path=None,
        processed_json_path=None,
        filename="paper.pdf",
    )
    _mock_session(monkeypatch, doc=doc, user_id=1)

    with pytest.raises(HTTPException, match="Access denied") as exc:
        await documents.get_download_info(
            document_id=_DOC_ID,
            user={"sub": "user123"},
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_download_document_file_blocks_pdfx_json_path_traversal(monkeypatch, tmp_path):
    _patch_runtime_pdf_storage_path(monkeypatch, tmp_path)

    doc = SimpleNamespace(
        id=_DOC_ID,
        user_id=1,
        file_path=None,
        pdfx_json_path="../outside.json",
        processed_json_path=None,
        filename="paper.pdf",
    )
    _mock_session(monkeypatch, doc=doc, user_id=1)

    with pytest.raises(HTTPException, match="Access denied") as exc:
        await documents.download_document_file(
            document_id=_DOC_ID,
            file_type="pdfx_json",
            user={"sub": "user123"},
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_download_document_file_blocks_processed_json_path_traversal(monkeypatch, tmp_path):
    _patch_runtime_pdf_storage_path(monkeypatch, tmp_path)

    doc = SimpleNamespace(
        id=_DOC_ID,
        user_id=1,
        file_path=None,
        pdfx_json_path=None,
        processed_json_path="../outside.json",
        filename="paper.pdf",
    )
    _mock_session(monkeypatch, doc=doc, user_id=1)

    with pytest.raises(HTTPException, match="Access denied") as exc:
        await documents.download_document_file(
            document_id=_DOC_ID,
            file_type="processed_json",
            user={"sub": "user123"},
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_download_info_blocks_pdfx_json_path_traversal(monkeypatch, tmp_path):
    _patch_runtime_pdf_storage_path(monkeypatch, tmp_path)

    doc = SimpleNamespace(
        id=_DOC_ID,
        user_id=1,
        file_path=None,
        pdfx_json_path="../outside.json",
        processed_json_path=None,
        filename="paper.pdf",
    )
    _mock_session(monkeypatch, doc=doc, user_id=1)

    with pytest.raises(HTTPException, match="Access denied") as exc:
        await documents.get_download_info(
            document_id=_DOC_ID,
            user={"sub": "user123"},
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_download_info_blocks_processed_json_path_traversal(monkeypatch, tmp_path):
    _patch_runtime_pdf_storage_path(monkeypatch, tmp_path)

    doc = SimpleNamespace(
        id=_DOC_ID,
        user_id=1,
        file_path=None,
        pdfx_json_path=None,
        processed_json_path="../outside.json",
        filename="paper.pdf",
    )
    _mock_session(monkeypatch, doc=doc, user_id=1)

    with pytest.raises(HTTPException, match="Access denied") as exc:
        await documents.get_download_info(
            document_id=_DOC_ID,
            user={"sub": "user123"},
        )

    assert exc.value.status_code == 403
