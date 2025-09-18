# MVP Roadmap: Minimal PDF Q&A Chatbot

This document outlines the **minimum set of tasks** required to deliver a working PDF chatbot that accepts a paper upload and answers free-form questions. It is derived from `tasks.md` in the same directory, but narrows the scope to the essentials, skipping domain-specialized pipelines.

## Current Status (2025-01-14)

✅ Completed foundations (T001–T030):

- Database schema + SQLAlchemy models with pgvector
- PDF processing with Unstructured.io (extraction, chunking)
- Embedding service & batch processor
- Hybrid search (vector + lexical) and reranker + MMR implementation
- Job queue infrastructure and CLI tooling

## Goal: Minimal LangGraph Chat Flow

Deliver “upload PDF → ask question → streamed answer with citations” orchestrated by a LangGraph supervisor that wraps our existing PydanticAI agents, keeping the specialist branches optional for later.

## MVP Scope

### 1. LangGraph Supervisor Orchestrator

- [x] Implement simple pipeline output model (`GeneralPipelineOutput`) and supporting data classes.
- [x] Implement general-purpose PydanticAI answer agent reused inside LangGraph nodes.
- [ ] Wrap the existing orchestrator flow in a LangGraph `general_supervisor` graph with explicit state class + checkpointer.
- [ ] Add LangGraph `IntentRouter` node (currently routes to general path only) so we can later expand to specialists.
- [ ] Capture LangGraph execution metadata (specialists invoked, timing) and persist via new tables.
- [ ] Extend unit tests to cover LangGraph node execution lifecycle (mocking parallel edges until implemented).

Mapping to tasks.md: tracked via T031–T045 plus new LangGraph entries T061–T065 for supervisor graph wiring.

### 2. Essential API Endpoints

Implement and test (FastAPI):

- [x] `POST /pdf` – upload PDF, persist metadata, enqueue processing (T047)
- [x] `POST /sessions` – create chat sessions tied to a PDF (T048 simplified)
- [x] `POST /sessions/{id}/question` – call LangGraph supervisor adapter and return answer JSON (T049; streaming upgrade pending)
- [ ] (Optional) `GET /jobs/{id}` – track embedding jobs (T050)
- [x] Upgrade `/sessions/{id}/question` to SSE once LangGraph edges stream tokens (depends on T064).

Contract tests from T046 should accompany these endpoints.

### 3. Frontend Glue

Update React components to hit new endpoints:

- [x] `PDFUpload` component: call `/pdf`, show progress/state (T052). Write component test (T051).
- [x] `ChatInterface`: manage session, send questions, render answer/citations via REST (T053/T054).
- [ ] Citation UI (T055) can be simplified or deferred; basic inline citation listing is acceptable for MVP.
- [ ] Surface LangGraph execution metadata in chat header (e.g., specialist badges once available).

### 4. Configuration & Dependencies

- Ensure LLM credentials (OpenAI/Gemini) are configurable via `.env` **and stored/edited through the admin panel** (`settings` table).
- Use the OpenAI embeddings API for both PDF ingestion and query-time retrieval, with model name/version controlled via admin settings (defaults seeded from `.env`).
- Add LangGraph workflow + version controls to the admin panel (select workflow, toggle human-gate, choose checkpointer backend).
- Document minimal setup in `quickstart.md` updates (env variables, docker build, LangGraph services).

### 5. Smoke Test / Validation

- Add integration test: upload fixture PDF → wait for embedding job → post question → assert non-empty answer with citation list.
- Add LangGraph supervisor test calling `general_supervisor.app.ainvoke` with mock dependencies to ensure state persists.
- Confirm CLI entry (`python -m backend.lib.reranker rerank`) still works.

## Nice-to-Have After MVP

- Specialized pipelines and agents (T033–T040)
- Advanced observability (T056–T060)
- Citation highlight UI (T055)
- Streaming SSE refinements & job dashboard

## Summary Sequence

1. Build general pipeline output model + LangGraph-wrapped PydanticAI orchestrator.
2. Expose upload/session/question endpoints with tests that call the LangGraph supervisor adapter.
3. Hook frontend Upload & Chat components to the new API and surface supervisor metadata.
4. Run end-to-end test (including LangGraph ainvoke) to confirm question answering works.

Once these are complete, the chatbot will answer questions about uploaded PDFs, providing a true MVP before layering in domain-specific or monitoring features.
