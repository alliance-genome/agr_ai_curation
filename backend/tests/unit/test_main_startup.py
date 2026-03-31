"""Unit tests covering the FastAPI startup sequence."""

import importlib
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI


def _main_module():
    """Always resolve the current main module to avoid stale reload references."""
    return importlib.import_module("main")


@pytest.fixture(autouse=True)
def set_pdf_extraction_timeout(monkeypatch):
    """Set PDF_EXTRACTION_TIMEOUT to valid value for all tests in this module."""
    monkeypatch.setenv("PDF_EXTRACTION_TIMEOUT", "300")
    monkeypatch.delenv("AGR_BOOTSTRAP_PACKAGE_ENVS_ON_START", raising=False)
    monkeypatch.delenv("AGR_PACKAGE_ENVS_PREPARED", raising=False)


def make_connection(list_all_return=None):
    connection = MagicMock()
    connection.connect_to_weaviate = AsyncMock()
    connection.close = AsyncMock()

    client = MagicMock()
    client.collections.list_all.return_value = list_all_return if list_all_return is not None else []

    @contextmanager
    def session():
        yield client

    connection.session.side_effect = session
    return connection, client


class TestInitializeWeaviateCollections:
    @pytest.mark.asyncio
    async def test_creates_missing_collections(self):
        connection, client = make_connection(list_all_return=["PDFDocument"])

        await _main_module().initialize_weaviate_collections(connection)

        created = client.collections.create.call_args_list
        assert len(created) == 1
        assert created[0].kwargs["name"] == "DocumentChunk"

    @pytest.mark.asyncio
    async def test_skips_existing_collections(self):
        connection, client = make_connection(list_all_return=["DocumentChunk", "PDFDocument"])

        await _main_module().initialize_weaviate_collections(connection)
        client.collections.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_all_when_none_exist(self):
        connection, client = make_connection(list_all_return=[])

        await _main_module().initialize_weaviate_collections(connection)
        created_names = {call.kwargs["name"] for call in client.collections.create.call_args_list}
        assert created_names == {"DocumentChunk", "PDFDocument"}


class TestLifespan:
    """Tests for the application lifespan context manager.

    These tests mock the various initialization subsystems to focus on
    the Weaviate connection lifecycle.
    """

    @pytest.fixture(autouse=True)
    def mock_subsystems(self):
        """Mock all initialization subsystems for lifespan tests."""
        with patch("main.SessionLocal") as mock_session, \
             patch("src.lib.config.prompt_loader.load_prompts", return_value={"base_prompts": 0, "group_rules": 0}), \
             patch(
                 "src.lib.agent_studio.system_agent_sync.sync_system_agents",
                 return_value={
                     "inserted": 0,
                     "updated": 0,
                     "reactivated": 0,
                     "deactivated": 0,
                     "discovered": 0,
                 },
             ), \
             patch("src.lib.prompts.cache.initialize"), \
             patch("src.lib.config.groups_loader.load_groups", return_value={}), \
             patch("src.lib.agent_studio.catalog_service.validate_active_agent_output_schemas") as mock_validate_schemas, \
             patch(
                 "src.lib.agent_studio.runtime_validation.validate_and_cache_agent_runtime_contracts",
                 return_value={
                     "status": "healthy",
                     "strict_mode": False,
                     "validated_at": "2026-02-25T00:00:00+00:00",
                     "errors": [],
                     "warnings": [],
                     "agents": [],
                     "summary": {"agent_count": 0},
                 },
             ) as mock_validate_agents, \
             patch("src.lib.config.connections_loader.load_connections", return_value=[]), \
             patch("src.lib.config.connections_loader.get_required_connections", return_value=[]), \
             patch("src.lib.config.connections_loader.get_optional_connections", return_value=[]), \
             patch(
                 "src.lib.config.provider_validation.validate_and_cache_provider_runtime_contracts",
                 return_value={
                     "status": "healthy",
                     "strict_mode": True,
                     "validated_at": "2026-02-23T00:00:00+00:00",
                     "errors": [],
                     "warnings": [],
                     "providers": [],
                     "models": [],
                     "summary": {},
                 },
             ), \
             patch("src.lib.openai_agents.langfuse_client.is_langfuse_configured", return_value=False):
            # Mock the database session context manager
            mock_db = MagicMock()
            mock_session.return_value = mock_db
            yield {
                "db": mock_db,
                "validate_schemas": mock_validate_schemas,
                "validate_agents": mock_validate_agents,
            }

    @pytest.mark.asyncio
    @patch("main.WeaviateConnection")
    @patch("main.initialize_weaviate_collections")
    async def test_successful_startup(self, mock_init, mock_conn_cls):
        connection, client = make_connection()
        mock_conn_cls.return_value = connection

        with patch("main.maybe_prepare_package_tool_environments_on_start", return_value=False) as mock_prewarm:
            app = FastAPI()

            async with _main_module().lifespan(app):
                mock_conn_cls.assert_called_once()
                connection.connect_to_weaviate.assert_awaited_once()
                client.collections.list_all.assert_called()
                mock_init.assert_awaited_once_with(connection)
            mock_prewarm.assert_called_once_with()

    @pytest.mark.asyncio
    @patch("main.WeaviateConnection")
    @patch("main.initialize_weaviate_collections")
    async def test_fail_fast_on_package_environment_prepare_error(self, mock_init, mock_conn_cls):
        connection, _ = make_connection()
        mock_conn_cls.return_value = connection

        app = FastAPI()

        with patch("main.maybe_prepare_package_tool_environments_on_start", side_effect=RuntimeError("package bootstrap failed")):
            with pytest.raises(RuntimeError, match="package bootstrap failed"):
                async with _main_module().lifespan(app):
                    pass
        mock_conn_cls.assert_not_called()
        mock_init.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("main.WeaviateConnection")
    async def test_fail_fast_on_connection_error(self, mock_conn_cls):
        connection, _ = make_connection()
        connection.connect_to_weaviate.side_effect = RuntimeError("boom")
        mock_conn_cls.return_value = connection

        app = FastAPI()

        with pytest.raises(RuntimeError, match="boom"):
            async with _main_module().lifespan(app):
                pass

    @pytest.mark.asyncio
    @patch("main.WeaviateConnection")
    @patch("main.initialize_weaviate_collections", side_effect=RuntimeError("bad collections"))
    async def test_fail_fast_on_collection_error(self, mock_init, mock_conn_cls):
        connection, _ = make_connection()
        mock_conn_cls.return_value = connection

        app = FastAPI()

        with pytest.raises(RuntimeError, match="bad collections"):
            async with _main_module().lifespan(app):
                pass
        connection.close.assert_not_called()

    @pytest.mark.asyncio
    @patch("main.WeaviateConnection")
    @patch("main.initialize_weaviate_collections")
    @patch.dict(os.environ, {
        "WEAVIATE_HOST": "example",
        "WEAVIATE_PORT": "9090",
        "WEAVIATE_SCHEME": "https",
    })
    async def test_uses_environment_configuration(self, mock_init, mock_conn_cls):
        connection, _ = make_connection()
        mock_conn_cls.return_value = connection

        app = FastAPI()

        async with _main_module().lifespan(app):
            mock_conn_cls.assert_called_with(url="https://example:9090")
            mock_init.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("main.WeaviateConnection")
    @patch("main.initialize_weaviate_collections")
    async def test_cleanup_on_shutdown(self, mock_init, mock_conn_cls):
        connection, _ = make_connection()
        mock_conn_cls.return_value = connection

        app = FastAPI()

        async with _main_module().lifespan(app):
            pass

        connection.close.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("main.WeaviateConnection")
    @patch("main.initialize_weaviate_collections")
    async def test_calls_output_schema_validation(self, mock_init, mock_conn_cls, mock_subsystems):
        connection, _ = make_connection()
        mock_conn_cls.return_value = connection

        app = FastAPI()

        async with _main_module().lifespan(app):
            pass

        mock_subsystems["validate_schemas"].assert_called_once_with(mock_subsystems["db"])

    @pytest.mark.asyncio
    @patch("main.WeaviateConnection")
    @patch("main.initialize_weaviate_collections")
    async def test_calls_agent_runtime_validation(self, mock_init, mock_conn_cls, mock_subsystems):
        connection, _ = make_connection()
        mock_conn_cls.return_value = connection

        app = FastAPI()

        async with _main_module().lifespan(app):
            pass

        mock_subsystems["validate_agents"].assert_called_once()

    @pytest.mark.asyncio
    @patch("main.WeaviateConnection")
    async def test_fail_fast_on_output_schema_validation_error(self, mock_conn_cls, mock_subsystems):
        connection, _ = make_connection()
        mock_conn_cls.return_value = connection
        mock_subsystems["validate_schemas"].side_effect = RuntimeError("unknown output schema")

        app = FastAPI()

        with pytest.raises(RuntimeError, match="unknown output schema"):
            async with _main_module().lifespan(app):
                pass

    @pytest.mark.asyncio
    @patch("main.WeaviateConnection")
    @patch("main.initialize_weaviate_collections")
    @patch("src.lib.config.provider_validation.validate_and_cache_provider_runtime_contracts")
    async def test_fail_fast_on_provider_validation_error(self, mock_validate, _mock_init, mock_conn_cls):
        connection, _ = make_connection()
        mock_conn_cls.return_value = connection
        mock_validate.side_effect = RuntimeError("LLM provider validation failed: missing OPENAI_API_KEY")

        app = FastAPI()

        with pytest.raises(RuntimeError, match="LLM provider validation failed"):
            async with _main_module().lifespan(app):
                pass
        mock_conn_cls.assert_not_called()
        _mock_init.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("main.WeaviateConnection")
    @patch("main.initialize_weaviate_collections")
    @patch("src.lib.agent_studio.runtime_validation.validate_and_cache_agent_runtime_contracts")
    async def test_fail_fast_on_agent_runtime_validation_error(self, mock_validate, _mock_init, mock_conn_cls):
        connection, _ = make_connection()
        mock_conn_cls.return_value = connection
        mock_validate.side_effect = RuntimeError("Agent runtime validation failed: ca_bad tools drifted")

        app = FastAPI()

        with pytest.raises(RuntimeError, match="Agent runtime validation failed"):
            async with _main_module().lifespan(app):
                pass


@pytest.mark.asyncio
async def test_lifespan_supports_core_only_runtime_packages(monkeypatch, tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    runtime_root = tmp_path / "runtime"
    packages_dir = runtime_root / "packages"
    shutil.copytree(repo_root / "packages" / "core", packages_dir / "agr.core")

    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(packages_dir))
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-large")
    monkeypatch.setenv("EMBEDDING_TOKEN_PREFLIGHT_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_MODEL_TOKEN_LIMIT", "8192")
    monkeypatch.setenv("EMBEDDING_TOKEN_SAFETY_MARGIN", "512")
    monkeypatch.setenv("CONTENT_PREVIEW_CHARS", "400")

    from src.lib.agent_studio import catalog_service
    from src.lib.config import agent_loader, prompt_loader, schema_discovery
    from src.lib.config.models_loader import reset_cache as reset_model_cache
    from src.lib.config.providers_loader import reset_cache as reset_provider_cache

    agent_loader.reset_cache()
    prompt_loader.reset_cache()
    schema_discovery.reset_cache()
    reset_model_cache()
    reset_provider_cache()
    catalog_service.clear_package_tool_runtime_caches()

    main = _main_module()
    connection, client = make_connection()

    class QueryStub:
        def filter(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def all(self):
            return []

        def first(self):
            return None

    class DBStub:
        def __init__(self):
            self.added = []
            self.committed = False

        def query(self, *_args, **_kwargs):
            return QueryStub()

        def execute(self, *_args, **_kwargs):
            result = MagicMock()
            result.scalar.return_value = True
            return result

        def add(self, item):
            self.added.append(item)

        def commit(self):
            self.committed = True

        def rollback(self):
            return None

        def close(self):
            return None

    db = DBStub()

    with patch.object(main, "WeaviateConnection", return_value=connection), \
         patch.object(main, "initialize_weaviate_collections", AsyncMock()), \
         patch.object(main, "SessionLocal", return_value=db), \
         patch(
             "src.lib.config.provider_validation.validate_and_cache_provider_runtime_contracts",
             return_value={
                 "status": "healthy",
                 "strict_mode": True,
                 "validated_at": "2026-03-18T00:00:00+00:00",
                 "errors": [],
                 "warnings": [],
                 "providers": [],
                 "models": [],
                 "summary": {"provider_count": 0, "model_count": 0},
             },
         ), \
         patch("src.lib.prompts.cache.initialize"), \
         patch("src.lib.config.groups_loader.load_groups", return_value={}), \
         patch(
             "src.lib.agent_studio.system_agent_sync.sync_system_agents",
             return_value={
                 "inserted": 0,
                 "updated": 0,
                 "reactivated": 0,
                 "deactivated": 0,
                 "discovered": 2,
             },
         ), \
         patch("src.lib.agent_studio.catalog_service.validate_active_agent_output_schemas"), \
         patch("src.lib.agent_studio.runtime_validation._fetch_active_agents", lambda: []), \
         patch("src.lib.openai_agents.langfuse_client.is_langfuse_configured", return_value=False):
        async with main.lifespan(FastAPI()):
            pass

    client.collections.list_all.assert_called()
    assert db.committed is True
    assert any(getattr(item, "agent_name", None) == "supervisor" for item in db.added)
