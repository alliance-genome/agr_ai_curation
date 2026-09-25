# Shared ledger: source inventory and cutover plan

## Unreleased writer cutover candidate

This worktree contains an incomplete, breaking cutover candidate. **Do not merge
or deploy it independently.** The reviewed read-side branch remains separate.
The benchmark repository now reserves the verified source/attempt binding before
dispatch and writes canonical usage/charge facts in the same savepoint as
lease-fenced invocation completion. It no longer writes inline cost columns.
Missing scope blocks dispatch; a changed or missing binding blocks completion.
Failed/cancelled calls without evidence retain unknown facts, not free calls.

The invocation page now uses schema version 2: execution fields plus a required
`accounting_reference` (deployment, attempt UUID, pinned fact revision). It no
longer returns token or billing columns. The page resolves references in one
read-only repeatable-read snapshot, after ownership checks; unknown evidence is
revision zero, while missing bindings fail explicitly. No inline fallback exists.

Still required in this candidate: replace new artifact/import cost fields with
versioned ledger references; update all consumers and historical
fixtures; explicitly migrate and retire old columns after verified backfill;
then run full integration and release gates. Existing artifact serializers and
private invocation consumers are not yet converted and are not valid release
consumers of this candidate. Historical backfill must run against the pre-cutover
schema/writer checkpoint during maintenance, not against newly captured rows.
Tests of the atomic writer boundary alone do not establish cutover readiness.

Status: implementation plan, not a deployed migration. This refines
[cost ledger ownership](COST_LEDGER_OWNERSHIP.md) for ALL-540 and ALL-1311.
The public source inventory is based on main `87ff9d6cb`; private portal consumers
were inspected read-only at main `290627ce`. Neither checkout nor a live database
was changed to perform this inventory.

## Existing accounting path

| Boundary | Existing owner | Current retained facts | Cutover responsibility |
| --- | --- | --- | --- |
| Provider capture | `openai_agents/provider_usage.py` | In-memory requested/actual route, sequence, usage and optional billed amount/unit/source | Emit one canonical attempt identity, shared with request measurement |
| Benchmark durable writer | `_DurableInvocationObserver` in `benchmarks/worker.py`; `BenchmarkRepository.finish_invocation` | Lease-fenced SQL writes before/after dispatch | Persist accounting through ledger in the same transaction; retain benchmark lifecycle fencing |
| Execution SQL | `models/sql/benchmark.py:BenchmarkInvocation` | Cell/attempt/stage, request/response digests, routing, timing/status, token counts and billed amount/unit/source | Keep execution facts; move accounting fields to ledger ownership and reference the event |
| Execution API | `schemas/benchmark_jobs.py:BenchmarkInvocationResponse`; `api/benchmark_jobs.py` | Invocation rows validated directly from SQL objects | Explicit projection of execution facts plus authorized ledger references; update consumers with the contract |
| Result artifact | `benchmarks/runtime.py:_stable_invocations` and `BenchmarkCellExecutionResult` | A second serialized usage list alongside scientific output | Preserve existing bytes; new versioned results reference ledger events rather than copy usage |
| Private portal import | `execution_import_repository.py:retain_cell`; `ImportedExecutionCell` | Immutable raw result and metadata containing serialized invocation rows, with integrity digests | Preserve old evidence; new imports retain execution provenance and ledger references, not another mutable usage store |
| Private portal reports | `report_accounting.py:summarize_invocations`, report aggregation/stage consumers | Reads imported invocation facts; groups amounts by unit/source, no provider pricing | Consume shared scoped accounting projections; retain scientific statistics and authorization locally |
| General application report | TraceReview `cost_report.py` | Read-only normalization over retained generations; request/response/span deduplication | Reuse semantics in ledger ingestion and shared reporting, not a second charge database |

The private report already distinguishes unknown costs from zero, rejects
conflicting duplicate invocation IDs, and keeps billing units/sources separate.
Preserve these rules. Do not replace them with an independent new pricing engine.

## Prerequisite: one call identity across producers

Request measurement already emits `measurement_id` as span
`cost_context.model_request_id`. Benchmark `PendingProviderInvocation` and
`ProviderUsageRecord` now snapshot that request identity at dispatch and retain
it through completion/failure. The `provider_usage` telemetry EVENT carries
`metadata.model_request_id` alongside its existing usage envelope. It is a
correlation reference, not another chargeable GENERATION. Unmeasured calls keep
the identity unknown; no unrelated UUID is invented to suggest a verified join.

Immutable result serialization remains unchanged. The durable benchmark row
retains its own invocation UUID and now has a nullable, unique `model_request_id`
binding, committed by the observer before dispatch under the existing job/cell
lease checks. Completion cannot rebind it through the repository API. Distinct
measured retries get distinct bindings; duplicate bindings are rejected rather
than silently accepted as another dispatch. This identity link is a prerequisite
for preventing benchmark SQL and telemetry from creating two ledger events for
one call; it is not yet a ledger ingestion implementation.

Migration `a31c05e0925a` adds only this binding and its uniqueness constraint.
Historical rows retain null identity and unchanged usage/billing facts. PostgreSQL
permits multiple null bindings. Downgrading removes the new bindings, not the
existing invocation or billing facts; upgrading again cannot reconstruct those
bindings. Do not use downgrade/re-upgrade as an identity-preserving recovery path.
Validation uses disposable PostgreSQL; no shared database has been migrated.

Before enabling multiple ingestion sources:

1. Establish the shared identity at the actual adapter dispatch boundary. Verify
   wrapper order for streaming, non-streaming, native and routed providers; do not
   assume a context variable is active when the benchmark observer runs.
2. Carry that identity through pending/completed/failed records, request
   measurement, durable invocation binding and telemetry export. Concurrent calls
   and explicit retries must have distinct identities. Stream closure and failure
   keep the identity allocated at start, even without a provider response ID.
3. Register provider response IDs and trace/span IDs as source references to the
   event, scoped to a verified producer/account boundary. The application UUID is
   not a provider invoice identifier. SDK-internal attempts that are not observable
   must remain an explicit coverage limitation, not fabricated child charges.
4. Use a uniqueness constraint on source-system/source-namespace/source-ID
   bindings. Equal timestamps, amounts, models or digests never prove duplicate
   billing. Conflicting bindings stop that record's valuation and surface a
   reconciliation finding rather than overwrite provenance.

Historical benchmark invocation UUIDs provide reliable identity within their
execution target. Import them once with that namespace. If no authoritative link
to a historical telemetry generation exists, retain the two source observations
as unresolved overlap rather than add them into a supposedly complete combined
total. Prefer the declared authoritative source for that covered cohort and
report unresolved coverage separately.

## Proposed relational ownership

Names below describe responsibilities, not settled SQL identifiers.

The identity registry uses `cost_attempts` and
`cost_source_references` (migration `b42c05e0925b`). The registry itself contains no
usage, charges or valuations, and no production writer calls it. A deployment-scoped
attempt UUID has one owner; source identity is the exact tuple of deployment,
source system, namespace and source ID. Namespaces must distinguish producer
accounts/targets. Producer authentication and scope derivation belong at the
future ingestion boundary, not in caller-controlled payload defaults.

`bind_cost_source` accepts identical replay, attaches another verified reference
to the same attempt, and rejects conflicting owner or source bindings. It uses
the caller's transaction plus a savepoint, so catching a conflict cannot leave an
orphan attempt. Concurrent deliveries are serialized by PostgreSQL uniqueness;
the contract assumes READ COMMITTED isolation. It never commits independently
of a benchmark lease check. Neither table cascades from scientific execution
rows; references restrict accidental attempt deletion. There is no reference
reassignment API and no automatic merging of historical observations.

This is an internal persistence primitive, not an authorized ingestion endpoint
or a complete reconciliation workflow. A conflict must be surfaced by the future
ingester, not caught and silently ignored. No existing accounting facts have
been copied or removed. Downgrading this migration deletes the registry; it is
not an identity-preserving recovery operation once populated.

- **Attempt:** one canonical billable-attempt identity, source/owner/environment
  scope, normalized usage and coverage status, actual provider/model and relevant
  attribution. Benchmark invocation links to this record; session/turn/run and
  paper/artifact identifiers are distinct concepts.
- **Source references:** unique source bindings and provenance for the attempt.
  Multiple observations of a call do not create multiple attempts. Do not retain
  raw prompt/tool payloads in this table.
- **Recorded charge:** authoritative provider amount/unit/source for an attempt,
  where supplied. This is not a reconstruction and must not be overwritten by
  a calculated price.
- **Valuation revision:** separately identified calculation from an attempt's
  usage revision and a reviewed pricing revision, including currency, tier and
  explicit assumptions. Consumers reference the revision for reproducibility;
  do not copy the whole event into every valuation.

Reuse the existing reviewed price-definition source; the ledger may retain the
immutable pricing provenance needed to reproduce a valuation, but must not
introduce another independently administered model-price catalog.

Do not persist aggregate totals initially. Derive them through bounded protected
queries. An ingestion checkpoint or reconciliation receipt is operational/audit
state, not another per-call accounting copy.

## Coordinated forward-only migration

### Normalized fact contract

`cost_ledger/facts.py` defines the local, pure contract for the next persistence
slice. Input/output counts are inclusive; cache reads and cache writes are
disjoint input subsets, reasoning is an output subset. Every quantity remains
nullable. No absent cache detail or missing total is synthesized as a recorded
zero or total. Exclusive buckets are derived only when their operands are known
and the record has no detected inconsistency. Status is missing, partial,
recorded (inclusive input/output known), or inconsistent; recorded does not mean
all details are known or that the call has a price. Failure/cancellation/omission
reasons remain separate provenance, not fabricated token quantities.

Late evidence may fill unknown fields, but may not overwrite known values.
Identical replay is not addition. Contradictory known facts raise a reconciliation
conflict; contradictory combinations retain their values and an inconsistent
status. Such usage must not be priced as valid. The eventual storage operation
must bind source provenance and usage revision; this pure contract does not
persist facts or resolve source authority.

Recorded charges require a finite nonnegative Decimal, explicit unit and source.
Provider credits are not implicitly USD. Explicit zero is a known recorded
charge; no record means unknown. Different amounts, units or sources require
reconciliation rather than replacement, conversion or summation. Estimates and
invoice adjustments must not be passed as provider-recorded attempt charges.

The existing TraceReview display normalizer supplies zero defaults and synthesized
totals. Do not persist those display projections as original ledger facts. A future
adapter must normalize the retained raw source with its declared inclusive or
exclusive semantics. The pure contract does not change live report behavior.

Provider capture now carries `ProviderUsageRecord.accounting_usage` using that
same `TokenUsage` type. Raw Responses and Chat Completions usage (including the
OpenRouter adapter) preserves reported inclusive counts, cache reads/writes and
reasoning details without synthesizing a missing total. Missing fields and values
that are not nonnegative integers remain null; inconsistent reported combinations remain
visible as inconsistent rather than being clamped. The telemetry event carries
these facts in its `accounting_usage` envelope. Existing benchmark artifact
serialization is unchanged; its legacy synthesized totals are not ledger inputs.
This is source capture, not live database ingestion or a second cost store.

There is an explicit SDK coverage limitation: the installed Agents SDK `Usage`
dataclass and its Chat Completions streaming conversion insert zero for missing
counts/details before the observer receives them. The streaming wrapper preserves
that normalization provenance. Such zeros cannot establish a recorded fact, so canonical
accounting leaves them unknown. Positive SDK counts are retained. Raw response
objects and mappings preserve explicit zero. Recovering exact SDK zero values
requires capture before that normalization; do not infer them from artifact
defaults. Failed calls with no usage retain unknown facts, not zero cost.

Field semantics were checked against the installed SDK and official
[OpenAI reasoning usage example](https://developers.openai.com/api/docs/guides/reasoning)
and [OpenRouter cache usage documentation](https://openrouter.ai/docs/guides/best-practices/prompt-caching).

### Storage and consumer cutover

The local consumer contract lives in `schemas/cost_ledger.py`:
`CostLedgerReference` requires schema version 1, deployment ID, attempt UUID and
an exact nonnegative fact revision. Revision zero explicitly pins the empty
snapshot before any facts were recorded. It is not a request for latest facts.
The internal repository still uses omitted `revision=None` for latest; no such
ambiguity is allowed in a persisted reference. Positive revisions pin the
corresponding immutable addition history.

`resolve_cost_reference` returns a versioned `CostFactsProjection` through an
owner-scoped read. The projection carries usage, validated status/issues, and
optional recorded charge with decimal-string money. Consumers retain the
reference, not a new mutable usage/money copy. A database outage propagates;
missing scope/revision is not silently replaced with zero, unknown or a stale
imported amount. Revision-zero reads still require a real attempt in the correct
owner/deployment scope. The eventual API must authenticate the caller and check
execution membership; reference possession alone is not access authorization.

Private portal inspection confirms that `result_contracts.Invocation` currently
requires inline token/billing fields, `execution_import_repository` retains them
in immutable import metadata, and `report_accounting` reads those retained facts.
Those readers and the public worker/result serializer must switch together. The
projection now has an implemented, gated benchmark read endpoint (below), but
has not been deployed or connected to portal consumers. No old artifact
bytes/hashes have been changed. Historical evidence
readers may inspect retained artifacts but must not become a live-cost fallback.

The implemented route is
`GET /api/v1/benchmarks/jobs/{job_id}/cells/{cell_id}/invocations/{invocation_id}/accounting`.
It uses the existing benchmark API gate and `benchmark:read` capability. Before
reading accounting it verifies invocation membership in the supplied cell/job
and that job's authenticated owner. It then resolves only a `benchmark` source
binding in the server-configured `COST_LEDGER_DEPLOYMENT_ID` and
`COST_LEDGER_BENCHMARK_SOURCE_NAMESPACE`. Clients cannot select an arbitrary
ledger UUID, deployment or namespace. Ledger owner scope is checked independently.

An omitted `revision` reads latest and returns the exact pinned reference;
`?revision=0` pins the empty snapshot and positive revisions pin fact history.
Unknown/foreign membership or absent requested revisions return 404. Unconfigured
accounting, absent bindings, database outages or malformed ledger facts return
explicit 503 accounting-unavailable responses, never the existing inline cost
columns. Unexpected accounting failures use sanitized operational telemetry.
Successful projections and 503 responses are `Cache-Control: no-store`.

Both identity environment settings default empty and must be set from verified
deployment inventory at coordinated cutover. They are not automatic migration
switches. No live ledger writer is enabled by setting them, and the endpoint does
not give the private portal any database credentials. This read slice does not
replace the current execution API/result fields or modify historical artifacts.

`GET /api/v1/benchmarks/jobs/{job_id}/accounting` now derives a job-level summary
from that same authorized ledger scope. It uses a repeatable-read, read-only
snapshot and a server-side cursor controlled by `COST_LEDGER_READ_PAGE_SIZE`
(default 200 rows per fetch, not a coverage cap). Each distinct ledger attempt is
counted once even when multiple invocation references identify it. Invocation and
attempt counts are reported separately. Sparse revisions enrich one attempt's
facts; they are not added as separate charges. No aggregate is persisted.

Token fields report known totals and known/unknown attempt counts; missing totals
are not inferred from other fields. Inconsistent reported usage remains visible,
with an explicit inconsistent-attempt count, and is not priced by this endpoint.
Recorded charges are summed exactly and separated by both unit and source, with
unknown-charge counts. An explicit zero remains a known charge. Missing bindings,
foreign ledger ownership or unavailable accounting return 503 for the whole read,
not a misleading partial total. Foreign/missing jobs return 404. The benchmark
read capability/API gate and no-store success/503 behavior also apply here.

This is a current snapshot, not a pinned historical valuation or a complete bill.
Its scope is benchmark invocations only: shared preparation is explicitly excluded
and no estimated model prices, currency conversion or provider invoice totals are
invented. Individual invocation reads still provide pinned fact references for
reproducible consumers. The portal is not switched to this endpoint yet.

`benchmark_migration.py` provides a pure, explicit backfill planner and exact
fact-receipt verifier. It is not a runtime compatibility reader and is not called
by any existing writer. It accepts only terminal invocations and requires
deployment, execution-source namespace and job-owner scope from verified
inventory. The offline runner freezes execution tables and obtains ownership
through the invocation/cell/job relationship, not artifact metadata.

Measured request IDs remain the canonical attempt ID. Historical invocations
without a measured ID receive a deterministic UUIDv5 over the versioned tuple
`["agr-ai-curation:benchmark-cost-source:v1", deployment, namespace, invocation]`.
This is a source-scoped ledger surrogate, explicitly marked
`historical_benchmark_source`, not a fabricated model request ID or inferred
telemetry join. The original invocation ID and null measurement remain intact.
The exact derivation has a pinned test and must not be changed after backfill.
New evidence claiming another identity must reconcile the existing source
binding rather than silently reimport the call under a new ID.

Planning preserves nullable token counts, inconsistent totals, exact amounts,
units and charge source; it does not infer zero costs for failed/cancelled calls.
Missing cache/reasoning details remain unknown. Malformed or incomplete charge
provenance fails preflight. The verifier requires exact fact parity (including
unknowns and charge units/source), not only matching aggregate spend. Independent
telemetry enrichment begins only after baseline migration verification.

The read-only `benchmark_audit` module inventories candidate-schema databases with
job-derived ownership, keyset pagination and a repeatable-read, read-only
transaction. It refuses a caller session without those transaction guarantees.
It reports active jobs (including queued jobs with no invocation yet), nonterminal
or malformed invocations, inconsistent usage, unknown charges and historical
identity coverage. Valid planned charges are summed exactly by unit/source;
the process Decimal precision cannot silently round away small charges. A
page-size-independent fingerprint covers the planned source/fact mappings without
emitting row or curator identities. The fingerprint excludes rejected and active
rows, whose counts must be reviewed separately. This is not a proof of readiness
for release or a complete production spending report.

Within the backend environment, against the candidate schema:

```bash
python -m src.lib.cost_ledger.benchmark_audit \
  --deployment-id VERIFIED_DEPLOYMENT_ID \
  --source-namespace VERIFIED_EXECUTION_SOURCE
```

`COST_MIGRATION_AUDIT_PAGE_SIZE` controls query size (default 200), not total
coverage. Use a dedicated read-only session and confirm target database and scope
labels before running. The tool uses the configured application database; do not
assume supplying a deployment label selects or verifies a different database.
It expects candidate schema columns, including `model_request_id`; it does not
upgrade old schemas automatically. A long snapshot can delay PostgreSQL vacuum
cleanup, so schedule large inventories with operations. No shared database audit
has been performed by the local tests.

### Explicit offline backfill

`benchmark_audit` remains read-only. The separate `benchmark_backfill` command
also defaults to the same read-only audit; only explicit `--apply` writes. It
requires the candidate schema through `c53c05e0925c`, verified target database,
and reviewed audit fingerprint. Deployment and source labels must exactly match
`COST_LEDGER_DEPLOYMENT_ID` and `COST_LEDGER_BENCHMARK_SOURCE_NAMESPACE` for apply.
Labels do not select a database or prove its identity.

After approved maintenance has stopped new submissions and drained workers,
review all audit blockers and the planned fingerprint. Within that backend
environment, the explicit apply command is:

```bash
python -m src.lib.cost_ledger.benchmark_backfill \
  --deployment-id VERIFIED_DEPLOYMENT_ID \
  --source-namespace VERIFIED_EXECUTION_SOURCE \
  --expected-planned-facts-sha256 REVIEWED_AUDIT_SHA256 \
  --apply
```

The write runner requires a fresh, dedicated READ COMMITTED session so a snapshot
taken before lock acquisition cannot hide a concurrent writer's commit. It locks
all three benchmark source tables and all three ledger destination tables against
writes while allowing ordinary readers. `COST_MIGRATION_LOCK_TIMEOUT_MS` bounds
lock waits (default 30000 ms), not total migration duration. Query pages use
`COST_MIGRATION_AUDIT_PAGE_SIZE`; the transaction covers the entire migration.

Active jobs, nonterminal or invalid source records, inconsistent usage, binding
conflicts, per-record parity failures, or a changed final fingerprint reject the
run. A savepoint rolls back all additions, including prior pages and identity
bindings, even if a caller catches the exception. Exact replay adds no duplicate
facts. The callable never commits; the CLI commits only after verification and
then emits its committed receipt. Failures emit sanitized error codes.

Keep maintenance active through commit **and the coordinated writer/consumer
cutover**. Database locks end at commit; they do not prevent an old worker from
writing afterward. This runner preserves source columns and immutable artifacts,
does not switch live writers, and explicitly reports that cutover is incomplete.
Its temporary migration copy is not permission to operate two accounting owners.
No shared database backfill has been performed; local tests use disposable data.
Current execution API/result contracts remain unchanged pending consumer work.

The local storage primitive now adds `cost_fact_revisions` in migration
`c53c05e0925c`. Each row contains only newly known token fields and/or a newly
recorded provider charge, with a foreign key to the exact source/attempt binding.
Known values are not recopied into later revisions. Reads reconstruct the facts
at an explicit revision or at the latest revision; no mutable total or materialized
snapshot is stored. Replays create no additional fact row, and missing usage may
bind a source without inventing a zero-valued revision.

`record_cost_facts` serializes additions on the attempt row using PostgreSQL
NO KEY UPDATE locks (compatible with source-reference foreign-key locks). It uses
the caller's transaction and a savepoint around both identity binding and facts;
catching a conflict cannot commit a newly bound source or partial fact additions.
No transaction is held across a provider request by this storage API.

Database guards reject updates/deletions, nonsequential revision numbers and
repeated known fields. Constraints reject empty revisions, negative tokens,
nonfinite/negative amounts and incomplete charge provenance. The composite source
foreign key prevents attaching a revision to another attempt's source. There can
only be as many nonempty addition revisions as fact fields; this is a schema
invariant, not an operational page limit. Token counts use PostgreSQL BIGINT and
money uses unrestricted NUMERIC with exact Decimal values, not floating point.

The scoped read requires deployment, attempt and owner, but remains an internal
repository method, not an authenticated public endpoint. Source admission,
execution lifecycle/attribution, reviewed corrections, pricing valuations and
conflict receipts still require implementation. Current facts can be inconsistent
and must not be valued until reconciled. This slice does not backfill existing
benchmark costs, remove existing fields, or enable any live writer/dual write.
Deploy only with the coordinated ownership/consumer cutover below. Downgrading
`c53c05e0925c` destroys new fact history and is not a data-preserving recovery path.

1. Inventory production/dev schemas and retained counts read-only before selecting
   Alembic revision and indexes. Existing benchmark invocation deletion cascades
   from cells; ledger retention must not inherit that cascade accidentally.
2. Implement the shared dispatch identity and test duplicate-source correlation
   first. Then add ledger storage and a deterministic backfill mapping for existing
   invocation IDs. Historical unknown cache/reasoning/identity fields remain null.
3. In one coordinated application/consumer cutover, route benchmark writes to the
   ledger and update execution API/result projections and private import/readers.
   The job lease must fence both scientific and ledger changes; a rejected or lost
   lease cannot leave an apparently valid independently committed charge update.
   Do not hold a database transaction across a provider network request.
4. Verify per-source row identities, unit/source bucket sums, unknown counts and
   record bindings before removing the old mutable token/billing columns. Backfill
   is an explicit migration, not application dual reads/writes. Preserve old
   immutable artifacts and hashes without making them live accounting owners.
5. New versioned artifact/import contracts contain accounting references. Historical
   evidence readers remain only where required to inspect retained artifacts, not
   as an alternate live charging path. No opportunistic JSON mass rewrite.
6. Validate recovery and deploy as a coordinated release. Do not point an old
   application image at the migrated schema. Recovery uses the reviewed database
   backup plus matching application/portal versions, or a verified forward repair.

No migration is executed by this plan. The two repositories and dev stacks have
different revisions, so an integrated deployment needs its own readiness check.

## Failure and access contracts

Local benchmark execution already relies on lease-fenced database persistence.
Its ledger writes should use the same owned transaction rather than a separate
best-effort HTTP collector. General application paths must not silently acquire
new scientific success/failure rules just to collect costs; select durable
capture/recovery behavior explicitly and expose any capture gap.

The private portal accesses authorized ledger records through a service contract,
not direct database credentials. Validate owner and execution-target bindings,
not merely possession of an event UUID. Admin access to application cost reporting
does not expand a portal user's scientific artifact permissions.

Missing remote ledger service is explicit accounting unavailability. Existing
scientific results remain readable. Never silently switch to a stale imported
amount while labeling it current authoritative cost.

## Required proof before cutover

- One measured call seen by both telemetry and benchmark ingestion produces one
  event; two true retries produce two. Repeated delivery does not change totals.
- Start/completion/failure/cancellation paths retain the same identity, including
  interleaved streams and parallel validator threads.
- Old invocation backfill preserves exact nullable units/amounts and identities;
  mismatched duplicate observations are flagged, not combined or overwritten.
- Expired lease, transaction rollback, restart and repeat backfill cannot produce
  orphan charges or silently lose already committed usage.
- CLI, protected ledger API and benchmark cost views agree on contributing event
  identities, decimal totals, currency/unit/source buckets and unknown counts.
- Scientific artifacts retain hashes; historical valuation revisions remain
  reproducible; owner denial and remote-service outages fail honestly.

Use synthetic fixtures and disposable PostgreSQL first. Arithmetic and identity
tests do not require paid model execution. Any later integrated paid run or
deployment requires its applicable budget and release workflow.
