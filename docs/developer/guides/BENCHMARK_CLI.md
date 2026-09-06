# Benchmark developer CLI

Run `python scripts/run_benchmarks.py --help` from a checkout. Python 3.11+ and
httpx are required; backend development/test images include them. The CLI uses
one deployment-local `/api/v1/benchmarks` origin. It never imports agents,
provider credentials, scoring or the local execution engine. The old profile,
provider/model override and dry-run flags have been removed, without aliases.
Use HTTPS; HTTP is allowed only on loopback. Redirects and environment proxies
are not used.

## Credentials

Supply credentials through environment/secret tooling, not argument values or
shell-history examples:

- `BENCHMARK_ACCESS_TOKEN`: OAuth access token for standard authorization.
- `BENCHMARK_CURATOR_TOKEN`: initiating human's target-provider credential for
  catalog/suite discovery, validation, submit and rerun; not service identity.
- `BENCHMARK_SOURCE_TOKEN`: source delegation credential, sent only with explicit
  `submit --delegate-source`, never on rerun.

Alternative variable names can be selected with `--access-token-env`,
`--curator-token-env` and `--source-token-env`. Those flags take **names**, not
token values. The server verifies credentials and current authorization; the
CLI does not refresh tokens or infer human identity from a service token.

## Commands

Set `BENCHMARK_API_URL` or pass `--base-url` before the subcommand. Add `--json`
before the subcommand for compact JSON; default output is indented JSON. Results
identify the API origin and preserve upstream IDs/digests/revisions. Explicit
result retrieval can contain private data: direct it only to private destinations.
Routine errors do not echo request content or server error bodies.

```bash
python scripts/run_benchmarks.py catalog targets
python scripts/run_benchmarks.py catalog route_slots
python scripts/run_benchmarks.py catalog models
python scripts/run_benchmarks.py suites
python scripts/run_benchmarks.py suite example.v2
python scripts/run_benchmarks.py validate --request preview.json
python scripts/run_benchmarks.py submit --request submission.json --idempotency-key experiment-2026-01
python scripts/run_benchmarks.py jobs
```

`preview.json` contains `catalog_digest` and exactly one of `suite` or
`checked_in_suite: {suite_id, suite_digest}`. Submit `{suite, plan}` with the
returned plan unchanged. Files are bounded by `BENCHMARK_ADMISSION_MAX_BYTES`.
See [catalog](BENCHMARK_CATALOG.md) and [persistence](BENCHMARK_PERSISTENCE.md)
for exact payload contracts. The CLI does not generate or recompute plans.

Replace uppercase UUID placeholders below with actual returned identifiers:

```bash
python scripts/run_benchmarks.py get JOB_UUID
python scripts/run_benchmarks.py cells JOB_UUID
python scripts/run_benchmarks.py get JOB_UUID --cell-id CELL_UUID
python scripts/run_benchmarks.py watch JOB_UUID
python scripts/run_benchmarks.py watch JOB_UUID --last-event-id JOB_UUID:42 --poll-fallback
python scripts/run_benchmarks.py cancel JOB_UUID
python scripts/run_benchmarks.py rerun JOB_UUID --cell-id CELL_UUID --idempotency-key rerun-2026-01
python scripts/run_benchmarks.py delete JOB_UUID --confirm JOB_UUID
```

Omitting `--cell-id` on rerun selects all failed cells; repeating it selects
specific failures. Frozen reruns still need current human authorization.
Deletion requires the exact UUID twice and remains subject to server retention
rules. No frozen-document expiry/automatic deletion is introduced.

Submit/rerun require an explicit reusable idempotency key. A timeout can mean
the server accepted the request. Recover deliberately using the identical
request, target and key. The CLI never retries mutations or creates replacement
work after uncertain delivery; a conflicting payload/key returns a conflict.

## Pages and progress

List commands return one page and its continuation, not a silently truncated
combined list. `--limit` requests a smaller page. Continue using:

- Catalog: same section, `--cursor` and `--catalog-digest`.
- Suites: `--cursor` and `--suite-catalog-digest`.
- Jobs: `--cursor-created-at` and `--cursor-job-id`, preserving `--status`.
- Cells: `--cursor-position` and `--cursor-cell-id` for the same job.

Watch emits progress immediately. Only `benchmark.event` advances
`last_event_id`; status frames invent no ID. EOF/disconnect causes an authorized
status check, never resubmission/cancellation. HTTP410 or `stream.error` history
expiry requires status reconciliation before using `resume_after`. Authorization
failure stops observation. A lost connection is not server job failure.
`--poll-fallback` explicitly enables bounded polling after reconnects run out.

Defaults in `.env.example`:

- `BENCHMARK_CLI_REQUEST_TIMEOUT_SECONDS=30`: per-socket timeout.
- `BENCHMARK_CLI_MAX_RESPONSE_BYTES=10485760`: decoded JSON/SSE frame byte cap.
- `BENCHMARK_CLI_EVENT_RECONNECT_ATTEMPTS=3`: additional stream connections.
- `BENCHMARK_CLI_POLL_INTERVAL_SECONDS=5`: observation retry/poll delay.
- `BENCHMARK_CLI_POLL_TIMEOUT_SECONDS=3600`: optional polling duration.

## Exit codes

0: API command succeeded or watch observed completion; 2: invalid input/not found;
3: authorization; 4: conflict; 5: watch observed failed/partially failed work;
6: watched cancellation; 7: transport/interrupted observation; 8: server/protocol
failure. A successful `get` of a failed job returns 0 and retains the job status.
Accepted submission is not a claim of completed execution.
