# Core Agent Snapshot

This directory is no longer the package-owned source for the shipped Alliance
agent catalog.

- `agr.core` now provides the foundation package contract and does not export
  these agent bundles.
- The shipped Alliance catalog lives under `packages/alliance/agents/`.
- The repo-local source mirror for that catalog lives under `config/agents/`.

This tree remains in the repository only as a transition aid while adjacent
package-split work lands. If you are maintaining shipped agent bundles from
source, edit `packages/alliance/agents/README.md` and `config/agents/README.md`
instead.
