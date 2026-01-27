# AI Curation Platform – HTTP + CLI Reference

_Primary goal: give engineers (or LLM agents) everything required to exercise the production-grade API surface end-to-end, including authentication, streaming semantics, document ingestion, and feedback loops._

## Table of Contents
1. [Deploy & Quick Start](#deploy--quick-start)
2. [Base URLs & Auth Modes](#base-urls--auth-modes)
3. [Endpoint Summary](#endpoint-summary)
4. [Chat & Conversation API (`/api/chat`)](#chat--conversation-api-apichat)
5. [Document Lifecycle API (`/weaviate/documents`)](#document-lifecycle-api-weaviatedocuments)
6. [Chunk & Search API (`/weaviate/documents/{id}/chunks`)](#chunk--search-api)
7. [Processing Controls (`/weaviate/documents/{id}/reprocess|reembed`)](#processing-controls)
8. [Settings, Schema, and Chunking Strategies (`/weaviate`)](#settings-schema-and-chunking-strategies)
9. [Health & Monitoring (`/weaviate/health|readiness`)](#health--monitoring)
10. [Feedback API (`/api/feedback`)](#feedback-api)
11. [User & Auth Utilities (`/api/auth`, `/api/users`)](#user--auth-utilities)
12. [PDF Viewer Metadata API (`/api/pdf-viewer`)](#pdf-viewer-metadata-api)
13. [Trace Review Claude API (`/api/claude/traces`)](#trace-review-claude-api)
14. [Workflow Analysis API (`/api/workflow-analysis`)](#workflow-analysis-api)
15. [Ontology CLI (no HTTP endpoints yet)](#ontology-cli)
16. [Streaming Event Reference](#streaming-event-reference)
17. [End-to-End Workflows](#end-to-end-workflows)
18. [Status & Error Reference](#status--error-reference)
19. [Appendix & Resources](#appendix--resources)

---

## Deploy & Quick Start

```bash
# 1. Copy .env.example → .env and set DEV_MODE=true for local auth bypass
cp .env.example .env
sed -i 's/DEV_MODE=.*/DEV_MODE=true/' .env

# 2. Launch backend stack
docker compose up backend docling-service weaviate postgres -d

# 3. Confirm backend is serving
curl http://localhost:8000/weaviate/health
```

- **Default base URL**: `http://localhost:8000`
- **Static uploads**: served from `http://localhost:8000/uploads/<tenant>/<filename>`
- **Docling service**: reachable at `http://docling-internal.alliancegenome.org:8000` (VPN required)

> All cURL snippets assume DEV mode (auth bypass). For Cognito-protected deployments see [Base URLs & Auth Modes](#base-urls--auth-modes).

### Minimal local sanity test (DEV mode)
These steps exercise upload → load → short-token search (uses lexical-first retrieval).

```bash
# 0) Make sure backend is healthy
docker compose ps              # backend should be healthy
curl http://localhost:8000/weaviate/health

# 1) Upload a PDF (multipart)
curl -X POST http://localhost:8000/weaviate/documents/upload \
  -F file=@sample_fly_publication.pdf
# If duplicate: response includes existing_document_id to reuse.

# 2) Check status (optional)
DOC=1379822b-95c7-4dae-b67b-cd30532b598b   # replace with your doc_id
curl http://localhost:8000/weaviate/documents/$DOC/status | jq

# 3) Load the document into chat context
curl -X POST http://localhost:8000/api/chat/document/load \
  -H 'Content-Type: application/json' \
  -d "{\"document_id\": \"$DOC\"}" | jq

# 4) Ask a short-token query (lexical-first path)
curl -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "Does the loaded paper mention Rh1? Cite page.", "session_id": null}'
```

If the paper contains the short token (e.g., “Rh1”), the reply should now cite the page/quote; short/symbol-like queries use a lexical-first strategy to reduce false “not found.”

---

## Base URLs & Auth Modes

### Prefix map

| Prefix | Domain | Examples |
|--------|--------|----------|
| `/api/chat` | Hierarchical CrewAI chat + document routing | `/api/chat/stream`, `/api/chat/document/load` |
| `/api/users` | Authenticated user profile | `/api/users/me` |
| `/api/auth` | Cognito login/logout/callback | `/api/auth/login` |
| `/api/feedback` | Curator feedback capture | `/api/feedback/submit` |
| `/api/pdf-viewer` | Viewer metadata + signed URLs | `/api/pdf-viewer/documents` |
| `/weaviate/*` | All document/chunk/schema/processing controls | `/weaviate/documents`, `/weaviate/settings` |

### Authentication modes

| Mode | When to use | How |
|------|-------------|-----|
| **DEV mode** | Local testing / CI. Skips Cognito and injects a mock user (`sub=dev-user-123`). | Ensure `DEV_MODE=true` before starting backend. No cookies or headers required. |
| **API Key** | Programmatic access, CI/CD, monitoring, LLM agents. Works in production without Okta login. | Pass `X-API-Key` header with the value from `TESTING_API_KEY` env var. |
| **Okta (prod)** | Interactive browser sessions. Required for tenant scoping & feedback attribution. | 1) Visit `/api/auth/login` (redirects to Okta). 2) After login, callback sets the session cookie. 3) Reuse that cookie for all API calls. |

#### API Key Authentication (Recommended for programmatic access)

The API key bypasses Okta authentication and is ideal for:
- Automated testing and CI/CD pipelines
- Monitoring and health checks
- LLM agents and programmatic integrations
- Command-line testing against production

**Configuration** (in `.env`):
```bash
# Required: The API key value
TESTING_API_KEY=your-secret-api-key-here

# Optional: Customize the authenticated user identity
TESTING_API_KEY_USER=api-user-id        # Default: "test-user"
TESTING_API_KEY_EMAIL=api@example.com   # Default: "test@localhost"
TESTING_API_KEY_GROUPS=developers       # Default: "developers"
TESTING_API_KEY_MODS=MGI,WB             # Default: "" (empty = all MODs)
```

**Usage**:
```bash
# Pass the X-API-Key header with all requests
API_KEY="your-secret-api-key-here"

# Create a chat session
curl -X POST "https://ai-curation.alliancegenome.org/api/chat/session" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{"document_id": null}'

# Send a chat message
curl -X POST "https://ai-curation.alliancegenome.org/api/chat" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "message": "What genes are associated with Parkinson disease?",
    "session_id": "your-session-id",
    "user_id": "api-user",
    "conversation_history": []
  }'

# List documents
curl -s "https://ai-curation.alliancegenome.org/api/weaviate/documents" \
  -H "X-API-Key: $API_KEY"

# Check user identity
curl -s "https://ai-curation.alliancegenome.org/api/users/me" \
  -H "X-API-Key: $API_KEY"
```

#### Checking auth state
```bash
# DEV mode (no cookie needed)
curl http://localhost:8000/api/users/me | jq

# API Key (production)
curl -H "X-API-Key: $API_KEY" https://ai-curation.alliancegenome.org/api/users/me | jq

# Okta (capture cookies)
curl -L -c cookies.txt "http://localhost:8000/api/auth/login"   # follow UI login manually
curl -b cookies.txt http://localhost:8000/api/users/me | jq
```

If Okta isn't configured and `DEV_MODE=false`, protected endpoints return `401` unless a valid `X-API-Key` header is provided.

---

## Endpoint Summary

| Domain | Method(s) | Path | Notes |
|--------|-----------|------|-------|
| Chat | POST | `/api/chat`, `/api/chat/stream` | JSON payload `{ "message": str, "session_id": optional }`. Streaming uses Server-Sent Events. |
| Chat context | POST/GET/DELETE | `/api/chat/document/load`, `/api/chat/document` | Manage the active PDF for the current user. |
| Sessions | POST/GET/DELETE | `/api/chat/session`, `/api/chat/history*`, `/api/chat/conversation*` | Create sessions, fetch history, reset memory. |
| Documents | GET/POST/DELETE | `/weaviate/documents*` | Upload PDFs (multipart), list, inspect, delete, download artifacts. |
| Processing | POST/GET | `/weaviate/documents/{id}/status`, `/progress/stream`, `/reprocess`, `/reembed` | Track pipeline or trigger re-processing. |
| Chunks | GET | `/weaviate/documents/{id}/chunks` | Paginated chunk + provenance retrieval. |
| Config | GET/PUT | `/weaviate/settings`, `/weaviate/schema`, `/weaviate/chunking-strategies` | Inspect/update embeddings & schema. |
| Health | GET | `/weaviate/health`, `/weaviate/readiness`, `/weaviate/documents/docling-health` | Service readiness + Docling connectivity. |
| Feedback | POST | `/api/feedback/submit` | Two-phase handler (fast ack + background Langfuse enrichment). |
| Users/Auth | GET/POST | `/api/users/me`, `/api/auth/login|logout|callback` | Identity & tokens. |
| PDF viewer | GET | `/api/pdf-viewer/documents*` | Metadata + signed viewer URLs (no auth in current build). |
| Trace Review (Claude) | GET | `/api/claude/traces/{id}/*` | Token-aware trace analysis for Opus/Claude (runs on port 8001). |
| Workflow Analysis | POST | `/api/workflow-analysis/stream` | Opus 4.5 streaming trace analysis with SSE. |
| CLI | python module | `backend/cli/ontology.py` | Only supported path for ontology loading/inspection today. |

The sections below detail each group with payloads and SSE formats.

---

## Chat & Conversation API (`/api/chat`)

All routes require a valid user (DEV mock or Cognito cookie).

### 1. Create session
```bash
curl -X POST http://localhost:8000/api/chat/session | jq
```
Response (`SessionResponse`):
```json
{
  "session_id": "1d6210d4-7af1-4bff-b797-0f3e54d99025",
  "created_at": "2025-01-24T14:22:03.918794"
}
```
Use the returned `session_id` in subsequent chat calls; omit it to auto-generate one per request.

### 2. Manage active document context
```bash
# Load
curl -X POST http://localhost:8000/api/chat/document/load \
  -H 'Content-Type: application/json' \
  -d '{"document_id": "<doc-uuid>"}' | jq

# Inspect
curl http://localhost:8000/api/chat/document | jq

# Clear
curl -X DELETE http://localhost:8000/api/chat/document | jq
```
`DocumentStatusResponse` includes `active`, `document` metadata (filename, chunks, vectors) and status text.

### 3. Non-streaming chat
```bash
curl -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{
        "message": "Summarize linker cell development",
        "session_id": "1d6210d4-7af1-4bff-b797-0f3e54d99025"
      }'
```
Response (`ChatResponse`):
```json
{
  "response": "...assistant reply...",
  "session_id": "1d6210d4-7af1-4bff-b797-0f3e54d99025"
}
```

### 4. Streaming chat (SSE)
```bash
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"message": "Extract reagents for dmd-3", "session_id": null}'
```
See [Streaming Event Reference](#streaming-event-reference) for event taxonomy (`RUN_STARTED`, `AuditEvent`, `TEXT_MESSAGE_*`, `CHUNK_PROVENANCE`, `RUN_ERROR`). The final assistant text arrives inside `TEXT_MESSAGE_CONTENT` followed by `TEXT_MESSAGE_END`. If a Langfuse trace was created, every `TEXT_MESSAGE_*` payload includes `trace_id` for later feedback submission.

### 5. Service + conversation metadata
```bash
curl http://localhost:8000/api/chat/status | jq
curl http://localhost:8000/api/chat/conversation | jq
curl -X POST http://localhost:8000/api/chat/conversation/reset | jq
```
`/conversation` exposes memory stats; reset returns a new `session_id` hint for the next turn.

### 6. Conversation history APIs
```bash
# List session IDs + stats
curl http://localhost:8000/api/chat/history | jq

# Fetch / delete a specific session
SESSION="1d6210d4-7af1-4bff-b797-0f3e54d99025"
curl http://localhost:8000/api/chat/history/$SESSION | jq
curl -X DELETE http://localhost:8000/api/chat/history/$SESSION
```
`history` payloads include each exchange plus supervisor routing metadata.

### 7. Chat configuration
```bash
curl http://localhost:8000/api/chat/config | jq
```
Returns CrewAI memory and routing thresholds (useful when asserting planner decisions in automated tests).

---

## Document Lifecycle API (`/weaviate/documents`)

All routes are tenant-scoped via the authenticated user’s `sub`. Core response types live in `src/models/api_schemas.py`.

### Common schema snippets
- **`DocumentResponse`**
  - `document_id`, `user_id`, `filename`, `status` (`pending|processing|completed|failed`)
  - `upload_timestamp`, optional processing timestamps
  - `file_size_bytes`, `weaviate_tenant`, optional `chunk_count`, `error_message`
- **`OperationResult`**: `{ "success": bool, "message": str, "document_id": str|null }`

### 1. Upload PDF (multipart)
```bash
curl -X POST http://localhost:8000/weaviate/documents/upload \
  -H 'Content-Type: multipart/form-data' \
  -F file=@/path/to/paper.pdf | jq
```
Returns an immediate `DocumentResponse` while the pipeline continues in the background. Files are stored under `pdf_storage/<cognito-sub>/...` and tied to the PostgreSQL `viewer_pdf_document` table.

### 2. List documents
```bash
curl "http://localhost:8000/weaviate/documents?page=1&page_size=20&search=dmd&sort_by=creationDate&sort_order=desc" | jq
```
Query parameters: `page`, `page_size (≤100)`, `search`, `embedding_status[]`, `sort_by`, `sort_order`, `date_from`, `date_to`, `min_vector_count`, `max_vector_count`. Response includes `documents` plus `total`, `limit`, `offset`.

### 3. Inspect / delete
```bash
DOC_ID="<uuid>"
curl http://localhost:8000/weaviate/documents/$DOC_ID | jq
curl -X DELETE http://localhost:8000/weaviate/documents/$DOC_ID | jq
```
Deletion removes metadata + tenant-scoped files (with ownership validation).

### 4. Processing status & live progress
```bash
curl http://localhost:8000/weaviate/documents/$DOC_ID/status | jq
```
Response:
```json
{
  "document_id": "...",
  "processing_status": "processing",
  "embedding_status": "partial",
  "pipeline_status": {
    "current_stage": "chunking",
    "progress_percentage": 67,
    "message": "Chunking by section headings",
    "stage_results": [ ... ]
  },
  "chunk_count": 412,
  "vector_count": 0
}
```
For real-time monitoring subscribe to SSE:
```bash
curl -N http://localhost:8000/weaviate/documents/$DOC_ID/progress/stream
```
Events are JSON blobs with `stage`, `progress`, `message`, `timestamp`, and `final=true` when the pipeline completes or fails (timeout after 5 minutes).

### 5. Download artifacts
```bash
# Discover availability + sizes
curl http://localhost:8000/weaviate/documents/$DOC_ID/download-info | jq

# Download specific asset (pdf | docling_json | processed_json)
curl -OJ http://localhost:8000/weaviate/documents/$DOC_ID/download/pdf
```
Ownership is re-checked against PostgreSQL before any file is read from disk.

### 6. Docling health probe
```bash
curl http://localhost:8000/weaviate/documents/docling-health | jq
```
Reports status, upstream HTTP code, and cached payload from the Docling service – essential when automating regression tests.

---

## Chunk & Search API

```bash
curl "http://localhost:8000/weaviate/documents/$DOC_ID/chunks?page=1&page_size=10&include_metadata=true" | jq
```
Response (`ChunkListResponse`):
- `chunks`: array of `DocumentChunk` objects (content, `element_type`, `page_number`, `doc_items` with bounding boxes, metadata).
- `pagination`: `{ current_page, total_pages, total_items, page_size }`

Use this endpoint to verify embedding coverage or to reconstruct provenance overlays produced by `/api/chat/stream` (the SSE `CHUNK_PROVENANCE` events reuse the same `doc_items` schema).

---

## Processing Controls

### 1. Reprocess (re-chunk)
```bash
curl -X POST http://localhost:8000/weaviate/documents/$DOC_ID/reprocess \
  -H 'Content-Type: application/json' \
  -d '{"strategy_name": "research", "force_reparse": false}' | jq
```
- `strategy_name`: must match one of `/weaviate/chunking-strategies` (e.g., `research` default).
- `force_reparse`: true to re-run Docling parsing instead of reusing stored JSON.

### 2. Re-embed
```bash
curl -X POST http://localhost:8000/weaviate/documents/$DOC_ID/reembed \
  -H 'Content-Type: application/json' \
  -d '{
        "embedding_config": {
          "model_provider": "openai",
          "model_name": "text-embedding-3-small",
          "dimensions": 1536,
          "batch_size": 32
        },
        "batch_size": 32
      }' | jq
```
`embedding_config` is optional; omit to reuse the active setting. Both operations return `OperationResult` and immediately transition the document to `processing` until the background task finishes.

---

## Settings, Schema, and Chunking Strategies

### Embedding + database settings
```bash
curl http://localhost:8000/weaviate/settings | jq

curl -X PUT http://localhost:8000/weaviate/settings \
  -H 'Content-Type: application/json' \
  -d '{
        "embedding_config": {
          "model_provider": "openai",
          "model_name": "text-embedding-3-small",
          "dimensions": 1536,
          "batch_size": 64
        }
      }'
```
`PUT` accepts any combination of `embedding_config` and `database_settings`; absent objects mean “no change”. Warnings in the response highlight actions that may require re-embedding or cluster restarts.

### Schema inspection & updates
```bash
curl http://localhost:8000/weaviate/schema | jq

curl -X PUT http://localhost:8000/weaviate/schema \
  -H 'Content-Type: application/json' \
  -d '{"vectorIndexConfig": {"distance": "cosine", "ef": 256}}'
```
Payload validation ensures supported `dataType`s and HNSW parameters. Destructive changes should only occur during maintenance windows.

### Chunking strategies catalogue
```bash
curl http://localhost:8000/weaviate/chunking-strategies | jq
```
Returns the statically configured strategies from `lib/pdf_processing/strategies.py` (fields: `name`, `method`, `max_characters`, `overlap`, `is_default`, `description`).

---

## Health & Monitoring

```bash
curl http://localhost:8000/weaviate/health | jq
curl http://localhost:8000/weaviate/readiness | jq
```
Responses include service version, Cognito configuration flag, and Weaviate diagnostics. Any degraded subsystem flips the HTTP status to 503 with structured details (`checks.api`, `checks.weaviate`).

---

## Feedback API

`POST /api/feedback/submit` (auth required)
```bash
curl -X POST http://localhost:8000/api/feedback/submit \
  -H 'Content-Type: application/json' \
  -d '{
        "session_id": "1d6210d4-7af1-4bff-b797-0f3e54d99025",
        "curator_id": "curator@alliance.org",
        "feedback_text": "The gene expression agent missed negative evidence.",
        "trace_ids": ["70a0a9be91eb4962af80bc4f9972c9b1"]
      }'
```
Immediate response (<500 ms):
```json
{
  "status": "success",
  "feedback_id": "60e82f8d-8b8a-4a18-bebe-ad6a038422ba",
  "message": "Feedback submitted successfully. Report will be processed in background."
}
```
A FastAPI `BackgroundTask` then enriches the payload with Langfuse traces, stores it in PostgreSQL, and (when SMTP is configured) emails the developer alias. *_Always pass the `trace_id` from `/api/chat/stream` to guarantee we correlate the right run._*

---

## User & Auth Utilities

- `GET /api/users/me` – returns the auto-provisioned PostgreSQL record (`user_id`, `email`, `display_name`, timestamps). Triggers provisioning if the user does not exist.
- `GET /api/auth/login` – redirects to Cognito Hosted UI. Requires browser interaction.
- `GET /api/auth/callback` – exchanges the authorization code, sets the `cognito_token` cookie.
- `POST /api/auth/logout` – clears cookies + performs Cognito global logout.

When `DEV_MODE=true` the dependency injects:
```json
{
  "sub": "dev-user-123",
  "email": "dev@localhost",
  "name": "Dev User",
  "cognito:groups": ["developers"]
}
```
Use `/api/users/me` to confirm env parity before running tests.

---

## PDF Viewer Metadata API

No auth guard is applied today (the viewer sits behind the same network perimeter). Endpoints:

```bash
curl http://localhost:8000/api/pdf-viewer/documents | jq
curl http://localhost:8000/api/pdf-viewer/documents/<uuid> | jq
curl http://localhost:8000/api/pdf-viewer/documents/<uuid>/url | jq
```
Responses contain `viewer_url` (always `/uploads/<tenant>/<relative-path>`), `page_count`, `file_size`, SHA-256 `file_hash`, and timestamps for last access – ideal for verifying UI download links.

---

## Trace Review Claude API

**Base URL**: `http://localhost:8001/api/claude/traces` (TraceReview service on port 8001)

Token-aware endpoints designed for Claude/Opus workflow analysis. All responses include `token_info` metadata to help Claude manage its 200K context budget. These endpoints are used by the Workflow Analysis feature.

### Token Budget Strategy

- **50K token budget** per response (leaves headroom in 200K window)
- Token estimation: 4 characters ≈ 1 token
- Every response includes `token_info.estimated_tokens` and `token_info.within_budget`
- If `within_budget` is false, use pagination or filtering to reduce data size

### Response Schema

All endpoints return:
```json
{
  "status": "success" | "error",
  "data": { ... },
  "token_info": {
    "estimated_tokens": 523,
    "within_budget": true,
    "warning": null
  }
}
```

### 1. Get Trace Summary (~500 tokens)

**ALWAYS call this first** when analyzing a trace. Provides essential overview with minimal token cost.

```bash
curl -s http://localhost:8001/api/claude/traces/{trace_id}/summary | jq
```

Response:
```json
{
  "status": "success",
  "data": {
    "trace_id": "d3b0a19f2c2df7b2b31dfb7cded3acbd",
    "trace_id_short": "d3b0a19f",
    "trace_name": "pdf-specialist-chat",
    "duration_seconds": 45.2,
    "total_cost": 0.0234,
    "total_tokens": 15420,
    "tool_call_count": 23,
    "unique_tools": ["search_document", "read_section", "transfer_to_pdf_specialist"],
    "has_errors": false,
    "context_overflow_detected": false,
    "timestamp": "2025-01-24T14:00:00Z"
  },
  "token_info": { "estimated_tokens": 487, "within_budget": true, "warning": null }
}
```

### 2. Get Tool Calls Summary (~100 tokens per call)

Lightweight list of ALL tool calls with summaries (no full results). Use to see what tools were called before drilling into details.

```bash
curl -s http://localhost:8001/api/claude/traces/{trace_id}/tool_calls/summary | jq
```

Response:
```json
{
  "status": "success",
  "data": {
    "total_count": 23,
    "unique_tools": ["search_document", "read_section"],
    "tool_calls": [
      {
        "index": 0,
        "call_id": "call_O3pBietwjqBaDwsUhF21LdtJ",
        "name": "read_section",
        "time": "2025-01-24T14:00:08.802000Z",
        "duration": "2.29s",
        "status": "completed",
        "input_summary": "section_name=Experimental Section",
        "result_summary": "Section found with 2341 characters"
      }
    ],
    "has_duplicates": true,
    "duplicate_count": 3
  },
  "token_info": { "estimated_tokens": 2300, "within_budget": true, "warning": null }
}
```

### 3. Get Tool Calls (Paginated, ~1-5K tokens per page)

Full tool call details with pagination and optional filtering by tool name.

```bash
# Get page 1 with 10 items
curl -s "http://localhost:8001/api/claude/traces/{trace_id}/tool_calls?page=1&page_size=10" | jq

# Filter by tool name
curl -s "http://localhost:8001/api/claude/traces/{trace_id}/tool_calls?tool_name=search_document&page_size=5" | jq
```

Query parameters:
- `page` (default: 1): Page number (1-indexed)
- `page_size` (default: 10, max: 20): Items per page
- `tool_name` (optional): Filter by tool name

Response includes pagination info:
```json
{
  "status": "success",
  "tool_calls": [ { /* full tool call with input and result */ } ],
  "pagination": {
    "page": 1,
    "page_size": 10,
    "total_items": 23,
    "total_pages": 3,
    "has_next": true,
    "has_prev": false
  },
  "filter_applied": null,
  "token_info": { "estimated_tokens": 4500, "within_budget": true, "warning": null }
}
```

### 4. Get Single Tool Call Detail (~1-5K tokens)

Full details for a specific tool call by `call_id`.

```bash
curl -s http://localhost:8001/api/claude/traces/{trace_id}/tool_calls/{call_id} | jq
```

Response:
```json
{
  "status": "success",
  "tool_call": {
    "call_id": "call_O3pBietwjqBaDwsUhF21LdtJ",
    "name": "read_section",
    "time": "2025-01-24T14:00:08.802000Z",
    "duration": "2.29s",
    "model": "gpt-4o-mini-2024-07-18",
    "status": "completed",
    "input": { "section_name": "Experimental Section" },
    "tool_result": {
      "summary": "Section found with 2341 characters",
      "parsed": { /* full parsed result */ }
    }
  },
  "token_info": { "estimated_tokens": 3200, "within_budget": true, "warning": null }
}
```

### 5. Get Conversation (~1-10K tokens)

User's query and assistant's final response.

```bash
curl -s http://localhost:8001/api/claude/traces/{trace_id}/conversation | jq
```

Response:
```json
{
  "status": "success",
  "data": {
    "user_query": "What alleles are mentioned in the paper?",
    "assistant_response": "Based on the paper, I found the following alleles...",
    "response_length": 1523
  },
  "token_info": { "estimated_tokens": 1800, "within_budget": true, "warning": null }
}
```

### 6. Get Trace View (Generic, varies)

Access other analysis views with token metadata.

```bash
# Available views: token_analysis, agent_context, pdf_citations,
#                  document_hierarchy, agent_configs, mod_context, trace_summary
curl -s http://localhost:8001/api/claude/traces/{trace_id}/views/pdf_citations | jq
```

### Recommended Analysis Workflow

```bash
TRACE_ID="d3b0a19f2c2df7b2b31dfb7cded3acbd"

# 1. ALWAYS start with summary (cheapest)
curl -s http://localhost:8001/api/claude/traces/$TRACE_ID/summary | jq '.data'

# 2. Get tool calls overview
curl -s http://localhost:8001/api/claude/traces/$TRACE_ID/tool_calls/summary | jq '.data'

# 3. Drill into specific calls if needed
curl -s "http://localhost:8001/api/claude/traces/$TRACE_ID/tool_calls?page=1&page_size=5" | jq

# 4. Get conversation if analyzing response quality
curl -s http://localhost:8001/api/claude/traces/$TRACE_ID/conversation | jq '.data'
```

---

## Workflow Analysis API

**Base URL**: `http://localhost:8000/api/workflow-analysis`

The Workflow Analysis feature uses Claude Opus 4.5 to analyze Langfuse traces and identify issues in AI agent behavior. It streams responses via Server-Sent Events (SSE).

### Stream Analysis

```bash
curl -N -X POST http://localhost:8000/api/workflow-analysis/stream \
  -H 'Content-Type: application/json' \
  -d '{
    "trace_id": "d3b0a19f2c2df7b2b31dfb7cded3acbd",
    "user_query": "Why did the agent fail to find the alleles?"
  }'
```

Request body:
- `trace_id` (required): Langfuse trace ID to analyze
- `user_query` (required): Question about the trace behavior

Response: Server-Sent Events stream with these event types:

| Event Type | Description |
|------------|-------------|
| `ANALYSIS_STARTED` | Analysis initiated, includes session info |
| `TEXT_DELTA` | Streaming text chunks from Opus |
| `TOOL_USE` | Opus is calling a trace analysis tool |
| `TOOL_RESULT` | Result from trace analysis tool |
| `CONTEXT_OVERFLOW` | Token limit reached, includes recovery suggestions |
| `ANALYSIS_COMPLETE` | Final response, includes full text |
| `ANALYSIS_ERROR` | Error occurred during analysis |

### Available Analysis Tools

Opus has access to these token-aware tools during analysis:

| Tool | Token Cost | Description |
|------|------------|-------------|
| `get_trace_summary` | ~500 | Lightweight overview (ALWAYS called first) |
| `get_tool_calls_summary` | ~100/call | List all calls without full results |
| `get_tool_calls_page` | ~1-5K | Paginated full details with filtering |
| `get_tool_call_detail` | ~1-5K | Single call full detail |
| `get_trace_conversation` | ~1-10K | User query + assistant response |
| `get_trace_view` | varies | Access other analysis views |
| `get_docker_logs` | varies | Retrieve container logs |
| `submit_anthropic_suggestion` | N/A | Submit system prompt improvements |

### Context Overflow Handling

If Opus hits its 200K token limit, the stream emits a `CONTEXT_OVERFLOW` event:

```json
{
  "type": "CONTEXT_OVERFLOW",
  "message": "I've hit my token limit for this conversation.",
  "recovery_hint": "Try a lighter-weight tool call: use get_trace_summary instead of full views...",
  "suggested_tools": [
    "get_trace_summary - lightweight overview (~500 tokens)",
    "get_tool_calls_summary - summaries only, no full results",
    "get_tool_calls_page with page_size=5 - smaller batches"
  ]
}
```

### Example: Full Analysis Session

```bash
# 1. First, create a chat to generate a trace
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"message": "What alleles are in the paper?", "session_id": null}'
# Note the trace_id from TEXT_MESSAGE events

# 2. Analyze the trace with Opus
TRACE_ID="<trace_id from step 1>"
curl -N -X POST http://localhost:8000/api/workflow-analysis/stream \
  -H 'Content-Type: application/json' \
  -d "{
    \"trace_id\": \"$TRACE_ID\",
    \"user_query\": \"Did the agent search efficiently? Were there duplicate calls?\"
  }"
```

---

## Ontology CLI

There are **no `/api/ontologies` HTTP routes** yet. Use the CLI shipped in `backend/cli/ontology.py` for loading, listing, or deleting ontologies directly via SQL.

### Examples
```bash
# Load an OBO file
python -m cli.ontology load --path data/doid.obo --name "Disease Ontology"

# List (optionally filter / paginate / JSON)
python -m cli.ontology list --filter disease --limit 20 --json

# Inspect or delete by UUID
python -m cli.ontology get --id <uuid> --json
python -m cli.ontology delete --id <uuid>
```
Keep this distinction in mind when designing LLM tests—ontology operations must shell out to the CLI.

---

## Streaming Event Reference

### Chat SSE (`POST /api/chat/stream`)
Events are emitted line-by-line as `data: <json>`. Key event types:

| Event `type` | Description |
|--------------|-------------|
| `RUN_STARTED` | Kick-off marker containing `session_id` and placeholder `runId`. |
| `CREW_START`, `TASK_START`, `TASK_COMPLETED`, `TOOL_START`, `TOOL_END`, `LLM_START`, `LLM_END` | Structured audit events produced by `ProgressEventListener`. Each payload includes `timestamp`, `sessionId`, and `details` (crew/task/tool name, friendly text, optional tokens/cost). |
| `TEXT_MESSAGE_START` | Marks the beginning of the assistant’s textual reply. Contains `messageId`, `session_id`, `trace_id`. |
| `TEXT_MESSAGE_CONTENT` | The complete assistant response (single payload) plus `trace_id`. |
| `TEXT_MESSAGE_END` | Signals completion of text streaming. |
| `CHUNK_PROVENANCE` | Per-chunk overlays: `{ document_id, chunk_id, doc_items: [...] }` for highlighting in the viewer. |
| `RUN_ERROR` | Only emitted when upstream processing fails; contains a human-readable error `message`. |

### Document progress SSE (`GET /weaviate/documents/{id}/progress/stream`)
Payload example:
```json
{
  "stage": "chunking",
  "progress": 55,
  "message": "Chunking by section headings",
  "timestamp": "2025-01-24T15:02:10.101201",
  "final": false
}
```
When `final=true` the document either finished (`stage=completed`, `progress=100`) or failed (message includes reason). Timeouts emit `{ "stage": "timeout", ... }` after ~5 minutes of inactivity.

Handle both streams with resilient parsers—events are newline-separated but may batch multiple `data:` lines per TCP frame.

---

## End-to-End Workflows

### Workflow A – Upload, monitor, and interrogate a PDF
1. **Upload PDF**
   ```bash
   UPLOAD=$(curl -s -X POST http://localhost:8000/weaviate/documents/upload \
     -F file=@sample_fly_publication.pdf)
   DOC_ID=$(echo "$UPLOAD" | jq -r '.document_id')
   ```
2. **Watch processing**
   ```bash
   curl -N http://localhost:8000/weaviate/documents/$DOC_ID/progress/stream
   ```
   Continue until a `final:true` event arrives.
3. **Load document into chat context**
   ```bash
   curl -X POST http://localhost:8000/api/chat/document/load \
     -H 'Content-Type: application/json' \
     -d "{\"document_id\": \"$DOC_ID\"}"
   ```
4. **Create session + stream a question**
   ```bash
   SESSION=$(curl -s -X POST http://localhost:8000/api/chat/session | jq -r '.session_id')
   curl -N -X POST http://localhost:8000/api/chat/stream \
     -H 'Content-Type: application/json' \
     -d "{\"message\": \"Summarize mab-3 expression\", \"session_id\": \"$SESSION\"}"
   ```
5. **Persist trace for feedback**
   Grab the `trace_id` from `TEXT_MESSAGE_*` events; pass it to `/api/feedback/submit` later if you want to audit the run.

### Workflow B – Reprocess with a different strategy
```bash
DOC_ID=<uuid created earlier>

curl -X POST http://localhost:8000/weaviate/documents/$DOC_ID/reprocess \
  -H 'Content-Type: application/json' \
  -d '{"strategy_name": "research", "force_reparse": true}' | jq

# Watch progress (same SSE endpoint as before)
curl -N http://localhost:8000/weaviate/documents/$DOC_ID/progress/stream

# Verify chunk count jumped or metadata changed
curl http://localhost:8000/weaviate/documents/$DOC_ID | jq '.chunk_count'
```

### Workflow C – Submit curator feedback referencing Langfuse trace
```bash
TRACE_ID="<from TEXT_MESSAGE events>"
SESSION_ID="<chat session uuid>"

curl -X POST http://localhost:8000/api/feedback/submit \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\": \"$SESSION_ID\", \"curator_id\": \"tester@example.org\", \"feedback_text\": \"Agent chose wrong reagent.\", \"trace_ids\": [\"$TRACE_ID\"]}"
```

---

## Status & Error Reference

### Processing + embedding statuses
| Field | Enum | Description |
|-------|------|-------------|
| `processing_status` | `pending`, `processing`, `parsing`, `chunking`, `embedding`, `storing`, `completed`, `failed` | Current high-level pipeline state (from `src/models/document.py`). |
| `embedding_status` | `pending`, `processing`, `completed`, `failed`, `partial` | Embedding coverage across chunks. |
| `pipeline_status.current_stage` | `pending`, `upload`, `parsing`, `chunking`, `embedding`, `storing`, `completed`, `failed` | Fine-grained tracker stage (see `src/models/pipeline.py`). |

### Typical HTTP responses
| Code | Meaning / common cause |
|------|------------------------|
| 200 | Successful GET/POST returning JSON. |
| 201 | File uploaded (`/weaviate/documents/upload`). |
| 202 | Background task scheduled (rare; most ops return 200 + message). |
| 204 | Successful deletion with no payload. |
| 400 | Bad request (invalid strategy, unsupported file type, etc.). |
| 401 | Missing/invalid Cognito token when `DEV_MODE=false`. |
| 403 | Document owned by another tenant. |
| 404 | Document/chunk not found or deleted. |
| 409 | Document still processing; reprocess/reembed refused. |
| 422 | FastAPI validation error (body/body field missing). |
| 500 | Unexpected backend failure (see `detail`). |
| 503 | Downstream dependency down (`/weaviate/health`, Cognito misconfig). |

Most FastAPI errors follow the default shape:
```json
{ "detail": "Error message" }
```
while domain-level operations return `OperationResult` with structured `error` payloads when applicable.

---

## Appendix & Resources

### Main Application (port 8000)
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **Langfuse dashboard** (when running via docker-compose): http://localhost:3000

### Trace Review Tool (port 8001)
- **Trace Review Web UI**: http://localhost:3001
- **Trace Review API**: http://localhost:8001
- **API Documentation**: See `docs/traces/TRACE_REVIEW_API.md` for complete trace review API documentation

### Development Resources
- **Scripts**: `scripts/testing` contains sample ingestion scripts used by CI.
- **Source references**:
  - Main API routers: `backend/src/api/*.py`
  - API schemas: `backend/src/models/api_schemas.py`
  - Pipeline tracker: `backend/src/lib/pipeline/tracker.py`
  - Trace Review: `trace_review/backend/src/` (analyzers, services, API)
  - Trace documentation: `docs/traces/TRACE_REVIEW_API.md`

Armed with the instructions above, an automated agent can:
1. Stand up the stack (DEV mode or Cognito-authenticated).
2. Upload and process PDFs, watching progress via SSE.
3. Load processed docs into the chat orchestrator, stream responses, and capture Langfuse traces.
4. Reprocess/re-embed as needed, download structured outputs, and file curator feedback with trace IDs.
5. Validate chunk provenance and settings to ensure regression coverage.
