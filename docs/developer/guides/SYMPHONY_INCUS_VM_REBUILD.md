# Symphony Incus VM Rebuild

This guide is the tracked source of truth for rebuilding the `symphony-main`
Incus VM without losing the baseline developer tooling that manual SSH sessions
expect.

## What Changed

Fresh VM creation now has a tracked cloud-init source:

- `scripts/utilities/symphony_print_incus_vm_cloud_init.sh`
- `scripts/utilities/symphony_rebuild_incus_vm.sh`
- `scripts/utilities/symphony_git_safety_tool_versions.sh`

That source installs pinned versions of `gitleaks` and `trufflehog` during VM
creation, before the repo checkout or Symphony startup happens.

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
  --ssh-key-file ~/.ssh/id_ed25519.pub \
  --dry-run
```

Rebuild the VM shell:

```bash
./scripts/utilities/symphony_rebuild_incus_vm.sh \
  --ssh-key-file ~/.ssh/id_ed25519.pub \
  --replace
```

What this does:

- recreates the `symphony-main` Incus VM
- applies tracked cloud-init for the `ctabone` user
- installs pinned `gitleaks` and `trufflehog` into `/usr/local/bin`
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
```

If the repo checkout is already present, you can also confirm the
source-checkout installer still agrees:

```bash
./scripts/utilities/symphony_ensure_git_safety_tools.sh --check
```

## Updating The Default Scanner Versions

The pinned versions and checksums live in:

`scripts/utilities/symphony_git_safety_tool_versions.sh`

When you intentionally update those pins:

1. change the version/checksum values in that file
2. rerun the local tests
3. sync the VM source checkout if you need the running runtime bootstrap to use
   the new pins immediately

## Notes

- The rebuild helper does not store secrets in the repo.
- The cloud-init helper reads a public SSH key file at runtime; it does not
  embed a private key anywhere in the repo.
- The current live VM was originally created with inline Incus cloud-init that
  only handled user creation and SSH access. This tracked helper replaces that
  one-off configuration as the documented baseline going forward.
