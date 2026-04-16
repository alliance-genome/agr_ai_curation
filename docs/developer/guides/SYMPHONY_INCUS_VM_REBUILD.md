# Symphony Incus VM Rebuild

This guide is the tracked source of truth for rebuilding the `symphony-main`
Incus VM without losing the baseline developer tooling that manual SSH sessions
expect. Host-side Incus helpers still fall back to the `default` project if
`SYMPHONY_INCUS_PROJECT` is unset. If your environment uses a different Incus
project, export `SYMPHONY_INCUS_PROJECT` or pass `--project` explicitly when
running the helper.

## What Changed

Fresh VM creation now has a tracked cloud-init source:

- `scripts/utilities/symphony_print_incus_vm_cloud_init.sh`
- `scripts/utilities/symphony_rebuild_incus_vm.sh`
- `scripts/utilities/symphony_git_safety_tool_versions.sh`
- `scripts/utilities/symphony_ruff_tool_version.sh`

That source installs pinned versions of `gitleaks`, `trufflehog`, and `ruff`
during VM creation, before the repo checkout or Symphony startup happens.

This matters because:

- interactive SSH work may happen before Symphony is started
- the repo `pre-commit` hook only does real secret scanning when those tools
  already exist on `PATH`
- rebuilding from plain Ubuntu plus ad-hoc shell history is too easy to drift

The repo-level installer at
`scripts/utilities/symphony_ensure_git_safety_tools.sh` remains the tracked
source-checkout and sandbox installer for the source checkout, main sandbox,
and workspaces. The rebuild helper simply moves the default all the way up to
VM creation.

## Standard Rebuild Path

Preview the rebuild first:

```bash
./scripts/utilities/symphony_rebuild_incus_vm.sh \
  --project "${SYMPHONY_INCUS_PROJECT:-default}" \
  --ssh-key-file ~/.ssh/id_ed25519.pub \
  --dry-run
```

Rebuild the VM shell:

```bash
./scripts/utilities/symphony_rebuild_incus_vm.sh \
  --project "${SYMPHONY_INCUS_PROJECT:-default}" \
  --ssh-key-file ~/.ssh/id_ed25519.pub \
  --replace
```

What this does:

- recreates the `symphony-main` Incus VM in the selected Incus project
- applies tracked cloud-init for the configured VM user
- installs pinned `gitleaks`, `trufflehog`, and `ruff` into `/usr/local/bin`
- leaves repo restore, secrets restore, and Symphony restart as explicit
  follow-up steps

## Post-Rebuild Follow-Up

After the VM is back:

1. Restore or clone the repo into the VM.
2. Restore the local `.symphony/` runtime support tree if needed.
3. Run `./.symphony/run.sh --setup-only` inside the VM repo checkout.
4. Restore PAT, Linear, AWS, and app-secret files.
5. Start or restart Symphony.

## Verifying The Baseline

Inside the rebuilt VM:

```bash
gitleaks version
trufflehog --version
ruff --version
```

If the repo checkout is already present, you can also confirm the
source-checkout installer still agrees:

```bash
./scripts/utilities/symphony_ensure_git_safety_tools.sh --check
```

## Updating The Default Tool Versions

The pinned versions and checksums live in:

- `scripts/utilities/symphony_git_safety_tool_versions.sh` (gitleaks, trufflehog)
- `scripts/utilities/symphony_ruff_tool_version.sh` (ruff)

When you intentionally update those pins:

1. change the version/checksum values in the relevant file
2. rerun the local tests
3. sync the VM source checkout if you need the running runtime bootstrap to use
   the new pins immediately

## Notes

- The rebuild helper does not store secrets in the repo.
- The cloud-init helper reads a public SSH key file at runtime; it does not
  embed a private key anywhere in the repo.
- Earlier environments may have been created with more manual Incus cloud-init
  or ad-hoc shell bootstrap. This tracked helper replaces that one-off setup as
  the documented baseline going forward.
