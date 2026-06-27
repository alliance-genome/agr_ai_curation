"""Unit tests for document-source identifier import endpoint wiring."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException
from starlette.requests import Request

from src.api import documents
from src.lib.document_sources.identifier_import import IdentifierImportValidationError
from src.models.api_schemas import DocumentSourceIdentifierImportRequest


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/weaviate/documents/import/source-identifiers",
            "headers": [(b"cookie", b"auth_token=request-token")],
        }
    )


def _resolve_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/weaviate/documents/resolve/source-identifiers",
            "headers": [(b"cookie", b"auth_token=request-token")],
        }
    )


@pytest.mark.asyncio
async def test_import_documents_by_source_identifiers_delegates_to_service(monkeypatch):
    captured = {}

    class _FakeIdentifierImportService:
        async def import_identifiers(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                to_dict=lambda: {
                    "results": [
                        {
                            "identifier": "123",
                            "normalized_identifier": "PMID:123",
                            "status": "imported",
                            "message": "Import queued for background processing.",
                            "document_id": "doc-1",
                            "job_id": "job-1",
                            "filename": "paper.pdf",
                        }
                    ],
                    "requested_count": 1,
                    "imported_count": 1,
                    "duplicate_count": 0,
                    "error_count": 0,
                }
            )

    monkeypatch.setattr(documents, "identifier_import_service", _FakeIdentifierImportService())
    monkeypatch.setattr(documents, "external_document_source_import_enabled", lambda: True)

    response = await documents.import_documents_by_source_identifiers(
        payload=DocumentSourceIdentifierImportRequest(identifiers="123"),
        background_tasks=BackgroundTasks(),
        request=_request(),
        user={"sub": "user-1", "groups": ["FBStaff"]},
    )

    assert response["imported_count"] == 1
    assert captured["identifiers"] == "123"
    assert captured["user"]["sub"] == "user-1"
    assert captured["document_source_context"].provider_groups == ("FBStaff",)


@pytest.mark.asyncio
async def test_resolve_documents_by_source_identifiers_delegates_to_service(monkeypatch):
    captured = {}

    class _FakeIdentifierImportService:
        async def resolve_identifiers(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                to_dict=lambda: {
                    "results": [
                        {
                            "identifier": "123",
                            "normalized_identifier": "PMID:123",
                            "status": "resolved",
                            "message": "Ready to import.",
                            "filename": "paper.pdf",
                        }
                    ],
                    "requested_count": 1,
                    "imported_count": 0,
                    "duplicate_count": 0,
                    "error_count": 0,
                }
            )

    monkeypatch.setattr(documents, "identifier_import_service", _FakeIdentifierImportService())
    monkeypatch.setattr(documents, "external_document_source_import_enabled", lambda: True)

    response = await documents.resolve_documents_by_source_identifiers(
        payload=DocumentSourceIdentifierImportRequest(identifiers="123"),
        request=_resolve_request(),
        user={"sub": "user-1", "groups": ["FBStaff"]},
    )

    assert response["results"][0]["status"] == "resolved"
    assert captured["identifiers"] == "123"
    assert captured["user"]["sub"] == "user-1"
    assert captured["document_source_context"].provider_groups == ("FBStaff",)


@pytest.mark.asyncio
async def test_import_documents_by_source_identifiers_rejects_disabled_import(monkeypatch):
    class _FakeIdentifierImportService:
        async def import_identifiers(self, **_kwargs):
            raise AssertionError("service should not be called when import is disabled")

    monkeypatch.setattr(documents, "identifier_import_service", _FakeIdentifierImportService())
    monkeypatch.setattr(documents, "external_document_source_import_enabled", lambda: False)

    with pytest.raises(HTTPException) as exc:
        await documents.import_documents_by_source_identifiers(
            payload=DocumentSourceIdentifierImportRequest(identifiers="123"),
            background_tasks=BackgroundTasks(),
            request=_request(),
            user={"sub": "user-1"},
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == "Document-source import is disabled"


@pytest.mark.asyncio
async def test_resolve_documents_by_source_identifiers_rejects_disabled_import(monkeypatch):
    class _FakeIdentifierImportService:
        async def resolve_identifiers(self, **_kwargs):
            raise AssertionError("service should not be called when import is disabled")

    monkeypatch.setattr(documents, "identifier_import_service", _FakeIdentifierImportService())
    monkeypatch.setattr(documents, "external_document_source_import_enabled", lambda: False)

    with pytest.raises(HTTPException) as exc:
        await documents.resolve_documents_by_source_identifiers(
            payload=DocumentSourceIdentifierImportRequest(identifiers="123"),
            request=_resolve_request(),
            user={"sub": "user-1"},
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == "Document-source import is disabled"


@pytest.mark.asyncio
async def test_import_documents_by_source_identifiers_returns_400_for_invalid_batch(monkeypatch):
    class _FakeIdentifierImportService:
        async def import_identifiers(self, **_kwargs):
            raise IdentifierImportValidationError("At most 10 identifiers can be imported at once")

    monkeypatch.setattr(documents, "identifier_import_service", _FakeIdentifierImportService())
    monkeypatch.setattr(documents, "external_document_source_import_enabled", lambda: True)

    with pytest.raises(HTTPException) as exc:
        await documents.import_documents_by_source_identifiers(
            payload=DocumentSourceIdentifierImportRequest(identifiers="1,2,3"),
            background_tasks=BackgroundTasks(),
            request=_request(),
            user={"sub": "user-1"},
        )

    assert exc.value.status_code == 400
