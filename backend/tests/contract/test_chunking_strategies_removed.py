"""The unused chunking-strategies route is gone (ALL-1610)."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from main import app
    from src.api.auth import auth

    app.dependency_overrides[auth.get_user] = lambda: {
        "sub": "contract-user",
        "uid": "contract-user",
        "email": "contract@test.local",
        "name": "Contract User",
        "groups": ["developers"],
        "cognito:groups": ["developers"],
    }
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(auth.get_user, None)


def test_chunking_strategies_route_is_not_served(client):
    response = client.get("/weaviate/chunking-strategies")

    assert response.status_code == 404
