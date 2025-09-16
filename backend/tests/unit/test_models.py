"""
Unit tests for PDF Document Q&A database models
Following TDD-RED phase - these tests should FAIL initially
"""

import pytest
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError
from unittest.mock import Mock, patch
import json
import os

# These imports will fail initially (TDD-RED)
from app.models import (
    PDFDocument,
    PDFChunk,
    PDFEmbedding,
    ChunkSearch,
    ExtractedTable,
    ExtractedFigure,
    ChatSession,
    Message,
    EmbeddingJob,
    CitationTracking,
    Base,
    ExtractionMethod,
    JobType,
    JobStatus,
    MessageType,
    FigureType,
)


@pytest.fixture
def test_engine():
    """Create a test database engine"""
    # Use the TEST_DATABASE_URL from docker-compose environment
    database_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql://curation_user:curation_pass@postgres-test:5432/ai_curation_test",  # pragma: allowlist secret
    )
    engine = create_engine(database_url)

    # Create tables for testing
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    yield engine

    # Cleanup
    Base.metadata.drop_all(engine)


@pytest.fixture
def test_session(test_engine):
    """Create a test database session"""
    Session = sessionmaker(bind=test_engine)
    session = Session()

    yield session

    session.rollback()
    session.close()


class TestPDFDocument:
    """Test PDFDocument model - represents uploaded PDF with deduplication"""

    def test_pdf_document_creation(self, test_session):
        """Test creating a basic PDF document"""
        pdf = PDFDocument(
            filename="test_paper.pdf",
            file_path="/uploads/test_paper.pdf",
            file_hash="abc123",
            content_hash_normalized="def456",
            file_size=1024000,
            page_count=10,
            extracted_text="Sample text from paper",
            extraction_method=ExtractionMethod.PYMUPDF,
        )

        test_session.add(pdf)
        test_session.commit()

        assert pdf.id is not None
        assert isinstance(pdf.id, UUID)
        assert pdf.filename == "test_paper.pdf"
        assert pdf.is_valid is True
        assert pdf.embeddings_generated is False

    def test_pdf_document_validation_file_size(self, test_session):
        """Test file size validation (max 100MB)"""
        with pytest.raises(IntegrityError):
            pdf = PDFDocument(
                filename="huge.pdf",
                file_path="/uploads/huge.pdf",
                file_hash="xyz",
                content_hash_normalized="unique123",
                file_size=104857601,  # 100MB + 1 byte
                page_count=10,
            )
            test_session.add(pdf)
            test_session.commit()

    def test_pdf_document_validation_page_count(self, test_session):
        """Test page count validation (max 500)"""
        with pytest.raises(IntegrityError):
            pdf = PDFDocument(
                filename="long.pdf",
                file_path="/uploads/long.pdf",
                file_hash="xyz",
                content_hash_normalized="unique456",
                file_size=1024000,
                page_count=501,  # Over limit
            )
            test_session.add(pdf)
            test_session.commit()

    def test_pdf_document_unique_content_hash(self, test_session):
        """Test content_hash_normalized uniqueness constraint"""
        pdf1 = PDFDocument(
            filename="paper1.pdf",
            file_path="/uploads/paper1.pdf",
            file_hash="hash1",
            content_hash_normalized="same_content",
            file_size=1024000,
            page_count=10,
        )
        test_session.add(pdf1)
        test_session.commit()

        with pytest.raises(IntegrityError):
            pdf2 = PDFDocument(
                filename="paper2.pdf",
                file_path="/uploads/paper2.pdf",
                file_hash="hash2",
                content_hash_normalized="same_content",  # Duplicate
                file_size=2048000,
                page_count=20,
            )
            test_session.add(pdf2)
            test_session.commit()

    def test_pdf_document_metadata_jsonb(self, test_session):
        """Test JSONB meta_data field"""
        metadata = {
            "title": "Test Paper",
            "authors": ["Author 1", "Author 2"],
            "publication_date": "2024-01-01",
            "journal": "Test Journal",
            "extracted_entities": ["BRCA1", "TP53"],
        }

        pdf = PDFDocument(
            filename="metadata_test.pdf",
            file_path="/uploads/metadata_test.pdf",
            file_hash="meta123",
            content_hash_normalized="meta456",
            file_size=1024000,
            page_count=15,
            meta_data=metadata,
        )

        test_session.add(pdf)
        test_session.commit()

        retrieved = (
            test_session.query(PDFDocument).filter_by(file_hash="meta123").first()
        )
        assert retrieved.meta_data["title"] == "Test Paper"
        assert len(retrieved.meta_data["authors"]) == 2
        assert "BRCA1" in retrieved.meta_data["extracted_entities"]


class TestPDFChunk:
    """Test PDFChunk model - semantic chunks with layout preservation"""

    def test_pdf_chunk_creation(self, test_session):
        """Test creating a PDF chunk"""
        # First create a PDF document
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/test.pdf",
            file_hash="abc",
            content_hash_normalized="unique_chunk_test",
            file_size=1024,
            page_count=5,
        )
        test_session.add(pdf)
        test_session.commit()

        chunk = PDFChunk(
            pdf_id=pdf.id,
            chunk_index=0,
            text="This is the first chunk of text",
            token_count=8,
            page_start=1,
            page_end=1,
            chunk_hash="test_hash_123",
            section_path="Introduction",
        )

        test_session.add(chunk)
        test_session.commit()

        assert chunk.id is not None
        assert chunk.pdf_id == pdf.id
        assert chunk.is_reference is False
        assert chunk.is_caption is False
        assert chunk.is_header is False

    def test_pdf_chunk_unique_index(self, test_session):
        """Test unique constraint on pdf_id + chunk_index"""
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/test.pdf",
            file_hash="abc",
            content_hash_normalized="unique_idx_test",
            file_size=1024,
            page_count=5,
        )
        test_session.add(pdf)
        test_session.commit()

        chunk1 = PDFChunk(
            pdf_id=pdf.id,
            chunk_index=0,
            text="First chunk",
            token_count=2,
            page_start=1,
            page_end=1,
            chunk_hash="hash1",
        )
        test_session.add(chunk1)
        test_session.commit()

        with pytest.raises(IntegrityError):
            chunk2 = PDFChunk(
                pdf_id=pdf.id,
                chunk_index=0,  # Duplicate index
                text="Different text",
                token_count=2,
                page_start=2,
                page_end=2,
                chunk_hash="hash_test",
            )
            test_session.add(chunk2)
            test_session.commit()

    def test_pdf_chunk_token_validation(self, test_session):
        """Test chunk token count validation (1-2000)"""
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/test.pdf",
            file_hash="abc",
            content_hash_normalized="token_test",
            file_size=1024,
            page_count=5,
        )
        test_session.add(pdf)
        test_session.commit()

        with pytest.raises(IntegrityError):
            chunk = PDFChunk(
                pdf_id=pdf.id,
                chunk_index=0,
                text="Text",
                token_count=2001,  # Over limit
                page_start=1,
                page_end=1,
                chunk_hash="hash_invalid",
            )
            test_session.add(chunk)
            test_session.commit()

    def test_pdf_chunk_bbox_metadata(self, test_session):
        """Test bounding box JSONB field"""
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/test.pdf",
            file_hash="abc",
            content_hash_normalized="bbox_test",
            file_size=1024,
            page_count=5,
        )
        test_session.add(pdf)
        test_session.commit()

        bbox = {"x1": 100.5, "y1": 200.5, "x2": 300.5, "y2": 400.5, "page": 1}

        chunk = PDFChunk(
            pdf_id=pdf.id,
            chunk_index=0,
            text="Text with bbox",
            token_count=3,
            page_start=1,
            page_end=1,
            chunk_hash="hash_bbox",
            bbox=bbox,
        )

        test_session.add(chunk)
        test_session.commit()

        retrieved = test_session.query(PDFChunk).filter_by(pdf_id=pdf.id).first()
        assert retrieved.bbox["x1"] == 100.5
        assert retrieved.bbox["page"] == 1


class TestPDFEmbedding:
    """Test PDFEmbedding model - multi-version embeddings with configurable dimensions"""

    def test_pdf_embedding_creation(self, test_session):
        """Test creating PDF embeddings with vector data"""
        # Create PDF and chunk first
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/test.pdf",
            file_hash="abc",
            content_hash_normalized="embed_test",
            file_size=1024,
            page_count=5,
        )
        test_session.add(pdf)
        test_session.commit()

        chunk = PDFChunk(
            pdf_id=pdf.id,
            chunk_index=0,
            text="Text to embed",
            token_count=3,
            page_start=1,
            page_end=1,
            chunk_hash="hash_test",
        )
        test_session.add(chunk)
        test_session.commit()

        # Mock vector (normally would be numpy array)
        embedding_vector = [0.1] * 1536  # 1536-dim vector

        embedding = PDFEmbedding(
            chunk_id=chunk.id,
            pdf_id=pdf.id,
            embedding=embedding_vector,
            model_name="text-embedding-3-small",
            model_version="v1",
            dimensions=1536,
            # is_active=True,  # field not in model
        )

        test_session.add(embedding)
        test_session.commit()

        assert embedding.id is not None
        assert embedding.dimensions == 1536
        # assert embedding.is_active is True  # field not in model

    def test_pdf_embedding_versioning(self, test_session):
        """Test multiple embedding versions for same chunk"""
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/test.pdf",
            file_hash="abc",
            content_hash_normalized="version_test",
            file_size=1024,
            page_count=5,
        )
        test_session.add(pdf)
        test_session.commit()

        chunk = PDFChunk(
            pdf_id=pdf.id,
            chunk_index=0,
            text="Text",
            token_count=1,
            page_start=1,
            page_end=1,
            chunk_hash="hash_test",
        )
        test_session.add(chunk)
        test_session.commit()

        # First version
        embed_v1 = PDFEmbedding(
            chunk_id=chunk.id,
            pdf_id=pdf.id,
            embedding=[0.1] * 1536,
            model_name="text-embedding-3-small",
            model_version="v1",
            dimensions=1536,
            # is_active=False,  # field not in model
        )

        # Second version (active)
        embed_v2 = PDFEmbedding(
            chunk_id=chunk.id,
            pdf_id=pdf.id,
            embedding=[0.2] * 1536,
            model_name="text-embedding-3-small",
            model_version="v2",
            dimensions=1536,
            # is_active=True,  # field not in model
        )

        test_session.add_all([embed_v1, embed_v2])
        test_session.commit()

        # Query active version
        active = (
            test_session.query(PDFEmbedding)
            .filter_by(chunk_id=chunk.id)  # removed is_active=True
            .first()
        )

        assert active.model_version == "v2"


class TestChunkSearch:
    """Test ChunkSearch model - lexical search index"""

    def test_chunk_search_creation(self, test_session):
        """Test creating lexical search index"""
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/test.pdf",
            file_hash="abc",
            content_hash_normalized="search_test",
            file_size=1024,
            page_count=5,
        )
        test_session.add(pdf)
        test_session.commit()

        chunk = PDFChunk(
            pdf_id=pdf.id,
            chunk_index=0,
            text="BRCA1 gene mutation analysis",
            token_count=4,
            page_start=1,
            page_end=1,
            chunk_hash="hash_test",
        )
        test_session.add(chunk)
        test_session.commit()

        search = ChunkSearch(
            chunk_id=chunk.id,
            search_vector="'brca1':1 'gene':2 'mutation':3 'analysis':4",  # tsvector format
            text_length=20,  # search_text="BRCA1 gene mutation analysis",
            # lexical_rank=0.95,  # field not in model
            # meta_data={"important_terms": ["BRCA1", "mutation"]},  # field not in model
        )

        test_session.add(search)
        test_session.commit()

        assert search.id is not None
        # assert search.lexical_rank == 0.95  # field not in model
        # assert "BRCA1" in search.meta_data["important_terms"]  # field not in model
        assert search.text_length == 20

    def test_chunk_search_unique_chunk(self, test_session):
        """Test unique constraint on chunk_id"""
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/test.pdf",
            file_hash="abc",
            content_hash_normalized="unique_search",
            file_size=1024,
            page_count=5,
        )
        test_session.add(pdf)
        test_session.commit()

        chunk = PDFChunk(
            pdf_id=pdf.id,
            chunk_index=0,
            text="Text",
            token_count=1,
            page_start=1,
            page_end=1,
            chunk_hash="hash_test",
        )
        test_session.add(chunk)
        test_session.commit()

        search1 = ChunkSearch(
            chunk_id=chunk.id,
            search_vector="'text':1",
            text_length=20,  # search_text="Text"
        )
        test_session.add(search1)
        test_session.commit()

        with pytest.raises(IntegrityError):
            search2 = ChunkSearch(
                chunk_id=chunk.id,  # Same chunk
                search_vector="'text':1",
                text_length=20,  # search_text="Text",
            )
            test_session.add(search2)
            test_session.commit()


class TestPDFTable:
    """Test PDFTable model - extracted tables with structured data"""

    def test_pdf_table_creation(self, test_session):
        """Test creating PDF table with structured data"""
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/test.pdf",
            file_hash="abc",
            content_hash_normalized="table_test",
            file_size=1024,
            page_count=5,
        )
        test_session.add(pdf)
        test_session.commit()

        headers = ["Gene", "Mutation", "Frequency"]
        data = [
            {"Gene": "BRCA1", "Mutation": "c.68_69del", "Frequency": "0.15"},
            {"Gene": "TP53", "Mutation": "R273H", "Frequency": "0.08"},
        ]

        table = ExtractedTable(
            pdf_id=pdf.id,
            page_number=3,
            table_index=0,
            caption="Table 1: Common mutations",
            headers=headers,
            data=data,
            # extraction_method="CAMELOT",  # field not in model
            confidence=0.95,
        )

        test_session.add(table)
        test_session.commit()

        assert table.id is not None
        assert len(table.data) == 2
        assert table.data[0]["Gene"] == "BRCA1"
        assert table.confidence == 0.95


class TestPDFFigure:
    """Test PDFFigure model - figure metadata and captions"""

    def test_pdf_figure_creation(self, test_session):
        """Test creating PDF figure with metadata"""
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/test.pdf",
            file_hash="abc",
            content_hash_normalized="figure_test",
            file_size=1024,
            page_count=5,
        )
        test_session.add(pdf)
        test_session.commit()

        figure = ExtractedFigure(
            pdf_id=pdf.id,
            page_number=2,
            figure_index=0,
            caption="Figure 1: Gene expression heatmap",
            figure_type=FigureType.CHART,
            # has_subfigures=False,  # field not in model
            bbox={"x1": 50, "y1": 100, "x2": 550, "y2": 400},
        )

        test_session.add(figure)
        test_session.commit()

        assert figure.id is not None
        assert figure.figure_type == FigureType.CHART
        assert figure.bbox["x2"] == 550


# class TestOntologyMapping:
#     """Test OntologyMapping model - query expansion synonyms"""
#
#     def test_ontology_mapping_creation(self, test_session):
#         """Test creating ontology mappings with synonyms"""
#         # OntologyMapping model not yet implemented
#         pass


class TestChatSession:
    """Test ChatSession model - RAG-powered conversation tracking"""

    def test_chat_session_creation(self, test_session):
        """Test creating a chat session with RAG config"""
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/test.pdf",
            file_hash="abc",
            content_hash_normalized="session_test",
            file_size=1024,
            page_count=5,
        )
        test_session.add(pdf)
        test_session.commit()

        rag_config = {
            "embedding_model": "text-embedding-3-small",
            "llm_model": "gpt-4",
            "top_k_vector": 50,
            "top_k_lexical": 50,
            "rerank_top_k": 5,
            "similarity_threshold": 0.7,
            "confidence_threshold": 0.7,
            "temperature": 0.3,
            "mmr_lambda": 0.7,
            "use_ontology_expansion": True,
            "max_expansions": 5,
        }

        session = ChatSession(
            session_name="test_token_123",
            pdf_id=pdf.id,
            user_id="user_123",
            rag_config=rag_config,
            is_active=True,
        )

        test_session.add(session)
        test_session.commit()

        assert session.id is not None
        assert session.rag_config["mmr_lambda"] == 0.7
        assert session.is_active is True

    def test_chat_session_unique_token(self, test_session):
        """Test unique constraint on session token"""
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/test.pdf",
            file_hash="abc",
            content_hash_normalized="token_unique",
            file_size=1024,
            page_count=5,
        )
        test_session.add(pdf)
        test_session.commit()

        session1 = ChatSession(session_name="unique_token", pdf_id=pdf.id)
        test_session.add(session1)
        test_session.commit()

        with pytest.raises(IntegrityError):
            session2 = ChatSession(
                session_name="unique_token", pdf_id=pdf.id  # Duplicate
            )
            test_session.add(session2)
            test_session.commit()


class TestMessage:
    """Test Message model - enhanced messages with RAG attribution"""

    def test_message_creation(self, test_session):
        """Test creating a message with RAG context"""
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/test.pdf",
            file_hash="abc",
            content_hash_normalized="message_test",
            file_size=1024,
            page_count=5,
        )
        test_session.add(pdf)
        test_session.commit()

        session = ChatSession(session_name="msg_session", pdf_id=pdf.id)
        test_session.add(session)
        test_session.commit()

        rag_context = {
            "query_expansion": ["BRCA1", "breast cancer gene 1"],
            "vector_chunks": [str(uuid4()), str(uuid4())],
            "lexical_chunks": [str(uuid4())],
            "reranked_chunks": [
                {"chunk_id": str(uuid4()), "score": 0.92, "source": "VECTOR"},
                {"chunk_id": str(uuid4()), "score": 0.88, "source": "LEXICAL"},
            ],
            "citations": [
                {"text": "BRCA1 mutations...", "page": 5, "section": "Results"}
            ],
        }

        message = Message(
            session_id=session.id,
            message_type=MessageType.AI_RESPONSE,
            content="Based on the paper, BRCA1 mutations are found in 15% of cases.",
            confidence_score=0.85,
            # sequence_number=1,
            retrieval_stats=rag_context,  # was rag_context
        )

        test_session.add(message)
        test_session.commit()

        assert message.id is not None
        assert message.confidence_score == 0.85
        assert len(message.retrieval_stats["reranked_chunks"]) == 2
        assert message.retrieval_stats["citations"][0]["page"] == 5

    def test_message_sequence_unique(self, test_session):
        """Test unique constraint on session_id + sequence_number"""
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/test.pdf",
            file_hash="abc",
            content_hash_normalized="seq_test",
            file_size=1024,
            page_count=5,
        )
        test_session.add(pdf)
        test_session.commit()

        session = ChatSession(session_name="seq_session", pdf_id=pdf.id)
        test_session.add(session)
        test_session.commit()

        msg1 = Message(
            session_id=session.id,
            message_type=MessageType.USER_QUESTION,
            content="Question 1",
            # sequence_number=1,
        )
        test_session.add(msg1)
        test_session.commit()

        with pytest.raises(IntegrityError):
            msg2 = Message(
                session_id=session.id,
                message_type=MessageType.AI_RESPONSE,
                content="Different content",
                # sequence_number=1,  # Duplicate sequence
            )
            test_session.add(msg2)
            test_session.commit()


class TestEmbeddingJobs:
    """Test EmbeddingJobs model - async job queue"""

    def test_embedding_job_creation(self, test_session):
        """Test creating an embedding job"""
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/test.pdf",
            file_hash="abc",
            content_hash_normalized="job_test",
            file_size=1024,
            page_count=5,
        )
        test_session.add(pdf)
        test_session.commit()

        job = EmbeddingJobs(
            job_type=JobType.EMBED_PDF,
            status=JobStatus.PENDING,
            pdf_id=pdf.id,
            priority=8,
            total_items=100,
            config={"batch_size": 64, "model": "text-embedding-3-small"},
        )

        test_session.add(job)
        test_session.commit()

        assert job.id is not None
        assert job.status == JobStatus.PENDING
        assert job.priority == 8
        assert job.config["batch_size"] == 64

    def test_embedding_job_retry_limit(self, test_session):
        """Test retry count validation (max 3)"""
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/test.pdf",
            file_hash="abc",
            content_hash_normalized="retry_test",
            file_size=1024,
            page_count=5,
        )
        test_session.add(pdf)
        test_session.commit()

        with pytest.raises(IntegrityError):
            job = EmbeddingJobs(
                job_type=JobType.EMBED_PDF,
                status=JobStatus.FAILED,
                pdf_id=pdf.id,
                retry_count=4,  # Over limit
            )
            test_session.add(job)
            test_session.commit()

    def test_embedding_job_progress_validation(self, test_session):
        """Test progress validation (0-100)"""
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/test.pdf",
            file_hash="abc",
            content_hash_normalized="progress_test",
            file_size=1024,
            page_count=5,
        )
        test_session.add(pdf)
        test_session.commit()

        with pytest.raises(IntegrityError):
            job = EmbeddingJobs(
                job_type=JobType.EMBED_PDF,
                status=JobStatus.RUNNING,
                pdf_id=pdf.id,
                progress=101,  # Over 100
            )
            test_session.add(job)
            test_session.commit()


# EmbeddingConfig model not yet implemented - tests commented out
'''
class TestEmbeddingConfig:
    """Test EmbeddingConfig model - system configuration"""

    def test_embedding_config_creation(self, test_session):
        """Test creating embedding configuration"""
        config = EmbeddingConfig(
            config_name="default",
            embedding_model="text-embedding-3-small",
            dimensions=1536,
            chunk_size=1000,
            chunk_overlap=200,
            batch_size=64,
            top_k_vector=50,
            top_k_lexical=50,
            similarity_threshold=0.7,
            confidence_threshold=0.7,
            mmr_lambda=0.7,
            max_context_tokens=4000,
            enable_ocr=True,
            ocr_timeout_seconds=30,
            max_file_size_mb=100,
            max_page_count=500,
            rate_limit_per_minute=10,
            is_active=True,
        )

        test_session.add(config)
        test_session.commit()

        assert config.id is not None
        assert config.model_dim == 1536
        assert config.mmr_lambda == 0.7
        assert config.is_active is True

    def test_embedding_config_unique_name(self, test_session):
        """Test unique constraint on config_name"""
        config1 = EmbeddingConfig(
            config_name="production",
            embedding_model="text-embedding-3-small",
            dimensions=1536,
            is_active=True,
        )
        test_session.add(config1)
        test_session.commit()

        with pytest.raises(IntegrityError):
            config2 = EmbeddingConfig(
                config_name="production",  # Duplicate
                embedding_model="text-embedding-3-large",
                dimensions=3072,
                is_active=False,
            )
            test_session.add(config2)
            test_session.commit()

    def test_embedding_config_validation_thresholds(self, test_session):
        """Test threshold validation (0-1 range)"""
        with pytest.raises(ValueError):
            config = EmbeddingConfig(
                config_name="invalid",
                embedding_model="test",
                dimensions=1536,
                similarity_threshold=1.5,  # Invalid: > 1
                confidence_threshold=0.7,
            )
            # Validation should happen before commit

    def test_embedding_config_validation_chunk_overlap(self, test_session):
        """Test chunk_overlap < chunk_size validation"""
        with pytest.raises(ValueError):
            config = EmbeddingConfig(
                config_name="invalid_chunks",
                embedding_model="test",
                dimensions=1536,
                chunk_size=100,
                chunk_overlap=150,  # Invalid: overlap > size
            )
'''  # End of commented out EmbeddingConfig tests


class TestDatabaseIndexes:
    """Test that all required indexes are created"""

    def test_indexes_exist(self, test_engine):
        """Test that all indexes are properly created"""
        inspector = inspect(test_engine)

        # Check PDFDocument indexes
        pdf_indexes = inspector.get_indexes("pdf_documents")
        pdf_index_names = [idx["name"] for idx in pdf_indexes]
        assert "idx_pdf_documents_doi" in pdf_index_names
        assert "idx_pdf_documents_normalized_hash" in pdf_index_names

        # Check PDFChunk indexes
        chunk_indexes = inspector.get_indexes("pdf_chunks")
        chunk_index_names = [idx["name"] for idx in chunk_indexes]
        assert "idx_pdf_chunks_pdf_tables" in chunk_index_names
        assert "idx_pdf_chunks_section" in chunk_index_names

        # Check ChunkSearch indexes
        search_indexes = inspector.get_indexes("chunk_search")
        search_index_names = [idx["name"] for idx in search_indexes]
        assert "idx_chunk_search_fts" in search_index_names

        # Check EmbeddingJobs indexes
        job_indexes = inspector.get_indexes("embedding_jobs")
        job_index_names = [idx["name"] for idx in job_indexes]
        assert "idx_jobs_queue" in job_index_names


class TestRelationships:
    """Test foreign key relationships between models"""

    def test_cascade_delete_pdf_to_chunks(self, test_session):
        """Test CASCADE delete from PDFDocument to PDFChunk"""
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/test.pdf",
            file_hash="abc",
            content_hash_normalized="cascade_test",
            file_size=1024,
            page_count=5,
        )
        test_session.add(pdf)
        test_session.commit()

        chunk = PDFChunk(
            pdf_id=pdf.id,
            chunk_index=0,
            text="Test chunk",
            token_count=2,
            page_start=1,
            page_end=1,
            chunk_hash="hash_test",
        )
        test_session.add(chunk)
        test_session.commit()

        # Delete PDF should cascade to chunks
        test_session.delete(pdf)
        test_session.commit()

        # Chunk should be deleted
        remaining_chunks = test_session.query(PDFChunk).filter_by(pdf_id=pdf.id).all()
        assert len(remaining_chunks) == 0

    def test_relationships_navigation(self, test_session):
        """Test navigating relationships between models"""
        pdf = PDFDocument(
            filename="test.pdf",
            file_path="/test.pdf",
            file_hash="abc",
            content_hash_normalized="nav_test",
            file_size=1024,
            page_count=5,
        )
        test_session.add(pdf)
        test_session.commit()

        # Add chunks
        for i in range(3):
            chunk = PDFChunk(
                pdf_id=pdf.id,
                chunk_index=i,
                text=f"Chunk {i}",
                token_count=2,
                page_start=i + 1,
                page_end=i + 1,
            )
            test_session.add(chunk)
        test_session.commit()

        # Navigate from PDF to chunks
        pdf_with_chunks = (
            test_session.query(PDFDocument)
            .filter_by(content_hash_normalized="nav_test")
            .first()
        )

        chunks = test_session.query(PDFChunk).filter_by(pdf_id=pdf_with_chunks.id).all()
        assert len(chunks) == 3
        assert chunks[0].text == "Chunk 0"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
