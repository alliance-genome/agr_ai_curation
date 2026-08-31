# Developer Benchmark Harness

The benchmark harness runs checked-in, versioned cases against explicit model
routes without changing curator-facing model defaults. It is developer-only,
disabled by default, and does not persist reports or scores.

## Definitions

The reusable schemas, loader, and execution service live in
`backend/src/lib/benchmarks/`. Project fixtures live with their package; the
shipped Alliance profiles and synthetic cases are under
`packages/alliance/benchmarks/`.

Profiles declare an agent or configured flow-recipe target, explicit
provider/model routes, case fixture and expected-output references, and scorer
references. Scorer implementations and durable reports are separate concerns.
Inputs and gold files must be synthetic, redistributable, or otherwise authorized.

## Validate Without Model Calls

Validation loads every reference and expands the bounded case/route matrix. It
does not construct an agent, call a provider, or execute a flow.

```bash
python scripts/run_benchmarks.py --validate
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
BENCHMARK_ENABLED=true python scripts/run_benchmarks.py \
  --profile isolated-gene-agent-v1 \
  --provider openai \
  --model gpt-5.6-sol
```

The protected API exposes profile and case discovery, dry-run validation, and
targeted execution below `/api/admin/benchmarks`. Every route requires the
canonical `ADMIN_EMAILS` allowlist policy; the feature gate returns 404 when
disabled.

Operational concurrency, matrix/case/result caps, timeouts, retries, and output
preview/inline limits are documented under `BENCHMARK_*` in `.env.example`.
Case-run responses contain stable target/route/fixture identity, timing, normalized
failure metadata, bounded redacted output, and the normalized provider-usage slot.
They are not an upload manifest or durable report.
