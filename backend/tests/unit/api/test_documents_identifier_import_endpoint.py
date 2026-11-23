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
async def test_document_source_provider_configuration_exposes_registry_presentation(
    monkeypatch,
):
    monkeypatch.setattr(documents, "external_document_source_import_enabled", lambda: True)
    monkeypatch.setattr(documents, "get_document_source_provider", lambda: "Example_Source")
    monkeypatch.setattr(
        documents,
        "get_document_source_provider_metadata",
        lambda provider: {
            "display_label": "Example Source",
            "identifier_help_label": "Use example identifiers.",
            "identifier_examples": ["SOURCE:1", "SOURCE:2"],
        }
        if provider == "example_source"
        else None,
    )

    response = await documents.get_document_source_provider_configuration(
        user={"sub": "user-1"},
    )

    assert response.model_dump() == {
        "provider": "example_source",
        "import_enabled": True,
        "presentation": {
            "display_label": "Example Source",
            "identifier_help_label": "Use example identifiers.",
            "identifier_examples": ["SOURCE:1", "SOURCE:2"],
        },
    }


@pytest.mark.asyncio
async def test_document_source_provider_configuration_is_generic_when_disabled(
    monkeypatch,
):
    monkeypatch.setattr(documents, "external_document_source_import_enabled", lambda: False)
    monkeypatch.setattr(
        documents,
        "get_document_source_provider_metadata",
        lambda _provider: pytest.fail("disabled configuration must not load presentation"),
    )
    response = await documents.get_document_source_provider_configuration(
        user={"sub": "user-1"},
    )

    assert response.model_dump() == {
        "provider": None,
        "import_enabled": False,
        "presentation": None,
    }


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


# --- KANBAN-1976 / ALL-1460, P7b: chosen supplements


@pytest.mark.asyncio
async def test_import_forwards_the_chosen_supplements(monkeypatch):
    from src.models.api_schemas import DocumentSourceSupplementChoice

    captured = {}

    class _FakeIdentifierImportService:
        async def import_identifiers(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(to_dict=lambda: {"results": [], "requested_count": 1,
                                                     "imported_count": 0, "duplicate_count": 0,
                                                     "error_count": 0})

    monkeypatch.setattr(documents, "identifier_import_service", _FakeIdentifierImportService())
    monkeypatch.setattr(documents, "external_document_source_import_enabled", lambda: True)
    choice = DocumentSourceSupplementChoice(
        identifier="123", provider_file_id="30", md5="md5-30", converted_md5=None,
    )

    await documents.import_documents_by_source_identifiers(
        payload=DocumentSourceIdentifierImportRequest(identifiers="123", supplements=[choice]),
        background_tasks=BackgroundTasks(), request=_request(), user={"sub": "user-1"},
    )

    assert captured["supplements"] == [choice]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["supplement_identifier_unknown", "file_chosen_twice", "paper_file_limit"])
async def test_a_selection_that_cannot_be_imported_is_a_422_with_its_code(monkeypatch, code):
    from src.lib.document_sources.identifier_import import SupplementSelectionError

    class _FakeIdentifierImportService:
        async def import_identifiers(self, **_kwargs):
            raise SupplementSelectionError(code)

    monkeypatch.setattr(documents, "identifier_import_service", _FakeIdentifierImportService())
    monkeypatch.setattr(documents, "external_document_source_import_enabled", lambda: True)

    with pytest.raises(HTTPException) as exc:
        await documents.import_documents_by_source_identifiers(
            payload=DocumentSourceIdentifierImportRequest(identifiers="123"),
            background_tasks=BackgroundTasks(), request=_request(), user={"sub": "user-1"},
        )

    assert (exc.value.status_code, exc.value.detail) == (422, {"code": code})


def test_a_supplement_choice_rejects_unknown_fields():
    from pydantic import ValidationError

    from src.models.api_schemas import DocumentSourceSupplementChoice

    with pytest.raises(ValidationError):
        DocumentSourceSupplementChoice(identifier="123", provider_file_id="30", file_id="x")
