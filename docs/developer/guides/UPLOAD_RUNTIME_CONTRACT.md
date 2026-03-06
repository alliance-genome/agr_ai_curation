# Upload Runtime Contract

Last updated: 2026-03-06
Related issues: ALL-24 (this contract), ALL-23 (implementation refactor)

## Goal

Define the required runtime behavior for PDF upload processing before moving orchestration out of the endpoint layer.

This contract covers:

- status source-of-truth and precedence,
- cancellation semantics,
- rollback/compensation expectations across storage systems,
- idempotency for request and job replay,
- known current deviations that must be handled as follow-up work.

## Runtime entities

- `pdf_processing_jobs` row (durable job state machine)
- `pdf_documents` row (SQL document metadata/status)
- Weaviate document/chunks
- filesystem artifacts (`pdf_storage/<user>/<doc_id>/...`, plus JSON artifacts)
- in-memory `PipelineTracker` state (best-effort live progress only)

## 1) Status contract

### 1.1 Durable source of truth

- The durable job row (`pdf_processing_jobs.status`) is the authoritative lifecycle state once created.
- In-memory pipeline state is advisory for live stage/progress display only.
- SQL `pdf_documents.status` and Weaviate status fields are derivative projections.

### 1.2 Terminal-state immutability

Terminal job statuses: `completed`, `failed`, `cancelled`.

- Once a job is terminal, subsequent writes must not move it back to `pending`, `running`, or `cancel_requested`.
- Conflicting terminal writes use first-writer-wins:
  - whichever terminal transition is persisted first is final,
  - later conflicting terminal transitions are no-op and logged with context.

### 1.3 Read precedence (effective processing status)

When serving `/weaviate/documents/{id}/status` and related status views:

1. If durable job exists and is terminal, map from durable job status.
2. Else if durable job exists and is active, map from durable job status (`pending`/`processing`) and include live pipeline stage details when available.
3. Else if no durable job exists, use SQL document status.
4. Only use Weaviate status as final fallback for legacy rows lacking job+SQL runtime fields.

### 1.4 Conflict resolution rules

| Conflict | Required rule |
|---|---|
| Cancel request arrives after terminal | Return success/no-op; keep terminal state unchanged. |
| Stage/progress update arrives after terminal | Ignore update; do not mutate terminal status. |
| `mark_completed` vs `mark_cancelled` race | First persisted terminal transition wins; later write is no-op. |
| Stale reconciliation runs while job still heartbeating | Reconciliation must not override active jobs that have fresh activity. |

## 2) Cancellation semantics

### 2.1 Pre-start cancellation

If cancellation is requested after upload acceptance but before orchestration starts:

- finalize job as `cancelled`,
- set user-visible status to failed/cancelled projection with clear message,
- do not start parsing/chunking/storing work.

### 2.2 Mid-run cancellation

Cancellation is cooperative and best-effort:

- API cancel request sets `cancel_requested=true` and status `cancel_requested` for active jobs,
- orchestrator checks cancellation between stages and in long polling loops,
- parser attempts remote extraction cancel (`/extract/{process_id}/cancel`) best-effort,
- final durable terminal state must become `cancelled` when cancellation is honored.

### 2.3 Cancellation races with terminal states

- If job is already terminal at cancel time, no-op (idempotent response).
- If cancel is requested before terminal write is finalized, terminal conflict rule applies (first terminal write wins).
- API and UI messaging must clearly distinguish:
  - `cancel_requested` (not terminal),
  - `cancelled` (terminal).

## 3) Rollback and compensation matrix

Required compensation behavior by failure point:

| Failure point | Filesystem | Weaviate | SQL document row | Durable job row |
|---|---|---|---|---|
| Save upload file fails | No-op | No-op | No-op | No-op |
| Weaviate create fails after file save | Delete saved upload directory | No-op | No-op | No-op |
| SQL document write fails after Weaviate create | Delete saved upload directory | Delete created Weaviate document/chunks | Roll back transaction | No job row should remain |
| Durable job creation fails after SQL+Weaviate success | Delete saved upload directory | Delete created Weaviate document/chunks | Delete SQL document row before response is finalized | Return explicit error; no partial "active" job |
| Pipeline fails during parsing/chunking/storing | Keep raw file and metadata for debugging/retry | Remove partial chunks when write is non-atomic, or mark partial write as failed and quarantined | Mark failed status + error message | Mark `failed` terminal |
| Cancellation accepted before run | Keep upload artifacts unless explicit delete policy says otherwise | No processing writes should occur | Set projection to failed/cancelled | Mark `cancelled` terminal |

## 4) Idempotency and replay contract

### 4.1 Upload request replay

| Scenario | Expected outcome |
|---|---|
| Same user replays identical file upload | `409 duplicate_file` with existing document reference; no new job/document created. |
| Different user uploads identical bytes | Allowed (user-scoped dedupe key); separate document/job records. |
| Client retries after network timeout during initial upload | Server returns either existing successful result or duplicate response; must not create more than one durable document/job for same user+file content. |

### 4.2 Job/cancel replay

| Scenario | Expected outcome |
|---|---|
| Repeated cancel on active job | Idempotent success; job remains `cancel_requested` or `cancelled`. |
| Repeated cancel on terminal job | Idempotent success/no-op with terminal state unchanged. |
| Worker/job retry after terminal state | No-op for status mutation; terminal state remains immutable. |

## 5) Existing behavior deviations (explicit follow-up tasks)

These are known gaps between current code behavior and the contract above:

1. `get_document_endpoint` returns status directly from Weaviate and does not apply durable-job precedence used by `/documents/{id}/status`.
   Owner area: `backend/src/api/documents.py`.
2. `mark_completed`, `mark_failed`, and `mark_cancelled` can overwrite already-terminal job rows; first-terminal-write-wins is not enforced.
   Owner area: `backend/src/lib/pdf_jobs/service.py`.
3. Upload rollback is incomplete for non-`IntegrityError` failures after file save / Weaviate create (can leave orphaned files or Weaviate records).
   Owner area: `backend/src/api/documents.py` upload flow.
4. Failure to create durable job row after successful SQL+Weaviate writes can return 500 with partially persisted upload state instead of executing the explicit cleanup policy in section 3.
   Owner area: `backend/src/api/documents.py` (`create_job` call path).
5. Cancellation race behavior between late completion and cancel terminalization is not explicitly serialized; current flow can produce ambiguous winner semantics.
   Owner areas: `backend/src/api/documents.py`, `backend/src/lib/pdf_jobs/service.py`.
6. No dedicated automated tests currently lock in terminal immutability and terminal conflict resolution for `pdf_processing_jobs` transitions.
   Owner area: `backend/tests/unit` (new service-level tests needed).

## 6) Implementation checklist for ALL-23

- [ ] Introduce a single runtime owner for job status transitions (endpoint thin, service/runtime thick).
- [ ] Enforce terminal immutability and first-terminal-write-wins in durable job writes.
- [ ] Apply one consistent status projection function across all document/status endpoints.
- [ ] Implement explicit compensation behavior for every matrix failure point.
- [ ] Add tests for cancellation races, terminal immutability, and replay/idempotency scenarios.
