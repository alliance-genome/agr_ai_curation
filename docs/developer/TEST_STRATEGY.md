# Test Strategy

This repository is Docker-first for backend validation. In Symphony issue
workspaces, run backend suites through `docker-compose.test.yml` so tests use
the same service wiring and dependencies as CI.

## Default Commands

Use the narrowest suite that covers the change:

```bash
# Backend unit suite
docker compose -f docker-compose.test.yml run --rm backend-unit-tests

# Backend contract suite
docker compose -f docker-compose.test.yml run --rm backend-contract-tests

# Full backend suite
docker compose -f docker-compose.test.yml run --rm backend-tests

# Specific backend test file
docker compose -f docker-compose.test.yml run --rm backend-unit-tests \
  bash -lc "python -m pytest tests/unit/path/to/test.py -v --tb=short"
```

Frontend validation runs on the host Node toolchain:

```bash
cd frontend
npm ci
npm run test:symphony
npm run type-check:changed -- --base origin/main
```

`FRONTEND_TYPECHECK_STATUS=baseline_only` means the TypeScript compiler found
existing errors outside changed frontend files. Record the baseline debt, but do
not treat it as ticket-local failure.

For syntax-only Python checks, keep cache artifacts outside the workspace:

```bash
PYTHONPYCACHEPREFIX=/tmp/symphony-pycache \
  python3 -m py_compile backend/src/path/to/file.py
```

## Domain-Envelope Release Gates

The 0.7.0 domain-envelope gates are recorded in
`backend/tests/fixtures/domain_packs/release_gate_matrix.yaml`. They cover:

- provider-agnostic fixture packs,
- Alliance domain-pack metadata,
- pinned LinkML grounding checks,
- explicit opt-in live curation DB projections,
- one-off legacy migration coverage,
- materialization,
- validation and repair loops,
- export/submission readiness,
- Agent Studio/Opus tool contracts,
- TraceReview support.

The offline provider-agnostic release gate uses:

```bash
docker compose -f docker-compose.test.yml run --rm backend-unit-tests \
  bash -lc "bash tests/unit/run_ci_unit_tests.sh --suite domain-envelope-release"
```

The path list is `backend/tests/unit/.domain-envelope-release-test-paths`.

The Alliance domain-pack contract gate uses:

```bash
docker compose -f docker-compose.test.yml run --rm backend-contract-tests \
  bash -lc "bash tests/contract/run_ci_contract_core_tests.sh \
    --path-file tests/contract/.alliance-domain-pack-test-paths \
    --suite-label alliance-domain-pack"
```

The path list is `backend/tests/contract/.alliance-domain-pack-test-paths`.

## LinkML and Domain-Pack Fixtures

Alliance domain packs pin LinkML provider refs in package metadata. Tests should
use those refs and the schema cache helper rather than guessing field/class
semantics from memory.

Use:

```bash
scripts/testing/cache_agr_curation_schema.sh
```

when a contract test needs the pinned Alliance LinkML cache. Keep generic core
tests provider-neutral. Alliance-specific classes, slots, MOD examples, and AGR
curation database projections belong in Alliance package contract tests.

Fixture packs live under `backend/tests/fixtures/domain_packs/` and
`packages/alliance/domain_packs/*/fixtures/`. They should exercise real
`DomainEnvelope` shapes, including object IDs or pending refs, field paths,
validation findings, evidence metadata, and projection metadata when relevant.

## Live Curation DB Gate

Live curation DB tests are opt-in and must stay out of normal offline unit and
contract runs. The live path list is:

```text
backend/tests/contract/.alliance-live-db-test-paths
```

Run only with explicit enablement:

```bash
docker compose -f docker-compose.test.yml run --rm backend-contract-tests \
  bash -lc "ALLIANCE_LIVE_DB_CONTRACT_TESTS=1 \
    bash tests/contract/run_ci_contract_core_tests.sh \
      --path-file tests/contract/.alliance-live-db-test-paths \
      --suite-label alliance-live-db \
      --require-truthy-env ALLIANCE_LIVE_DB_CONTRACT_TESTS"
```

These tests should prove lookup contract shape, projection metadata, and audit
attempt behavior against read-only curation DB access. They must not become a
hidden dependency of regular PR validation.

## Validation and Repair Coverage

Domain-envelope tests should cover the implemented contract, not design notes:

- `DomainEnvelope` schema validation for object refs, field paths, findings, and
  history.
- `DomainPackMetadata` validation for object definitions, field definitions,
  schema refs, fixture packs, and metadata references.
- `DomainPackValidationRegistry` normalization of active/planned/blocked
  validators, required/export-blocking policies, and opt-out policy.
- `run_validation_supervisor()` behavior for required fields, planned/blocked
  findings, active bindings, dispatch-unavailable findings, and stable finding
  IDs.
- `lookup_attempts` as an audit trail, including transient attempts that may
  exist even when the top-level lookup status succeeds after retry.
- `DomainEnvelopeRepairRequest`, `DomainEnvelopeRepairPatch`, retry budgets,
  protected fields, stale revision rejection, and final classifications.
- Materialized review rows as projections over persisted envelope objects.
- Export/submission readiness blockers and expected envelope revision checks.

## Changelog and Docs Validation

For docs-only changes, run at least:

```bash
git diff --check
```

If frontend changelog source files change, run a targeted frontend validation:

```bash
cd frontend
npm run type-check:changed -- --base origin/main
```

Run broader frontend tests when the change affects runtime UI components, not
only static changelog content.

Run the broader harness hygiene check when a docs change needs repository-wide
link validation:

```bash
./scripts/maintenance/harness_hygiene.sh
```

The harness includes a Markdown link check and required-doc presence check. In
local Symphony environments it may also report unrelated stale workspace
hygiene; record that separately from ticket-local docs failures.
