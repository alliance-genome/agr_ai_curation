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
- Development doctrine: `docs/developer/guides/DEVELOPMENT_DOCTRINE.md`
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
   - **CRITICAL**: After pushing to `origin/main`, you MUST also sync the VM's local source checkout. Symphony uses `SYMPHONY_LOCAL_SOURCE_ROOT` (set in `.symphony/run.sh`) which points to the VM's local checkout — NOT directly to GitHub. If the VM checkout is stale, `ensure_workspace_runtime.sh` will fail to find newly-added required scripts.

2. **Gitignored orchestration/runtime files** (`.symphony/WORKFLOW.md`, `.symphony/*.sh`, `.symphony/elixir/**`):
   - `.symphony/` is in `.gitignore` — these files are NOT committed to the repo.
   - Deployed by copying directly into the VM's local source root with `incus file push`.
   - Important: this category is not limited to `WORKFLOW.md`. If you changed local Symphony runtime code under `.symphony/elixir/`, push those files into the VM too.
   - Example:
     `incus file push .symphony/WORKFLOW.md symphony-main/<repo-path>/.symphony/WORKFLOW.md`
   - Example for runtime code:
     `incus file push .symphony/elixir/lib/symphony_elixir/config.ex symphony-main/<repo-path>/.symphony/elixir/lib/symphony_elixir/config.ex`
   - The `ensure_workspace_runtime.sh` hook then copies workflow/helper files from the local source root into per-issue workspaces; Elixir runtime changes are picked up after rebuild/restart in the VM source tree.

### Local sandbox sync rule

- For normal application code under `backend/`, `frontend/`, `config/`, or other tracked repo files, do **not** manually copy files into `~/.symphony/sandboxes/...`, `incus file push` app code into the VM, or hot-patch the running sandbox containers unless the user explicitly asks for that.
- Preferred path for normal app changes: commit to the repo, push to Git, then use the Symphony UI sync/redeploy controls to update the local sandbox/worktree.
- Reserve manual VM file pushing for true Symphony runtime/orchestration maintenance under `.symphony/` (or other cases where the user explicitly wants direct VM intervention).

**Syncing the VM checkout** (required after pushing git-tracked changes):
```bash
incus exec symphony-main -- sudo --login --user ctabone bash -lc \
  'cd /home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation && git pull origin main'
```
After syncing, restart Symphony so the new process picks up the updated source root:
```bash
incus exec symphony-main -- sudo --login --user ctabone bash -lc \
  'cd /home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation && pkill -f "./bin/symphony" || true; sleep 2; nohup ./.symphony/run.sh --port 4000 > .symphony/log/manual-restart.out 2>&1 < /dev/null & disown'
```

**Important restart ownership quirk**:
- The running `./bin/symphony` process in the VM is sometimes owned by a different user/session than the `ctabone` login shell used above.
- Symptom: `pkill -f "./bin/symphony"` from `sudo --login --user ctabone` fails with `Operation not permitted`, or the command returns no useful output and the old process keeps holding port `4000`.
- If that happens, stop the old process from the VM root shell first, then start Symphony again as `ctabone`:
```bash
incus exec symphony-main -- bash -lc 'pkill -f "./bin/symphony" || true; sleep 2; pgrep -af "[b]in/symphony" || true'
incus exec symphony-main -- sudo --login --user ctabone bash -lc \
  'cd /home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation && nohup ./.symphony/run.sh --port 4000 > .symphony/log/manual-restart.out 2>&1 < /dev/null & disown'
```
- Verify both the VM listener and the host-side proxy after restart:
```bash
incus exec symphony-main -- bash -lc 'ss -ltnp | grep :4000 || true'
curl -i -sS -m 5 http://127.0.0.1:4000/ | head -n 5
```
- If the host check still gives `Empty reply from server`, wait a few seconds and retry before assuming the restart failed.

**When adding a new Symphony lane helper**:
1. Create the script in `scripts/utilities/symphony_<name>.sh` (git-tracked).
2. Add an `ensure_one` line for it in `scripts/utilities/symphony_ensure_workspace_runtime.sh`.
3. Commit and push both files.
4. **Sync the VM checkout**: `git pull origin main` inside the VM (see command above).
5. **Restart Symphony** inside the VM so the running process uses the updated source root.
6. If the script is referenced in `WORKFLOW.md`, update `.symphony/WORKFLOW.md` and push it to the VM via `incus file push`.
7. If the change also touched local Symphony runtime code under `.symphony/elixir/`, push those changed files into the VM source tree too, then rebuild/restart Symphony there.
8. Existing workspaces pick up the new script on their next `before_run` hook execution.

## 6) Expected Change Workflow

1. Sync branch and inspect changed scope.
2. Reproduce or define the expected behavior first.
3. Make minimal, scoped code/config edits. Default to forward-only changes: remove fallbacks, compatibility paths, and legacy branches instead of extending them; use explicit migrations when persistence changes are needed.
4. Run targeted validation + tests.
5. Update docs if behavior or process changed.
6. For Symphony/Linear execution, keep a single workpad-style progress trail and clear acceptance criteria.
