# Recorded AI Curation model cost

ALL-1205 / KANBAN-1725 adds content-free execution attribution and a read-only
TraceReview CLI. It is not the ALL-540 billing ledger. PDFX, compute, storage,
embeddings, and reranking are outside this report's boundary.

## Run the report

Use the existing TraceReview backend environment and its configured Langfuse
source credentials. From `trace_review/backend`:

```bash
python -m src.services.cost_report_cli \
  --source remote --start 2026-09-07T00:00:00Z --end 2026-09-14T00:00:00Z \
  --group-by environment,activity,agent_role,agent_name,model,effort
```

`remote`/`local` refer to configured telemetry sources, not automatically to
production/dev. Filter the actual observation environment. Discovery uses model
call start times, not root-trace times, then retrieves ancestry. Both steps share
`COST_REPORT_MAX_REQUESTS` (default 200); page size is `COST_REPORT_PAGE_LIMIT`
(1000). Request exhaustion returns available rows, `source_complete=false`, null
complete totals, and exit code 2. Missing timestamps and conflicting duplicates
also prevent a complete total. Dates require an offset and use UTC `[start,end)`.
Calls in a run outside this interval are excluded; never describe this as lifetime
or complete-run cost. Source completion does not prove retention or provider-bill
coverage.

For a known paper, use its recorded source-provider namespace/reference ID:

```bash
# Paper total (rows are ranked by covered priced subtotal).
python -m src.services.cost_report_cli --start 2026-09-07T00:00:00Z --end 2026-09-14T00:00:00Z --filter environment=production --paper example paper-A --group-by paper
# That paper's agents, runs, and agents within one run.
python -m src.services.cost_report_cli --start 2026-09-07T00:00:00Z --end 2026-09-14T00:00:00Z --paper example paper-A --group-by paper,agent_id,agent_name
python -m src.services.cost_report_cli --start 2026-09-07T00:00:00Z --end 2026-09-14T00:00:00Z --paper example paper-A --group-by paper,run_id
python -m src.services.cost_report_cli --start 2026-09-07T00:00:00Z --end 2026-09-14T00:00:00Z --paper example paper-A --filter run_id=run-A1 --group-by paper,run_id,agent_id,model,effort,agent_revision,status --format csv
```

These example identities are synthetic, not live paper IDs. Repeat `--filter`
for provider, activity, role, model, effort, status, or environment. No implicit
success-only filter hides paid failures. `--input file.json` accepts
`{"traces":[{"trace_id":"...","observations":[]}],"source_complete":true}` for
offline verification. Omit source completeness to mark an unverified export partial.
JSON includes contributing events and trace/span references; CSV exposes the same
group values, Decimal amounts, null totals, interval, filters, and coverage.
No prompt/output/user identity is copied into exported events.

## Attribution and accounting

- Scientific paper identity is the owned PDFDocument's existing source-provider
  namespace and reference CURIE (or source reference ID when no CURIE exists).
  No filename/title/prompt matching occurs. Document UUID and checksum remain
  artifact identifiers. Runtime authorization uses the authenticated subject's
  existing User-to-PDFDocument ownership relationship.
- A durable chat/flow turn is the logical processing run where available. The
  flow resume path retains that turn; deliberately new turns get new runs.
  Other entrypoints get a new correlation UUID. A batch grouping ID is a job ID,
  never silently treated as one per-paper execution. Serialized package context
  preserves known run identity across subprocesses. No historical IDs are made up.
- Registry key/name, category-derived role, effective prompt/snapshot revision,
  provider, and flow node identity are attached to the SDK agent, inherited by
  generations through their nearest owning agent. Output category maps to
  formatter. Activity is independent of role. Code-owned authoring/classifiers
  have explicit IDs. Undeclared categories remain unknown.
- Immutable request snapshots isolate concurrent tasks. The async stream resets
  its context before yielding to its caller; worker requests hydrate an empty
  context. A standalone validator starts a validation run only if no parent run
  exists. Background calls inherit a known parent, otherwise start background work.
- Paper categories are `paper`, `not_associated`, `unknown`, `artifact_only`, and
  `shared`. A documentless explicit authoring/chat request is not associated;
  historical missing metadata is unknown. An indivisible shared call is counted
  once outside exclusive paper totals. Existing runtime paths do not manufacture
  a multi-paper association; producers can supply explicit `related_papers`.
- Only GENERATION observations contribute exclusive cost. Parent workflow rollups
  are alternative inclusive views, never added to child charges. Provider response
  IDs, model request IDs (ALL-1279 measurement IDs), attempt IDs, or trace/span
  identities deduplicate ingestion; distinct retries remain distinct. Conflicting duplicates are one ambiguous unpriced call.
  Attempts without identifiers remain separate; no fuzzy token/text deduplication.
- Provider input includes cache read/write subsets; provider output includes
  reasoning. Flat Langfuse usageDetails buckets are already disjoint. The pinned
  OpenInference adapter enriches existing spans, not a second generation, and
  publishes inclusive Sentry/OTel totals alongside disjoint Langfuse buckets.
  A low-cardinality missing-usage counter and one report coverage warning expose
  gaps without paper/run/agent IDs as metric labels.
- A zero requires recorded matched pricing or an explicit provider cost source.
  Missing usage/prices are not free. Complete totals are null if pricing is
  incomplete or uncertain; measured cost, estimated range and priced subtotal
  remain available. Decimal strings preserve export precision before display rounding.

## Usage status per call

Each call reports one `usage_status`, counted per row and in totals as
`<status>_usage_calls` and usable as a `--group-by`/`--filter` dimension. Since
ALL-1288 the span's `cost_context.usage_status` says why usage is absent (see
`MODEL_REQUEST_MEASUREMENT_MATRIX.md`); events also carry the span's
`model_request_id` and the raw `usage_status_declared`.

| `usage_status` | Meaning in the report |
| --- | --- |
| `recorded` | usage retained on the observation |
| `inconsistent` | impossible token buckets, or the span declared `recorded` but the retained observation holds no usage |
| `provider_omitted` | terminal provider response without usage |
| `failed` | the attempt errored before usage was returned |
| `cancelled` | the stream ended before the provider's terminal event |
| `missing_status_unknown` | older span without a declared status and without usage; the cause was not recorded |

Older spans without a declared status keep the observed classification
(`recorded`, `inconsistent` or `missing_status_unknown`). `missing_usage_calls`
still counts observations without usage. `usage_complete` is true only when every
call is `recorded`; token totals are sums over retained usage, never zero-filled
for other calls. Calls without usage stay unpriced and are never estimated, so
complete cost totals remain null. The CLI prints every status count when usage
or pricing is incomplete.

## Supported pricing repair (release-time operation)

Do not edit Langfuse vendor tables or historical observations. Obtain a dated,
reviewed export from Langfuse's Models API or its upstream
[default model definitions](https://github.com/langfuse/langfuse/blob/main/worker/src/constants/default-model-prices.json).
Verify rates against the provider's official pricing before application. Keep the
reviewed operational artifact with release notes, not as a second repository
price catalog. Include `startDate`, `unit: "TOKENS"`, usage-type prices and all
applicable pricing tiers. Never apply a default model's prices to another model.

```bash
# Read-only model coverage and pending configuration. Repeat --model for every
# active config/models.yaml entry. Output.models is the supported API export.
python -m src.services.model_prices_cli --at 2026-09-15T00:00:00Z \
  --definitions /private/reviewed-langfuse-models.json \
  --model gpt-6-astra --model gpt-5.6-sol --model gpt-5.6-terra \
  --model deepseek/deepseek-v4-pro-0813
# Only after production release authorization: same command plus --apply.
```

The tool is read-only by default, creates only missing matches, and refuses an
apply when a requested model lacks a supplied definition. Re-export verifies
creation. Existing custom definitions are not overwritten. HTTP is intentional:
the installed Python SDK's Models schema cannot decode newer service-tier
conditions (`in`/`not_in`), while the supported public HTTP API can.

September 14 preflight: production has Sol/Terra managed definitions but no Astra
match. Upstream commit `e9d67b7eabef839337b47e11c80bcf368edf6810` supplies Astra
and its effective date/tier prices. Neither checked catalog supplies the explicit
DeepSeek V4 route. Leave that route unpriced unless a supported verified definition
is supplied; provider routing usage records may separately report credits, which
this USD generation report does not silently convert. This is a documented
pricing dependency, not permission to upgrade the observability stack.

No production pricing is changed during code preparation. At release, review and
apply the missing Astra definition separately, then run a bounded read-only report
on retained new calls. Check actual nonzero matched model costs, exclusive token
buckets, and nearest agent identity. Do not create paid extraction solely as a
telemetry smoke test. Prices affect new ingestion, not historical raw traces.

## Retrospective reconstruction and reconciliation

```bash
python -m src.services.cost_report_cli --input retained.json \
  --start 2026-09-07T00:00:00Z --end 2026-09-14T00:00:00Z \
  --model-definitions /private/reviewed-langfuse-models.json \
  --reconstruct-all --assume-service-tier default --organization-cost 767.81
```

Without `--reconstruct-all`, estimates fill only missing prices. With it, every
event retains `recorded_cost` and receives a separately labeled reconstruction.
The optional tier assumption applies only to missing tiers and is disclosed in
the output; without it all possible supported tiers contribute to the interval.
Missing cache-write detail yields a range, not fabricated fresh input. Invalid
bucket totals remain inconsistent/unpriced. Definitions are matched by model,
effective date, custom precedence and ordered tier conditions.

Organization cost must have a comparable provider/account/window boundary.
`unexplained_residual` subtracts covered measured cost plus estimated lower bound;
`unexplained_residual_lower` uses the estimated upper bound. Neither is forced to
zero or attributed to a paper without evidence. Incomplete telemetry is not a
complete reconciliation even if a numeric residual happens to be small.

The September 7–13 investigation's three-model anchor counts are 6,939 production
and 2,410 dev observations with nonempty usage; some have zero token totals. Dev
also retains other model routes not priced by that investigation. The old
$339.19–390.11 production/$72.55–82.44 dev estimates assumed standard tier and
did not reject inconsistent usage or model long-context tiers. This report does;
compare counts, model set, usage completeness and pricing assumptions before
comparing totals. Historical named ancestors are recoverable, but the retained
export lacks verified paper/run/activity metadata. Never retrofit those from names.

The deterministic test fixture reconciles exactly: paper A $3.15 (run A1 $1.35,
A2 $1.80), paper B $2.00, not-associated $0.40, unknown $0.30, shared $0.60 =
$6.45. A's extraction/validation/formatter partitions are $2.50/$0.55/$0.10,
including the failed $0.10 attempt. Duplicate ingestion adds nothing; an unpriced
attempt preserves the $6.45 subtotal but makes its affected totals incomplete.
