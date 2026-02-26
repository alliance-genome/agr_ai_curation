"""Unit tests for Weaviate settings helpers and async adapters."""

import copy

import pytest

from src.lib.weaviate_client import settings


class _Session:
    def __init__(self, client):
        self.client = client

    def __enter__(self):
        return self.client

    def __exit__(self, exc_type, exc, tb):
        return False


class _ClientSchema:
    def __init__(self, existing_classes=None, fail_on_create=False):
        self._existing_classes = existing_classes or []
        self.fail_on_create = fail_on_create
        self.created_classes = []

    def get(self):
        return {"classes": [{"class": c} for c in self._existing_classes]}

    def create_class(self, schema):
        if self.fail_on_create:
            raise RuntimeError("create failed")
        self.created_classes.append(schema)


class _Client:
    def __init__(self, schema):
        self.schema = schema


class _Connection:
    def __init__(self, client):
        self.client = client

    def session(self):
        return _Session(self.client)


@pytest.fixture(autouse=True)
def _reset_settings_state(monkeypatch):
    original_config = copy.deepcopy(settings._current_config)
    original_connection = settings.connection_module._connection
    yield
    monkeypatch.setattr(settings, "_current_config", original_config)
    monkeypatch.setattr(settings.connection_module, "_connection", original_connection)


def test_get_embedding_config_returns_copy():
    config = settings.get_embedding_config()
    config["modelProvider"] = "changed"
    assert settings._current_config["embedding"]["modelProvider"] == "openai"


def test_update_embedding_config_accepts_snake_case():
    result = settings.update_embedding_config(
        {"provider": "cohere", "model": "embed-english-v3.0", "batch_size": 25}
    )
    assert result["success"] is True
    assert settings._current_config["embedding"]["modelProvider"] == "cohere"
    assert settings._current_config["embedding"]["modelName"] == "embed-english-v3.0"
    assert settings._current_config["embedding"]["dimensions"] == 1024
    assert settings._current_config["embedding"]["batchSize"] == 25


def test_update_embedding_config_accepts_camel_case():
    result = settings.update_embedding_config(
        {
            "modelProvider": "openai",
            "modelName": "text-embedding-3-large",
            "batchSize": 12,
        }
    )
    assert result["success"] is True
    assert settings._current_config["embedding"]["modelName"] == "text-embedding-3-large"
    assert settings._current_config["embedding"]["dimensions"] == 3072
    assert settings._current_config["embedding"]["batchSize"] == 12


def test_update_embedding_config_invalid_provider():
    result = settings.update_embedding_config({"provider": "bad-provider", "model": "x"})
    assert result["success"] is False
    assert result["error"]["code"] == "CONFIG_UPDATE_FAILED"


def test_get_collection_settings_returns_copy():
    config = settings.get_collection_settings()
    config["collectionName"] = "changed"
    assert settings._current_config["database"]["collectionName"] == "PDFDocuments"


def test_get_available_models_returns_flat_list():
    models = settings.get_available_models()
    assert any(
        m["provider"] == "openai" and m["modelName"] == "text-embedding-3-small"
        for m in models
    )
    assert all("dimensions" in m for m in models)


def test_update_schema_creates_collections_when_missing(monkeypatch):
    client_schema = _ClientSchema(existing_classes=["OtherClass"])
    monkeypatch.setattr(settings.connection_module, "_connection", _Connection(_Client(client_schema)))

    result = settings.update_schema({"collectionName": "PDFDocumentsNew", "schemaVersion": "2.0.0"})

    assert result["success"] is True
    created_names = [schema["class"] for schema in client_schema.created_classes]
    assert "PDFDocumentsNew" in created_names
    assert "DocumentChunk" in created_names
    assert settings._current_config["database"]["collectionName"] == "PDFDocumentsNew"


def test_update_schema_noop_when_collection_exists(monkeypatch):
    client_schema = _ClientSchema(existing_classes=["PDFDocuments"])
    monkeypatch.setattr(settings.connection_module, "_connection", _Connection(_Client(client_schema)))

    result = settings.update_schema({"collectionName": "PDFDocuments"})

    assert result["success"] is True
    assert client_schema.created_classes == []


def test_update_schema_raises_without_connection(monkeypatch):
    monkeypatch.setattr(settings.connection_module, "_connection", None)
    with pytest.raises(RuntimeError, match="No Weaviate connection established"):
        settings.update_schema({"collectionName": "PDFDocuments"})


@pytest.mark.asyncio
async def test_async_get_embedding_config_maps_keys():
    result = await settings.get_embedding_config_async()
    assert result["provider"] == "openai"
    assert result["model"] == settings._current_config["embedding"]["modelName"]
    assert "batch_size" in result


@pytest.mark.asyncio
async def test_async_update_embedding_config_success_and_failure():
    ok = await settings.update_embedding_config_async(
        {"provider": "openai", "model": "text-embedding-3-small", "batch_size": 7}
    )
    assert ok["success"] is True

    with pytest.raises(RuntimeError, match="Failed to update embedding config"):
        await settings.update_embedding_config_async(
            {"provider": "bad-provider", "model": "does-not-exist"}
        )


@pytest.mark.asyncio
async def test_async_get_collection_settings_maps_keys():
    result = await settings.get_collection_settings_async()
    assert result["collection_name"] == settings._current_config["database"]["collectionName"]
    assert result["schema_version"] == settings._current_config["database"]["schemaVersion"]


@pytest.mark.asyncio
async def test_async_update_schema_adapter(monkeypatch):
    async def _to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(settings.asyncio, "to_thread", _to_thread)
    async_result = {"success": True, "config": {}}
    monkeypatch.setattr(settings, "update_schema", lambda _payload: async_result)
    ok = await settings.update_schema_async({"schemaVersion": "3.0.0"})
    assert ok["success"] is True
    assert ok["applied_changes"] == []

    monkeypatch.setattr(
        settings,
        "update_schema",
        lambda _payload: {"success": False, "message": "Failed to update schema: boom"},
    )
    with pytest.raises(RuntimeError, match="Failed to update schema"):
        await settings.update_schema_async({"schemaVersion": "3.1.0"})


@pytest.mark.asyncio
async def test_async_get_available_models_groups_by_provider():
    grouped = await settings.get_available_models_async()
    assert "openai" in grouped
    assert any(m["name"] == "text-embedding-3-small" for m in grouped["openai"])
