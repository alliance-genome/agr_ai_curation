# Production/main migration convergence

The September 2026 production and main branches assigned the same three
Alembic IDs to different changes. The combined graph keeps main's existing
benchmark IDs and gives the incoming production profile migrations unique IDs:

| Production's former ID | Canonical ID | Change |
| --- | --- | --- |
| `f3a4b5c6d7e8` | `3cea536116c6` | Generic profile revisions |
| `g4b5c6d7e8f9` | `fd396e8286ab` | Agent execution revisions |
| `h5c6d7e8f9a0` | `314e1a470941` | Profile validator references |

Their DDL bodies are unchanged. Production flow pinning `i6d7e8f9a0b1` now
follows `314e1a470941`; production's remaining chain and main's benchmark chain
retain their IDs and order. Merge revision `f54e2c6f6848` joins unchanged
terminal heads `p3e4f5a6b7c8` (production) and `7c9e2a4b6d80` (main).

## Upgrade contract

Use normal `alembic upgrade head` after verifying the original release and
database head and taking the environment's normal protected backup. A database
at either verified terminal head runs only its missing branch. A fresh database
runs both branches. No deployed `alembic_version` row is manually changed, no
applied DDL is replayed, and no data is discarded or reconstructed from current
application model metadata.

The online environment refuses a database currently stamped one of the three
ambiguous IDs above **before scheduling migration DDL**. Such a stamp alone
cannot identify which history was applied. Verify and finish its original
release's migration chain to that release's known terminal head, using the
original release and normal operational review, before using the combined
graph. Do not guess from table presence, manually stamp it, or disable the
guard. Offline SQL generation remains unchanged; it does not validate a live
database's provenance and is not a substitute for the online guard.

The main-only durable saved-flow repair `i6j7k8l9m0n1` remains distinct from
production flow pinning `i6d7e8f9a0b1`. Retired validation selections are repaired
transactionally by migrations; runtime read/execute paths do not repair copies
of stored definitions.

## Validation

Run the graph and online-environment guard tests through the Docker backend
unit-test service. Release validation must also exercise real disposable
PostgreSQL upgrades from each original parent and from an empty database.
Populate representative benchmark and profile/revision/pinned-flow records in
their respective original-parent fixtures and verify IDs, counts, digests,
immutable guards and both schema families after convergence. A stamp-only or
`Base.metadata.create_all` fixture is not evidence of an upgrade path.

The opt-in executable rehearsal is
`backend/tests/integration/test_production_main_migration_convergence.py`.
Supply `MIGRATION_CONVERGENCE_TEST_DATABASE_URL` for an owned disposable
loopback PostgreSQL instance (user `migration_test`, database
`migration_control`), and `MIGRATION_MAIN_PARENT_ROOT` /
`MIGRATION_PRODUCTION_PARENT_ROOT` pointing to read-only original source exports.
The test creates unique `convergence_test_*` databases, runs original-parent
Alembic migrations and deterministic fixture writes, upgrades using the current
source, verifies preserved rows and constraints, and removes only those test
databases. Run with `--noconftest` to avoid unrelated service fixtures. The
owning operator must remove only the disposable PostgreSQL resource afterward.

This rehearsal exercises the existing release migration/preservation gate; it
does not change release ordering, authorize deployment or add a provider smoke.
