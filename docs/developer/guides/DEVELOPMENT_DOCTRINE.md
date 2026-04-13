# Development Doctrine

This repository defaults to forward-only development.

The bias is simple:

- prefer clean replacement over compatibility layers;
- prefer one canonical path over old-and-new behavior living side by side;
- prefer explicit migrations over runtime fallbacks;
- prefer removing obsolete code while touching an area instead of extending it.

## Default Rules

1. Do not add backward-compatibility shims unless a human explicitly asks for one in the current task.
2. Do not preserve legacy API shapes, config aliases, dual reads/writes, deprecated wrappers, or temporary adapters "just in case."
3. When changing a schema, contract, or data model, update the canonical implementation and all known consumers in the same change.
4. When persistence changes are required, use Alembic or another explicit migration path rather than runtime compatibility logic.
5. If a touched area already contains fallback or legacy handling, prefer removing or consolidating it rather than adding another layer.
6. If the safe path is unclear, escalate the risk explicitly instead of silently adding compatibility code.

## What This Means In Practice

Prefer:

- renaming the field everywhere and migrating stored data;
- deleting the deprecated branch once callers are updated;
- updating fixtures, tests, docs, and runtime config in the same change;
- failing fast on invalid or obsolete inputs when the new contract is intentional.

Avoid:

- `old_value or new_value` style compatibility reads when both shapes are under our control;
- writing both old and new fields during a transition unless the task explicitly requires a staged rollout;
- "temporary" fallback branches with no clear removal plan;
- keeping old endpoint shapes, env var aliases, or schema wrappers alive by default.

## Source-Of-Truth Layers

- `.symphony/WORKFLOW.md` is the authoritative contract for unattended Symphony runs.
- `AGENTS.md` is the fast startup map and should summarize this doctrine briefly.
- This document is the fuller human-readable explanation of the policy and examples.

## VM / Symphony Note

Because Symphony runs inside the `symphony-main` Incus VM (in the Incus
project named by `SYMPHONY_INCUS_PROJECT`, default `default`):

- changes to `.symphony/WORKFLOW.md` must be pushed into the VM source tree and picked up by the running Symphony process;
- tracked repo docs such as `AGENTS.md` and this file only affect new workspaces after they are committed, pushed, and available from the source branch Symphony clones.
