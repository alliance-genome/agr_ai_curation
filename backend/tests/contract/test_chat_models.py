"""
Contract test for GET /chat/models endpoint
Tests the API contract for retrieving available AI models
"""

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


class TestChatModelsContract:
    """Contract tests for GET /chat/models endpoint"""

    def test_chat_models_get_request(self):
        """Test that GET /chat/models responds successfully"""
        response = client.get("/chat/models")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    def test_chat_models_response_schema(self):
        """Test that GET /chat/models returns the correct response schema"""
        response = client.get("/chat/models")
        assert response.status_code == 200

        data = response.json()

        # Should have both provider keys
        assert "openai" in data, "Response must contain 'openai' key"
        assert "gemini" in data, "Response must contain 'gemini' key"

        # Each provider should have a list of models
        assert isinstance(data["openai"], list), "OpenAI models must be a list"
        assert isinstance(data["gemini"], list), "Gemini models must be a list"

        # OpenAI models should include expected models
        expected_openai_models = ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"]
        for model in expected_openai_models:
            assert model in data["openai"], f"OpenAI models should include {model}"

        # Gemini models should include expected models
        expected_gemini_models = [
            "gemini-2.0-flash",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
        ]
        for model in expected_gemini_models:
            assert model in data["gemini"], f"Gemini models should include {model}"

    def test_chat_models_no_parameters(self):
        """Test that GET /chat/models doesn't require any parameters"""
        # Should work without any query parameters
        response = client.get("/chat/models")
        assert response.status_code == 200

        # Should ignore unexpected query parameters
        response = client.get("/chat/models?unexpected=param")
        assert response.status_code == 200

    def test_chat_models_response_content_type(self):
        """Test that GET /chat/models returns JSON content type"""
        response = client.get("/chat/models")
        assert response.status_code == 200
        assert "application/json" in response.headers.get("content-type", "")

    def test_chat_models_caching_headers(self):
        """Test that GET /chat/models includes appropriate caching headers"""
        response = client.get("/chat/models")
        assert response.status_code == 200

        # Models list should be cacheable
        headers = response.headers
        # Could have cache-control header for static model lists
        # This is optional but good practice
        if "cache-control" in headers:
            assert "max-age" in headers["cache-control"]
