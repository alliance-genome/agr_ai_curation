# Developer Benchmark Harness

The benchmark harness runs checked-in, versioned cases against explicit model
routes without changing curator-facing model defaults. It is developer-only and
disabled by default. Report generation is local and credential-free; private S3
upload is a separate explicit operation with its own default-off feature switch.

## Definitions

The reusable schemas, loader, and execution service live in
`backend/src/lib/benchmarks/`. Project fixtures live with their package; the
shipped Alliance profiles and synthetic cases are under
`packages/alliance/benchmarks/`.

Profiles declare an agent or configured flow-recipe target, explicit
provider/model routes, case fixture and expected-output references, and scorer
references. Inputs and gold files must be synthetic, redistributable, or otherwise
authorized.

`exact-json` compares the whole output using version 1 exact JSON semantics. For
field-level scoring, use `deterministic-v1` with RFC 6901 JSON-pointer paths and
the `exact`, `normalized_string`, `normalized_identifier`,
`ordered_collection`, `unordered_collection`, `structured`, or `evidence`
comparison. Fields may declare positive weights. Collection fields may select an
item comparison, and evidence fields must list the evidence paths they require.
Catalog validation rejects unknown scorers and malformed configuration before any
model call.

A field can set `ambiguous: true` only to classify an ordinary value, collection,
or evidence mismatch as eligible for supplemental adjudication. Missing required
fields, malformed output, provider failures, and any case containing a mixture of
hard and ambiguous failures remain ineligible. Adjudication never changes the
deterministic score.

Reporting consumes these canonical versioned scoring outcomes without changing
their decisions.

Set `BENCHMARK_ROOT` to the benchmark package for the active deployment. The
Alliance Docker deployment uses `/runtime/packages/alliance/benchmarks`; when
running the CLI from a repository checkout, use `packages/alliance/benchmarks`.

The checked-in Alliance release profiles use this exact ordered route matrix:

1. `openai` / `gpt-5.6-sol`
2. `openai` / `gpt-5.6-terra`
3. `openrouter` / `deepseek/deepseek-v4-pro-0813`
4. `openrouter` / `google/gemini-3.7-flash`
5. `openrouter` / `qwen/qwen3.8-27b`

Each profile route is the requested provider/model identity. Runtime usage and
reports preserve that requested identity separately from the actual provider and
model returned by provider telemetry. In particular, an OpenRouter request may
report the upstream provider that served it; that actual route is evidence, not a
rewrite of the requested route. Missing actual-route telemetry remains missing.

## Developer CLI

The CLI uses only the asynchronous API, not this guide's legacy in-process
runner. See [Benchmark CLI](BENCHMARK_CLI.md) for commands, credentials and
recovery behavior. Old profile/provider override and dry-run flags are removed.
The remaining legacy API/engine removal is separately owned.

```bash
python scripts/run_benchmarks.py catalog targets
python scripts/run_benchmarks.py validate --request preview.json
```

## Legacy admin API (not the CLI protocol)

The protected API exposes profile and case discovery, dry-run validation, and
targeted execution below `/api/admin/benchmarks`. Profile discovery, case
discovery, and validation require `benchmark:read`; execution requires
`benchmark:run`. Bearer callers must present an OIDC access token validated
against `BENCHMARK_OIDC_ISSUER_URL`, `BENCHMARK_OIDC_AUDIENCE`, and
`BENCHMARK_OIDC_ALLOWED_CLIENT_IDS`, with a scope configured for the capability
in `BENCHMARK_OIDC_READ_SCOPES` or `BENCHMARK_OIDC_RUN_SCOPES`. Browser callers
receive the same capabilities only through groups explicitly configured in
`BENCHMARK_OPERATOR_READ_GROUPS` or `BENCHMARK_OPERATOR_RUN_GROUPS`. These
scope and group mappings default to blank and therefore deny access; the
`X-API-Key` testing bypass is not accepted by benchmark routes. The stable
authorization contract also defines `benchmark:cancel`, `benchmark:delete`,
and `benchmark:source:read`. The feature gate continues to return 404 when
disabled.

AWS Cognito client-credentials access tokens use a separate, default-off
profile because they can omit `aud` and `sub`. Enable
`BENCHMARK_OIDC_COGNITO_M2M_ENABLED` only with a Cognito user-pool issuer and
set `BENCHMARK_OIDC_COGNITO_M2M_CLIENT_ID` to the one dedicated confidential
orchestration client. When enabled, this profile is the only accepted bearer
profile and `BENCHMARK_OIDC_ALLOWED_CLIENT_IDS` is ignored. The verifier
requires `token_use=access`, that exact client identity, valid
signature/issuer/time claims, and an endpoint scope. An absent audience is
accepted only in this profile; a present audience must equal
`BENCHMARK_OIDC_AUDIENCE`. The resulting principal is namespaced as
`service:<client_id>`, never as a curator subject. Browser clients and ID
tokens cannot enter this profile.

## Versioned Input Sources

`POST /api/v1/benchmarks/sources/materialize` accepts only the strict
`resolver`, `reference`, `version`, and `sha256` digest contract used by suite
v2. It synchronously materializes and verifies the source, freezes its canonical
bytes in the configured private snapshot store, and returns a token-free
`FrozenBenchmarkInputSnapshot` receipt. The response contains the immutable
snapshot ID, digest, source version, content type and size, sanitized provenance,
owner and service-principal identities, creation time, and internal blob
reference; it does not return source content. The route requires
`benchmark:source:read`; the ordinary read/run capabilities do not grant source
access.

The public application registers three resolvers at startup:

- `checked_in_fixture` reads only the input references declared by suites
  loaded from `BENCHMARK_ROOT`. Other files beneath that root, including gold
  fixtures, are not source references.
- `local_document` reads the completed processed-JSON artifact of a persisted
  AI Curation document only when the authenticated principal owns that
  document. Its authoritative version is the processing-completion timestamp.
- `frozen_snapshot` reuses canonical bytes from an existing owner-accessible
  immutable snapshot. It requires the matching digest and source version and
  performs no remote source contact.

### Transfer saved canonical contents

Both endpoints below require `benchmark:source:read` and `BENCHMARK_ENABLED`.
They reject `X-Benchmark-Delegated-Source-Authorization`, including on otherwise
authorized requests: saved-byte transfer does not access ABC or another original
source. They create no jobs and grant no execution permission. Normal submission
still requires the run capability and verified initiating curator.

`POST /api/v1/benchmarks/sources/snapshots` accepts **raw UTF-8 bytes**, not an
object wrapping `content`, an arbitrary source reference, or an upload URL:

```http
POST /api/v1/benchmarks/sources/snapshots
Authorization: Bearer <source-read credential>
Content-Type: text/markdown
X-Benchmark-Content-Digest: sha256:<64 lowercase hex>

# Results
The exact saved paper text goes here.
```

Supported media types are `text/plain`, `text/markdown`, `application/json`
(the existing nonempty pipeline-element list), and `application/xml` (the
installed scientific-document parser). Optional `; charset=utf-8` is accepted;
compressed bodies and other encodings are not. The server authenticates before
reading the bounded body, verifies its SHA-256, and validates it with the same
document decoder used for frozen execution. It stores the **original bytes**,
without newline, Unicode, or Markdown normalization.

Success is HTTP 200 with the existing `FrozenBenchmarkInputSnapshot` receipt.
Its server-generated provenance has resolver `uploaded_document`, source version
`1`, and a sorted compact JSON reference:

```json
{"content_type":"text/markdown","digest":"sha256:<verified digest>","schema":"uploaded_document/v1"}
```

This records an authenticated upload, not verified ABC/local-document provenance,
paper identity, or an execution context. Original corpus provenance stays with
the caller. `uploaded_document` is a receipt namespace, not a registered resolver;
reuse the receipt through `frozen_snapshot`. Content type participates in upload
identity, so the same bytes interpreted under different types do not reuse the
wrong receipt. Repeated/concurrent equivalent uploads by the same owner reuse a
verified immutable receipt; another owner receives separate metadata.

`GET /api/v1/benchmarks/sources/snapshots/{snapshot_id}/content` returns the exact
verified bytes of an owned snapshot, including snapshots produced by the existing
materialize endpoint. It returns the stored Content-Type, Content-Length,
`X-Benchmark-Content-Digest`, attachment disposition and `nosniff`; it returns no
filesystem path, blob reference, or signed storage URL. Compare the returned bytes
and digest with the original receipt before retaining them as corpus contents.
All transfer responses and errors use `Cache-Control: no-store`. Missing and
other-owner snapshots both return 404 before any blob read.

`BENCHMARK_MAX_INPUT_BYTES` bounds uploads, downloads, and actual blob reads;
`BENCHMARK_SOURCE_TIMEOUT_SECONDS` also bounds receiving an upload body. Bad
digests return 409, oversized inputs 413, unsupported media/encoding 415,
invalid documents 422, upload receive timeouts 408, and unavailable/corrupt
storage 503. Source errors use `{"detail":{"error":"<code>","message":"<safe text>"}}`.
Unknown upload outcomes can be recovered by repeating the exact same bytes and
content type; never infer that a lost response means no snapshot was created.

For execution, use the returned `snapshot_id` as reference and retain the receipt's
`source_version` and digest:

```json
{"resolver":"frozen_snapshot","reference":"<snapshot UUID>","version":"1","digest":"sha256:<verified digest>"}
```

The version above is `1` only for a new uploaded-document receipt. Snapshots
materialized from other sources retain those sources' original versions. Snapshot
IDs and service ownership are local to the selected target; transferring corpus
bytes to another target creates that target's own uploaded receipt.

All three resolvers read at most `BENCHMARK_MAX_INPUT_BYTES`, recompute the digest
from the exact returned bytes, and fail when the requested identity is stale.
`BENCHMARK_SOURCE_TIMEOUT_SECONDS` bounds the complete resolver call. URLs,
absolute/traversing fixture paths, request-supplied Python import paths,
unregistered resolver IDs, unversioned documents, and cross-owner documents
are rejected before any benchmark work is queued. Unexpected resolver or
storage failures are normalized to the sanitized `source_unavailable` error.

Remote source materialization may carry
`X-Benchmark-Delegated-Source-Authorization: Bearer <opaque-token>` separately
from the ordinary `Authorization` header that authenticates the Benchmark API
caller. The delegated header is accepted only when exactly one selected resolver
identity declares delegated authorization support. It is limited by
`BENCHMARK_DELEGATED_SOURCE_AUTH_MAX_BYTES`; blank, malformed, oversized,
unexpected, missing-required, or multi-resolver delegated authorization fails
before source I/O with the sanitized `invalid_delegated_authorization`,
`unexpected_delegated_authorization`, or `missing_delegated_authorization`
contract. Credential-free resolvers never receive the opaque bearer, and the
credential is discarded after synchronous materialization rather than persisted
or sent to a worker.

Submission implementations must call
`materialize_and_freeze_plan_inputs(...)` before creating queueable jobs or
cells. The function validates delegated resolver selection, resolves and verifies
every case, enforces the per-input limit plus
`BENCHMARK_MAX_MATERIALIZED_SUBMISSION_BYTES`, and freezes every canonical input
before returning snapshot IDs. It returns nothing if any source fails, so
unresolved, partially verified, or token-dependent references cannot be handed
to a queue. Repeated configurations and cells reference the same immutable
snapshot instead of duplicating source content.

`BENCHMARK_SNAPSHOT_STORE_BACKEND=filesystem` is the fresh-clone Compose default;
`BENCHMARK_SNAPSHOT_STORE_PATH` points at its durable private named volume.
Deployments may select `s3` with `BENCHMARK_SNAPSHOT_S3_BUCKET` and
`BENCHMARK_SNAPSHOT_S3_PREFIX`; that bucket must be private and have versioning
enabled. Both stores use verified SHA-256 content addressing and deduplicate
canonical bytes.

Private deployments may package an approved resolver in their private
application and pass its instance to
`install_benchmark_input_resolvers(app, extra_resolvers=(resolver,))` during
application construction. The resolver must implement `BenchmarkInputResolver`
and provide a strict Pydantic `reference_schema`. It must also declare
`delegated_authorization` as `required`, `optional`, or `unsupported` using the
public resolver capability enum; registration fails when the declaration is
absent. Registration IDs are simple lowercase identifiers. Registration is
code/configuration owned: request data must never select an import path or
network destination. Duplicate IDs fail application construction instead of
overriding another resolver. Keep remote credentials and destination
configuration in the private deployment's secret store; do not add them to this
repository, fixture metadata, provenance, snapshots, or logs.

Registration validates resolver IDs during application construction, but the
checked-in suite catalog is loaded and memoized only when an enabled source
request first materializes input. This keeps ordinary backend startup and health
routes independent of optional benchmark package configuration.

Operational concurrency, matrix/case/result caps, timeouts, retries, output
preview/inline limits, and all adjudication bounds are documented under
`BENCHMARK_*` in `.env.example`. `BENCHMARK_ADJUDICATION_ENABLED` defaults to
false. When explicitly enabled, only eligible records use the direct
`gpt-5.6-sol` structured-output adjudicator; case, turn, tool, timeout, retry, and
result-size settings bound that path.

Case-run responses contain stable target/route/fixture identity, timing, normalized
failure metadata, bounded redacted output, normalized provider usage, and separate
deterministic/adjudication records. Profile, requested-route, and scorer aggregates
contain only the
deterministic metrics. Adjudication records retain rubric/prompt/model identity,
reason, confidence, uncertainty, tokens, billed cost when the provider supplies
one, latency, and normalized failure metadata. They are not an upload manifest or
durable report.

The case-run provider-usage slot contains the final normalized provider request
emitted during that case and remains null when the runtime emits no usage. Earlier
requests in a multi-call agent or flow run remain available to request-scoped
telemetry but are not duplicated into this single slot.

## Reports and Immutable Manifests

`src.lib.benchmarks.reporting` turns canonical case runs and their embedded
`BenchmarkScoringRecord` values into a
versioned developer report. It provides per-case, per-agent, actual-route,
cross-route, aggregate accuracy/cost/latency/usage, and normalized failure
summaries. Deterministic pass/partial/fail and weighted results remain separate
from supplemental adjudication status/outcomes. Provider-execution and
adjudication billed costs are reported separately and grouped by telemetry source
and unit; a missing cost stays explicitly missing and is never estimated.

Create `ReportProvenance` with the logical run ID, fixed generation timestamp,
and the exact profile, config, and code revisions. Then call
`build_benchmark_report(runs, provenance)` and `build_artifact_bundle(report)`.
The resulting
canonical JSON is stable for identical inputs and can be reviewed or saved
locally without AWS credentials.

The report/manifest schemas are an allowlist: outputs, prompts, source documents,
evidence text, authorization headers, and exception bodies have no artifact
fields. Serialization also fails closed on credential-like values and any
newline-separated regular expressions configured through
`BENCHMARK_ARTIFACT_SECRET_PATTERNS`. Do not log pre-serialization inputs or
replace this contract with general-purpose model dumps.

## Private S3 Upload

Upload requires an approved versioned bucket and prefix supplied by the caller.
Set `BENCHMARK_ARTIFACT_UPLOAD_ENABLED=true`, then construct the store with
`create_configured_s3_artifact_store(bucket=..., prefix=...)` and call
`upload_bundle(...)`. The client uses the standard AWS environment/role
resolution chain; this code does not accept or materialize credentials.

Reports use content-addressed object keys and resumable multipart uploads. Every
existing object, uploaded object, and resumed part is checked against the
S3-returned SHA-256 checksum before it is accepted; multipart object checks use
S3's composite checksum semantics. The
logical run's `manifest.json` uses a conditional create, so a different manifest
cannot silently replace it. A retry of identical content returns the existing
version receipt. The stored manifest includes the report object's bucket, key,
version ID, ETag, size, and SHA-256; the call also returns the manifest's own
version receipt. No upload or recovery path requires broad delete permission.

Artifact/part sizes, retries/backoff, operation timeout, connection concurrency,
secret patterns, and the upload switch are documented under
`BENCHMARK_ARTIFACT_*` in `.env.example`.
