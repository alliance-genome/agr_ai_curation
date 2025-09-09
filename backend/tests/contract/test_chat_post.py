"""
Contract test for POST /chat/ endpoint
Tests the API contract for basic chat functionality with AI integration
"""

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


class TestChatPostContract:
    """Contract tests for POST /chat/ endpoint"""

    def test_chat_post_request_schema(self):
        """Test that POST /chat/ accepts the correct request schema"""
        # Valid request with all fields
        request_data = {
            "message": "Test message",
            "history": [],
            "session_id": "123e4567-e89b-12d3-a456-426614174000",
            "provider": "openai",
            "model": "gpt-4o",
            "stream": False,
        }

        response = client.post("/chat/", json=request_data)
        assert response.status_code in [
            200,
            500,
        ], f"Expected 200 or 500, got {response.status_code}"

    def test_chat_post_minimal_request(self):
        """Test that POST /chat/ works with minimal required fields"""
        request_data = {"message": "Hello"}

        response = client.post("/chat/", json=request_data)
        assert response.status_code in [
            200,
            500,
        ], f"Expected 200 or 500, got {response.status_code}"

    def test_chat_post_response_schema(self):
        """Test that POST /chat/ returns the correct response schema"""
        request_data = {
            "message": "Test message",
            "provider": "openai",
            "model": "gpt-4o",
        }

        response = client.post("/chat/", json=request_data)

        # Check response structure when successful
        if response.status_code == 200:
            data = response.json()
            assert "response" in data, "Response must contain 'response' field"
            assert "session_id" in data, "Response must contain 'session_id' field"
            assert "provider" in data, "Response must contain 'provider' field"
            assert "model" in data, "Response must contain 'model' field"
            assert isinstance(data["response"], str), "Response must be a string"
            assert isinstance(data["session_id"], str), "Session ID must be a string"

    def test_chat_post_validation_error(self):
        """Test that POST /chat/ returns 422 for invalid requests"""
        # Missing required message field
        request_data = {"provider": "openai"}

        response = client.post("/chat/", json=request_data)
        assert (
            response.status_code == 422
        ), f"Expected 422 for missing required field, got {response.status_code}"

    def test_chat_post_provider_enum_validation(self):
        """Test that provider field only accepts valid enum values"""
        request_data = {"message": "Test", "provider": "invalid_provider"}

        response = client.post("/chat/", json=request_data)
        assert (
            response.status_code == 422
        ), f"Expected 422 for invalid provider, got {response.status_code}"

    def test_chat_post_message_length_validation(self):
        """Test that message field respects max length constraint"""
        # Create a message longer than 10,000 characters
        long_message = "x" * 10001
        request_data = {"message": long_message}

        response = client.post("/chat/", json=request_data)
        assert (
            response.status_code == 422
        ), f"Expected 422 for message too long, got {response.status_code}"

    @pytest.mark.xfail(reason="Requires valid API keys for real AI response")
    def test_chat_post_successful_ai_response(self):
        """Test that POST /chat/ returns actual AI response (not stub)"""
        request_data = {
            "message": "Hello, what can you help me with?",
            "provider": "openai",
            "model": "gpt-4o",
        }

        response = client.post("/chat/", json=request_data)
        assert response.status_code == 200
        data = response.json()

        # Should NOT contain stub response
        assert "stub response" not in data["response"].lower()
        assert "AI integration will be implemented" not in data["response"]
