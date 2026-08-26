# Langfuse v4 operator migration

This is a one-time procedure for the two AI Curation deployments we control:
the dev server and, later, production. It is not a generic migration framework
and does not authorize a production deployment.

The local Symphony VM is the rehearsal target. Dev is migrated after the same
commands work there. Production is a separate, explicitly approved operation.

Upstream reference:
[Langfuse v3 to v4](https://langfuse.com/self-hosting/upgrade/upgrade-guides/upgrade-v3-to-v4).
Re-read it immediately before each remote migration.

## Repository defaults

The Compose files default to:

| Component | Version |
| --- | --- |
| Langfuse web/worker | 4.21.0, pinned multi-architecture digests |
| ClickHouse | 25.12, pinned multi-architecture digest |
| PostgreSQL | 16 |
| Redis | 7 with `noeviction` and a durable `/data` volume |

The one-time latest-v3 staging override is
`scripts/migrations/langfuse-v4-latest-v3.override.yml`. Do not include it
after the v4 start.

## Before the first v4 start

1. Inventory the exact checkout, Compose project, containers, volumes, disk
   space, and current Langfuse/ClickHouse versions.
2. Preserve a dirty or divergent checkout. Use a clean checkout with the same
   Compose project name when existing volumes must be reused.
3. Put these values in the deployment's ignored `.env` **before** running the
   new Compose file:

   ```dotenv
   LANGFUSE_MIGRATION_V4_WRITE_MODE=legacy
   LANGFUSE_MIGRATION_V4_NATIVE_OTEL_BEHAVIOUR=dual_write
   LANGFUSE_MIGRATION_V4_ALLOW_PREVIEW_OPT_IN=false
   LANGFUSE_BACKGROUND_MIGRATION_V4_ENABLE_HISTORIC_BACKFILL=false
   LANGFUSE_BACKGROUND_MIGRATION_V4_DROP_PID_TID_SORTING_TABLES=false
   LANGFUSE_EVENT_PROPAGATION_STUCK_THRESHOLD_MINUTES=15
   ```

4. Verify PostgreSQL, both ClickHouse volumes, MinIO, and Redis are included in
   the backup inventory. Do not use `docker compose down -v`.
5. Measure ClickHouse usage. Historic backfill needs roughly three times the
   current data size in free space.

## 1. Stage latest v3 and ClickHouse 25.12

Use the repository's pinned ClickHouse default while temporarily overriding
only the Langfuse images:

```bash
COMPOSE_PROJECT=agr_ai_curation  # use an isolated project in the Symphony rehearsal
COMPOSE_FILE=docker-compose.yml
docker compose -p "$COMPOSE_PROJECT" \
  -f "$COMPOSE_FILE" \
  -f scripts/migrations/langfuse-v4-latest-v3.override.yml \
  up -d --force-recreate \
  postgres redis clickhouse minio langfuse-worker langfuse
```

The later standalone production operation must use the production deployment
runbook and its render-backed preflight before running the analogous command.

Require Langfuse health to report `3.225.5` and the worker health check to pass,
confirm existing traces remain readable, and inspect PostgreSQL background
migrations. Every non-v4 job must be finished and unfailed. The five
`20260701_v4_step_*` jobs are expected to remain dormant on v3 while historic
backfill is false.

## 2. Take the recovery backup

Stop Langfuse web/worker, ClickHouse, PostgreSQL, Redis, and MinIO. Create cold
archives or snapshots of their exact durable volumes, checksum them, and start
latest v3 again to confirm the original trace is still readable.

The recovery point is the supported rollback. A plain v4-to-v3 image swap is
not sufficient after v4 ClickHouse schema migrations run.

## 3. Start v4 in legacy mode

With the legacy values still persisted in `.env`, remove the temporary v3
image overrides and recreate ClickHouse, web, and worker from repository
defaults:

```bash
docker compose -p "$COMPOSE_PROJECT" up -d --no-deps --force-recreate \
  clickhouse langfuse-worker langfuse
```

Require health to report `4.21.0`, the worker health endpoint to pass, and an
existing legacy trace to remain readable. Stay in this mode while diagnosing
server-upgrade problems.

## 4. Move to dual write

AI Curation already pins Langfuse Python SDK 4.7.1, which meets the v4 direct
ingestion threshold. TraceReview still uses legacy read surfaces until ALL-766
is complete, so do not jump directly to `events_only`.

Persist and recreate web/worker with:

```dotenv
LANGFUSE_MIGRATION_V4_WRITE_MODE=dual
LANGFUSE_MIGRATION_V4_NATIVE_OTEL_BEHAVIOUR=dual_write
LANGFUSE_MIGRATION_V4_ALLOW_PREVIEW_OPT_IN=true
LANGFUSE_BACKGROUND_MIGRATION_V4_ENABLE_HISTORIC_BACKFILL=false
```

Create a real AI Curation trace. Verify immediate Observations API v2
visibility, legacy TraceReview access, parent/child reconstruction, token
usage, cached-token details, and model cost. Request the `core`, `basic`, and
`usage` field groups when validating v2, for example:

```text
GET /api/public/v2/observations?traceId=<trace-id>&fields=core,basic,usage
```

Older SDK/legacy ingestion reaches the v2 tables through the propagation job
with an expected delay of roughly 15 minutes; SDK 4.7.1 traces should appear
immediately.

## 5. Backfill and cut over

Only after dual write and the ALL-766 TraceReview migration are verified, set:

```dotenv
LANGFUSE_BACKGROUND_MIGRATION_V4_ENABLE_HISTORIC_BACKFILL=true
```

Require v4 backfill steps 1–4 to finish without failure and reconcile trace,
observation, token, and cost evidence. Leave step 5 and
`LANGFUSE_BACKGROUND_MIGRATION_V4_DROP_PID_TID_SORTING_TABLES=false`; cleanup
is irreversible and is not part of this migration.

The final values are:

```dotenv
LANGFUSE_MIGRATION_V4_WRITE_MODE=events_only
LANGFUSE_MIGRATION_V4_NATIVE_OTEL_BEHAVIOUR=direct
LANGFUSE_MIGRATION_V4_ALLOW_PREVIEW_OPT_IN=true
LANGFUSE_BACKGROUND_MIGRATION_V4_ENABLE_HISTORIC_BACKFILL=true
LANGFUSE_BACKGROUND_MIGRATION_V4_DROP_PID_TID_SORTING_TABLES=false
LANGFUSE_EVENT_PROPAGATION_STUCK_THRESHOLD_MINUTES=15
```

Recreate web/worker, restart them once more, and rerun the trace, TraceReview,
and cost checks. Legacy trace APIs return 404 after this cutover by design.

## Stop conditions

Stop and preserve evidence if a durable store is missing from backup, a
required background migration fails, disk headroom is insufficient, worker
propagation health fails, TraceReview cannot reconstruct the trace, or token
and cost totals diverge. Do not improvise a production rollback or delete the
old/scratch tables.
