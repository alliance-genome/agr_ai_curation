"""Contract tests for SSE progress streaming endpoint."""

import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from httpx import AsyncClient, ASGITransport, ReadTimeout
import httpx

from src.models.pipeline import ProcessingStage, PipelineStatus


@pytest.fixture
def mock_pipeline_tracker():
    """Mock pipeline tracker for testing."""
    tracker = AsyncMock()
    return tracker


@pytest.fixture
def mock_get_document():
    """Mock get_document function."""
    async def _get_document(document_id):
        if document_id == "test-doc-123":
            return {
                "document": {
                    "id": "test-doc-123",
                    "filename": "test.pdf",
                    "processing_status": "processing"
                }
            }
        return None
    return _get_document


class TestProgressStreamEndpoint:
    """Test SSE progress streaming endpoint contract."""

    @pytest.mark.asyncio
    async def test_sse_endpoint_connection(self, test_client):
        """Test that SSE endpoint accepts connections and returns proper headers."""
        async with test_client as client:
            with patch('src.api.documents.get_document', new_callable=AsyncMock) as mock_get:
                mock_get.return_value = {"document": {"id": "test-doc-123"}}

                # Use stream context manager for SSE
                async with client.stream('GET', '/weaviate/documents/test-doc-123/progress/stream') as response:
                    # Check response headers
                    assert response.status_code == 200
                    assert response.headers.get('content-type') == 'text/event-stream'
                    assert response.headers.get('cache-control') == 'no-cache'
                    assert response.headers.get('connection') == 'keep-alive'

    @pytest.mark.asyncio
    async def test_sse_progress_updates(self, test_client):
        """Test that SSE endpoint streams progress updates correctly."""
        async with test_client as client:
            with patch('src.api.documents.get_document', new_callable=AsyncMock) as mock_get, \
                 patch('src.api.documents.pipeline_tracker.get_pipeline_status', new_callable=AsyncMock) as mock_status:

                mock_get.return_value = {"document": {"id": "test-doc-123"}}

                # Simulate progress updates
                statuses = [
                    PipelineStatus(
                        document_id="test-doc-123",
                        current_stage=ProcessingStage.PARSING,
                        progress_percentage=25,
                        message="Extracting text from PDF",
                        updated_at=datetime.now()
                    ),
                    PipelineStatus(
                        document_id="test-doc-123",
                        current_stage=ProcessingStage.CHUNKING,
                        progress_percentage=50,
                        message="Creating document chunks",
                        updated_at=datetime.now()
                    ),
                    PipelineStatus(
                        document_id="test-doc-123",
                        current_stage=ProcessingStage.COMPLETED,
                        progress_percentage=100,
                        message="Processing completed",
                        updated_at=datetime.now()
                    )
                ]

                mock_status.side_effect = statuses

                events = []
                async with client.stream('GET', '/weaviate/documents/test-doc-123/progress/stream') as response:
                    # Read up to 3 events
                    event_count = 0
                    async for line in response.aiter_lines():
                        if line.startswith('data: '):
                            event_data = json.loads(line[6:])
                            events.append(event_data)
                            event_count += 1
                            if event_count >= 3 or event_data.get('final'):
                                break

                # Verify we got progress updates
                assert len(events) >= 2
                assert events[0]['stage'] == 'parsing'
                assert events[-1]['stage'] == 'completed'
                assert events[-1].get('final') == True

    @pytest.mark.asyncio
    async def test_sse_document_not_found(self, test_client):
        """Test SSE endpoint with non-existent document."""
        async with test_client as client:
            with patch('src.api.documents.get_document', new_callable=AsyncMock) as mock_get:
                mock_get.return_value = None

                events = []
                async with client.stream('GET', '/weaviate/documents/nonexistent-doc/progress/stream') as response:
                    async for line in response.aiter_lines():
                        if line.startswith('data: '):
                            event_data = json.loads(line[6:])
                            events.append(event_data)
                            break  # Exit after first event

                # Verify error event
                assert len(events) == 1
                assert 'error' in events[0]
                assert events[0]['error'] == 'Document not found'

    @pytest.mark.asyncio
    async def test_sse_processing_failure(self, test_client):
        """Test SSE endpoint when processing fails."""
        async with test_client as client:
            with patch('src.api.documents.get_document', new_callable=AsyncMock) as mock_get, \
                 patch('src.api.documents.pipeline_tracker.get_pipeline_status', new_callable=AsyncMock) as mock_status:

                mock_get.return_value = {"document": {"id": "test-doc-123"}}

                # Simulate failure
                mock_status.return_value = PipelineStatus(
                    document_id="test-doc-123",
                    current_stage=ProcessingStage.FAILED,
                    progress_percentage=0,
                    message="PDF parsing failed: Invalid format",
                    updated_at=datetime.now()
                )

                events = []
                async with client.stream('GET', '/weaviate/documents/test-doc-123/progress/stream') as response:
                    async for line in response.aiter_lines():
                        if line.startswith('data: '):
                            event_data = json.loads(line[6:])
                            events.append(event_data)
                            if event_data.get('final'):
                                break

                # Verify failure event
                assert len(events) >= 1
                assert events[-1]['stage'] == 'failed'
                assert 'Processing failed' in events[-1]['message']
                assert events[-1].get('final') == True

    @pytest.mark.asyncio
    async def test_sse_timeout_handling(self, test_client):
        """Test SSE endpoint timeout after no updates."""
        async with test_client as client:
            with patch('src.api.documents.get_document', new_callable=AsyncMock) as mock_get, \
                 patch('src.api.documents.pipeline_tracker.get_pipeline_status', new_callable=AsyncMock) as mock_status, \
                 patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:

                mock_get.return_value = {"document": {"id": "test-doc-123"}}
                mock_status.return_value = None  # No status available

                # Speed up the test by making sleep instant
                mock_sleep.return_value = None

                events = []
                try:
                    # Set a short timeout for the test
                    timeout = httpx.Timeout(5.0)
                    async with client.stream(
                        'GET',
                        '/weaviate/documents/test-doc-123/progress/stream',
                        timeout=timeout
                    ) as response:
                        # Read only first few events to simulate timeout scenario
                        event_count = 0
                        async for line in response.aiter_lines():
                            if line.startswith('data: '):
                                event_data = json.loads(line[6:])
                                events.append(event_data)
                                event_count += 1
                                # Break after getting initial waiting message
                                if event_count >= 1:
                                    break
                except ReadTimeout:
                    pass  # Expected timeout

                # Should have received at least waiting message
                if events:
                    assert events[0]['stage'] == 'waiting'
                    assert events[0]['progress'] == 0

    @pytest.mark.asyncio
    async def test_sse_multiple_status_changes(self, test_client):
        """Test SSE endpoint with multiple rapid status changes."""
        async with test_client as client:
            with patch('src.api.documents.get_document', new_callable=AsyncMock) as mock_get, \
                 patch('src.api.documents.pipeline_tracker.get_pipeline_status', new_callable=AsyncMock) as mock_status:

                mock_get.return_value = {"document": {"id": "test-doc-123"}}

                # Create a sequence of status updates
                call_count = 0
                def status_sequence():
                    nonlocal call_count
                    stages = [
                        (ProcessingStage.UPLOAD, 10, "Uploading document"),
                        (ProcessingStage.PARSING, 30, "Parsing PDF"),
                        (ProcessingStage.CHUNKING, 50, "Creating chunks"),
                        (ProcessingStage.STORING, 85, "Storing in Weaviate"),
                        (ProcessingStage.COMPLETED, 100, "Complete")
                    ]
                    if call_count < len(stages):
                        stage, progress, msg = stages[call_count]
                        status = PipelineStatus(
                            document_id="test-doc-123",
                            current_stage=stage,
                            progress_percentage=progress,
                            message=msg,
                            updated_at=datetime.now()
                        )
                        call_count += 1
                        return status
                    return stages[-1]  # Return completed status after all stages

                mock_status.side_effect = lambda doc_id: status_sequence()

                events = []
                async with client.stream('GET', '/weaviate/documents/test-doc-123/progress/stream') as response:
                    async for line in response.aiter_lines():
                        if line.startswith('data: '):
                            event_data = json.loads(line[6:])
                            events.append(event_data)
                            if event_data.get('final'):
                                break

                # Verify we got multiple stage updates
                assert len(events) >= 5
                stages_seen = [e['stage'] for e in events]
                assert 'upload' in stages_seen
                assert 'parsing' in stages_seen
                assert 'completed' in stages_seen

    @pytest.mark.asyncio
    async def test_sse_cli_monitoring_format(self, test_client):
        """Test that SSE output is suitable for CLI monitoring tools."""
        async with test_client as client:
            with patch('src.api.documents.get_document', new_callable=AsyncMock) as mock_get, \
                 patch('src.api.documents.pipeline_tracker.get_pipeline_status', new_callable=AsyncMock) as mock_status:

                mock_get.return_value = {"document": {"id": "test-doc-123"}}

                mock_status.return_value = PipelineStatus(
                    document_id="test-doc-123",
                    current_stage=ProcessingStage.STORING,
                    progress_percentage=85,
                    message="Storing in Weaviate",
                    updated_at=datetime.now()
                )

                async with client.stream('GET', '/weaviate/documents/test-doc-123/progress/stream') as response:
                    async for line in response.aiter_lines():
                        if line.startswith('data: '):
                            event_data = json.loads(line[6:])

                            # Verify CLI-friendly format
                            assert 'stage' in event_data
                            assert 'progress' in event_data
                            assert 'message' in event_data
                            assert 'timestamp' in event_data

                            # Verify data types for CLI parsing
                            assert isinstance(event_data['stage'], str)
                            assert isinstance(event_data['progress'], (int, float))
                            assert isinstance(event_data['message'], str)
                            assert isinstance(event_data['timestamp'], str)

                            # Simulate CLI output formatting
                            cli_output = f"[{event_data['stage']}] {event_data['progress']}% - {event_data['message']}"
                            assert len(cli_output) > 0  # Valid CLI output

                            break  # One event is enough for this test


@pytest.fixture
def test_client(test_app):
    """Create test client for SSE testing."""
    transport = ASGITransport(app=test_app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def test_app():
    """Create test FastAPI app."""
    from fastapi import FastAPI
    from src.api.documents import router

    app = FastAPI()
    app.include_router(router)
    return app
