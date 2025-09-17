# Unified RAG + Multi-Agent Final Implementation Guide

> **Document family note**: This blueprint supersedes overlapping instructions in `unified-rag-pipeline-guide.md` and `multi-agent-implementation-guide.md`. Treat those documents as historical context unless they add details not captured here.

**Version**: 1.0.0  
**Date**: 2025-01-17  
**Status**: Implementation Blueprint  
**Primary Owners**: AGR AI Curation Engineering

---

## 1. Executive Summary

This document consolidates the unified RAG pipeline initiative with the context-aware multi-agent routing effort. The current system only supports PDF-centric retrieval and a single generalist agent. We will introduce a source-agnostic retrieval layer, shared ingestion pipeline, and LangGraph-based supervisor that routes queries to specialist agents (disease, gene, pathway, etc.) using document-aware intent analysis. Implementation spans database schema updates, ingestion services, retrieval pipeline refactors, orchestrator enhancements, and API/streaming changes. All verification will continue through the docker-compose environment.

---

## 2. Goals & Non-Goals

**Goals**

- Support arbitrary knowledge sources (PDF, OBO, CSV, databases) through a unified ingestion and retrieval stack.
- Expose RAG context to the LangGraph supervisor so routing decisions are grounded in actual document content.
- Add specialist agents that consume the shared pipeline and report structured results/citations.
- Maintain backward compatibility for existing PDF workflows during the rollout.
- Preserve streaming UX while surfacing multi-agent lifecycle events to the frontend.

**Non-Goals**

- Replacing the existing PDF upload/processing workflow.
- Implementing production caching or advanced performance tuning beyond the outlined optimizations.
- Shipping new frontend UI; this guide focuses on backend contract changes that the frontend can adopt incrementally.

---

## 3. Current State Assessment

| Area                   | Findings                                                                                                              | Evidence                                                                                                  |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Retrieval pipeline     | `GeneralPipeline` hardcodes `pdf_id` and invokes `HybridSearch` against PDF tables.                                   | `backend/lib/pipelines/general_pipeline.py:65`                                                            |
| Hybrid search plumbing | `HybridSearch` reads directly from `pdf_chunks`; `VectorSearch`/`LexicalSearch` are PDF-specific.                     | `backend/lib/hybrid_search.py:250`, `backend/lib/vector_search.py:33`, `backend/lib/lexical_search.py:27` |
| Embeddings & ingestion | `EmbeddingService.embed_pdf` only handles PDF chunks; ingestion service writes only to `pdf_chunks` + `chunk_search`. | `backend/lib/embedding_service.py:45`, `backend/app/services/pdf_ingest_service.py:44`                    |
| Orchestrator           | `PreparedRequest` returns prompt/deps/citations but not chunk texts or embeddings.                                    | `backend/app/agents/main_orchestrator.py:68`                                                              |
| LangGraph supervisor   | State lacks retrieved context; analyzer is stubbed and always routes to general agent.                                | `backend/app/orchestration/general_supervisor.py:16`                                                      |
| Streaming endpoint     | Streams directly from general agent, bypassing LangGraph, so no multi-agent events.                                   | `backend/app/routers/rag_endpoints.py:95`                                                                 |
| Disease ontology data  | DOID file present but unused; no ingestion or index status tracking.                                                  | `doid.obo.txt`                                                                                            |

These gaps prevent multi-source retrieval, specialist routing, and ontology support.

---

## 4. Target Architecture Overview

1. **Data Layer**
   - `unified_chunks` table stores all chunked content with `source_type` and `source_id` columns, companion indices (HNSW + GIN), and optional `unified_embeddings` view or inline vector column.
   - Ingestion jobs (CLI/Celery) parse raw sources (PDFs, OBO, CSV, APIs) and persist normalized chunks, then call a generalized embedding helper.
   - `ingestion_status` table (or materialized view) records per-source lifecycle (`NOT_INDEXED`, `INDEXING`, `READY`, `ERROR`).

2. **Pipeline Layer**
   - `DocumentSource` abstraction defines `ingest`, `index_status`, `registration`, and `format_citation` methods.
   - `UnifiedRAGPipeline` registers document sources, applies config overrides, performs hybrid search keyed by `source_type/source_id`, supports optional context boosting, and returns `UnifiedPipelineOutput` chunks with citations delegated to each source.
   - PDF adapter becomes a thin wrapper that reuses uploaded data through the new unified interface.

3. **Multi-Agent Layer**
   - `PDFQAState` extended with `retrieved_chunks`, `retrieved_context`, `chunk_embeddings`, `detected_entities`, `specialist_results`, and routing telemetry.
   - `retrieve_context` node runs the pipeline once and stores normalized outputs.
   - `analyze_intent` uses question + context to produce structured routing decisions and detected entities; returns LangGraph `Command` for branching.
   - Specialist nodes (disease, gene, pathway, general QA, parallel aggregator) invoke the unified pipeline and enrich state with results/citations.
   - `synthesize_answer` collates specialist answers, generates final response, and merges citations.

4. **API & Streaming Layer**
   - REST endpoint always invokes LangGraph (even for streaming) to centralize orchestration.
   - SSE payloads emit `agent_start`, `delta`, `agent_finish`, `final`, and `error` events with consistent metadata for frontend telemetry.
   - `LangGraphRunRepository` records per-node metrics for observability.

5. **Configuration & Deployment**
   - Environment settings expanded to include default document sources, ontology paths, ingestion batch sizes, and optional caching toggles.
   - Docker compose remains the integration harness; migrations run within backend container startup.

---

## 5. Detailed Implementation Workstreams

### 5.1 Database & Models

- Create Alembic migration for `unified_chunks` with vector/tsearch columns and composite uniqueness (`source_type`, `source_id`, `chunk_id`).
- Optionally extend `pdf_embeddings` or introduce `unified_embeddings` for normalized storage; ensure pgvector indices are configured.
- Add SQLAlchemy models for new tables and update relationships in `app/models.py`.
- Introduce ingestion status model keyed on `source_type/source_id` with timestamps and failure reasons.

### 5.2 Ingestion Services

- Implement `DocumentSource` base class in `backend/lib/pipelines/document_source.py` per spec.
- Refactor `PDFIngestService` to emit entries into `unified_chunks` in addition to legacy tables (or add a follow-up ETL job).
- Create `OntologyDocumentSource` that parses `doid.obo.txt`, extracts IDs/names/definitions/synonyms, and stores narrative-formatted chunks.
- Provide CLI jobs (`backend/lib/cli/...`) for running ingestion: e.g., `python -m app.jobs.ingest_ontology --type disease --source-id all`.
- Extend `EmbeddingService` with `embed_unified_chunks(source_type, source_id, model_name, ...)` that mirrors `embed_pdf` but targets the unified table.

### 5.3 Retrieval Pipeline

- Replace `GeneralPipeline` usage with `UnifiedRAGPipeline`; keep a compatibility shim returning PDF-focused outputs until all call sites migrate.
- Update `HybridSearch`, `VectorSearch`, and `LexicalSearch` signatures to accept `source_type/source_id` and dynamically choose the appropriate table/view.
- Introduce context boost helper inside the pipeline for entity-aware reranking when `context` is supplied.
- Provide configuration overrides for each registered source (vector/lexical top-k, rerank parameters, MMR lambda).

### 5.4 Orchestrator & LangGraph

- Expand `GeneralOrchestrator.PreparedRequest` to expose chunk texts, embeddings (if available), and formatted context string.
- Update `PDFQAState` schema to include new fields (retrieved context, chunk metadata, specialist outputs).
- Implement LangGraph nodes per the multi-agent guide: `retrieve_context`, `analyze_intent_with_context`, specialist nodes, `parallel_specialists`, and `synthesize_answer`.
- Update supervisor compilation to register new nodes, edges, and default config injection.
- Integrate `LangGraphRunRepository` hooks to log node start/finish, capturing routing decisions and latencies.

### 5.5 Specialist Agents

- Implement `DiseaseOntologyAgent` leveraging `OntologyDocumentSource` and the unified pipeline.
- Mirror pattern for `GeneOntologyAgent` and `PathwayAgent`; include helper functions to format natural-language summaries and citations.
- Add lightweight entity extraction alignment (reuse `entity_extraction_agent` outputs) to feed detected entities into intent analysis.

### 5.6 API & Streaming

- Modify `/api/rag/sessions/{id}/question` to always call the LangGraph runner; for streaming, wrap `graph.astream_events` (or equivalent) to emit SSE events with consistent metadata.
- Ensure streaming includes orchestrator metadata (retrieved chunk count, routing confidence) incrementally so frontend can display progress.
- Update synchronous response to include combined citations from all specialists.

### 5.7 Configuration & Ops

- Extend `Settings` with ontology file paths, default source registrations, and toggles for multi-agent routing.
- Document how to register new sources via environment variables or configuration files.
- Provide fallback behavior when ingestion status is not `READY` (e.g., degrade to general agent with warning metadata).

---

## 6. File-by-File Change Checklist

| Component                                                        | Planned Adjustments                                                                                                                                                 |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/app/models.py`                                          | Add `UnifiedChunk`, `UnifiedEmbedding`, `IngestionStatus`. Preserve existing PDF tables until retirement.                                                           |
| `backend/lib/pipelines`                                          | Introduce `document_source.py`, `unified_pipeline.py`, and adapt `__init__`. Provide compatibility wrapper for current `build_general_pipeline`.                    |
| `backend/lib/hybrid_search.py`                                   | Parameterize by `source_type/source_id`, swap direct table references for unified views, and include metadata retrieval for ontologies.                             |
| `backend/lib/vector_search.py` & `backend/lib/lexical_search.py` | Query unified tables/views, accept dynamic filters, retain PDF code path for migration.                                                                             |
| `backend/lib/embedding_service.py`                               | Add `embed_unified_chunks`, share batching logic, ensure `EmbeddingModelConfig` can be reused.                                                                      |
| `backend/app/services`                                           | Build ontology ingestion job, update orchestrator service to return new pipeline instance, extend LangGraph runner to support streaming events.                     |
| `backend/app/agents`                                             | Add DocumentSource-based agents, extend `main_orchestrator` to expose extra context, adjust factory to register new specialists.                                    |
| `backend/app/orchestration/general_supervisor.py`                | Expand state schema, add new nodes, wire `Command`-based routing, integrate telemetry logging.                                                                      |
| `backend/app/routers/rag_endpoints.py`                           | Route all requests through LangGraph, manage SSE event types, include routing metadata in responses.                                                                |
| `backend/tests`                                                  | Introduce unit tests for `UnifiedRAGPipeline`, ontology ingestion, and LangGraph routing; add integration tests covering multi-agent flows via dockerized Postgres. |

---

## 7. Testing Strategy

1. **Unit Tests**
   - Pipeline: verify multi-source search, context boosting, citation formatting.
   - Ingestion: confirm OBO parser normalization and ingestion status transitions.
   - Intent analyzer: mock context to ensure routing decisions fall back gracefully when confidence < 0.5.

2. **Integration Tests**
   - Docker-backed Postgres tests for unified schema migrations and ingestion jobs.
   - LangGraph runner tests ensuring `retrieve_context → analyze_intent → specialist → synthesize` path completes with expected state.
   - Streaming endpoint contract tests verifying ordered SSE events.

3. **End-to-End Validation**
   - Run `docker compose up --build` and execute sample questions that trigger each specialist (e.g., disease ontology lookups using DOID data).
   - Monitor `LangGraphRunRepository` entries for per-node latency and specialist invocation auditing.

4. **Regression**
   - Execute existing PDF QA integration tests to confirm backward compatibility while the PDF adapter migrates.

---

## 8. Deployment & Migration Plan

1. Apply database migrations (unified tables + status tracking) via backend container startup.
2. Backfill existing PDF chunks into `unified_chunks` (one-off script or migration) to ensure PDFs remain searchable through the new pipeline.
3. Deploy new ingestion jobs; ingest DOID ontology before enabling routing to the disease specialist.
4. Gradually roll out multi-agent routing behind a feature flag (`ENABLE_MULTI_AGENT=true`) to allow safe fallback to the general agent.
5. Update frontend to handle new SSE events and display specialist outputs; coordinate API contract changes with QA testing.
6. Monitor runtime metrics and ingestion status dashboards after deployment; ensure error handling downgrades gracefully on missing indices.

---

## 9. Risks & Mitigations

| Risk                                                | Mitigation                                                                                                             |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Large ingestion jobs impacting Postgres performance | Batch inserts, leverage COPY where possible, run ingestion offline, monitor transaction size.                          |
| Routing model drift or failures                     | Enforce confidence threshold fallback to general agent; log intent analyzer errors and expose metrics.                 |
| Streaming regressions                               | Maintain fallback synchronous path, add integration tests simulating SSE clients, ensure timeouts on streaming agents. |
| Backward compatibility with existing PDFs           | Keep PDF adapter registered by default; only remove old pipeline once unified path is battle-tested.                   |
| Ontology data quality                               | Validate OBO parsing (IDs, synonyms) and provide ingestion status alerts for malformed entries.                        |

---

## 10. Next Steps

1. Finalize database migration scripts and models.
2. Implement `DocumentSource` abstraction with PDF + disease ontology adapters.
3. Refactor pipeline/search utilities to honor `source_type/source_id`.
4. Extend LangGraph supervisor and orchestrator to support retrieve-then-route flow.
5. Wire up API streaming to new supervisor events and expand tests.
6. Schedule ingestion of `doid.obo.txt` and verify end-to-end disease specialist responses.

This guide will evolve as features land; keep change logs in this directory to track incremental updates.
