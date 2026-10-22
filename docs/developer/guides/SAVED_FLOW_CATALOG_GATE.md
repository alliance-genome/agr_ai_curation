# Saved-flow catalog regression gate

`tests/unit/lib/flows/test_catalog_compatibility_gate.py` runs in the normal
Backend Unit Tests CI job (including package/config changes). Run it before a
release and when changing catalog identifiers or validator bindings:

```bash
bash scripts/testing/docker-test-compose.sh run --rm backend-unit-tests \
  python -m pytest tests/unit/lib/flows/test_catalog_compatibility_gate.py -q
```

The gate compares the installed package catalog with a fixed baseline of saved
selection identities. Display text and package version changes are intentionally
excluded; attachment identity, validator target, binding, scope, and field target
are checked. Additions are allowed. Unhandled removals and changes to an existing identity fail with
the affected agent and attachment identifier. Agent removal also fails. A new
identifier plus a removed identifier is treated as addition plus removal; the
gate does not infer that these represent a rename. The removal must still have
fixed upgrade evidence. When authoring such a case, explicitly preserve saved
choices (including opt-outs), or document the approved intentional removal.

The baseline `catalog-545c47b14.json` was captured from main before v0.11.0.
`retired-allele-v094.json` is a synthetic reproduction of the retired-selection
shape behind ALL-1052; it contains no private curator data. Its identifiers are
literal, not derived from the current migration registry. The lifecycle test
uses real API conversion and strict validation, with storage/auth stubbed, and
also prepares the real ordered tool set with model factories stubbed. The
explicit upgrade entry point is exercised with an isolated database adapter
that runs the actual definition repair against fixed before/after fixtures.
A working retirement declaration without this evidence fails the gate. It is not a live extraction
or a database upgrade test.

## Changing the catalog

1. Keep historical fixtures unchanged. Do not regenerate an old baseline to
   make a failing change pass.
2. Provide an explicit forward data migration and a fixed before/after fixture
   when a persisted selection changes. The existing retired-attachment repair
   only supports exact removal; it does not rename agents or rebind validators. The `tested_removals` fixture
   registers the fixed case and verifies the real Alembic entry point dispatches
   its repair; extend that evidence for every newly permitted removal.
3. Exercise the actual repair, prove idempotency and preservation of unrelated
   flow structure, and pass list/open, strict validation, and execution
   preparation. Add a negative case with the repair absent. A declaration alone
   is not sufficient.
4. For a rename or rebinding, extend the test harness with the actual new explicit
   migration and its before/after lifecycle fixture. Do not add a blanket
   allowlist or runtime compatibility behavior. An existing identifier cannot silently change bindings. A rename represented
   as removal plus addition needs an explicit before/after case that verifies
   the intended handling of saved choices.
5. At release, add a new immutable baseline if the supported catalog gained
   selections; retain earlier baselines for the supported upgrade window and
   parameterize the gate over them. Record the source commit. Use current
   runtime catalog output, retaining only the identity fields listed in the
   test. Do not store prompts, credentials, or curator data in fixtures.

This is a development regression gate. It does not modify production flows,
add an LLM judgment check, or replace PostgreSQL migration validation and the
release smoke tests.
