"""
Integration test for basic AI chat responses with OpenAI
Tests end-to-end AI integration with real API calls (when configured)
"""

import pytest
import os
from fastapi.testclient import TestClient
from app.main import app
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base, get_db
from app.models import ChatHistory

client = TestClient(app)

# Test database setup
SQLALCHEMY_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "sqlite:///./test.db")
engine = create_engine(SQLALCHEMY_DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


class TestAIChatBasic:
    """Integration tests for basic AI chat functionality"""

    @classmethod
    def setup_class(cls):
        """Create test database tables"""
        Base.metadata.create_all(bind=engine)

    @classmethod
    def teardown_class(cls):
        """Drop test database tables"""
        Base.metadata.drop_all(bind=engine)

    @pytest.mark.xfail(reason="AI integration not yet implemented")
    @pytest.mark.skipif(
        not os.getenv("OPENAI_API_KEY"), reason="OpenAI API key not configured"
    )
    def test_basic_openai_response(self):
        """Test that OpenAI returns a real AI response"""
        request_data = {
            "message": "Hello, what can you help me with?",
            "provider": "openai",
            "model": "gpt-4o",
        }

        response = client.post("/chat/", json=request_data)
        assert response.status_code == 200

        data = response.json()
        assert "response" in data
        assert "session_id" in data
        assert data["provider"] == "openai"
        assert data["model"] == "gpt-4o"

        # Response should not be the stub
        assert "stub response" not in data["response"].lower()
        assert "AI integration will be implemented" not in data["response"]

        # Response should be meaningful
        assert len(data["response"]) > 10

    @pytest.mark.xfail(reason="AI integration not yet implemented")
    @pytest.mark.skipif(
        not os.getenv("OPENAI_API_KEY"), reason="OpenAI API key not configured"
    )
    def test_openai_response_with_context(self):
        """Test that OpenAI can use conversation history"""
        session_id = "test-session-123"

        # First message
        request1 = {
            "message": "My name is Alice",
            "provider": "openai",
            "model": "gpt-4o",
            "session_id": session_id,
        }

        response1 = client.post("/chat/", json=request1)
        assert response1.status_code == 200

        # Second message with context
        request2 = {
            "message": "What is my name?",
            "history": [
                {"role": "user", "content": "My name is Alice"},
                {"role": "assistant", "content": response1.json()["response"]},
            ],
            "provider": "openai",
            "model": "gpt-4o",
            "session_id": session_id,
        }

        response2 = client.post("/chat/", json=request2)
        assert response2.status_code == 200

        data = response2.json()
        # AI should remember the name from context
        assert "alice" in data["response"].lower()

    @pytest.mark.xfail(reason="AI integration not yet implemented")
    def test_ai_response_saved_to_database(self):
        """Test that AI responses are saved to the database"""
        session_id = "db-test-session"

        request_data = {
            "message": "Test message for database",
            "provider": "openai",
            "model": "gpt-4o",
            "session_id": session_id,
        }

        response = client.post("/chat/", json=request_data)
        assert response.status_code == 200

        # Check database for saved messages
        db = TestingSessionLocal()
        messages = db.query(ChatHistory).filter_by(session_id=session_id).all()
        db.close()

        assert len(messages) >= 2  # User message and AI response

        # Check user message was saved
        user_msg = next((m for m in messages if m.role == "user"), None)
        assert user_msg is not None
        assert user_msg.content == "Test message for database"
        assert user_msg.model_provider is None  # User messages don't have model info

        # Check AI response was saved
        ai_msg = next((m for m in messages if m.role == "assistant"), None)
        assert ai_msg is not None
        assert ai_msg.model_provider == "openai"
        assert ai_msg.model_name == "gpt-4o"

    @pytest.mark.xfail(reason="AI integration not yet implemented")
    def test_ai_error_handling(self):
        """Test graceful handling of AI service errors"""
        # Test with invalid API key scenario
        request_data = {
            "message": "Test error handling",
            "provider": "openai",
            "model": "gpt-4o",
        }

        # Temporarily override with invalid key to test error handling
        import os

        original_key = os.environ.get("OPENAI_API_KEY", "")
        os.environ["OPENAI_API_KEY"] = "invalid-key"

        try:
            response = client.post("/chat/", json=request_data)
            # Should return 500 with error details
            assert response.status_code == 500

            data = response.json()
            assert "detail" in data

        finally:
            # Restore original key
            os.environ["OPENAI_API_KEY"] = original_key
