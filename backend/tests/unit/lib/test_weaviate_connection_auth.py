from unittest.mock import MagicMock, patch

from src.lib.weaviate_client import connection as connection_module


def test_docker_weaviate_connection_passes_api_key_credentials():
    client = MagicMock()
    client.is_ready.return_value = True
    auth = object()
    connection = connection_module.WeaviateConnection(
        url="http://weaviate:8080",
        api_key="production-key",
    )
    # The production helper is intentionally a singleton; isolate this test
    # from any instance created during earlier startup tests.
    connection.url = "http://weaviate:8080"
    connection.api_key = "production-key"
    connection._client = None

    with (
        patch.object(connection_module.Auth, "api_key", return_value=auth) as make_auth,
        patch.object(connection_module.weaviate, "connect_to_local", return_value=client) as connect,
    ):
        assert connection.connect() is client

    make_auth.assert_called_once_with("production-key")
    connect.assert_called_once_with(
        host="weaviate",
        port=8080,
        auth_credentials=auth,
    )


def test_default_connection_reads_weaviate_api_key_from_environment(monkeypatch):
    created = MagicMock()
    monkeypatch.setenv("WEAVIATE_HOST", "weaviate")
    monkeypatch.setenv("WEAVIATE_PORT", "8080")
    monkeypatch.setenv("WEAVIATE_SCHEME", "http")
    monkeypatch.setenv("WEAVIATE_API_KEY", "production-key")
    monkeypatch.setattr(connection_module, "_connection", None)

    with patch.object(connection_module, "WeaviateConnection", return_value=created) as constructor:
        assert connection_module.get_connection() is created

    constructor.assert_called_once_with(
        url="http://weaviate:8080",
        api_key="production-key",
    )
    created.connect.assert_called_once_with()
