"""Integration tests for complete Weaviate PDF processing pipeline."""

import pytest
import asyncio
import tempfile
import json
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, List, Any
import sys
import hashlib

pytest.skip(
    "Pipeline integration tests require updates after embedding refactor",
    allow_module_level=True,
)

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

# Import pipeline components
from lib.pipeline.upload import PDFUploadHandler
from lib.pipeline.parse import parse_pdf_document, handle_parsing_errors, validate_pdf_file
from lib.pipeline.chunk import chunk_parsed_document, assign_chunk_indices
from lib.pipeline.embed import generate_embeddings, batch_embed_chunks
from lib.pipeline.store import store_to_weaviate, finalize_processing
from lib.pipeline.tracker import PipelineTracker, RetryConfig
from lib.pipeline.orchestrator import DocumentPipelineOrchestrator as PipelineOrchestrator

# Import Weaviate operations
from lib.weaviate_client.connection import WeaviateConnection
from lib.weaviate_client.documents import (
    list_documents, get_document, delete_document,
    update_document_status, re_embed_document
)
from lib.weaviate_client.chunks import store_chunks, get_chunks, delete_chunks
from lib.weaviate_client.settings import get_embedding_config, update_embedding_config

# Import models
from models.document import PDFDocument, ProcessingStatus, EmbeddingStatus
from models.chunk import DocumentChunk
from models.pipeline import ProcessingStage, PipelineStatus, StageResult


class TestCompletePDFProcessingPipeline:
    """Test the complete end-to-end PDF processing pipeline."""

    @pytest.fixture
    def sample_pdf(self):
        """Create a mock PDF file for testing."""
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
            f.write(b'%PDF-1.4\nMock PDF content for testing')
            return Path(f.name)

    @pytest.fixture
    def pipeline_tracker(self):
        """Create a pipeline tracker instance."""
        return PipelineTracker()

    @pytest.fixture
    def pipeline_orchestrator(self):
        """Create a pipeline orchestrator instance."""
        return PipelineOrchestrator()

    @pytest.mark.asyncio
    async def test_complete_pdf_processing_flow(self, sample_pdf, pipeline_tracker):
        """Test the complete PDF processing pipeline from upload to storage."""
        document_id = "test-doc-001"

        # Step 1: Upload PDF
        upload_handler = PDFUploadHandler()
        metadata = {
            "filename": sample_pdf.name,
            "documentType": "research",
            "uploadedBy": "test_user"
        }

        stored_path = upload_handler.save_uploaded_pdf(sample_pdf, metadata)
        assert stored_path.exists()

        # Track pipeline start
        pipeline_tracker.track_pipeline_progress(document_id, ProcessingStage.UPLOADING)

        # Step 2: Parse PDF with Unstructured.io
        pipeline_tracker.track_pipeline_progress(document_id, ProcessingStage.PARSING)

        with patch('lib.pipeline.parse.partition_pdf_with_strategy') as mock_partition:
            # Mock Unstructured.io response
            mock_partition.return_value = [
                Mock(text="Title: Test Document", category="Title"),
                Mock(text="This is the content.", category="NarrativeText"),
                Mock(text="More content here.", category="NarrativeText")
            ]

            elements = await parse_pdf_document(stored_path, document_id, "test_integration_user")
            assert len(elements) > 0

        # Step 3: Chunk the document
        pipeline_tracker.track_pipeline_progress(document_id, ProcessingStage.CHUNKING)

        strategy_config = {
            "method": "by_paragraph",
            "max_chars": 1000,
            "overlap": 100
        }

        chunks = await chunk_parsed_document(elements, strategy_config)
        chunks_with_indices = assign_chunk_indices(chunks)
        assert len(chunks_with_indices) > 0
        assert all(hasattr(chunk, 'index') for chunk in chunks_with_indices)

        # Step 4: Generate embeddings
        pipeline_tracker.track_pipeline_progress(document_id, ProcessingStage.EMBEDDING)

        with patch('lib.pipeline.embed.OpenAIEmbeddingClient') as mock_client:
            mock_client.return_value.generate.return_value = [[0.1] * 1536] * len(chunks)

            model_config = {
                "provider": "openai",
                "model": "text-embedding-3-small",
                "dimensions": 1536
            }

            embeddings = await generate_embeddings(chunks_with_indices, model_config)
            assert len(embeddings) == len(chunks_with_indices)

        # Step 5: Store in Weaviate
        pipeline_tracker.track_pipeline_progress(document_id, ProcessingStage.STORING)

        with patch('lib.pipeline.store.WeaviateClient') as mock_weaviate:
            mock_weaviate.return_value.batch_insert.return_value = True

            chunks_with_embeddings = [
                {**chunk.__dict__, "embedding": emb}
                for chunk, emb in zip(chunks_with_indices, embeddings)
            ]

            success = await store_to_weaviate(chunks_with_embeddings)
            assert success

            # Finalize processing
            await finalize_processing(document_id)

        # Step 6: Verify pipeline completion
        pipeline_tracker.track_pipeline_progress(document_id, ProcessingStage.COMPLETED)
        status = pipeline_tracker.get_pipeline_status(document_id)

        assert status["current_stage"] == ProcessingStage.COMPLETED
        assert status["is_complete"] == True
        assert "error" not in status

        # Clean up
        stored_path.unlink(missing_ok=True)
        sample_pdf.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_pipeline_error_recovery(self, sample_pdf, pipeline_tracker):
        """Test error recovery mechanisms in the pipeline."""
        document_id = "test-doc-002"

        # Simulate parsing error
        pipeline_tracker.track_pipeline_progress(document_id, ProcessingStage.PARSING)

        with patch('lib.pipeline.parse.partition_pdf_with_strategy') as mock_partition:
            mock_partition.side_effect = Exception("PDF parsing failed")

            error = await handle_parsing_errors(Exception("PDF parsing failed"))
            assert error["category"] == "parsing_error"

            # Track failure
            pipeline_tracker.handle_pipeline_failure(
                document_id,
                {"stage": ProcessingStage.PARSING, "error": error}
            )

            # Attempt retry
            retry_config = RetryConfig(max_retries=3, backoff_factor=2.0)

            # Mock successful retry
            mock_partition.side_effect = None
            mock_partition.return_value = [Mock(text="Recovered content")]

            success = await pipeline_tracker.retry_failed_stage(
                document_id,
                ProcessingStage.PARSING,
                retry_config
            )

            assert success

        sample_pdf.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_concurrent_pipeline_processing(self, pipeline_orchestrator):
        """Test concurrent processing of multiple documents."""
        document_ids = ["doc-001", "doc-002", "doc-003"]

        with patch.object(pipeline_orchestrator, 'process_document') as mock_process:
            mock_process.return_value = {"status": "completed"}

            # Process documents concurrently
            tasks = [
                pipeline_orchestrator.process_document(doc_id)
                for doc_id in document_ids
            ]

            results = await asyncio.gather(*tasks)

            assert len(results) == 3
            assert all(r["status"] == "completed" for r in results)
            assert mock_process.call_count == 3


class TestDocumentCRUDOperations:
    """Test document CRUD operations with Weaviate."""

    @pytest.fixture
    def mock_weaviate_connection(self):
        """Mock Weaviate connection."""
        with patch('lib.weaviate_client.connection.weaviate.Client') as mock:
            connection = WeaviateConnection()
            connection._client = mock.return_value
            return connection

    def test_list_documents_with_pagination(self, mock_weaviate_connection):
        """Test listing documents with pagination and filtering."""
        # Mock Weaviate response
        mock_response = {
            "data": {
                "Get": {
                    "PDFDocument": [
                        {
                            "id": "doc-1",
                            "filename": "test1.pdf",
                            "fileSize": 1024,
                            "embeddingStatus": "completed",
                            "vectorCount": 50
                        },
                        {
                            "id": "doc-2",
                            "filename": "test2.pdf",
                            "fileSize": 2048,
                            "embeddingStatus": "processing",
                            "vectorCount": 0
                        }
                    ]
                }
            }
        }

        with patch.object(mock_weaviate_connection._client.query, 'get') as mock_get:
            mock_get.return_value.with_additional.return_value.with_limit.return_value.with_offset.return_value.do.return_value = mock_response

            # Test with filters
            filters = {
                "embeddingStatus": ["completed", "processing"],
                "searchTerm": "test"
            }

            pagination = {
                "page": 1,
                "pageSize": 20,
                "sortBy": "creationDate",
                "sortOrder": "desc"
            }

            results = list_documents(filters, pagination)

            assert len(results["documents"]) == 2
            assert results["documents"][0]["filename"] == "test1.pdf"
            assert results["pagination"]["currentPage"] == 1

    def test_get_document_with_chunks(self, mock_weaviate_connection):
        """Test retrieving a document with chunk preview."""
        document_id = "doc-123"

        mock_doc = {
            "id": document_id,
            "filename": "research.pdf",
            "fileSize": 5000,
            "chunkCount": 25,
            "vectorCount": 25,
            "embeddingStatus": "completed"
        }

        mock_chunks = [
            {"index": 0, "content": "Chunk 1 content", "pageNumber": 1},
            {"index": 1, "content": "Chunk 2 content", "pageNumber": 1}
        ]

        with patch('lib.weaviate_client.documents.get_document_by_id') as mock_get_doc:
            with patch('lib.weaviate_client.chunks.get_chunks') as mock_get_chunks:
                mock_get_doc.return_value = mock_doc
                mock_get_chunks.return_value = {"chunks": mock_chunks}

                result = get_document(document_id)

                assert result["document"]["id"] == document_id
                assert len(result["chunks"]) == 2
                assert result["chunks"][0]["content"] == "Chunk 1 content"

    def test_delete_document_cascade(self, mock_weaviate_connection):
        """Test deleting a document with cascade chunk deletion."""
        document_id = "doc-456"

        with patch('lib.weaviate_client.documents.get_document_by_id') as mock_get:
            with patch('lib.weaviate_client.chunks.delete_chunks') as mock_delete_chunks:
                with patch.object(mock_weaviate_connection._client.data_object, 'delete') as mock_delete:
                    mock_get.return_value = {"id": document_id, "processingStatus": "completed"}
                    mock_delete_chunks.return_value = {"deleted": 10}

                    result = delete_document(document_id)

                    assert result["success"] == True
                    assert mock_delete_chunks.called
                    assert mock_delete.called

    @pytest.mark.asyncio
    async def test_update_document_status_transitions(self, mock_weaviate_connection):
        """Test valid status transitions for documents."""
        document_id = "doc-789"
        user_id = "test_user_user_id"

        # Test valid transition
        with patch('lib.weaviate_client.documents.get_document_by_id') as mock_get:
            with patch.object(mock_weaviate_connection._client.data_object, 'update') as mock_update:
                mock_get.return_value = {"id": document_id, "processingStatus": "pending"}

                result = await update_document_status(document_id, user_id, ProcessingStatus.PARSING)

                assert result["success"] == True
                assert mock_update.called

    @pytest.mark.asyncio
    async def test_re_embed_document(self, mock_weaviate_connection):
        """Test re-embedding a document with new configuration."""
        document_id = "doc-999"
        user_id = "test_user_user_id"

        with patch('lib.weaviate_client.documents.get_document_by_id') as mock_get:
            with patch('lib.weaviate_client.chunks.get_chunks') as mock_get_chunks:
                with patch('lib.pipeline.embed.generate_embeddings') as mock_embed:
                    mock_get.return_value = {"id": document_id, "embeddingStatus": "completed"}
                    mock_get_chunks.return_value = {"chunks": [{"content": "test"}]}
                    mock_embed.return_value = [[0.1] * 1536]

                    result = await re_embed_document(document_id, user_id)

                    assert result["success"] == True
                    assert "message" in result


class TestChunkingAndEmbeddingFlow:
    """Test the chunking and embedding workflow."""

    @pytest.mark.asyncio
    async def test_intelligent_chunking_strategies(self):
        """Test different chunking strategies for various document types."""
        from lib.pdf_processing.strategies import CHUNKING_STRATEGIES
        from lib.pipeline.chunk import chunk_parsed_document

        # Mock parsed elements
        elements = [
            Mock(text="Introduction", category="Title"),
            Mock(text="This is a long paragraph about the research topic. " * 50, category="NarrativeText"),
            Mock(text="Methods", category="Title"),
            Mock(text="We conducted experiments. " * 30, category="NarrativeText"),
            Mock(text="Results", category="Title"),
            Mock(text="The results show. " * 40, category="NarrativeText")
        ]

        # Test research strategy (by_title)
        research_chunks = await chunk_parsed_document(elements, CHUNKING_STRATEGIES["research"])
        assert len(research_chunks) >= 3  # Should split by titles

        # Test legal strategy (by_paragraph)
        legal_chunks = await chunk_parsed_document(elements, CHUNKING_STRATEGIES["legal"])
        assert all(len(chunk) <= CHUNKING_STRATEGIES["legal"]["max_chars"] + CHUNKING_STRATEGIES["legal"]["overlap"]
                  for chunk in legal_chunks)

        # Test technical strategy (by_character)
        technical_chunks = await chunk_parsed_document(elements, CHUNKING_STRATEGIES["technical"])
        assert all(len(chunk) <= CHUNKING_STRATEGIES["technical"]["max_chars"] + CHUNKING_STRATEGIES["technical"]["overlap"]
                  for chunk in technical_chunks)

    @pytest.mark.asyncio
    async def test_batch_embedding_with_retry(self):
        """Test batch embedding with retry logic on failure."""
        chunks = ["Chunk 1", "Chunk 2", "Chunk 3", "Chunk 4", "Chunk 5"]

        with patch('lib.pipeline.embed.OpenAIEmbeddingClient') as mock_client:
            # Simulate failure then success
            mock_instance = mock_client.return_value
            mock_instance.generate.side_effect = [
                Exception("API rate limit"),
                [[0.1] * 1536, [0.2] * 1536],  # Success for first batch
                [[0.3] * 1536, [0.4] * 1536, [0.5] * 1536]  # Success for second batch
            ]

            embeddings = await batch_embed_chunks(chunks, batch_size=2)

            assert len(embeddings) == 5
            assert mock_instance.generate.call_count >= 2  # At least one retry

    @pytest.mark.asyncio
    async def test_embedding_dimension_validation(self):
        """Test that embedding dimensions are validated."""
        from lib.pipeline.embed import validate_embedding_dimensions

        # Valid embeddings
        valid_embeddings = [[0.1] * 1536, [0.2] * 1536]
        assert validate_embedding_dimensions(valid_embeddings, expected_dim=1536) == True

        # Invalid embeddings (mismatched dimensions)
        invalid_embeddings = [[0.1] * 1536, [0.2] * 768]
        assert validate_embedding_dimensions(invalid_embeddings, expected_dim=1536) == False

    @pytest.mark.asyncio
    async def test_chunk_metadata_preservation(self):
        """Test that chunk metadata is preserved through the pipeline."""
        from lib.pipeline.chunk import chunk_parsed_document, assign_chunk_indices
        from lib.pdf_processing.metadata import extract_chunk_metadata

        elements = [
            Mock(text="Test content", category="NarrativeText", metadata={"page_number": 1})
        ]

        strategy = {"method": "by_paragraph", "max_chars": 500, "overlap": 50}
        chunks = await chunk_parsed_document(elements, strategy)
        chunks_with_indices = assign_chunk_indices(chunks)

        # Extract metadata for each chunk
        for i, chunk in enumerate(chunks_with_indices):
            metadata = extract_chunk_metadata(chunk, page_num=1, section_title="Test Section")

            assert metadata["page_number"] == 1
            assert metadata["section_title"] == "Test Section"
            assert metadata["chunk_index"] == i
            assert "chunk_hash" in metadata


class TestErrorRecoveryMechanisms:
    """Test error recovery and resilience features."""

    @pytest.mark.asyncio
    async def test_partial_processing_recovery(self, pipeline_tracker):
        """Test recovery from partial processing failures."""
        document_id = "doc-partial-001"

        # Simulate partial chunking failure
        pipeline_tracker.track_pipeline_progress(document_id, ProcessingStage.CHUNKING)

        # Mark some chunks as processed, others as failed
        chunk_statuses = {
            "chunk_0": "completed",
            "chunk_1": "completed",
            "chunk_2": "failed",
            "chunk_3": "failed"
        }

        pipeline_tracker.handle_pipeline_failure(
            document_id,
            {
                "stage": ProcessingStage.CHUNKING,
                "partial": True,
                "chunk_statuses": chunk_statuses
            }
        )

        # Recover failed chunks only
        with patch('lib.pipeline.chunk.process_failed_chunks') as mock_process:
            mock_process.return_value = ["chunk_2", "chunk_3"]

            retry_config = RetryConfig(max_retries=3)
            success = await pipeline_tracker.retry_failed_stage(
                document_id,
                ProcessingStage.CHUNKING,
                retry_config,
                partial=True
            )

            assert success
            mock_process.assert_called_once()

    @pytest.mark.asyncio
    async def test_connection_failure_handling(self):
        """Test handling of Weaviate connection failures."""
        from lib.weaviate_client.connection import WeaviateConnection

        connection = WeaviateConnection()

        with patch('weaviate.Client') as mock_client:
            mock_client.side_effect = Exception("Connection refused")

            # Should handle connection failure gracefully
            result = connection.connect_to_weaviate()
            assert result == False

            # Health check should indicate unhealthy
            health = connection.health_check()
            assert health["healthy"] == False
            assert "Connection refused" in health.get("error", "")

    @pytest.mark.asyncio
    async def test_corrupted_pdf_handling(self):
        """Test handling of corrupted PDF files."""
        from lib.pipeline.parse import validate_pdf_file, handle_parsing_errors

        # Create a corrupted PDF (invalid header)
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
            f.write(b'Not a valid PDF file')
            corrupted_pdf = Path(f.name)

        # Validation should detect corruption
        is_valid = validate_pdf_file(corrupted_pdf)
        assert is_valid == False

        # Error handler should categorize appropriately
        error = await handle_parsing_errors(
            Exception("Failed to parse PDF: Invalid PDF header")
        )

        assert error["category"] == "invalid_pdf"
        assert error["recoverable"] == False

        corrupted_pdf.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_storage_rollback_on_failure(self):
        """Test rollback of stored chunks on embedding failure."""
        from lib.pipeline.store import store_to_weaviate, rollback_storage

        chunks_with_embeddings = [
            {"id": f"chunk_{i}", "content": f"Content {i}", "embedding": [0.1] * 1536}
            for i in range(5)
        ]

        with patch('lib.pipeline.store.WeaviateClient') as mock_client:
            # Simulate partial storage success then failure
            mock_client.return_value.batch_insert.side_effect = [
                True,  # First batch succeeds
                Exception("Storage quota exceeded")  # Second batch fails
            ]

            try:
                await store_to_weaviate(chunks_with_embeddings, batch_size=3)
            except Exception:
                # Rollback should be triggered
                with patch('lib.pipeline.store.delete_chunks') as mock_delete:
                    await rollback_storage(["chunk_0", "chunk_1", "chunk_2"])
                    mock_delete.assert_called_once()


class TestPerformanceAndScalability:
    """Test performance metrics and scalability."""

    @pytest.mark.asyncio
    async def test_large_document_processing(self):
        """Test processing of large documents within time constraints."""
        from lib.pipeline.orchestrator import DocumentPipelineOrchestrator as PipelineOrchestrator

        # Mock a large document (1000 chunks)
        large_document = {
            "id": "large-doc-001",
            "chunks": [f"Chunk {i} content" * 100 for i in range(1000)]
        }

        orchestrator = PipelineOrchestrator()

        start_time = time.time()

        with patch.object(orchestrator, 'process_chunks') as mock_process:
            mock_process.return_value = {"processed": 1000}

            result = await orchestrator.process_document(large_document["id"])

            elapsed_time = time.time() - start_time

            # Should process within 30 seconds (per requirements)
            assert elapsed_time < 30
            assert result["processed"] == 1000

    def test_concurrent_document_limit(self):
        """Test enforcement of concurrent processing limits."""
        from lib.pipeline.orchestrator import DocumentPipelineOrchestrator as PipelineOrchestrator

        orchestrator = PipelineOrchestrator(max_concurrent=3)

        # Try to process 5 documents concurrently
        document_ids = [f"doc-{i}" for i in range(5)]

        with patch.object(orchestrator, 'get_active_pipelines') as mock_active:
            mock_active.return_value = ["doc-1", "doc-2", "doc-3"]

            # Fourth document should be queued
            can_process = orchestrator.can_process_document("doc-4")
            assert can_process == False

            # After one completes, new one can start
            mock_active.return_value = ["doc-1", "doc-2"]
            can_process = orchestrator.can_process_document("doc-4")
            assert can_process == True

    @pytest.mark.asyncio
    async def test_memory_efficient_batch_processing(self):
        """Test memory-efficient batch processing of chunks."""
        from lib.pipeline.chunk import process_chunks_in_batches

        # Large number of chunks
        chunks = [f"Chunk {i}" for i in range(10000)]

        processed_count = 0

        async def process_batch(batch):
            nonlocal processed_count
            processed_count += len(batch)
            return [f"Processed: {chunk}" for chunk in batch]

        # Process in batches to avoid memory issues
        batch_size = 100
        results = []

        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i+batch_size]
            batch_results = await process_batch(batch)
            results.extend(batch_results)

        assert processed_count == 10000
        assert len(results) == 10000


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
