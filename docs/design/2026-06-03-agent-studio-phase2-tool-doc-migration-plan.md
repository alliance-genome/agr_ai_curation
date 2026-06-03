# Agent Studio Phase 2 — Tool Docs to `bindings.yaml` Implementation Plan

Date: 2026-06-03. Status: **PLAN — awaiting Chris's go-ahead.** Branch: `agent-studio-phase1-doc-migration` (continuing per Chris).

> **For agentic workers:** use superpowers:subagent-driven-development (Opus implementer + spec-compliance review + code-quality review per task). Use the **LSP** tool for symbol navigation/reference-finding (combine with grep — LSP `findReferences` misses cross-module attribute access like `catalog_service.CURATED_TOOL_REGISTRY[...]`). Curator-facing prose must be approachable for biologists with no programming background (memory `feedback_curator_friendly_docs`). No usage "tips" (memory `feedback_no_usage_tips_curators_dont_drive_agents`). No `--no-verify`; explicit `git add` paths; Opus subagents; no emojis.

---

## Goal & decisions

Move curator-facing **tool** documentation out of the hardcoded Python `CURATED_TOOL_REGISTRY` + `TOOL_OVERRIDES` in `backend/src/lib/agent_studio/catalog_service.py`, with **`packages/alliance/tools/bindings.yaml` as the single source of truth** (Chris's decision: bindings.yaml, in the domain package — not docstrings, because multi-method tools and REST factory tools can't be docstring-driven; YAML is uniform and already the de-facto source). Then delete the Python dicts. **Drop the 8 `transfer_to_*` handoff stubs** from the tool catalog (Chris's decision: they have no callable, no agent references them, they're supervisor routing artifacts).

This is a **faithful migration first (parity-locked), reviewed voice pass second** — same discipline as Phase 1.

---

## Grounding facts (from 2026-06-03 exploration — verified against code)

- **The real source of tool docs is already `bindings.yaml`**, not the Python dict. `_build_tool_registry()` (`catalog_service.py:1152`) introspects each package tool, merges `binding.metadata` (from `bindings.yaml`) at `:1188-1192`, then merges `CURATED_TOOL_REGISTRY` at `:1194`, then `TOOL_OVERRIDES` at `:1200`. YAML `binding.description` already wins over the docstring (`:1171,1175`: `binding.description or metadata.description`).
- **`CURATED_TOOL_REGISTRY` = 27 entries, ALL single-method** (`methods`/`agent_methods` all `None`). 19 overlap with package tools (augment them); 8 are `transfer_to_*` curated-only stubs (no package binding, no callable).
- **`TOOL_OVERRIDES` = 1 entry**: `search_document` → `category: "Document"` (largely redundant; `_tool_category_for_binding` would infer "Document" anyway from its `document_id`+`user_id` context).
- **Consumers of the dicts (complete set):** `catalog_service.py:1194` (CURATED), `:1200` (OVERRIDES); and `backend/tests/unit/lib/agent_studio/test_catalog_service_tool_bindings.py:130,148,152,155` (access `catalog_service.CURATED_TOOL_REGISTRY[...]` directly — these break on deletion and must be rewritten to assert against catalog output).
- **`_merge_tool_metadata` (`:1121`)** shallow-merges override onto base; `documentation` is merged one level deep (`documentation.update(value)` replaces sub-keys like `summary`/`parameters` wholesale, keeps absent ones); `source_file` + package_* keys are FORCE-preserved from the package base. → Curated `source_file` never wins for package tools; moving a curated field into `binding.metadata` yields the identical final value (binding.metadata merges just before the curated dict).
- **Multi-method tools** (`agr_curation_query` 28 methods, `agr_literature_reference_lookup` 2) carry their method docs ONLY in `bindings.yaml` `metadata.methods`/`agent_methods` — un-introspectable (one dispatching function). **Not in the curated dict; unaffected by deletion.** They stay in YAML (consistent with the chosen source of truth).
- **Coverage audit: NO gaps.** Every tool any `agent.yaml` `tools:` references has a catalog entry; the validation-era builder/grounding tools are all registered and documented. So the design's "tool audit / register new tools" item is already satisfied — nothing to add.
- **Tool API is untyped raw dicts** (`GET /api/agent-studio/tools`, `/tools/{id}` return `{"tools": ...}` / `{"tool": ...}` with no `response_model`). The curator-facing surface is defined by the dict shape + the frontend `ToolInfo` TS type (`frontend/src/types/promptExplorer.ts:421-466`), rendered in `frontend/src/components/AgentStudio/ToolDetailsDialog.tsx`. Rendered fields: `name, toolId, category, parent_tool, agent_context.methods, description, documentation.summary, documentation.parameters[].{name,type,required,description}, example, methods/relevant_methods[].{name,description,required_params,optional_params,example}, source_file`.

---

## Tasks

### Task 1 — Tool-catalog parity baseline (before any change)
- Create `backend/tests/unit/api/test_tool_catalog_parity.py`, mirroring the agent parity test: snapshot `catalog_service.get_all_tools()` (the full tool dict incl. method entries) to a committed fixture `backend/tests/unit/api/fixtures/tool_catalog_baseline.json`. Self-baselining (first run writes; later runs assert).
- This locks the EXACT curator-facing tool surface so the fold-in is provably lossless.
- Commit (baseline + test).

### Task 2 — Inventory what the dict uniquely adds over `bindings.yaml`
- Write a throwaway diff (a test or script) that builds the catalog **with the `CURATED_TOOL_REGISTRY`/`TOOL_OVERRIDES` merge steps bypassed** and compares per-tool output to the real catalog, for the 19 overlap tools. Output the exact fields each curated entry contributes that would otherwise be lost (e.g. `search_document.documentation.parameters.search_mode.{description,enum}`, `record_evidence` span semantics, display `name`, `category`).
- Deliverable: a precise per-tool list of fields to move into `bindings.yaml`. No production code change in this task.

### Task 3 — Fold the unique content into `bindings.yaml` `metadata` (faithful)
- For each of the 19 overlap tools, set the fields identified in Task 2 in that tool's `metadata:` block in `packages/alliance/tools/bindings.yaml` (replacing any thinner value), so the merged catalog output is identical with the curated dict bypassed.
- Run the Task 1 parity test: must stay **GREEN** (output unchanged — content just sourced from YAML now). This proves the fold-in is lossless before the dict is deleted.
- Commit.

### Task 4 — Delete `CURATED_TOOL_REGISTRY` + `TOOL_OVERRIDES`; drop `transfer_to_*`
- Remove both dict definitions (`:187-789`, `:790-794`) and the two merge loops (`:1194-1204`) in `_build_tool_registry`. Use LSP `findReferences` + grep to confirm no other consumers remain.
- Effect: the 19 overlap tools now source solely from `bindings.yaml` (identical, per Task 3); the 8 `transfer_to_*` curated-only entries disappear (intended). Verify nothing else (tests, UI fixtures) depends on `transfer_to_*`.
- Rewrite the 2 tests in `test_catalog_service_tool_bindings.py` that read `CURATED_TOOL_REGISTRY` directly → assert the same facts against `get_tool_details(...)` / the catalog output instead.
- Regenerate the tool parity baseline; the diff must be **exactly**: 8 `transfer_to_*` entries removed, nothing else changed. **Chris reviews this diff** (it's the one intended content change in the mechanical migration).
- Commit.

### Task 5 — Tool-doc completeness guard test
- Create `backend/tests/unit/api/test_tool_documentation_completeness.py`: for the union of every `tools:` entry across all live `agent.yaml` files, assert each resolved tool has a non-empty curator-facing `description` and `documentation.summary` in the catalog (and each declared parameter has a description). Anti-rot mirror of Phase 1's agent guard, so a newly added tool can't ship undocumented.
- Should PASS given current coverage; if a tool fails, author its `bindings.yaml` doc (don't weaken the test).
- Commit.

### Task 6 — Curator-voice pass on tool docs (REVIEWED, like Phase 1 Task 8)
- Triage the now-surfaced tool descriptions/summaries/parameter docs in `bindings.yaml` for curator-friendliness (plain language; no code identifiers/SQL/field paths as the explanation; spell out acronyms). Expect only a subset to need changes (Phase 1 was 12/26).
- **Draft rewrites for Chris's review** before committing; regenerate the parity baseline after approval (intended content change). This is the curator-voice gate — Chris approves wording.
- Commit after approval.

### Task 7 — Frontend check + full verification
- Confirm `ToolInfo`/`ToolMethod`/`ToolParameter` TS types still match the API shape (no field removed that the UI reads); `npx tsc --noEmit` filtered to touched files. No frontend change is expected (API shape unchanged for kept tools).
- Run the agent-studio + config + tool suites green; confirm `grep -rn "CURATED_TOOL_REGISTRY\|TOOL_OVERRIDES" backend/src` is empty.
- Manual UI spot-check (bring the stack up near the end, per Chris): Agent Studio → a tool detail dialog reads curator-friendly; `transfer_to_*` no longer listed; multi-method tools (e.g. `agr_curation_query`) still show their methods.

---

## Notes / risks

- **Parity oracle for tools is new** — Task 1 must land before any content moves, exactly like Phase 1's `883c3326`.
- **The only intended catalog diffs** in the mechanical phase are the 8 `transfer_to_*` removals (Task 4) and the reviewed voice changes (Task 6). Everything else stays byte-identical.
- **Multi-method/REST/factory tools are out of scope to "docstring-ify"** — they stay YAML-sourced by design (the chosen source of truth). No dispatcher refactor.
- **`bindings.yaml` will grow** as curated content folds in; it stays in the domain package (the goal) and remains the one place to edit a tool's curator docs.
- **Baseline fixtures are root-owned** (written by the test container running as root); regenerate inside the container (`rm` + rerun parity test). Run doc tests with `docker compose -f docker-compose.test.yml run --rm --no-deps backend-unit-tests bash -lc "cd /app/backend && python -m pytest <path> -q -p no:cacheprovider"`.
