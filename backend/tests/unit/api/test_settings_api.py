"""Unit tests for settings API endpoints."""

import pytest
from fastapi import HTTPException

from src.api import settings
from src.models.api_schemas import EmbeddingConfiguration, WeaviateSettings


@pytest.mark.asyncio
async def test_get_settings_endpoint_success(monkeypatch):
    async def _embedding():
        return {"provider": "openai", "model": "text-embedding-3-small", "dimensions": 1536, "batch_size": 20}

    async def _collection():
        return {
            "collection_name": "PDFDocument",
            "schema_version": "v1",
            "replication_factor": 1,
            "consistency": "eventual",
            "vector_index_type": "hnsw",
        }

    async def _models():
        return {
            "openai": [{"name": "text-embedding-3-small", "dimensions": 1536}],
            "voyage": [{"name": "voyage-3-large", "dimensions": 1024}],
        }

    monkeypatch.setattr(settings, "get_embedding_config", _embedding)
    monkeypatch.setattr(settings, "get_collection_settings", _collection)
    monkeypatch.setattr(settings, "get_available_models", _models)

    result = await settings.get_settings_endpoint({"sub": "dev-user-123"})
    assert result.embedding.model_provider == "openai"
    assert result.embedding.batch_size == 20
    assert result.database.collection_name == "PDFDocument"
    assert len(result.available_models) == 2


@pytest.mark.asyncio
async def test_get_settings_endpoint_raises_500_on_error(monkeypatch):
    async def _boom():
        raise RuntimeError("settings backend down")

    monkeypatch.setattr(settings, "get_embedding_config", _boom)
    monkeypatch.setattr(settings, "get_collection_settings", _boom)
    monkeypatch.setattr(settings, "get_available_models", _boom)

    with pytest.raises(HTTPException) as exc:
        await settings.get_settings_endpoint({"sub": "dev-user-123"})

    assert exc.value.status_code == 500
    assert "Failed to retrieve settings" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_update_settings_endpoint_updates_embedding(monkeypatch):
    update_calls = []

    async def _models():
        return {
            "openai": [
                {"name": "text-embedding-3-small", "dimensions": 1536},
                {"name": "text-embedding-3-large", "dimensions": 3072},
            ]
        }

    async def _update_embedding(payload):
        update_calls.append(payload)

    monkeypatch.setattr(settings, "get_available_models", _models)
    monkeypatch.setattr(settings, "update_embedding_config", _update_embedding)

    result = await settings.update_settings_endpoint(
        embedding_config=EmbeddingConfiguration(
            model_provider="openai",
            model_name="text-embedding-3-large",
            dimensions=3072,
            batch_size=8,
        ),
        database_settings=None,
        user={"sub": "dev-user-123"},
    )

    assert result["success"] is True
    assert result["updated"] == ["embedding_configuration"]
    assert update_calls == [
        {
            "provider": "openai",
            "model": "text-embedding-3-large",
            "dimensions": 3072,
            "batch_size": 8,
        }
    ]


@pytest.mark.asyncio
async def test_update_settings_endpoint_rejects_unknown_model(monkeypatch):
    async def _models():
        return {"openai": [{"name": "text-embedding-3-small", "dimensions": 1536}]}

    async def _update_embedding(_payload):
        raise AssertionError("Should not be called for invalid model")

    monkeypatch.setattr(settings, "get_available_models", _models)
    monkeypatch.setattr(settings, "update_embedding_config", _update_embedding)

    with pytest.raises(HTTPException) as exc:
        await settings.update_settings_endpoint(
            embedding_config=EmbeddingConfiguration(
                model_provider="openai",
                model_name="not-a-model",
                dimensions=999,
                batch_size=10,
            ),
            database_settings=None,
            user={"sub": "dev-user-123"},
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_update_settings_endpoint_database_warning(monkeypatch):
    async def _models():
        return {"openai": [{"name": "text-embedding-3-small", "dimensions": 1536}]}

    async def _update_embedding(_payload):
        return None

    monkeypatch.setattr(settings, "get_available_models", _models)
    monkeypatch.setattr(settings, "update_embedding_config", _update_embedding)

    result = await settings.update_settings_endpoint(
        embedding_config=None,
        database_settings=WeaviateSettings(
            collection_name="PDFDocument",
            schema_version="v1",
            replication_factor=2,
            consistency="eventual",
            vector_index_type="hnsw",
        ),
        user={"sub": "dev-user-123"},
    )

    assert result["success"] is True
    assert "database_settings" in result["updated"]
    assert any("cluster restart" in warning.lower() for warning in result["warnings"])


@pytest.mark.asyncio
async def test_update_settings_endpoint_no_payload_returns_warning(monkeypatch):
    async def _models():
        return {"openai": [{"name": "text-embedding-3-small", "dimensions": 1536}]}

    async def _update_embedding(_payload):
        return None

    monkeypatch.setattr(settings, "get_available_models", _models)
    monkeypatch.setattr(settings, "update_embedding_config", _update_embedding)

    result = await settings.update_settings_endpoint(
        embedding_config=None,
        database_settings=None,
        user={"sub": "dev-user-123"},
    )

    assert result["success"] is False
    assert result["updated"] == []
    assert any("No settings provided to update" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_update_settings_endpoint_internal_error_returns_500(monkeypatch):
    async def _models():
        raise RuntimeError("model backend unavailable")

    async def _update_embedding(_payload):
        return None

    monkeypatch.setattr(settings, "get_available_models", _models)
    monkeypatch.setattr(settings, "update_embedding_config", _update_embedding)

    with pytest.raises(HTTPException) as exc:
        await settings.update_settings_endpoint(
            embedding_config=EmbeddingConfiguration(
                model_provider="openai",
                model_name="text-embedding-3-small",
                dimensions=1536,
                batch_size=10,
            ),
            database_settings=None,
            user={"sub": "dev-user-123"},
        )

    assert exc.value.status_code == 500
    assert "Failed to update settings" in str(exc.value.detail)
