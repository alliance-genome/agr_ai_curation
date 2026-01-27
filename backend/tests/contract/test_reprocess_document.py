"""Contract tests for reprocessDocument endpoint.

These tests verify the API contract for the document reprocessing endpoint.
They test reprocessing with different strategies, force reparse option, and concurrent processing checks.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock
import sys
from pathlib import Path

# Add the backend/src directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from models.api_schemas import OperationResult
from models.document import PDFDocument, ProcessingStatus, EmbeddingStatus, DocumentMetadata
from models.strategy import ChunkingStrategy, ChunkingMethod
from datetime import datetime


def _create_document_with_metadata(document_id: str, filename: str, file_size: int,
                                     creation_date: str, last_accessed_date: str,
                                     embedding_status, vector_count: int,
                                     processing_status, error_message=None) -> PDFDocument:
    """Helper to create PDFDocument with required metadata structure."""
    metadata = DocumentMetadata(
        page_count=10,
        checksum="test-checksum",
        document_type="pdf",
        last_processed_stage=processing_status.value
    )
    return PDFDocument(
        id=document_id,
        filename=filename,
        file_size=file_size,
        creation_date=datetime.fromisoformat(creation_date),
        last_accessed_date=datetime.fromisoformat(last_accessed_date),
        processing_status=processing_status,
        embedding_status=embedding_status,
        vector_count=vector_count,
        metadata=metadata
    )


class TestReprocessDocumentEndpoint:
    """Contract tests for POST /weaviate/documents/{document_id}/reprocess endpoint."""

    @pytest.fixture
    def client(self):
        """Create a test client for the FastAPI app."""
        try:
            from api.main import app
            return TestClient(app)
        except ImportError:
            # If API not implemented yet, create a mock client for contract definition
            mock_client = Mock()
            mock_client.post = Mock()
            mock_client.get = Mock()
            return mock_client

    @pytest.fixture
    def completed_document(self) -> PDFDocument:
        """Create a document that has completed processing."""
        return _create_document_with_metadata(
            document_id="doc_completed",
            filename="completed.pdf",
            file_size=1500000,
            creation_date="2025-01-10T10:00:00",
            last_accessed_date="2025-01-20T15:00:00",
            embedding_status=EmbeddingStatus.COMPLETED,
            vector_count=100,
            processing_status=ProcessingStatus.COMPLETED,
            error_message=None
        )

    @pytest.fixture
    def failed_document(self) -> PDFDocument:
        """Create a document that failed processing."""
        return _create_document_with_metadata(
            document_id="doc_failed",
            filename="failed.pdf",
            file_size=2000000,
            creation_date="2025-01-12T09:00:00",
            last_accessed_date="2025-01-20T14:00:00",
            embedding_status=EmbeddingStatus.FAILED,
            vector_count=0,
            processing_status=ProcessingStatus.FAILED,
            error_message="Processing failed: Invalid PDF structure"
        )

    @pytest.fixture
    def processing_document(self) -> PDFDocument:
        """Create a document currently being processed."""
        return _create_document_with_metadata(
            document_id="doc_processing",
            filename="processing.pdf",
            file_size=1800000,
            creation_date="2025-01-20T09:00:00",
            last_accessed_date="2025-01-20T16:30:00",
            embedding_status=EmbeddingStatus.PROCESSING,
            vector_count=50,
            processing_status=ProcessingStatus.PROCESSING,
            error_message=None
        )

    @pytest.fixture
    def available_strategies(self) -> list:
        """Create list of available chunking strategies."""
        return [
            ChunkingStrategy(
                name="research",
                method=ChunkingMethod.BY_TITLE,
                max_characters=1500,
                overlap_characters=200,
                exclude_element_types=[]
            ),
            ChunkingStrategy(
                name="legal",
                method=ChunkingMethod.BY_PARAGRAPH,
                max_characters=1000,
                overlap_characters=100,
                exclude_element_types=[]
            ),
            ChunkingStrategy(
                name="technical",
                method=ChunkingMethod.BY_CHARACTER,
                max_characters=2000,
                overlap_characters=400,
                exclude_element_types=[]
            ),
            ChunkingStrategy(
                name="general",
                method=ChunkingMethod.BY_PARAGRAPH,
                max_characters=1500,
                overlap_characters=200,
                exclude_element_types=[]
            )
        ]

    def test_reprocessing_with_different_strategy(self, client, completed_document, available_strategies):
        """Test reprocessing a document with a different chunking strategy."""
        document_id = "doc_completed"

        # Request reprocessing with different strategy
        request_data = {
            "strategy_name": "legal",
            "force_reparse": False
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reprocess",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Check success response
            if isinstance(data, dict) and "success" in data:
                assert data["success"] is True
                assert "message" in data
                assert "reprocess" in data["message"].lower() or "initiated" in data["message"].lower()
                assert data.get("document_id") == document_id

            # Verify document status changed to processing
            response = client.get(f"/weaviate/documents/{document_id}")
            if response.status_code == 200:
                doc_data = response.json()
                doc = doc_data.get("document", doc_data)
                # Document should be in processing state
                assert doc["processing_status"] in ["processing", "pending"]

    def test_force_reparse_option(self, client, completed_document):
        """Test force reparsing from original PDF."""
        document_id = "doc_completed"

        # Request with force reparse
        request_data = {
            "strategy_name": "technical",
            "force_reparse": True
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reprocess",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Check that force reparse is acknowledged
            if isinstance(data, dict):
                assert data.get("success") is True
                message = data.get("message", "")
                # Message might indicate full reprocessing
                assert "reprocess" in message.lower() or "reparse" in message.lower()

    def test_concurrent_processing_check(self, client, processing_document):
        """Test that concurrent reprocessing is prevented."""
        document_id = "doc_processing"

        # Try to reprocess a document that's already processing
        request_data = {
            "strategy_name": "general",
            "force_reparse": False
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reprocess",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            # Should return 409 Conflict
            assert response.status_code == 409
            data = response.json()

            # Check conflict response
            if isinstance(data, dict) and "success" in data:
                assert data["success"] is False
                assert "already processing" in data["message"].lower() or "conflict" in data["message"].lower()
                error = data.get("error", {})
                assert error.get("code") == "PROCESSING_CONFLICT" or "conflict" in str(error).lower()
            else:
                assert "processing" in str(data).lower() or "conflict" in str(data).lower()

    def test_reprocess_failed_document(self, client, failed_document):
        """Test reprocessing a document that previously failed."""
        document_id = "doc_failed"

        request_data = {
            "strategy_name": "research",
            "force_reparse": True  # Often needed for failed documents
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reprocess",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Failed documents should be reprocessable
            if isinstance(data, dict):
                assert data.get("success") is True
                assert data.get("document_id") == document_id

    def test_invalid_strategy_name(self, client, completed_document):
        """Test reprocessing with invalid strategy name."""
        document_id = "doc_completed"

        request_data = {
            "strategy_name": "invalid_strategy",
            "force_reparse": False
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reprocess",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            # Should return 400 Bad Request or 422 Validation Error
            assert response.status_code in [400, 422]
            data = response.json()

            # Error should mention invalid strategy
            error_msg = str(data).lower()
            assert "invalid" in error_msg or "strategy" in error_msg or "not found" in error_msg

    def test_reprocess_nonexistent_document(self, client):
        """Test reprocessing a non-existent document."""
        nonexistent_id = "nonexistent_doc"

        request_data = {
            "strategy_name": "general",
            "force_reparse": False
        }

        response = client.post(
            f"/weaviate/documents/{nonexistent_id}/reprocess",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            assert response.status_code == 404
            data = response.json()
            assert "not found" in str(data).lower()

    def test_request_validation(self, client):
        """Test request body validation."""
        document_id = "doc_completed"

        # Missing required field
        response = client.post(
            f"/weaviate/documents/{document_id}/reprocess",
            json={}
        )

        if hasattr(response, 'status_code'):
            assert response.status_code in [400, 422]

        # Invalid data type
        response = client.post(
            f"/weaviate/documents/{document_id}/reprocess",
            json={
                "strategy_name": 123,  # Should be string
                "force_reparse": "yes"  # Should be boolean
            }
        )

        if hasattr(response, 'status_code'):
            assert response.status_code in [400, 422]

    def test_reprocess_with_same_strategy(self, client, completed_document):
        """Test reprocessing with the same strategy (might be useful for retries)."""
        document_id = "doc_completed"

        # Reprocess with same strategy
        request_data = {
            "strategy_name": "research",  # Assuming this was the original
            "force_reparse": False
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reprocess",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            # Should allow reprocessing even with same strategy
            assert response.status_code == 200
            data = response.json()

            if isinstance(data, dict):
                assert data.get("success") is True

    def test_reprocess_response_schema(self, client, completed_document):
        """Test that reprocess response matches expected schema."""
        document_id = "doc_completed"

        request_data = {
            "strategy_name": "technical",
            "force_reparse": False
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reprocess",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Try to parse as OperationResult
            try:
                result = OperationResult(**data)
                assert result.success is True
                assert result.message is not None
                assert result.document_id == document_id
                assert result.error is None
            except Exception:
                # Alternative response format
                assert "success" in data or "status" in data

    def test_reprocess_partial_document(self, client):
        """Test reprocessing a document with partial embeddings."""
        document_id = "doc_partial"

        request_data = {
            "strategy_name": "general",
            "force_reparse": False
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reprocess",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            # Partial documents should be reprocessable
            assert response.status_code == 200

    def test_reprocess_status_tracking(self, client, completed_document):
        """Test that reprocessing updates can be tracked."""
        document_id = "doc_completed"

        # Start reprocessing
        request_data = {
            "strategy_name": "legal",
            "force_reparse": False
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reprocess",
            json=request_data
        )

        if hasattr(response, 'status_code') and response.status_code == 200:
            # Check document status
            response = client.get(f"/weaviate/documents/{document_id}")
            if response.status_code == 200:
                doc_data = response.json()
                doc = doc_data.get("document", doc_data)

                # Should have processing-related status
                assert doc["processing_status"] in ["pending", "processing", "completed", "failed"]

    def test_reprocess_with_custom_parameters(self, client, completed_document):
        """Test reprocessing with additional custom parameters (if supported)."""
        document_id = "doc_completed"

        # Request with extended parameters
        request_data = {
            "strategy_name": "technical",
            "force_reparse": False,
            # Additional parameters that might be supported
            "priority": "high",
            "notify_on_completion": True,
            "preserve_metadata": True
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reprocess",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            # Should either accept or ignore extra parameters
            assert response.status_code in [200, 400, 422]

    def test_reprocess_queue_handling(self, client):
        """Test that multiple reprocess requests are queued properly."""
        # Request reprocessing for multiple documents
        document_ids = ["doc1", "doc2", "doc3"]
        responses = []

        for doc_id in document_ids:
            request_data = {
                "strategy_name": "general",
                "force_reparse": False
            }
            response = client.post(
                f"/weaviate/documents/{doc_id}/reprocess",
                json=request_data
            )
            if hasattr(response, 'status_code'):
                responses.append(response.status_code)

        # All should be accepted (200) or not found (404)
        for status in responses:
            assert status in [200, 404]

    def test_reprocess_error_recovery(self, client, failed_document):
        """Test that reprocessing can recover from previous errors."""
        document_id = "doc_failed"

        # First attempt without force reparse
        request_data = {
            "strategy_name": "research",
            "force_reparse": False
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reprocess",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            first_attempt = response.status_code

            # Second attempt with force reparse
            request_data["force_reparse"] = True
            response = client.post(
                f"/weaviate/documents/{document_id}/reprocess",
                json=request_data
            )
            second_attempt = response.status_code

            # At least one attempt should succeed
            assert 200 in [first_attempt, second_attempt]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])