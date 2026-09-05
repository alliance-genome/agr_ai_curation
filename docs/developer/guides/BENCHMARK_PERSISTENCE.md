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

New jobs also require a token-free `curator_context` captured by trusted
admission code from the initiating logged-in AI Curation curator. It stores
the subject, authentication provider, issuer/provider username when present,
local user ID, and frozen authenticated group IDs; it does not store raw
claims, email, cookies, or tokens. This execution
identity is separate from `owner_subject`, which may identify the portal
service. The repository accepts only the typed context, but constructing that
type is not authentication: admission must verify the human and separately
enforce benchmark capabilities, including for explicit reruns.
Capture uses the same provider-group-to-internal-group mapping as ordinary
curator flow execution. For example, `flybase-curators` becomes `FB`; raw
provider role names are not injected into scientific prompts. Standalone agent
construction and execution both receive those frozen active groups.

Migration `j7k8l9m0n1o2` leaves historical contexts NULL rather than guessing a
human from service ownership. New inserts require a context, and a database
trigger prevents its replacement even on queued jobs. The worker rejects
missing or malformed context before reading input bytes. These historical
jobs remain inspectable; they cannot be upgraded into executable jobs by
editing their identity.

Before invoking a target, the worker now rechecks the current Cognito account
and all group pages, then the local active user row. The captured issuer must
match the configured user pool, and the returned username and stable subject
must match the receipt. Missing provider locators, disabled accounts, removed
mapped execution groups, unavailable lookups, and unsupported provider types fail closed.
Additional groups do not change an accepted run. The worker overwrites
content-supplied user/group fields with the frozen verified context.

The read-only adapter uses IAM credentials for
[`AdminGetUser`](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_AdminGetUser.html)
and [`AdminListGroupsForUser`](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_AdminListGroupsForUser.html),
not a saved human bearer. Deployment needs those two actions scoped to the
configured user pool; this source change does not grant them. AdminGetUser
contributes to Cognito MAU billing. SDK connect/read timeouts and total attempts
are configured by `BENCHMARK_CURATOR_AUTH_TIMEOUT_SECONDS` and
`BENCHMARK_CURATOR_AUTH_MAX_ATTEMPTS`. Generic OIDC and dev bypass have no
token-free current-membership adapter implemented and cannot execute these
jobs. The auth adapter is tested with synthetic responses, not live Cognito.

Each suite case can now carry an explicit `user_query`, copied into the resolved
case and cell and included in their digests. New standalone-agent jobs require
a nonblank query; flows may use their saved instructions without an additional
query. Admission must capture curator intent separately from paper content.
The worker no longer parses paper JSON as runtime arguments: it builds messages,
query, document identity and user/group context from the plan and preparation.

`document_preparation.prepare_frozen_document` provides the internal normal
ingestion operation for a new, already-recorded document identity. It checks
the frozen digest, decodes supported extracted JSON/text/Markdown/XML, verifies
the active local owner, writes exclusive source/element artifacts under a
UUID-only storage path, creates owned SQL/Weaviate documents, and calls the
same hierarchy/chunking/figure/vector pipeline as normal imports. Original
page metadata is retained. The SQL file hash is a scoped duplicate key; the
vector metadata checksum is the raw source checksum. Existing document IDs
cannot be replayed. Failures retain identified partial state for explicit
cleanup, not automatic repeated model calls.

The worker calls this operation through its preparation coordinator. Durable
tracking is available through `BenchmarkPreparationRepository`, using
`document_preparation.started` and `.completed` job events. `begin()` locks
the active leased job, verifies snapshot ownership/membership, and either
records a fresh document ID or returns the completed receipt for that same
job/snapshot. The caller must commit the start before performing preparation.
A started-only history is uncertain and cannot authorize a retry. Completion
requires the recorded ID and frozen digest, with a live lease and no requested
cancellation. The lease is checked again after obtaining the row lock.

`PreparationStageCheckpoint` now commits a fenced `document_preparation.stage`
event before each artifact, vector-document, hierarchy, chunking, figure-location,
vector-storage, and ready stage. Frozen preparation requires this callback;
normal imports can continue using the shared indexer without a benchmark
checkpoint. Lease/cancellation errors are checked outside the hierarchy
fallback. Events identify preparation separately and record cumulative elapsed
milliseconds with `measurement="elapsed_time_only"`; they do not claim
per-provider invocation, token, embedding, or cost measurements.

`preparation_service.prepare_job_document` now coordinates current curator
authorization, the journal claim, verified snapshot bytes, committed start,
normal ingestion with committed stage checkpoints, and committed completion.
It obtains identity from the immutable job context, not caller-supplied paper
data. Reuse rechecks authorization and returns the completed receipt without
repeating indexing. Real PostgreSQL tests verify a separate connection sees
the start before vector creation, and that revoked authorization blocks reuse.

The worker keeps its heartbeat and cell timeout around preparation and target
execution, then rechecks authorization after preparation. The target invocation
observer is installed only around target execution, not around preparation.
Four-format integration tests now exercise the worker, real coordinator,
journal, verified blob reads, files, SQL, normal chunk serialization and storage
verification, the real agent benchmark adapter, and the package-exported
`read_chunk` tool through successful cell
completion. The tool returns frozen text and deterministic evidence spans from
the properties emitted by normal storage. A different document ID cannot read
those chunks. Model/vector network calls and the target model are synthetic.
The synthetic model stream emits canonical structured output and separate
completion text; the actual adapter/worker persist the structured result and
durable invocation route/token fields, preserving unavailable billing as NULL.
The coordinator passes the connection wrapper to storage, which opens its own
session inside its worker thread; a raw SDK client is not that interface.

Production readiness still requires trusted submission capture, prepared-copy
library/API isolation and cleanup, and complete preparation accounting.
These tests do not validate live embeddings or provider execution. Cleanup must
not be inferred from terminal job status alone: cancelled executor/thread work
can outlive the asyncio caller. The SQL-only `delete_terminal_job` rejects jobs
with a preparation-start event, retaining their recovery identities rather than
erasing the journal and orphaning copies. Jobs without preparation retain their
existing terminal deletion behavior. Prepared copies are retained until a
quiescence-aware vector/file/SQL cleanup lifecycle is provided.

Before recording successful preparation and on every reuse, the coordinator
verifies the active SQL owner, completed benchmark document status, exact
UUID-scoped artifact paths, original source SHA-256/size and processed JSON
SHA-256 recorded in the receipt. Missing files, altered bytes and replacement
symlinks fail closed without re-indexing. This verifies file integrity, not
immutability of the separate vector collection. The canonical curator document
access guard now returns 404 for benchmark copies, including the shared guard
used by rename/delete/reprocess/re-embed and viewer routes. Its SQL-owned listing
also excludes them while retaining normal NULL viewer-mode rows. Internal
ingestion and document-tool ownership paths are separate and remain available.
Phantom cleanup excludes benchmark SQL rows from its missing-vector deletion
pass, because preparation creates SQL before vectors. Weaviate-backed library
listing reads owner-scoped benchmark IDs from SQL and adds a contains-none ID
filter before both page retrieval and aggregate counting. Existing search/date
filters are combined with that exclusion, and final SQL ownership filtering
also excludes benchmark rows. Other document-selection surfaces still need an
audit; these filter tests use the installed SDK and mocked vector responses.
The detailed-library helper also rejects owned benchmark copies from SQL before
contacting vectors; chat document load/session hydration use this helper.
Ordinary batch admission excludes benchmark rows from its owned-document query.
Direct flow and custom-agent test APIs reject persisted benchmark document IDs
before starting their runtime. This narrow exclusion does not replace existing
ownership or request validation; internal benchmark execution bypasses these
curator-only API boundaries and retains normal document-tool access.

## Durable worker boundary

The `benchmark_worker` Compose service is the only asynchronous execution
process. It reads input bytes exclusively through
`BenchmarkSnapshotRepository.read_verified`; it never invokes a remote input
resolver or receives delegated source credentials. The worker polls and claims
nothing unless both `BENCHMARK_WORKER_ENABLED` and
`BENCHMARK_EXECUTION_ENABLED` are true. Both default to false, and the
production Compose definition fixes both gates to false.

Worker concurrency defaults to one. `BENCHMARK_WORKER_CONCURRENCY`,
`BENCHMARK_WORKER_LEASE_SECONDS`, `BENCHMARK_WORKER_HEARTBEAT_SECONDS`, and
`BENCHMARK_CELL_TIMEOUT_SECONDS` tune the isolated worker lifecycle. A provider
call is checkpointed as a running invocation before dispatch. Every heartbeat,
invocation update, cell result, and terminal job update requires the current
lease owner and an unexpired lease.

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

An expired running cell is never requeued. Startup recovery locks the affected
job and cell rows, fails any running invocation, and terminalizes the cell with
`{"category":"interrupted_uncertain","retryable":false}` in one transaction.
Queued sibling cells remain claimable. Repeating interrupted work requires a
new linked rerun job.

## Results, paging, and replay

Successful cell envelopes are JSONB and are accepted only when their serialized
UTF-8 JSON size is at most `BENCHMARK_MAX_ENVELOPE_BYTES` (default 10 MiB).
The repository installs the configured limit transaction-locally and a database
trigger computes the authoritative size, so direct writes retain the default
bound and cannot forge `envelope_size_bytes`.
Job and cell list projections intentionally exclude envelope JSON; cell detail
is the complete-result boundary.

Resolved agent cells consume the validated structured object in
`STRUCTURED_RESULT.data.result`; the text in `RUN_FINISHED.data.response` is
not an extraction result. Document-aware agents receive the same normal
`DocumentContext` for construction and streaming/tool execution.

Resolved flow cells require a successful `FLOW_FINISHED.data` receipt, then
resolve its extraction references through persisted records scoped to the
execution owner, document, flow run, origin session, and FLOW source kind.
Completion status, output attachments, and result-reference metadata are not
stored as extraction envelopes. Missing/out-of-scope references and malformed
envelopes fail the cell. The existing domain-envelope normalizer validates
canonical payloads and converts extractor `curatable_objects` into canonical
`extracted_objects`, preserving authoritative persisted provenance.

Flow output has the versioned shape
`{"schema_version":"benchmark-flow-extractions/v1","envelopes":[...]}`.
Every referenced envelope is retained in receipt order, including a valid
envelope containing zero extracted objects. A flow with no extraction receipt
is not an extraction benchmark success. Multiple envelopes stay distinct:
they are not merged across domain packs or reduced to an arbitrary first
result. This is an execution-result contract, not a scoring policy.

Successful cells also store canonical SHA-256 envelope and execution-result
digests. Invocation rows preserve the frozen route slot, requested and actual
provider/model, reasoning effort, routing attempt, sequence, wall-clock timing,
latency, nullable token counts, failure status, and the provider-reported billed
amount/unit/source tuple. Missing provider values remain null; exact billed cost
is never estimated.

Job, cell, invocation, and event reads use deterministic keyset/ordered
pagination. `BENCHMARK_DEFAULT_PAGE_SIZE` defaults to 50 and requests are
capped by `BENCHMARK_MAX_PAGE_SIZE`, which defaults to 200. Partial indexes
separately support oldest-queued claims and expired running leases. Replay
events have a job-local monotonically increasing sequence allocated while the
job row is locked.
