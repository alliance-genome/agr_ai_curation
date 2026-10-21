# PDFX queue and client deadline investigation

Tracking: ALL-1273 / KANBAN-1827. On October 21, 2026, the release owner approved
option 2: separate bounded queue and processing allowances. No resubmission,
failed-job recovery, or production deployment is included.

## Historical boundary

On October 19, 2026, nine PDFs were submitted within approximately one minute.
The last two waited behind seven successful serial worker executions. Proxy to
backend handoff took about three seconds; the worker was already ready. This was
backend queue wait, rather than a cold-start or upload-delivery delay.

| Affected job | Backend queue wait | Backend acceptance to completion | Client outcome |
| --- | --- | --- | --- |
| Eighth | About 42 minutes | About 67 minutes 39 seconds | Timed out while provider reported progress |
| Ninth | About 68 minutes | About 80 minutes 58 seconds | Timed out while provider reported pending |

The historical worker used the solo pool. Its concurrency banner did not imply
parallel document execution. Worker task receive/success ordering established
serial processing. The broad merge stage also included sequential page-provenance
resolution: approximately 14 minutes for the eighth job and 6 minutes 45 seconds
for the ninth. Separate PDFX changes have since addressed page-resolution
parallelism and split GPU extraction from CPU finalization.

Evidence is retained with the ticket: authenticated provider status, per-attempt
S3 audit metadata, and centralized worker logs from the incident window. Later
provider success and later cached submissions do not establish that AI Curation
ingested the original result or recovered the curator's failed upload.

## Approved client contract

- `PDF_EXTRACTION_QUEUE_TIMEOUT_SECONDS` defaults to 5400 (90 minutes). It starts
  when status polling begins after submission, and covers pending/warming states.
- `PDF_EXTRACTION_TIMEOUT` retains its 3600-second default for processing, and
  for the existing submission/HTTP operations. The processing clock starts once,
  on the first observed `started`, `progress`, or `running` status. It never resets
  on progress, retries, or a return to pending. These are client-observed phases,
  not a reconstruction of the provider's exact worker start timestamp.
- Total status polling is bounded by queue plus processing allowances. Status
  requests use the lesser of remaining time and
  `PDF_EXTRACTION_STATUS_REQUEST_TIMEOUT_SECONDS` (default 30), so a hung request
  cannot use the much longer extraction timeout. Request timeouts are retried
  within the same allowance. Sleeps also respect remaining time.
- A cache hit or fast completion can arrive directly from pending. Real provider
  failures still stop immediately. User cancellation is checked between requests,
  including after a request timeout, and uses the existing provider cancel path.
- Queue expiry and processing expiry remain `polling_timeout` failures, with
  `poll_queue` or `poll_processing` boundaries in the existing failure receipt
  and sanitized Sentry context. Queue expiry has distinct curator-facing text.
- The default browser wait (`VITE_PDF_PROGRESS_TIMEOUT_MS`, 9000000 ms) and
  backend progress stream cover queue plus processing allowances. Align the
  browser setting when changing either allowance. An
  explicit `PDF_PROCESSING_SSE_TIMEOUT_SECONDS` still overrides its lifetime.
  Stale-job reconciliation measures inactivity, not total job age: each successful
  poll's progress callback refreshes durable activity. Its default two-hour
  inactivity bound exceeds either individual default allowance. When increasing
  queue wait or lowering `PDF_EXTRACTION_TIMEOUT`, explicitly set
  `PDF_JOB_STALE_TIMEOUT_SECONDS` above the longest intended interval without
  successful status responses. This independent abandoned-job safeguard is not
  an overall upload timer.

The historical jobs (about 68 minutes queued, or 42 queued plus 26 processing)
fit these default client allowances. This does not recover historical failed
uploads. Submission, artifact download, and downstream ingestion are separate
operations; the polling sum is not a claimed end-to-end upload deadline.

PDFX's staged pipeline separately bounds queue age (default 86400 seconds) and
execution from first extraction claim (default 2100 seconds). Its limits remain
unchanged and it may fail before the client's allowance expires.

## Acceptance evidence

Deterministic tests exercise completion beyond the old total polling limit,
queue expiry, active expiry, active-to-pending transitions, request timeouts,
cancellation, and sleeps clipped to the remaining allowance. Existing parser,
receipt, API, and failure-observability tests protect surrounding behavior.
Compose contracts exercise defaults and nondefault environment overrides.
No schema migration, new paid submission, or late-result recovery is introduced.
