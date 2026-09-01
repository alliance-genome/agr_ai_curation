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

## Validate Without Model Calls

Validation loads every reference and expands the bounded case/route matrix. It
does not construct an agent, call a provider, or execute a flow.

```bash
BENCHMARK_ROOT=packages/alliance/benchmarks \
  python scripts/run_benchmarks.py --validate
BENCHMARK_ROOT=packages/alliance/benchmarks \
  python scripts/run_benchmarks.py --dry-run \
  --profile isolated-gene-agent-v1 \
  --case synthetic-gene-lookup-1 \
  --provider openai \
  --model gpt-5.6-sol
```

## Execute

Set `BENCHMARK_ENABLED=true` only in a developer benchmark environment with the
intended provider credentials and dependencies. Execution uses the same service
contract from the CLI and the admin API:

```bash
BENCHMARK_ENABLED=true \
  BENCHMARK_ROOT=packages/alliance/benchmarks \
  python scripts/run_benchmarks.py \
  --profile isolated-gene-agent-v1 \
  --provider openai \
  --model gpt-5.6-sol
```

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
orchestration client. The verifier requires `token_use=access`, that exact
client identity, valid signature/issuer/time claims, and an endpoint scope. An
absent audience is accepted only in this profile; a present audience must equal
`BENCHMARK_OIDC_AUDIENCE`. The resulting principal is namespaced as
`service:<client_id>`, never as a curator subject. Browser clients and ID tokens
cannot enter this profile.

## Versioned Input Sources

`POST /api/v1/benchmarks/sources/materialize` accepts only the strict
`resolver`, `reference`, `version`, and `sha256` digest contract used by suite
v2. It returns UTF-8 canonical extracted content plus fixed-shape metadata and
an immutable provenance receipt. The route requires `benchmark:source:read`;
the ordinary read/run capabilities do not grant source access.

The public application registers two resolvers at startup:

- `checked_in_fixture` reads only the input references declared by suites
  loaded from `BENCHMARK_ROOT`. Other files beneath that root, including gold
  fixtures, are not source references.
- `local_document` reads the completed processed-JSON artifact of a persisted
  AI Curation document only when the authenticated principal owns that
  document. Its authoritative version is the processing-completion timestamp.

Both resolvers read at most `BENCHMARK_MAX_INPUT_BYTES`, recompute the digest
from the exact returned bytes, and fail when the requested identity is stale.
`BENCHMARK_SOURCE_TIMEOUT_SECONDS` bounds the complete resolver call. URLs,
absolute/traversing fixture paths, request-supplied Python import paths,
unregistered resolver IDs, unversioned documents, and cross-owner documents
are rejected before any benchmark work is queued. Unexpected resolver or
storage failures are normalized to the sanitized `source_unavailable` error.

Queue/worker implementations must call `materialize_plan_inputs(...)` and use
the returned frozen bundle. The function resolves every case and returns
nothing if any source fails, so unresolved or partially verified case
references cannot be handed to a queue.

Private deployments may package an approved resolver in their private
application and pass its instance to
`install_benchmark_input_resolvers(app, extra_resolvers=(resolver,))` during
application construction. The resolver must implement `BenchmarkInputResolver`
and provide a strict Pydantic `reference_schema`; registration IDs are simple
lowercase identifiers. Registration is code/configuration owned: request data
must never select an import path or network destination. Duplicate IDs fail
application construction instead of overriding another resolver. Keep remote
credentials and destination configuration in the private deployment's secret
store; do not add them to this repository, fixture metadata, provenance, or
logs.

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
