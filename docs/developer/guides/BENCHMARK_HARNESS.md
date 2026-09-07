# Benchmark execution API

The public benchmark component is an execution service. It validates version 2
suites, freezes source documents, resolves an immutable cell plan, runs cells
asynchronously, and retains lifecycle data in PostgreSQL. Gold data, biological
comparisons, accuracy reporting, and administrator presentation belong to the
separate private benchmark portal.

The API is disabled by default. `BENCHMARK_ENABLED` gates source routes,
`BENCHMARK_API_ENABLED` gates catalog and lifecycle routes, and
`BENCHMARK_EXECUTION_ENABLED` gates provider execution. Enabling discovery does
not enable execution.

## Suite and catalog contract

Suites under `BENCHMARK_ROOT/suites` use `schema_version: 2`. Each case names an
agent or flow and a versioned, digest-pinned input reference. Configurations map
route slots such as `supervisor`, `agent:<id>`, and `validator:<id>` to explicit
provider/model choices. `POST /api/v1/benchmarks/plans/validate` resolves
defaults and returns the immutable plan and digest without running a provider.

Use the API-driven CLI described in [Benchmark CLI](BENCHMARK_CLI.md):

```bash
python scripts/run_benchmarks.py catalog targets
python scripts/run_benchmarks.py validate --request preview.json
python scripts/run_benchmarks.py submit --request submission.json --idempotency-key example-run
```

The former `/api/admin/benchmarks` endpoint, profile-v1 format, synchronous
runner, built-in gold scoring/adjudication, aggregate reports, and benchmark
artifact S3 uploader have been removed. There is no compatibility endpoint.

## Source preparation and frozen documents

`POST /api/v1/benchmarks/sources/materialize` accepts a resolver, opaque
reference, version, and SHA-256 digest. Discovery and preparation-capable
resolvers also support `/sources/discover` and `/sources/prepare`. Source calls
require `benchmark:source:read`; an optional delegated source credential is
request-local and is never persisted or passed to workers.

The built-in `checked_in_fixture` resolver reads only references declared by
version 2 suites. `local_document` reads a completed document owned by the
authenticated principal. `frozen_snapshot` reuses an already verified snapshot.
Deployments may register additional strict resolvers during application setup.
URLs, traversal paths, unregistered resolver IDs, stale versions, digest
mismatches, and cross-owner access fail before job admission.

Saved canonical bytes may be transferred with
`POST /api/v1/benchmarks/sources/snapshots` and retrieved by their owner with
`GET /api/v1/benchmarks/sources/snapshots/{snapshot_id}/content`. The upload
route verifies the caller-supplied digest and document format and stores the
original bytes without normalization. Repeating the same owner/content-type/
content combination reuses the immutable receipt.

The snapshot store may use the durable filesystem backend or a private,
versioned S3 bucket configured with `BENCHMARK_SNAPSHOT_STORE_BACKEND` and the
`BENCHMARK_SNAPSHOT_S3_*` settings. This S3 backend stores frozen input bytes;
it is not the removed first-generation result/report uploader.

## Jobs, cells, events, and deletion

Job submission verifies the suite, catalog, frozen inputs, initiating curator,
and configured execution gate before creating durable work. Workers claim
leased cells and invoke `execute_resolved_agent_cell` or
`execute_resolved_flow_cell`. Each successful cell retains its structured JSON
envelope, digest, and ordered provider invocation telemetry. Requested and
actual provider/model identities, reasoning effort, latency, tokens, billed
cost when supplied, and sanitized failures remain separate.

Use job, cell, invocation, and event endpoints under `/api/v1/benchmarks` to
observe work. Event cursors are resumable; reconnecting never resubmits a job.
Cancellation is explicit. Authorized deletion follows the stable lifecycle
contract and removes eligible PostgreSQL execution records; it does not mutate
source systems or private portal imports.

## Authorization and operational limits

Bearer tokens are validated against the configured OIDC issuer, audience,
client allowlist, and endpoint scopes. Cognito client-credentials tokens use the
separate default-off M2M profile. Browser users receive only capabilities mapped
from their authenticated groups. No API-key bypass is accepted.

All timeouts, size limits, page sizes, concurrency, leases, event retention,
source limits, snapshot settings, and feature gates are documented under the
`BENCHMARK_*` section of `.env.example`. Provider execution remains off unless
explicitly enabled. Never put provider credentials, delegated tokens, document
contents, or private reference data in suite files, logs, traces, or tickets.
