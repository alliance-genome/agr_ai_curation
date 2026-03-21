# AGENTS.md

This file is a fast startup map for humans and coding agents working in `agr_ai_curation`.

## 1) System Boundaries

- App type: full-stack AI curation platform.
- Backend: `backend/` (FastAPI, config-driven agents, tool runtime).
- Frontend: `frontend/` (React/TypeScript UI).
- Runtime config + agent definitions: `config/`.
- Local Symphony orchestration: `.symphony/`.
- Persistent stores/services are orchestrated via `docker-compose*.yml`.
- Docker-first layout:
  - `docker-compose.yml` for local development stacks
  - `docker-compose.test.yml` for isolated test runs
  - `docker-compose.production.yml` for standalone / production-style deploys

## 2) Authoritative Docs

- Repository knowledge index: `docs/README.md`
- Developer docs index: `docs/developer/README.md`
- Curator docs index: `docs/curator/README.md`
- Config system of record: `config/README.md`
- Test strategy and known scope: `docs/developer/TEST_STRATEGY.md`
- Symphony workflow contract: `.symphony/WORKFLOW.md`

## 3) Test + Validation Commands

- Most backend testing, especially in Symphony issue workspaces, should use `docker-compose.test.yml` rather than the long-running local dev stack.
- Prefer `backend-unit-tests` for most day-to-day backend changes. Use `backend-contract-tests`, `backend-integration-tests`, `backend-persistence-tests`, or `backend-tests` when the ticket or acceptance criteria need the heavier end-to-end test image or broader stack coverage.
- Local dev checkout with the main app stack running:
  - Backend unit tests: `docker compose exec backend pytest tests/unit/ -v`
  - Backend contract core tests: `docker compose exec backend pytest tests/contract/ -q`
  - Frontend tests: `docker compose exec frontend npm run test -- --run`
  - Frontend build: `docker compose exec frontend npm run build`
- Symphony issue workspaces and isolated backend test runs:
  - Backend unit tests: `docker compose -f docker-compose.test.yml run --rm backend-unit-tests`
  - Backend contract tests: `docker compose -f docker-compose.test.yml run --rm backend-contract-tests`
  - Specific backend test file: `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/path/to/test.py -v --tb=short"`
  - Syntax-only validation: `python3 -m py_compile backend/src/path/to/file.py`
- LLM provider smoke (local evidence JSON):
  - `./scripts/testing/llm_provider_smoke_local.sh`
- Agent PR gate (local):
  - `./scripts/testing/agent_pr_gate.sh`

## 4) Dangerous Areas

- Secrets: never commit `.env`, API keys, or any credential files.
- Migrations/data integrity: review backend persistence changes carefully.
- Tool policy and agent config: changes in `config/` can alter runtime behavior broadly.
- Symphony runtime controls: `.symphony/WORKFLOW.md` controls unattended execution behavior.
- Symphony runtime helper copies in issue workspaces may be intentionally untracked; do not assume `git status` noise in a Symphony workspace means PR-relevant repo changes.
- CI ignore-path files:
  - `backend/tests/unit/.ci-ignore-paths`
  - `backend/tests/contract/.core-test-paths`
  - Treat edits as high-risk and always justify in PR notes.

## 5) Symphony Runtime Notes

- Canonical Symphony orchestration sources live under `.symphony/`.
- For dispatched Symphony runs, `.symphony/WORKFLOW.md` is the active execution contract. If this startup map and the workflow disagree for a Symphony workspace, follow `.symphony/WORKFLOW.md`.
- `.symphony/` may be intentionally untracked in this repo/workspace; do not assume its absence from Git is accidental or PR-relevant.
- The `.symphony/` tree is manually backed up every night, so local runtime changes may be preserved outside normal Git tracking.
- Workspace-local runtime helpers are materialized by `scripts/utilities/symphony_ensure_workspace_runtime.sh`.
- Some helpers are copied and renamed for workspace use. Important example:
  - source of truth: `.symphony/allocate_issue_ports.sh`
  - workspace runtime copy: `scripts/symphony_allocate_issue_ports.sh`
- Workspace helper copies may exist on disk without being tracked by Git in the main repo or in the workspace branch. This is intentional for local/Symphony runtime support.
- Presence on disk is not the same as a tracked repo file. Check `git ls-files` before deciding a helper belongs in a PR.
- If a workflow references `scripts/symphony_allocate_issue_ports.sh` and the file is missing in a workspace, check the source under `.symphony/allocate_issue_ports.sh` and the runtime sync script before assuming the repo lost functionality.

### Symphony deployment route for new/modified scripts

Symphony runs inside an Incus VM (`symphony-main`). There are TWO categories of files with different deployment paths:

1. **Git-tracked scripts** (`scripts/utilities/symphony_*.sh`):
   - Committed to the repo and pushed to `origin/main`.
   - Synced into per-issue workspaces by `scripts/utilities/symphony_ensure_workspace_runtime.sh`.
   - **CRITICAL**: New scripts MUST be added to the `ensure_one` manifest in `symphony_ensure_workspace_runtime.sh` or workspaces will not have them. Existing workspaces get updated on the next `before_run` hook.

2. **Gitignored orchestration files** (`.symphony/WORKFLOW.md`, `.symphony/*.sh`):
   - `.symphony/` is in `.gitignore` — these files are NOT committed to the repo.
   - Deployed by copying directly into the VM's local source root:
     `incus file push .symphony/WORKFLOW.md symphony-main/<repo-path>/.symphony/WORKFLOW.md`
   - The `ensure_workspace_runtime.sh` hook then copies them from the local source root into per-issue workspaces.

**When adding a new Symphony lane helper**:
1. Create the script in `scripts/utilities/symphony_<name>.sh` (git-tracked).
2. Add an `ensure_one` line for it in `scripts/utilities/symphony_ensure_workspace_runtime.sh`.
3. Commit and push both files.
4. If the script is referenced in WORKFLOW.md, update `.symphony/WORKFLOW.md` and push it to the VM.
5. Existing workspaces pick up the new script on their next `before_run` hook execution.

## 6) Expected Change Workflow

1. Sync branch and inspect changed scope.
2. Reproduce or define the expected behavior first.
3. Make minimal, scoped code/config edits.
4. Run targeted validation + tests.
5. Update docs if behavior or process changed.
6. For Symphony/Linear execution, keep a single workpad-style progress trail and clear acceptance criteria.
