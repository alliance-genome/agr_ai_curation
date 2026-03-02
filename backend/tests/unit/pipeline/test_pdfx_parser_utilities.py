"""Additional unit tests for PDFX parser utility and edge branches."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.lib.exceptions import ConfigurationError, PDFParsingError
from src.lib.pipeline import pdfx_parser as parser_module
from src.lib.pipeline.pdfx_parser import PDFXParser


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


@pytest.fixture
def parser_env(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_SERVICE_URL", "http://pdfx.local")
    monkeypatch.setenv("PDF_EXTRACTION_TIMEOUT", "60")
    monkeypatch.setenv("PDF_EXTRACTION_POLL_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("PDF_EXTRACTION_METHODS", "grobid,marker")
    monkeypatch.delenv("PDF_EXTRACTION_PRIMARY_DOWNLOAD_METHOD", raising=False)
    monkeypatch.delenv("PDF_EXTRACTION_AUTH_MODE", raising=False)


def test_parser_init_validation_branches(parser_env, monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_TIMEOUT", "not-int")
    with pytest.raises(ConfigurationError, match="must be an integer"):
        PDFXParser()

    monkeypatch.setenv("PDF_EXTRACTION_TIMEOUT", "0")
    with pytest.raises(ConfigurationError, match="greater than 0"):
        PDFXParser()

    monkeypatch.setenv("PDF_EXTRACTION_TIMEOUT", "60")
    monkeypatch.setenv("PDF_EXTRACTION_POLL_INTERVAL_SECONDS", "0")
    with pytest.raises(ConfigurationError, match="POLL_INTERVAL_SECONDS must be greater than 0"):
        PDFXParser()

    monkeypatch.setenv("PDF_EXTRACTION_POLL_INTERVAL_SECONDS", "abc")
    with pytest.raises(ConfigurationError, match="must be numeric"):
        PDFXParser()

    monkeypatch.setenv("PDF_EXTRACTION_POLL_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("PDF_EXTRACTION_METHODS", " , ")
    with pytest.raises(ConfigurationError, match="must include at least one extraction method"):
        PDFXParser()

    monkeypatch.setenv("PDF_EXTRACTION_METHODS", "grobid")
    monkeypatch.setenv("PDF_EXTRACTION_AUTH_MODE", "bogus")
    with pytest.raises(ConfigurationError, match="Invalid PDF_EXTRACTION_AUTH_MODE"):
        PDFXParser()


def test_parser_download_variant_selection(parser_env, monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_MERGE", "false")
    monkeypatch.setenv("PDF_EXTRACTION_METHODS", "grobid,marker")
    monkeypatch.setenv("PDF_EXTRACTION_PRIMARY_DOWNLOAD_METHOD", "marker")
    parser = PDFXParser()
    assert parser.download_variant == "marker"


@pytest.mark.asyncio
async def test_download_markdown_error_and_empty_branches(parser_env):
    parser = PDFXParser()
    session_500 = SimpleNamespace(get=lambda *_args, **_kwargs: _DummyResponse(500, "upstream down"))
    with pytest.raises(PDFParsingError, match="download failed"):
        await parser._download_markdown(session=session_500, process_id="proc-1", headers={})

    session_empty = SimpleNamespace(get=lambda *_args, **_kwargs: _DummyResponse(200, "  "))
    with pytest.raises(PDFParsingError, match="returned empty markdown"):
        await parser._download_markdown(session=session_empty, process_id="proc-2", headers={})


@pytest.mark.asyncio
async def test_poll_until_complete_failure_and_non_json_non_transient(parser_env, monkeypatch):
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(parser_module.asyncio, "sleep", _no_sleep)
    parser = PDFXParser()

    session_failed = SimpleNamespace(
        get=lambda *_args, **_kwargs: _DummyResponse(200, '{"status":"failed","error":"boom"}')
    )
    with pytest.raises(PDFParsingError, match="PDF extraction failed"):
        await parser._poll_until_complete(
            session=session_failed,
            process_id="proc-fail",
            headers={},
            progress_callback=None,
        )

    session_bad_json = SimpleNamespace(get=lambda *_args, **_kwargs: _DummyResponse(200, "<html>oops</html>"))
    with pytest.raises(PDFParsingError, match="returned non-JSON response"):
        await parser._poll_until_complete(
            session=session_bad_json,
            process_id="proc-json",
            headers={},
            progress_callback=None,
        )


@pytest.mark.asyncio
async def test_poll_progress_callback_failure_is_non_fatal(parser_env, monkeypatch):
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(parser_module.asyncio, "sleep", _no_sleep)
    parser = PDFXParser()

    responses = [
        _DummyResponse(200, '{"status":"running","progress":{"stage":"Extracting","percent":10}}'),
        _DummyResponse(200, '{"status":"complete"}'),
    ]
    session = SimpleNamespace(get=lambda *_args, **_kwargs: responses.pop(0))

    callback_calls = {"count": 0}

    async def _bad_progress(_msg):
        callback_calls["count"] += 1
        raise RuntimeError("callback failed")

    payload = await parser._poll_until_complete(
        session=session,
        process_id="proc-ok",
        headers={},
        progress_callback=_bad_progress,
    )
    assert payload["status"] == "complete"
    assert callback_calls["count"] >= 1


class _TokenSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.post_calls = 0

    def post(self, *_args, **_kwargs):
        self.post_calls += 1
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_cognito_token_success_and_cache(parser_env, monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_AUTH_MODE", "cognito_client_credentials")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_TOKEN_URL", "https://auth.example.org/oauth2/token")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_ID", "client-id")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_SCOPE", "pdfx/read")

    parser = PDFXParser()
    parser._cognito_access_token = None
    parser._cognito_token_expires_at = 0
    session = _TokenSession([_DummyResponse(200, json.dumps({"access_token": "abc123", "expires_in": 120}))])

    token_1 = await parser._get_cognito_client_credentials_token(session)
    token_2 = await parser._get_cognito_client_credentials_token(session)
    assert token_1 == "abc123"
    assert token_2 == "abc123"
    assert session.post_calls == 1


@pytest.mark.asyncio
async def test_cognito_token_error_branches(parser_env, monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_AUTH_MODE", "cognito_client_credentials")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_TOKEN_URL", "https://auth.example.org/oauth2/token")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_ID", "client-id")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("PDF_EXTRACTION_COGNITO_SCOPE", "pdfx/read")

    parser = PDFXParser()
    parser._cognito_access_token = None
    parser._cognito_token_expires_at = 0

    with pytest.raises(PDFParsingError, match="non-JSON response"):
        await parser._get_cognito_client_credentials_token(_TokenSession([_DummyResponse(200, "<html>bad</html>")]))

    with pytest.raises(PDFParsingError, match="missing access_token"):
        await parser._get_cognito_client_credentials_token(_TokenSession([_DummyResponse(200, '{"expires_in":120}')]))


@pytest.mark.asyncio
async def test_parse_pdf_document_wrapper_delegates(monkeypatch):
    expected = {"elements": [{"index": 0}], "pdfx_json_path": "u/doc.json", "processed_json_path": "u/proc.json"}
    observed = {}

    class _FakeParser:
        async def parse_pdf_document(self, **kwargs):
            observed.update(kwargs)
            return expected

    monkeypatch.setattr(parser_module, "PDFXParser", _FakeParser)
    result = await parser_module.parse_pdf_document(
        file_path=Path("/tmp/a.pdf"),
        document_id="doc-1",
        user_id="user-1",
        extraction_strategy="auto",
        enable_table_extraction=True,
        progress_callback=None,
    )

    assert result == expected
    assert observed["file_path"] == Path("/tmp/a.pdf")
    assert observed["document_id"] == "doc-1"
    assert observed["user_id"] == "user-1"
    assert observed["extraction_strategy"] == "auto"
    assert observed["enable_table_extraction"] is True
    assert observed["progress_callback"] is None


def test_validate_pdf_file_branches(tmp_path):
    missing = parser_module.validate_pdf_file(tmp_path / "missing.pdf")
    assert missing["is_valid"] is False
    assert missing["file_exists"] is False

    not_pdf = tmp_path / "paper.txt"
    not_pdf.write_text("hello")
    invalid_ext = parser_module.validate_pdf_file(not_pdf)
    assert invalid_ext["is_pdf"] is False
    assert any("Not a PDF file" in e for e in invalid_ext["errors"])

    empty_pdf = tmp_path / "empty.pdf"
    empty_pdf.write_bytes(b"")
    empty_result = parser_module.validate_pdf_file(empty_pdf)
    assert empty_result["is_valid"] is False
    assert any("File is empty" in e for e in empty_result["errors"])

    bad_header = tmp_path / "bad.pdf"
    bad_header.write_bytes(b"HELLO")
    bad_header_result = parser_module.validate_pdf_file(bad_header)
    assert bad_header_result["is_valid"] is False
    assert any("Invalid PDF header" in e for e in bad_header_result["errors"])

    good_pdf = tmp_path / "good.pdf"
    good_pdf.write_bytes(b"%PDF-1.4\n%good")
    valid_result = parser_module.validate_pdf_file(good_pdf)
    assert valid_result["is_valid"] is True
    assert valid_result["is_pdf"] is True


def test_misc_strategy_and_error_helpers(monkeypatch, caplog):
    monkeypatch.setenv("PDF_EXTRACTION_STRATEGY", "fast")
    assert parser_module.get_extraction_strategy() == "fast"
    parser_module.validate_extraction_strategy("auto")
    with pytest.raises(ConfigurationError):
        parser_module.validate_extraction_strategy("invalid")

    monkeypatch.setenv("ENABLE_TABLE_EXTRACTION", "YES")
    assert parser_module.is_table_extraction_enabled() is True
    monkeypatch.setenv("ENABLE_TABLE_EXTRACTION", "false")
    assert parser_module.is_table_extraction_enabled() is False

    with caplog.at_level("WARNING"):
        parser_module.handle_parsing_errors(RuntimeError("timeout while parsing"))
    assert any("timed out" in msg.lower() for msg in caplog.messages)
