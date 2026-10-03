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

## Immutable result retrieval

`GET /api/v1/benchmarks/jobs/{job_id}/cells/{cell_id}/result` returns the exact
canonical UTF-8 JSON bytes produced by a successful cell, including its output
and invocation outcomes. It requires benchmark-read capability and the job's
owner identity. Hash the response body directly with SHA-256 and compare it to
`X-Benchmark-Result-Digest` (`sha256:<hex>`); parsing and reserializing JSON can
change numeric representations. Identity and format headers are
`X-Benchmark-Job-ID`, `X-Benchmark-Cell-ID`, `X-Benchmark-Attempt-Count` and
`X-Benchmark-Artifact-Version` (currently `1`). Responses use `Cache-Control: no-store`.

Cell detail exposes the authoritative `attempt_count` in every state, including
failure or cancellation without provider invocation records. Paginated invocation
telemetry remains available through the existing invocation endpoint.

Nonterminal cells return `409 result_not_terminal`. Failed/cancelled cells and
historical successful rows whose full result was not retained return
`409 result_artifact_unavailable`. Historical digests are preserved; unavailable
results are never reconstructed. Corrupt artifacts return
`503 result_artifact_corrupt`. `BENCHMARK_MAX_RESULT_ARTIFACT_BYTES` bounds stored
and returned body bytes (default 16 MiB); lowering it can make an older artifact
return `413 result_artifact_oversize`. Authorization and the database byte bound
apply before content is loaded. Artifacts share terminal-cell immutability and
the existing explicit job deletion lifecycle.

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

### Converting papers into frozen documents

`POST /api/v1/benchmarks/sources/document-conversions` turns a paper into
AI Curation pipeline elements and freezes them as an `application/json`
snapshot that the calling service's jobs can use with the `frozen_snapshot`
resolver. It requires `benchmark:source:read` and a verified curator in
`X-Benchmark-Curator-Authorization`, the same human check used for job
submission. Delegated source credentials are rejected.

- PDF: send the raw bytes as `application/pdf` with
  `X-Benchmark-Content-Digest: sha256:<hex>`. The bytes must match the digest,
  start with `%PDF-`, fit within `BENCHMARK_MAX_INPUT_BYTES`, and arrive within
  `BENCHMARK_SOURCE_TIMEOUT_SECONDS`.
- Source reference: send `application/json`
  `{"source_reference": "<identifier>"}`, where the identifier names a paper
  in the deployment's configured document-source provider. It must be
  non-empty, at most 256 characters, have no leading or trailing whitespace,
  and contain no control characters. AI Curation does not check the
  identifier's format itself: the configured provider resolves it, and an
  identifier the provider does not know fails the conversion with `not_found`.
- Both require an `Idempotency-Key` of at most 255 characters. Keys are scoped
  to the calling service. Repeating a key with the same input and curator
  returns the same conversion without starting it again; reusing it for
  different input or another curator returns 409.

The response is `202 {"conversion_id", "status"}` with a `Location` header.
Poll `GET /api/v1/benchmarks/sources/document-conversions/{conversion_id}` for
`status` (`queued`, `running`, `succeeded`, `failed`), `progress`, `error`
(`{"code", "message"}` when failed), `snapshot` (the same receipt the snapshot
routes return, when succeeded), `conversion_identity`, `created_at`, and
`completed_at`. Only the service that started a conversion can read it; other
callers get 404.

`progress` is `{"stage", "step", "total_steps", "detail", "percent"}` while a
conversion is running, and `null` otherwise. The stages come in order and never go back:
`fetching_source` (step 1: reading the uploaded PDF, or getting the paper from
the configured document source), `extracting_text` (step 2: PDF extraction, or
converting the source's main text) and `saving` (step 3: freezing the converted
document as a benchmark input). They are coarse and real: each is recorded when
the conversion reaches it, and nothing estimates a percentage. A conversion
already running when migration `t7c8d9e0f1a2` applied reports `null` until it
finishes.

While `extracting_text` runs PDF extraction, `detail` is what the PDF reader
(PDFX) reports, mapped from its status: `waking_reader` (its GPU worker is
starting from sleep, reported as progress stage `ec2_starting`, a `starting` or
`stopped` state, or a `warming` status; this takes minutes), `waiting_for_reader`
(queued behind other work) or `reading`. `percent` is PDFX's own reported
percent (completed extraction steps) while reading, 100 once it is complete,
and `null` when PDFX gives none (a queued job's placeholder 0 is never used).
Both are `null` otherwise, including for a source's main text, which needs no
PDF reader. Only changes are recorded (migration `u8d9e0f1a2b3`). The main
app's PDF jobs use the same mapping for their message: "Waking up the PDF reader
(can take a few minutes)", "Waiting for the PDF reader", "Reading the PDF ·
35%". Request errors use the source error envelope, for example
`invalid_reference`, `invalid_document`, `oversize_payload`, `conflict`, and
`not_found`.

Conversion runs in the background and is never retried; a failed conversion
stays failed, and the caller starts a new one with a new key. Failure codes
include `not_found` (the provider does not know the reference, or the paper
has no usable text or PDF), `access_denied`,
`ambiguous_source`, `source_unavailable`, `extraction_failed`,
`invalid_document`, `oversize_payload` (converted elements exceed
`BENCHMARK_MAX_INPUT_BYTES`), `storage_unavailable`, `conversion_failed`, and
`interrupted`. Queued or running conversions older than
`BENCHMARK_DOCUMENT_CONVERSION_STALE_SECONDS` are marked `interrupted` at API
startup, before each new conversion, and whenever a conversion's status is
read, so a conversion lost to a restart shows as failed on the next poll.

`conversion_identity` records what produced the elements: the parser and its
settings, the AI Curation application version (`APP_VERSION`), the page
provenance receipt for PDF extraction, and for source references the
configured provider's ID (`source_provider`), the provider artifact whose bytes
were converted (`source_artifact`: ID and checksum), any figure metadata
artifacts used with main text (`source_figure_metadata`), and the access policy
of the provider source PDF the curator's groups were authorized against
(`source_access`: artifact ID, scope and group IDs). `parser` is `pdfx` when a
PDF was extracted and `source_main_text` when the provider's main text was
used.
The snapshot `source_version` is the SHA-256 of that identity, so it changes
when the application version changes.

How this differs from a curator's document import:

- The snapshot is owned by the calling service, not the curator. The curator
  is recorded on the conversion and in the snapshot reference, so the same
  paper converted for two curators gives two snapshots that share one stored
  blob.
- Nothing is added to the curator's document library, and no per-user parser
  artifacts are written.
- For source references, AI Curation calls the configured document source
  with its own configured credentials and uses the curator's groups to decide which
  restricted PDFs may be read. The curator's `X-Benchmark-Curator-Authorization`
  bearer is never forwarded to the provider. For ABC Literature those
  credentials are the deployment's own machine reader
  (`ABC_LITERATURE_AUTH_MODE=cognito_client_credentials` with a dedicated
  read-only client per environment holding `abc-literature/read`; see
  `docs/developer/integrations/abc_literature/release_config.md`). ABC lets that
  reader see every file, so the curator-group check is the access gate. It
  uses the provider's main text and figure metadata only when the provider
  binds them to that selected PDF; reference-level text and figure metadata are
  ignored. Otherwise it parses the selected main PDF with PDFX. It never asks the
  provider to convert a paper, and when the provider's own conversion is still
  running or has failed it parses the PDF instead of waiting or stopping.
- Conversion uses the deployment's configured document-source provider, as
  curator imports do.

Trust boundary for group-restricted source papers: access is checked once, when
the conversion runs, against the requesting curator's groups. After that the
snapshot, and its content through
`GET /api/v1/benchmarks/sources/snapshots/{snapshot_id}/content` and the
`frozen_snapshot` resolver, is readable by the owning service without any
further group check. The owning service must therefore restrict reuse of an
source-reference conversion to the curator who requested it (recorded as
`curator_subject` in the snapshot reference). `source_access` in the conversion
identity records which access policy authorized the conversion.

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
