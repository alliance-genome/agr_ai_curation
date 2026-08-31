# Benchmark PostgreSQL persistence

PostgreSQL is the benchmark execution system of record. A benchmark job stores
the canonical suite specification and complete resolved plan alongside their
suite, catalog, plan, configuration, code, and aggregate-input digests. Each
resolved plan cell is also projected into a relational row so workers and
readers can claim and page work without loading the plan JSON.

The public persistence schema is project-agnostic. Input provenance consists
only of a registered `resolver`, opaque `reference`, `version`, and SHA-256
`digest`. Source documents and other input binaries are never stored in these
tables.

## Ownership and lineage

`owner_subject` is the stable OIDC subject for either a user or service
principal. Every repository read and destructive operation is scoped to that
subject. A composite foreign key requires a rerun and its source job to have
the same owner. Each rerun cell points to the matching source cell, and a
database trigger requires that cell to belong to the job named by
`rerun_of_job_id`.

## Lifecycle and deletion

Jobs use `queued`, `running`, `completed`, `completed_with_failures`,
`cancel_requested`, `cancelled`, or `failed`. Cells use `queued`, `running`,
`succeeded`, `failed`, or `cancelled`. Database checks keep timestamps, leases,
counters, envelopes, and failures consistent with those states.

Once a job, cell, or invocation is terminal, a database trigger rejects all
updates. A cell cannot become terminal while an invocation is running, and a
terminal cell rejects later invocation inserts, updates, or deletes. Only terminal
jobs may be hard-deleted. That deletion cascades to the job's cells, invocations,
and replay events; references from later reruns remain restrictive so lineage
cannot be silently severed.

## Results, paging, and replay

Successful cell envelopes are JSONB and are accepted only when their serialized
UTF-8 JSON size is at most `BENCHMARK_MAX_ENVELOPE_BYTES` (default 10 MiB).
The repository installs the configured limit transaction-locally and a database
trigger computes the authoritative size, so direct writes retain the default
bound and cannot forge `envelope_size_bytes`.
Job and cell list projections intentionally exclude envelope JSON; cell detail
is the complete-result boundary.

Job, cell, invocation, and event reads use deterministic keyset/ordered
pagination. `BENCHMARK_DEFAULT_PAGE_SIZE` defaults to 50 and requests are
capped by `BENCHMARK_MAX_PAGE_SIZE`, which defaults to 200. Partial indexes
separately support oldest-queued claims and expired running leases. Replay
events have a job-local monotonically increasing sequence allocated while the
job row is locked.
