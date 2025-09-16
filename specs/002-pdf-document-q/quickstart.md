# Quick Start Guide: Enhanced PDF Q&A with Hybrid RAG

**Feature**: PDF Document Q&A with Hybrid Search, Reranking, and Guardrails
**Version**: 2.0.0
**Prerequisites**: Docker, PostgreSQL 16+, pgvector, Python 3.11+, Node.js 20+

## 🚀 5-Minute Setup

### 1. Clone and Setup

```bash
# Clone repository
git clone <repository-url>
cd ai_curation
git checkout 002-pdf-document-q

# Install dependencies
cd backend && pip install -r requirements.txt && cd ..
cd frontend && npm install && cd ..
```

### 2. Configure Environment

```bash
cp .env.example .env

# Required settings in .env:
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...  # Optional for Gemini models
# Note: DATABASE_URL is set automatically by docker-compose.yml
```

### 3. Start Services with Docker Compose

```bash
# Start all services (PostgreSQL, Backend, Frontend)
docker compose up -d

# Wait for services to be ready
sleep 5

# Create tables from SQLAlchemy models
# WARNING: This will DROP ALL TABLES and delete all data! Only use in development!
docker compose exec backend python -c "
from app.models import Base
from app.database import engine
# CAUTION: drop_all() will DELETE ALL DATA - ensure this is not production!
Base.metadata.drop_all(engine)  # Clean start - DESTROYS ALL DATA
Base.metadata.create_all(engine)  # Create all tables with indexes
print('Database schema created successfully!')
"
```

### 4. Alternative: Manual Setup (without Docker Compose)

```bash
# Only if not using Docker Compose
# Run PostgreSQL standalone with same credentials as docker-compose
docker run -d \
  --name ai_curation_db \
  -e POSTGRES_USER=curation_user \
  -e POSTGRES_PASSWORD=curation_pass \
  -e POSTGRES_DB=ai_curation_db \
  -p 5432:5432 \
  pgvector/pgvector:pg16

# Then manually start services:
# Terminal 1: Backend with job workers
cd backend && uvicorn app.main:app --reload --port 8002

# Terminal 2: Job worker (processes embeddings)
cd backend && python -m app.workers.embedding_worker

# Terminal 3: Frontend
cd frontend && npm run dev
```

### 5. Verify Setup & Access Application

- Frontend: http://localhost:8080 (Docker Compose nginx)
- API Docs: http://localhost:8002/docs
- Metrics: http://localhost:8002/metrics
- Health: http://localhost:8002/health

## 📚 Enhanced Library CLI Usage

### PDF Processor (Element-Based Extraction)

```bash
# Extract with Unstructured.io
python -m backend.lib.pdf_processor extract paper.pdf \
  --strategy=hi_res \
  --extract-images-in-pdf

# Check extraction quality with element types
python -m backend.lib.pdf_processor validate paper.pdf \
  --show-elements \
  --format=json

# Get page-level hashes for deduplication
python -m backend.lib.pdf_processor hash paper.pdf \
  --normalized \
  --per-page
```

### Chunk Manager (Element-Based Chunking)

```bash
# Smart chunking with element type preservation
python -m backend.lib.chunk_manager chunk paper.pdf \
  --size=1000 \
  --overlap=200 \
  --preserve-elements \
  --group-tables \
  --group-captions

# Analyze chunk quality by element types
python -m backend.lib.chunk_manager analyze paper.pdf \
  --show-elements \
  --token-counts
```

### Embedding Service (Multi-Model Support)

```bash
# Generate embeddings with versioning
python -m backend.lib.embedding_service embed \
  --pdf-id=<uuid> \
  --model=text-embedding-3-small \
  --batch-size=64 \
  --version=1.0

# List available models and dimensions
python -m backend.lib.embedding_service list-models

# Check for existing embeddings
python -m backend.lib.embedding_service status --pdf-id=<uuid>
```

### Hybrid Search (Vector + Lexical)

```bash
# Perform hybrid search
echo "BRCA1 gene mutations" | python -m backend.lib.hybrid_search query \
  --pdf-id=<uuid> \
  --vector-k=50 \
  --lexical-k=50 \
  --format=json

# Test search performance
python -m backend.lib.hybrid_search benchmark \
  --pdf-id=<uuid> \
  --queries=test_queries.txt
```

### Reranker (Cross-Encoder + MMR)

```bash
# Rerank search results
python -m backend.lib.reranker rerank \
  --candidates=search_results.json \
  --top-k=5 \
  --mmr \
  --lambda=0.7

# Evaluate reranker quality
python -m backend.lib.reranker evaluate \
  --test-set=rerank_test.json
```

### Query Expander (Ontology-Aware)

```bash
# Expand query with synonyms
echo "parkin protein" | python -m backend.lib.query_expander expand \
  --max=5 \
  --sources=GO,UniProt \
  --format=json

# Load ontology mappings
python -m backend.lib.query_expander load \
  --file=ontology_mappings.csv
```

### RAG Orchestrator (PydanticAI Pipeline)

```bash
# Full RAG pipeline
echo "What are the main findings?" | python -m backend.lib.rag_orchestrator question \
  --pdf-id=<uuid> \
  --confidence-threshold=0.7 \
  --include-tables \
  --format=json

# Test with custom config
python -m backend.lib.rag_orchestrator question \
  --pdf-id=<uuid> \
  --config=rag_config.json \
  < question.txt
```

### Job Queue (Postgres-Based)

```bash
# Check job status
python -m backend.lib.job_queue status --job-id=<uuid>

# List pending jobs
python -m backend.lib.job_queue list --status=PENDING

# Retry failed job
python -m backend.lib.job_queue retry --job-id=<uuid>

# Monitor queue metrics
python -m backend.lib.job_queue monitor --interval=5
```

## 🧪 Comprehensive Testing

### Backend Tests

```bash
cd backend

# Unit tests for each library
pytest tests/unit/test_pdf_processor.py -v
pytest tests/unit/test_chunk_manager.py -v
pytest tests/unit/test_hybrid_search.py -v
pytest tests/unit/test_reranker.py -v
pytest tests/unit/test_query_expander.py -v
pytest tests/unit/test_rag_orchestrator.py -v

# Integration tests
pytest tests/integration/test_hybrid_pipeline.py
pytest tests/integration/test_pgvector_hnsw.py
pytest tests/integration/test_job_queue.py
pytest tests/integration/test_confidence_scoring.py

# Performance benchmarks
pytest tests/performance/test_search_latency.py
pytest tests/performance/test_rerank_throughput.py
pytest tests/performance/test_concurrent_users.py

# Contract tests
pytest tests/contract/test_rag_endpoints.py
pytest tests/contract/test_hybrid_search_api.py
```

### Frontend Tests

```bash
cd frontend

# Component tests
npm test -- PDFUpload
npm test -- ChatInterface
npm test -- CitationDisplay
npm test -- ConfidenceIndicator
npm test -- SearchBreakdown
npm test -- JobProgress

# E2E tests
npm run test:e2e -- upload-to-answer
npm run test:e2e -- low-confidence-handling
npm run test:e2e -- citation-navigation
```

## 📝 Usage Flow Examples

### 1. Upload PDF with Deduplication

```bash
# Upload and check for duplicates
curl -X POST http://localhost:8002/api/v1/pdf/upload \
  -F "file=@paper.pdf" \
  -F "extract_tables=true"

# Response (new PDF):
{
  "pdf_id": "123e4567...",
  "status": "new",
  "job_id": "456e7890...",
  "content_hash_normalized": "5d41402abc..."
}

# Response (duplicate):
{
  "status": "duplicate",
  "existing_pdf_id": "789abc...",
  "embeddings_ready": true
}
```

### 2. Monitor Processing Job

```bash
# Check job progress
curl http://localhost:8002/api/v1/jobs/456e7890

{
  "job_id": "456e7890...",
  "status": "RUNNING",
  "progress": 45,
  "total_items": 150,
  "processed_items": 68,
  "estimated_completion": "2025-01-14T10:05:00Z"
}
```

### 3. Create Session with Custom Config

```bash
curl -X POST http://localhost:8002/api/v1/session/create \
  -H "Content-Type: application/json" \
  -d '{
    "pdf_id": "123e4567...",
    "rag_config": {
      "top_k_vector": 50,
      "top_k_lexical": 50,
      "rerank_top_k": 5,
      "confidence_threshold": 0.7,
      "mmr_lambda": 0.7,
      "use_ontology_expansion": true
    }
  }'
```

### 4. Ask Question with Hybrid RAG

```bash
curl -X POST http://localhost:8002/api/v1/session/abc123/question \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What genes are associated with the disease phenotype?",
    "include_tables": true
  }'

# High confidence response:
{
  "answer": "Based on the document, BRCA1 and BRCA2 genes...",
  "confidence_score": 0.92,
  "citations": [
    {
      "text": "BRCA1 mutations were found in 45% of cases...",
      "page": 12,
      "section": "Results > 3.2 Genetic Analysis",
      "confidence": 0.95,
      "source_type": "both",
      "bbox": {"x1": 72, "y1": 340, "x2": 540, "y2": 380}
    }
  ],
  "retrieval_stats": {
    "query_expansions": ["BRCA1", "breast cancer 1"],
    "vector_hits": 28,
    "lexical_hits": 15,
    "reranked_count": 5
  }
}

# Low confidence response:
{
  "message": "Insufficient evidence to answer confidently",
  "confidence_score": 0.45,
  "suggested_sections": ["Methods", "Supplementary Data"],
  "reformulation_suggestions": [
    "Try: 'In the Results section, what genes...'",
    "Try: 'Table 2 gene associations...'"
  ]
}
```

### 5. Direct Hybrid Search (No LLM)

```bash
curl -X POST http://localhost:8002/api/v1/session/abc123/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "CRISPR knockout efficiency",
    "top_k_vector": 30,
    "top_k_lexical": 30,
    "rerank_top_k": 10,
    "use_mmr": true
  }'

{
  "results": [
    {
      "chunk_id": "def456...",
      "text": "CRISPR/Cas9 achieved 87% knockout efficiency...",
      "score": 0.94,
      "source": "both",
      "page": 8,
      "section": "Methods > Gene Editing",
      "is_table": false
    }
  ],
  "query_expansions": ["CRISPR/Cas9", "gene editing"],
  "search_metrics": {
    "vector_candidates": 30,
    "lexical_candidates": 30,
    "overlap_count": 12,
    "final_count": 10
  }
}
```

## 🔍 Validation Checklist

### RAG Pipeline

- [ ] Hybrid search returns both vector and lexical results
- [ ] Reranker properly scores and diversifies with MMR
- [ ] Query expansion works with ontology terms
- [ ] Confidence scoring prevents hallucinations
- [ ] Citations include page numbers and bounding boxes
- [ ] Tables and figures extracted correctly
- [ ] Low confidence triggers "insufficient evidence"
- [ ] Job queue processes embeddings asynchronously

### Performance

- [ ] Vector search <100ms for top-50
- [ ] Lexical search <50ms for top-50
- [ ] Reranking <200ms for 100 candidates
- [ ] Full pipeline <2s end-to-end
- [ ] 12 concurrent users supported
- [ ] HNSW index properly configured

### Quality

- [ ] Deduplication via normalized content hash works
- [ ] Tables and figures extracted correctly
- [ ] Section paths preserved in chunks
- [ ] References marked and down-weighted
- [ ] Per-page hashes enable incremental updates

## 🐛 Troubleshooting

### HNSW Index Issues

```bash
# Check HNSW configuration
docker exec -it pgvector-fts psql -U postgres -d agr_curation -c "
SELECT indexdef FROM pg_indexes
WHERE indexname LIKE '%hnsw%';
"

# Verify index performance
docker exec -it pgvector-fts psql -U postgres -d agr_curation -c "
EXPLAIN ANALYZE
SELECT * FROM pdf_embeddings
ORDER BY embedding <=> '[...]'::vector
LIMIT 50;
"
```

### Lexical Search Not Working

```bash
# Check tsvector index
docker exec -it pgvector-fts psql -U postgres -d agr_curation -c "
SELECT COUNT(*) FROM chunk_search
WHERE search_vector @@ plainto_tsquery('BRCA1');
"

# Rebuild lexical index if needed
python -m backend.lib.hybrid_search rebuild-lexical --pdf-id=<uuid>
```

### Job Queue Stuck

```bash
# Check job status
docker exec -it pgvector-fts psql -U postgres -d agr_curation -c "
SELECT job_type, status, COUNT(*)
FROM embedding_jobs
GROUP BY job_type, status;
"

# Reset stuck jobs
python -m backend.lib.job_queue reset --status=RUNNING --older-than=1h
```

### Low Confidence on Good Questions

```bash
# Check reranker scores
curl http://localhost:8002/api/v1/session/abc123/search \
  -d '{"query": "...", "rerank_top_k": 20}'

# Adjust confidence threshold
curl -X PUT http://localhost:8002/api/v1/settings/embeddings \
  -d '{"confidence_threshold": 0.6}'
```

## 📊 Monitoring & Metrics

### Performance Dashboard

```bash
curl http://localhost:8002/metrics

{
  "performance": {
    "avg_vector_search_ms": 85,
    "avg_lexical_search_ms": 42,
    "avg_rerank_ms": 156,
    "avg_pipeline_ms": 1823,
    "p95_pipeline_ms": 2145
  },
  "quality": {
    "total_queries": 1250,
    "high_confidence_ratio": 0.78,
    "low_confidence_count": 275,
    "no_answer_count": 23
  }
}
```

### Cost Tracking

```bash
# Session costs
curl http://localhost:8002/api/v1/session/abc123/cost

{
  "embedding_tokens": 15420,
  "llm_tokens": 8750,
  "total_cost_usd": 0.47
}
```

## 🎯 Success Criteria

1. ✅ Hybrid search combines vector + lexical effectively
2. ✅ Reranking with MMR provides diverse results
3. ✅ Query expansion improves recall for synonyms
4. ✅ Confidence thresholds prevent hallucinations
5. ✅ Citations are precise with page/bbox references
6. ✅ Tables and figures extracted and searchable
7. ✅ Job queue handles async processing reliably
8. ✅ Deduplication prevents redundant processing
9. ✅ Performance meets all latency targets
10. ✅ All PydanticAI agents working correctly

## 📚 Next Steps

1. Review [Enhanced Data Model](./data-model.md)
2. Check [API Contracts](./contracts/openapi.yaml)
3. Study [Research Decisions](./research.md)
4. Implement libraries with TDD
5. Create PydanticAI agents
6. Setup HNSW and lexical indexes
7. Build reranker with cross-encoder
8. Add confidence scoring
9. Implement job queue with LISTEN/NOTIFY
10. Create monitoring dashboard

---

**Support**: Check logs, use CLI tools, monitor metrics
**Key Innovation**: Hybrid search + reranking + guardrails = curator trust
