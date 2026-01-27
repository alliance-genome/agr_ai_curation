"""Contract tests for deleteDocument endpoint.

These tests verify the API contract for the document deletion endpoint.
They test successful deletion, non-existent documents, and processing conflicts.
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


class TestDeleteDocumentEndpoint:
    """Contract tests for DELETE /weaviate/documents/{document_id} endpoint."""

    @pytest.fixture
    def client(self):
        """Create a test client for the FastAPI app."""
        try:
            from api.main import app
            return TestClient(app)
        except ImportError:
            # If API not implemented yet, create a mock client for contract definition
            mock_client = Mock()
            mock_client.delete = Mock()
            mock_client.get = Mock()
            return mock_client

    @pytest.fixture
    def existing_document(self) -> PDFDocument:
        """Create a sample existing document."""
        return _create_document_with_metadata(
            document_id="doc123",
            filename="existing_document.pdf",
            file_size=1500000,
            creation_date="2025-01-15T10:00:00",
            last_accessed_date="2025-01-20T15:00:00",
            embedding_status=EmbeddingStatus.COMPLETED,
            vector_count=100,
            processing_status=ProcessingStatus.COMPLETED,
            error_message=None
        )

    @pytest.fixture
    def processing_document(self) -> PDFDocument:
        """Create a document currently being processed."""
        return _create_document_with_metadata(
            document_id="doc_processing",
            filename="processing_document.pdf",
            file_size=2000000,
            creation_date="2025-01-20T09:00:00",
            last_accessed_date="2025-01-20T16:00:00",
            embedding_status=EmbeddingStatus.PROCESSING,
            vector_count=25,
            processing_status=ProcessingStatus.PROCESSING,
            error_message=None
        )

    def test_successful_deletion(self, client, existing_document):
        """Test successful deletion of an existing document."""
        document_id = "doc123"

        # First verify document exists
        response = client.get(f"/weaviate/documents/{document_id}")
        if hasattr(response, 'status_code'):
            # Document should exist
            assert response.status_code == 200

            # Now delete the document
            response = client.delete(f"/weaviate/documents/{document_id}")
            assert response.status_code == 200
            data = response.json()

            # Check success response
            if isinstance(data, dict):
                # If using OperationResult schema
                assert data.get("success") is True
                assert "message" in data
                assert "deleted" in data["message"].lower() or "removed" in data["message"].lower()
                assert data.get("document_id") == document_id
            else:
                # Alternative simple response
                assert "message" in data or "status" in data

            # Verify document no longer exists
            response = client.get(f"/weaviate/documents/{document_id}")
            assert response.status_code == 404

    def test_deletion_of_nonexistent_document(self, client):
        """Test deletion of a non-existent document."""
        nonexistent_id = "nonexistent_doc_id"

        response = client.delete(f"/weaviate/documents/{nonexistent_id}")

        if hasattr(response, 'status_code'):
            # Should return 404 Not Found
            assert response.status_code == 404
            data = response.json()

            # Check error response
            if isinstance(data, dict) and "success" in data:
                # If using OperationResult schema
                assert data["success"] is False
                assert "message" in data
                assert "not found" in data["message"].lower()
                assert data.get("error") is not None
            else:
                # Alternative error response
                assert "detail" in data or "message" in data
                assert "not found" in str(data).lower()

    def test_deletion_during_processing(self, client, processing_document):
        """Test deletion of document currently being processed (409 Conflict)."""
        document_id = "doc_processing"

        response = client.delete(f"/weaviate/documents/{document_id}")

        if hasattr(response, 'status_code'):
            # Should return 409 Conflict
            assert response.status_code == 409
            data = response.json()

            # Check conflict response
            if isinstance(data, dict) and "success" in data:
                # If using OperationResult schema
                assert data["success"] is False
                assert "message" in data
                assert "processing" in data["message"].lower() or "conflict" in data["message"].lower()
                error = data.get("error", {})
                assert error.get("code") == "PROCESSING_CONFLICT" or "conflict" in str(error).lower()
            else:
                # Alternative error response
                assert "detail" in data or "message" in data
                assert "processing" in str(data).lower() or "conflict" in str(data).lower()

    def test_cascade_chunk_deletion(self, client, existing_document):
        """Test that deleting a document also deletes its chunks."""
        document_id = "doc123"

        # First check that chunks exist
        response = client.get(f"/weaviate/documents/{document_id}/chunks?page=1&page_size=10")
        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            initial_chunks = response.json()
            assert len(initial_chunks.get("chunks", [])) > 0

            # Delete the document
            response = client.delete(f"/weaviate/documents/{document_id}")
            assert response.status_code == 200

            # Verify chunks are also deleted
            response = client.get(f"/weaviate/documents/{document_id}/chunks?page=1&page_size=10")
            assert response.status_code == 404  # Document and its chunks no longer exist

    def test_deletion_response_schema(self, client, existing_document):
        """Test that deletion response matches expected schema."""
        document_id = "doc123"

        response = client.delete(f"/weaviate/documents/{document_id}")

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
                assert "message" in data or "status" in data

    def test_deletion_idempotency(self, client):
        """Test that multiple deletion attempts are handled gracefully."""
        document_id = "doc123"

        # First deletion
        response = client.delete(f"/weaviate/documents/{document_id}")
        if hasattr(response, 'status_code'):
            first_status = response.status_code

            # Second deletion attempt
            response = client.delete(f"/weaviate/documents/{document_id}")
            second_status = response.status_code

            # Second attempt should return 404 (already deleted)
            if first_status == 200:
                assert second_status == 404

            # Both attempts should return valid responses
            assert second_status in [200, 404]

    def test_deletion_with_special_characters_in_id(self, client):
        """Test handling of document IDs with special characters."""
        # Test URL encoding
        special_id = "doc-123_test.pdf"
        response = client.delete(f"/weaviate/documents/{special_id}")

        if hasattr(response, 'status_code'):
            # Should handle the ID properly or return appropriate error
            assert response.status_code in [200, 404, 400]

    def test_deletion_authorization(self, client):
        """Test deletion with authorization headers (if implemented)."""
        document_id = "doc123"

        # Test without authorization (if auth is required)
        response = client.delete(f"/weaviate/documents/{document_id}")

        if hasattr(response, 'status_code'):
            # If authorization is implemented, might return 401/403
            # Otherwise should work normally
            assert response.status_code in [200, 401, 403, 404]

    def test_deletion_of_failed_document(self, client):
        """Test deletion of document with failed processing status."""
        document_id = "doc_failed"

        response = client.delete(f"/weaviate/documents/{document_id}")

        if hasattr(response, 'status_code'):
            # Failed documents should be deletable
            assert response.status_code in [200, 404]

            if response.status_code == 200:
                data = response.json()
                # Check successful deletion
                if "success" in data:
                    assert data["success"] is True

    def test_deletion_of_partial_document(self, client):
        """Test deletion of document with partial embedding status."""
        document_id = "doc_partial"

        response = client.delete(f"/weaviate/documents/{document_id}")

        if hasattr(response, 'status_code'):
            # Partial documents should be deletable
            assert response.status_code in [200, 404]

            if response.status_code == 200:
                data = response.json()
                # Check successful deletion
                if "success" in data:
                    assert data["success"] is True

    def test_deletion_cleanup_verification(self, client):
        """Test that deletion properly cleans up all related data."""
        document_id = "doc123"

        # Delete the document
        response = client.delete(f"/weaviate/documents/{document_id}")

        if hasattr(response, 'status_code') and response.status_code == 200:
            # Verify document is gone
            response = client.get(f"/weaviate/documents/{document_id}")
            assert response.status_code == 404

            # Verify chunks are gone
            response = client.get(f"/weaviate/documents/{document_id}/chunks")
            assert response.status_code == 404

            # Verify document doesn't appear in list
            response = client.get(f"/weaviate/documents?search={document_id}")
            if response.status_code == 200:
                data = response.json()
                doc_ids = [doc.get("document_id") for doc in data.get("documents", [])]
                assert document_id not in doc_ids

    def test_concurrent_deletion_handling(self, client):
        """Test handling of concurrent deletion requests."""
        document_id = "doc123"

        # Simulate concurrent deletion attempts
        # In practice, these would be parallel requests
        responses = []
        for _ in range(3):
            response = client.delete(f"/weaviate/documents/{document_id}")
            if hasattr(response, 'status_code'):
                responses.append(response.status_code)

        # First should succeed (200), others should get 404
        if responses:
            assert 200 in responses or all(status == 404 for status in responses)

    def test_deletion_error_details(self, client):
        """Test that deletion errors provide useful details."""
        # Try to delete with malformed ID
        response = client.delete("/weaviate/documents/../../etc/passwd")

        if hasattr(response, 'status_code'):
            if response.status_code >= 400:
                data = response.json()

                # Error response should have details
                if "error" in data:
                    error = data["error"]
                    assert "code" in error or "type" in error
                    assert "details" in error or "message" in error
                else:
                    assert "detail" in data or "message" in data

    def test_deletion_audit_trail(self, client):
        """Test that deletion operations can be tracked (if audit is implemented)."""
        document_id = "doc123"

        response = client.delete(f"/weaviate/documents/{document_id}")

        if hasattr(response, 'status_code') and response.status_code == 200:
            _ = response.json()  # Verify response is valid JSON

            # Response might include audit information
            # such as timestamp, user, etc.
            # This is implementation-specific


if __name__ == "__main__":
    pytest.main([__file__, "-v"])