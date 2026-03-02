"""Unit tests for PDF extraction parser adapter."""

import pytest

from src.lib.exceptions import ConfigurationError
from src.lib.exceptions import PDFParsingError
from src.lib.pipeline.pdfx_parser import (
    PDFXParser,
    _build_progress_message,
    markdown_to_pipeline_elements,
)


class _DummyResponse:
    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self._body


class _DummySession:
    def __init__(self, response: _DummyResponse):
        self._response = response
        self.last_url = None

    def get(self, url, headers=None):
        self.last_url = url
        return self._response


class _SequenceSession:
    def __init__(self, post_responses=None, get_responses=None):
        self._post_responses = list(post_responses or [])
        self._get_responses = list(get_responses or [])
        self.post_calls = 0
        self.get_calls = 0

    def post(self, url, data=None, headers=None):
        del url, data, headers
        self.post_calls += 1
        return self._post_responses.pop(0)

    def get(self, url, headers=None):
        del url, headers
        self.get_calls += 1
        return self._get_responses.pop(0)


@pytest.fixture
def parser_env(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_SERVICE_URL", "http://pdfx.local")
    monkeypatch.setenv("PDF_EXTRACTION_TIMEOUT", "300")
    monkeypatch.setenv("PDF_EXTRACTION_POLL_INTERVAL_SECONDS", "2")
    monkeypatch.delenv("PDF_EXTRACTION_PRIMARY_DOWNLOAD_METHOD", raising=False)


def test_markdown_to_pipeline_elements_builds_expected_types():
    markdown = """# Introduction
This is the intro paragraph.

## Methods
- First item
1. Second item

| col_a | col_b |
| --- | --- |
| 1 | 2 |
"""

    elements = markdown_to_pipeline_elements(markdown)

    assert [element["type"] for element in elements] == [
        "Title",
        "NarrativeText",
        "Title",
        "ListItem",
        "ListItem",
        "Table",
    ]
    assert elements[0]["text"] == "Introduction"
    assert elements[2]["text"] == "Methods"
    assert elements[3]["metadata"]["section_path"] == ["Introduction", "Methods"]
    assert elements[5]["metadata"]["content_type"] == "table"


def test_build_progress_message_prefers_stage_display():
    message = _build_progress_message(
        {
            "status": "progress",
            "progress": {
                "stage_display": "Merging extraction outputs",
                "percent": 80,
            },
        }
    )
    assert message == "PDF extraction: Merging extraction outputs (80%)"


@pytest.mark.asyncio
async def test_build_auth_headers_static_bearer(parser_env, monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_AUTH_MODE", "static_bearer")
    monkeypatch.setenv("PDF_EXTRACTION_BEARER_TOKEN", "token-123")

    parser = PDFXParser()
    headers = await parser._build_auth_headers(session=None)  # type: ignore[arg-type]
    assert headers == {"Authorization": "Bearer token-123"}


@pytest.mark.asyncio
async def test_build_auth_headers_static_bearer_requires_token(parser_env, monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_AUTH_MODE", "static_bearer")
    monkeypatch.delenv("PDF_EXTRACTION_BEARER_TOKEN", raising=False)

    parser = PDFXParser()
    with pytest.raises(ConfigurationError, match="PDF_EXTRACTION_BEARER_TOKEN"):
        await parser._build_auth_headers(session=None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_download_markdown_uses_merged_variant_when_merge_enabled(parser_env, monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_MERGE", "true")

    parser = PDFXParser()
    session = _DummySession(_DummyResponse(200, "# merged markdown\n"))

    markdown = await parser._download_markdown(session=session, process_id="proc-1", headers={})

    assert markdown == "# merged markdown"
    assert session.last_url.endswith("/api/v1/extract/proc-1/download/merged")


@pytest.mark.asyncio
async def test_download_markdown_uses_first_method_when_merge_disabled(parser_env, monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_MERGE", "false")
    monkeypatch.setenv("PDF_EXTRACTION_METHODS", "grobid,marker")

    parser = PDFXParser()
    session = _DummySession(_DummyResponse(200, "# grobid markdown\n"))

    markdown = await parser._download_markdown(session=session, process_id="proc-2", headers={})

    assert markdown == "# grobid markdown"
    assert parser.download_variant == "grobid"
    assert session.last_url.endswith("/api/v1/extract/proc-2/download/grobid")


def test_primary_download_method_must_be_in_configured_methods(parser_env, monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_MERGE", "false")
    monkeypatch.setenv("PDF_EXTRACTION_METHODS", "grobid,marker")
    monkeypatch.setenv("PDF_EXTRACTION_PRIMARY_DOWNLOAD_METHOD", "legacy")

    with pytest.raises(ConfigurationError, match="PDF_EXTRACTION_PRIMARY_DOWNLOAD_METHOD"):
        PDFXParser()


@pytest.mark.asyncio
async def test_submit_retries_on_transient_504_and_succeeds(parser_env, monkeypatch, tmp_path):
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr("src.lib.pipeline.pdfx_parser.asyncio.sleep", _no_sleep)
    parser = PDFXParser()
    parser.poll_interval_seconds = 0

    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test")

    session = _SequenceSession(
        post_responses=[
            _DummyResponse(504, "<html>gateway timeout</html>"),
            _DummyResponse(202, '{"process_id": "proc-123"}'),
        ]
    )

    payload = await parser._submit_extraction(session=session, file_path=pdf_path, headers={})
    assert payload["process_id"] == "proc-123"
    assert session.post_calls == 2


@pytest.mark.asyncio
async def test_submit_fails_on_non_transient_error(parser_env, tmp_path):
    parser = PDFXParser()

    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test")

    session = _SequenceSession(
        post_responses=[
            _DummyResponse(401, '{"detail":"unauthorized"}'),
        ]
    )

    with pytest.raises(PDFParsingError, match="PDF extraction submit failed: 401"):
        await parser._submit_extraction(session=session, file_path=pdf_path, headers={})
    assert session.post_calls == 1


@pytest.mark.asyncio
async def test_poll_retries_transient_missing_status_until_complete(parser_env, monkeypatch):
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr("src.lib.pipeline.pdfx_parser.asyncio.sleep", _no_sleep)
    parser = PDFXParser()
    parser.poll_interval_seconds = 0

    session = _SequenceSession(
        get_responses=[
            _DummyResponse(502, "<html>bad gateway</html>"),
            _DummyResponse(200, '{"status":"running","progress":{"stage_display":"Extracting","percent":30}}'),
            _DummyResponse(200, '{"status":"complete"}'),
        ]
    )
    messages = []

    async def on_progress(message: str):
        messages.append(message)

    payload = await parser._poll_until_complete(
        session=session,
        process_id="proc-xyz",
        headers={},
        progress_callback=on_progress,
    )

    assert payload["status"] == "complete"
    assert session.get_calls == 3
    assert any("Extracting" in msg for msg in messages)


@pytest.mark.asyncio
async def test_poll_raises_when_status_missing_on_non_transient_response(parser_env):
    parser = PDFXParser()
    session = _SequenceSession(
        get_responses=[
            _DummyResponse(200, '{"detail":"still processing"}'),
        ]
    )

    with pytest.raises(PDFParsingError, match="missing 'status'"):
        await parser._poll_until_complete(
            session=session,
            process_id="proc-abc",
            headers={},
            progress_callback=None,
        )
