"""
Integration test for chat conversation persistence
Tests that conversations are properly saved and retrievable from the database
"""

import pytest
import uuid
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.main import app
from app.database import Base, get_db
from app.models import ChatHistory

client = TestClient(app)

# Test database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_persistence.db"  # gitleaks:allow
engine = create_engine(SQLALCHEMY_DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


class TestChatPersistence:
    """Integration tests for conversation persistence"""

    @classmethod
    def setup_class(cls):
        """Create test database tables"""
        Base.metadata.create_all(bind=engine)

    @classmethod
    def teardown_class(cls):
        """Drop test database tables"""
        Base.metadata.drop_all(bind=engine)

    def setup_method(self):
        """Clear database before each test"""
        db = TestingSessionLocal()
        db.query(ChatHistory).delete()
        db.commit()
        db.close()

    @pytest.mark.xfail(reason="AI integration not yet implemented")
    def test_conversation_saved_to_database(self):
        """Test that both user and AI messages are saved"""
        session_id = str(uuid.uuid4())

        request_data = {
            "message": "Hello, this is a test message",
            "provider": "openai",
            "model": "gpt-4o",
            "session_id": session_id,
        }

        response = client.post("/chat/", json=request_data)
        assert response.status_code == 200

        # Check database
        db = TestingSessionLocal()
        messages = (
            db.query(ChatHistory)
            .filter_by(session_id=session_id)
            .order_by(ChatHistory.created_at)
            .all()
        )
        db.close()

        assert len(messages) == 2  # User message and AI response

        # Check user message
        user_msg = messages[0]
        assert user_msg.role == "user"
        assert user_msg.content == "Hello, this is a test message"
        assert user_msg.session_id == session_id
        assert user_msg.model_provider is None
        assert user_msg.model_name is None

        # Check AI response
        ai_msg = messages[1]
        assert ai_msg.role == "assistant"
        assert ai_msg.session_id == session_id
        assert ai_msg.model_provider == "openai"
        assert ai_msg.model_name == "gpt-4o"
        assert len(ai_msg.content) > 0
        assert "stub response" not in ai_msg.content.lower()

    @pytest.mark.xfail(reason="AI integration not yet implemented")
    def test_conversation_history_retrieval(self):
        """Test that conversation history can be retrieved"""
        session_id = str(uuid.uuid4())

        # Send multiple messages
        messages = ["First message", "Second message", "Third message"]

        for msg in messages:
            request_data = {
                "message": msg,
                "provider": "openai",
                "model": "gpt-4o",
                "session_id": session_id,
            }

            response = client.post("/chat/", json=request_data)
            assert response.status_code == 200

        # Retrieve conversation history
        db = TestingSessionLocal()
        history = (
            db.query(ChatHistory)
            .filter_by(session_id=session_id)
            .order_by(ChatHistory.created_at)
            .all()
        )
        db.close()

        # Should have 6 messages (3 user + 3 AI)
        assert len(history) == 6

        # Verify message order
        for i in range(0, 6, 2):
            assert history[i].role == "user"
            assert history[i + 1].role == "assistant"

        # Verify content
        assert history[0].content == "First message"
        assert history[2].content == "Second message"
        assert history[4].content == "Third message"

    @pytest.mark.xfail(reason="AI integration not yet implemented")
    def test_session_id_generation(self):
        """Test that session ID is auto-generated if not provided"""
        request_data = {
            "message": "Test without session ID",
            "provider": "openai",
            "model": "gpt-4o",
            # No session_id provided
        }

        response = client.post("/chat/", json=request_data)
        assert response.status_code == 200

        data = response.json()
        assert "session_id" in data
        assert data["session_id"] is not None
        assert len(data["session_id"]) > 0

        # Verify it's a valid UUID
        try:
            uuid.UUID(data["session_id"])
        except ValueError:
            pytest.fail("Generated session_id is not a valid UUID")

        # Check database
        db = TestingSessionLocal()
        messages = db.query(ChatHistory).filter_by(session_id=data["session_id"]).all()
        db.close()

        assert len(messages) >= 1

    @pytest.mark.xfail(reason="AI integration not yet implemented")
    def test_multiple_sessions_isolation(self):
        """Test that different sessions are properly isolated"""
        session1 = str(uuid.uuid4())
        session2 = str(uuid.uuid4())

        # Send message in session 1
        request1 = {
            "message": "Message for session 1",
            "provider": "openai",
            "model": "gpt-4o",
            "session_id": session1,
        }

        response1 = client.post("/chat/", json=request1)
        assert response1.status_code == 200

        # Send message in session 2
        request2 = {
            "message": "Message for session 2",
            "provider": "gemini",
            "model": "gemini-2.0-flash",
            "session_id": session2,
        }

        response2 = client.post("/chat/", json=request2)
        assert response2.status_code == 200

        # Check database isolation
        db = TestingSessionLocal()

        session1_messages = db.query(ChatHistory).filter_by(session_id=session1).all()
        session2_messages = db.query(ChatHistory).filter_by(session_id=session2).all()

        db.close()

        # Each session should have its own messages
        assert len(session1_messages) == 2  # User + AI
        assert len(session2_messages) == 2  # User + AI

        # Verify content isolation
        assert all(msg.session_id == session1 for msg in session1_messages)
        assert all(msg.session_id == session2 for msg in session2_messages)

        # Verify different models used
        ai_msg1 = next(msg for msg in session1_messages if msg.role == "assistant")
        ai_msg2 = next(msg for msg in session2_messages if msg.role == "assistant")

        assert ai_msg1.model_provider == "openai"
        assert ai_msg2.model_provider == "gemini"

    @pytest.mark.xfail(reason="AI integration not yet implemented")
    def test_model_metadata_persistence(self):
        """Test that model provider and name are correctly persisted"""
        session_id = str(uuid.uuid4())

        # Test different model combinations
        test_cases = [
            ("openai", "gpt-4o"),
            ("openai", "gpt-4o-mini"),
            ("gemini", "gemini-2.0-flash"),
            ("gemini", "gemini-1.5-pro"),
        ]

        for provider, model in test_cases:
            request_data = {
                "message": f"Test with {provider}/{model}",
                "provider": provider,
                "model": model,
                "session_id": session_id,
            }

            response = client.post("/chat/", json=request_data)
            assert response.status_code == 200

        # Check database
        db = TestingSessionLocal()
        messages = (
            db.query(ChatHistory)
            .filter_by(session_id=session_id, role="assistant")
            .order_by(ChatHistory.created_at)
            .all()
        )
        db.close()

        assert len(messages) == len(test_cases)

        # Verify each AI response has correct metadata
        for i, (provider, model) in enumerate(test_cases):
            assert messages[i].model_provider == provider
            assert messages[i].model_name == model
