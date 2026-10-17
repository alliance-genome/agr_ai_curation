# PDF-job snapshots and stale recovery

The PDF-job SSE endpoint reads one bounded page in a worker thread. The worker
owns the SQLAlchemy session and closes it before returning response DTOs. It
does not count, reconcile, or write. Selection remains authenticated-user scoped,
uses the inclusive creation-time window, and orders by creation time descending
then job ID descending. REST retains counts, filters, offsets, and read repair.

The stream compares canonical JSON for all returned job fields, excluding the
event timestamp. Message, error, stage, filename, cancellation, and receipt
metadata changes therefore trigger delivery even without a status/progress or
timestamp change. An initially empty selection remains suppressed because the
page performs an initial REST load; a transition from nonempty to empty is sent.
Ticks are awaited sequentially. Disconnect cancellation stops further ticks;
an already-running worker still closes its session when it finishes.

## Recovery ownership

FastAPI lifespan starts one periodic task per process, independent of viewers.
Each pass runs off-loop and acquires a PostgreSQL transaction advisory lock.
Competing processes skip a pass while another owns that lock; there is no
connection held across the interval and no process-local claim of global
leadership. Separate replicas can run successive passes. Shutdown stops further
ticks and awaits an in-flight worker's cleanup.

A pass selects at most `PDF_JOB_RECOVERY_BATCH_SIZE` stale active jobs across
all users and all history, ordered by ID. It uses `FOR UPDATE SKIP LOCKED`.
Eligibility is evaluated on current rows under lock; job writers and REST
read repair also lock their mutation reads. Recent activity, committed terminal
winners, and locked active writers are therefore preserved. The existing stale
timeout, activity timestamp policy, terminal receipts, latest-job check, and
protection of terminal documents remain authoritative.

Each candidate has a savepoint covering job and document changes. Failure rolls
back that candidate and emits a fixed, content-free Sentry report. The keyset
cursor advances past failed candidates and wraps at the end, allowing other
users to progress and failed candidates to retry. A pass-level database failure
rolls back the transaction and retries from the previous cursor on the next
interval. Documents are locked before mutation; no session escapes a pass.

New defaults are conservative operating choices, not measured throughput targets:

- `PDF_JOB_RECOVERY_INTERVAL_SECONDS=60`: delay after each completed pass.
- `PDF_JOB_RECOVERY_BATCH_SIZE=100`: candidates per pass.
- `PDF_JOB_RECOVERY_TIMEOUT_SECONDS=30`: individual SQL statement timeout,
  including lock waits. This is not a total-pass deadline.

Existing stale thresholds and stream poll/timeout defaults are unchanged.
Persistent database errors prevent convergence and are reported; successful
subsequent passes retry. Manual repair of documents with no job remains separate.

## Synthetic validation evidence

The PostgreSQL test uses the same dataset and three ticks for each viewer count,
with recent active jobs so no read repair is needed:

| Viewers | Old list-based snapshot SELECTs | Read-only snapshot SELECTs | Snapshot writes |
| --- | ---: | ---: | ---: |
| 1 | 9 | 3 | 0 |
| 3 | 27 | 9 | 0 |

One separately invoked empty recovery pass executes three SELECT statements:
the transaction-local timeout setting, advisory lock attempt, and bounded
candidate query. That count is independent of viewers. A nonempty pass adds
savepoints, latest-job/document queries, and updates for recovered jobs.

Authentication/provisioning is outside these counts and still happens once at
stream entry through `provision_user_async`; provisioning unit tests cover that
boundary. These are deterministic synthetic statement counts, not production
latency or throughput measurements.

Focused tests live in `tests/unit/api/test_pdf_jobs_stream.py`,
`tests/unit/lib/pdf_jobs/test_pdf_job_recovery.py`, and
`tests/integration/persistence/test_pdf_job_recovery.py`, alongside existing
service, provisioning, and startup tests. PostgreSQL tests exercise competing
owners/writers, terminal documents, latest-job semantics, candidate rollback and
retry, scoped selection, stable ties, and REST totals.
