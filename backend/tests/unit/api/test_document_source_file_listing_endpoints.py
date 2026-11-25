"""Endpoint wiring for the provider file listing (P6): codes only, owned papers only."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import sentry_sdk
from fastapi import HTTPException
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.transport import Transport
from starlette.requests import Request

from src.api import documents
from src.lib.document_sources import file_listing_service
from src.lib.document_sources.access import DocumentSourceRequestContext
from src.lib.document_sources.file_listing import FileListing, ListingRoute, listing_to_payload
from src.lib.document_sources.file_listing_service import FileListingUnavailable
from src.lib.document_sources.models import (
    DocumentSourceReferenceNotFound,
    ProviderBearerKind,
    SourceReference,
)
from src.lib.observability.sentry import before_send


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/weaviate/document-sources/files", "headers": []})


@contextmanager
def _capture_sentry_events(monkeypatch, request_url):
    monkeypatch.setenv("SENTRY_CONTENT_REDACTION_ENABLED", "false")
    events = []

    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):
            events.extend(item.get_event() for item in envelope.items if item.headers["type"] == "event")

    with sentry_sdk.Client(
        dsn="https://public@example.invalid/1",
        transport=MemoryTransport,
        default_integrations=False,
        integrations=[LoggingIntegration(event_level=logging.ERROR)],
        before_send=before_send,
        include_local_variables=False,
    ) as sdk, sentry_sdk.isolation_scope() as scope:
        scope.set_client(sdk)
        scope.add_event_processor(lambda event, hint: {**event, "request": {"url": request_url}})
        yield events
        sdk.flush()


def _context(token: str | None = "curator-token") -> DocumentSourceRequestContext:
    return DocumentSourceRequestContext(
        provider_groups=("group-a",), authorized_group_ids=("group-a",), bearer_kind=ProviderBearerKind.CURATOR,
        curator_token=token,
    )


class _Provider:
    provider_id = "made_up_provider"

    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


EMPTY = FileListing(
    provider="made_up_provider", reference_id="1", reference_curie="MADEUP:1", main_status="ready",
    files=(), not_shown_count=2, duplicate_copies_count=0, check_version="1-x", existing_document_id=None,
)


@pytest.fixture
def wired(monkeypatch):
    provider = _Provider()
    monkeypatch.setattr(documents, "external_document_source_import_enabled", lambda: True)

    async def context(**_):
        return _context()

    monkeypatch.setattr(documents, "_document_source_context_or_503", context)

    async def provision(_principal):
        return SimpleNamespace(id=7)

    monkeypatch.setattr(documents, "provision_user_async", provision)
    monkeypatch.setattr(documents, "principal_from_claims", lambda claims: claims)
    monkeypatch.setattr(
        "src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider,
    )
    return provider


@pytest.mark.asyncio
async def test_listing_by_identifier_returns_codes_and_counts(monkeypatch, wired):
    async def listed(identifier, *, owner_user_id, provider, context):
        assert identifier == "MADEUP:1" and provider is wired and owner_user_id == 7
        return EMPTY

    monkeypatch.setattr(file_listing_service, "list_files_for_identifier", listed)
    payload = await documents.list_document_source_files_by_identifier(
        _request(), identifier="MADEUP:1", user={"sub": "curator"},
    )
    assert payload["not_shown_count"] == 2 and payload["files"] == []
    assert payload["main_status"] == "ready"
    assert wired.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (FileListingUnavailable("invalid_identifier"), 400, "invalid_identifier"),
        (FileListingUnavailable("document_source_curator_token_unavailable"), 409,
         "document_source_curator_token_unavailable"),
        (DocumentSourceReferenceNotFound("made-up"), 404, "reference_not_found"),
    ],
)
async def test_listing_failures_are_codes(monkeypatch, wired, error, status, code):
    async def listed(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(file_listing_service, "list_files_for_identifier", listed)
    with pytest.raises(HTTPException) as raised:
        await documents.list_document_source_files_by_identifier(
            _request(), identifier="MADEUP:1", user={"sub": "curator"},
        )
    assert raised.value.status_code == status
    assert raised.value.detail == {"error": code}
    assert wired.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["identifier", "paper"])
@pytest.mark.parametrize("reporter_fails", [False, True], ids=["reported", "reporter-raises"])
async def test_unexpected_listing_failure_is_sanitized(monkeypatch, wired, caplog, route, reporter_fails):
    error = ConnectionError("private provider response")
    error.__cause__ = OSError("private transport token")
    error.__cause__.__context__ = ValueError("private provider context")
    lookup = AsyncMock(side_effect=error)
    user = {"sub": "private-curator"}
    if route == "paper":
        identifier = str(uuid4())
        url = f"/documents/{identifier}/source-files"
        monkeypatch.setattr(file_listing_service, "list_files_for_paper", lookup)
        listing = documents.list_document_source_files_for_paper(_request(), document_id=identifier, user=user)
    else:
        identifier = "SOURCE:private-listing-id"
        url = f"/document-sources/files?identifier={identifier}"
        monkeypatch.setattr(file_listing_service, "list_files_for_identifier", lookup)
        listing = documents.list_document_source_files_by_identifier(_request(), identifier=identifier, user=user)

    reporter = Mock(wraps=documents.report_runtime_exception)
    if reporter_fails:
        reporter.side_effect = RuntimeError("private reporter detail")
    monkeypatch.setattr(documents, "report_runtime_exception", reporter)
    monkeypatch.setenv("RUNTIME_OBSERVABILITY_CONTEXT_VALUE_MAX_CHARS", "10")
    with _capture_sentry_events(monkeypatch, url) as events:
        with pytest.raises(HTTPException) as raised:
            await listing

    assert raised.value.status_code == 500
    assert raised.value.detail == {"error": "file_listing_failed"}
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert wired.closed
    reporter.assert_called_once()
    reported, = reporter.call_args.args
    assert reported.__cause__ is None and reported.__context__ is None
    record, = [record for record in caplog.records if record.name == documents.__name__]
    assert record.levelno == logging.ERROR
    assert record.sentry_skip_event is True and record.exc_info is None
    for private in (identifier, user["sub"], str(error), str(error.__cause__),
                    str(error.__cause__.__context__), "private reporter detail"):
        assert private not in repr(events)
        assert private not in caplog.text
    if reporter_fails:
        assert events == []
    else:
        event, = events
        assert "request" not in event
        assert event["level"] == "error"
        assert event["contexts"]["runtime_exception"] == {
            "component": "document_file_listing",
            "operation": "file_listing_failed",
            "exception_type": "Connection",
        }
        value, = event["exception"]["values"]
        assert value["type"] == "RuntimeError"
        assert value["value"] == "Document-source file listing failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [False, True], ids=["listed", "not-found"])
@pytest.mark.parametrize("reporter_fails", [False, True], ids=["reported", "reporter-raises"])
async def test_provider_cleanup_failure_preserves_listing(monkeypatch, wired, caplog, missing, reporter_fails):
    identifier = "SOURCE:private-listing-id"
    close_error = OSError("private provider response")
    close_error.__cause__ = ValueError("private transport detail")
    close = AsyncMock(side_effect=close_error)
    monkeypatch.setattr(wired, "aclose", close)
    lookup = AsyncMock(return_value=EMPTY)
    if missing:
        lookup.side_effect = DocumentSourceReferenceNotFound(identifier)
    monkeypatch.setattr(file_listing_service, "list_files_for_identifier", lookup)
    reporter = Mock(wraps=documents.report_runtime_exception)
    if reporter_fails:
        reporter.side_effect = RuntimeError("private reporter detail")
    monkeypatch.setattr(documents, "report_runtime_exception", reporter)
    with _capture_sentry_events(monkeypatch, f"/documents/{identifier}") as events:
        listing = documents.list_document_source_files_by_identifier(
            _request(), identifier=identifier, user={"sub": "private-curator"},
        )
        if missing:
            with pytest.raises(HTTPException) as raised:
                await listing
            assert raised.value.status_code == 404
            assert raised.value.detail == {"error": "reference_not_found"}
        else:
            assert await listing == listing_to_payload(EMPTY)

    close.assert_awaited_once_with()
    reporter.assert_called_once()
    error, = reporter.call_args.args
    assert error.__cause__ is None and error.__context__ is None
    record, = [record for record in caplog.records if record.name == documents.__name__]
    assert record.sentry_skip_event is True and record.exc_info is None
    for private in (
        identifier, "private-curator", str(close_error), str(close_error.__cause__), "private reporter detail",
    ):
        assert private not in repr(events)
        assert private not in caplog.text
    if reporter_fails:
        assert events == []
    else:
        event, = events
        assert "request" not in event
        assert event["level"] == "warning"
        assert event["contexts"]["runtime_exception"] == {
            "component": "document_file_listing",
            "operation": "provider_cleanup_failed",
            "exception_type": "OSError",
        }
        value, = event["exception"]["values"]
        assert value["type"] == "RuntimeError"
        assert value["value"] == "Document-source file listing provider cleanup failed"


@pytest.mark.asyncio
async def test_listing_is_refused_when_provider_import_is_disabled(monkeypatch):
    monkeypatch.setattr(documents, "external_document_source_import_enabled", lambda: False)
    with pytest.raises(HTTPException) as raised:
        await documents.list_document_source_files_by_identifier(
            _request(), identifier="MADEUP:1", user={"sub": "curator"},
        )
    assert raised.value.status_code == 503


def _paper(provider="made_up_provider", reference_id="55", curie="MADEUP:55", file_ids=("10", "20")):
    return file_listing_service._PaperSource(
        provider=provider, reference_id=reference_id, reference_curie=curie, file_ids=file_ids,
        provider_observations={},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("paper", "code"),
    [
        (_paper(provider=None, reference_id=None, curie=None, file_ids=()), "not_imported_from_provider"),
        (_paper(provider="another_provider"), "not_imported_from_provider"),
        (_paper(curie=None), "paper_reference_incomplete"),
        (_paper(reference_id=None), "paper_reference_incomplete"),
    ],
)
async def test_only_a_paper_imported_from_this_provider_with_its_reference_is_listed(monkeypatch, paper, code):
    monkeypatch.setattr(file_listing_service, "_paper_lookup", lambda *_: paper)
    with pytest.raises(FileListingUnavailable) as raised:
        await file_listing_service.list_files_for_paper(
            uuid4(), owner_user_id=7, provider=_Provider(), context=_context(),
        )
    assert raised.value.code == code


@pytest.mark.asyncio
async def test_a_paper_listing_resolves_the_papers_curie_and_requires_its_reference_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(file_listing_service, "_paper_lookup", lambda *_: _paper())

    async def listed(provider, lookup, context, files_in_paper, *, route, provider_observations,
                     owner_user_id, expected_reference_id=None, caller_reports_download_outages=False):
        assert not caller_reports_download_outages
        captured.update(
            lookup=lookup, files=files_in_paper, expected=expected_reference_id, route=route,
            observations=provider_observations,
        )
        return EMPTY

    monkeypatch.setattr(file_listing_service, "_list", listed)
    await file_listing_service.list_files_for_paper(
        uuid4(), owner_user_id=7, provider=_Provider(), context=_context(),
    )
    assert captured == {
        "lookup": "MADEUP:55", "files": ("10", "20"), "expected": "55", "route": ListingRoute.PAPER,
        "observations": {},
    }


@pytest.mark.asyncio
async def test_a_reference_that_resolves_to_another_id_is_refused(monkeypatch):
    class Resolving(_Provider):
        async def resolve_reference(self, identifier, *, request_bearer_token=None):
            return SourceReference(provider="made_up_provider", reference_id="56", reference_curie=identifier)

        async def list_artifacts(self, *_args, **_kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("another reference's files were listed")

    with pytest.raises(FileListingUnavailable) as raised:
        await file_listing_service._list(
            Resolving(), "MADEUP:55", _context(), (), route=ListingRoute.PAPER, provider_observations={},
            owner_user_id=7, expected_reference_id="55",
        )
    assert raised.value.code == "paper_reference_mismatch"


@pytest.mark.asyncio
async def test_no_curator_token_means_no_listing():
    provider = SimpleNamespace(provider_id="made_up_provider")
    with pytest.raises(FileListingUnavailable) as raised:
        await file_listing_service._list(
            provider, "MADEUP:1", _context(token=None), (), route=ListingRoute.IDENTIFIER, provider_observations={},
            owner_user_id=7,
        )
    assert raised.value.code == "document_source_curator_token_unavailable"
