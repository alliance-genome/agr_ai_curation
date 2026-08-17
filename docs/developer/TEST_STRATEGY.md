# Test Strategy

This repository is Docker-first for backend validation. Run isolated backend
suites through `docker-compose.test.yml` so tests use the same service wiring
and dependencies as CI.

## Default Commands

Use the narrowest suite that covers the change:

```bash
# Backend unit suite
bash scripts/testing/docker-test-compose.sh run --rm backend-unit-tests

# Backend contract suite
bash scripts/testing/docker-test-compose.sh run --rm backend-contract-tests

# Full backend suite
bash scripts/testing/docker-test-compose.sh run --rm backend-tests

# Specific backend test file
bash scripts/testing/docker-test-compose.sh run --rm backend-unit-tests \
  bash -lc "python -m pytest tests/unit/path/to/test.py -v --tb=short"
```

The generic Compose helper uses rootless Docker by default. Set
`AI_CURATION_TEST_DOCKER_MODE=rootful` or pass `--rootful` when the local
environment intentionally uses the system daemon. Run only one command against
the same Compose project at a time, and inspect active containers before any
manual cleanup.

Backend test images keep generated caches outside bind-mounted source trees:
Python bytecode is redirected to `/tmp/agr-ai-curation-python-pycache`, and
pytest uses `/tmp/agr-ai-curation-pytest-cache`. Both locations are local to
the test container, so rootful and rootless runs leave no `__pycache__` or
`.pytest_cache` directories in the checkout. New backend test images must use
the same policy.

Frontend validation runs on the host Node toolchain:

```bash
cd frontend
npm ci
npm run test -- --run src/path/to/File.test.tsx  # replace with the changed test file
npm run type-check:changed -- --base origin/main
```

Run the smallest frontend test selection that proves the changed behavior.
The blocking GitHub `Frontend Tests` job owns the complete clean-checkout
suite. Run `npm run test:stable` locally only for documented cross-cutting
risk, not as routine pre-PR validation or review duplication.
GitHub intentionally runs Vitest with its default parallel configuration for
faster broad feedback. The low-concurrency `test:stable` script is a local
diagnostic for concurrency-sensitive failures, not the CI-equivalent command.

`FRONTEND_TYPECHECK_STATUS=baseline_only` means the TypeScript compiler found
existing errors outside changed frontend files. Record the baseline debt, but do
not treat it as ticket-local failure.

For syntax-only Python checks, keep cache artifacts outside the workspace:

```bash
PYTHONPYCACHEPREFIX=/tmp/agr-ai-curation-pycache \
  python3 -m py_compile backend/src/path/to/file.py
```

## Figure Locator Classifier Evaluation

The labeled corpus at
`backend/tests/fixtures/figure_locator/semantic_cases.json` keeps candidate
selection errors separate from semantic-classifier errors. Unit tests verify
the broad candidate selector offline. To evaluate the configured live Terra
classifier, run:

```bash
bash scripts/testing/docker-test-compose.sh run --rm \
  -e OPENAI_API_KEY \
  -e FIGURE_LOCATOR_LLM_MODEL=gpt-5.6-terra \
  -e FIGURE_LOCATOR_LLM_REASONING=low \
  backend-integration-tests \
  bash -lc "cd /app && python scripts/testing/evaluate_figure_locator_classifier.py"
```

The report lists `candidate_misses`, `false_singletons`, `false_omissions`, and
`cardinality_mismatches` independently. Any false singleton fails the evaluation.
This is an opt-in classifier-quality diagnostic, not a required release gate.

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
bash scripts/testing/docker-test-compose.sh run --rm backend-unit-tests \
  bash -lc "bash tests/unit/run_ci_unit_tests.sh --suite domain-envelope-release"
```

The path list is `backend/tests/unit/.domain-envelope-release-test-paths`.

The Alliance domain-pack contract gate uses:

```bash
bash scripts/testing/docker-test-compose.sh run --rm backend-contract-tests \
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
bash scripts/testing/docker-test-compose.sh run --rm backend-unit-tests \
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
bash scripts/testing/docker-test-compose.sh run --rm backend-contract-tests \
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

Run the focused tests for any runtime UI components affected by the change.
Expand to `npm run test:stable` only when a documented cross-cutting or
concurrency-sensitive risk requires it.

Run the broader harness hygiene check when a docs change needs repository-wide
link validation:

```bash
./scripts/maintenance/harness_hygiene.sh
```

The harness includes a Markdown link check and a required-doc presence check.
