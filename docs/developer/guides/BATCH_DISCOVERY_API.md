# Batch discovery and detail reads

ALL-1410 supplies the API contract for ALL-1405's shared polling coordinator.
Types are in `frontend/src/types/batch.ts`. All routes require authentication;
every query is owner-filtered. Summary paths never hydrate batch documents,
titles, result manifests, extraction results, or review sessions.

## History and independent active discovery

`GET /api/batches?limit=5` returns, for example:

```json
{
  "batches": [{
    "id": "b9b880a4-1089-4cfe-a310-e583ba6a65c8",
    "flow_id": "61c2d8f1-9707-4a52-8aac-f77091e4f01c",
    "flow_name": null,
    "status": "completed",
    "total_documents": 10,
    "completed_documents": 8,
    "failed_documents": 2,
    "created_at": "2026-10-16T12:00:00Z",
    "started_at": "2026-10-16T12:00:01Z",
    "completed_at": "2026-10-17T12:00:00Z"
  }],
  "total": 123,
  "next_cursor": "<opaque continuation>"
}
```

`total` is the exact current owned count matching the filter, including rows
created since traversal began; it is not the remaining count or page length.
`status` accepts `active` (pending/running), `pending`, `running`, `completed`,
or `cancelled`. Auto-resume uses `?status=active`, independently of the recent
history cards, then loads `GET /api/batches/{id}` before attaching SSE.

Pages use descending `(created_at, id)` keysets and freeze the initial upper
creation timestamp. Pass `cursor` unchanged with the same status filter; limit
may change. Tokens are opaque transport values, validated for route, owner,
filter, shape, and bounds, not authorization credentials. The owner predicate
always comes from authentication. Invalid tokens/limits/filters return 422.
No transaction is held between requests. This is a live traversal, not a durable
snapshot: status changes and late commits can change membership. Repeating a
request reuses its cursor; advance only after successfully consuming its page.

`GET /api/batches/running-count` remains the nav badge's active contract:

```json
{"running_count": 3, "pending_documents": 17}
```

Both values come from one aggregate across **all** owned pending/running batches,
independent of history and active pages. Pending documents means the sum of
`total_documents - completed_documents - failed_documents`.

## Bounded terminal discovery and reconciliation

`GET /api/batches/terminals?limit=5` returns:

```json
{
  "batches": [],
  "window_start": "2026-10-16T12:00:00Z",
  "window_end": "2026-10-17T12:00:00Z",
  "next_cursor": null,
  "coverage": "recent_window"
}
```

The projection is identical to history. Completed/cancelled rows are ordered by
`(completed_at, id)` descending, inclusive within the returned window. An old
batch completed or cancelled now belongs here even if absent from recent cards.
Null continuation exhausts **that window**, not future terminal changes.

The default window is the last `BATCH_TERMINAL_WINDOW_SECONDS` (86400 seconds).
An optional timezone-aware `since` starts a narrower window ending now; future
or naive times are rejected. `since` and `cursor` are mutually exclusive.
A continuation retains both bounds and ordering position. Traversal has a grace
period until its frozen `window_end` is older than the configured window duration;
this permits finishing pages without dropping the earliest edge as time passes.
An expired cursor, a cursor wider than a newly reduced configured duration, or a
`since` before current coverage starts a fresh recent window with `coverage: reset`.
The returned bounds describe the replacement coverage. History and terminal
cursors are never interchangeable.

Use bounded overlapping windows, deduplicating `(id, status)`. Do not advance
past unread pages when a polling request budget is reached. Retain active
traversal cursors between scheduled requests; never drain all active/terminal
pages per tick. Timestamps are not commit order: a late commit can miss a prior
traversal. Overlap improves discovery but is not durable delivery evidence.

`POST /api/batches/lookup` with `{"ids": ["<previously observed active UUID>"]}`
returns `{"batches": [<summary>], "unavailable_ids": []}`. It works even after a
batch leaves the terminal window. Missing and other-owner IDs both appear in
`unavailable_ids`; their existence is not disclosed. Duplicate IDs produce one
result in requested order. Empty/oversized requests return 422.

The coordinator must retain every observed active ID until it sees a terminal
summary (or explicitly handles an unavailable ID). On reconnect it refreshes
exact totals, reconciles known IDs in bounded requests, and resumes or resets
terminal traversal according to coverage. Initial mount seeds **all pages of
its initial window** without historical toasts. A reset must be treated as a
coverage gap, never as proof of a complete offline notification feed.

The guarantee is discoverable recent terminals and eventual outcomes of observed
active IDs. Exactly-once delivery of every unknown terminal over arbitrarily
long offline periods is not promised. Older unknown outcomes remain in history.
There is no notification event store. ALL-1405 owns coordinator scheduling,
visibility/auth cancellation, retention of observed IDs, and notification policy.
The interim App consumer follows one terminal page per existing scheduled poll,
retains continuation on failure, and suppresses initial-window historical toasts.

## Detail, SSE, configuration, and measured evidence

`GET /api/batches/{id}` retains full detail and enforces ownership before enrichment.
Titles and extraction/review identities load in configured chunks. Extractions
match document **and** flow-run ID, exclude curation-prep output, and retain
`(created_at, id)` order and first nonempty origin session. Persisted review-ID
order determines adapter-key order. Invalid/missing authoritative review IDs and
corrupt result manifests remain errors; no scientific judgments are recomputed.
Noncompleted documents retain empty handoff identities. Nullable flow names and
title-to-filename selection retain their existing meaning.

The single-document SSE helper delegates to the same bulk implementation; its
payload contract is unchanged. ALL-1409 still owns stream session lifetime and
snapshot offloading. ALL-1413 still owns download/ZIP metadata.

Environment settings (documented in `.env.example`):

- `BATCH_PAGE_DEFAULT=20`: default summary page size, capped by page maximum.
- `BATCH_PAGE_MAX=100`: summary page and observed-ID lookup ceiling.
- `BATCH_TERMINAL_WINDOW_SECONDS=86400`: terminal coverage and traversal grace.
- `BATCH_METADATA_CHUNK_SIZE=100`: identities per detail query.

PostgreSQL synthetic tests instrument actual SQL: histories of 10, 100, and 1000
rows return five summaries with three queries (count, page, scalar existence),
zero document/title/extraction/review queries, and payloads of 1685–1687 bytes.
A separate existence query keeps materialized summaries at the requested limit.
With metadata chunk size 3, 1 and 10 completed documents with two review IDs each
use 2 and 11 metadata queries respectively. Counts and database scan work can
still grow with history; these are query/payload measurements, not production
speedup claims. Composite owner/time/ID indexes support history, active, and
terminal keysets; tests inspect PostgreSQL plans over 10000 synthetic rows.
