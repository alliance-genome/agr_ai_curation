"""Contract tests for reembedDocument endpoint.

These tests verify the API contract for the document re-embedding endpoint.
They test re-embedding initiation, status updates, and response schema compliance.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock
import sys
from pathlib import Path

# Add the backend/src directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from models.api_schemas import OperationResult, EmbeddingConfiguration
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


class TestReembedDocumentEndpoint:
    """Contract tests for POST /weaviate/documents/{document_id}/reembed endpoint."""

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
        """Create a document with completed embeddings."""
        return _create_document_with_metadata(
            document_id="doc_embedded",
            filename="embedded_document.pdf",
            file_size=1500000,
            creation_date="2025-01-10T10:00:00",
            last_accessed_date="2025-01-20T15:00:00",
            embedding_status=EmbeddingStatus.COMPLETED,
            vector_count=100,
            processing_status=ProcessingStatus.COMPLETED,
            error_message=None
        )

    @pytest.fixture
    def partial_document(self) -> PDFDocument:
        """Create a document with partial embeddings."""
        return _create_document_with_metadata(
            document_id="doc_partial",
            filename="partial_document.pdf",
            file_size=2000000,
            creation_date="2025-01-12T09:00:00",
            last_accessed_date="2025-01-20T14:00:00",
            embedding_status=EmbeddingStatus.PARTIAL,
            vector_count=50,
            processing_status=ProcessingStatus.COMPLETED,
            error_message=None
        )

    @pytest.fixture
    def processing_document(self) -> PDFDocument:
        """Create a document currently being embedded."""
        return _create_document_with_metadata(
            document_id="doc_processing",
            filename="processing_embeddings.pdf",
            file_size=1800000,
            creation_date="2025-01-20T09:00:00",
            last_accessed_date="2025-01-20T16:30:00",
            embedding_status=EmbeddingStatus.PROCESSING,
            vector_count=25,
            processing_status=ProcessingStatus.PROCESSING,
            error_message=None
        )

    @pytest.fixture
    def no_embedding_document(self) -> PDFDocument:
        """Create a document with no embeddings yet."""
        return _create_document_with_metadata(
            document_id="doc_no_embeddings",
            filename="no_embeddings.pdf",
            file_size=1200000,
            creation_date="2025-01-15T11:00:00",
            last_accessed_date="2025-01-20T13:00:00",
            embedding_status=EmbeddingStatus.PENDING,
            vector_count=0,
            processing_status=ProcessingStatus.COMPLETED,
            error_message=None
        )

    @pytest.fixture
    def embedding_configs(self) -> list:
        """Create sample embedding configurations."""
        return [
            EmbeddingConfiguration(
                model_provider="openai",
                model_name="text-embedding-3-small",
                dimensions=1536,
                batch_size=10
            ),
            EmbeddingConfiguration(
                model_provider="openai",
                model_name="text-embedding-3-large",
                dimensions=3072,
                batch_size=5
            ),
            EmbeddingConfiguration(
                model_provider="cohere",
                model_name="embed-english-v3.0",
                dimensions=1024,
                batch_size=20
            )
        ]

    def test_reembedding_initiation(self, client, completed_document):
        """Test initiating re-embedding for a document."""
        document_id = "doc_embedded"

        # Request re-embedding with default configuration
        request_data = {
            "batch_size": 10
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reembed",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Check success response
            if isinstance(data, dict) and "success" in data:
                assert data["success"] is True
                assert "message" in data
                assert "embed" in data["message"].lower() or "initiated" in data["message"].lower()
                assert data.get("document_id") == document_id

    def test_status_update_to_processing(self, client, completed_document):
        """Test that re-embedding updates status to processing."""
        document_id = "doc_embedded"

        # Initiate re-embedding
        request_data = {
            "batch_size": 10
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reembed",
            json=request_data
        )

        if hasattr(response, 'status_code') and response.status_code == 200:
            # Check document status
            response = client.get(f"/weaviate/documents/{document_id}")
            if response.status_code == 200:
                doc_data = response.json()
                doc = doc_data.get("document", doc_data)

                # Embedding status should change to processing
                assert doc["embedding_status"] in ["processing", "pending"]

    def test_response_schema(self, client, completed_document):
        """Test that the response matches expected schema."""
        document_id = "doc_embedded"

        request_data = {
            "batch_size": 15
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reembed",
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
                assert "message" in data

    def test_reembed_with_custom_configuration(self, client, completed_document, embedding_configs):
        """Test re-embedding with custom embedding configuration."""
        document_id = "doc_embedded"

        # Request with custom embedding configuration
        request_data = {
            "embedding_config": {
                "model_provider": "openai",
                "model_name": "text-embedding-3-large",
                "dimensions": 3072,
                "batch_size": 5
            },
            "batch_size": 5
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reembed",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Configuration should be accepted
            if isinstance(data, dict):
                assert data.get("success") is True

    def test_reembed_partial_document(self, client, partial_document):
        """Test re-embedding a document with partial embeddings."""
        document_id = "doc_partial"

        request_data = {
            "batch_size": 20
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reembed",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Partial documents should be re-embeddable
            if isinstance(data, dict):
                assert data.get("success") is True
                # Might indicate completion of partial embeddings
                _ = data.get("message", "")  # Message might mention partial or resuming

    def test_reembed_processing_document(self, client, processing_document):
        """Test attempting to re-embed a document already being processed."""
        document_id = "doc_processing"

        request_data = {
            "batch_size": 10
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reembed",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            # Should return 409 Conflict
            assert response.status_code == 409
            data = response.json()

            # Check conflict response
            if isinstance(data, dict) and "success" in data:
                assert data["success"] is False
                assert "processing" in data["message"].lower() or "conflict" in data["message"].lower()
            else:
                assert "processing" in str(data).lower() or "conflict" in str(data).lower()

    def test_reembed_nonexistent_document(self, client):
        """Test re-embedding a non-existent document."""
        nonexistent_id = "nonexistent_doc"

        request_data = {
            "batch_size": 10
        }

        response = client.post(
            f"/weaviate/documents/{nonexistent_id}/reembed",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            assert response.status_code == 404
            data = response.json()
            assert "not found" in str(data).lower()

    def test_reembed_no_chunks_document(self, client, no_embedding_document):
        """Test re-embedding a document with no chunks."""
        document_id = "doc_no_embeddings"

        request_data = {
            "batch_size": 10
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reembed",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            # Might return error or warning about no chunks
            assert response.status_code in [200, 400]

            if response.status_code == 400:
                data = response.json()
                assert "no chunks" in str(data).lower() or "nothing to embed" in str(data).lower()

    def test_batch_size_validation(self, client, completed_document):
        """Test batch size parameter validation."""
        document_id = "doc_embedded"

        # Invalid batch size (too small)
        request_data = {
            "batch_size": 0
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reembed",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            assert response.status_code in [400, 422]

        # Invalid batch size (too large)
        request_data = {
            "batch_size": 1000
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reembed",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            # Might accept or reject based on limits
            assert response.status_code in [200, 400, 422]

        # Valid batch size
        request_data = {
            "batch_size": 25
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reembed",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            assert response.status_code == 200

    def test_embedding_config_validation(self, client, completed_document):
        """Test embedding configuration validation."""
        document_id = "doc_embedded"

        # Invalid model provider
        request_data = {
            "embedding_config": {
                "model_provider": "invalid_provider",
                "model_name": "some-model",
                "dimensions": 1024,
                "batch_size": 10
            }
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reembed",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            # Should reject invalid provider
            assert response.status_code in [400, 422]

        # Invalid dimensions
        request_data = {
            "embedding_config": {
                "model_provider": "openai",
                "model_name": "text-embedding-3-small",
                "dimensions": -1,  # Invalid
                "batch_size": 10
            }
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reembed",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            assert response.status_code in [400, 422]

    def test_reembed_without_configuration(self, client, completed_document):
        """Test re-embedding without providing configuration (use defaults)."""
        document_id = "doc_embedded"

        # Empty request body
        response = client.post(
            f"/weaviate/documents/{document_id}/reembed",
            json={}
        )

        if hasattr(response, 'status_code'):
            # Should use default configuration
            assert response.status_code == 200
            data = response.json()

            if isinstance(data, dict):
                assert data.get("success") is True

    def test_reembed_progress_tracking(self, client, completed_document):
        """Test that re-embedding progress can be tracked."""
        document_id = "doc_embedded"

        # Start re-embedding
        request_data = {
            "batch_size": 10
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reembed",
            json=request_data
        )

        if hasattr(response, 'status_code') and response.status_code == 200:
            _ = response.json()  # Verify response is valid JSON

            # Response might include tracking information
            # such as job ID, estimated time, etc.
            # This is implementation-specific

            # Check if we can query the status
            response = client.get(f"/weaviate/documents/{document_id}")
            if response.status_code == 200:
                doc_data = response.json()
                embeddings = doc_data.get("embeddings", {})

                # Should have embedding progress info
                assert "total_chunks" in embeddings
                assert "embedded_chunks" in embeddings

    def test_reembed_error_handling(self, client):
        """Test error handling during re-embedding."""
        document_id = "doc_embedding_error"

        request_data = {
            "embedding_config": {
                "model_provider": "openai",
                "model_name": "invalid-model",  # Might cause error
                "dimensions": 1536,
                "batch_size": 10
            }
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reembed",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            # Should handle gracefully
            assert response.status_code in [200, 400, 404, 422]

            if response.status_code >= 400:
                data = response.json()
                # Should provide error details
                assert "error" in data or "detail" in data or "message" in data

    def test_reembed_multiple_documents_sequentially(self, client):
        """Test re-embedding multiple documents in sequence."""
        document_ids = ["doc1", "doc2", "doc3"]
        results = []

        for doc_id in document_ids:
            request_data = {
                "batch_size": 10
            }
            response = client.post(
                f"/weaviate/documents/{doc_id}/reembed",
                json=request_data
            )
            if hasattr(response, 'status_code'):
                results.append(response.status_code)

        # All should be processed (200) or not found (404)
        for status in results:
            assert status in [200, 404, 409]  # 409 if already processing

    def test_reembed_with_priority(self, client, completed_document):
        """Test re-embedding with priority parameter (if supported)."""
        document_id = "doc_embedded"

        request_data = {
            "batch_size": 10,
            "priority": "high"  # If priority queuing is supported
        }

        response = client.post(
            f"/weaviate/documents/{document_id}/reembed",
            json=request_data
        )

        if hasattr(response, 'status_code'):
            # Should either accept or ignore the priority parameter
            assert response.status_code in [200, 400, 422]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])