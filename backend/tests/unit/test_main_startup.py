"""Unit tests covering the FastAPI startup sequence."""

import os
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

import main


@pytest.fixture(autouse=True)
def set_docling_timeout(monkeypatch):
    """Set DOCLING_TIMEOUT to valid value for all tests in this module."""
    monkeypatch.setenv("DOCLING_TIMEOUT", "300")


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

        await main.initialize_weaviate_collections(connection)

        created = client.collections.create.call_args_list
        assert len(created) == 1
        assert created[0].kwargs["name"] == "DocumentChunk"

    @pytest.mark.asyncio
    async def test_skips_existing_collections(self):
        connection, client = make_connection(list_all_return=["DocumentChunk", "PDFDocument"])

        await main.initialize_weaviate_collections(connection)
        client.collections.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_all_when_none_exist(self):
        connection, client = make_connection(list_all_return=[])

        await main.initialize_weaviate_collections(connection)
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
             patch("src.lib.prompts.cache.initialize"), \
             patch("src.lib.config.groups_loader.load_groups", return_value={}), \
             patch("src.lib.config.connections_loader.load_connections", return_value=[]), \
             patch("src.lib.config.connections_loader.get_required_connections", return_value=[]), \
             patch("src.lib.config.connections_loader.get_optional_connections", return_value=[]), \
             patch("src.lib.openai_agents.langfuse_client.is_langfuse_configured", return_value=False):
            # Mock the database session context manager
            mock_db = MagicMock()
            mock_session.return_value = mock_db
            yield

    @pytest.mark.asyncio
    @patch("main.WeaviateConnection")
    @patch("main.initialize_weaviate_collections")
    async def test_successful_startup(self, mock_init, mock_conn_cls):
        connection, client = make_connection()
        mock_conn_cls.return_value = connection

        app = FastAPI()

        async with main.lifespan(app):
            mock_conn_cls.assert_called_once()
            connection.connect_to_weaviate.assert_awaited_once()
            client.collections.list_all.assert_called()
            mock_init.assert_awaited_once_with(connection)

    @pytest.mark.asyncio
    @patch("main.WeaviateConnection")
    async def test_fail_fast_on_connection_error(self, mock_conn_cls):
        connection, _ = make_connection()
        connection.connect_to_weaviate.side_effect = RuntimeError("boom")
        mock_conn_cls.return_value = connection

        app = FastAPI()

        with pytest.raises(RuntimeError, match="boom"):
            async with main.lifespan(app):
                pass

    @pytest.mark.asyncio
    @patch("main.WeaviateConnection")
    @patch("main.initialize_weaviate_collections", side_effect=RuntimeError("bad collections"))
    async def test_fail_fast_on_collection_error(self, mock_init, mock_conn_cls):
        connection, _ = make_connection()
        mock_conn_cls.return_value = connection

        app = FastAPI()

        with pytest.raises(RuntimeError, match="bad collections"):
            async with main.lifespan(app):
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

        async with main.lifespan(app):
            mock_conn_cls.assert_called_with(url="https://example:9090")
            mock_init.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("main.WeaviateConnection")
    @patch("main.initialize_weaviate_collections")
    async def test_cleanup_on_shutdown(self, mock_init, mock_conn_cls):
        connection, _ = make_connection()
        mock_conn_cls.return_value = connection

        app = FastAPI()

        async with main.lifespan(app):
            pass

        connection.close.assert_awaited_once()
