# Harness Health

This page defines recurring hygiene checks that keep the repository agent-legible over time.

## Weekly Routine

Run:

```bash
./scripts/maintenance/harness_hygiene.sh
```

This produces:

- `file_outputs/harness_hygiene/<timestamp>/summary.md`
- `file_outputs/harness_hygiene/latest.md`

## What the Hygiene Check Verifies

1. Unit ignore-path list still points at real files.
2. Contract-core path list still points at real files.
3. Required source-of-truth docs exist.
4. Markdown links in `README.md` and `docs/**/*.md` resolve.
5. Stale Symphony workspaces are surfaced.

## Monthly Review

1. Review weekly reports for repeated failures.
2. Remove or archive stale workspaces under `~/.symphony/workspaces/agr_ai_curation`.
3. Reconcile any persistent docs drift before starting new autonomous ticket batches.

