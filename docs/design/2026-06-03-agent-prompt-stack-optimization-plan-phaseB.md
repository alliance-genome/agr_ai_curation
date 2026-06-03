# Agent Prompt-Stack Optimization — Phase B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove cross-layer duplication from the curator-facing base prompts by relocating the load-bearing document-search guidance into the search/read tool descriptions (where OpenAI's guidance says tool-usage detail belongs), then deleting the now-redundant `<search_infrastructure>` block and the `Available tools:` re-listing from the base prompts that carry them.

**Architecture:** Phase A slimmed the backend-generated contract; Phase B touches `packages/` — the shared tool descriptions and a small number of base prompts. The relocation must be ordered: enrich the tool descriptions FIRST (and prove the enriched text reaches the model-facing tool schema), THEN remove the duplicated block from the prompts. Each removal is justified by a per-agent redundancy map (removed line → surviving home).

**Tech Stack:** Python 3.11, YAML, pytest. Tests run in `ai-curation-unit-tests:latest` (the one-off `docker run` pattern used in Phase A; no DB needed for prompt/tool-doc assembly). Lightweight run command (reused throughout):
```bash
docker run --rm -v "$(pwd)/backend:/app/backend" -v "$(pwd)/packages:/app/packages:ro" -v "$(pwd)/config:/app/config:ro" -v "$(pwd)/alliance_agents:/app/alliance_agents" -v "$(pwd)/docs:/app/docs:ro" -v "$(pwd)/frontend:/app/frontend:ro" -w /app/backend -e OPENAI_API_KEY=test -e PYTHONUNBUFFERED=1 -e EMBEDDING_MODEL=text-embedding-3-small -e EMBEDDING_MODEL_TOKEN_LIMIT=8191 -e EMBEDDING_TOKEN_SAFETY_MARGIN=500 ai-curation-unit-tests:latest python -m pytest <paths> -q
```

---

## Scope (refined from the spec after exploration)

Exploration of all agent base prompts found the *mechanical, provably-redundant* cross-layer content is concentrated:
- `<search_infrastructure>` block: **only `gene_expression` and `pdf`** (`prompt.yaml`). The same ~2K guidance duplicated across two base prompts — consolidate into the shared search/read tool descriptions (removes the cross-agent duplication and follows the "tool guidance lives in tool descriptions" principle).
- `Available tools:` re-listing: **only `gene_expression`** — pure duplication of the always-sent tool schemas + the kept required-tool-call line; delete.
- **Evidence-policy span-id mechanics** appear in all six extractors, but they are interwoven with genuine curation guidance (what counts as strong/weak evidence, examples, record/attach workflow), not a clean restatement to mechanically excise. **Their de-duplication is folded into Phase C** (the holistic per-agent rewrite, where the semantic-coverage checklist governs), NOT Phase B.

So Phase B is intentionally small and low-risk: enrich 4 tool descriptions, de-dup 2 base prompts. Other agents have no Phase-B-removable block (documented in the Task 4 artifact); their cleanup happens in Phase C.

**Per the spec's "all agents" intent:** Phase B still *scans* every agent (Task 4) and records the per-agent finding; "no Phase-B redundancy — handled in Phase C" is a valid, recorded outcome.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| (model-facing tool-description source — to be confirmed in Task 1; candidates: `packages/alliance/tools/bindings.yaml` `description:` for the 4 document tools, and/or `packages/alliance/python/src/agr_ai_curation_alliance/tools/documents.py` factories) | search/read tool descriptions | Enrich with search guidance |
| `packages/alliance/agents/gene_expression/prompt.yaml` | gene_expression base prompt | Remove `<search_infrastructure>` + `Available tools:` blocks |
| `packages/alliance/agents/pdf/prompt.yaml` | pdf base prompt | Remove `<search_infrastructure>` block |
| `backend/tests/unit/...` (tool-doc + prompt redundancy guards) | tests | New assertion that search guidance is in the tool schema; re-baseline tool-doc completeness if needed |
| `docs/design/2026-06-03-prompt-size-report-phaseB.md` | per-agent redundancy map + before/after sizes | Create (Task 4) |

---

## Task 1: Locate the model-facing tool-description source, then enrich the search/read tools

**Goal:** the search guidance must reach the MODEL (the tool schema description in the tools array), not just curator docs. First determine where that text comes from; then enrich it; then prove it lands in the assembled tool schema.

- [ ] **Step 1: Trace the source of truth (investigation; record the answer in the commit message)**

Read these and determine what string becomes the model-facing description for `search_document` / `read_section` / `read_subsection` / `read_chunk`:
- `packages/alliance/python/src/agr_ai_curation_alliance/tools/documents.py` (`create_search_document_tool` @ line 34, `create_read_section_tool` @ line 50, and the read_subsection/read_chunk factories) — does the `@function_tool`/`FunctionTool` get its description from a docstring, a `description=`/`description_override=` arg, or from injected metadata?
- `packages/alliance/tools/bindings.yaml` lines 1172-1211 — the `description:` field for each document tool.
- `backend/src/lib/openai_agents/streaming_tools.py:1320` (`description_override=getattr(existing_tool, "description", "")`) and `backend/src/lib/agent_studio/catalog_service.py` tool-resolution — does the runtime set the SDK tool's description from the bindings.yaml `description`?

**Decision rule:** edit whichever source actually populates the SDK tool's `.description` sent to the model. If it is the bindings.yaml `description:` field, enrich that. If it is the Python factory docstring/`description_override`, enrich that and (for curator parity) mirror a `documentation.summary` in bindings.yaml. If unclear after reading, STOP and report findings rather than guessing.

- [ ] **Step 2: Write the failing test that the enriched guidance reaches the assembled tool schema**

Add `backend/tests/unit/lib/prompts/test_search_tool_guidance_present.py`. The test must build the actual tools the runtime sends for a document agent and assert the search guidance is in their descriptions. Use the same resolution path the runtime uses (resolve via the catalog/tool registry for, e.g., `gene_expression`). Assert the model-facing description for the search/read tools contains the load-bearing phrases that are being relocated, e.g.:
- `search_document` description contains `lexical` AND `section_keywords` (mode + scoping guidance).
- `read_section`/`read_subsection` description conveys "returns all chunks of the named section" (full-coverage survey, not page order).
- `read_chunk` description conveys `evidence_spans` / `span_id` selection.

(Use the exact phrasing you settle on in Step 3; the point is non-vacuous assertions on the real assembled descriptions. If the resolution path needs the DB, fall back to asserting on the source-of-truth field identified in Step 1 — bindings.yaml `description` or the factory's description string — and note it.)

Run it; expect FAIL (guidance not there yet).

- [ ] **Step 3: Enrich the four document-tool descriptions** with the load-bearing content from gene_expression's `<search_infrastructure>` block (`packages/alliance/agents/gene_expression/prompt.yaml`, the `<search_infrastructure>` section). Map the content to tools:
  - **search_document:** default `auto`/hybrid (semantic + BM25) bridges paraphrases; pass `search_mode="lexical"` for exact gene symbols/IDs/strains/alleles/probes/genotypes/PMIDs/DOIs; `search_mode="hybrid_lexical_first"` to retry lexical-heavy; results reranked by a cross-encoder then diversified via MMR; short queries (<=3 tokens) auto-boost lexical; pass `section_keywords` to scope to sections before search; returns up to ~1500 chars per chunk preview.
  - **read_section / read_subsection:** return ALL chunks of a named section/subsection using the LLM-resolved semantic hierarchy (not linear page order), with full chunk text; use when you need complete coverage (search may miss low-scoring but relevant passages); figure legends are a rich source.
  - **read_chunk:** returns full chunk text plus `evidence_spans[].span_id` values to pass to `record_evidence` for final evidence selection.

  Keep each description focused and crisp (1–4 sentences per tool, per OpenAI's "crisp tool descriptions" guidance — do NOT paste the whole 2K block verbatim into one tool). Do not introduce new behavior; only relocate existing guidance.

- [ ] **Step 4: Run the test — expect PASS.** Also run the existing tool-doc guards and re-baseline only if a length/exact-string assertion legitimately changed:
  Run: tool-doc completeness/parity tests (`tests/unit/api/test_tool_documentation_completeness.py`, `tests/unit/api/test_tool_catalog_parity.py`) + the new test. Expect PASS (parity baseline may need regeneration if the description text is snapshotted — if so, regenerate per its documented procedure and review the diff).

- [ ] **Step 5: Commit**
```bash
git add <enriched source file(s)> backend/tests/unit/lib/prompts/test_search_tool_guidance_present.py <any re-baselined tool-doc test/fixture>
git commit -m "feat(tools): carry document-search guidance in the search/read tool descriptions (prereq for prompt de-dup)"
```

---

## Task 2: De-dup the gene_expression base prompt

**Files:** Modify `packages/alliance/agents/gene_expression/prompt.yaml`.

- [ ] **Step 1: Build the per-agent redundancy map (write it in the commit message + Task 4 artifact)**

For gene_expression, map each block being removed to its surviving home:
- `<search_infrastructure>` block → the enriched search/read tool descriptions (Task 1).
- `Available tools:` list (inside `<tools_and_retrieval>`) → the always-sent tool schemas + the kept "Required tool-call policy" line in `core_generated`.

Confirm every distinct fact in those two blocks is preserved in its new home BEFORE deleting (this is the spec's semantic-coverage requirement for the de-dup).

- [ ] **Step 2: Remove the `<search_infrastructure>` block** (the entire `<search_infrastructure> ... </search_infrastructure>` section) from `gene_expression/prompt.yaml`.

- [ ] **Step 3: Remove the `Available tools:` re-listing** within `<tools_and_retrieval>` (the bulleted tool list) — keep the surrounding retrieval-strategy guidance that is NOT a bare tool list (e.g., the preferred-order workflow, retrieval budget) unless that guidance is itself pure tool-schema duplication. When in doubt, keep curation guidance and remove only the bare "tool: what it does" enumeration; note any judgment calls.

- [ ] **Step 4: Run the prompt/contract surface + size report — expect PASS and a smaller prompt**

Run: `tests/unit/lib/prompts/ tests/unit/test_gene_expression_prompt_policy.py tests/unit/api/test_agent_studio_domain_envelope_prompt_policy.py`. Expect PASS. If `test_gene_expression_prompt_policy.py` asserts a phrase from a removed block, verify the phrase now lives in the tool description and re-baseline that assertion to its new location (do NOT weaken it); if it asserts surviving curation guidance, it should still pass untouched.
Then capture the new base-prompt size for gene_expression (`wc -c packages/alliance/agents/gene_expression/prompt.yaml`).

- [ ] **Step 5: Commit**
```bash
git add packages/alliance/agents/gene_expression/prompt.yaml <any re-baselined test>
git commit -m "refactor(prompts): drop search-infrastructure + tool re-listing from gene_expression base prompt (now in tool descriptions/schemas)"
```

---

## Task 3: De-dup the pdf base prompt

**Files:** Modify `packages/alliance/agents/pdf/prompt.yaml`.

- [ ] **Step 1: Redundancy map** — `<search_infrastructure>` block → enriched search/read tool descriptions (Task 1). Confirm every fact in pdf's block is covered by the enriched tool descriptions; if pdf's block contains a fact NOT in gene_expression's block (and thus possibly not yet relocated), add that fact to the relevant tool description in the same way as Task 1 before removing it here.

- [ ] **Step 2: Remove the `<search_infrastructure>` block** from `pdf/prompt.yaml`.

- [ ] **Step 3: Run + size**

Run: `tests/unit/lib/prompts/` + any `pdf`-specific prompt test (search `backend/tests/unit` for `pdf` prompt/contract tests; run them). Expect PASS (re-baseline only assertions on relocated text). Capture new `wc -c` for `pdf/prompt.yaml`.

- [ ] **Step 4: Commit**
```bash
git add packages/alliance/agents/pdf/prompt.yaml <any re-baselined test>
git commit -m "refactor(prompts): drop duplicated search-infrastructure block from pdf base prompt (now in tool descriptions)"
```

---

## Task 4: Per-agent redundancy-map artifact + before/after sizes

**Files:** Create `docs/design/2026-06-03-prompt-size-report-phaseB.md`.

- [ ] **Step 1: Scan every agent and record the finding.** For each agent under `packages/alliance/agents/*/`, grep its `prompt.yaml` for Phase-B-removable cross-layer blocks (`<search_infrastructure>`, `Available tools:`, and any verbatim restatement of the `core_generated` lines). Record per-agent: either the redundancy map (removed → surviving home) for gene_expression/pdf, or "no Phase-B-removable redundancy; base-prompt cleanup handled in Phase C."

- [ ] **Step 2: Before/after base-prompt sizes** for the two de-dup'd agents (gene_expression, pdf) — chars before (gene_expression 31,675; pdf 9,707, from the spec's measurement) vs after (`wc -c`), with delta. Note the search guidance moved into the tool descriptions (net token effect: removes the cross-agent duplication; the consolidated guidance is sent once via the shared tool schemas).

- [ ] **Step 3: Commit**
```bash
git add docs/design/2026-06-03-prompt-size-report-phaseB.md
git commit -m "docs(design): Phase B per-agent redundancy map + before/after base-prompt sizes"
```

---

## Phase B gate (after all tasks)

1. Dispatch the final **Opus 4.8** review of the Phase B diff (vs the Phase A tip), tasked to confirm: every removed line has a verified surviving home (no curation guidance lost); the relocated search guidance actually reaches the model-facing tool schema; tool descriptions stayed crisp (not bloated).
2. Run **`/external-llm-code-review`** (Codex, gpt-5.5/high) on the Phase B diff with the same task; show Chris the output verbatim.
3. Address findings, then proceed to the Phase C plan.

---

## Self-Review

**Spec coverage (Phase B portion):** relocate search guidance to tool descriptions before removal — Task 1 (with the prerequisite ordering + a test that it reaches the model); per-agent redundancy maps — Tasks 2/3 maps + Task 4 artifact; remove `<search_infrastructure>` (gene_expression+pdf) and `Available tools:` (gene_expression) — Tasks 2/3; "all agents" scanned with recorded findings — Task 4; evidence-policy de-dup deferred to Phase C with rationale — Scope section. Covered.

**Placeholder scan:** Task 1 contains an explicit investigation Step (locate the description source) rather than a guessed file path — this is deliberate (the source of truth must be confirmed by reading code) and bounded by a decision rule + a STOP-and-report fallback, not a vague "figure it out." All other steps have concrete files, content, and commands. The two reviewers (Codex gpt-5.5/high + Opus 4.8) will validate Task 1's resolution.

**Consistency:** the run command and the "relocate-before-remove" ordering are consistent across Tasks 1→2→3; the redundancy-map requirement is consistent in Tasks 2, 3, and the Task 4 artifact.
