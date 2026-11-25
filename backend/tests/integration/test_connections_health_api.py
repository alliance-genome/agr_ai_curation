"""Integration tests for public and admin connection health endpoints.

Verifies:
- GET /api/health/connections is public and returns only minimal status fields
  (no URLs, hostnames, ports, raw errors, or internal service IDs).
- GET /api/admin/health/connections and /api/admin/health/connections/{service_id}
  require admin authorization (401 unauthenticated, 403 non-admin, 200 admin)
  and redact credentials from URLs and errors.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock

import src.api.admin.auth as admin_auth

# Common credential patterns that should NEVER appear in API responses
CREDENTIAL_PATTERNS = [
    # Common test/default passwords
    "password",
    "secret",
    "admin123",
    "changeme",
    # URL credential patterns (unredacted)
    ":password@",
    ":secret@",
    # Env var names that might leak
    "POSTGRES_PASSWORD",
    "REDIS_PASSWORD",
]


@pytest.fixture
def mock_connections_loaded():
    """Mock connections as loaded with test data."""
    from src.lib.config.connections_loader import (
        ConnectionDefinition,
        HealthCheck,
    )

    # Create test connections with credentials in URLs
    # Using testdb:// scheme to avoid TruffleHog detection
    test_connections = {
        "test_db": ConnectionDefinition(
            service_id="test_db",
            description="Test database",
            url="testdb://dbuser:dbsecretpass@db-private.local:5432/testdb",
            health_check=HealthCheck(method="CONNECT"),
            required=True,
            is_healthy=True,
            last_error=None,
        ),
        "test_cache": ConnectionDefinition(
            service_id="test_cache",
            description="Test cache",
            url="testcache://cacheuser:cachesecretpw@cache-private.local:6379",
            health_check=HealthCheck(method="PING"),
            required=False,
            is_healthy=False,
            last_error="Connection refused to cache-private.local:6379",
        ),
        "test_api": ConnectionDefinition(
            service_id="test_api",
            description="Test API (no credentials)",
            url="http://api-private.local:8080/api",
            health_check=HealthCheck(endpoint="/health", method="GET"),
            required=False,
            is_healthy=True,
            last_error=None,
        ),
    }

    with patch("src.lib.config.connections_loader._connection_registry", test_connections), \
         patch("src.lib.config.connections_loader._initialized", True):
        yield test_connections


@pytest_asyncio.fixture
async def test_client():
    """Create async test client for the FastAPI app."""
    from main import app
    from src.lib.config.connections_monitor import ConnectionsHealthMonitor

    owner = ConnectionsHealthMonitor()
    app.state.connections_health_monitor = owner

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        try:
            yield client
        finally:
            await owner.close()


@pytest.fixture
def admin_headers(monkeypatch):
    """Configure an authenticated admin API-key identity."""
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.setenv("TESTING_API_KEY", "integration-admin-key")
    monkeypatch.setenv("TESTING_API_KEY_EMAIL", "admin@example.org")
    monkeypatch.setattr(admin_auth, "get_admin_emails", lambda: {"admin@example.org"})
    return {"X-API-Key": "integration-admin-key"}


class TestConnectionsHealthEndpointSecurity:
    """Test public vs admin connection health authorization and redaction."""

    @pytest.mark.asyncio
    async def test_public_connections_route_exposes_only_minimal_status(
        self, test_client, mock_connections_loaded, monkeypatch
    ):
        """GET /api/health/connections works without auth and omits all internal details."""
        monkeypatch.setenv("DEV_MODE", "false")
        monkeypatch.delenv("TESTING_API_KEY", raising=False)
        monkeypatch.setattr(admin_auth, "get_admin_emails", lambda: {"admin@example.org"})

        with patch(
            "src.lib.config.connections_loader.check_all_health",
            new_callable=AsyncMock,
        ) as mock_check:
            from src.lib.config.connections_loader import get_connection_status
            mock_check.return_value = get_connection_status()

            response = await test_client.get("/api/health/connections")

            assert response.status_code == 200
            data = response.json()
            assert set(data.keys()) == {
                "observed_at",
                "status",
                "required_healthy",
                "unhealthy_services",
            }
            assert data["status"] == "degraded"
            assert data["required_healthy"] is True
            assert data["unhealthy_services"] == [
                {"description": "Test cache", "required": False}
            ]

            raw = response.text
            for forbidden in (
                "url",
                "last_error",
                "service_id",
                "test_db",
                "test_cache",
                "test_api",
                "db-private.local",
                "cache-private.local",
                "api-private.local",
                "5432",
                "6379",
                "8080",
                "dbsecretpass",
                "cachesecretpw",
                "Connection refused",
            ):
                assert forbidden not in raw

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "path",
        ["/api/admin/health/connections", "/api/admin/health/connections/test_db"],
    )
    async def test_admin_routes_require_authentication_and_admin_role(
        self, test_client, mock_connections_loaded, monkeypatch, path
    ):
        """Admin connection health routes return 401 unauthenticated and 403 for curators."""
        monkeypatch.setenv("DEV_MODE", "false")
        monkeypatch.delenv("TESTING_API_KEY", raising=False)
        monkeypatch.delenv("AUTH_PROVIDER", raising=False)
        monkeypatch.setattr(admin_auth, "get_admin_emails", lambda: {"admin@example.org"})

        anon = await test_client.get(path)
        assert anon.status_code == 401

        monkeypatch.setenv("TESTING_API_KEY", "curator-key")
        monkeypatch.setenv("TESTING_API_KEY_EMAIL", "curator@example.org")
        curator = await test_client.get(path, headers={"X-API-Key": "curator-key"})
        assert curator.status_code == 403

    @pytest.mark.asyncio
    async def test_all_connections_endpoint_redacts_credentials(
        self, test_client, mock_connections_loaded, admin_headers
    ):
        """GET /api/admin/health/connections allows admins and never exposes credentials."""
        # Mock the health check to avoid actual network calls
        with patch(
            "src.lib.config.connections_loader.check_all_health",
            new_callable=AsyncMock,
        ) as mock_check:
            # Return the mocked connection status (uses display_url internally)
            from src.lib.config.connections_loader import get_connection_status
            mock_check.return_value = get_connection_status()

            response = await test_client.get(
                "/api/admin/health/connections",
                headers=admin_headers,
            )

            # Should succeed
            assert response.status_code == 200

            # Get response as text to search for credentials
            response_text = response.text.lower()

            # Verify NO credential patterns appear in response
            for pattern in CREDENTIAL_PATTERNS:
                assert pattern.lower() not in response_text, \
                    f"Credential pattern '{pattern}' found in response!"

            # Verify the redaction marker IS present for URLs with credentials
            assert "***" in response.text, \
                "Redaction marker '***' should appear for URLs with credentials"

    @pytest.mark.asyncio
    async def test_single_connection_endpoint_redacts_credentials(
        self, test_client, mock_connections_loaded, admin_headers
    ):
        """GET /api/admin/health/connections/{service_id} allows admins and never exposes credentials."""
        with patch(
            "src.lib.config.connections_loader.check_all_health",
            new_callable=AsyncMock,
        ) as mock_check:
            from src.lib.config.connections_loader import get_connection_status
            mock_check.return_value = get_connection_status()

            response = await test_client.get(
                "/api/admin/health/connections/test_db",
                headers=admin_headers,
            )

            # Should succeed
            assert response.status_code == 200

            # Get response as text
            response_text = response.text.lower()

            # Verify NO credential patterns appear
            for pattern in CREDENTIAL_PATTERNS:
                assert pattern.lower() not in response_text, \
                    f"Credential pattern '{pattern}' found in response!"

            # Verify specific password from test data is NOT present
            assert "dbsecretpass" not in response_text, \
                "Actual password 'dbsecretpass' leaked in response!"

            # Verify redaction marker is present
            assert "***" in response.text

    @pytest.mark.asyncio
    async def test_response_structure_includes_required_fields(
        self, test_client, mock_connections_loaded, admin_headers
    ):
        """Verify admin response structure is correct."""
        with patch(
            "src.lib.config.connections_loader.check_all_health",
            new_callable=AsyncMock,
        ) as mock_check:
            from src.lib.config.connections_loader import get_connection_status
            mock_check.return_value = get_connection_status()

            response = await test_client.get(
                "/api/admin/health/connections",
                headers=admin_headers,
            )

            assert response.status_code == 200
            data = response.json()

            # Check top-level structure
            assert "observed_at" in data
            assert "status" in data
            assert "total_services" in data
            assert "healthy_count" in data
            assert "unhealthy_count" in data
            assert "services" in data

            # Check each service has required fields
            for service_id, service in data["services"].items():
                assert "service_id" in service
                assert "description" in service
                assert "url" in service
                assert "required" in service
                assert "is_healthy" in service
