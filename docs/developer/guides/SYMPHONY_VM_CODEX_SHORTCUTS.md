# Symphony VM Codex Shortcuts

This guide documents the interactive Codex shortcuts used inside the Symphony
Incus VM and explains how they persist across VM rebuilds.

## Why This Exists

Interactive Codex sessions in the VM need two repo-specific behaviors:

1. `gh` should inherit the repo-scoped PAT from `~/.agr_ai_curation/pats/symphony.token`.
2. the default `co` shortcut should remain easy to use for manual work in the
   main sandbox and other repo checkouts.

Running `codex` directly from an SSH shell does **not** automatically inherit
the GitHub PAT that Symphony loads for the orchestrator process. The shortcuts
installed here bridge that gap for manual sessions.

## Files

- Tracked shell helpers:
  - `scripts/utilities/codex_with_repo_pat.sh`
  - `scripts/utilities/symphony_vm_shell_shortcuts.sh`
  - `scripts/utilities/symphony_install_vm_shell_shortcuts.sh`
- VM runtime bootstrap hook:
  - `.symphony/run.sh`
- User shell file updated by the installer:
  - `~/.bash_aliases`
- Optional user-local override file:
  - `~/.agr_ai_curation/shell/codex_shortcuts.env`

## Persistence Model

The shell shortcuts persist through VM rebuilds in two layers:

1. `scripts/utilities/symphony_install_vm_shell_shortcuts.sh` writes a managed
   block into `~/.bash_aliases` that sources the tracked repo helper.
2. `.symphony/run.sh` re-runs that installer on Symphony startup, so a rebuilt
   VM re-seeds the shell shortcuts the next time Symphony is started from the
   local source root.

This means the durable source of truth is the tracked repo utility, while the
home-directory dotfile is just a thin bootstrap shim.

Git safety scanners still have repo/bootstrap fallback coverage:

1. `scripts/utilities/symphony_ensure_git_safety_tools.sh` ensures `gitleaks`
   and `trufflehog` exist in `~/.local/bin`.
2. `.symphony/run.sh` invokes that installer on Symphony startup.
3. `scripts/utilities/symphony_main_sandbox.sh` also invokes it before
   preparing or repairing the main sandbox.

Fresh VM rebuilds now have an earlier source of truth too:

1. `scripts/utilities/symphony_print_incus_vm_cloud_init.sh` generates the
   tracked Incus cloud-init payload.
2. `scripts/utilities/symphony_rebuild_incus_vm.sh` uses that payload to
   install pinned `gitleaks` and `trufflehog` into `/usr/local/bin` during VM
   creation.

That keeps the existing repo `pre-commit` hook's secret scanning active for the
source checkout, the main sandbox worktree, and issue workspaces that inherit
the same git hooks.

## Installed Shortcuts

- `co`
  - Launch Codex with the repo PAT loaded, using the current directory as the
    working directory.
- `CO`
  - Same as `co`.
- `comain`
  - Launch Codex against `~/.symphony/sandboxes/agr_ai_curation/main`.
- `COMAIN`
  - Same as `comain`.
- `cor`
  - Run `codex resume`.
- `COR`
  - Same as `cor`.
- `codex-high`
  - Interactive Codex with `high` reasoning effort.
- `codex-xhigh`
  - Interactive Codex with `xhigh` reasoning effort.

By default the shortcuts use:

- model: `gpt-5.4`
- `--yolo`
- reasoning: `high` or `xhigh` depending on the shortcut

## Updating The Default Model Later

There are two supported update paths.

### Option 1: Per-user override without editing the repo

Create `~/.agr_ai_curation/shell/codex_shortcuts.env` with entries like:

```bash
SYMPHONY_CODEX_DEFAULT_MODEL=gpt-5.5
SYMPHONY_CODEX_DEFAULT_REASONING_XHIGH=high
SYMPHONY_CODEX_USE_NO_ALT_SCREEN=1
```

Open a new shell after editing the file.

### Option 2: Change the tracked defaults for everyone

Edit the defaults near the top of:

`scripts/utilities/symphony_vm_shell_shortcuts.sh`

Then re-run:

```bash
./scripts/utilities/symphony_install_vm_shell_shortcuts.sh
```

If the change also needs to affect the running Symphony VM runtime, update the
VM source checkout and restart Symphony so `.symphony/run.sh` picks up the new
bootstrap behavior.

## Git Safety Scanners

The repo `pre-commit` hook already knows how to run `gitleaks` and
`trufflehog`, but it can only do real secret scanning when those binaries are
installed. The tracked bootstrap now ensures they are installed during Symphony
startup and during main sandbox setup.

Manual checks:

```bash
./scripts/utilities/symphony_ensure_git_safety_tools.sh --check
gitleaks version
trufflehog --version
```

## Manual Repair

If the shortcuts disappear or `co` starts behaving like plain `codex`, run:

```bash
cd /home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation
./scripts/utilities/symphony_install_vm_shell_shortcuts.sh
```

Then open a fresh shell or run:

```bash
source ~/.bash_aliases
```

## Sanity Checks

From a fresh VM shell:

```bash
type co
type comain
```

From an interactive Codex session launched with `co`, `gh auth status` should
show a `GH_TOKEN`-backed login for the repo-scoped PAT instead of asking for
`gh auth login`.
