# Benchmark discovery and plan preview

The deployment-local API uses the same current, verified curator context and
route catalog as benchmark submission. All endpoints require
`BENCHMARK_API_ENABLED=true`, benchmark **read** capability, and the initiating
human's target-appropriate cookie or `X-Benchmark-Curator-Authorization` bearer.
An orchestration M2M token is not a curator identity. ABC source credentials,
payload-supplied subjects/groups and matching email addresses are not accepted
as that identity. Read preview does not require run capability.

Current identity-provider authorization checks are permitted and fail closed;
document materialization, source authorization, LLM calls and job writes are
not performed. API-only deployments can preview with execution and workers off.

## Discovery

- `GET /api/v1/benchmarks/catalog?section=targets` returns one canonical catalog
  section. Other sections are `models` and `route_slots`. The version-1 response
  includes the whole-catalog digest, section items, registered resolver IDs,
  non-secret environment ID and API/execution/worker flags.
- `GET /api/v1/benchmarks/suites` returns checked-in suite summaries: IDs,
  schema versions, digests, case/configuration counts and repetitions. Suites
  with targets unavailable to the verified curator are omitted; available
  targets do not guarantee that a suite's model overrides satisfy current
  capabilities or configured execution limits.
- `GET /api/v1/benchmarks/suites/{suite_id}` returns the existing `BenchmarkSuite`
  v2 specification and its digest. The ID is a catalog key, never a file path.

Both lists accept `limit` (default `BENCHMARK_DEFAULT_PAGE_SIZE`, capped at
`BENCHMARK_MAX_PAGE_SIZE`). Continue catalog pages using the same `section`,
the returned `next_cursor` as `cursor`, and `catalog_digest`. Suite pages use
`suite_catalog_digest` instead. A continuation without its digest or with an
invalid cursor returns 422; changed contents return 409. Null `next_cursor`
means the list is complete. Preserve item order when reconstructing the three
catalog arrays: this is the exact order used by the authoritative digest.
Pass the first catalog digest on subsequent section requests to detect drift
while assembling a complete catalog. Responses have `Cache-Control: no-store`.

`BENCHMARK_ENVIRONMENT_ID` is an operator-assigned, non-secret target identifier;
its default `unconfigured` does not claim to identify local, AWS dev or
production. It is display/provenance information, not a trusted destination
URL or authorization grant. Configure it distinctly on each execution target.

## Preview

`POST /api/v1/benchmarks/plans/validate` accepts JSON containing the current
`catalog_digest` and exactly one of:

- `suite`: an execution-only `BenchmarkSuite` v2, including explicit queries
  for standalone-agent cases and named route configurations; or
- `checked_in_suite`: `{ "suite_id": "...", "suite_digest": "sha256:..." }`
  from discovery.

The version-1 response includes the unchanged `ResolvedBenchmarkPlan` v2,
catalog/suite schema versions, exact `cell_count`, and a structured warning
that inputs have not been materialized. Digests live in the existing plan:
`plan_digest`, `catalog_digest`, and `suite_digest`. Named configurations are
resolved directly; no implicit model Cartesian product is introduced.

The same pure planning helper is used by admission: route/model/reasoning
capabilities, configured case/configuration/repetition/cell limits and explicit
standalone-agent queries are checked consistently. Preview additionally rejects
unregistered resolver IDs without invoking them. Input references are validated
structurally, but source existence/access, document bytes, version/content
digests and frozen-document decoding are verified only during materialization.
Preview is not an execution authorization receipt. Submission recomputes against
the same verified curator and current catalog before accepting work.

Requests are bounded by `BENCHMARK_ADMISSION_MAX_BYTES`; catalog, suite and
preview responses by `BENCHMARK_CATALOG_MAX_RESPONSE_BYTES` (default 1 MiB).
An oversized response returns 413 without silently truncating a plan. Reduce
page size or suite size, or have an operator review the configured limit.

Errors use `{ "detail": { "code": "...", "message": "..." } }`:
401/403 for authentication/authorization, 404 for disabled API or missing/hidden
suite, 409 for catalog/suite drift, 413 for byte bounds, 415 for non-JSON preview,
422 for schema/cursor/resolver/plan failures, and sanitized 503 for unavailable
dependencies. OpenAPI includes synthetic request, response and error examples.
No gold, biological scoring or private document content is part of discovery.
