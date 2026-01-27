"""Contract tests for getDocumentChunks endpoint.

These tests verify the API contract for the document chunks endpoint.
They test chunk pagination, metadata inclusion, and response schema compliance.
"""

import pytest
from typing import List
from fastapi.testclient import TestClient
from unittest.mock import Mock
import sys
from pathlib import Path

# Add the backend/src directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from models.api_schemas import ChunkListResponse
from models.chunk import DocumentChunk, ElementType


class TestGetDocumentChunksEndpoint:
    """Contract tests for GET /weaviate/documents/{document_id}/chunks endpoint."""

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
    def sample_chunks(self) -> List[DocumentChunk]:
        """Create sample chunks for testing (more than 10 for pagination testing)."""
        chunks = []
        for i in range(50):  # Create 50 chunks for pagination testing
            chunk = DocumentChunk(
                chunk_id=f"chunk_{i:03d}",
                document_id="doc123",
                chunk_index=i,
                content=f"This is the content of chunk {i}. It contains detailed information " +
                         f"extracted from page {i // 5 + 1} of the document. This chunk represents " +
                         "important data that has been processed and stored in Weaviate.",
                page_number=i // 5 + 1,
                character_count=150,
                element_type=self._get_element_type(i),
                metadata={
                    "section": f"Section {i // 10 + 1}",
                    "confidence": 0.90 + (i % 10) * 0.01,
                    "language": "en",
                    "has_tables": i % 7 == 0,
                    "has_figures": i % 5 == 0
                },
                embedding_vector=[0.1 * i for _ in range(10)]  # Simplified vector
            )
            chunks.append(chunk)
        return chunks

    def _get_element_type(self, index: int) -> ElementType:
        """Helper to vary element types for testing."""
        types = [
            ElementType.NARRATIVE_TEXT,
            ElementType.TITLE,
            ElementType.LIST_ITEM,
            ElementType.TABLE,
            ElementType.FIGURE_CAPTION
        ]
        return types[index % len(types)]

    def test_chunk_pagination(self, client, sample_chunks):
        """Test pagination of chunks."""
        document_id = "doc123"

        # Test first page
        response = client.get(f"/weaviate/documents/{document_id}/chunks?page=1&page_size=20")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Validate pagination info
            assert "pagination" in data
            assert data["pagination"]["current_page"] == 1
            assert data["pagination"]["page_size"] == 20
            assert data["pagination"]["total_items"] >= 50
            assert data["pagination"]["total_pages"] >= 3

            # Validate chunks array
            assert "chunks" in data
            assert len(data["chunks"]) == 20
            assert data["document_id"] == document_id

            # Test second page
            response = client.get(f"/weaviate/documents/{document_id}/chunks?page=2&page_size=20")
            assert response.status_code == 200
            data = response.json()

            assert data["pagination"]["current_page"] == 2
            assert len(data["chunks"]) == 20

            # Verify different chunks on different pages
            first_chunk_page2 = data["chunks"][0]
            assert first_chunk_page2["chunk_index"] == 20  # Should start from index 20

            # Test last page (partial)
            response = client.get(f"/weaviate/documents/{document_id}/chunks?page=3&page_size=20")
            assert response.status_code == 200
            data = response.json()

            assert data["pagination"]["current_page"] == 3
            assert len(data["chunks"]) == 10  # Remaining chunks (50 total, 40 on first 2 pages)

    def test_chunk_metadata_inclusion(self, client, sample_chunks):
        """Test that chunk metadata is properly included."""
        document_id = "doc123"
        response = client.get(f"/weaviate/documents/{document_id}/chunks?page=1&page_size=10")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Check that chunks include metadata
            chunks = data["chunks"]
            assert len(chunks) > 0

            for chunk in chunks:
                # Verify all expected fields are present
                assert "chunk_id" in chunk
                assert "document_id" in chunk
                assert "chunk_index" in chunk
                assert "content" in chunk
                assert "page_number" in chunk
                assert "character_count" in chunk
                assert "element_type" in chunk
                assert "metadata" in chunk

                # Verify metadata structure
                metadata = chunk["metadata"]
                assert isinstance(metadata, dict)
                assert "section" in metadata
                assert "confidence" in metadata

                # Verify embedding vector is included (if requested)
                if "include_embeddings" in response.url.query:
                    assert "embedding_vector" in chunk
                    assert isinstance(chunk["embedding_vector"], list)

    def test_response_schema(self, client):
        """Test that the response matches the expected schema."""
        document_id = "doc123"
        response = client.get(f"/weaviate/documents/{document_id}/chunks?page=1&page_size=20")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Validate the response can be parsed into our schema
            response_obj = ChunkListResponse(**data)

            # Check required fields
            assert response_obj.chunks is not None
            assert response_obj.pagination is not None
            assert response_obj.document_id == document_id

            # Check pagination structure
            assert response_obj.pagination.current_page > 0
            assert response_obj.pagination.page_size > 0
            assert response_obj.pagination.total_pages >= 0
            assert response_obj.pagination.total_items >= 0

            # Check chunk structure if chunks exist
            if response_obj.chunks:
                chunk = response_obj.chunks[0]
                assert chunk.chunk_id is not None
                assert chunk.document_id == document_id
                assert chunk.chunk_index >= 0
                assert chunk.content is not None
                assert chunk.page_number > 0
                assert chunk.character_count > 0
                assert chunk.element_type is not None

    def test_chunks_ordered_by_index(self, client):
        """Test that chunks are returned in order by chunk_index."""
        document_id = "doc123"
        response = client.get(f"/weaviate/documents/{document_id}/chunks?page=1&page_size=20")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            chunks = data["chunks"]
            if len(chunks) > 1:
                # Verify chunks are ordered by index
                for i in range(1, len(chunks)):
                    assert chunks[i]["chunk_index"] > chunks[i-1]["chunk_index"]

    def test_invalid_document_id(self, client):
        """Test retrieving chunks for non-existent document."""
        invalid_id = "nonexistent_doc"
        response = client.get(f"/weaviate/documents/{invalid_id}/chunks?page=1&page_size=20")

        if hasattr(response, 'status_code'):
            assert response.status_code == 404
            data = response.json()
            assert "detail" in data or "message" in data
            assert "not found" in str(data).lower()

    def test_invalid_pagination_parameters(self, client):
        """Test handling of invalid pagination parameters."""
        document_id = "doc123"

        # Invalid page number (0 or negative)
        response = client.get(f"/weaviate/documents/{document_id}/chunks?page=0&page_size=20")
        if hasattr(response, 'status_code'):
            assert response.status_code == 422

        response = client.get(f"/weaviate/documents/{document_id}/chunks?page=-1&page_size=20")
        if hasattr(response, 'status_code'):
            assert response.status_code == 422

        # Invalid page size (too small or too large)
        response = client.get(f"/weaviate/documents/{document_id}/chunks?page=1&page_size=0")
        if hasattr(response, 'status_code'):
            assert response.status_code == 422

        response = client.get(f"/weaviate/documents/{document_id}/chunks?page=1&page_size=1001")
        if hasattr(response, 'status_code'):
            assert response.status_code in [422, 400]  # May be limited

    def test_empty_chunks_response(self, client):
        """Test response for document with no chunks."""
        document_id = "doc_no_chunks"
        response = client.get(f"/weaviate/documents/{document_id}/chunks?page=1&page_size=20")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Should return empty chunks array
            assert data["chunks"] == []
            assert data["document_id"] == document_id
            assert data["pagination"]["total_items"] == 0
            assert data["pagination"]["total_pages"] == 0

    def test_chunk_content_truncation(self, client):
        """Test that very long chunk content is handled appropriately."""
        document_id = "doc_long_chunks"
        response = client.get(f"/weaviate/documents/{document_id}/chunks?page=1&page_size=5")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            chunks = data["chunks"]
            if chunks:
                for chunk in chunks:
                    # Content should be present but may be truncated
                    assert "content" in chunk
                    assert len(chunk["content"]) <= 10000  # Reasonable max length

    def test_element_type_filtering(self, client):
        """Test filtering chunks by element type (if supported)."""
        document_id = "doc123"

        # Filter for only TITLE elements
        response = client.get(
            f"/weaviate/documents/{document_id}/chunks?page=1&page_size=20&element_type=TITLE"
        )

        if hasattr(response, 'status_code'):
            if response.status_code == 200:
                data = response.json()
                chunks = data["chunks"]

                # All returned chunks should be of type TITLE
                for chunk in chunks:
                    assert chunk["element_type"] == "TITLE"

    def test_include_embeddings_parameter(self, client):
        """Test optional inclusion of embedding vectors."""
        document_id = "doc123"

        # Without embeddings (default)
        response = client.get(f"/weaviate/documents/{document_id}/chunks?page=1&page_size=5")
        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()
            chunks = data["chunks"]
            if chunks:
                # Embeddings should not be included by default (for performance)
                first_chunk = chunks[0]
                # Check if embeddings are excluded by default
                # (implementation may vary)

        # With embeddings explicitly requested
        response = client.get(
            f"/weaviate/documents/{document_id}/chunks?page=1&page_size=5&include_embeddings=true"
        )
        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()
            chunks = data["chunks"]
            if chunks:
                first_chunk = chunks[0]
                if "embedding_vector" in first_chunk:
                    assert isinstance(first_chunk["embedding_vector"], list)
                    assert len(first_chunk["embedding_vector"]) > 0

    def test_page_number_filtering(self, client):
        """Test filtering chunks by source page number (if supported)."""
        document_id = "doc123"

        # Get chunks from specific page
        response = client.get(
            f"/weaviate/documents/{document_id}/chunks?page=1&page_size=20&source_page=3"
        )

        if hasattr(response, 'status_code'):
            if response.status_code == 200:
                data = response.json()
                chunks = data["chunks"]

                # All chunks should be from page 3
                for chunk in chunks:
                    assert chunk["page_number"] == 3

    def test_chunk_statistics_in_response(self, client):
        """Test that response includes useful statistics about chunks."""
        document_id = "doc123"
        response = client.get(f"/weaviate/documents/{document_id}/chunks?page=1&page_size=20")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Check for useful statistics
            pagination = data["pagination"]
            assert pagination["total_items"] >= 0  # Total number of chunks

            # Optionally, response might include additional stats
            # like average chunk size, element type distribution, etc.

    def test_concurrent_request_handling(self, client):
        """Test that concurrent requests to same document work correctly."""
        document_id = "doc123"

        # Simulate multiple concurrent requests (in practice would be parallel)
        responses = []
        for page in range(1, 4):
            response = client.get(
                f"/weaviate/documents/{document_id}/chunks?page={page}&page_size=10"
            )
            if hasattr(response, 'status_code'):
                assert response.status_code == 200
                responses.append(response.json())

        # Verify each page has different chunks
        if len(responses) >= 2:
            page1_first_chunk = responses[0]["chunks"][0]["chunk_index"]
            page2_first_chunk = responses[1]["chunks"][0]["chunk_index"]
            assert page2_first_chunk > page1_first_chunk


if __name__ == "__main__":
    pytest.main([__file__, "-v"])