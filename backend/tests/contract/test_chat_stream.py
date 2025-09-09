"""
Contract test for POST /chat/stream endpoint
Tests the API contract for streaming chat responses with Server-Sent Events
"""

import pytest
import json
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


class TestChatStreamContract:
    """Contract tests for POST /chat/stream endpoint"""

    def test_chat_stream_request_schema(self):
        """Test that POST /chat/stream accepts the correct request schema"""
        request_data = {
            "message": "Tell me about gene ontology",
            "session_id": "123e4567-e89b-12d3-a456-426614174000",
            "provider": "openai",
            "model": "gpt-4o",
        }

        response = client.post("/chat/stream", json=request_data)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    def test_chat_stream_minimal_request(self):
        """Test that POST /chat/stream works with minimal required fields"""
        request_data = {"message": "Hello"}

        response = client.post("/chat/stream", json=request_data)
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    @pytest.mark.xfail(reason="Requires valid API keys for streaming response")
    def test_chat_stream_response_format(self):
        """Test that POST /chat/stream returns Server-Sent Events format"""
        request_data = {
            "message": "Test streaming",
            "provider": "openai",
            "model": "gpt-4o",
        }

        with client.stream("POST", "/chat/stream", json=request_data) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

            # Read streaming chunks
            chunks = []
            for line in response.iter_lines():
                if line.startswith("data: "):
                    chunk_data = line[6:]  # Remove "data: " prefix
                    if chunk_data:
                        try:
                            chunk = json.loads(chunk_data)
                            chunks.append(chunk)
                        except json.JSONDecodeError:
                            pass

            # Verify chunk structure
            assert len(chunks) > 0, "Should receive at least one chunk"

            for chunk in chunks:
                assert "delta" in chunk, "Each chunk must have 'delta' field"
                assert "session_id" in chunk, "Each chunk must have 'session_id' field"
                assert "provider" in chunk, "Each chunk must have 'provider' field"
                assert "model" in chunk, "Each chunk must have 'model' field"
                assert (
                    "is_complete" in chunk
                ), "Each chunk must have 'is_complete' field"

            # Last chunk should be marked as complete
            assert (
                chunks[-1]["is_complete"] is True
            ), "Last chunk should be marked complete"

    def test_chat_stream_validation_error(self):
        """Test that POST /chat/stream returns 422 for invalid requests"""
        # Missing required message field
        request_data = {"provider": "openai"}

        response = client.post("/chat/stream", json=request_data)
        assert (
            response.status_code == 422
        ), f"Expected 422 for missing required field, got {response.status_code}"

    def test_chat_stream_provider_validation(self):
        """Test that provider field validates against enum"""
        request_data = {"message": "Test", "provider": "invalid_provider"}

        response = client.post("/chat/stream", json=request_data)
        assert (
            response.status_code == 422
        ), f"Expected 422 for invalid provider, got {response.status_code}"

    def test_chat_stream_message_length_validation(self):
        """Test that message field respects max length constraint"""
        long_message = "x" * 10001
        request_data = {"message": long_message}

        response = client.post("/chat/stream", json=request_data)
        assert (
            response.status_code == 422
        ), f"Expected 422 for message too long, got {response.status_code}"
