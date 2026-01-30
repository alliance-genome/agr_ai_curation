"""Integration tests for /api/admin/health/connections endpoints.

Tests that the health API properly redacts credentials and returns
expected response structure.

Note: These endpoints are public (no auth required) for monitoring purposes.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock

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
            url="testdb://dbuser:dbsecretpass@localhost:5432/testdb",
            health_check=HealthCheck(method="CONNECT"),
            required=True,
            is_healthy=True,
            last_error=None,
        ),
        "test_cache": ConnectionDefinition(
            service_id="test_cache",
            description="Test cache",
            url="testcache://cacheuser:cachesecretpw@localhost:6379",
            health_check=HealthCheck(method="PING"),
            required=False,
            is_healthy=True,
            last_error=None,
        ),
        "test_api": ConnectionDefinition(
            service_id="test_api",
            description="Test API (no credentials)",
            url="http://localhost:8080/api",
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

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as client:
        yield client


class TestConnectionsHealthEndpointSecurity:
    """Test that health endpoints never expose credentials."""

    @pytest.mark.asyncio
    async def test_all_connections_endpoint_redacts_credentials(
        self, test_client, mock_connections_loaded
    ):
        """GET /api/admin/health/connections should never expose credentials."""
        # Mock the health check to avoid actual network calls
        with patch(
            "src.lib.config.connections_loader.check_all_health",
            new_callable=AsyncMock,
        ) as mock_check:
            # Return the mocked connection status (uses display_url internally)
            from src.lib.config.connections_loader import get_connection_status
            mock_check.return_value = get_connection_status()

            response = await test_client.get("/api/admin/health/connections")

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
        self, test_client, mock_connections_loaded
    ):
        """GET /api/admin/health/connections/{service_id} should never expose credentials."""
        with patch(
            "src.lib.config.connections_loader.check_service_health",
            new_callable=AsyncMock,
        ) as mock_check:
            mock_check.return_value = True

            response = await test_client.get("/api/admin/health/connections/test_db")

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
        self, test_client, mock_connections_loaded
    ):
        """Verify response structure is correct."""
        with patch(
            "src.lib.config.connections_loader.check_all_health",
            new_callable=AsyncMock,
        ) as mock_check:
            from src.lib.config.connections_loader import get_connection_status
            mock_check.return_value = get_connection_status()

            response = await test_client.get("/api/admin/health/connections")

            assert response.status_code == 200
            data = response.json()

            # Check top-level structure
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
