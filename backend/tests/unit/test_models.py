"""Unit tests for Pydantic models."""

import pytest
from datetime import datetime, timedelta
from pydantic import ValidationError

from src.models.document import (
    PDFDocument, ProcessingStatus, EmbeddingStatus, DocumentMetadata
)
from src.models.chunk import (
    DocumentChunk,
    ElementType,
    ChunkBoundingBox,
    ChunkDocItemProvenance,
    ChunkMetadata,
)
from src.models.strategy import (
    ChunkingStrategy, StrategyName, ChunkingMethod
)
from src.models.pipeline import (
    ProcessingStage, PipelineStatus, StageResult, ProcessingError
)
from src.models.api_schemas import (
    DocumentFilter, PaginationParams, OperationResult,
    EmbeddingConfiguration, WeaviateSettings
)


class TestPDFDocument:
    """Test PDFDocument model validation."""

    def test_create_valid_document(self):
        """Test creating a valid PDFDocument."""
        metadata = DocumentMetadata(
            page_count=10,
            author="Test Author",
            title="Test Document",
            checksum="abc123",
            document_type="research",
            last_processed_stage="completed"
        )

        doc = PDFDocument(
            id="123e4567-e89b-12d3-a456-426614174000",
            filename="test.pdf",
            file_size=1024000,
            creation_date=datetime.now(),
            last_accessed_date=datetime.now(),
            processing_status=ProcessingStatus.COMPLETED,
            embedding_status=EmbeddingStatus.COMPLETED,
            chunk_count=50,
            vector_count=50,
            metadata=metadata
        )

        assert doc.filename == "test.pdf"
        assert doc.file_size == 1024000
        assert doc.chunk_count == 50
        assert doc.vector_count == 50

    def test_filename_validation(self):
        """Test filename validation rules."""
        metadata = DocumentMetadata(
            page_count=10,
            checksum="abc123",
            document_type="research",
            last_processed_stage="completed"
        )

        # Empty filename should fail
        with pytest.raises(ValidationError, match="at least 1 character"):
            PDFDocument(
                id="123e4567-e89b-12d3-a456-426614174000",
                filename="",
                file_size=1024,
                creation_date=datetime.now(),
                last_accessed_date=datetime.now(),
                metadata=metadata
            )

        # Filename too long should fail
        with pytest.raises(ValidationError, match="at most 255 characters"):
            PDFDocument(
                id="123e4567-e89b-12d3-a456-426614174000",
                filename="a" * 256,
                file_size=1024,
                creation_date=datetime.now(),
                last_accessed_date=datetime.now(),
                metadata=metadata
            )

    def test_file_size_validation(self):
        """Test file size must be positive."""
        metadata = DocumentMetadata(
            page_count=10,
            checksum="abc123",
            document_type="research",
            last_processed_stage="completed"
        )

        with pytest.raises(ValidationError):
            PDFDocument(
                id="123e4567-e89b-12d3-a456-426614174000",
                filename="test.pdf",
                file_size=-1,
                creation_date=datetime.now(),
                last_accessed_date=datetime.now(),
                metadata=metadata
            )

    def test_vector_count_validation(self):
        """Test vector count must be non-negative."""
        metadata = DocumentMetadata(
            page_count=10,
            checksum="abc123",
            document_type="research",
            last_processed_stage="completed"
        )

        with pytest.raises(ValidationError):
            PDFDocument(
                id="123e4567-e89b-12d3-a456-426614174000",
                filename="test.pdf",
                file_size=1024,
                creation_date=datetime.now(),
                last_accessed_date=datetime.now(),
                vector_count=-1,
                metadata=metadata
            )

    def test_to_dict_from_dict(self):
        """Test serialization and deserialization."""
        metadata = DocumentMetadata(
            page_count=10,
            checksum="abc123",
            document_type="research",
            last_processed_stage="completed"
        )

        doc = PDFDocument(
            id="123e4567-e89b-12d3-a456-426614174000",
            filename="test.pdf",
            file_size=1024,
            creation_date=datetime.now(),
            last_accessed_date=datetime.now(),
            metadata=metadata
        )

        # Convert to dict
        doc_dict = doc.to_dict()
        assert isinstance(doc_dict['creation_date'], str)

        # Convert back to model
        doc2 = PDFDocument.from_dict(doc_dict)
        assert doc2.filename == doc.filename
        assert doc2.file_size == doc.file_size


class TestDocumentChunk:
    """Test DocumentChunk model validation."""

    def test_create_valid_chunk(self):
        """Test creating a valid DocumentChunk."""
        metadata = ChunkMetadata(
            character_count=500,
            word_count=100,
            has_table=False,
            has_image=False
        )

        chunk = DocumentChunk(
            id="chunk-001",
            document_id="doc-001",
            chunk_index=0,
            content="This is test content",
            element_type=ElementType.NARRATIVE_TEXT,
            page_number=1,
            metadata=metadata
        )

        assert chunk.content == "This is test content"
        assert chunk.element_type == ElementType.NARRATIVE_TEXT.value
        assert chunk.page_number == 1

    def test_content_validation(self):
        """Test content cannot be empty."""
        metadata = ChunkMetadata(
            character_count=0,
            word_count=0
        )

        with pytest.raises(ValueError, match="Content must not be empty"):
            DocumentChunk(
                id="chunk-001",
                document_id="doc-001",
                chunk_index=0,
                content="   ",
                element_type=ElementType.NARRATIVE_TEXT,
                page_number=1,
                metadata=metadata
            )

    def test_chunk_index_validation(self):
        """Test chunk index must be non-negative."""
        metadata = ChunkMetadata(
            character_count=100,
            word_count=20
        )

        with pytest.raises(ValidationError):
            DocumentChunk(
                id="chunk-001",
                document_id="doc-001",
                chunk_index=-1,
                content="Test content",
                element_type=ElementType.NARRATIVE_TEXT,
                page_number=1,
                metadata=metadata
            )

    def test_page_number_validation(self):
        """Test page number must be positive."""
        metadata = ChunkMetadata(
            character_count=100,
            word_count=20
        )

        with pytest.raises(ValidationError):
            DocumentChunk(
                id="chunk-001",
                document_id="doc-001",
                chunk_index=0,
                content="Test content",
                element_type=ElementType.NARRATIVE_TEXT,
                page_number=0,
                metadata=metadata
            )

    def test_doc_item_provenance_validation(self):
        """Test provenance bounding box coordinate validation."""
        # right must be greater than left
        with pytest.raises(ValidationError, match=r"right.*must be greater than left"):
            ChunkBoundingBox(left=100, top=50, right=50, bottom=150)

        # For BOTTOMLEFT coordinates (default), bottom must be less than or equal to top
        with pytest.raises(ValidationError, match=r"bottom.*must be less than or equal to top"):
            ChunkBoundingBox(left=50, top=100, right=200, bottom=150)

        # Valid bounding box (bottom < top for BOTTOMLEFT)
        bbox = ChunkBoundingBox(left=10, top=60, right=30, bottom=20)
        provenance = ChunkDocItemProvenance(
            element_id="element-1",
            page=1,
            doc_item_label="text",
            bbox=bbox,
        )
        assert provenance.page == 1
        assert provenance.bbox.right > provenance.bbox.left


class TestChunkingStrategy:
    """Test ChunkingStrategy model validation."""

    def test_create_valid_strategy(self):
        """Test creating a valid ChunkingStrategy."""
        strategy = ChunkingStrategy(
            strategy_name=StrategyName.RESEARCH,
            chunking_method=ChunkingMethod.BY_TITLE,
            max_characters=1500,
            overlap_characters=200,
            include_metadata=True,
            exclude_element_types=[]
        )

        assert strategy.strategy_name == StrategyName.RESEARCH.value
        assert strategy.max_characters == 1500
        assert strategy.overlap_characters == 200

    def test_max_characters_validation(self):
        """Test max_characters range validation."""
        # Too small
        with pytest.raises(ValidationError):
            ChunkingStrategy(
                strategy_name=StrategyName.RESEARCH,
                chunking_method=ChunkingMethod.BY_TITLE,
                max_characters=499,
                overlap_characters=100
            )

        # Too large
        with pytest.raises(ValidationError):
            ChunkingStrategy(
                strategy_name=StrategyName.RESEARCH,
                chunking_method=ChunkingMethod.BY_TITLE,
                max_characters=5001,
                overlap_characters=100
            )

    def test_overlap_validation(self):
        """Test overlap must be less than max_characters/2."""
        with pytest.raises(ValueError, match="must be less than max_characters/2"):
            ChunkingStrategy(
                strategy_name=StrategyName.RESEARCH,
                chunking_method=ChunkingMethod.BY_TITLE,
                max_characters=1000,
                overlap_characters=500
            )

    def test_get_default_strategies(self):
        """Test getting predefined strategies."""
        strategies = ChunkingStrategy.get_default_strategies()

        assert len(strategies) == 1
        assert StrategyName.RESEARCH in strategies

        research = strategies[StrategyName.RESEARCH]
        assert research.max_characters == 1500
        assert research.overlap_characters == 200
        assert research.chunking_method == ChunkingMethod.BY_TITLE.value


class TestPipelineModels:
    """Test pipeline state models."""

    def test_pipeline_status(self):
        """Test PipelineStatus model."""
        status = PipelineStatus(
            document_id="doc-001",
            current_stage=ProcessingStage.CHUNKING,
            started_at=datetime.now(),
            updated_at=datetime.now(),
            progress_percentage=50
        )

        assert status.current_stage == ProcessingStage.CHUNKING.value
        assert status.progress_percentage == 50
        assert status.error_count == 0

        # Test serialization
        status_dict = status.to_dict()
        assert isinstance(status_dict['started_at'], str)

        # Test deserialization
        status2 = PipelineStatus.from_dict(status_dict)
        assert status2.document_id == status.document_id

    def test_stage_result(self):
        """Test StageResult model."""
        started = datetime.now()
        completed = started + timedelta(seconds=5)

        result = StageResult(
            stage=ProcessingStage.PARSING,
            success=True,
            started_at=started,
            completed_at=completed,
            duration_seconds=5.0,
            message="Parsing completed successfully"
        )

        assert result.success is True
        assert result.duration_seconds == 5.0

        # Duration must be positive
        with pytest.raises(ValidationError):
            StageResult(
                stage=ProcessingStage.PARSING,
                success=True,
                started_at=started,
                completed_at=completed,
                duration_seconds=-1
            )

    def test_processing_error(self):
        """Test ProcessingError model."""
        error = ProcessingError(
            stage=ProcessingStage.EMBEDDING,
            error_code="EMB_001",
            error_message="Embedding service unavailable",
            timestamp=datetime.now(),
            document_id="doc-001",
            retry_count=2,
            is_retryable=True
        )

        assert error.error_code == "EMB_001"
        assert error.retry_count == 2
        assert error.is_retryable is True


class TestAPISchemas:
    """Test API request/response schemas."""

    def test_document_filter(self):
        """Test DocumentFilter validation."""
        filter = DocumentFilter(
            search_term="research",
            embedding_status=[EmbeddingStatus.COMPLETED],
            date_from=datetime.now() - timedelta(days=7),
            date_to=datetime.now(),
            min_vector_count=10,
            max_vector_count=100
        )

        assert filter.is_date_range_valid
        assert filter.is_vector_range_valid

        # Invalid date range
        filter.date_from = datetime.now()
        filter.date_to = datetime.now() - timedelta(days=1)
        assert not filter.is_date_range_valid

        # Invalid vector range
        filter.min_vector_count = 100
        filter.max_vector_count = 10
        assert not filter.is_vector_range_valid

    def test_pagination_params(self):
        """Test PaginationParams validation."""
        # Valid pagination
        params = PaginationParams(page=1, page_size=20)
        assert params.page == 1
        assert params.page_size == 20

        # Page must be positive
        with pytest.raises(ValidationError):
            PaginationParams(page=0)

        # Page size must be within range
        with pytest.raises(ValidationError):
            PaginationParams(page_size=5)

        with pytest.raises(ValidationError):
            PaginationParams(page_size=101)

    def test_operation_result(self):
        """Test OperationResult helper methods."""
        # Success result
        success = OperationResult.success_result(
            message="Document deleted",
            document_id="doc-001"
        )
        assert success.success is True
        assert success.document_id == "doc-001"

        # Error result
        error = OperationResult.error_result(
            message="Failed to delete document",
            error_code="DEL_001",
            details="Document is currently being processed"
        )
        assert error.success is False
        assert error.error['code'] == "DEL_001"

    def test_embedding_configuration(self):
        """Test EmbeddingConfiguration validation."""
        config = EmbeddingConfiguration(
            model_provider="openai",
            model_name="text-embedding-3-small",
            dimensions=1536,
            batch_size=50
        )

        assert config.model_provider == "openai"
        assert config.dimensions == 1536

        # Dimensions must be positive
        with pytest.raises(ValidationError):
            EmbeddingConfiguration(
                model_provider="openai",
                model_name="test",
                dimensions=0,
                batch_size=50
            )

        # Batch size must be within range
        with pytest.raises(ValidationError):
            EmbeddingConfiguration(
                model_provider="openai",
                model_name="test",
                dimensions=1536,
                batch_size=101
            )

    def test_weaviate_settings(self):
        """Test WeaviateSettings validation."""
        settings = WeaviateSettings(
            collection_name="PDFDocuments",
            schema_version="1.0.0",
            replication_factor=3,
            consistency="quorum",
            vector_index_type="hnsw"
        )

        assert settings.collection_name == "PDFDocuments"
        assert settings.replication_factor == 3

        # Replication factor must be positive
        with pytest.raises(ValidationError):
            WeaviateSettings(
                collection_name="PDFDocuments",
                schema_version="1.0.0",
                replication_factor=0
            )
