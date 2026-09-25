# Shared cost accounting ownership (0.10.0)

Status: approved architectural direction; local identity/fact persistence is in
development, with consumer migration and live ingestion still pending. This is
not a deployed accounting service. Tracked by ALL-1310, with ALL-540 owning
the ledger and ALL-1311 owning benchmark integration.

See [source inventory and migration plan](COST_LEDGER_MIGRATION.md) for existing
writers/readers, the shared call-identity prerequisite and coordinated cutover.

## One authoritative record

A billable provider attempt has one authoritative usage record in the shared
ledger. Application execution and benchmark execution contribute facts to that
ledger. The admin cost site, TraceReview reporting and benchmark cost reports are
consumers, not independently writable cost stores.

Keep scientific execution state separate from accounting state. Benchmark jobs,
cells, stages, repetitions and results retain their existing ownership. Link them
to canonical ledger event identities; do not copy usage and money into another
mutable benchmark cost table. A call may have several source-observation references
without becoming several charges. A genuinely issued retry is a distinct attempt.

The ledger owns normalized usage, explicit missing/inconsistent statuses, recorded
provider charges, provenance and versioned valuations. Recorded charges and
calculated estimates are different facts, not two costs to add. Effective-dated
pricing is shared; consumers must not maintain their own price catalogs. Monetary
values use exact decimals with explicit currency and service units.

## Existing data and migration

Existing benchmark invocation tables and immutable imported artifacts already
retain usage facts. Migration must inspect those real schemas and all consumers
before adding new tables. Reuse existing identity where authoritative, migrate
mutable accounting ownership explicitly, and cut consumers over together. Do not
introduce permanent dual writes or a second independent cost calculator.

Preserve immutable historical artifacts and their hashes as original evidence.
They are not a second accounting authority. Backfill records only where source
identity is sufficient; preserve unresolved identities and conflicting observations
instead of deduplicating by text, timestamps or equal token counts.

An old report's valuation must remain reproducible through ledger event references
and its pricing/valuation revision. Repricing cannot rewrite provider charges or
silently change historical benchmark comparisons.

## Read boundaries and availability

The admin-cost container is a presentation client of protected cost APIs. Start
with the application's PostgreSQL infrastructure for the authoritative ledger;
the site has no separate cost database. Aggregate on read initially. A future
materialized aggregate would be explicitly derived and rebuildable, not an
independent source of truth, and requires a measured need.

The private benchmark portal consumes a scoped service contract; it does not gain
unrestricted application-database access. Preserve owner restrictions and keep
provider credentials out of the portal and browser. Across database/service
boundaries use validated identities, not fictitious cross-database foreign keys.

Persisted ledger reporting must survive telemetry/provider outages. If the ledger
API itself is unavailable, benchmark scientific results remain available, while
cost views report accounting unavailability. Do not create an unapproved shadow
ledger merely to make remote cost reads appear available. This qualifies the
older benchmark requirement for offline imported reports: scientific artifacts
remain local; authoritative current costs come from the shared accounting service.

Environment and deployment identity are explicit. A shared provider key does not
establish production ownership. Deployment/service boundaries and authenticated
producer admission must be settled before enabling cross-environment ingestion;
the existing dev and production stores are not silently merged.

## First integrated acceptance

One benchmark execution links all covered provider attempts to the ledger,
including validators, formatting, retries and failures. CLI, benchmark report and
admin API return the same contributing event identities and exclusive totals.
Preparation and assistance remain separate from trial spend; shared preparation
is not copied into every cell or counted as another repetition.

Tests must cover duplicate source delivery, a distinct retry, partial usage,
late-arriving facts, missing pricing, mismatched currencies, concurrent runs,
historical artifacts, permission denial and ledger API unavailability. No paid
model call is needed to establish numerical correctness.
