"""Unit tests for schema API endpoints."""

import pytest
from fastapi import HTTPException

from src.api import schema


@pytest.mark.asyncio
async def test_get_schema_endpoint_success(monkeypatch):
    async def _settings():
        return {
            "collection_name": "PDFDocument",
            "schema_version": "v2",
            "vectorizer": "none",
            "embedding_model": "text-embedding-3-small",
            "replication_factor": 2,
        }

    monkeypatch.setattr(schema, "get_collection_settings", _settings)
    result = await schema.get_schema_endpoint({"sub": "dev-user-123"})

    assert result["collection"] == "PDFDocument"
    assert result["version"] == "v2"
    assert result["vectorizer"]["model"] == "text-embedding-3-small"
    assert result["replicationConfig"]["factor"] == 2
    assert isinstance(result["properties"], list)
    assert len(result["properties"]) > 3


@pytest.mark.asyncio
async def test_get_schema_endpoint_failure(monkeypatch):
    async def _boom():
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(schema, "get_collection_settings", _boom)

    with pytest.raises(HTTPException) as exc:
        await schema.get_schema_endpoint({"sub": "dev-user-123"})

    assert exc.value.status_code == 500
    assert "Failed to retrieve schema" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_update_schema_endpoint_rejects_invalid_property_dtype():
    with pytest.raises(HTTPException) as exc:
        await schema.update_schema_endpoint(
            {
                "properties": [
                    {"name": "bad", "dataType": ["uuid"]},
                ]
            },
            {"sub": "dev-user-123"},
        )

    assert exc.value.status_code == 400
    assert "Invalid dataType" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_update_schema_endpoint_rejects_invalid_distance():
    with pytest.raises(HTTPException) as exc:
        await schema.update_schema_endpoint(
            {
                "vectorIndexConfig": {"distance": "chebyshev"},
            },
            {"sub": "dev-user-123"},
        )

    assert exc.value.status_code == 400
    assert "Invalid distance metric" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_update_schema_endpoint_success(monkeypatch):
    async def _update(payload):
        assert payload["vectorIndexConfig"]["distance"] == "cosine"
        return {"applied_changes": ["vectorIndexConfig.distance"]}

    monkeypatch.setattr(schema, "update_schema", _update)

    result = await schema.update_schema_endpoint(
        {
            "properties": [{"name": "title", "dataType": ["text"]}],
            "vectorIndexConfig": {"distance": "cosine"},
        },
        {"sub": "dev-user-123"},
    )

    assert result["success"] is True
    assert "Schema updated successfully" in result["message"]
    assert result["applied_changes"] == ["vectorIndexConfig.distance"]


@pytest.mark.asyncio
async def test_update_schema_endpoint_handles_update_failure(monkeypatch):
    async def _boom(_payload):
        raise RuntimeError("schema backend error")

    monkeypatch.setattr(schema, "update_schema", _boom)

    with pytest.raises(HTTPException) as exc:
        await schema.update_schema_endpoint(
            {
                "properties": [{"name": "title", "dataType": ["text"]}],
            },
            {"sub": "dev-user-123"},
        )

    assert exc.value.status_code == 500
    assert "Failed to update schema" in str(exc.value.detail)
