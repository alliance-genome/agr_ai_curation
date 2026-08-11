# AGENTS.md

This file is a fast startup map for humans and coding agents working in
`agr_ai_curation`.

## 1) System Boundaries

- App type: full-stack AI curation platform.
- Backend: `backend/` (FastAPI, config-driven agents, tool runtime).
- Frontend: `frontend/` (React/TypeScript UI).
- Runtime config and agent definitions: `config/`.
- Persistent stores and services are orchestrated through the
  `docker-compose*.yml` files.
- Docker-first layout:
  - `docker-compose.yml` for local development stacks
  - `docker-compose.test.yml` for isolated test runs
  - `docker-compose.production.yml` for standalone/production-style deploys

Private deployment and workstation orchestration are maintained outside this
public application repository. Application changes must remain usable from a
fresh clone without access to private operations tooling.

## 2) Authoritative Docs

- Repository knowledge index: `docs/README.md`
- Developer docs index: `docs/developer/README.md`
- Curator docs index: `docs/curator/README.md`
- Development doctrine: `docs/developer/guides/DEVELOPMENT_DOCTRINE.md`
- Config system of record: `config/README.md`
- Test strategy and known scope: `docs/developer/TEST_STRATEGY.md`

## 3) Test and Validation Commands

Prefer `backend-unit-tests` for day-to-day backend changes. Use
`backend-contract-tests`, `backend-integration-tests`,
`backend-persistence-tests`, or `backend-tests` when acceptance criteria
need broader coverage.

For isolated backend test runs:

- Backend unit tests:
  `bash scripts/testing/docker-test-compose.sh run --rm backend-unit-tests`
- Backend contract tests:
  `bash scripts/testing/docker-test-compose.sh run --rm backend-contract-tests`
- Specific backend test:
  `bash scripts/testing/docker-test-compose.sh run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/path/to/test.py -v --tb=short"`

For a local development checkout with the main app stack running:

- Backend unit tests: `docker compose exec backend pytest tests/unit/ -v`
- Backend contract tests: `docker compose exec backend pytest tests/contract/ -q`
- Focused frontend tests:
  `docker compose exec frontend npm run test -- --run src/path/to/File.test.tsx`
- Frontend build: `docker compose exec frontend npm run build`

Host-side frontend validation:

- Focused frontend tests:
  `cd frontend && npm ci && npm run test -- --run src/path/to/File.test.tsx`
- Scoped TypeScript guard:
  `cd frontend && npm run type-check:changed -- --base origin/main`

Use focused tests for local implementation and review. The blocking GitHub
`Frontend Tests` job is the authoritative clean-checkout broad suite; run
`npm run test:stable` locally only when documented cross-cutting risk requires
it, not as routine pre-PR validation.

`type-check:changed` runs the full TypeScript compiler but fails only on
changed frontend TypeScript files or unscoped/config-level errors. If it reports
`FRONTEND_TYPECHECK_STATUS=baseline_only`, record the baseline debt and do not
treat it as a change-local failure.

Other useful validation:

- Syntax-only Python:
  `PYTHONPYCACHEPREFIX=/tmp/agr-ai-curation-pycache python3 -m py_compile backend/src/path/to/file.py`
- LLM provider smoke: `./scripts/testing/llm_provider_smoke_local.sh`
- Agent PR gate: `./scripts/testing/agent_pr_gate.sh`

Real extraction and release-evidence runs require a healthy local stack,
backend-to-Langfuse reachability, durable Langfuse dependencies, configured
read-only curation/literature access, and intact Compose DNS. Follow
`docs/developer/DEV_RELEASE_SMOKE_STRATEGY.md` and the release workflow for
the target environment.

OpenAI Responses websocket transport is the default
(`OPENAI_RESPONSES_WEBSOCKET_ENABLED=true`). A websocket handshake failure is
a transport/provider issue; disabling websocket is acceptable only as a narrow
diagnostic workaround, not as a permanent configuration change.

## 4) Agent Semantic Navigation Tools

Use `rg` first for broad discovery. When the remaining uncertainty is about
symbol ownership, definitions, references, imports/exports, or shared API blast
radius, use `scripts/utilities/agent_lsp.py` before editing or final review.

Common commands:

- `scripts/utilities/agent_lsp.py --root . status`
- `scripts/utilities/agent_lsp.py symbols path/to/file.py`
- `scripts/utilities/agent_lsp.py definition path/to/file.py 120 17`
- `scripts/utilities/agent_lsp.py references path/to/file.py 120 17`
- `scripts/utilities/agent_lsp.py --timeout 30 diagnostics --changed`
- `scripts/utilities/agent_lsp.py diagnostics backend/src/path.py frontend/src/path.tsx`

Positions are 1-based by default. Add `--zero-based` only for raw editor/LSP
coordinates. Diagnostics are navigation and review aids, not replacements for
required Docker tests.

## 5) Dangerous Areas

- Secrets: never commit `.env`, API keys, passwords, tokens, or credential
  files.
- Migrations and persistence: review data-integrity changes carefully.
- Tool policy and agent config: changes under `config/` can alter runtime
  behavior broadly.
- CI path-selection files:
  - `backend/tests/unit/.ci-ignore-paths`
  - `backend/tests/contract/.core-test-paths`
  Treat changes to these files as high risk and justify them in PR notes.

## 6) Expected Change Workflow

1. Sync the branch and inspect the changed scope.
2. Reproduce the problem or define expected behavior first.
3. Make minimal, scoped changes. Default to forward-only development: remove
   fallbacks, compatibility paths, and legacy branches instead of extending
   them; use explicit migrations for persistence changes.
4. Run targeted validation and tests.
5. Update documentation when behavior or process changes.
6. Keep one workpad-style progress trail and clear acceptance criteria for
   autonomous agent work.
7. Surface every operational limit through environment configuration as
   described below.

## 7) Operational Limits Must Be Surfaced to `.env`

Every operational limit must be environment-configurable with its current value
as the default and documented in `.env.example`. Never introduce a bare
hardcoded operational limit.

Operational limits include agent/validator turn limits, tool-call budgets, batch
sizes, parallelism caps, list/page/section caps, result caps, retry counts,
timeouts, size/preview thresholds, and feature kill switches. Pure internal
plumbing waits, such as a short queue poll, are exempt.

For backend code, add a getter in
`backend/src/lib/openai_agents/config.py` using the existing environment
helpers. Isolated package code under
`packages/alliance/python/src/agr_ai_curation_alliance/` cannot import the
backend config module; read the same environment variable directly there so one
setting tunes both processes.

Document each setting under the `# Operational limits` section of
`.env.example`, including what it controls, why someone might change it, and
its default. Adding a numeric or boolean operational limit without both the
environment getter and documentation is a review-blocking defect.
