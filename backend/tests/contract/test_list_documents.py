"""HTTP contracts for the current vector-backed document listing API."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def listing_api(monkeypatch):
    from src.api import documents
    from src.api.auth import _get_user_from_cookie_impl

    app = FastAPI()
    app.include_router(documents.router)
    listing = AsyncMock(return_value={"documents": [], "total": 0, "limit": 20, "offset": 0})
    monkeypatch.setattr(documents, "list_documents", listing)
    return app, listing, _get_user_from_cookie_impl


@pytest.fixture
def authenticated_listing(listing_api):
    app, listing, auth_dependency = listing_api
    app.dependency_overrides[auth_dependency] = lambda: {"sub": "contract-curator"}
    with TestClient(app) as client:
        yield client, listing


def test_listing_requires_authentication(listing_api):
    app, listing, _ = listing_api
    with TestClient(app) as client:
        response = client.get("/weaviate/documents")
    assert response.status_code == 401
    listing.assert_not_awaited()


def test_listing_flat_response_preserves_aggregate_and_metadata(authenticated_listing):
    client, listing = authenticated_listing
    row = {
        "document_id": str(uuid4()),
        "user_id": "contract-curator",
        "filename": "paper.pdf",
        "title": "SQL title",
        "status": "FAILED",
        "upload_timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
        "file_size_bytes": 2048,
        "weaviate_tenant": "contract_curator",
        "error_message": "Import failed",
    }
    # Aggregate precedes the SQL ownership post-filter and can exceed page length.
    listing.return_value = {"documents": [row], "total": 2, "limit": 20, "offset": 0}
    response = client.get("/weaviate/documents")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    data = response.json()
    assert set(data) == {"documents", "total", "limit", "offset"}
    assert data["total"] == 2 and len(data["documents"]) == 1
    for key, value in row.items():
        if key != "upload_timestamp":
            assert data["documents"][0][key] == value


def test_listing_dispatches_filters_sort_and_pagination(authenticated_listing):
    client, listing = authenticated_listing
    listing.return_value = {"documents": [], "total": 0, "limit": 7, "offset": 14}
    response = client.get("/weaviate/documents", params={
        "page": 3, "page_size": 7, "search": "paper",
        "embedding_status": "completed", "sort_by": "filename", "sort_order": "asc",
        "date_from": "2026-01-01T00:00:00Z", "date_to": "2026-02-01T00:00:00Z",
        "min_vector_count": 0, "max_vector_count": 5,
    })
    assert response.status_code == 200
    assert response.json() == {"documents": [], "total": 0, "limit": 7, "offset": 14}
    user, filters, pagination = listing.await_args.args
    assert user == "contract-curator"
    assert pagination == {"page": 3, "page_size": 7, "sort_by": "filename", "sort_order": "asc"}
    assert filters.search_term == "paper"
    assert filters.embedding_status == ["completed"]
    assert filters.min_vector_count == 0 and filters.max_vector_count == 5
    assert filters.date_from == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert filters.date_to == datetime(2026, 2, 1, tzinfo=timezone.utc)


def test_listing_empty_default_response(authenticated_listing):
    client, listing = authenticated_listing
    response = client.get("/weaviate/documents")
    assert response.status_code == 200
    assert response.json() == {"documents": [], "total": 0, "limit": 20, "offset": 0}
    assert listing.await_args.args[2] == {
        "page": 1, "page_size": 20, "sort_by": "creationDate", "sort_order": "desc",
    }


@pytest.mark.parametrize("params,status", [
    ({"page": 0}, 422),
    ({"page_size": 0}, 422),
    ({"page_size": 101}, 422),
    ({"min_vector_count": -1}, 422),
    ({"min_vector_count": 5, "max_vector_count": 1}, 400),
    ({"date_from": "2026-02-01", "date_to": "2026-01-01"}, 400),
    ({"sort_order": "invalid"}, 422),
    ({"embedding_status": "invalid"}, 422),
])
def test_invalid_listing_never_dispatches(authenticated_listing, params, status):
    client, listing = authenticated_listing
    assert client.get("/weaviate/documents", params=params).status_code == status
    listing.assert_not_awaited()
