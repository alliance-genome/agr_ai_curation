# Test Strategy

This repository is Docker-first for backend validation. In Symphony issue
workspaces, run backend suites through `docker-compose.test.yml` so tests use
the same service wiring and dependencies as CI.

## Default Commands

Use the narrowest suite that covers the change:

```bash
# Backend unit suite
bash scripts/utilities/symphony_backend_test.sh run --rm backend-unit-tests

# Backend contract suite
bash scripts/utilities/symphony_backend_test.sh run --rm backend-contract-tests

# Full backend suite
bash scripts/utilities/symphony_backend_test.sh run --rm backend-tests

# Specific backend test file
bash scripts/utilities/symphony_backend_test.sh run --rm backend-unit-tests \
  bash -lc "python -m pytest tests/unit/path/to/test.py -v --tb=short"
```

The Symphony wrapper takes a real-workspace lock and a derived
Docker-daemon/Compose-project lock before invoking `docker-compose.test.yml`,
preventing unit/contract/integration commands from starting concurrently
against the same project resources. It never cleans up containers on the
normal path. If it reports a recognized stale
container/network collision, `--repair-known-collision` explicitly permits a
project-scoped `down --remove-orphans` followed by the configured bounded retry.
Do not opt into repair while a raw Compose command is still active in that
workspace.
The Symphony wrapper explicitly selects rootful Docker by default because the
Incus VM uses the system daemon at `/var/run/docker.sock`; it does not depend on
the general test helper's rootless default. Set `AI_CURATION_TEST_DOCKER_MODE`
or pass `--rootless` only in an environment with a working rootless daemon.
The `--rootful` and `--rootless` selectors are supported. Custom Compose
project, directory, env-file, profile, and file selectors are rejected because
they would make lock and cleanup identity ambiguous.

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
- validation findings and curator review flows,
- export/submission readiness,
- Agent Studio/Opus tool contracts,
- TraceReview support.

The offline provider-agnostic release gate uses:

```bash
bash scripts/utilities/symphony_backend_test.sh run --rm backend-unit-tests \
  bash -lc "bash tests/unit/run_ci_unit_tests.sh --suite domain-envelope-release"
```

The path list is `backend/tests/unit/.domain-envelope-release-test-paths`.

The Alliance domain-pack contract gate uses:

```bash
bash scripts/utilities/symphony_backend_test.sh run --rm backend-contract-tests \
  bash -lc "bash tests/contract/run_ci_contract_core_tests.sh \
    --path-file tests/contract/.alliance-domain-pack-test-paths \
    --suite-label alliance-domain-pack"
```

The path list is `backend/tests/contract/.alliance-domain-pack-test-paths`.

## Guardrail Catalog

Invariant, scan, and smoke guards are catalogued in
`docs/testing/guardrail-catalog.md`. Any new guardrail test should add a catalog
row in the same change, including what it protects, its trace or incident, and
the repo-relative test module or guard file.

The cheap structural catalog check is:

```bash
bash scripts/utilities/symphony_backend_test.sh run --rm backend-unit-tests \
  bash -lc "python -m pytest tests/unit/test_guardrail_catalog.py -v --tb=short"
```

## Release Gate and Skill Alignment

When adding or changing tests, smoke scripts, evidence runners, or guardrails
that affect dev-release readiness, update the release skill in the same change
or record why it does not apply. In practice, this means checking
`$ai-curation-release`, especially its `references/dev-validation.md`, whenever
the new coverage should be required before production release.

Examples that should trigger a release-skill update:

- a new deployed-backend smoke or live integration gate,
- new required coverage for flows, batch, export/download artifacts, TraceReview,
  Langfuse, ABC Literature, Add Literature, or agent evidence quality,
- new required release evidence JSON or PR evidence marker,
- any change to the order of full backend/frontend gates, deployed smoke,
  agent evidence review, or browser/manual approval.

Keep the skill, this document, `scripts/README.md`, and any release/runbook docs
consistent so future agents run the same release gate humans expect.

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
bash scripts/utilities/symphony_backend_test.sh run --rm backend-contract-tests \
  bash -lc "ALLIANCE_LIVE_DB_CONTRACT_TESTS=1 \
    bash tests/contract/run_ci_contract_core_tests.sh \
      --path-file tests/contract/.alliance-live-db-test-paths \
      --suite-label alliance-live-db \
      --require-truthy-env ALLIANCE_LIVE_DB_CONTRACT_TESTS"
```

These tests should prove lookup contract shape, projection metadata, and audit
attempt behavior against read-only curation DB access. They must not become a
hidden dependency of regular PR validation.

## Validation and Curator-Edit Coverage

Domain-envelope tests should cover the implemented contract, not design notes:

- `DomainEnvelope` schema validation for object refs, field paths, findings, and
  history.
- `DomainPackMetadata` validation for object definitions, field definitions,
  schema refs, fixture packs, and metadata references.
- `DomainPackValidationRegistry` handling of active/under-development validator
  bindings, default-enabled attachment policy, export-blocking policy, and
  explicit flow replacement/skip locks.
- `run_domain_envelope_structural_checks()` behavior for required fields,
  `dispatch_active_validator_bindings()` behavior for active bindings, and
  `append_validation_findings_to_envelope()` stable finding IDs.
- `lookup_attempts` as an audit trail, including transient attempts that may
  exist even when the top-level lookup status succeeds after retry.
- unresolved validation findings, lookup attempts, curator messages, protected
  fields, and stale revision rejection.
- Materialized review rows as projections over persisted envelope objects.
- Export/submission readiness blockers and expected envelope revision checks.
- Agent Studio/Opus validation inspection: domain-envelope state, domain-pack
  validation plans, validator-agent prompt inspection, review rows, and
  export/submission readiness should stay covered by prompt/tool policy tests.

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
