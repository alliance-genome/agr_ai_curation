# Data Model: PDF Document Q&A with Enhanced RAG

**Feature**: PDF Document Q&A Chat Interface with Hybrid Search
**Date**: 2025-01-14
**Version**: 2.0.0

## Entity Relationship Diagram

```
User (future)
  │
  ├─< ChatSession >─── PDFDocument ──< PDFChunk ──< PDFEmbedding
  │         │              │              │
  │         └─< Message    │              └─< ChunkSearch (lexical)
  │         │              │
  │         └─< LangGraphRun ──< LangGraphNodeRun
  │                        │              │
  └─< SessionHistory       └─< PDFTable/PDFFigure
                           │
                           └─< OntologyMapping

EmbeddingConfig ─── EmbeddingJobs (queue)
```

## Core Entities

### 1. PDFDocument

**Purpose**: Represents uploaded PDF with comprehensive deduplication and versioning

**Fields**:

- `id`: UUID (primary key)
- `filename`: String (original filename, max 255 chars)
- `file_path`: String (storage location, encrypted path)
- `file_hash`: String (raw file MD5)
- `content_hash_normalized`: String (normalized text MD5, unique)
- `page_hashes`: JSONB (array of per-page MD5s)
- `doi`: String (extracted DOI if available, indexed)
- `file_size`: Integer (bytes, max 100MB)
- `page_count`: Integer (max 500 pages)
- `extracted_text`: Text (full document text)
- `extraction_method`: Enum (UNSTRUCTURED_FAST, UNSTRUCTURED_HI_RES, UNSTRUCTURED_OCR_ONLY)
- `is_ocr`: Boolean (true if OCR was used)
- `embeddings_generated`: Boolean
- `embedding_models`: JSONB (list of models used)
- `chunk_count`: Integer
- `table_count`: Integer
- `figure_count`: Integer
- `preproc_version`: String (processing pipeline version)
- `metadata`: JSONB
  - `title`: String
  - `authors`: List[String]
  - `publication_date`: Date
  - `journal`: String
  - `extracted_entities`: List[String] (genes, proteins, etc.)
- `upload_timestamp`: DateTime
- `last_accessed`: DateTime
- `is_valid`: Boolean
- `validation_errors`: JSONB
- `processing_stats`: JSONB
  - `extraction_time_ms`: Integer
  - `chunking_time_ms`: Integer
  - `embedding_time_ms`: Integer

**Validation Rules**:

- filename must end with .pdf
- file_size <= 104857600 (100MB)
- page_count <= 500
- content_hash_normalized must be unique
- If is_ocr = true, include OCR confidence in metadata

**Indexes**:

- content_hash_normalized (unique)
- doi (for cross-reference)
- file_hash (for raw file lookup)
- upload_timestamp, last_accessed (for cleanup)

### 2. PDFChunk

**Purpose**: Semantic chunk with layout preservation and metadata

**Fields**:

- `id`: UUID (primary key)
- `pdf_id`: UUID (foreign key to PDFDocument)
- `chunk_index`: Integer (position in document)
- `chunk_text`: Text (the actual chunk content)
- `chunk_tokens`: Integer (token count)
- `element_type`: String (from Unstructured: Title, NarrativeText, Table, FigureCaption, ListItem, etc.)
- `start_page`: Integer
- `end_page`: Integer
- `heading_text`: String (section heading if applicable)
- `section_path`: String (e.g., "Methods > 2.1 Sample Preparation")
- `is_caption`: Boolean (figure/table caption)
- `is_table`: Boolean
- `is_figure`: Boolean
- `is_reference`: Boolean (bibliography section)
- `bbox`: JSONB (bounding box coordinates)
  - `x1`, `y1`, `x2`, `y2`: Float
  - `page`: Integer
- `metadata`: JSONB
  - `has_equations`: Boolean
  - `has_citations`: Boolean
  - `language`: String
  - `confidence_score`: Float (if OCR)

**Validation Rules**:

- chunk_index unique within pdf_id
- chunk_text not empty
- chunk_tokens > 0, < 2000
- start_page <= end_page
- If is_table or is_figure, related entity must exist

**Indexes**:

- pdf_id, chunk_index (compound, unique)
- pdf_id, is_table (for table queries)
- pdf_id, is_reference (for filtering)
- section_path (for section-based search)

### 3. PDFEmbedding

**Purpose**: Multi-version embeddings with configurable dimensions

**Fields**:

- `id`: UUID (primary key)
- `chunk_id`: UUID (foreign key to PDFChunk)
- `pdf_id`: UUID (foreign key to PDFDocument, denormalized)
- `embedding`: vector (dimension varies by model)
- `model_name`: String (e.g., "text-embedding-3-small")
- `model_version`: String (for tracking updates)
- `model_dim`: Integer (1536, 3072, etc.)
- `is_active`: Boolean (current version for this model)
- `created_at`: DateTime
- `metadata`: JSONB
  - `processing_time_ms`: Integer
  - `token_count`: Integer
  - `batch_id`: UUID (for batch processing)

**Validation Rules**:

- embedding dimension must match model_dim
- Only one is_active per chunk_id + model_name
- model_dim matches known model specifications

**Indexes**:

- chunk_id, model_name, is_active (compound)
- pdf_id (for bulk operations)
- embedding using HNSW (vector_cosine_ops)
  - Parameters: m=16, ef_construction=200

### 4. ChunkSearch (Lexical Index)

**Purpose**: Full-text search index for hybrid retrieval

**Fields**:

- `id`: UUID (primary key)
- `chunk_id`: UUID (foreign key to PDFChunk, unique)
- `search_vector`: tsvector (normalized text for FTS)
- `search_text`: Text (original text for highlighting)
- `lexical_rank`: Float (BM25-like score)
- `metadata`: JSONB
  - `term_frequencies`: dict
  - `important_terms`: list (genes, proteins)

**Validation Rules**:

- search_vector not null
- chunk_id must be unique

**Indexes**:

- chunk_id (unique)
- search_vector using GIN
- lexical_rank (for sorting)

### 5. PDFTable

**Purpose**: Extracted tables with structured data

**Fields**:

- `id`: UUID (primary key)
- `pdf_id`: UUID (foreign key to PDFDocument)
- `chunk_id`: UUID (foreign key to PDFChunk)
- `page_number`: Integer
- `table_index`: Integer (position on page)
- `caption`: Text
- `headers`: JSONB (column headers)
- `data`: JSONB (table rows as list of dicts)
- `extraction_method`: Enum (UNSTRUCTURED_FAST, UNSTRUCTURED_HI_RES, UNSTRUCTURED_OCR_ONLY)
- `confidence_score`: Float
- `bbox`: JSONB (bounding box)

**Indexes**:

- pdf_id, page_number
- chunk_id (for retrieval context)

### 6. PDFFigure

**Purpose**: Figure metadata and captions

**Fields**:

- `id`: UUID (primary key)
- `pdf_id`: UUID (foreign key to PDFDocument)
- `chunk_id`: UUID (foreign key to PDFChunk)
- `page_number`: Integer
- `figure_index`: Integer
- `caption`: Text
- `figure_type`: Enum (CHART, DIAGRAM, IMAGE, PLOT)
- `bbox`: JSONB
- `has_subfigures`: Boolean
- `metadata`: JSONB

**Indexes**:

- pdf_id, page_number
- chunk_id

### 7. OntologyMapping

**Purpose**: Query expansion synonyms and ontology terms

**Fields**:

- `id`: UUID (primary key)
- `term`: String (primary term, indexed)
- `synonyms`: JSONB (list of synonyms)
- `ontology_source`: String (DO, GO, UniProt, etc.)
- `ontology_id`: String (e.g., "GO:0008150")
- `confidence`: Float (synonym quality)
- `usage_count`: Integer (for popularity)
- `last_updated`: DateTime

**Indexes**:

- term (for fast lookup)
- ontology_source, ontology_id (compound)
- Each synonym in JSONB (using GIN index)

### 8. ChatSession

**Purpose**: RAG-powered conversation with enhanced tracking

**Fields**:

- `id`: UUID (primary key)
- `session_token`: String (unique, secure)
- `pdf_document_id`: UUID (foreign key to PDFDocument)
- `user_id`: String (optional)
- `created_at`: DateTime
- `updated_at`: DateTime
- `last_activity`: DateTime
- `is_active`: Boolean
- `rag_config`: JSONB
  - `embedding_model`: String
  - `llm_model`: String
  - `top_k_vector`: Integer (default 50)
  - `top_k_lexical`: Integer (default 50)
  - `rerank_top_k`: Integer (default 5)
  - `similarity_threshold`: Float (0.7)
  - `confidence_threshold`: Float (0.7)
  - `temperature`: Float
  - `mmr_lambda`: Float (0.7)
  - `use_ontology_expansion`: Boolean
  - `max_expansions`: Integer (5)
  - `langgraph_workflow`: String (default `general_supervisor`)
  - `langgraph_version`: String (semantic version)
  - `checkpointer_strategy`: String (memory, redis, postgres)
  - `enable_human_review`: Boolean
- `session_stats`: JSONB
  - `total_questions`: Integer
  - `total_tokens_used`: Integer
  - `total_cost_usd`: Float
  - `avg_confidence_score`: Float
  - `low_confidence_answers`: Integer
  - `retrieval_stats`: dict
  - `graph_runs_completed`: Integer
  - `graph_retries`: Integer

**Indexes**:

- session_token (unique)
- user_id, created_at DESC
- pdf_document_id
- last_activity (for cleanup)

### 9. Message

**Purpose**: Enhanced message with RAG attribution and confidence

**Fields**:

- `id`: UUID (primary key)
- `session_id`: UUID (foreign key to ChatSession)
- `message_type`: Enum (USER_QUESTION, AI_RESPONSE, INSUFFICIENT_EVIDENCE, ERROR)
- `content`: Text
- `confidence_score`: Float (for AI responses)
- `timestamp`: DateTime
- `sequence_number`: Integer
- `rag_context`: JSONB
  - `query_expansion`: list (synonyms used)
  - `vector_chunks`: list[UUID] (chunk IDs from vector search)
  - `lexical_chunks`: list[UUID] (chunk IDs from lexical search)
  - `reranked_chunks`: list[dict]
    - `chunk_id`: UUID
    - `score`: Float
    - `source`: Enum (VECTOR, LEXICAL, BOTH)
  - `mmr_scores`: list[Float]
  - `citations`: list[dict]
    - `text`: String
    - `page`: Integer
    - `section`: String
    - `bbox`: dict
  - `langgraph_workflow`: String
  - `langgraph_node_path`: list[String]
- `performance_metrics`: JSONB
  - `query_expansion_ms`: Integer
  - `vector_search_ms`: Integer
  - `lexical_search_ms`: Integer
  - `rerank_ms`: Integer
  - `generation_ms`: Integer
  - `total_ms`: Integer
- `cost_breakdown`: JSONB
  - `embedding_tokens`: Integer
  - `llm_tokens`: Integer
  - `cost_usd`: Float

**Indexes**:

- session_id, sequence_number (compound, unique)
- session_id, timestamp
- confidence_score (for filtering)
- GIN index on rag_context (jsonb_path_ops)

### 10. LangGraphRun

**Purpose**: Persist each LangGraph supervisor execution per user question for replay and analytics

**Fields**:

- `id`: UUID (primary key)
- `session_id`: UUID (foreign key to ChatSession)
- `pdf_id`: UUID (foreign key to PDFDocument, nullable)
- `workflow_name`: String (graph identifier such as `general_supervisor`)
- `input_query`: Text (original user prompt)
- `state_snapshot`: JSONB (final state from LangGraph checkpointer)
- `status`: Enum (PENDING, RUNNING, COMPLETED, FAILED, INTERRUPTED)
- `started_at`: DateTime with timezone
- `completed_at`: DateTime with timezone
- `latency_ms`: Integer
- `specialists_invoked`: JSONB (list of node keys executed)
- `debug_trace_path`: Text (optional local path to serialized trace)
- `run_metadata`: JSONB (SSE channel, admin overrides, etc.)

**Validation Rules**:

- session_id must exist
- latency_ms >= 0 when completed_at present
- completed_at required when status in (COMPLETED, FAILED)
- specialists_invoked defaults to []

**Indexes**:

- session_id, started_at DESC
- status (partial index for RUNNING)
- workflow_name

### 11. LangGraphNodeRun

**Purpose**: Track per-node execution for debugging, human approvals, and observability

**Fields**:

- `id`: UUID (primary key)
- `graph_run_id`: UUID (foreign key to LangGraphRun, cascade delete)
- `node_key`: String (LangGraph node identifier)
- `node_type`: String (intent_router, pydantic_agent, tool, human_gate, etc.)
- `input_state`: JSONB (subset entering node)
- `output_state`: JSONB (state diff produced)
- `status`: Enum (PENDING, RUNNING, COMPLETED, FAILED, SKIPPED)
- `started_at`: DateTime with timezone
- `completed_at`: DateTime with timezone
- `latency_ms`: Integer
- `error`: Text (stack trace or structured payload)
- `deps_snapshot`: JSONB (payload handed to wrapped PydanticAI agents)

**Validation Rules**:

- graph_run_id must exist
- node_key unique per graph_run_id
- latency_ms >= 0 with completed_at
- deps_snapshot required when node_type = 'pydantic_agent'

**Indexes**:

- graph_run_id, node_key (unique)
- node_type (observability)
- status (partial index for FAILED nodes)

### 12. EmbeddingJobs

**Purpose**: Postgres-based job queue for async processing

**Fields**:

- `id`: UUID (primary key)
- `job_type`: Enum (EMBED_PDF, REEMBED_PDF, EXTRACT_TABLES)
- `status`: Enum (PENDING, RUNNING, RETRY, FAILED, DONE)
- `pdf_id`: UUID (foreign key to PDFDocument)
- `priority`: Integer (1-10, higher = more urgent)
- `progress`: Integer (0-100)
- `total_items`: Integer
- `processed_items`: Integer
- `retry_count`: Integer (max 3)
- `error_log`: Text
- `config`: JSONB (job-specific configuration)
- `created_at`: DateTime
- `started_at`: DateTime
- `completed_at`: DateTime
- `worker_id`: String (for distributed processing)

**Validation Rules**:

- retry_count <= 3
- progress between 0 and 100
- status transitions enforced

**Indexes**:

- status, priority DESC, created_at (for job polling)
- pdf_id (for status checks)
- worker_id, status (for worker management)

### 13. EmbeddingConfig

**Purpose**: System configuration for embeddings and RAG

**Fields**:

- `id`: UUID (primary key)
- `config_name`: String (unique)
- `embedding_model`: String
- `model_dim`: Integer (not hardcoded)
- `chunk_size`: Integer (default 1000)
- `chunk_overlap`: Integer (default 200)
- `batch_size`: Integer (default 64)
- `top_k_vector`: Integer (default 50)
- `top_k_lexical`: Integer (default 50)
- `similarity_threshold`: Float (0.7)
- `confidence_threshold`: Float (0.7)
- `mmr_lambda`: Float (0.7)
- `max_context_tokens`: Integer (4000)
- `enable_ocr`: Boolean
- `ocr_timeout_seconds`: Integer (30)
- `max_file_size_mb`: Integer (100)
- `max_page_count`: Integer (500)
- `rate_limit_per_minute`: Integer (10)
- `is_active`: Boolean
- `created_at`: DateTime
- `updated_at`: DateTime

**Validation Rules**:

- Only one is_active = true
- chunk_overlap < chunk_size
- All thresholds between 0 and 1

## Database Schema

### PostgreSQL with pgvector and Full-Text Search

```sql
-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- For fuzzy text matching

-- PDFDocument table with enhanced deduplication
CREATE TABLE pdf_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    filename VARCHAR(255) NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    file_hash VARCHAR(32) NOT NULL,
    content_hash_normalized VARCHAR(32) UNIQUE NOT NULL,
    page_hashes JSONB,
    doi VARCHAR(255),
    file_size INTEGER NOT NULL CHECK (file_size <= 104857600),
    page_count INTEGER NOT NULL CHECK (page_count <= 500),
    extracted_text TEXT,
    extraction_method VARCHAR(20),
    is_ocr BOOLEAN DEFAULT FALSE,
    embeddings_generated BOOLEAN DEFAULT FALSE,
    embedding_models JSONB DEFAULT '[]',
    chunk_count INTEGER DEFAULT 0,
    table_count INTEGER DEFAULT 0,
    figure_count INTEGER DEFAULT 0,
    preproc_version VARCHAR(20),
    metadata JSONB DEFAULT '{}',
    upload_timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_accessed TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_valid BOOLEAN DEFAULT TRUE,
    validation_errors JSONB DEFAULT '{}',
    processing_stats JSONB DEFAULT '{}',
    CONSTRAINT valid_pdf_extension CHECK (filename ILIKE '%.pdf')
);

-- PDFChunk table with layout metadata
CREATE TABLE pdf_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pdf_id UUID NOT NULL REFERENCES pdf_documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    chunk_tokens INTEGER NOT NULL CHECK (chunk_tokens BETWEEN 1 AND 2000),
    start_page INTEGER NOT NULL,
    end_page INTEGER NOT NULL,
    heading_text VARCHAR(500),
    section_path VARCHAR(500),
    is_caption BOOLEAN DEFAULT FALSE,
    is_table BOOLEAN DEFAULT FALSE,
    is_figure BOOLEAN DEFAULT FALSE,
    is_reference BOOLEAN DEFAULT FALSE,
    bbox JSONB,
    metadata JSONB DEFAULT '{}',
    UNIQUE(pdf_id, chunk_index),
    CHECK (start_page <= end_page)
);

-- PDFEmbedding with configurable dimensions
CREATE TABLE pdf_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_id UUID NOT NULL REFERENCES pdf_chunks(id) ON DELETE CASCADE,
    pdf_id UUID NOT NULL REFERENCES pdf_documents(id) ON DELETE CASCADE,
    embedding vector NOT NULL,  -- Dimension set at runtime
    model_name VARCHAR(100) NOT NULL,
    model_version VARCHAR(20) NOT NULL,
    model_dim INTEGER NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

-- ChunkSearch for lexical retrieval
CREATE TABLE chunk_search (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_id UUID UNIQUE NOT NULL REFERENCES pdf_chunks(id) ON DELETE CASCADE,
    search_vector tsvector NOT NULL,
    search_text TEXT NOT NULL,
    lexical_rank FLOAT DEFAULT 0,
    metadata JSONB DEFAULT '{}'
);

-- PDFTable for structured data
CREATE TABLE pdf_tables (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pdf_id UUID NOT NULL REFERENCES pdf_documents(id) ON DELETE CASCADE,
    chunk_id UUID REFERENCES pdf_chunks(id),
    page_number INTEGER NOT NULL,
    table_index INTEGER NOT NULL,
    caption TEXT,
    headers JSONB,
    data JSONB NOT NULL,
    extraction_method VARCHAR(20),
    confidence_score FLOAT,
    bbox JSONB
);

-- PDFFigure for figure tracking
CREATE TABLE pdf_figures (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pdf_id UUID NOT NULL REFERENCES pdf_documents(id) ON DELETE CASCADE,
    chunk_id UUID REFERENCES pdf_chunks(id),
    page_number INTEGER NOT NULL,
    figure_index INTEGER NOT NULL,
    caption TEXT,
    figure_type VARCHAR(20),
    bbox JSONB,
    has_subfigures BOOLEAN DEFAULT FALSE,
    metadata JSONB DEFAULT '{}'
);

-- OntologyMapping for query expansion
CREATE TABLE ontology_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    term VARCHAR(255) NOT NULL,
    synonyms JSONB NOT NULL,
    ontology_source VARCHAR(50),
    ontology_id VARCHAR(100),
    confidence FLOAT DEFAULT 1.0,
    usage_count INTEGER DEFAULT 0,
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- LangGraph workflow executions
CREATE TABLE langgraph_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    pdf_id UUID REFERENCES pdf_documents(id) ON DELETE SET NULL,
    workflow_name VARCHAR(100) NOT NULL,
    input_query TEXT NOT NULL,
    state_snapshot JSONB DEFAULT '{}',
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE,
    latency_ms INTEGER,
    specialists_invoked JSONB DEFAULT '[]',
    debug_trace_path TEXT,
    metadata JSONB DEFAULT '{}',
    CHECK (latency_ms IS NULL OR latency_ms >= 0)
);

CREATE INDEX idx_langgraph_runs_session ON langgraph_runs(session_id, started_at DESC);
CREATE INDEX idx_langgraph_runs_status ON langgraph_runs(status);
CREATE INDEX idx_langgraph_runs_workflow ON langgraph_runs(workflow_name);

-- LangGraph node executions
CREATE TABLE langgraph_node_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    graph_run_id UUID NOT NULL REFERENCES langgraph_runs(id) ON DELETE CASCADE,
    node_key VARCHAR(150) NOT NULL,
    node_type VARCHAR(50) NOT NULL,
    input_state JSONB DEFAULT '{}',
    output_state JSONB DEFAULT '{}',
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE,
    latency_ms INTEGER,
    error TEXT,
    deps_snapshot JSONB,
    CHECK (latency_ms IS NULL OR latency_ms >= 0)
);

CREATE UNIQUE INDEX idx_langgraph_node_unique ON langgraph_node_runs(graph_run_id, node_key);
CREATE INDEX idx_langgraph_node_type ON langgraph_node_runs(node_type);
CREATE INDEX idx_langgraph_node_status ON langgraph_node_runs(status);

-- EmbeddingJobs for async processing
CREATE TABLE embedding_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    pdf_id UUID REFERENCES pdf_documents(id),
    priority INTEGER DEFAULT 5,
    progress INTEGER DEFAULT 0,
    total_items INTEGER,
    processed_items INTEGER DEFAULT 0,
    retry_count INTEGER DEFAULT 0,
    error_log TEXT,
    config JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    worker_id VARCHAR(100),
    CHECK (retry_count <= 3),
    CHECK (progress BETWEEN 0 AND 100)
);

-- Indexes for performance
CREATE INDEX idx_pdf_documents_doi ON pdf_documents(doi);
CREATE INDEX idx_pdf_documents_normalized_hash ON pdf_documents(content_hash_normalized);
CREATE INDEX idx_pdf_chunks_pdf_tables ON pdf_chunks(pdf_id) WHERE is_table = TRUE;
CREATE INDEX idx_pdf_chunks_section ON pdf_chunks(section_path);
CREATE INDEX idx_pdf_embeddings_active ON pdf_embeddings(chunk_id, model_name) WHERE is_active = TRUE;
CREATE INDEX idx_pdf_embeddings_hnsw ON pdf_embeddings USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 200);
CREATE INDEX idx_chunk_search_fts ON chunk_search USING GIN (search_vector);
CREATE INDEX idx_ontology_term ON ontology_mappings(term);
CREATE INDEX idx_ontology_synonyms ON ontology_mappings USING GIN (synonyms);
CREATE INDEX idx_jobs_queue ON embedding_jobs(status, priority DESC, created_at) WHERE status IN ('PENDING', 'RETRY');

-- Function for job queue notifications
CREATE OR REPLACE FUNCTION notify_job_queue() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('embedding_queue', NEW.id::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER job_queue_notify
    AFTER INSERT ON embedding_jobs
    FOR EACH ROW
    WHEN (NEW.status = 'PENDING')
    EXECUTE FUNCTION notify_job_queue();
```

## Performance Optimizations

- HNSW index for fast vector similarity (target: <100ms for top-50)
- GIN index for full-text search (target: <50ms for top-50)
- Partial indexes for filtered queries
- JSONB indexes for metadata searches
- Denormalized pdf_id for faster joins
- Trigger-based notifications for job queue

## Security Considerations

- Encrypted file paths
- Row-level security ready
- No PII in embeddings
- Audit trail via processing_stats
- Rate limiting via config

---

**Status**: Ready for API contract generation
**Next**: Generate OpenAPI contracts with hybrid search endpoints
