"""Unit tests for PDF extraction parser adapter."""

import json
from pathlib import Path

import pytest

from src.lib.exceptions import ConfigurationError
from src.lib.exceptions import PDFCancellationError, PDFParsingError
from src.lib.pipeline.pdfx_parser import (
    PDFX_FAILURE_DETAILS_KEY,
    PDFX_POLLING_TIMEOUT_MESSAGE,
    PDFX_PROVIDER_FAILURE_MESSAGE,
    PDFX_PUBLIC_MESSAGE_DETAILS_KEY,
    PDFXParser,
    _build_progress_message,
    _cache_hit_from_payloads,
    _safe_provider_token,
    markdown_to_pipeline_elements,
)
from src.lib.pipeline.processing_receipt import PDFProcessingReceipt


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

    async def read(self):
        return self._body.encode("utf-8")


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


def test_markdown_to_pipeline_elements_does_not_synthesize_bbox_provenance():
    markdown = """<!-- page: 2 -->
# Results
This paragraph came from a markdown-only extraction fallback.
"""

    elements = markdown_to_pipeline_elements(markdown)

    assert [element["metadata"]["page_number"] for element in elements] == [2, 2]
    assert all("bbox" not in element["metadata"] for element in elements)
    assert all("provenance" not in element["metadata"] for element in elements)


def test_markdown_to_pipeline_elements_does_not_split_on_form_feed():
    elements = markdown_to_pipeline_elements("First page\fcontinued paragraph")

    assert len(elements) == 1
    assert elements[0]["text"] == "First page\fcontinued paragraph"


def test_markdown_to_pipeline_elements_strips_inline_formatting_from_text_and_sections():
    markdown = """# **Results** <sup>2+</sup>
Signal from **B cells** depends on Ca<sup>2+</sup> and *kinase* activity.
"""

    elements = markdown_to_pipeline_elements(markdown)

    assert [element["text"] for element in elements] == [
        "Results 2+",
        "Signal from B cells depends on Ca2+ and kinase activity.",
    ]
    assert elements[0]["metadata"]["section_title"] == "Results 2+"
    assert elements[0]["metadata"]["section_path"] == ["Results 2+"]
    assert elements[1]["metadata"]["section_title"] == "Results 2+"
    assert elements[1]["metadata"]["section_path"] == ["Results 2+"]


def test_build_progress_message_uses_local_text_with_valid_numeric_percent():
    sentinel = "PRIVATE_PROVIDER_SENTINEL"
    message = _build_progress_message(
        {
            "status": "progress",
            "message": sentinel,
            "progress": {
                "stage_display": sentinel,
                "stage": sentinel,
                "percent": 80,
            },
        }
    )
    assert message == "Extracting PDF content... (80%)"
    assert sentinel not in message


def test_build_progress_message_does_not_surface_pdfx_queue_message():
    sentinel = "PRIVATE_PROVIDER_SENTINEL"
    message = _build_progress_message(
        {
            "status": "queued",
            "state": "ready",
            "message": sentinel,
        }
    )

    assert message == "PDF extraction queued; waiting for PDFX worker..."
    assert sentinel not in message


@pytest.mark.parametrize("percent", [-1, 101, True, "80"])
def test_build_progress_message_rejects_invalid_percent(percent):
    message = _build_progress_message(
        {"status": "running", "progress": {"percent": percent}}
    )

    assert message == "Extracting PDF content..."


def test_build_progress_message_uses_ready_queue_fallback():
    message = _build_progress_message({"status": "pending", "state": "busy"})

    assert message == "PDF extraction queued; waiting for PDFX worker..."


@pytest.mark.parametrize(
    ("payloads", "expected"),
    [
        (({"cache_hit": True},), True),
        (({"cached": False},), False),
        (({"cache": {"hit": True}},), True),
        (({"status": "cached"},), None),
    ],
)
def test_cache_hit_normalization_requires_explicit_boolean(payloads, expected):
    assert _cache_hit_from_payloads(*payloads) is expected


def test_safe_provider_token_obeys_configured_bound(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_RECEIPT_TOKEN_MAX_CHARS", "8")

    assert _safe_provider_token("safe-123") == "safe-123"
    assert _safe_provider_token("too-long-token") is None


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

    assert markdown == "# merged markdown\n"
    assert session.last_url.endswith("/api/v1/extract/proc-1/download/merged")


@pytest.mark.asyncio
async def test_download_markdown_preserves_exact_response_bytes(parser_env, monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_MERGE", "true")

    parser = PDFXParser()
    session = _DummySession(_DummyResponse(200, "# merged markdown\n\n"))

    markdown = await parser._download_markdown(
        session=session,
        process_id="proc-exact",
        headers={},
    )

    assert markdown.encode("utf-8") == b"# merged markdown\n\n"


@pytest.mark.asyncio
async def test_download_page_provenance_rejects_invalid_contract(parser_env, monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_MERGE", "true")

    parser = PDFXParser()
    session = _DummySession(_DummyResponse(200, "{}\n"))

    with pytest.raises(PDFParsingError, match="page provenance is invalid"):
        await parser._download_page_provenance(
            session=session,
            process_id="proc-invalid",
            headers={},
            merged_markdown=b"# merged markdown\n",
        )

    assert session.last_url.endswith(
        "/api/v1/extract/proc-invalid/download/page_provenance"
    )


@pytest.mark.asyncio
async def test_parse_threads_merged_page_provenance_into_elements_and_receipt(
    parser_env,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("PDF_EXTRACTION_MERGE", "true")
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: tmp_path)
    markdown = "# Title\n\nBody\n"
    body_start = markdown.encode("utf-8").index(b"Body")
    receipt = {
        "schema": "pdfx-merged-page-provenance",
        "contract_version": "merged-page-provenance-v1",
        "record_sha256": "a" * 64,
        "expected_page_count": 2,
        "range_count": 2,
        "summary": {},
    }
    observed = {}
    observations = []

    class _SessionContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Provenance:
        def page_for_byte_offset(self, byte_offset):
            return 2 if byte_offset >= body_start else 1

        def receipt(self):
            return receipt

    parser = PDFXParser()

    async def _submit_extraction(session, file_path, headers):
        del session, file_path, headers
        return {"process_id": "proc-wiring"}

    async def _poll_until_complete(**kwargs):
        del kwargs
        return {"status": "complete", "cache_hit": True}

    async def _download_markdown(session, process_id, headers):
        del session, process_id, headers
        return markdown

    async def _download_page_provenance(session, process_id, headers, *, merged_markdown):
        del session, process_id, headers
        observed["merged_markdown"] = merged_markdown
        return _Provenance()

    monkeypatch.setattr(
        "src.lib.pipeline.pdfx_parser.aiohttp.ClientSession",
        lambda timeout: _SessionContext(),
    )
    monkeypatch.setattr(parser, "_submit_extraction", _submit_extraction)
    monkeypatch.setattr(parser, "_poll_until_complete", _poll_until_complete)
    monkeypatch.setattr(parser, "_download_markdown", _download_markdown)
    monkeypatch.setattr(parser, "_download_page_provenance", _download_page_provenance)

    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test")
    result = await parser.parse_pdf_document(
        pdf_path,
        "doc-wiring",
        "user-wiring",
        observability_callback=observations.append,
    )

    assert observed["merged_markdown"] == markdown.encode("utf-8")
    assert [item["metadata"]["page_number"] for item in result["elements"]] == [1, 2]
    raw_payload = json.loads((tmp_path / result["pdfx_json_path"]).read_text())
    assert raw_payload["page_provenance"] == receipt
    assert len(observations) == 1
    assert observations[0]["status"] == "completed"
    assert observations[0]["cache_hit"] is True
    assert observations[0]["extraction_methods"] == ["grobid", "marker"]
    assert observations[0]["merge_enabled"] is True
    assert observations[0]["process_id"] == "proc-wiring"
    assert observations[0]["submit_attempt_count"] == 0
    assert observations[0]["poll_attempt_count"] == 0
    assert observations[0]["duration_ms"] >= 0


@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (PDFParsingError("provider failed"), "failed"),
        (PDFCancellationError("cancelled"), "cancelled"),
    ],
)
@pytest.mark.asyncio
async def test_parse_reports_external_failure_outcome(
    parser_env,
    monkeypatch,
    tmp_path,
    failure,
    expected_status,
):
    class _SessionContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    parser = PDFXParser()
    observations = []

    async def _submit_extraction(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(
        "src.lib.pipeline.pdfx_parser.aiohttp.ClientSession",
        lambda timeout: _SessionContext(),
    )
    monkeypatch.setattr(parser, "_submit_extraction", _submit_extraction)
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test")

    with pytest.raises(type(failure)):
        await parser.parse_pdf_document(
            pdf_path,
            "doc-failed",
            "user-failed",
            observability_callback=observations.append,
        )

    assert len(observations) == 1
    assert observations[0]["status"] == expected_status
    assert observations[0]["cache_hit"] is None
    if expected_status == "failed":
        assert observations[0]["failure_category"] == "unknown_provider_failure"
        assert observations[0]["failure_boundary"] == "submit"


@pytest.mark.asyncio
async def test_observation_callback_failure_reports_sanitized_warning_without_masking_success(
    parser_env,
    monkeypatch,
    tmp_path,
    caplog,
):
    monkeypatch.setenv("PDF_EXTRACTION_MERGE", "false")
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: tmp_path)
    callback_sentinel = "PRIVATE_CALLBACK_SENTINEL"
    reported = []

    class _SessionContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    parser = PDFXParser()

    async def _submit_extraction(*_args, **_kwargs):
        return {"process_id": "proc-success"}

    async def _poll_until_complete(**_kwargs):
        return {"status": "complete"}

    async def _download_markdown(*_args, **_kwargs):
        return "# Results\n\nBody\n"

    def _failing_callback(observation):
        raise RuntimeError(f"{callback_sentinel}: {observation!r}")

    def _report_runtime_exception(exc, **kwargs):
        reported.append((exc, kwargs))
        return False

    monkeypatch.setattr(
        "src.lib.pipeline.pdfx_parser.aiohttp.ClientSession",
        lambda timeout: _SessionContext(),
    )
    monkeypatch.setattr(parser, "_submit_extraction", _submit_extraction)
    monkeypatch.setattr(parser, "_poll_until_complete", _poll_until_complete)
    monkeypatch.setattr(parser, "_download_markdown", _download_markdown)
    monkeypatch.setattr(
        "src.lib.pipeline.pdfx_parser.report_runtime_exception",
        _report_runtime_exception,
    )
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test")

    result = await parser.parse_pdf_document(
        pdf_path,
        "doc-private",
        "user-private",
        observability_callback=_failing_callback,
    )

    assert [element["text"] for element in result["elements"]] == ["Results", "Body"]
    assert len(reported) == 1
    reported_exc, report_kwargs = reported[0]
    assert str(reported_exc) == "PDF extraction observability callback failed"
    assert reported_exc.__traceback__ is not None
    assert reported_exc.__context__ is None
    assert reported_exc.__cause__ is None
    assert report_kwargs == {
        "component": "pdfx_parser",
        "operation": "external_observation_callback_failed",
        "level": "warning",
    }
    assert callback_sentinel not in str(reported_exc)
    assert callback_sentinel not in caplog.text
    assert "doc-private" not in str(reported_exc)


@pytest.mark.asyncio
async def test_observation_callback_failure_does_not_mask_active_parser_exception(
    parser_env,
    monkeypatch,
    tmp_path,
):
    callback_sentinel = "PRIVATE_CALLBACK_SENTINEL"
    provider_sentinel = "PRIVATE_PROVIDER_SENTINEL"
    reported = []

    class _SessionContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    parser = PDFXParser()

    async def _submit_extraction(*_args, **_kwargs):
        raise PDFParsingError(provider_sentinel)

    def _failing_callback(observation):
        raise RuntimeError(f"{callback_sentinel}: {observation!r}")

    def _report_runtime_exception(exc, **kwargs):
        reported.append((exc, kwargs))
        return True

    monkeypatch.setattr(
        "src.lib.pipeline.pdfx_parser.aiohttp.ClientSession",
        lambda timeout: _SessionContext(),
    )
    monkeypatch.setattr(parser, "_submit_extraction", _submit_extraction)
    monkeypatch.setattr(
        "src.lib.pipeline.pdfx_parser.report_runtime_exception",
        _report_runtime_exception,
    )
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test")

    with pytest.raises(PDFParsingError) as raised:
        await parser.parse_pdf_document(
            pdf_path,
            "doc-private",
            "user-private",
            observability_callback=_failing_callback,
        )

    assert raised.value.details[PDFX_FAILURE_DETAILS_KEY]["failure_boundary"] == "submit"
    assert provider_sentinel not in str(raised.value)
    assert callback_sentinel not in str(raised.value)
    assert len(reported) == 1
    reported_exc, report_kwargs = reported[0]
    assert str(reported_exc) == "PDF extraction observability callback failed"
    assert reported_exc.__context__ is None
    assert reported_exc.__cause__ is None
    assert report_kwargs["level"] == "warning"
    assert callback_sentinel not in str(reported_exc)
    assert provider_sentinel not in str(reported_exc)


@pytest.mark.asyncio
async def test_parse_sanitizes_unclassified_provider_exception(
    parser_env,
    monkeypatch,
    tmp_path,
    caplog,
):
    sentinel = "PRIVATE_PROVIDER_SENTINEL"

    class _SessionContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    parser = PDFXParser()
    receipt = PDFProcessingReceipt(document_id="doc-private")

    async def _submit_extraction(*_args, **_kwargs):
        raise PDFParsingError(f"provider returned {sentinel}")

    monkeypatch.setattr(
        "src.lib.pipeline.pdfx_parser.aiohttp.ClientSession",
        lambda timeout: _SessionContext(),
    )
    monkeypatch.setattr(parser, "_submit_extraction", _submit_extraction)
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test")

    with pytest.raises(PDFParsingError) as raised:
        await parser.parse_pdf_document(
            pdf_path,
            "doc-private",
            "user-private",
            observability_callback=receipt.record_external_observation,
        )

    stored_receipt = receipt.finalize("failed")
    assert sentinel not in str(raised.value)
    assert sentinel not in repr(raised.value.__cause__)
    assert sentinel not in caplog.text
    assert sentinel not in str(stored_receipt)
    assert raised.value.details[PDFX_FAILURE_DETAILS_KEY]["failure_category"] == (
        "unknown_provider_failure"
    )


@pytest.mark.asyncio
async def test_parse_preserves_sanitized_download_http_status_in_observation(
    parser_env,
    monkeypatch,
    tmp_path,
):
    sentinel = "PRIVATE_PROVIDER_SENTINEL"

    class _SessionContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, *_args, **_kwargs):
            return _DummyResponse(403, sentinel)

    parser = PDFXParser()
    parser.download_retry_seconds = 0
    observations = []

    async def _submit_extraction(*_args, **_kwargs):
        parser._submit_attempt_count = 1
        return {"process_id": "proc-download"}

    async def _poll_until_complete(**_kwargs):
        parser._poll_attempt_count = 2
        return {"status": "complete"}

    monkeypatch.setattr(
        "src.lib.pipeline.pdfx_parser.aiohttp.ClientSession",
        lambda timeout: _SessionContext(),
    )
    monkeypatch.setattr(parser, "_submit_extraction", _submit_extraction)
    monkeypatch.setattr(parser, "_poll_until_complete", _poll_until_complete)
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test")

    with pytest.raises(PDFParsingError) as raised:
        await parser.parse_pdf_document(
            pdf_path,
            "doc-download",
            "user-download",
            observability_callback=observations.append,
        )

    assert sentinel not in str(raised.value)
    assert raised.value.details[PDFX_FAILURE_DETAILS_KEY] == {
        "failure_category": "unknown_provider_failure",
        "failure_boundary": "download",
        "process_id": "proc-download",
        "http_status": 403,
        "submit_attempt_count": 1,
        "poll_attempt_count": 2,
        "timeout_seconds": 300,
    }
    assert observations[0]["http_status"] == 403
    assert observations[0]["failure_boundary"] == "download"
    assert sentinel not in str(observations)


@pytest.mark.asyncio
async def test_download_markdown_retries_transient_503(parser_env, monkeypatch):
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr("src.lib.pipeline.pdfx_parser.asyncio.sleep", _no_sleep)
    monkeypatch.setenv("PDF_EXTRACTION_DOWNLOAD_RETRY_SECONDS", "30")

    parser = PDFXParser()
    session = _SequenceSession(
        get_responses=[
            _DummyResponse(503, '{"detail":"EC2 is not running"}'),
            _DummyResponse(200, "# merged markdown\n"),
        ]
    )

    markdown = await parser._download_markdown(session=session, process_id="proc-1", headers={})

    assert markdown == "# merged markdown\n"
    assert session.get_calls == 2


@pytest.mark.asyncio
async def test_download_markdown_uses_first_method_when_merge_disabled(parser_env, monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTION_MERGE", "false")
    monkeypatch.setenv("PDF_EXTRACTION_METHODS", "grobid,marker")

    parser = PDFXParser()
    session = _DummySession(_DummyResponse(200, "# grobid markdown\n"))

    markdown = await parser._download_markdown(session=session, process_id="proc-2", headers={})

    assert markdown == "# grobid markdown\n"
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
    assert parser._submit_attempt_count == 2


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

    with pytest.raises(PDFParsingError, match="PDF extraction submit failed with HTTP 401") as raised:
        await parser._submit_extraction(session=session, file_path=pdf_path, headers={})
    assert session.post_calls == 1
    assert raised.value.details[PDFX_FAILURE_DETAILS_KEY] == {
        "failure_category": "unknown_provider_failure",
        "failure_boundary": "submit",
        "http_status": 401,
    }


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
    assert parser._poll_attempt_count == 3
    assert any("Extracting" in msg for msg in messages)


@pytest.mark.asyncio
async def test_poll_classifies_terminal_failure_without_provider_prose(
    parser_env,
    caplog,
):
    sentinel = "PRIVATE_PROVIDER_SENTINEL"
    parser = PDFXParser()
    session = _SequenceSession(
        get_responses=[
            _DummyResponse(
                200,
                json.dumps(
                    {
                        "status": "failed",
                        "error_code": "publish_failed",
                        "message": sentinel,
                        "error": f"private free-form provider explanation {sentinel}",
                    }
                ),
            )
        ]
    )
    progress_messages = []

    async def on_progress(message: str):
        progress_messages.append(message)

    with pytest.raises(PDFParsingError) as raised:
        await parser._poll_until_complete(
            session=session,
            process_id="proc-terminal",
            headers={},
            progress_callback=on_progress,
        )

    assert "private free-form" not in str(raised.value)
    assert sentinel not in str(raised.value)
    assert sentinel not in caplog.text
    assert progress_messages == []
    assert raised.value.details[PDFX_PUBLIC_MESSAGE_DETAILS_KEY] == PDFX_PROVIDER_FAILURE_MESSAGE
    assert raised.value.details[PDFX_FAILURE_DETAILS_KEY] == {
        "failure_category": "provider_terminal_failure",
        "failure_boundary": "poll",
        "process_id": "proc-terminal",
        "provider_status": "failed",
        "provider_error_code": "publish_failed",
        "http_status": 200,
    }
    assert parser._poll_attempt_count == 1


@pytest.mark.asyncio
async def test_poll_classifies_bounded_timeout_separately(parser_env):
    parser = PDFXParser()
    parser.timeout_seconds = 0.001
    parser.poll_interval_seconds = 0.01
    session = _SequenceSession(
        get_responses=[_DummyResponse(200, '{"status":"running"}')]
    )

    with pytest.raises(PDFParsingError) as raised:
        await parser._poll_until_complete(
            session=session,
            process_id="proc-timeout",
            headers={},
            progress_callback=None,
        )

    assert raised.value.details[PDFX_PUBLIC_MESSAGE_DETAILS_KEY] == PDFX_POLLING_TIMEOUT_MESSAGE
    assert raised.value.details[PDFX_FAILURE_DETAILS_KEY] == {
        "failure_category": "polling_timeout",
        "failure_boundary": "poll",
        "process_id": "proc-timeout",
        "provider_status": "running",
    }
    assert parser._poll_attempt_count == 1


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


@pytest.mark.asyncio
async def test_save_pdfx_and_processed_json_normalize_directory_permissions(parser_env, monkeypatch, tmp_path):
    parser = PDFXParser()
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: tmp_path)

    user_dir = tmp_path / "user-1"
    user_dir.mkdir(parents=True, exist_ok=True)
    user_dir.chmod(0o755)

    pdfx_path = await parser._save_pdfx_json({"status": "complete"}, "doc-1", "user-1")
    processed_path = await parser._save_processed_json([{"type": "Title", "text": "Results"}], "doc-1", "user-1")

    assert pdfx_path == Path("user-1/pdfx_json/doc-1.json")
    assert processed_path == Path("user-1/processed_json/doc-1.json")
    assert json.loads((tmp_path / pdfx_path).read_text())["status"] == "complete"
    assert json.loads((tmp_path / processed_path).read_text())[0]["text"] == "Results"
    assert (tmp_path / "user-1" / "pdfx_json").stat().st_mode & 0o777 == 0o777
    assert (tmp_path / "user-1" / "processed_json").stat().st_mode & 0o777 == 0o777
