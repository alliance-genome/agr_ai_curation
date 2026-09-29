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

## Explicit executor onboarding

`POST /api/v1/benchmarks/curator/onboard` registers the initiating human in the
executor's local account store. A portal Connect callback can call this after
verifying the target human login, before using discovery. It requires the same
API gate, read capability and verified human identity described above. It does
not require run capability, accept a payload-supplied identity or submit work.

The executor checks the current identity provider before provisioning: disabled
accounts, identity mismatches and revoked mapped groups are denied. An inactive
local account cannot be reactivated by onboarding. Successful calls use normal
profile synchronization and best-effort tenant provisioning; concurrent first
connections converge on one local account. The token-free response has exactly
`schema_version: 1`, `subject`, `issuer` and `environment_id`, with
`Cache-Control: no-store`. Provider outages fail closed with a sanitized 503.

Discovery remains read-only: a missing local account returns 403, and a catalog
GET never creates an account or tenant. A failed or lost onboarding response
must not establish a portal connection; a later explicit Connect can safely
repeat onboarding for the same verified human.

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

## Saved-flow preparation

`GET /api/v1/benchmarks/saved-flows` lists active flows owned by the initiating
curator or shared with a project they currently belong to. It accepts `offset`
and `limit`, using the same configured page limits above, and returns
`items`, `total_items`, and `next_offset`. This is a live list, not an immutable
snapshot; editing flows between pages can change ordering. Saved-flow IDs are
explicitly distinguished from installed recipe names by `source_kind`.

`GET /api/v1/benchmarks/saved-flows/{flow_id}/contracts?revision=sha256:...`
reauthorizes the selected flow and its agents. Pass the list entry's `revision`
to reject edits since selection. Inaccessible, deleted and archived flows all
return `flow_unavailable`; changed revisions return `flow_changed`. Shared-flow
access does not grant access to the owner's private agents or profiles.

The response contains declared output structures per node, not result data:

- `json_schema` retains the declared schema, including descriptions and nested
  types, rather than guessing fields from examples.
- `profile_attributes` is the exact saved generic profile's attributes schema.
  Its saved agent receipt includes the profile revision and fingerprint.
- `pack_fields` is a benchmark catalog of the installed domain pack (not the
  Studio export catalog and not a JSON Schema): `pack_id`, `pack_version`,
  `pack_label`; `record_kinds` (`object_type`, `label`, `role` `curatable` or
  `supporting`); `families` from the pack's explicit `record_kind_families`
  (`id`, `label`, `fallback_object_type`, `object_types`); `fields` for curatable
  kinds (`object_type`, `path`, `label`, `shape`, `inside_list`, `is_identifier`,
  `validator_written`, `is_pointer`, `free_text`, `validator_binding_id`); and
  `default_fields` per curatable kind (`path`, `if_not_validated`), read live
  from the kind's workspace layout. A leaf read from every element of a list of
  inline objects is written `list[].leaf` with shape `text_from_each_item`.
  Descriptions and enum values are omitted.
- `not_verified` means a contract is unavailable or undeclared. It does not
  mean a model failed, and it must not be presented as an empty gene list.
- Every verified pack or profile contract has `structure_source`:
  `{"kind": "pack", "pack_id", "pack_version", "pack_label"}` or
  `{"kind": "profile", "profile_id", "profile_revision_id", "revision", "name"}`.

Each step is described on its own. A step has `output_kind` (`pack_fields`,
`profile_attributes`, `flexible`, `pdf_extraction`, `envelope_legacy`,
`validator_result`, `formatter`, `text`, `other` or `unavailable`), `problem`
(`needs_resave`, `unavailable_agent`, `unreadable_structure` or null) and
`validated_bindings` (the validator binding ids that run on its output; only
when the flow is runnable). A step whose agent the caller can't see is
`unavailable_agent` with one generic reason and no receipt. A step whose model
or tools need a re-save keeps its structure (`needs_resave`).

`runnable` is true only when every step resolved and the flow's stages, model
defaults and validators are available; otherwise `run_problem` is a fixed
sentence chosen by the first problem's code and `stages` is empty. Admission
refuses a flow that isn't runnable.

The whole contracts response is bounded by
`BENCHMARK_CATALOG_MAX_RESPONSE_BYTES` (default 1 MiB), like the catalog
responses. Each `pack_fields` step carries its pack's full benchmark catalog,
so a flow with several pack steps grows with each one; a flow over the limit
returns 413 `response_too_large` rather than a truncated contract list.

The `stages` list identifies extraction, validation, output and other roles.
Graph-attached validators and repeated nodes using one agent share its
`agent:<key>` slot. Automatically scheduled model validators use their
`validator:<binding>` slot. Deterministic validators and file-output tools have
no model slot. These identities describe coupling; consumers must not offer
conflicting independent model choices for one shared slot.

Model-bearing stages include their actual `default_route`. Custom stages use
the pinned revision's model and reasoning settings, not the mutable agent head.
`route_default_conflicts` lists shared slots whose saved defaults differ;
setup must ask for one explicit override for each such slot. Declared generic
profile semantics and package IDs are returned where available, without
inferring a biological task from a title or field name.

Discovery never runs the flow. A schema marked verified is not proof of
biological suitability: an explicit semantic mapping is still required.
Discovery itself grants no execution approval. Preview and admission capture
the selected saved flow through the initiating curator's authorized source.
Use a target such as
`{"kind":"flow","id":"<flow UUID>","source_kind":"saved_flow","source_revision":"sha256:..."}`.
Never substitute the flow title for its UUID or send executable definitions.

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

Preview and admission share source preparation for selected saved flows,
installed recipes and standalone agents. New plans include immutable source
snapshots and dependency receipts; the discovery catalog remains lightweight.
See [source revisions](BENCHMARK_CUSTOM_SOURCE_REVISIONS.md) for captured fields,
access rechecks and reproducibility limits. A shared default conflict requires
an explicit route in every configuration; an incompatible custom source
revision is not resolved by changing only the model route.

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
