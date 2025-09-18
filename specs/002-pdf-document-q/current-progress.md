# Current Backend Progress (Unified RAG + Disease Specialist)

_Last updated: 2025-01-18_

## High-Level Status

- ✅ Unified pipeline now supports both PDF and ontology sources via `UnifiedRAGPipeline`.
- ✅ Disease specialist agent implemented; uses LLM-driven routing and mixes vector + relational lookups.
- ✅ Ontology ingestion parses DOID OBO into **both** vector chunks and normalized PostgreSQL tables, enabling hierarchy traversal.
- ✅ API (sync + SSE) surfaces specialist metadata while still returning a single, combined chat answer.
- ✅ Docker image refactored to multi-stage (`deps` + `runtime`) for faster rebuilds.
- ✅ Dockerized unit suite passes (`backend-test-unit`: 110 passed / 8 skipped, warnings only).
- 🔄 Remaining focus: expose ontology management endpoints/UI and extend router/spec agents for additional domains (gene/pathway).

## Key Components & Files

### Relational Ontology Storage

- `backend/app/models.py`
  - Added `OntologyTerm` and `OntologyTermRelation` (indexed by `ontology_type`, `source_id`, `term_id`).
  - `IngestionStatus` already tracks lifecycle (`not_indexed`, `indexing`, `ready`, `error`).
- `docker/migrations/003_create_ontology_tables.sql`
  - Creates tables + indexes for normalized ontology metadata and relations.

### Ontology Ingestion

- `backend/app/jobs/ingest_ontology.py`
  - Parses `doid.obo.txt`; generates `UnifiedChunk` entries **and** normalized rows.
  - Samples summary metrics (inserted counts, deletions, embeddings) into ingestion status.
  - **Next**: provide CLI/API wrappers to list/read ingestion status, trigger reindex with `source_id` and new file path.
- `backend/tests/unit/test_ontology_ingest.py`
  - Integration-style unit test bootstraps the DB, runs ingestion, and asserts that:
    - Terms are stored in `ontology_terms`.
    - Relationships (parent/child) land in `ontology_term_relations`.
    - Vector chunks saved in `unified_chunks`.

### Disease Specialist & Router

- `backend/app/agents/disease_ontology_agent.py`
  - `lookup_diseases()` inspects `query_mode` from metadata:
    - `vector_search`: current unified RAG flow (vector + rerank).
    - `term_lookup`: direct term metadata (`OntologyTerm`).
    - `hierarchy_lookup`: fetches parents/children via `OntologyTermRelation`.
  - Returns combined answer + relational hierarchy when available.
- `backend/app/orchestration/general_supervisor.py`
  - `IntentAnalysis` expanded with `query_mode`.
  - `analyze_intent()` calls `get_intent_router()` (a PydanticAI Agent) with a structured prompt describing modes.
  - Specialist nodes save findings in `state.specialist_results`, aggregated by `general_answer`, which now appends “Specialist Findings” to the prompt/context before the final LLM call.
  - Metadata merges specialist info, citations deduplicated.
- `backend/app/services/unified_pipeline_service.py`
  - Registers PDF + ontology (when file present) with the new pipeline.

### API & Streaming

- `backend/app/routers/rag_endpoints.py`
  - `QuestionResponse` now returns `specialist_results` + `specialists_invoked`.
  - Streaming SSE emits these fields in the `final` event.
  - Stored retrieval stats include specialist metadata for later inspection.
- `backend/tests/unit/test_rag_endpoints.py`
  - Stubs router/specialist to ensure sync & streaming paths surface combined results.

### LangGraph Tests

- `backend/tests/unit/test_langgraph_state.py`, `test_langgraph_supervisor.py`
  - Router stub returns `IntentAnalysis` with `query_mode` to validate branching.
  - Specialists produce hierarchical metadata; state verifies `metadata["query_mode"]` persists.

### Supporting Utilities

- `backend/lib/pipelines/unified_pipeline.py`, `document_source.py`, `query_embedder.py`
  - Implement source registry, context boosting, and shared query embedding.
- `backend/lib/{hybrid_search,vector_search,lexical_search}.py`
  - Parameterized for `source_type/source_id`; fetch metadata from `unified_chunks` when non-PDF.
- `backend/app/services/langgraph_runner.py`
  - `stream()` converts graph `astream_events` to SSE-friendly dictionaries (`agent_start`, `delta`, `final`, etc.).

### Docker

- `docker/Dockerfile.backend`
  - Stage `deps` installs apt + pip requirements (cached).
  - `runtime` copies code; unit-compose targets reuse cached deps.

## Open TODOs (for next session)

1. **Ontology management API/UI**
   - Endpoints to list ingestion status (`ingestion_status` table), trigger reindex for a specific `source_id`, and inspect version metadata.
   - Optional: record DOID file version/hash alongside status for change tracking.
2. **Expose relational info to frontend**
   - Ensure `/api/rag/...` responses (and SSE) include parent/child lists when `hierarchy_lookup` used so UI can render tree.
   - Decide on payload format (`specialist_results["disease_ontology"]["hierarchy"]`).
3. **Router prompt tuning**
   - Refine `get_intent_router()` prompt to better separate term vs hierarchy requests (provide few-shot examples).
   - Consider caching router responses if we see repeated queries.
4. **Additional specialists** (future)
   - Similar pattern for GO/Pathway; create relational ingestion (GO DAG) and agent wrappers.
5. **UI indicators**
   - Frontend work: display “ontology consulted” chip and optionally allow expanding parent/child tree.
6. **Tests/coverage**
   - Add integration tests covering `/api/rag/...` SSE with actual pipeline once ingestion runs inside docker-compose.

## Quick Reference – Important Paths

- Models: `backend/app/models.py`
- Ontology ingress: `backend/app/jobs/ingest_ontology.py`
- Disease agent: `backend/app/agents/disease_ontology_agent.py`
- Unified pipeline: `backend/lib/pipelines/*`
- LangGraph supervisor: `backend/app/orchestration/general_supervisor.py`
- API router: `backend/app/routers/rag_endpoints.py`
- Tests: `backend/tests/unit/test_ontology_ingest.py`, `.../test_langgraph_state.py`, `.../test_rag_endpoints.py`
- Docker: `docker/Dockerfile.backend`, `docker/migrations/003_create_ontology_tables.sql`

## Command Cheatsheet

```bash
# rebuild backend images with cached deps
docker compose --progress=plain -f docker-compose.yml -f docker-compose.test.yml build backend backend-test backend-test-unit

# run unit suite with full LangGraph/pydantic-ai dependencies
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm backend-test-unit

# ingest DOID ontology (example)
docker compose run --rm backend-test-python python -m app.jobs.ingest_ontology --type disease --source-id unit --obo-path doid.obo.txt
```

---

Ready to resume: implement ontology management endpoints + integrate new relational capabilities into the API/front-end.
