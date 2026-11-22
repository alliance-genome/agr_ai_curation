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
from src.lib.document_sources.file_listing import FileListing, ListingRoute
from src.lib.document_sources.file_listing_service import FileListingUnavailable
from src.lib.document_sources.models import (
    DocumentSourceReferenceNotFound,
    ProviderBearerKind,
    SourceReference,
)


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
                     expected_reference_id=None):
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
            expected_reference_id="55",
        )
    assert raised.value.code == "paper_reference_mismatch"


@pytest.mark.asyncio
async def test_no_curator_token_means_no_listing():
    provider = SimpleNamespace(provider_id="made_up_provider")
    with pytest.raises(FileListingUnavailable) as raised:
        await file_listing_service._list(
            provider, "MADEUP:1", _context(token=None), (), route=ListingRoute.IDENTIFIER, provider_observations={},
        )
    assert raised.value.code == "document_source_curator_token_unavailable"
