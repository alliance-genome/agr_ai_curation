# Research: PDF Document Q&A Chat Interface with Enhanced RAG

**Feature**: PDF Document Q&A Chat Interface with Hybrid Search RAG
**Date**: 2025-01-14
**Status**: Complete

## Executive Summary

Research completed for implementing an enhanced RAG-based PDF Q&A system using PydanticAI for orchestration, hybrid search (vector + lexical), reranking, pgvector with HNSW indexing, and comprehensive PDF processing. The system implements layout-aware chunking, table/figure extraction, ontology-aware query expansion, and confidence-based guardrails with precise citations.

## High-Impact Technical Decisions

### 1. Hybrid Search Architecture (Vector + Lexical + Reranking)

**Decision**: Dual-index approach with vector (HNSW) and lexical (tsvector) search, unified through reranking
**Rationale**:

- Vector search captures semantic similarity but misses exact strings (gene symbols, IDs)
- Lexical search excels at exact matches for scientific nomenclature
- Reranking unifies both approaches with confidence scoring
  **Implementation with PydanticAI**:

```python
class HybridSearchAgent(BaseModel):
    query: str
    vector_candidates: list[ChunkResult]  # Top-50 from HNSW
    lexical_candidates: list[ChunkResult]  # Top-50 from tsvector

@agent
async def rerank_and_select(ctx, search_results: HybridSearchAgent) -> RankedResults:
    # PydanticAI agent for reranking with biomed cross-encoder
    # Returns unified top-k with confidence scores
```

**Success Metrics**:

- Higher recall for gene symbols/IDs (target: >90%)
- Reduced false negatives for exact terminology
- Improved answer accuracy with source diversity

### 2. Layout-Aware PDF Processing with Fallback Chain

**Decision**: PyMuPDF (fitz) for all PDF extraction
**Rationale**:

- PyMuPDF extracts structure (headings, tables, figures) with bounding boxes
- pdfminer.six handles complex layouts PyMuPDF misses
- Tesseract OCR for image-only PDFs (with user warning)
  **Chunking Strategy**:
- Preserve paragraph boundaries and heading hierarchy
- Keep figure/table captions with content
- Strip headers/footers via n-gram detection
- Store section paths (e.g., "Results > Figure 2")
- Mark reference sections for down-weighting
  **PydanticAI Integration**:

```python
class ChunkingAgent(BaseModel):
    page_content: str
    layout_elements: list[LayoutElement]

@agent
async def smart_chunk(ctx, doc: ChunkingAgent) -> list[Chunk]:
    # Semantic boundary detection
    # Table/figure grouping
    # Metadata enrichment
```

### 3. HNSW Indexing Instead of IVFFlat

**Decision**: HNSW index for pgvector
**Rationale**:

- Better recall/latency trade-off than IVFFlat
- No training step required (unlike IVFFlat)
- Easier operational maintenance
  **Configuration**:

```sql
CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)
WITH (m=16, ef_construction=200);
-- Query time: SET hnsw.ef_search=64
```

**Performance Targets**:

- <100ms for top-50 retrieval
- > 95% recall@50 compared to brute force

### 4. Ontology-Aware Query Expansion

**Decision**: Local synonym mapping with bounded expansion
**Rationale**:

- Curators use different terms than papers (PARK2 vs parkin)
- Ontology expansion dramatically improves recall
- Local control over synonym quality
  **Implementation**:

```python
class QueryExpansionAgent(BaseModel):
    original_query: str
    ontology_db: dict  # DO/GO/UniProt mappings

@agent
async def expand_query(ctx, input: QueryExpansionAgent) -> ExpandedQuery:
    # Max 5 expansions per term
    # Both lexical and semantic expansion
    # Log all expansions for transparency
```

### 5. Strong Deduplication Strategy

**Decision**: Three-tier hashing (file MD5, normalized content, per-page)
**Rationale**:

- File MD5 changes with metadata updates
- Normalized content hash catches true duplicates
- Per-page hashes enable incremental updates
  **Implementation**:
- `file_hash`: Raw file MD5
- `content_hash_normalized`: MD5 of cleaned, normalized text
- `page_hashes`: JSONB array of per-page hashes
- `doi`: Extracted if available for cross-reference

### 6. Table & Figure Extraction as First-Class Citizens

**Decision**: Camelot/Tabula for tables, explicit caption preservation
**Rationale**:

- Critical data lives in tables and figure captions
- Plain text extraction loses structure
- Curators need precise table/figure citations
  **Implementation**:

```python
class TableExtractionAgent(BaseModel):
    page_image: bytes
    detected_tables: list[BoundingBox]

@agent
async def extract_tables(ctx, input: TableExtractionAgent) -> list[Table]:
    # Camelot for vector PDFs
    # Fallback to OCR + grid detection
    # Store as structured JSON in metadata
```

### 7. Confidence-Based Guardrails & Citations

**Decision**: Multi-factor confidence scoring with mandatory attribution
**Rationale**:

- "I don't know" better than confident hallucination
- Curators need verifiable sources
- Trust requires transparency
  **Confidence Factors**:
- Reranker scores of selected chunks
- Similarity score distribution
- Coverage of claims by citations
- Query-answer alignment score
  **PydanticAI Implementation**:

```python
class AnswerValidationAgent(BaseModel):
    draft_answer: str
    source_chunks: list[Chunk]
    confidence_threshold: float = 0.7

@agent
async def validate_answer(ctx, input: AnswerValidationAgent) -> ValidatedAnswer:
    # Score confidence
    # Enforce citation requirements
    # Return "insufficient evidence" if below threshold
```

### 8. Postgres-Based Job Queue (No Redis)

**Decision**: LISTEN/NOTIFY with jobs table
**Rationale**:

- No additional infrastructure
- ACID guarantees for job state
- Native integration with existing DB
  **Implementation**:

```sql
CREATE TABLE embedding_jobs (
    id UUID PRIMARY KEY,
    status ENUM('PENDING','RUNNING','FAILED','DONE'),
    pdf_id UUID,
    progress INTEGER,
    retry_count INTEGER,
    error_log TEXT
);
-- NOTIFY embedding_queue, '{job_id}';
```

### 9. Reranking with MMR Diversification

**Decision**: Cross-encoder reranking + MMR (λ=0.7)
**Rationale**:

- Avoid redundant adjacent chunks
- Balance relevance with diversity
- Better coverage of document sections
  **PydanticAI Agent**:

```python
@agent
async def mmr_rerank(ctx, candidates: list[ScoredChunk]) -> list[Chunk]:
    # Maximal Marginal Relevance
    # Penalize similarity to already-selected chunks
    # Maintain section diversity
```

### 10. Configurable Embedding Dimensions

**Decision**: Dynamic dimension support (not hardcoded 1536)
**Rationale**:

- Different models have different dimensions
- Future-proof for model upgrades
- Support multiple models per PDF
  **Implementation**:
- `model_dim` in embedding_configs
- Runtime validation against actual embeddings
- Version tracking for model changes

## Retrieval Pipeline (Concrete Implementation)

1. **Query Expansion** (PydanticAI agent):
   - Lookup ontology synonyms (cap at 5)
   - Generate semantic variations
   - Log all expansions

2. **Parallel Search**:
   - Vector: HNSW top-50 via pgvector
   - Lexical: tsvector top-50 with ts_rank
   - Union distinct chunk IDs (~100 candidates)

3. **Reranking** (PydanticAI agent):
   - Score with biomed cross-encoder
   - Apply MMR diversification (λ=0.7)
   - Filter by confidence threshold

4. **Context Assembly** (PydanticAI agent):
   - Sort by rerank score
   - Prefer section diversity
   - Include source tags (Section · p.12)
   - Cap at token budget (4000 default)

5. **Answer Generation** (PydanticAI agent):
   - Template requiring attributions
   - Confidence scoring
   - Fallback to "insufficient evidence"

6. **Post-processing**:
   - Add clickable citations with page/bbox
   - Log all scores and selections
   - Track token usage and costs

## Performance & Scaling Targets

### Latency Budgets

- PDF processing: <10s for 100 pages (with tables)
- Embedding generation: <5s for 100 chunks (batched)
- Vector search: <100ms for top-50
- Lexical search: <50ms for top-50
- Reranking: <200ms for 100 candidates
- Full RAG pipeline: <2s end-to-end

### Throughput

- 12 concurrent users minimum
- 100 PDFs/day processing capacity
- 10,000 queries/day

### Storage

- ~15MB per PDF (text + embeddings + tables)
- 10 historical sessions per user
- 90-day retention for embeddings

## Schema Enhancements

### Additional Fields

```python
# PDFDocument
content_hash_normalized: str  # Normalized text hash
page_hashes: list[str]  # Per-page hashes
doi: Optional[str]  # Extracted DOI
preproc_version: str  # Processing version

# PDFChunk
heading_text: str  # Section heading
section_path: str  # Full path (e.g., "Methods > 2.1")
is_caption: bool
is_table: bool
is_reference: bool
bbox: Optional[dict]  # Bounding box coordinates

# PDFEmbedding
model_version: str
model_dim: int  # Not hardcoded 1536
is_active: bool  # Multiple versions support

# New: ChunkSearch (lexical index)
chunk_id: UUID
search_vector: tsvector
lexical_rank: float
```

## Frontend/UX Enhancements

### RAG Transparency

- Show retrieval breakdown: "5 chunks (3 vector, 2 lexical)"
- Confidence indicator with threshold
- Toggle to view raw retrieved chunks
- Query expansion display

### Citations & Navigation

- Clickable citations → PDF.js at exact page/bbox
- Highlight relevant passages
- Section breadcrumbs
- "Jump to source" buttons

### Progress & Feedback

- Embedding progress bar with ETA
- "Processing with OCR" warning
- Cost tracking display
- FAQ precomputed on ingest

## Observability & Monitoring

### Metrics to Track

```python
# Retrieval
retrieval_vector_latency_ms
retrieval_lexical_latency_ms
rerank_latency_ms
retrieval_recall_at_k

# Quality
answers_low_confidence_total
no_result_total
lexical_search_total

# Costs
embedding_tokens_total
llm_tokens_total
cost_per_session_usd

# Errors
extraction_errors_total
timeout_total
rate_limit_429_total
```

### Structured Logging

- Correlation IDs across full pipeline
- Query expansions used
- Chunks retrieved with scores
- Confidence calculations
- Final answer with attributions

## Security & Resilience

### PDF Sanitization

- qpdf preprocessing to remove JS/malware
- File size cap (100MB default)
- Page count cap (500 pages)
- OCR timeout (30s per page)

### API Protection

- Rate limiting: 10 req/min per user
- Scoped API keys with rotation
- No PII in logs
- Encrypted storage paths

### Failure Handling

- OCR failure: Mark and continue with warning
- OpenAI outage: Fallback to lexical-only
- Empty retrieval: Suggest reformulations
- Timeout: Return partial results

## Testing Strategy

### Unit Tests (TDD)

- Each PydanticAI agent individually
- Chunking boundary cases
- Reranking with edge scores
- MMR diversification

### Integration Tests

- Full pipeline with real PDFs
- Hybrid search accuracy
- Job queue resilience
- Embedding versioning

### Performance Tests

- 12 concurrent users
- 100-page PDF processing
- Search latency under load
- Memory usage with HNSW

## Migration Path

### Phase 1: Core RAG (Week 1-2)

- HNSW indexing
- Hybrid search
- Basic reranking
- PydanticAI agents

### Phase 2: Enhancements (Week 3-4)

- Table extraction
- Query expansion
- MMR diversification
- Confidence scoring

### Phase 3: Production (Week 5-6)

- Job queue
- Progress UI
- Metrics dashboard
- Cost tracking

## Next Steps

Proceed to Phase 1 with:

1. Enhanced data model with all new fields
2. API contracts for hybrid search
3. PydanticAI agent specifications
4. Prioritized task list

---

**Research Status**: COMPLETE with all improvements
**Blockers**: None
**Ready for**: Enhanced Phase 1 Design
