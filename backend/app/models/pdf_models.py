"""
SQLAlchemy models for PDF Document Q&A with Enhanced RAG
Following TDD-GREEN phase - implementing models to pass tests
"""

from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional, List, Dict, Any
from uuid import uuid4

from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Boolean,
    Text,
    DateTime,
    ForeignKey,
    Enum,
    JSON,
    CheckConstraint,
    UniqueConstraint,
    Index,
    event,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, validates
from sqlalchemy.sql import func

Base = declarative_base()


# Enums for various fields
class ExtractionMethod(str, PyEnum):
    PYMUPDF = "PYMUPDF"
    PDFMINER = "PDFMINER"
    OCR = "OCR"


class JobType(str, PyEnum):
    EMBED_PDF = "EMBED_PDF"
    REEMBED_PDF = "REEMBED_PDF"
    EXTRACT_TABLES = "EXTRACT_TABLES"


class JobStatus(str, PyEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RETRY = "RETRY"
    FAILED = "FAILED"
    DONE = "DONE"


class MessageType(str, PyEnum):
    USER_QUESTION = "USER_QUESTION"
    AI_RESPONSE = "AI_RESPONSE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    ERROR = "ERROR"


class FigureType(str, PyEnum):
    CHART = "CHART"
    DIAGRAM = "DIAGRAM"
    IMAGE = "IMAGE"
    PLOT = "PLOT"


class PDFDocument(Base):
    """Represents uploaded PDF with comprehensive deduplication and versioning"""

    __tablename__ = "pdf_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_hash = Column(String(32), nullable=False)
    content_hash_normalized = Column(String(32), unique=True, nullable=False)
    page_hashes = Column(JSONB)
    doi = Column(String(255), index=True)
    file_size = Column(Integer, nullable=False)
    page_count = Column(Integer, nullable=False)
    extracted_text = Column(Text)
    extraction_method = Column(Enum(ExtractionMethod))
    is_ocr = Column(Boolean, default=False)
    embeddings_generated = Column(Boolean, default=False)
    embedding_models = Column(JSONB, default=list)
    chunk_count = Column(Integer, default=0)
    table_count = Column(Integer, default=0)
    figure_count = Column(Integer, default=0)
    preproc_version = Column(String(20))
    metadata = Column(JSONB, default=dict)
    upload_timestamp = Column(DateTime(timezone=True), default=func.now())
    last_accessed = Column(DateTime(timezone=True), default=func.now())
    is_valid = Column(Boolean, default=True)
    validation_errors = Column(JSONB, default=dict)
    processing_stats = Column(JSONB, default=dict)

    # Relationships
    chunks = relationship(
        "PDFChunk", back_populates="document", cascade="all, delete-orphan"
    )
    tables = relationship(
        "PDFTable", back_populates="document", cascade="all, delete-orphan"
    )
    figures = relationship(
        "PDFFigure", back_populates="document", cascade="all, delete-orphan"
    )
    sessions = relationship("ChatSession", back_populates="document")
    jobs = relationship("EmbeddingJobs", back_populates="document")

    # Constraints
    __table_args__ = (
        CheckConstraint("file_size <= 104857600", name="check_file_size_max"),
        CheckConstraint("page_count <= 500", name="check_page_count_max"),
        CheckConstraint("filename ILIKE '%.pdf'", name="valid_pdf_extension"),
        Index("idx_pdf_documents_normalized_hash", "content_hash_normalized"),
        Index("idx_pdf_documents_doi", "doi"),
    )


class PDFChunk(Base):
    """Semantic chunk with layout preservation and metadata"""

    __tablename__ = "pdf_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    pdf_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pdf_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=False)
    chunk_tokens = Column(Integer, nullable=False)
    start_page = Column(Integer, nullable=False)
    end_page = Column(Integer, nullable=False)
    heading_text = Column(String(500))
    section_path = Column(String(500))
    is_caption = Column(Boolean, default=False)
    is_table = Column(Boolean, default=False)
    is_figure = Column(Boolean, default=False)
    is_reference = Column(Boolean, default=False)
    bbox = Column(JSONB)
    metadata = Column(JSONB, default=dict)

    # Relationships
    document = relationship("PDFDocument", back_populates="chunks")
    embeddings = relationship(
        "PDFEmbedding", back_populates="chunk", cascade="all, delete-orphan"
    )
    search_index = relationship(
        "ChunkSearch",
        back_populates="chunk",
        uselist=False,
        cascade="all, delete-orphan",
    )
    table_refs = relationship("PDFTable", back_populates="chunk")
    figure_refs = relationship("PDFFigure", back_populates="chunk")

    # Constraints
    __table_args__ = (
        UniqueConstraint("pdf_id", "chunk_index", name="unique_pdf_chunk_index"),
        CheckConstraint(
            "chunk_tokens > 0 AND chunk_tokens <= 2000", name="check_chunk_tokens"
        ),
        CheckConstraint("start_page <= end_page", name="check_page_order"),
        Index(
            "idx_pdf_chunks_pdf_tables", "pdf_id", postgresql_where="is_table = TRUE"
        ),
        Index("idx_pdf_chunks_section", "section_path"),
    )


class PDFEmbedding(Base):
    """Multi-version embeddings with configurable dimensions"""

    __tablename__ = "pdf_embeddings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    chunk_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pdf_chunks.id", ondelete="CASCADE"),
        nullable=False,
    )
    pdf_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pdf_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    embedding = Column(JSON, nullable=False)  # Will be vector type in PostgreSQL
    model_name = Column(String(100), nullable=False)
    model_version = Column(String(20), nullable=False)
    model_dim = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=func.now())
    metadata = Column(JSONB, default=dict)

    # Relationships
    chunk = relationship("PDFChunk", back_populates="embeddings")

    # Constraints and indexes
    __table_args__ = (
        Index(
            "idx_pdf_embeddings_active",
            "chunk_id",
            "model_name",
            postgresql_where="is_active = TRUE",
        ),
        # Note: HNSW index will be created in migration for actual PostgreSQL
        # Index('idx_pdf_embeddings_hnsw', 'embedding', postgresql_using='hnsw')
    )


class ChunkSearch(Base):
    """Full-text search index for hybrid retrieval"""

    __tablename__ = "chunk_search"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    chunk_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pdf_chunks.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    search_vector = Column(Text, nullable=False)  # Will be tsvector in PostgreSQL
    search_text = Column(Text, nullable=False)
    lexical_rank = Column(Float, default=0)
    metadata = Column(JSONB, default=dict)

    # Relationships
    chunk = relationship("PDFChunk", back_populates="search_index")

    # Constraints and indexes
    __table_args__ = (
        # Note: GIN index will be created in migration for actual PostgreSQL
        # Index('idx_chunk_search_fts', 'search_vector', postgresql_using='gin'),
    )


class PDFTable(Base):
    """Extracted tables with structured data"""

    __tablename__ = "pdf_tables"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    pdf_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pdf_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_id = Column(UUID(as_uuid=True), ForeignKey("pdf_chunks.id"))
    page_number = Column(Integer, nullable=False)
    table_index = Column(Integer, nullable=False)
    caption = Column(Text)
    headers = Column(JSONB)
    data = Column(JSONB, nullable=False)
    extraction_method = Column(String(20))
    confidence_score = Column(Float)
    bbox = Column(JSONB)

    # Relationships
    document = relationship("PDFDocument", back_populates="tables")
    chunk = relationship("PDFChunk", back_populates="table_refs")

    # Indexes
    __table_args__ = (Index("idx_pdf_tables_page", "pdf_id", "page_number"),)


class PDFFigure(Base):
    """Figure metadata and captions"""

    __tablename__ = "pdf_figures"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    pdf_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pdf_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_id = Column(UUID(as_uuid=True), ForeignKey("pdf_chunks.id"))
    page_number = Column(Integer, nullable=False)
    figure_index = Column(Integer, nullable=False)
    caption = Column(Text)
    figure_type = Column(Enum(FigureType))
    bbox = Column(JSONB)
    has_subfigures = Column(Boolean, default=False)
    metadata = Column(JSONB, default=dict)

    # Relationships
    document = relationship("PDFDocument", back_populates="figures")
    chunk = relationship("PDFChunk", back_populates="figure_refs")

    # Indexes
    __table_args__ = (Index("idx_pdf_figures_page", "pdf_id", "page_number"),)


class OntologyMapping(Base):
    """Query expansion synonyms and ontology terms"""

    __tablename__ = "ontology_mappings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    term = Column(String(255), nullable=False, index=True)
    synonyms = Column(JSONB, nullable=False)
    ontology_source = Column(String(50))
    ontology_id = Column(String(100))
    confidence = Column(Float, default=1.0)
    usage_count = Column(Integer, default=0)
    last_updated = Column(DateTime(timezone=True), default=func.now())

    # Indexes
    __table_args__ = (
        Index("idx_ontology_term", "term"),
        Index("idx_ontology_source_id", "ontology_source", "ontology_id"),
        # Note: GIN index for JSONB will be created in migration
        # Index('idx_ontology_synonyms', 'synonyms', postgresql_using='gin'),
    )


class ChatSession(Base):
    """RAG-powered conversation with enhanced tracking"""

    __tablename__ = "chat_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_token = Column(String(255), unique=True, nullable=False)
    pdf_document_id = Column(UUID(as_uuid=True), ForeignKey("pdf_documents.id"))
    user_id = Column(String(255))
    created_at = Column(DateTime(timezone=True), default=func.now())
    updated_at = Column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now()
    )
    last_activity = Column(DateTime(timezone=True), default=func.now())
    is_active = Column(Boolean, default=True)
    rag_config = Column(JSONB, default=dict)
    session_stats = Column(JSONB, default=dict)

    # Relationships
    document = relationship("PDFDocument", back_populates="sessions")
    messages = relationship(
        "Message", back_populates="session", cascade="all, delete-orphan"
    )

    # Indexes
    __table_args__ = (
        Index("idx_sessions_user", "user_id", "created_at"),
        Index("idx_sessions_activity", "last_activity"),
    )


class Message(Base):
    """Enhanced message with RAG attribution and confidence"""

    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_type = Column(Enum(MessageType), nullable=False)
    content = Column(Text, nullable=False)
    confidence_score = Column(Float)
    timestamp = Column(DateTime(timezone=True), default=func.now())
    sequence_number = Column(Integer, nullable=False)
    rag_context = Column(JSONB, default=dict)
    performance_metrics = Column(JSONB, default=dict)
    cost_breakdown = Column(JSONB, default=dict)

    # Relationships
    session = relationship("ChatSession", back_populates="messages")

    # Constraints and indexes
    __table_args__ = (
        UniqueConstraint(
            "session_id", "sequence_number", name="unique_session_sequence"
        ),
        Index("idx_messages_session_time", "session_id", "timestamp"),
        Index("idx_messages_confidence", "confidence_score"),
    )


class EmbeddingJobs(Base):
    """Postgres-based job queue for async processing"""

    __tablename__ = "embedding_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    job_type = Column(Enum(JobType), nullable=False)
    status = Column(Enum(JobStatus), nullable=False, default=JobStatus.PENDING)
    pdf_id = Column(UUID(as_uuid=True), ForeignKey("pdf_documents.id"))
    priority = Column(Integer, default=5)
    progress = Column(Integer, default=0)
    total_items = Column(Integer)
    processed_items = Column(Integer, default=0)
    retry_count = Column(Integer, default=0)
    error_log = Column(Text)
    config = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), default=func.now())
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    worker_id = Column(String(100))

    # Relationships
    document = relationship("PDFDocument", back_populates="jobs")

    # Constraints and indexes
    __table_args__ = (
        CheckConstraint("retry_count <= 3", name="check_retry_limit"),
        CheckConstraint(
            "progress >= 0 AND progress <= 100", name="check_progress_range"
        ),
        Index(
            "idx_jobs_queue",
            "status",
            "priority",
            "created_at",
            postgresql_where="status IN ('PENDING', 'RETRY')",
        ),
        Index("idx_jobs_worker", "worker_id", "status"),
    )


class EmbeddingConfig(Base):
    """System configuration for embeddings and RAG"""

    __tablename__ = "embedding_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    config_name = Column(String(100), unique=True, nullable=False)
    embedding_model = Column(String(100), nullable=False)
    model_dim = Column(Integer, nullable=False)
    chunk_size = Column(Integer, default=1000)
    chunk_overlap = Column(Integer, default=200)
    batch_size = Column(Integer, default=64)
    top_k_vector = Column(Integer, default=50)
    top_k_lexical = Column(Integer, default=50)
    similarity_threshold = Column(Float, default=0.7)
    confidence_threshold = Column(Float, default=0.7)
    mmr_lambda = Column(Float, default=0.7)
    max_context_tokens = Column(Integer, default=4000)
    enable_ocr = Column(Boolean, default=False)
    ocr_timeout_seconds = Column(Integer, default=30)
    max_file_size_mb = Column(Integer, default=100)
    max_page_count = Column(Integer, default=500)
    rate_limit_per_minute = Column(Integer, default=10)
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=func.now())
    updated_at = Column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now()
    )

    @validates("similarity_threshold", "confidence_threshold", "mmr_lambda")
    def validate_thresholds(self, key, value):
        """Validate that thresholds are between 0 and 1"""
        if value is not None and not (0 <= value <= 1):
            raise ValueError(f"{key} must be between 0 and 1")
        return value

    @validates("chunk_overlap")
    def validate_chunk_overlap(self, key, value):
        """Validate that chunk_overlap < chunk_size"""
        if hasattr(self, "chunk_size") and self.chunk_size and value >= self.chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size")
        return value

    # Indexes
    __table_args__ = (Index("idx_config_active", "is_active"),)


# Database indexes that need special PostgreSQL features
# These will be created in Alembic migrations for actual PostgreSQL
POSTGRES_SPECIFIC_INDEXES = """
-- HNSW index for vector similarity search
CREATE INDEX idx_pdf_embeddings_hnsw ON pdf_embeddings
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 200);

-- GIN index for full-text search
CREATE INDEX idx_chunk_search_fts ON chunk_search
USING GIN (search_vector);

-- GIN index for JSONB synonym search
CREATE INDEX idx_ontology_synonyms ON ontology_mappings
USING GIN (synonyms);

-- Function for job queue notifications
CREATE OR REPLACE FUNCTION notify_job_queue() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('embedding_queue', NEW.id::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger for job queue notifications
CREATE TRIGGER job_queue_notify
    AFTER INSERT ON embedding_jobs
    FOR EACH ROW
    WHEN (NEW.status = 'PENDING')
    EXECUTE FUNCTION notify_job_queue();
"""
