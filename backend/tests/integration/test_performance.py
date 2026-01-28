"""Integration test for performance validation.

Task: T056 - Integration test for performance validation
Scenario: quickstart.md:390-412
Requirements: FR-004 (session state performance goal)

Tests that:
1. Authentication flow completes quickly (< 3s goal)
2. Token validation adds minimal overhead (< 200ms goal)
3. Protected endpoints remain performant with auth
4. Multiple sequential requests maintain performance

CRITICAL: This test validates that authentication does not significantly
          impact application performance.

Implementation Notes:
- Measures actual request timing using time.perf_counter()
- Compares protected vs public endpoint performance
- Tests both single requests and sequential request patterns
- Uses statistical measures (mean, percentiles) for reliability
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import time
from statistics import mean, median
from datetime import datetime, timezone

from src.models.sql.user import User

# Note: test_db, cleanup_db, and mock_weaviate fixtures are now in conftest.py
# Note: performance_user is pre-registered as "perf1" in conftest.py

from conftest import MOCK_USERS


@pytest.fixture
def performance_user():
    """Get the perf1 user from conftest registry."""
    return MOCK_USERS["perf1"]


@pytest.fixture
def authenticated_client(performance_user, test_db, mock_weaviate, get_auth_mock):
    """Create test client with valid authentication for performance testing."""
    # Configure shared auth mock for perf1 user
    get_auth_mock.set_user("perf1")

    # Create user in database
    user = User(
        user_id=performance_user.uid,
        email=performance_user.sub,
        display_name=performance_user.sub,
        created_at=datetime.now(timezone.utc),
        last_login=datetime.now(timezone.utc),
        is_active=True
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)

    from main import app
    from src.models.sql.database import get_db

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db

    yield TestClient(app)

    app.dependency_overrides.clear()


@pytest.fixture
def unauthenticated_client(get_auth_mock):
    """Create test client without authentication for baseline comparison."""
    # Configure auth to fail (unauthenticated)
    get_auth_mock.set_failure("Not authenticated")

    from main import app

    yield TestClient(app)


def measure_request_time(client, method, endpoint, **kwargs):
    """Measure the time taken for a single HTTP request.

    Returns:
        tuple: (status_code, duration_ms)
    """
    start = time.perf_counter()

    if method.upper() == "GET":
        response = client.get(endpoint, **kwargs)
    elif method.upper() == "POST":
        response = client.post(endpoint, **kwargs)
    else:
        raise ValueError(f"Unsupported method: {method}")

    end = time.perf_counter()
    duration_ms = (end - start) * 1000

    return response.status_code, duration_ms


class TestPerformance:
    """Integration tests for performance validation."""

    def test_authentication_flow_timing(self, authenticated_client):
        """Test that authentication flow completes quickly.

        Validates FR-004: Authentication flow < 3 seconds.

        Note: This test measures the time to make an authenticated request,
        which includes token validation overhead. In production, the full
        authentication flow (including Cognito redirect) should be < 3s.
        """
        # Measure time to access user profile (first authenticated request)
        start = time.perf_counter()
        response = authenticated_client.get("/users/me")
        end = time.perf_counter()

        duration_ms = (end - start) * 1000

        assert response.status_code == 200, \
            f"Authentication should succeed, got {response.status_code}"

        # Should be well under 3 seconds (3000ms)
        # In test environment with mocked auth, expect < 500ms
        assert duration_ms < 500, \
            f"Authenticated request should be fast, took {duration_ms:.2f}ms"

        print(f"✓ Authentication flow timing: {duration_ms:.2f}ms")

    def test_token_validation_overhead(self, authenticated_client, unauthenticated_client):
        """Test that token validation adds minimal overhead.

        Validates: Token validation overhead < 200ms.

        Compares:
        - Protected endpoint with authentication (/users/me)
        - Public endpoint without authentication (/weaviate/health)
        """
        # Measure public endpoint (no auth)
        public_times = []
        for _ in range(10):
            _, duration = measure_request_time(
                unauthenticated_client, "GET", "/weaviate/health"
            )
            public_times.append(duration)

        # Measure protected endpoint (with auth)
        protected_times = []
        for _ in range(10):
            status, duration = measure_request_time(
                authenticated_client, "GET", "/users/me"
            )
            assert status == 200
            protected_times.append(duration)

        # Calculate overhead
        public_median = median(public_times)
        protected_median = median(protected_times)
        overhead_ms = protected_median - public_median

        assert overhead_ms < 200, \
            f"Token validation overhead should be < 200ms, got {overhead_ms:.2f}ms " \
            f"(public: {public_median:.2f}ms, protected: {protected_median:.2f}ms)"

        print(f"✓ Token validation overhead: {overhead_ms:.2f}ms")
        print(f"  Public endpoint median: {public_median:.2f}ms")
        print(f"  Protected endpoint median: {protected_median:.2f}ms")

    def test_sequential_authenticated_requests(self, authenticated_client):
        """Test that multiple sequential authenticated requests remain performant.

        Validates: Consistent performance across multiple requests.
        """
        # Make 20 sequential requests
        durations = []
        for _ in range(20):
            status, duration = measure_request_time(
                authenticated_client, "GET", "/users/me"
            )
            assert status == 200
            durations.append(duration)

        # Calculate statistics
        avg_duration = mean(durations)
        max_duration = max(durations)
        min_duration = min(durations)

        # All requests should be reasonably fast
        assert avg_duration < 500, \
            f"Average request time should be < 500ms, got {avg_duration:.2f}ms"

        assert max_duration < 1000, \
            f"Maximum request time should be < 1s, got {max_duration:.2f}ms"

        print(f"✓ Sequential requests (n=20):")
        print(f"  Average: {avg_duration:.2f}ms")
        print(f"  Min: {min_duration:.2f}ms")
        print(f"  Max: {max_duration:.2f}ms")

    def test_document_list_performance_with_auth(self, authenticated_client):
        """Test that document listing remains fast with authentication.

        Validates: Protected endpoints remain performant.
        """
        # Measure document list endpoint (common operation)
        durations = []
        for _ in range(10):
            status, duration = measure_request_time(
                authenticated_client, "GET", "/weaviate/documents"
            )
            assert status == 200
            durations.append(duration)

        avg_duration = mean(durations)

        # Should be fast even with auth and database query
        assert avg_duration < 500, \
            f"Document list should be < 500ms, got {avg_duration:.2f}ms"

        print(f"✓ Document list performance: {avg_duration:.2f}ms average")

    def test_health_endpoint_unaffected_by_auth(self, unauthenticated_client):
        """Test that public health endpoint remains fast.

        Validates: Public endpoints maintain baseline performance.
        """
        # Measure health endpoint (public, no auth)
        durations = []
        for _ in range(10):
            _, duration = measure_request_time(
                unauthenticated_client, "GET", "/weaviate/health"
            )
            durations.append(duration)

        avg_duration = mean(durations)

        # Should be very fast (no auth, minimal processing)
        assert avg_duration < 200, \
            f"Health endpoint should be < 200ms, got {avg_duration:.2f}ms"

        print(f"✓ Health endpoint performance: {avg_duration:.2f}ms average")

    def test_performance_degradation_check(self, authenticated_client):
        """Test that performance doesn't degrade over multiple requests.

        Validates: No performance degradation in authentication layer.
        """
        # Make requests in batches
        batch_1_times = []
        batch_2_times = []
        batch_3_times = []

        # Batch 1: First 10 requests
        for _ in range(10):
            status, duration = measure_request_time(
                authenticated_client, "GET", "/users/me"
            )
            assert status == 200
            batch_1_times.append(duration)

        # Batch 2: Next 10 requests
        for _ in range(10):
            status, duration = measure_request_time(
                authenticated_client, "GET", "/users/me"
            )
            assert status == 200
            batch_2_times.append(duration)

        # Batch 3: Final 10 requests
        for _ in range(10):
            status, duration = measure_request_time(
                authenticated_client, "GET", "/users/me"
            )
            assert status == 200
            batch_3_times.append(duration)

        # Calculate batch averages
        batch_1_avg = mean(batch_1_times)
        batch_2_avg = mean(batch_2_times)
        batch_3_avg = mean(batch_3_times)

        # Performance should not degrade significantly
        # Allow up to 20% variance due to system load
        max_degradation = batch_1_avg * 1.2

        assert batch_2_avg <= max_degradation, \
            f"Batch 2 degraded: {batch_2_avg:.2f}ms > {max_degradation:.2f}ms"

        assert batch_3_avg <= max_degradation, \
            f"Batch 3 degraded: {batch_3_avg:.2f}ms > {max_degradation:.2f}ms"

        print(f"✓ No performance degradation detected:")
        print(f"  Batch 1 (1-10): {batch_1_avg:.2f}ms")
        print(f"  Batch 2 (11-20): {batch_2_avg:.2f}ms")
        print(f"  Batch 3 (21-30): {batch_3_avg:.2f}ms")

    def test_concurrent_user_performance(
        self, monkeypatch, test_db, mock_weaviate
    ):
        """Test performance with multiple concurrent users.

        Validates: Authentication scales with multiple users.

        Note: TestClient is synchronous, so this simulates sequential
        requests from different users, not true concurrency.
        """
        # Create two users using MockCognitoUser from conftest
        from conftest import MockCognitoUser

        user1 = MockCognitoUser(
            uid="test_perf_user1_00u1abc",
            sub="perf_user1@alliancegenome.org",
            groups=[]
        )

        user2 = MockCognitoUser(
            uid="test_perf_user2_00u2def",
            sub="perf_user2@alliancegenome.org",
            groups=[]
        )

        # Add users to database
        for cognito_user in [user1, user2]:
            user = User(
                user_id=cognito_user.uid,
                email=cognito_user.sub,
                display_name=cognito_user.sub,
                created_at=datetime.now(timezone.utc),
                last_login=datetime.now(timezone.utc),
                is_active=True
            )
            test_db.add(user)
        test_db.commit()

        # Create clients for each user
        def create_client_for_user(cognito_user):
            import sys
            import os
            from fastapi import Depends

            sys.path.insert(
                0,
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            )

            def get_mock_user():
                return cognito_user

            with patch("src.api.auth.get_auth_dependency") as mock_get_auth_dep:
                mock_get_auth_dep.return_value = Depends(get_mock_user)

                from main import app
                from src.models.sql.database import get_db

                def override_get_db():
                    yield test_db

                app.dependency_overrides[get_db] = override_get_db

                return TestClient(app)

        client1 = create_client_for_user(user1)
        client2 = create_client_for_user(user2)

        # Alternate requests between users
        user1_times = []
        user2_times = []

        for i in range(10):
            # User 1 request
            status1, duration1 = measure_request_time(client1, "GET", "/users/me")
            assert status1 == 200
            user1_times.append(duration1)

            # User 2 request
            status2, duration2 = measure_request_time(client2, "GET", "/users/me")
            assert status2 == 200
            user2_times.append(duration2)

        # Both users should have similar performance
        user1_avg = mean(user1_times)
        user2_avg = mean(user2_times)

        assert user1_avg < 500, \
            f"User 1 requests should be < 500ms, got {user1_avg:.2f}ms"

        assert user2_avg < 500, \
            f"User 2 requests should be < 500ms, got {user2_avg:.2f}ms"

        print(f"✓ Multi-user performance:")
        print(f"  User 1 average: {user1_avg:.2f}ms")
        print(f"  User 2 average: {user2_avg:.2f}ms")
