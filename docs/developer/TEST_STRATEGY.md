# Test Strategy

This page summarizes the validation commands that are intended to be actionable
in local development and Symphony issue workspaces.

## Backend

The repository is Docker-first for backend validation. In Symphony issue
workspaces, use the isolated test compose file instead of host-local pytest or
the long-running development stack.

```bash
# Common day-to-day backend validation
docker compose -f docker-compose.test.yml run --rm backend-unit-tests

# Contract boundary validation
docker compose -f docker-compose.test.yml run --rm backend-contract-tests

# Specific backend unit test file
docker compose -f docker-compose.test.yml run --rm backend-unit-tests \
  bash -lc "python -m pytest tests/unit/path/to/test.py -v --tb=short"
```

Host Python is appropriate for syntax-only checks:

```bash
python3 -m py_compile backend/src/path/to/file.py
```

## Frontend

Install dependencies from `frontend/` before frontend validation:

```bash
cd frontend
npm ci
```

For frontend tests, run Vitest directly on the host:

```bash
npm run test -- --run
```

For ticket-local TypeScript validation, use the scoped type-check guard:

```bash
npm run type-check
```

`npm run type-check` is intentionally the same as
`npm run type-check:changed`. It compares changed `.ts`, `.tsx`, `.mts`, and
`.cts` files plus `frontend/tsconfig*.json` against `TYPECHECK_BASE`
(default: `origin/main`) plus staged, unstaged, and untracked files. The command
still runs the TypeScript compiler, but it only fails when compiler diagnostics
belong to changed frontend TypeScript source/config files or when TypeScript
emits a global configuration diagnostic.

Use the full repo-wide compiler scan when you are intentionally paying down
frontend TypeScript debt or investigating the baseline:

```bash
npm run type-check:all
```

As of ALL-283, `npm run type-check:all` is known to fail on existing frontend
TypeScript debt. Treat those failures as baseline debt unless the diagnostic
points at a file changed by the current ticket. Do not block unrelated
frontend tickets solely because the full baseline still reports older errors.

To compare branch changes against a different base ref:

```bash
TYPECHECK_BASE=origin/release npm run type-check
# or
npm run type-check -- --base origin/release
```
