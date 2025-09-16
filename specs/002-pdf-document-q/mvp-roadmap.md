# MVP Roadmap: Minimal PDF Q&A Chatbot

This document outlines the **minimum set of tasks** required to deliver a working PDF chatbot that accepts a paper upload and answers free-form questions. It is derived from `tasks.md` in the same directory, but narrows the scope to the essentials, skipping domain-specialized pipelines.

## Current Status (2025-01-14)

✅ Completed foundations (T001–T030):

- Database schema + SQLAlchemy models with pgvector
- PDF processing with Unstructured.io (extraction, chunking)
- Embedding service & batch processor
- Hybrid search (vector + lexical) and reranker + MMR implementation
- Job queue infrastructure and CLI tooling

## Goal: Minimal Chat Flow

Deliver “upload PDF → ask question → streamed answer with citations” without domain-specific agent tree.

## MVP Scope

### 1. General RAG Orchestrator

- [x] Implement simple pipeline output model (`GeneralPipelineOutput`) and supporting data classes.
- [x] Implement main orchestrator agent (PydanticAI) that:
  1. Accepts session+question
  2. Runs the general pipeline (placeholder uses injected dependencies for now)
  3. Prompts the selected LLM using top chunks and returns answer + citations
- [x] Cover with unit tests for pipeline aggregation and orchestrator logic (integration test still pending once API wiring exists).

Mapping to tasks.md: tracked via T031–T045 (initial subset—general pipeline wiring + end-to-end integration test still outstanding).

### 2. Essential API Endpoints

Implement and test (FastAPI):

- [ ] `POST /pdf` – upload PDF, persist metadata, enqueue processing (T047)
- [x] `POST /sessions` – create chat sessions tied to a PDF (T048 simplified)
- [x] `POST /sessions/{id}/question` – call orchestrator and return answer JSON (T049; streaming upgrade pending)
- [ ] (Optional) `GET /jobs/{id}` – track embedding jobs (T050)

Contract tests from T046 should accompany these endpoints.

### 3. Frontend Glue

Update React components to hit new endpoints:

- [ ] `PDFUpload` component: call `/pdf`, show progress/state (T052). Write component test (T051).
- [ ] `ChatInterface`: manage session, send questions, render streamed answer/citations (T053/T054). Tests ensure streaming and confidence display.
- [ ] Citation UI (T055) can be simplified or deferred; basic inline citation listing is acceptable for MVP.

### 4. Configuration & Dependencies

- Ensure LLM credentials (OpenAI/Gemini) are configurable via `.env` **and stored/edited through the admin panel** (`settings` table).
- Use the OpenAI embeddings API for both PDF ingestion and query-time retrieval, with model name/version controlled via admin settings (defaults seeded from `.env`).
- Document minimal setup in `quickstart.md` updates (env variables, docker build).

### 5. Smoke Test / Validation

- Add integration test: upload fixture PDF → wait for embedding job → post question → assert non-empty answer with citation list.
- Confirm CLI entry (`python -m backend.lib.reranker rerank`) still works.

## Nice-to-Have After MVP

- Specialized pipelines and agents (T033–T040)
- Advanced observability (T056–T060)
- Citation highlight UI (T055)
- Streaming SSE refinements & job dashboard

## Summary Sequence

1. Build general pipeline output model + orchestrator (single agent path).
2. Expose upload/session/question endpoints with tests.
3. Hook frontend Upload & Chat components to the new API.
4. Run end-to-end test to confirm question answering works.

Once these are complete, the chatbot will answer questions about uploaded PDFs, providing a true MVP before layering in domain-specific or monitoring features.
