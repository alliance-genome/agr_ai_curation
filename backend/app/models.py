"""
SQLAlchemy models for AI Curation Backend
Combines PDF Document Q&A models with legacy models using SQLAlchemy 2.0 style
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
from sqlalchemy.dialects.postgresql import UUID, JSONB, TSVECTOR
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import DeclarativeBase, relationship, validates
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Base class for all models using SQLAlchemy 2.0 style"""

    pass


# ====================
# LEGACY MODELS
# ====================
# These models are still in use by existing features
# Will be gradually migrated or integrated with the new PDF Q&A models


class Entity(Base):
    """Model for storing biological entities (genes, proteins, etc.)"""

    __tablename__ = "entities"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)
    synonyms = Column(JSON, default=list)
    references = Column(JSON, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Settings(Base):
    """Model for storing application settings"""

    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


# ====================
# PDF Q&A MODELS
# ====================
# New models for PDF Document Q&A with Enhanced RAG feature


# Enums for various fields
class ExtractionMethod(str, PyEnum):
    UNSTRUCTURED_FAST = "UNSTRUCTURED_FAST"
    UNSTRUCTURED_HI_RES = "UNSTRUCTURED_HI_RES"
    UNSTRUCTURED_OCR_ONLY = "UNSTRUCTURED_OCR_ONLY"


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
    meta_data = Column(JSONB, default=dict)
    upload_timestamp = Column(DateTime(timezone=True), default=func.now())
    last_accessed = Column(DateTime(timezone=True), default=func.now())
    is_valid = Column(Boolean, default=True)
    validation_errors = Column(JSONB, default=dict)

    # Relationships
    chunks = relationship(
        "PDFChunk", back_populates="pdf_document", cascade="all, delete-orphan"
    )
    embeddings = relationship(
        "PDFEmbedding", back_populates="pdf_document", cascade="all, delete-orphan"
    )
    chat_sessions = relationship(
        "ChatSession", back_populates="pdf_document", cascade="all, delete-orphan"
    )
    tables = relationship(
        "ExtractedTable", back_populates="pdf_document", cascade="all, delete-orphan"
    )
    figures = relationship(
        "ExtractedFigure", back_populates="pdf_document", cascade="all, delete-orphan"
    )

    # Indexes
    __table_args__ = (
        Index("idx_pdf_file_hash", "file_hash"),
        Index("idx_pdf_content_hash", "content_hash_normalized"),
        Index("idx_pdf_doi", "doi"),
        Index("idx_pdf_upload_timestamp", "upload_timestamp"),
    )

    @validates("file_size")
    def validate_file_size(self, key, value):
        """Validate file size is positive and within 100MB limit"""
        if value <= 0:
            raise ValueError("File size must be positive")
        if value > 104857600:  # 100MB in bytes
            raise ValueError("File size exceeds 100MB limit")
        return value

    @validates("page_count")
    def validate_page_count(self, key, value):
        """Validate page count is positive and within 500 page limit"""
        if value <= 0:
            raise ValueError("Page count must be positive")
        if value > 500:
            raise ValueError("Page count exceeds 500 page limit")
        return value


class PDFChunk(Base):
    """Stores document chunks with semantic boundaries for RAG"""

    __tablename__ = "pdf_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    pdf_id = Column(UUID(as_uuid=True), ForeignKey("pdf_documents.id"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    element_type = Column(
        String(50)
    )  # Title, NarrativeText, Table, FigureCaption, etc.
    page_start = Column(Integer, nullable=False)
    page_end = Column(Integer, nullable=False)
    char_start = Column(Integer)
    char_end = Column(Integer)
    bbox = Column(JSONB)
    section_path = Column(Text)
    is_reference = Column(Boolean, default=False)
    is_caption = Column(Boolean, default=False)
    is_header = Column(Boolean, default=False)
    is_table = Column(Boolean, default=False)
    is_figure = Column(Boolean, default=False)
    token_count = Column(Integer)
    chunk_hash = Column(String(32), nullable=False)
    meta_data = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), default=func.now())

    # Relationships
    pdf_document = relationship("PDFDocument", back_populates="chunks")
    embeddings = relationship(
        "PDFEmbedding", back_populates="chunk", cascade="all, delete-orphan"
    )
    search_entries = relationship(
        "ChunkSearch", back_populates="chunk", cascade="all, delete-orphan"
    )

    # Constraints and indexes
    __table_args__ = (
        UniqueConstraint("pdf_id", "chunk_index", name="uq_pdf_chunk_index"),
        Index("idx_chunk_pdf_id", "pdf_id"),
        Index("idx_chunk_hash", "chunk_hash"),
        Index("idx_chunk_section", "section_path"),
    )

    @validates("page_start", "page_end")
    def validate_pages(self, key, value):
        """Validate page numbers are positive"""
        if value <= 0:
            raise ValueError(f"{key} must be positive")
        return value


class PDFEmbedding(Base):
    """Stores embeddings with versioning and model tracking"""

    __tablename__ = "pdf_embeddings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    pdf_id = Column(UUID(as_uuid=True), ForeignKey("pdf_documents.id"), nullable=False)
    chunk_id = Column(UUID(as_uuid=True), ForeignKey("pdf_chunks.id"), nullable=False)
    embedding = Column(
        Vector(1536), nullable=False
    )  # Default to text-embedding-3-small
    model_name = Column(String(100), nullable=False)
    model_version = Column(String(20))
    dimensions = Column(Integer, nullable=False)
    usage_count = Column(Integer, default=0)
    processing_time_ms = Column(Float)
    created_at = Column(DateTime(timezone=True), default=func.now())
    last_used = Column(DateTime(timezone=True), default=func.now())

    # Relationships
    pdf_document = relationship("PDFDocument", back_populates="embeddings")
    chunk = relationship("PDFChunk", back_populates="embeddings")

    # Indexes - HNSW for vector similarity search
    __table_args__ = (
        Index(
            "idx_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 200},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("idx_embedding_pdf_id", "pdf_id"),
        Index("idx_embedding_chunk_id", "chunk_id"),
        Index("idx_embedding_model", "model_name"),
        UniqueConstraint("chunk_id", "model_name", name="uq_chunk_model"),
    )

    @validates("dimensions")
    def validate_dimensions(self, key, value):
        """Validate embedding dimensions"""
        valid_dims = [384, 768, 1024, 1536, 3072]
        if value not in valid_dims:
            raise ValueError(f"Dimensions must be one of {valid_dims}")
        return value


class ChunkSearch(Base):
    """Lexical search index using PostgreSQL tsvector"""

    __tablename__ = "chunk_search"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    chunk_id = Column(
        UUID(as_uuid=True), ForeignKey("pdf_chunks.id"), unique=True, nullable=False
    )
    search_vector = Column(TSVECTOR, nullable=False)
    text_length = Column(Integer, nullable=False)
    lang = Column(String(10), default="english")
    updated_at = Column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now()
    )

    # Relationships
    chunk = relationship("PDFChunk", back_populates="search_entries")

    # Indexes for full-text search
    __table_args__ = (
        Index("idx_search_vector_gin", "search_vector", postgresql_using="gin"),
        Index("idx_search_chunk_id", "chunk_id"),
    )


class ChatSession(Base):
    """Manages chat sessions with RAG configuration"""

    __tablename__ = "chat_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    pdf_id = Column(UUID(as_uuid=True), ForeignKey("pdf_documents.id"), nullable=False)
    user_id = Column(String(255))
    session_name = Column(String(255))
    rag_config = Column(JSONB, default=dict)
    confidence_threshold = Column(Float, default=0.7)
    total_messages = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    total_cost_usd = Column(Float, default=0.0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=func.now())
    last_activity = Column(DateTime(timezone=True), default=func.now())

    # Relationships
    pdf_document = relationship("PDFDocument", back_populates="chat_sessions")
    messages = relationship(
        "Message", back_populates="session", cascade="all, delete-orphan"
    )

    # Indexes
    __table_args__ = (
        Index("idx_session_pdf_id", "pdf_id"),
        Index("idx_session_user_id", "user_id"),
        Index("idx_session_created", "created_at"),
        CheckConstraint(
            "confidence_threshold >= 0 AND confidence_threshold <= 1",
            name="check_confidence_range",
        ),
    )


class Message(Base):
    """Stores messages with confidence scores and citations"""

    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id = Column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id"), nullable=False
    )
    message_type = Column(Enum(MessageType), nullable=False)
    content = Column(Text, nullable=False)
    confidence_score = Column(Float)
    citations = Column(JSONB, default=list)
    retrieval_stats = Column(JSONB, default=dict)
    token_count = Column(Integer)
    cost_usd = Column(Float)
    processing_time_ms = Column(Float)
    timestamp = Column(DateTime(timezone=True), default=func.now())

    # Relationships
    session = relationship("ChatSession", back_populates="messages")

    # Indexes
    __table_args__ = (
        Index("idx_message_session_id", "session_id"),
        Index("idx_message_timestamp", "timestamp"),
        Index("idx_message_type", "message_type"),
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="check_message_confidence",
        ),
    )


class ExtractedTable(Base):
    """Stores extracted tables with structured data"""

    __tablename__ = "extracted_tables"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    pdf_id = Column(UUID(as_uuid=True), ForeignKey("pdf_documents.id"), nullable=False)
    page_number = Column(Integer, nullable=False)
    table_index = Column(Integer, nullable=False)
    caption = Column(Text)
    headers = Column(JSONB)
    data = Column(JSONB, nullable=False)
    bbox = Column(JSONB)
    confidence = Column(Float)
    table_hash = Column(String(32))
    created_at = Column(DateTime(timezone=True), default=func.now())

    # Relationships
    pdf_document = relationship("PDFDocument", back_populates="tables")

    # Indexes
    __table_args__ = (
        UniqueConstraint(
            "pdf_id", "page_number", "table_index", name="uq_pdf_page_table"
        ),
        Index("idx_table_pdf_id", "pdf_id"),
        Index("idx_table_page", "page_number"),
    )


class ExtractedFigure(Base):
    """Stores extracted figures with metadata"""

    __tablename__ = "extracted_figures"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    pdf_id = Column(UUID(as_uuid=True), ForeignKey("pdf_documents.id"), nullable=False)
    page_number = Column(Integer, nullable=False)
    figure_index = Column(Integer, nullable=False)
    figure_type = Column(Enum(FigureType))
    caption = Column(Text)
    image_path = Column(String(500))
    bbox = Column(JSONB)
    ocr_text = Column(Text)
    figure_hash = Column(String(32))
    created_at = Column(DateTime(timezone=True), default=func.now())

    # Relationships
    pdf_document = relationship("PDFDocument", back_populates="figures")

    # Indexes
    __table_args__ = (
        UniqueConstraint(
            "pdf_id", "page_number", "figure_index", name="uq_pdf_page_figure"
        ),
        Index("idx_figure_pdf_id", "pdf_id"),
        Index("idx_figure_page", "page_number"),
        Index("idx_figure_type", "figure_type"),
    )


class EmbeddingJob(Base):
    """Job queue for async embedding generation"""

    __tablename__ = "embedding_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    pdf_id = Column(UUID(as_uuid=True), ForeignKey("pdf_documents.id"), nullable=False)
    job_type = Column(Enum(JobType), nullable=False)
    status = Column(Enum(JobStatus), default=JobStatus.PENDING, nullable=False)
    priority = Column(Integer, default=5)  # 1-10, higher = more urgent
    config = Column(JSONB, default=dict)
    progress = Column(Integer, default=0)
    total_items = Column(Integer)
    processed_items = Column(Integer, default=0)
    error_log = Column(Text)
    retry_count = Column(Integer, default=0)
    result = Column(JSONB)
    worker_id = Column(String(100))
    created_at = Column(DateTime(timezone=True), default=func.now())
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))

    # Indexes
    __table_args__ = (
        Index("idx_job_pdf_id", "pdf_id"),
        Index("idx_job_status", "status"),
        Index("idx_job_type", "job_type"),
        Index("idx_job_priority", "priority"),
        Index("idx_job_created", "created_at"),
    )


class CitationTracking(Base):
    """Tracks citation usage and accuracy"""

    __tablename__ = "citation_tracking"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    message_id = Column(UUID(as_uuid=True), ForeignKey("messages.id"), nullable=False)
    chunk_id = Column(UUID(as_uuid=True), ForeignKey("pdf_chunks.id"), nullable=False)
    confidence = Column(Float, nullable=False)
    relevance_score = Column(Float)
    user_feedback = Column(Boolean)
    created_at = Column(DateTime(timezone=True), default=func.now())

    # Indexes
    __table_args__ = (
        Index("idx_citation_message_id", "message_id"),
        Index("idx_citation_chunk_id", "chunk_id"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="check_citation_confidence"
        ),
    )


# Event listeners for maintaining updated_at timestamps
@event.listens_for(ChunkSearch, "before_update")
def receive_before_update(mapper, connection, target):
    """Update the updated_at timestamp before update"""
    target.updated_at = func.now()
