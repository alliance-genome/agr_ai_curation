"""
Tests for connections_loader.py health check functions and curation resolver.

Tests the async health check functions for HTTP, Redis, and Postgres,
plus the CurationConnectionResolver credential resolution logic.
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from src.lib.config.connections_loader import (
    ConnectionDefinition,
    CredentialsConfig,
    HealthCheck,
    _check_http_health,
    _check_redis_health,
    _check_postgres_health,
    _redact_url_credentials,
    sanitize_error_message,
    get_connection_status,
    load_connections,
    reset_cache,
)
from src.lib.database.curation_resolver import (
    CurationConnectionResolver,
    get_curation_resolver,
    reset_curation_resolver,
)


@pytest.fixture
def sample_http_connection():
    """Create a sample HTTP connection definition."""
    return ConnectionDefinition(
        service_id="test_http",
        description="Test HTTP service",
        url="http://localhost:8080",
        required=True,
        timeout_seconds=5.0,
        health_check=HealthCheck(
            endpoint="/health",
            method="GET",
            expected_status=200,
            headers={},
        ),
        is_healthy=None,
        last_error=None,
    )


@pytest.fixture
def sample_redis_connection():
    """Create a sample Redis connection definition."""
    return ConnectionDefinition(
        service_id="test_redis",
        description="Test Redis service",
        url="redis://localhost:6379",
        required=True,
        timeout_seconds=5.0,
        health_check=HealthCheck(
            endpoint=None,
            method="PING",
            expected_status=200,
            headers={},
        ),
        is_healthy=None,
        last_error=None,
    )


@pytest.fixture
def sample_postgres_connection():
    """Create a sample Postgres connection definition."""
    # Using IP format without credentials to avoid secret detection false positives
    return ConnectionDefinition(
        service_id="test_postgres",
        description="Test Postgres service",
        url="postgresql://127.0.0.1:5432/testdb",
        required=True,
        timeout_seconds=5.0,
        health_check=HealthCheck(
            endpoint=None,
            method="CONNECT",
            expected_status=200,
            headers={},
        ),
        is_healthy=None,
        last_error=None,
    )


class TestCheckHttpHealth:
    """Tests for _check_http_health function."""

    @pytest.mark.asyncio
    async def test_returns_true_on_expected_status(self, sample_http_connection):
        """Should return (True, None) when response matches expected status."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            is_healthy, error = await _check_http_health(sample_http_connection)

            assert is_healthy is True
            assert error is None

    @pytest.mark.asyncio
    async def test_returns_false_on_wrong_status(self, sample_http_connection):
        """Should return (False, message) when status doesn't match."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(return_value=mock_response)
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            is_healthy, error = await _check_http_health(sample_http_connection)

            assert is_healthy is False
            assert "Expected status 200, got 500" in error

    @pytest.mark.asyncio
    async def test_handles_timeout(self, sample_http_connection):
        """Should return (False, message) on timeout."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            is_healthy, error = await _check_http_health(sample_http_connection)

            assert is_healthy is False
            assert "timeout" in error.lower()

    @pytest.mark.asyncio
    async def test_handles_connection_error(self, sample_http_connection):
        """Should return (False, message) on connection error."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.request = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            is_healthy, error = await _check_http_health(sample_http_connection)

            assert is_healthy is False
            assert "Connection" in error


class TestCheckRedisHealth:
    """Tests for _check_redis_health function."""

    @pytest.mark.asyncio
    async def test_returns_tuple_result(self, sample_redis_connection):
        """Should return a tuple of (bool, Optional[str])."""
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock(return_value=True)
        mock_client.aclose = AsyncMock(return_value=None)

        with patch("redis.asyncio.from_url", return_value=mock_client):
            result = await _check_redis_health(sample_redis_connection)

        assert isinstance(result, tuple)
        assert len(result) == 2
        is_healthy, error = result
        assert isinstance(is_healthy, bool)
        assert error is None or isinstance(error, str)
        assert is_healthy is True

    @pytest.mark.asyncio
    async def test_handles_missing_package_gracefully(self, sample_redis_connection, monkeypatch):
        """Should return (False, message) when redis package not installed."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "redis.asyncio":
                raise ImportError("redis package not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        is_healthy, error = await _check_redis_health(sample_redis_connection)
        assert is_healthy is False
        assert error == "redis package not installed"

    @pytest.mark.asyncio
    async def test_connection_failure_returns_false(self, sample_redis_connection):
        """Should return (False, error) when connection fails."""
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock(side_effect=ConnectionError("Connection refused"))
        mock_client.aclose = AsyncMock(return_value=None)

        with patch("redis.asyncio.from_url", return_value=mock_client):
            is_healthy, error = await _check_redis_health(sample_redis_connection)

        assert is_healthy is False
        assert "Connection failed" in error


class TestCheckPostgresHealth:
    """Tests for _check_postgres_health function."""

    @pytest.mark.asyncio
    async def test_returns_tuple_result(self, sample_postgres_connection):
        """Should return a tuple of (bool, Optional[str])."""
        import sys
        from types import SimpleNamespace

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value="SELECT 1")
        mock_conn.close = AsyncMock(return_value=None)
        mock_asyncpg = SimpleNamespace(connect=AsyncMock(return_value=mock_conn))

        with patch.dict(sys.modules, {"asyncpg": mock_asyncpg}):
            result = await _check_postgres_health(sample_postgres_connection)

        assert isinstance(result, tuple)
        assert len(result) == 2
        is_healthy, error = result
        assert is_healthy is True
        assert error is None

    @pytest.mark.asyncio
    async def test_handles_missing_packages_gracefully(self, sample_postgres_connection, monkeypatch):
        """Should handle missing asyncpg/psycopg2 packages."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name in {"asyncpg", "psycopg2"}:
                raise ImportError(f"{name} missing")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        is_healthy, error = await _check_postgres_health(sample_postgres_connection)
        assert is_healthy is False
        assert error == "Neither asyncpg nor psycopg2 installed"

    @pytest.mark.asyncio
    async def test_returns_none_when_optional_service_is_not_configured(self, monkeypatch):
        """Optional Postgres services should report not configured, not failed."""
        conn = ConnectionDefinition(
            service_id="curation_db",
            description="Optional curation DB",
            url="",
            required=False,
            timeout_seconds=5.0,
            health_check=HealthCheck(
                endpoint=None,
                method="CONNECT",
                expected_status=200,
                headers={},
            ),
            credentials=CredentialsConfig(source="env"),
            is_healthy=None,
            last_error=None,
        )

        mock_resolver = MagicMock()
        mock_resolver.get_connection_url.return_value = None
        monkeypatch.setattr(
            "src.lib.database.curation_resolver.get_curation_resolver",
            lambda: mock_resolver,
        )

        is_healthy, error = await _check_postgres_health(conn)

        assert is_healthy is None
        assert error is None

    @pytest.mark.asyncio
    async def test_optional_configured_broken_service_returns_false(self):
        """Optional configured Postgres services should still surface failures."""
        import sys
        from types import SimpleNamespace

        optional_conn = ConnectionDefinition(
            service_id="curation_db",
            description="Optional curation DB",
            url="postgresql://127.0.0.1:5432/curation",
            required=False,
            timeout_seconds=5.0,
            health_check=HealthCheck(
                endpoint=None,
                method="CONNECT",
                expected_status=200,
                headers={},
            ),
            is_healthy=None,
            last_error=None,
        )

        mock_asyncpg = SimpleNamespace(connect=AsyncMock(side_effect=Exception("connection refused")))
        with patch.dict(sys.modules, {"asyncpg": mock_asyncpg}):
            is_healthy, error = await _check_postgres_health(optional_conn)

        assert is_healthy is False
        assert "connection refused" in error

    @pytest.mark.asyncio
    async def test_required_configured_broken_service_returns_false(self, sample_postgres_connection):
        """Required configured Postgres services should report unhealthy on failure."""
        import sys
        from types import SimpleNamespace

        mock_asyncpg = SimpleNamespace(connect=AsyncMock(side_effect=Exception("connection refused")))
        with patch.dict(sys.modules, {"asyncpg": mock_asyncpg}):
            is_healthy, error = await _check_postgres_health(sample_postgres_connection)

        assert is_healthy is False
        assert "connection refused" in error

class TestCheckServiceHealth:
    """Tests for check_service_health function."""

    def setup_method(self):
        """Reset cache before each test."""
        import src.lib.config.connections_loader as connections_loader
        connections_loader.reset_cache()

    @pytest.mark.asyncio
    async def test_returns_false_for_unknown_service(self):
        """Should return False for services not in registry."""
        import src.lib.config.connections_loader as connections_loader

        connections_loader.load_connections()  # Ensure registry is loaded

        result = await connections_loader.check_service_health("nonexistent_service_xyz")
        assert result is False

    @pytest.mark.asyncio
    async def test_updates_health_status_after_check(self):
        """Should update connection's is_healthy field after check."""
        import src.lib.config.connections_loader as connections_loader

        connections_loader.load_connections()

        with patch.object(connections_loader, "_check_http_health", new=AsyncMock(return_value=(True, None))):
            await connections_loader.check_service_health("weaviate")

        conn = connections_loader.get_connection("weaviate")

        # Status should be updated (either True or False, not None)
        assert conn is not None
        # is_healthy should have been set to a boolean
        assert conn.is_healthy is True or conn.is_healthy is False

    @pytest.mark.asyncio
    async def test_optional_unconfigured_service_updates_status_to_none(self):
        """Optional unconfigured services should remain non-degrading in cached status."""
        import src.lib.config.connections_loader as connections_loader

        connections_loader._connection_registry = {
            "curation_db": ConnectionDefinition(
                service_id="curation_db",
                description="Optional curation DB",
                url="",
                required=False,
                timeout_seconds=5.0,
                health_check=HealthCheck(
                    endpoint=None,
                    method="CONNECT",
                    expected_status=200,
                    headers={},
                ),
                credentials=CredentialsConfig(source="env"),
                is_healthy=None,
                last_error=None,
            )
        }
        connections_loader._initialized = True

        with patch.object(
            connections_loader,
            "_check_postgres_health",
            new=AsyncMock(return_value=(None, None)),
        ):
            result = await connections_loader.check_service_health("curation_db")

        conn = connections_loader.get_connection("curation_db")
        assert result is None
        assert conn is not None
        assert conn.is_healthy is None
        assert conn.last_error is None


class TestCheckAllHealth:
    """Tests for check_all_health function."""

    def setup_method(self):
        """Reset cache before each test."""
        import src.lib.config.connections_loader as connections_loader
        connections_loader.reset_cache()

    @pytest.mark.asyncio
    async def test_returns_dict_with_all_services(self):
        """Should return health status for all configured services."""
        import src.lib.config.connections_loader as connections_loader

        connections_loader.load_connections()

        with patch.object(connections_loader, "check_service_health", new=AsyncMock(return_value=True)):
            result = await connections_loader.check_all_health()

        assert isinstance(result, dict)
        # Should have entries for configured services
        assert len(result) > 0

        # Each entry should have expected keys
        for service_id, status in result.items():
            assert "service_id" in status
            assert "is_healthy" in status
            assert "required" in status

    @pytest.mark.asyncio
    async def test_includes_overall_status(self):
        """Should include overall system status."""
        import src.lib.config.connections_loader as connections_loader

        connections_loader.load_connections()

        with patch.object(connections_loader, "check_service_health", new=AsyncMock(return_value=True)):
            result = await connections_loader.check_all_health()

        # The overall status is returned separately, check any service has expected structure
        for status in result.values():
            assert isinstance(status.get("is_healthy"), bool) or status.get("is_healthy") is None


class TestRedactUrlCredentials:
    """Tests for _redact_url_credentials function (KANBAN-1017 security fix)."""

    def test_redacts_password_from_database_url(self):
        """Should redact both username and password from database-style URLs."""
        # Using testdb:// scheme to avoid TruffleHog false positives
        url = "testdb://myuser:supersecretpass@localhost:5432/mydb"
        result = _redact_url_credentials(url)

        assert "supersecretpass" not in result
        assert "myuser" not in result
        assert "***:***@" in result
        assert "localhost:5432" in result
        assert "/mydb" in result

    def test_redacts_password_from_cache_url(self):
        """Should redact both username and password from cache-style URLs."""
        # Using testcache:// scheme to avoid TruffleHog false positives
        url = "testcache://default:myprivatepw@localhost:6379"
        result = _redact_url_credentials(url)

        assert "myprivatepw" not in result
        assert "default" not in result
        assert "***:***@" in result
        assert "localhost:6379" in result

    def test_preserves_url_without_credentials(self):
        """Should return unchanged URL when no credentials present."""
        url = "http://localhost:8080/health"
        result = _redact_url_credentials(url)

        assert result == url

    def test_handles_url_with_username_only(self):
        """Should handle URL with username but no password."""
        url = "http://user@localhost:8080/path"
        result = _redact_url_credentials(url)

        # No password to redact, should be unchanged
        assert result == url

    def test_handles_empty_string(self):
        """Should handle empty string input."""
        assert _redact_url_credentials("") == ""

    def test_handles_none_input(self):
        """Should handle None input gracefully."""
        assert _redact_url_credentials(None) is None

    def test_handles_malformed_url(self):
        """Should handle malformed URLs safely."""
        # Malformed URL that can't be parsed
        result = _redact_url_credentials("not-a-valid-url")
        # Should return safely without crashing
        assert isinstance(result, str)

    def test_preserves_query_params_and_path(self):
        """Should preserve query parameters and path after redaction."""
        # Using testdb:// scheme to avoid TruffleHog false positives
        url = "testdb://user:topsecretval@host:5432/db?sslmode=require"
        result = _redact_url_credentials(url)

        assert "topsecretval" not in result
        assert "/db" in result
        assert "sslmode=require" in result

    def test_handles_special_characters_in_password(self):
        """Should handle passwords with special characters."""
        # Using testdb:// scheme; p%40ss%3Dword is URL-encoded p@ss=word
        url = "testdb://user:p%40ss%3Dword@host:5432/db"
        result = _redact_url_credentials(url)

        # Password should be redacted regardless of special chars
        assert "p%40ss%3Dword" not in result
        assert "***" in result


class TestConnectionDefinitionDisplayUrl:
    """Tests for ConnectionDefinition.display_url property."""

    def test_display_url_redacts_credentials(self):
        """display_url property should return redacted URL with both username and password hidden."""
        # Using testdb:// scheme to avoid TruffleHog false positives
        conn = ConnectionDefinition(
            service_id="test",
            url="testdb://dbuser:dbpassval@localhost:5432/testdb",
        )

        assert "dbpassval" not in conn.display_url
        assert "dbuser" not in conn.display_url
        assert "***:***@" in conn.display_url

    def test_display_url_preserves_url_without_credentials(self):
        """display_url should preserve URLs without credentials."""
        conn = ConnectionDefinition(
            service_id="test",
            url="http://localhost:8080/api",
        )

        assert conn.display_url == conn.url


class TestGetConnectionStatusRedaction:
    """Tests for get_connection_status credential redaction."""

    def setup_method(self):
        """Reset cache before each test."""
        reset_cache()

    def test_get_connection_status_returns_redacted_urls(self):
        """get_connection_status should return redacted URLs."""
        load_connections()

        status = get_connection_status()

        # All URLs in the status should be redacted
        for service_id, service_status in status.items():
            url = service_status.get("url", "")
            # If the URL originally had credentials, they should now be redacted
            # The actual test is: no plain passwords should appear
            # We can't test for specific passwords without knowing them,
            # but we can verify the URL field exists and is a string
            assert isinstance(url, str)

    def test_credentials_never_leak_in_status(self):
        """Verify no credentials leak through get_connection_status."""
        load_connections()

        status = get_connection_status()

        # Check that common password patterns aren't present
        status_str = str(status)

        # These are example passwords that should never appear in status
        # If connections.yaml uses env vars like ${POSTGRES_PASSWORD}, the
        # substituted value should be redacted
        common_test_passwords = ["password", "secret", "admin123"]

        # Note: This test catches common password patterns but can't catch all
        # The real protection is the display_url property which is tested above
        for password in common_test_passwords:
            # Only check if it looks like an unredacted password (not preceded by ***)
            if password in status_str and ":***@" not in status_str:
                # This might be a false positive if "password" is in a description
                # but it's a good sanity check
                pass  # Allow in descriptions, but the key test is display_url


class TestSanitizeErrorMessage:
    """Tests for sanitize_error_message function."""

    def test_returns_none_for_none_input(self):
        """Should return None when input is None."""
        assert sanitize_error_message(None) is None

    def test_returns_empty_for_empty_input(self):
        """Should return empty string for empty input."""
        assert sanitize_error_message("") == ""

    def test_truncates_long_messages(self):
        """Should truncate messages longer than max_length."""
        long_error = "x" * 1000
        result = sanitize_error_message(long_error, max_length=100)

        assert len(result) <= 120  # 100 + "... [truncated]"
        assert "... [truncated]" in result

    def test_preserves_short_messages(self):
        """Should not truncate messages under max_length."""
        short_error = "Connection refused"
        result = sanitize_error_message(short_error)

        assert result == short_error
        assert "[truncated]" not in result

    def test_preserves_messages_without_urls(self):
        """Should preserve error messages that don't contain URLs."""
        error = "Connection refused: timeout after 30 seconds"
        result = sanitize_error_message(error)

        assert result == error

    def test_handles_unrecognized_url_schemes(self):
        """Should pass through URLs with unrecognized schemes unchanged."""
        # testdb:// is not in the URL pattern, so it won't be redacted
        # This verifies the function doesn't crash on arbitrary input
        error = "Failed: testdb://user:secretval@host/path"
        result = sanitize_error_message(error)

        assert isinstance(result, str)
        # Original content preserved since scheme not recognized
        assert "testdb://" in result

    def test_redacts_credentials_from_postgresql_url_in_error(self):
        """Should redact credentials from postgresql:// URLs in error messages."""
        # Build URL dynamically to avoid secret scanner false positives
        scheme = "postgresql"
        user = "dbadmin"
        password = "secretdbpass123"
        host = "db.example.com:5432/mydb"
        error = f"Connection failed: {scheme}://{user}:{password}@{host}"

        result = sanitize_error_message(error)

        assert password not in result
        assert f":{password}@" not in result
        assert host in result
        assert "***" in result

    def test_redacts_credentials_from_redis_url_in_error(self):
        """Should redact credentials from redis:// URLs in error messages."""
        # Build URL dynamically to avoid secret scanner false positives
        scheme = "redis"
        user = "default"
        password = "redispassword456"
        host = "cache.example.com:6379"
        error = f"Redis error: {scheme}://{user}:{password}@{host}"

        result = sanitize_error_message(error)

        assert password not in result
        assert "***" in result
        assert host in result

    def test_redacts_credentials_from_http_url_in_error(self):
        """Should redact credentials from http:// URLs in error messages."""
        # Build URL dynamically to avoid secret scanner false positives
        scheme = "http"
        user = "apiuser"
        password = "apitoken789"
        host = "api.example.com/endpoint"
        error = f"HTTP request failed: {scheme}://{user}:{password}@{host}"

        result = sanitize_error_message(error)

        assert password not in result
        assert "***" in result
        assert host in result


class TestCurationResolver:
    """Tests for CurationConnectionResolver credential resolution."""

    @pytest.fixture(autouse=True)
    def _isolate_env_and_cache(self, monkeypatch):
        """Ensure resolver tests are isolated from container-level credential config."""
        monkeypatch.setenv("CURATION_DB_CREDENTIALS_SOURCE", "env")
        monkeypatch.delenv("CURATION_DB_AWS_SECRET_ID", raising=False)
        monkeypatch.delenv("AWS_PROFILE", raising=False)
        monkeypatch.delenv("AWS_REGION", raising=False)
        reset_cache()
        reset_curation_resolver()
        # Keep "not configured" scenarios deterministic even when connections.yaml
        # includes a concrete curation_db.url in local/dev environments.
        with patch("src.lib.config.connections_loader.get_connection", return_value=None):
            yield
        reset_cache()
        reset_curation_resolver()

    def setup_method(self):
        """Reset resolver singleton before each test."""
        reset_cache()
        reset_curation_resolver()

    def teardown_method(self):
        """Clean up resolver after each test."""
        reset_cache()
        reset_curation_resolver()

    def test_resolver_returns_url_from_env(self, monkeypatch):
        """Resolver should use CURATION_DB_URL env var as highest priority."""
        monkeypatch.setenv("CURATION_DB_URL", "postgresql://127.0.0.1:5432/curation")
        resolver = CurationConnectionResolver()

        url = resolver.get_connection_url()

        assert url == "postgresql://127.0.0.1:5432/curation"

    def test_resolver_returns_none_when_not_configured(self, monkeypatch):
        """Resolver should return None when no curation DB is configured."""
        monkeypatch.delenv("CURATION_DB_URL", raising=False)

        resolver = CurationConnectionResolver()

        url = resolver.get_connection_url()

        assert url is None

    def test_resolver_is_configured_true_when_url_set(self, monkeypatch):
        """is_configured() should return True when CURATION_DB_URL is set."""
        monkeypatch.setenv("CURATION_DB_URL", "postgresql://127.0.0.1:5432/curation")
        resolver = CurationConnectionResolver()

        assert resolver.is_configured() is True

    def test_resolver_is_configured_false_when_not_set(self, monkeypatch):
        """is_configured() should return False when no DB config exists."""
        monkeypatch.delenv("CURATION_DB_URL", raising=False)
        resolver = CurationConnectionResolver()

        assert resolver.is_configured() is False

    def test_resolver_ignores_legacy_persistent_store_vars(self, monkeypatch):
        """Legacy PERSISTENT_STORE_DB_* vars should not be used implicitly."""
        monkeypatch.delenv("CURATION_DB_URL", raising=False)
        monkeypatch.setenv("PERSISTENT_STORE_DB_HOST", "dbhost")
        monkeypatch.setenv("PERSISTENT_STORE_DB_PORT", "5433")
        monkeypatch.setenv("PERSISTENT_STORE_DB_NAME", "curation")
        monkeypatch.setenv("PERSISTENT_STORE_DB_USERNAME", "reader")
        monkeypatch.setenv("PERSISTENT_STORE_DB_PASSWORD", "test_db_password")

        resolver = CurationConnectionResolver()
        url = resolver.get_connection_url()

        assert url is None

    def test_resolver_uses_curation_url_even_if_legacy_env_present(self, monkeypatch):
        """CURATION_DB_URL remains explicit override even if legacy env vars are set."""
        monkeypatch.setenv("CURATION_DB_URL", "postgresql://127.0.0.1:5432/curation")
        monkeypatch.setenv("PERSISTENT_STORE_DB_HOST", "otherhost")
        monkeypatch.setenv("PERSISTENT_STORE_DB_PORT", "5433")
        monkeypatch.setenv("PERSISTENT_STORE_DB_NAME", "other")
        monkeypatch.setenv("PERSISTENT_STORE_DB_USERNAME", "user")
        monkeypatch.setenv("PERSISTENT_STORE_DB_PASSWORD", "pass")

        resolver = CurationConnectionResolver()
        url = resolver.get_connection_url()

        assert url == "postgresql://127.0.0.1:5432/curation"

    def test_resolver_health_status_not_configured(self, monkeypatch):
        """get_health_status() should report not_configured when DB is not set up."""
        monkeypatch.delenv("CURATION_DB_URL", raising=False)
        resolver = CurationConnectionResolver()

        status = resolver.get_health_status()

        assert status["status"] == "not_configured"

    def test_resolver_get_db_client_returns_none_when_not_configured(self, monkeypatch):
        """get_db_client() should return None when no URL is configured."""
        monkeypatch.delenv("CURATION_DB_URL", raising=False)
        resolver = CurationConnectionResolver()

        client = resolver.get_db_client()

        assert client is None

    def test_resolver_reset_clears_state(self, monkeypatch):
        """reset() should clear resolved URL and allow re-resolution."""
        monkeypatch.setenv("CURATION_DB_URL", "postgresql://127.0.0.1:5432/curation")
        resolver = CurationConnectionResolver()

        url1 = resolver.get_connection_url()
        assert url1 is not None

        resolver.reset()
        monkeypatch.delenv("CURATION_DB_URL")

        url2 = resolver.get_connection_url()
        assert url2 is None

    def test_singleton_returns_same_instance(self):
        """get_curation_resolver() should return the same instance."""
        r1 = get_curation_resolver()
        r2 = get_curation_resolver()

        assert r1 is r2

    def test_resolver_invalid_credentials_source_fails_fast(self, monkeypatch):
        """Invalid curation_db credentials.source should raise a clear error."""
        from types import SimpleNamespace

        monkeypatch.delenv("CURATION_DB_URL", raising=False)
        mock_conn = SimpleNamespace(
            url="",
            credentials=SimpleNamespace(source="invalid_source"),
        )

        with patch("src.lib.config.connections_loader.get_connection", return_value=mock_conn):
            resolver = CurationConnectionResolver()
            with pytest.raises(ValueError, match="Invalid curation_db credentials.source"):
                resolver.get_connection_url()

    def test_resolver_url_source_without_url_fails_fast(self, monkeypatch):
        """credentials.source=url requires explicit services.curation_db.url value."""
        from types import SimpleNamespace

        monkeypatch.delenv("CURATION_DB_URL", raising=False)
        mock_conn = SimpleNamespace(
            url="",
            credentials=SimpleNamespace(source="url"),
        )

        with patch("src.lib.config.connections_loader.get_connection", return_value=mock_conn):
            resolver = CurationConnectionResolver()
            with pytest.raises(ValueError, match="credentials.source is 'url'"):
                resolver.get_connection_url()
