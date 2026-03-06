# AGENTS.md

This file is a fast startup map for humans and coding agents working in `agr_ai_curation`.

## 1) System Boundaries

- App type: full-stack AI curation platform.
- Backend: `backend/` (FastAPI, config-driven agents, tool runtime).
- Frontend: `frontend/` (React/TypeScript UI).
- Runtime config + agent definitions: `config/`.
- Local Symphony orchestration: `.symphony/`.
- Persistent stores/services are orchestrated via `docker-compose*.yml`.

## 2) Authoritative Docs

- Repository knowledge index: `docs/README.md`
- Developer docs index: `docs/developer/README.md`
- Curator docs index: `docs/curator/README.md`
- Config system of record: `config/README.md`
- Test strategy and known scope: `docs/developer/TEST_STRATEGY.md`
- Symphony workflow contract: `.symphony/WORKFLOW.md`

## 3) Test + Validation Commands

- Backend unit tests:
  - `docker compose exec backend pytest tests/unit/ -v`
- Backend contract core tests:
  - `docker compose exec backend pytest tests/contract/ -q`
- Frontend tests:
  - `docker compose exec frontend npm run test -- --run`
- Frontend build:
  - `docker compose exec frontend npm run build`
- LLM provider smoke (local evidence JSON):
  - `./scripts/testing/llm_provider_smoke_local.sh`
- Agent PR gate (local):
  - `./scripts/testing/agent_pr_gate.sh`

## 4) Dangerous Areas

- Secrets: never commit `.env`, API keys, or any credential files.
- Migrations/data integrity: review backend persistence changes carefully.
- Tool policy and agent config: changes in `config/` can alter runtime behavior broadly.
- Symphony runtime controls: `.symphony/WORKFLOW.md` controls unattended execution behavior.
- CI ignore-path files:
  - `backend/tests/unit/.ci-ignore-paths`
  - `backend/tests/contract/.core-test-paths`
  - Treat edits as high-risk and always justify in PR notes.

## 5) Expected Change Workflow

1. Sync branch and inspect changed scope.
2. Reproduce or define the expected behavior first.
3. Make minimal, scoped code/config edits.
4. Run targeted validation + tests.
5. Update docs if behavior or process changed.
6. For Symphony/Linear execution, keep a single workpad-style progress trail and clear acceptance criteria.

