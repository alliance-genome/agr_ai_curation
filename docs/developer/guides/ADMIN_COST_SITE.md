# Admin cost explorer

The ordinary conversation/flow cost explorer reads the shared accounting ledger.
It has no usage database, provider secrets, background collector or price list of
its own. Benchmark accounting continues to use the same canonical facts; benchmark
discovery/import and provider invoice reconciliation remain separate work.

## Services and access

`frontend/Dockerfile.cost` builds a separate React entry using the application's
theme and existing dependency lock. It runs as UID101, read-only, on an internal
Compose network with only the frontend gateway and backend. There is no published
port. `/cost` redirects to `/cost/`; query-string drill-down links survive reload.

The gateway sends both page and asset requests through the backend's existing
`require_admin` policy. Every report and export independently checks the same
policy. Cookies stay same-origin and httpOnly; this service has no separate login
database or browser token storage. Authentication failures offer the existing
OAuth login with the fixed `destination=cost` return target. Ordinary users get
403. Never rely on a hidden link as access control.

Local Compose builds `admin-cost` automatically. Standalone production Compose
keeps the new service under the optional `admin-cost` profile: explicitly build
and select an immutable `ADMIN_COST_IMAGE` / `ADMIN_COST_IMAGE_TAG`, then enable
the profile. Image publication/release enablement is an operator release step,
not permission to deploy the feature to production. Private workstation and dev
routing are maintained outside this repository.

Set `ADMIN_EMAILS`. Production is fail-closed when it is absent. `DEV_MODE=true`
deliberately bypasses normal identity and is suitable only for an isolated dev
stack; a dev smoke in that mode is not proof of real OAuth authentication. Unit
authorization contracts and an authenticated-mode integration check are required
before production enablement.

## Pricing ownership

`backend/cost_pricing/` is the single dependency-free `agr-cost-pricing` package
used by the backend and TraceReview. Container builds install it explicitly.
For host development install `pip install -e backend/cost_pricing` from the repo
root (including for a TraceReview virtual environment). TraceReview Docker builds
now use the repository root context with Dockerfile-specific allowlists.

The operational catalog comes from reviewed Langfuse model definitions, verified
against official provider pricing. Do not add prices to application source. Import
a reviewed JSON envelope through:

```sh
python -m src.lib.cost_ledger.import_prices /private/reviewed-prices.json
```

Envelope fields: `schema_version: 1`, `currency: "USD"`, a nonempty `source`
provenance string, timezone-aware `captured_at`, and `providers`, a mapping from
provider ID to exported definition arrays. Definitions require `id`, `unit:
"TOKENS"`, `matchPattern`, and a timezone-aware `startDate`. Tier conditions and
usage-type prices retain their Langfuse shape. Numeric prices are parsed directly
as Decimal and stored canonically as strings. Never infer a historical effective
date from catalog creation/capture time. Definitions without a valid effective
date stay unavailable until reviewed.

The importer prints a content-addressed snapshot ID. Identical imports are
idempotent. Database triggers reject updates/deletes. Set
`COST_PRICING_SNAPSHOT_ID` to select the default, or pass `snapshot_id` on a
report. A newer catalog is a new snapshot, not a rewrite of usage or old prices.
The capture timestamp states when the catalog was obtained, not what prices were
historically known. Report valuations are retrospective estimates against the
explicit selected snapshot.

Inclusive input/output counts are partitioned without double-counting cache or
reasoning. Unknown cache/reasoning counts produce justified lower/upper bounds
where the rate conditions permit, otherwise an explicit unavailable reason.
New requests retain requested and provider-reported effective service tiers
separately. Only the effective tier is used for valuation; requesting Flex does
not prove that Flex served the request. Missing provider tier evidence leaves
supported alternatives as a range. Estimates are independent of recorded charges and never summed
with them. Credits are not silently converted into USD. Missing is not zero.

## Runtime attribution

Each measured request carries the registered agent ID, display name, role,
revision and flow node ID where available. Identity lives on the existing agent
hooks so SDK/Sentry cloning retains it; structured retries inherit the specialist
identity. Supervisors need not have a flow node. The request drill-down and
JSON/CSV exports expose these fields without storing prompts or scientific data.

Tier capture runs at the existing SDK measurement boundary, observing native
Responses (HTTP/WebSocket) and Chat Completions before SDK normalization can drop
provider metadata. Requested tiers reflect the effective request settings,
including provider policy and extra-body overrides. A missing requested tier is
not specified, while a missing effective tier is unknown—not assumed standard.
This is forward-only capture: no backfill or historical reconstruction is run.
Catalog tier conditions must cover only supported pricing modes; a default tier
must not silently price an unsupported reported mode (for example a negotiated
or access-controlled tier).

## Read contract

- `GET /api/admin/cost/access`: protected presentation access check.
- `GET /api/admin/cost/reports`: bounded summary, run groups, paged requests.
- `GET /api/admin/cost/export?format=json|csv`: complete bounded selection.
- Existing `GET /api/admin/cost/sessions/{session_id}` retains its recorded-facts
  projection for current consumers.

Select timezone-aware `start` inclusive and `end` exclusive, or `session_id`
without dates for full recorded-session scope. Optional `run_id` requires the
session. Additional filters: `provider`, `model`, `activity`, `agent_id`,
`flow_run_id`; `__unknown__` selects null attribution. Every report is scoped to
the configured deployment; this is not an organization-wide billing report.

Totals cover the entire bounded selection, not just the page. `offset` pages
requests and run rows separately; their counts are explicit. Full-turn totals
are distinct from the selected-window subtotal. Colliding session owners are
rejected instead of combining curators' conversations. Window length, request
count, DB deadline, and page size are environment-configurable in `.env.example`.
Oversized selections are refused, not silently truncated. The API and exports
use `Cache-Control: no-store`.

JSON exports retain exact decimal strings, contributing request IDs and their
fact revision cutoffs, deployment, filters, pricing snapshot and algorithm
revision. These are the inputs needed to reconstruct the valuation after later
fact enrichment. Outcomes are a current execution observation, not an immutable
historical state. CSV includes corresponding provenance columns and neutralizes
spreadsheet formula prefixes. Each export is a fresh consistent read, so new
facts may appear after a displayed report; use its generated timestamp and pins.

No prompts, results, paper contents or scientific artifacts are returned.
Current coverage excludes pre-enablement history, benchmark listing, document
processing charges, infrastructure and invoice reconciliation. Those exclusions
are visible in the UI and report metadata.

## Validation

Focused frontend: `npm run test -- --run src/cost/CostApp.test.tsx` and
`npm run build:cost`. The blocking frontend CI build builds both entries.
Backend contracts live in `test_cost_pricing.py`, `test_admin_costs.py` and
`test_admin_cost_reports.py`; the latter uses a disposable PostgreSQL database.
TraceReview's full Docker suite also exercises the shared package.
`docker compose -f docker-compose.test.yml run --rm --build admin-cost-build-check`
checks the production-style presentation container configuration without secrets.
