# Tasks: PDF Document Q&A with Enhanced RAG

**Input**: Design documents from `/specs/002-pdf-document-q/`
**Prerequisites**: plan.md (required), research.md, data-model.md, contracts/openapi.yaml, quickstart.md

## Execution Flow

```
1. Core Infrastructure (P1: T001-T015) - Must complete first
2. Hybrid Search & Reranking (P2: T016-T030) - Critical for quality
3. RAG Pipeline with Guardrails (P3: T031-T040) - Core functionality
4. API & Frontend (P4: T041-T050) - User-facing components
5. Polish & Monitoring (P5: T051-T055) - Production readiness
```

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Tests marked with **[TDD-RED]** must be written to fail first
- Implementation marked with **[TDD-GREEN]** makes tests pass

## Priority 1: Core Infrastructure (Week 1)

### Database Setup & Migrations

- [x] T001 Create PostgreSQL docker setup with pgvector extension ~~in `docker/postgres/Dockerfile`~~ using `pgvector/pgvector:pg16` image
- [x] T002 [P] Write tests for database models in `backend/tests/unit/test_models.py` **[TDD-RED]**
- [x] T003 Create SQLAlchemy models for all 11 entities in `backend/app/models/pdf_models.py` **[TDD-GREEN]**
- [x] T004 [P] ~~Write HNSW index configuration tests~~ Indexes defined in models **[COMPLETED]**
- [x] T005 ~~Create Alembic migration~~ Direct table creation via `Base.metadata.create_all()` **[NO MIGRATION NEEDED]**

### PDF Processing Library

- [x] T006 [P] Write pdf-processor library tests in `backend/tests/unit/test_pdf_processor.py` **[TDD-RED]**
- [x] T007 Implement pdf-processor with Unstructured.io in `backend/lib/pdf_processor.py` **[TDD-GREEN]**
- [x] T008 [P] Write chunk-manager tests with layout preservation in `backend/tests/unit/test_chunk_manager.py` **[TDD-RED]**
- [x] T009 Implement chunk-manager with semantic boundaries in `backend/lib/chunk_manager.py` **[TDD-GREEN]**
- [x] T010 [P] Create CLI interfaces for pdf-processor and chunk-manager in `backend/lib/cli/pdf_cli.py`

### Job Queue System

- [x] T011 [P] Write job queue tests with LISTEN/NOTIFY in `backend/tests/unit/test_job_queue.py` **[TDD-RED]**
- [x] T012 Implement Postgres-based job queue in `backend/lib/job_queue.py` **[TDD-GREEN]**
- [x] T013 [P] Write worker pool tests in `backend/tests/unit/test_workers.py` **[TDD-RED]**
- [x] T014 Implement job workers with rate limiting in `backend/app/workers/embedding_worker.py` **[TDD-GREEN]**
- [x] T015 [P] Create job monitoring CLI in `backend/lib/cli/job_cli.py`

## Priority 2: Hybrid Search & Reranking (Week 1-2)

### Embedding Service

- [x] T016 [P] Write embedding service tests with versioning in `backend/tests/unit/test_embedding_service.py` **[TDD-RED]**
- [x] T017 Implement multi-model embedding service in `backend/lib/embedding_service.py` **[TDD-GREEN]**
- [x] T018 [P] Write batch processing tests in `backend/tests/unit/test_batch_embeddings.py` **[TDD-RED]**
- [x] T019 Implement embedding batch processor in `backend/lib/batch_processor.py` **[TDD-GREEN]**
- [x] T020 [P] Create embedding CLI with status tracking in `backend/lib/cli/embedding_cli.py`

### Hybrid Search Implementation

- [x] T021 [P] Write vector search tests (<100ms) in `backend/tests/unit/test_vector_search.py` **[TDD-RED]**
- [x] T022 Implement HNSW vector search in `backend/lib/vector_search.py` **[TDD-GREEN]**
- [x] T023 [P] Write lexical search tests (<50ms) in `backend/tests/unit/test_lexical_search.py` **[TDD-RED]**
- [x] T024 Implement tsvector lexical search in `backend/lib/lexical_search.py` **[TDD-GREEN]**
- [x] T025 [P] Create hybrid search orchestrator with result merging in `backend/lib/hybrid_search.py`

### Reranker with MMR

- [x] T026 [P] Write reranker tests with cross-encoder in `backend/tests/unit/test_reranker.py` **[TDD-RED]**
- [x] T027 Implement PydanticAI reranking agent in `backend/lib/reranker.py` **[TDD-GREEN]**
- [x] T028 [P] Write MMR diversification tests (λ=0.7) in `backend/tests/unit/test_mmr.py` **[TDD-RED]**
- [x] T029 Implement MMR algorithm in `backend/lib/mmr_diversifier.py` **[TDD-GREEN]**
- [x] T030 [P] Create reranker CLI with performance metrics in `backend/lib/cli/rerank_cli.py`

## Priority 3: Multi-Agent RAG System (Week 2)

### Specialized Agents & Pipelines

- [x] T031 [P] Write pipeline output models tests in `backend/tests/unit/test_pipeline_models.py` **[TDD-RED]**
- [x] T032 [P] Implement pipeline output models in `backend/app/agents/pipeline_models.py` **[TDD-GREEN]**
  - DiseasePipelineOutput, GenePipelineOutput, PathwayPipelineOutput, ChemicalPipelineOutput
- [ ] T033 [P] Write Disease Agent tests in `backend/tests/unit/test_disease_agent.py` **[TDD-RED]**
- [ ] T034 Implement Disease Agent in `backend/app/agents/disease_agent.py` **[TDD-GREEN]**
- [ ] T035 [P] Write Gene Agent tests in `backend/tests/unit/test_gene_agent.py` **[TDD-RED]**
- [ ] T036 Implement Gene Agent in `backend/app/agents/gene_agent.py` **[TDD-GREEN]**
- [ ] T037 [P] Write Disease Pipeline tests in `backend/tests/unit/test_disease_pipeline.py` **[TDD-RED]**
- [ ] T038 Implement Disease Pipeline in `backend/lib/pipelines/disease_pipeline.py` **[TDD-GREEN]**
  - Integrates hybrid search, filtering, ontology matching
- [ ] T039 [P] Write Gene Pipeline tests in `backend/tests/unit/test_gene_pipeline.py` **[TDD-RED]**
- [ ] T040 Implement Gene Pipeline in `backend/lib/pipelines/gene_pipeline.py` **[TDD-GREEN]**

### Orchestrator & Integration

- [x] T041 [P] Write Main Orchestrator tests in `backend/tests/unit/test_orchestrator.py` **[TDD-RED]**
- [x] T042 Implement Main Orchestrator in `backend/app/agents/main_orchestrator.py` **[TDD-GREEN]**
- [ ] T043 [P] Write orchestrator intent detection tests in `backend/tests/unit/test_intent_detection.py` **[TDD-RED]**
- [ ] T044 Implement intent detection in orchestrator tools **[TDD-GREEN]**
- [ ] T045 [P] Write full pipeline integration tests in `backend/tests/integration/test_full_pipeline.py` **[TDD-RED]**

## Priority 4: API & Frontend (Week 2-3)

### FastAPI Endpoints

- [ ] T046 [P] Write contract tests for all 12 endpoints in `backend/tests/contract/test_api_contracts.py` **[TDD-RED]**
- [ ] T047 Implement PDF upload endpoint with deduplication in `backend/app/api/pdf_endpoints.py` **[TDD-GREEN]**
- [x] T048 Implement session management endpoints in `backend/app/api/session_endpoints.py` **[TDD-GREEN]**
- [x] T049 Implement RAG question endpoint with streaming in `backend/app/api/rag_endpoints.py` **[TDD-GREEN]**
- [ ] T050 Implement job management endpoints in `backend/app/api/job_endpoints.py` **[TDD-GREEN]**

### React Components

- [ ] T051 [P] Write component tests for PDF upload in `frontend/src/components/__tests__/PDFUpload.test.tsx` **[TDD-RED]**
- [ ] T052 [P] Implement PDFUpload with progress tracking in `frontend/src/components/PDFUpload.tsx` **[TDD-GREEN]**
- [ ] T053 [P] Write ChatInterface tests with streaming in `frontend/src/components/__tests__/ChatInterface.test.tsx` **[TDD-RED]**
- [ ] T054 [P] Implement ChatInterface with confidence indicators in `frontend/src/components/ChatInterface.tsx` **[TDD-GREEN]**
- [ ] T055 [P] Implement CitationDisplay with bbox highlighting in `frontend/src/components/CitationDisplay.tsx` **[TDD-GREEN]**

## Priority 5: Polish & Monitoring (Week 3)

### Observability & Performance

- [ ] T056 [P] Implement structured logging with correlation IDs in `backend/app/core/logging.py`
- [ ] T057 [P] Create metrics collection for all services in `backend/app/core/metrics.py`
- [ ] T058 [P] Write performance benchmarks in `backend/tests/performance/test_benchmarks.py`
- [ ] T059 Implement health check endpoints in `backend/app/api/system_endpoints.py`
- [ ] T060 Create monitoring dashboard configuration in `docker/monitoring/prometheus.yml`

## Dependencies Graph

```
Database Setup (T001-T005) ──┬──> Embedding Service (T016-T020)
                             │
PDF Processing (T006-T010) ──┼──> Hybrid Search (T021-T025)
                             │
Job Queue (T011-T015) ───────┴──> Reranker (T026-T030)
                                        │
                                        v
                              PydanticAI Agents (T031-T040)
                                        │
                                        v
                              API Endpoints (T041-T045)
                                        │
                                        v
                              Frontend Components (T046-T050)
                                        │
                                        v
                              Monitoring (T051-T055)
```

## Parallel Execution Examples

### Week 1 - Infrastructure Sprint

```bash
# Launch T002, T004, T006, T008, T011, T013 together (all test files):
Task subagent_type="general-purpose" prompt="Write failing test for database models in backend/tests/unit/test_models.py following TDD-RED phase"
Task subagent_type="general-purpose" prompt="Write failing test for HNSW indexes in backend/tests/unit/test_indexes.py following TDD-RED phase"
Task subagent_type="general-purpose" prompt="Write failing test for pdf-processor in backend/tests/unit/test_pdf_processor.py following TDD-RED phase"
Task subagent_type="general-purpose" prompt="Write failing test for chunk-manager in backend/tests/unit/test_chunk_manager.py following TDD-RED phase"
Task subagent_type="general-purpose" prompt="Write failing test for job queue in backend/tests/unit/test_job_queue.py following TDD-RED phase"
Task subagent_type="general-purpose" prompt="Write failing test for worker pool in backend/tests/unit/test_workers.py following TDD-RED phase"
```

### Week 1-2 - Search & Reranking Sprint

```bash
# Launch T016, T018, T021, T023, T026, T028 together (all test files):
Task subagent_type="general-purpose" prompt="Write failing test for embedding service in backend/tests/unit/test_embedding_service.py"
Task subagent_type="general-purpose" prompt="Write failing test for batch embeddings in backend/tests/unit/test_batch_embeddings.py"
Task subagent_type="general-purpose" prompt="Write failing test for vector search <100ms in backend/tests/unit/test_vector_search.py"
Task subagent_type="general-purpose" prompt="Write failing test for lexical search <50ms in backend/tests/unit/test_lexical_search.py"
Task subagent_type="general-purpose" prompt="Write failing test for reranker in backend/tests/unit/test_reranker.py"
Task subagent_type="general-purpose" prompt="Write failing test for MMR λ=0.7 in backend/tests/unit/test_mmr.py"
```

### Week 2 - PydanticAI Agents Sprint

```bash
# Launch T031, T033, T035, T037 together (all agent test files):
Task subagent_type="general-purpose" prompt="Write failing test for QueryExpansionAgent in backend/tests/unit/test_query_expansion.py"
Task subagent_type="general-purpose" prompt="Write failing test for HybridSearchAgent in backend/tests/unit/test_search_agent.py"
Task subagent_type="general-purpose" prompt="Write failing test for AnswerValidationAgent in backend/tests/unit/test_validation_agent.py"
Task subagent_type="general-purpose" prompt="Write failing test for citation enforcement in backend/tests/unit/test_citations.py"
```

### Week 2-3 - Frontend Components Sprint

```bash
# Launch T046, T048 together (component tests):
Task subagent_type="general-purpose" prompt="Write failing test for PDFUpload component in frontend/src/components/__tests__/PDFUpload.test.tsx"
Task subagent_type="general-purpose" prompt="Write failing test for ChatInterface component in frontend/src/components/__tests__/ChatInterface.test.tsx"
```

## Critical Implementation Notes

1. **TDD Enforcement**: Every test marked [TDD-RED] MUST fail before implementation
2. **Performance Targets**:
   - Vector search: <100ms for top-50
   - Lexical search: <50ms for top-50
   - Reranking: <200ms for 100 candidates
   - Full pipeline: <2s end-to-end
3. **Confidence Threshold**: 0.7 default, configurable per session
4. **MMR Lambda**: 0.7 for diversity/relevance balance
5. **HNSW Parameters**: m=16, ef_construction=200, ef_search=64
6. **Batch Sizes**: 64 for embeddings, 100 for search candidates
7. **Job Queue**: LISTEN/NOTIFY on channel 'embedding_queue'
8. **All PydanticAI agents** must use structured outputs with validation
9. **RAG Pipeline Flow**:
   - MainOrchestratorAgent analyzes intent and dispatches to pipelines
   - Domain-specific pipelines perform hybrid search (vector + lexical)
   - Pipelines perform reranking and MMR diversification
   - Pipelines pass pre-processed data to specialized domain agents
   - Specialized agents (Disease, Gene, Pathway, etc.) synthesize expert answers

## Validation Checklist

- [x] All 12 API endpoints have contract tests (T041)
- [x] All 11 entities have model creation tasks (T003)
- [x] All tests come before implementation (TDD-RED → TDD-GREEN)
- [x] Parallel tasks ([P]) modify different files
- [x] Each task specifies exact file path
- [x] Dependencies properly sequenced
- [x] All 8 libraries have implementation tasks
- [x] All 8 libraries have CLI interfaces

## Success Metrics

- **P1 Complete**: Database migrated, PDFs extractable, jobs queueable
- **P2 Complete**: Hybrid search <200ms total, reranking functional
- **P3 Complete**: Confidence scoring prevents hallucinations, citations work
- **P4 Complete**: End-to-end upload→question→answer flow works
- **P5 Complete**: All metrics visible, performance targets met

---

**Total Tasks**: 60
**Estimated Duration**: 3 weeks
**Team Size**: 1-2 developers
**Parallel Potential**: High (many [P] tasks)
