"""
Tests for message history functionality in PydanticAI agents
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from typing import List
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
    SystemPromptPart,
)
from pydantic_core import to_jsonable_python

from app.agents import BioCurationAgent, BioCurationDependencies
from app.agents.models import AgentRequest, AgentResponse


@pytest.fixture
def mock_db():
    """Mock database session"""
    db = Mock()
    db.add = Mock()
    db.commit = Mock()
    db.rollback = Mock()
    return db


@pytest.fixture
def sample_message_history():
    """Sample message history for testing"""
    return [
        ModelRequest(parts=[UserPromptPart(content="What genes are in this text?")]),
        ModelResponse(parts=[TextPart(content="I found p53 and BRCA1 genes.")]),
        ModelRequest(
            parts=[UserPromptPart(content="Tell me more about the first one")]
        ),
    ]


class TestMessageHistory:
    """Test message history handling"""

    @pytest.mark.asyncio
    async def test_agent_with_message_history(self, mock_db):
        """Test agent processes with message history"""
        agent = BioCurationAgent(model="openai:gpt-4o")
        deps = BioCurationDependencies(db_session=mock_db, session_id="test-session")

        # Create sample history
        history = [
            ModelRequest(parts=[UserPromptPart(content="What is p53?")]),
            ModelResponse(parts=[TextPart(content="p53 is a tumor suppressor gene.")]),
        ]

        with patch.object(agent.agent, "run") as mock_run:
            mock_result = Mock()
            # Agent now returns string directly as output
            mock_result.output = "p53 mutations are common in cancer."
            mock_result.new_messages = Mock(
                return_value=history
                + [
                    ModelRequest(parts=[UserPromptPart(content="What mutations?")]),
                    ModelResponse(
                        parts=[TextPart(content="p53 mutations are common in cancer.")]
                    ),
                ]
            )
            mock_run.return_value = mock_result

            output, new_messages = await agent.process(
                "What mutations?", deps, message_history=history
            )

            # Verify history was passed to agent
            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args.kwargs.get("message_history") == history

            # Verify we got updated history back
            assert len(new_messages) == 4  # Original 2 + new 2

    @pytest.mark.asyncio
    async def test_history_processor_keep_recent(self):
        """Test that history processor keeps only recent messages"""
        agent = BioCurationAgent(model="openai:gpt-4o", max_history_messages=5)

        # Create long history
        long_history = []
        for i in range(10):
            long_history.append(
                ModelRequest(parts=[UserPromptPart(content=f"Message {i}")])
            )

        # Process history
        filtered = await agent._keep_recent_messages(long_history)

        # Should keep first (system) + last 4
        assert len(filtered) == 5
        assert filtered[0] == long_history[0]  # First message preserved
        assert filtered[-1] == long_history[-1]  # Last message preserved

    @pytest.mark.asyncio
    async def test_history_processor_summarize(self):
        """Test that history processor summarizes old messages"""
        agent = BioCurationAgent(
            model="openai:gpt-4o",
            max_history_messages=5,
            enable_history_summary=True,
        )

        # Create long history (more than 2x max)
        long_history = []
        for i in range(12):
            long_history.append(
                ModelRequest(parts=[UserPromptPart(content=f"Message {i}")])
            )

        with patch.object(agent, "summary_agent") as mock_summary:
            mock_result = Mock()
            mock_result.new_messages.return_value = [
                ModelRequest(parts=[UserPromptPart(content="Summary of messages")])
            ]
            mock_summary.run.return_value = mock_result

            # Process history
            filtered = await agent._summarize_old_messages(long_history)

            # Should have called summary agent
            mock_summary.run.assert_called_once()

            # Result should be shorter than original
            assert len(filtered) < len(long_history)

    @pytest.mark.asyncio
    async def test_serialization_deserialization(self, sample_message_history):
        """Test message history serialization and deserialization"""
        from pydantic_ai.messages import ModelMessagesTypeAdapter

        # Serialize to JSON-compatible format
        serialized = to_jsonable_python(sample_message_history)

        # Should be a list of dicts
        assert isinstance(serialized, list)
        assert all(isinstance(msg, dict) for msg in serialized)

        # Deserialize back
        deserialized = ModelMessagesTypeAdapter.validate_python(serialized)

        # Should match original
        assert len(deserialized) == len(sample_message_history)
        assert all(
            isinstance(msg, (ModelRequest, ModelResponse)) for msg in deserialized
        )

    @pytest.mark.asyncio
    async def test_streaming_with_history(self, mock_db):
        """Test streaming includes message history in response"""
        agent = BioCurationAgent(model="openai:gpt-4o")
        deps = BioCurationDependencies(db_session=mock_db, session_id="test-session")

        history = [
            ModelRequest(parts=[UserPromptPart(content="Previous question")]),
            ModelResponse(parts=[TextPart(content="Previous answer")]),
        ]

        updates = []
        with patch.object(agent.agent, "run_stream") as mock_stream:
            # Mock the context manager and streaming
            mock_run = AsyncMock()
            mock_stream.return_value.__aenter__.return_value = mock_run

            # Mock stream_text as a method that accepts delta parameter
            async def mock_text_gen(delta=False):
                yield "Response "
                yield "text"

            mock_run.stream_text = mock_text_gen

            # Mock new_messages as a regular method that returns a list
            mock_run.new_messages = Mock(
                return_value=history
                + [
                    ModelRequest(parts=[UserPromptPart(content="New question")]),
                    ModelResponse(parts=[TextPart(content="Response text")]),
                ]
            )

            # Collect streaming updates
            async for update in agent._process_stream("New question", deps, history):
                updates.append(update)

            # Check history was passed to run_stream
            mock_stream.assert_called_once()
            call_args = mock_stream.call_args
            assert call_args.kwargs.get("message_history") == history

            # Check we got history update
            history_updates = [u for u in updates if u.type == "history"]
            assert len(history_updates) == 1
            assert "messages" in history_updates[0].metadata

    @pytest.mark.asyncio
    async def test_delta_streaming(self, mock_db):
        """Test delta streaming mode"""
        agent = BioCurationAgent(model="openai:gpt-4o")
        deps = BioCurationDependencies(db_session=mock_db, session_id="test-session")

        updates = []
        with patch.object(agent.agent, "run_stream") as mock_stream:
            # Mock the context manager and streaming
            mock_run = AsyncMock()
            mock_stream.return_value.__aenter__.return_value = mock_run

            # Mock stream_text as a method that accepts delta parameter
            async def mock_delta_gen(delta=True):
                yield "Hello"
                yield " world"
                yield "!"

            mock_run.stream_text = mock_delta_gen

            # Mock new_messages as a regular method that returns a list
            mock_run.new_messages = Mock(return_value=[])

            # Collect streaming updates with delta mode
            async for update in agent._process_stream(
                "Test", deps, None, use_delta=True
            ):
                if update.type == "text_delta":
                    updates.append(update.content)

            # Check we got delta updates
            assert updates == ["Hello", " world", "!"]


class TestHistoryInEndpoints:
    """Test message history in API endpoints"""

    @pytest.mark.asyncio
    async def test_endpoint_with_history(self, mock_db):
        """Test endpoint handles message history correctly"""
        from app.routers.agents import biocurate
        from pydantic_ai.messages import ModelMessagesTypeAdapter

        request = AgentRequest(
            message="Follow-up question",
            session_id="test-session",
            message_history=to_jsonable_python(
                [
                    ModelRequest(parts=[UserPromptPart(content="Initial question")]),
                    ModelResponse(parts=[TextPart(content="Initial answer")]),
                ]
            ),
        )

        with patch("app.routers.agents.AgentFactory") as mock_factory:
            mock_agent = Mock()
            mock_factory.get_biocuration_agent.return_value = mock_agent

            # Create a proper BioCurationOutput instance
            from app.agents.models import BioCurationOutput

            mock_output = BioCurationOutput(
                response="Follow-up answer",
                entities=[],
                annotations=[],
                confidence=0.9,
            )

            new_messages = [
                ModelRequest(parts=[UserPromptPart(content="Initial question")]),
                ModelResponse(parts=[TextPart(content="Initial answer")]),
                ModelRequest(parts=[UserPromptPart(content="Follow-up question")]),
                ModelResponse(parts=[TextPart(content="Follow-up answer")]),
            ]

            # Mock async process method
            async def mock_process(*args, **kwargs):
                return (mock_output, new_messages)

            mock_agent.process = mock_process

            # Mock async get_usage method
            async def mock_get_usage():
                return {"tokens": 100}

            mock_agent.get_usage = mock_get_usage

            response = await biocurate(request, mock_db)

            # Check response includes message history
            assert response.message_history is not None
            assert len(response.message_history) == 4

            # Verify history can be deserialized
            deserialized = ModelMessagesTypeAdapter.validate_python(
                response.message_history
            )
            assert len(deserialized) == 4
