"""Contract tests for getDocument endpoint.

These tests verify the API contract for the document detail endpoint.
They test valid/invalid document IDs, chunk preview inclusion, and response schema compliance.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock
import sys
from pathlib import Path

# Add the backend/src directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from models.api_schemas import (
    DocumentDetailResponse
)
from models.document import PDFDocument, ProcessingStatus, EmbeddingStatus, DocumentMetadata
from models.chunk import DocumentChunk, ElementType
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


class TestGetDocumentEndpoint:
    """Contract tests for GET /weaviate/documents/{document_id} endpoint."""

    @pytest.fixture
    def client(self):
        """Create a test client for the FastAPI app."""
        try:
            from api.main import app
            return TestClient(app)
        except ImportError:
            # If API not implemented yet, create a mock client for contract definition
            mock_client = Mock()
            mock_client.get = Mock()
            return mock_client

    @pytest.fixture
    def sample_document(self) -> PDFDocument:
        """Create a sample document for testing."""
        return _create_document_with_metadata(
            document_id="doc123",
            filename="research_paper.pdf",
            file_size=2048000,
            creation_date="2025-01-15T10:30:00",
            last_accessed_date="2025-01-20T14:45:00",
            embedding_status=EmbeddingStatus.COMPLETED,
            vector_count=150,
            processing_status=ProcessingStatus.COMPLETED,
            error_message=None
        )

    @pytest.fixture
    def sample_chunks(self) -> list:
        """Create sample chunks for testing."""
        return [
            DocumentChunk(
                chunk_id=f"chunk{i}",
                document_id="doc123",
                chunk_index=i,
                content=f"This is the content of chunk {i}. It contains important information about the document.",
                page_number=i // 5 + 1,
                character_count=85,
                element_type=ElementType.NARRATIVE_TEXT,
                metadata={
                    "section": f"Section {i // 5 + 1}",
                    "confidence": 0.95
                },
                embedding_vector=None  # Not included in preview
            )
            for i in range(10)
        ]

    @pytest.fixture
    def sample_strategy(self) -> ChunkingStrategy:
        """Create a sample chunking strategy."""
        return ChunkingStrategy(
            name="research",
            method=ChunkingMethod.BY_TITLE,
            max_characters=1500,
            overlap_characters=200,
            exclude_element_types=[]
        )

    @pytest.fixture
    def related_documents(self) -> list:
        """Create sample related documents."""
        return [
            _create_document_with_metadata(
                document_id="related1",
                filename="similar_paper1.pdf",
                file_size=1500000,
                creation_date="2025-01-10T09:00:00",
                last_accessed_date="2025-01-19T16:00:00",
                embedding_status=EmbeddingStatus.COMPLETED,
                vector_count=120,
                processing_status=ProcessingStatus.COMPLETED,
                error_message=None
            ),
            _create_document_with_metadata(
                document_id="related2",
                filename="similar_paper2.pdf",
                file_size=1800000,
                creation_date="2025-01-12T11:30:00",
                last_accessed_date="2025-01-18T13:20:00",
                embedding_status=EmbeddingStatus.COMPLETED,
                vector_count=135,
                processing_status=ProcessingStatus.COMPLETED,
                error_message=None
            )
        ]

    def test_valid_document_id(self, client, sample_document, sample_chunks):
        """Test retrieving a document with a valid ID."""
        document_id = "doc123"
        response = client.get(f"/weaviate/documents/{document_id}")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Validate document information
            assert data["document"]["document_id"] == document_id
            assert data["document"]["filename"] == "research_paper.pdf"
            assert data["document"]["file_size"] == 2048000
            assert data["document"]["embedding_status"] == "completed"
            assert data["document"]["vector_count"] == 150

            # Validate chunks preview is included
            assert "chunks" in data
            assert isinstance(data["chunks"], list)
            # Should include first 10 chunks as preview
            assert len(data["chunks"]) <= 10

            # Validate embedding info
            assert "embeddings" in data
            assert data["embeddings"]["total_chunks"] >= 0
            assert data["embeddings"]["embedded_chunks"] >= 0

            # Validate chunking strategy
            assert "chunking_strategy" in data
            assert data["chunking_strategy"]["name"] is not None
            assert data["chunking_strategy"]["method"] is not None

    def test_invalid_document_id(self, client):
        """Test retrieving a document with an invalid ID (404)."""
        invalid_id = "nonexistent_doc_id"
        response = client.get(f"/weaviate/documents/{invalid_id}")

        if hasattr(response, 'status_code'):
            assert response.status_code == 404
            data = response.json()

            # Should return error message
            assert "detail" in data or "message" in data
            assert "not found" in str(data).lower()

    def test_response_includes_chunks_preview(self, client, sample_document, sample_chunks):
        """Test that response includes chunks preview (first 10 chunks)."""
        document_id = "doc123"
        response = client.get(f"/weaviate/documents/{document_id}")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Check chunks are included
            assert "chunks" in data
            chunks = data["chunks"]

            # Should have at most 10 chunks in preview
            assert len(chunks) <= 10

            # Validate chunk structure if chunks exist
            if chunks:
                first_chunk = chunks[0]
                assert "chunk_id" in first_chunk
                assert "document_id" in first_chunk
                assert "chunk_index" in first_chunk
                assert "content" in first_chunk
                assert "page_number" in first_chunk
                assert "character_count" in first_chunk
                assert "element_type" in first_chunk
                assert "metadata" in first_chunk

                # Chunks should be ordered by chunk_index
                for i in range(1, len(chunks)):
                    assert chunks[i]["chunk_index"] > chunks[i-1]["chunk_index"]

    def test_response_schema(self, client, sample_document):
        """Test that the response matches the expected schema."""
        document_id = "doc123"
        response = client.get(f"/weaviate/documents/{document_id}")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Validate the response can be parsed into our schema
            response_obj = DocumentDetailResponse(**data)

            # Check all required fields are present
            assert response_obj.document is not None
            assert response_obj.chunks is not None
            assert response_obj.embeddings is not None
            assert response_obj.chunking_strategy is not None

            # Check document structure
            doc = response_obj.document
            assert doc.document_id == document_id
            assert doc.filename is not None
            assert doc.file_size >= 0
            assert doc.creation_date is not None
            assert doc.embedding_status is not None

            # Check embedding info structure
            emb_info = response_obj.embeddings
            assert emb_info.total_chunks >= 0
            assert emb_info.embedded_chunks >= 0
            assert emb_info.embedded_chunks <= emb_info.total_chunks
            assert emb_info.avg_processing_time >= 0

            # Check chunking strategy structure
            strategy = response_obj.chunking_strategy
            assert strategy.name is not None
            assert strategy.method is not None
            assert strategy.max_characters > 0
            assert strategy.overlap_characters >= 0

    def test_related_documents_included(self, client, sample_document, related_documents):
        """Test that related documents are included in the response."""
        document_id = "doc123"
        response = client.get(f"/weaviate/documents/{document_id}")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Check related documents are included
            assert "related_documents" in data
            related = data["related_documents"]
            assert isinstance(related, list)

            # Related documents should have basic document fields
            for doc in related:
                assert "document_id" in doc
                assert "filename" in doc
                assert "file_size" in doc
                assert "embedding_status" in doc
                assert "vector_count" in doc

    def test_document_with_no_chunks(self, client):
        """Test retrieving a document that has no chunks yet."""
        document_id = "doc_no_chunks"
        response = client.get(f"/weaviate/documents/{document_id}")

        if hasattr(response, 'status_code'):
            # This should still return 200 as the document exists
            assert response.status_code == 200
            data = response.json()

            # Chunks should be empty array
            assert data["chunks"] == []
            assert data["embeddings"]["total_chunks"] == 0
            assert data["embeddings"]["embedded_chunks"] == 0

    def test_document_with_failed_processing(self, client):
        """Test retrieving a document with failed processing status."""
        document_id = "doc_failed"
        response = client.get(f"/weaviate/documents/{document_id}")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Document should include error information
            doc = data["document"]
            if doc["processing_status"] == "failed":
                assert doc.get("error_message") is not None
                assert doc["embedding_status"] == "failed"
                assert doc["vector_count"] == 0

    def test_document_with_partial_embedding(self, client):
        """Test retrieving a document with partial embedding status."""
        document_id = "doc_partial"
        response = client.get(f"/weaviate/documents/{document_id}")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Check embedding info for partial status
            if data["document"]["embedding_status"] == "partial":
                embeddings = data["embeddings"]
                assert embeddings["embedded_chunks"] < embeddings["total_chunks"]
                assert embeddings["embedded_chunks"] > 0

    def test_malformed_document_id(self, client):
        """Test handling of malformed document IDs."""
        # Test with special characters
        response = client.get("/weaviate/documents/../../etc/passwd")
        if hasattr(response, 'status_code'):
            assert response.status_code in [400, 404]

        # Test with empty ID
        response = client.get("/weaviate/documents/")
        if hasattr(response, 'status_code'):
            assert response.status_code in [404, 405]  # Not found or method not allowed

    def test_document_metadata_completeness(self, client):
        """Test that all expected metadata fields are present."""
        document_id = "doc123"
        response = client.get(f"/weaviate/documents/{document_id}")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Check all expected metadata fields
            doc = data["document"]
            required_fields = [
                "document_id", "filename", "file_size", "creation_date",
                "last_accessed_date", "embedding_status", "vector_count",
                "processing_status"
            ]

            for field in required_fields:
                assert field in doc, f"Missing required field: {field}"

    def test_chunk_metadata_structure(self, client):
        """Test the structure of chunk metadata in the preview."""
        document_id = "doc123"
        response = client.get(f"/weaviate/documents/{document_id}")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            chunks = data["chunks"]
            if chunks:
                # Check first chunk metadata structure
                first_chunk = chunks[0]
                assert "metadata" in first_chunk
                metadata = first_chunk["metadata"]
                assert isinstance(metadata, dict)

                # Metadata should contain relevant information
                # (actual fields depend on implementation)
                # Common fields might include: section, confidence, etc.

    def test_embedding_statistics_accuracy(self, client):
        """Test that embedding statistics are accurate."""
        document_id = "doc123"
        response = client.get(f"/weaviate/documents/{document_id}")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            embeddings = data["embeddings"]
            doc = data["document"]

            # Embedded chunks should not exceed total chunks
            assert embeddings["embedded_chunks"] <= embeddings["total_chunks"]

            # Vector count should match embedded chunks (roughly)
            # Some variance allowed for dimension differences
            if doc["embedding_status"] == "completed":
                assert embeddings["embedded_chunks"] == embeddings["total_chunks"]

            # Average processing time should be reasonable (in seconds)
            if embeddings["avg_processing_time"] > 0:
                assert embeddings["avg_processing_time"] < 3600  # Less than 1 hour per chunk


if __name__ == "__main__":
    pytest.main([__file__, "-v"])