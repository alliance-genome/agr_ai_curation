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
- Frontend issue-workspace validation:
  - Install dependencies: `cd frontend && npm ci`
  - Frontend tests: `cd frontend && npm run test -- --run`
  - Actionable frontend type-check guard: `cd frontend && npm run type-check`
  - Full known-debt TypeScript baseline: `cd frontend && npm run type-check:all`
  - Interpret `type-check:all` failures as baseline debt unless diagnostics point at files changed by the current ticket.
- Symphony issue workspaces and isolated backend test runs:
  - Backend unit tests: `docker compose -f docker-compose.test.yml run --rm backend-unit-tests`
  - Backend contract tests: `docker compose -f docker-compose.test.yml run --rm backend-contract-tests`
  - Specific backend test file: `docker compose -f docker-compose.test.yml run --rm backend-unit-tests bash -lc "python -m pytest tests/unit/path/to/test.py -v --tb=short"`
  - Syntax-only validation: `python3 -m py_compile backend/src/path/to/file.py`
- LLM provider smoke (local evidence JSON):
  - `./scripts/testing/llm_provider_smoke_local.sh`
- Agent PR gate (local):
  - `./scripts/testing/agent_pr_gate.sh`

## 3.5) GitHub Access In Interactive Codex Sessions

- In Symphony/Incus VM shells, bare `gh auth status` may report "not logged in" unless the repo PAT has already been exported into the current shell environment.
- Preferred interactive entry points in the Symphony VM shell:
  - `co` for the current directory
  - `comain` for `~/.symphony/sandboxes/agr_ai_curation/main`
- Those shortcuts are installed from `scripts/utilities/symphony_vm_shell_shortcuts.sh` via `scripts/utilities/symphony_install_vm_shell_shortcuts.sh`.
- The shortcuts call `scripts/utilities/codex_with_repo_pat.sh`, which loads the repo-scoped PAT from `.symphony/github_pat_env.sh` before starting Codex so plain `gh ...` commands inside the interactive session inherit `GH_TOKEN`/`GITHUB_TOKEN`.
- If you are already inside Codex and need a manual GitHub CLI command, use `./.symphony/with_github_pat.sh gh ...`.
- Never run `gh auth login` in this VM/workspace and never copy a personal `~/.config/gh` into the VM.
- Before concluding that GitHub Actions logs or PR metadata are unavailable, try:
  - `./.symphony/with_github_pat.sh gh auth status`
  - `./.symphony/with_github_pat.sh gh pr checks <number>`

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

Symphony runs inside an Incus VM (`symphony-main`) in the Incus project named by `SYMPHONY_INCUS_PROJECT`. Repo helpers still fall back to `default` if the variable is unset, but on Chris's workstation the live host and VM login profiles now export `SYMPHONY_INCUS_PROJECT=user-1000`. There are TWO categories of files with different deployment paths:

- For full `symphony-main` rebuilds, the tracked source of truth is now:
  - `scripts/utilities/symphony_print_incus_vm_cloud_init.sh`
  - `scripts/utilities/symphony_rebuild_incus_vm.sh`
  - `docs/developer/guides/SYMPHONY_INCUS_VM_REBUILD.md`
- That rebuild path installs pinned `gitleaks` and `trufflehog` during VM creation so interactive SSH work does not have to wait for repo bootstrap before secret-scanning hooks become real.
- Host-side Incus helpers in this repo honor `SYMPHONY_INCUS_PROJECT`. On Chris's workstation, bare `incus` now uses a restricted localhost TLS remote pinned to the confined `user-1000` project, so login shells should already have `SYMPHONY_INCUS_PROJECT=user-1000`. If a shell does not source profiles, export it explicitly before manual `incus ...` commands.

1. **Git-tracked scripts** (`scripts/utilities/symphony_*.sh`):
   - Committed to the repo and pushed to `origin/main`.
   - Workspaces should get them from Git in the workspace checkout. `scripts/utilities/symphony_ensure_workspace_runtime.sh` now verifies tracked repo files are present in the workspace instead of refreshing them from `SYMPHONY_LOCAL_SOURCE_ROOT`.
   - **CRITICAL**: If you add a new tracked helper that existing issue workspaces do not have yet, update those workspaces from Git (for example by recreating or rebasing them). Do not rely on runtime overlay refresh to push tracked files into workspace repo paths.
   - Syncing the VM's local source checkout is still useful for VM-local maintenance and debugging, but it is no longer the source of truth for tracked workspace files.

2. **Gitignored orchestration/runtime files** (`.symphony/WORKFLOW.md`, `.symphony/*.sh`, `.symphony/elixir/**`):
   - `.symphony/` is in `.gitignore` — these files are NOT committed to the repo.
   - Deployed by copying directly into the VM's local source root with `incus file push`.
   - Important: this category is not limited to `WORKFLOW.md`. If you changed local Symphony runtime code under `.symphony/elixir/`, push those files into the VM too.
   - Example:
     `incus --project "${SYMPHONY_INCUS_PROJECT:-default}" file push .symphony/WORKFLOW.md symphony-main/<repo-path>/.symphony/WORKFLOW.md`
   - Example for runtime code:
     `incus --project "${SYMPHONY_INCUS_PROJECT:-default}" file push .symphony/elixir/lib/symphony_elixir/config.ex symphony-main/<repo-path>/.symphony/elixir/lib/symphony_elixir/config.ex`
   - The `ensure_workspace_runtime.sh` hook now copies only runtime overlay files (workflow, PAT helpers, Git hooks) from the local source root into per-issue workspaces. Tracked repo files must come from Git in the workspace checkout. Elixir runtime changes are picked up after rebuild/restart in the VM source tree.

### Local sandbox sync rule

- For normal application code under `backend/`, `frontend/`, `config/`, or other tracked repo files, do **not** manually copy files into `~/.symphony/sandboxes/...`, `incus file push` app code into the VM, or hot-patch the running sandbox containers unless the user explicitly asks for that.
- Preferred path for normal app changes: commit to the repo, push to Git, then use the Symphony UI sync/redeploy controls to update the local sandbox/worktree.
- Reserve manual VM file pushing for true Symphony runtime/orchestration maintenance under `.symphony/` (or other cases where the user explicitly wants direct VM intervention).

**Syncing the VM checkout** (required after pushing git-tracked changes):
```bash
incus --project "${SYMPHONY_INCUS_PROJECT:-default}" exec symphony-main -- sudo --login --user ctabone bash -lc \
  'cd /home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation && git pull origin main'
```
After syncing, restart Symphony so the new process picks up the updated source root:
```bash
incus --project "${SYMPHONY_INCUS_PROJECT:-default}" exec symphony-main -- sudo --login --user ctabone bash -lc \
  'cd /home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation && pkill -f "./bin/symphony" || true; sleep 2; nohup ./.symphony/run.sh --port 4000 > .symphony/log/manual-restart.out 2>&1 < /dev/null & disown'
```

**Important launcher rule**:
- `./.symphony/run.sh` is now a singleton per repo and port. A second `./.symphony/run.sh --port 4000` exits cleanly instead of starting another orchestrator.
- Keep one launcher authority. Do not run the manual `nohup` path at the same time as a VM `systemd` `symphony.service` for the same repo and port.
- If port `4000` is already in use, the wrapper now refuses to launch and prints the existing listener so duplicate restarts fail safe instead of dispatching another token-burning run.

**Important restart ownership quirk**:
- The running `./bin/symphony` process in the VM is sometimes owned by a different user/session than the `ctabone` login shell used above.
- Symptom: `pkill -f "./bin/symphony"` from `sudo --login --user ctabone` fails with `Operation not permitted`, or the command returns no useful output and the old process keeps holding port `4000`.
- If that happens, stop the old process from the VM root shell first, then start Symphony again as `ctabone`:
```bash
incus --project "${SYMPHONY_INCUS_PROJECT:-default}" exec symphony-main -- bash -lc 'pkill -f "./bin/symphony" || true; sleep 2; pgrep -af "[b]in/symphony" || true'
incus --project "${SYMPHONY_INCUS_PROJECT:-default}" exec symphony-main -- sudo --login --user ctabone bash -lc \
  'cd /home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation && nohup ./.symphony/run.sh --port 4000 > .symphony/log/manual-restart.out 2>&1 < /dev/null & disown'
```
- Verify both the VM listener and the host-side proxy after restart:
```bash
incus --project "${SYMPHONY_INCUS_PROJECT:-default}" exec symphony-main -- bash -lc 'ss -ltnp | grep :4000 || true'
curl -i -sS -m 5 http://127.0.0.1:4000/ | head -n 5
```
- If the host check still gives `Empty reply from server`, wait a few seconds and retry before assuming the restart failed.

**When adding a new Symphony lane helper**:
1. Create the script in `scripts/utilities/symphony_<name>.sh` (git-tracked).
2. Commit and push it to `origin/main`.
3. Make sure any existing issue workspaces that need the new tracked helper are updated from Git (for example by recreating or rebasing them).
4. If the script is referenced in `WORKFLOW.md`, update `.symphony/WORKFLOW.md` and push it to the VM via `incus file push`.
5. If the change also touched local Symphony runtime code under `.symphony/elixir/`, push those changed files into the VM source tree too, then rebuild/restart Symphony there.
6. Existing workspaces will pick up runtime overlay changes on the next `before_run` hook execution, but tracked helpers only arrive through Git.

## 6) Expected Change Workflow

1. Sync branch and inspect changed scope.
2. Reproduce or define the expected behavior first.
3. Make minimal, scoped code/config edits. Default to forward-only changes: remove fallbacks, compatibility paths, and legacy branches instead of extending them; use explicit migrations when persistence changes are needed.
4. Run targeted validation + tests.
5. Update docs if behavior or process changed.
6. For Symphony/Linear execution, keep a single workpad-style progress trail and clear acceptance criteria.
