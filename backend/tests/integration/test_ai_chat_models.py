"""
Integration test for AI model selection and Gemini provider
Tests switching between different AI providers and models
"""

import pytest
import os
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


class TestAIChatModels:
    """Integration tests for AI model selection functionality"""

    @pytest.mark.xfail(reason="Requires valid API keys")
    @pytest.mark.skipif(
        not os.getenv("GEMINI_API_KEY"), reason="Gemini API key not configured"
    )
    def test_gemini_provider_response(self):
        """Test that Gemini provider works with OpenAI compatibility"""
        request_data = {
            "message": "Explain biological curation in one sentence",
            "provider": "gemini",
            "model": "gemini-2.0-flash",
        }

        response = client.post("/chat/", json=request_data)
        assert response.status_code == 200

        data = response.json()
        assert data["provider"] == "gemini"
        assert data["model"] == "gemini-2.0-flash"

        # Response should be real, not stub
        assert "stub response" not in data["response"].lower()
        assert len(data["response"]) > 10

    @pytest.mark.xfail(reason="Requires valid API keys")
    def test_switch_between_providers(self):
        """Test switching between OpenAI and Gemini providers"""
        session_id = "provider-switch-test"

        # First request with OpenAI
        openai_request = {
            "message": "Hello from OpenAI test",
            "provider": "openai",
            "model": "gpt-4o",
            "session_id": session_id,
        }

        openai_response = client.post("/chat/", json=openai_request)

        if os.getenv("OPENAI_API_KEY"):
            assert openai_response.status_code == 200
            openai_data = openai_response.json()
            assert openai_data["provider"] == "openai"

        # Second request with Gemini in same session
        gemini_request = {
            "message": "Hello from Gemini test",
            "provider": "gemini",
            "model": "gemini-1.5-flash",
            "session_id": session_id,
        }

        gemini_response = client.post("/chat/", json=gemini_request)

        if os.getenv("GEMINI_API_KEY"):
            assert gemini_response.status_code == 200
            gemini_data = gemini_response.json()
            assert gemini_data["provider"] == "gemini"
            assert gemini_data["model"] == "gemini-1.5-flash"

    @pytest.mark.xfail(reason="Requires valid API keys")
    def test_model_selection_within_provider(self):
        """Test switching between models within the same provider"""
        # Test different OpenAI models
        models_to_test = ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"]

        for model in models_to_test:
            request_data = {
                "message": f"Testing {model}",
                "provider": "openai",
                "model": model,
            }

            response = client.post("/chat/", json=request_data)

            if os.getenv("OPENAI_API_KEY"):
                assert response.status_code == 200
                data = response.json()
                assert data["model"] == model

    def test_get_available_models(self):
        """Test retrieving list of available models"""
        response = client.get("/chat/models")
        assert response.status_code == 200

        data = response.json()

        # Check OpenAI models
        assert "openai" in data
        assert "gpt-4o" in data["openai"]
        assert "gpt-4o-mini" in data["openai"]
        assert "gpt-3.5-turbo" in data["openai"]

        # Check Gemini models
        assert "gemini" in data
        assert "gemini-2.0-flash" in data["gemini"]
        assert "gemini-1.5-pro" in data["gemini"]
        assert "gemini-1.5-flash" in data["gemini"]

    @pytest.mark.xfail(reason="Requires valid API keys")
    def test_default_model_selection(self):
        """Test that default model is used when not specified"""
        request_data = {
            "message": "Test default model"
            # No provider or model specified
        }

        response = client.post("/chat/", json=request_data)
        assert response.status_code == 200

        data = response.json()
        # Should use defaults from environment or code
        assert data["provider"] == "openai"  # Default provider
        assert data["model"] == "gpt-4o"  # Default model

    @pytest.mark.xfail(reason="Requires valid API keys")
    def test_invalid_model_for_provider(self):
        """Test error handling for invalid model-provider combinations"""
        # Try to use OpenAI model with Gemini provider
        request_data = {
            "message": "Test invalid combination",
            "provider": "gemini",
            "model": "gpt-4o",  # OpenAI model
        }

        response = client.post("/chat/", json=request_data)
        # Should either auto-correct or return error
        assert response.status_code in [200, 400, 422]

        if response.status_code == 200:
            # If auto-corrected, should use valid Gemini model
            data = response.json()
            assert data["model"] in [
                "gemini-2.0-flash",
                "gemini-1.5-pro",
                "gemini-1.5-flash",
            ]
