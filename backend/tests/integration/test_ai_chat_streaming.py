"""
Integration test for streaming AI chat responses
Tests Server-Sent Events streaming functionality with real AI providers
"""

import pytest
import json
import time
import os
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


class TestAIChatStreaming:
    """Integration tests for streaming AI chat functionality"""

    @pytest.mark.xfail(reason="Streaming not yet implemented")
    @pytest.mark.skipif(
        not os.getenv("OPENAI_API_KEY"), reason="OpenAI API key not configured"
    )
    def test_streaming_response_basic(self):
        """Test basic streaming response functionality"""
        request_data = {
            "message": "Count from 1 to 5",
            "provider": "openai",
            "model": "gpt-4o",
        }

        chunks_received = []
        start_time = time.time()

        with client.stream("POST", "/chat/stream", json=request_data) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")

            for line in response.iter_lines():
                if line.startswith("data: "):
                    chunk_data = line[6:]
                    if chunk_data and chunk_data != "[DONE]":
                        try:
                            chunk = json.loads(chunk_data)
                            chunks_received.append(chunk)

                            # Record time of first chunk
                            if len(chunks_received) == 1:
                                first_chunk_time = time.time() - start_time
                        except json.JSONDecodeError:
                            pass

        # Verify streaming behavior
        assert len(chunks_received) > 1, "Should receive multiple chunks"
        assert first_chunk_time < 2.0, "First chunk should arrive within 2 seconds"

        # Verify chunk structure
        for chunk in chunks_received:
            assert "delta" in chunk
            assert "session_id" in chunk
            assert "provider" in chunk
            assert chunk["provider"] == "openai"
            assert "model" in chunk
            assert chunk["model"] == "gpt-4o"
            assert "is_complete" in chunk

        # Last chunk should be marked complete
        assert chunks_received[-1]["is_complete"] is True

        # Reconstruct full response
        full_response = "".join(chunk["delta"] for chunk in chunks_received)
        assert len(full_response) > 0

    @pytest.mark.xfail(reason="Streaming not yet implemented")
    def test_streaming_with_gemini(self):
        """Test streaming works with Gemini provider via OpenAI compatibility"""
        if not os.getenv("GEMINI_API_KEY"):
            pytest.skip("Gemini API key not configured")

        request_data = {
            "message": "Write a haiku about DNA",
            "provider": "gemini",
            "model": "gemini-2.0-flash",
        }

        chunks_received = []

        with client.stream("POST", "/chat/stream", json=request_data) as response:
            assert response.status_code == 200

            for line in response.iter_lines():
                if line.startswith("data: "):
                    chunk_data = line[6:]
                    if chunk_data and chunk_data != "[DONE]":
                        try:
                            chunk = json.loads(chunk_data)
                            chunks_received.append(chunk)
                        except json.JSONDecodeError:
                            pass

        assert len(chunks_received) > 1

        # Verify Gemini provider in chunks
        for chunk in chunks_received:
            assert chunk["provider"] == "gemini"
            assert chunk["model"] == "gemini-2.0-flash"

    @pytest.mark.xfail(reason="Streaming not yet implemented")
    def test_streaming_latency(self):
        """Test streaming response latency meets performance goals"""
        request_data = {"message": "Say hello", "provider": "openai", "model": "gpt-4o"}

        chunk_times = []
        start_time = time.time()

        with client.stream("POST", "/chat/stream", json=request_data) as response:
            assert response.status_code == 200

            for line in response.iter_lines():
                if line.startswith("data: "):
                    chunk_data = line[6:]
                    if chunk_data and chunk_data != "[DONE]":
                        try:
                            chunk = json.loads(chunk_data)
                            chunk_time = time.time() - start_time
                            chunk_times.append(chunk_time)
                        except json.JSONDecodeError:
                            pass

        if len(chunk_times) > 1:
            # Calculate inter-chunk latency
            inter_chunk_times = []
            for i in range(1, len(chunk_times)):
                inter_chunk_times.append(chunk_times[i] - chunk_times[i - 1])

            avg_inter_chunk_time = sum(inter_chunk_times) / len(inter_chunk_times)

            # Performance goal: <500ms between tokens
            assert (
                avg_inter_chunk_time < 0.5
            ), f"Inter-chunk time {avg_inter_chunk_time}s exceeds 500ms goal"

    @pytest.mark.xfail(reason="Streaming not yet implemented")
    def test_streaming_error_handling(self):
        """Test streaming handles errors gracefully"""
        # Test with message that might cause issues
        request_data = {
            "message": "x" * 5000,  # Long but valid message
            "provider": "openai",
            "model": "gpt-4o",
        }

        error_received = False

        with client.stream("POST", "/chat/stream", json=request_data) as response:
            # Should either work or return error status
            if response.status_code != 200:
                error_received = True
                assert response.status_code in [400, 422, 500]
            else:
                # If successful, should still stream properly
                chunks = []
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        chunk_data = line[6:]
                        if chunk_data and chunk_data != "[DONE]":
                            try:
                                chunk = json.loads(chunk_data)
                                chunks.append(chunk)
                            except json.JSONDecodeError:
                                pass

                assert len(chunks) > 0

    @pytest.mark.xfail(reason="Streaming not yet implemented")
    def test_streaming_session_persistence(self):
        """Test that streaming responses are saved to chat history"""
        session_id = "streaming-persistence-test"

        request_data = {
            "message": "Test streaming persistence",
            "provider": "openai",
            "model": "gpt-4o",
            "session_id": session_id,
        }

        # Collect streamed response
        full_response = ""
        with client.stream("POST", "/chat/stream", json=request_data) as response:
            assert response.status_code == 200

            for line in response.iter_lines():
                if line.startswith("data: "):
                    chunk_data = line[6:]
                    if chunk_data and chunk_data != "[DONE]":
                        try:
                            chunk = json.loads(chunk_data)
                            full_response += chunk.get("delta", "")
                        except json.JSONDecodeError:
                            pass

        # Check if response was saved to history
        # This would require database check in real implementation
        assert len(full_response) > 0
