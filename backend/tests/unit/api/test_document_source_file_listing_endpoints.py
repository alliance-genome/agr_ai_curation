"""Endpoint wiring for the provider file listing (P6): codes only, owned papers only."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api import documents
from src.lib.document_sources import file_listing_service
from src.lib.document_sources.access import DocumentSourceRequestContext
from src.lib.document_sources.file_listing import FileListing
from src.lib.document_sources.file_listing_service import FileListingUnavailable
from src.lib.document_sources.models import DocumentSourceReferenceNotFound, ProviderBearerKind


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/weaviate/document-sources/files", "headers": []})


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
    files=(), not_shown_count=2, duplicate_copies_count=0, check_version="1-x",
)


@pytest.fixture
def wired(monkeypatch):
    provider = _Provider()
    monkeypatch.setattr(documents, "external_document_source_import_enabled", lambda: True)

    async def context(**_):
        return _context()

    monkeypatch.setattr(documents, "_document_source_context_or_503", context)
    monkeypatch.setattr(
        "src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider,
    )
    return provider


@pytest.mark.asyncio
async def test_listing_by_identifier_returns_codes_and_counts(monkeypatch, wired):
    async def listed(identifier, *, provider, context):
        assert identifier == "MADEUP:1" and provider is wired
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
async def test_listing_is_refused_when_provider_import_is_disabled(monkeypatch):
    monkeypatch.setattr(documents, "external_document_source_import_enabled", lambda: False)
    with pytest.raises(HTTPException) as raised:
        await documents.list_document_source_files_by_identifier(
            _request(), identifier="MADEUP:1", user={"sub": "curator"},
        )
    assert raised.value.status_code == 503


@pytest.mark.asyncio
async def test_an_uploaded_paper_has_no_provider_file_list(monkeypatch):
    """Including an upload linked to its reference (Q5): only provider imports list files."""

    monkeypatch.setattr(file_listing_service, "_paper_lookup", lambda *_: (None, None, ()))
    with pytest.raises(FileListingUnavailable) as raised:
        await file_listing_service.list_files_for_paper(
            uuid4(), owner_user_id=7, provider=_Provider(), context=_context(),
        )
    assert raised.value.code == "not_imported_from_provider"


@pytest.mark.asyncio
async def test_a_paper_listing_uses_the_papers_reference_and_its_file_ids(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        file_listing_service, "_paper_lookup", lambda *_: ("made_up_provider", "55", ("10", "20")),
    )

    async def listed(provider, lookup, context, files_in_paper):
        captured.update(lookup=lookup, files=files_in_paper)
        return EMPTY

    monkeypatch.setattr(file_listing_service, "_list", listed)
    await file_listing_service.list_files_for_paper(
        uuid4(), owner_user_id=7, provider=_Provider(), context=_context(),
    )
    assert captured == {"lookup": "55", "files": ("10", "20")}


@pytest.mark.asyncio
async def test_no_curator_token_means_no_listing():
    provider = SimpleNamespace(provider_id="made_up_provider")
    with pytest.raises(FileListingUnavailable) as raised:
        await file_listing_service._list(provider, "MADEUP:1", _context(token=None), ())
    assert raised.value.code == "document_source_curator_token_unavailable"
