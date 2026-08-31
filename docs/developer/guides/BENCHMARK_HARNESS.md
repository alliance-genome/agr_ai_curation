# Developer Benchmark Harness

The benchmark harness runs checked-in, versioned cases against explicit model
routes without changing curator-facing model defaults. It is developer-only,
disabled by default, and returns scoring records without persisting reports.

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

Set `BENCHMARK_ROOT` to the benchmark package for the active deployment. The
Alliance Docker deployment uses `/runtime/packages/alliance/benchmarks`; when
running the CLI from a repository checkout, use `packages/alliance/benchmarks`.

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
targeted execution below `/api/admin/benchmarks`. Every route requires the
canonical `ADMIN_EMAILS` allowlist policy; the feature gate returns 404 when
disabled.

Operational concurrency, matrix/case/result caps, timeouts, retries, output
preview/inline limits, and all adjudication bounds are documented under
`BENCHMARK_*` in `.env.example`. `BENCHMARK_ADJUDICATION_ENABLED` defaults to
false. When explicitly enabled, only eligible records use the direct
`gpt-5.6-sol` structured-output adjudicator; case, turn, tool, timeout, retry, and
result-size settings bound that path.

Case-run responses contain stable target/route/fixture identity, timing, normalized
failure metadata, bounded redacted output, normalized provider usage, and separate
deterministic/adjudication records. Profile-and-scorer aggregates contain only the
deterministic metrics. Adjudication records retain rubric/prompt/model identity,
reason, confidence, uncertainty, tokens, billed cost when the provider supplies
one, latency, and normalized failure metadata. They are not an upload manifest or
durable report.

The case-run provider-usage slot contains the final normalized provider request
emitted during that case and remains null when the runtime emits no usage. Earlier
requests in a multi-call agent or flow run remain available to request-scoped
telemetry but are not duplicated into this single slot.
