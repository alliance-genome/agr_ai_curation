# Agent Prompt-Stack Optimization — Phase B Implementation Plan (revised post-#446)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove cross-layer duplication from the curator-facing base prompts by relocating the load-bearing document-search guidance into the search/read tool descriptions (where OpenAI's guidance says tool-usage detail belongs), then deleting the now-redundant `<search_infrastructure>` block and the `Available tools:` re-listing from the base prompts that carry them.

**Architecture (confirmed against post-#446 main by a code-tracing investigation — these are NOT assumptions):**
- The **model-facing** `FunctionTool.description` for `search_document` / `read_chunk` / `read_section` / `read_subsection` comes from the inner `@function_tool` **docstrings in the PACKAGE copy** `packages/alliance/python/src/agr_ai_curation_alliance/tools/weaviate_search.py` (search_document `:150-165`, read_chunk `:242-249`, read_section `:376-389`, read_subsection `:537-552`). The runtime reaches these via bindings.yaml `callable_factory` -> `documents.py` -> package `weaviate_search`; `catalog_service._resolve_package_tool` only swaps `on_invoke_tool` and leaves `.description` (the docstring) untouched. **bindings.yaml `description` does NOT reach the model.**
- The **curator-facing** Agent Studio catalog (`TOOL_REGISTRY[tool_id]['description']` / `['documentation']['summary']`) now reads **bindings.yaml `description` + `metadata.documentation.summary`** (post-#446 `CURATED_TOOL_REGISTRY` is deleted, so bindings.yaml is the live source).
- There are **two `weaviate_search.py` copies**: the package copy (feeds the runtime/model) and `backend/src/lib/openai_agents/tools/weaviate_search.py` (legacy, differs only in import paths + minor wording). `backend/tests/unit/lib/openai_agents/tools/test_tool_descriptions.py` builds tools from and asserts on the **backend** copy via `inspect.getdoc`. So the test currently guards a copy the runtime does not use.
- **Therefore Phase B edits BOTH layers and BOTH copies:** enrich the package docstrings (model) AND the backend copy (so the test guards the runtime text) AND bindings.yaml (curator parity). Relocate-before-remove ordering: enrich tool descriptions first, prove the guidance reaches the model via the docstring/runtime path, then delete the base-prompt blocks.

**Tech Stack:** Python 3.11, YAML, pytest. Tests run in `ai-curation-unit-tests:latest` (the one-off `docker run` pattern from Phase A; no DB needed). Lightweight run command (reused):
```bash
docker run --rm -v "$(pwd)/backend:/app/backend" -v "$(pwd)/packages:/app/packages:ro" -v "$(pwd)/config:/app/config:ro" -v "$(pwd)/alliance_agents:/app/alliance_agents" -v "$(pwd)/docs:/app/docs:ro" -v "$(pwd)/frontend:/app/frontend:ro" -w /app/backend -e OPENAI_API_KEY=test -e PYTHONUNBUFFERED=1 -e EMBEDDING_MODEL=text-embedding-3-small -e EMBEDDING_MODEL_TOKEN_LIMIT=8191 -e EMBEDDING_TOKEN_SAFETY_MARGIN=500 ai-curation-unit-tests:latest python -m pytest <paths> -q
```

---

## Scope (refined after the post-#446 investigation)

The mechanical, provably-redundant cross-layer search guidance is **duplicated across six prompts**, but Phase B cleanly handles only the two `<search_infrastructure>` carriers; the rest is recorded and deferred:
- `<search_infrastructure>` block — **`gene_expression` and `pdf`** (`prompt.yaml`). Consolidate into the shared search/read tool descriptions; **remove in Phase B**.
- `Available tools:` re-listing — **`gene_expression` only**. Pure duplication of the always-sent tool schemas + the kept required-tool-call line; **remove in Phase B**.
- `<search_context>` blocks — **`gene_extractor`, `allele_extractor`, `disease_extractor`, `phenotype_extractor`** carry the SAME search-backend facts (gene_extractor's is a 53-line near-twin of `<search_infrastructure>`; the others are 7-12 lines). These are interwoven with extractor-specific guidance, so they are **recorded in the Task 4 redundancy map and deferred to Phase C** (the holistic per-agent rewrite). Their search-backend facts are already relocated by Task 1, so Phase C can drop them cleanly.
- Evidence-policy span-id mechanics (all six extractors) — interwoven with curation guidance; **deferred to Phase C**.

So Phase B is intentionally small: enrich 4 tool descriptions (2 code copies + bindings.yaml), de-dup 2 base prompts. Task 4 scans every agent and records the per-agent finding.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `packages/alliance/python/src/agr_ai_curation_alliance/tools/weaviate_search.py` | model-facing docstrings (runtime) | Enrich the 4 search/read `@function_tool` docstrings |
| `backend/src/lib/openai_agents/tools/weaviate_search.py` | legacy copy guarded by the doc test | Mirror the same docstring enrichment (keep copies in sync) |
| `packages/alliance/tools/bindings.yaml` | curator catalog source | Enrich the 4 tools' `description` / `metadata.documentation.summary` (curator-voice) |
| `packages/alliance/agents/gene_expression/prompt.yaml` | base prompt | Remove `<search_infrastructure>` + `Available tools:`; relocate the immutability sentence (see Task 2) |
| `packages/alliance/agents/pdf/prompt.yaml` | base prompt | Remove `<search_infrastructure>` |
| `backend/tests/unit/lib/openai_agents/tools/test_tool_descriptions.py` | model-visible doc contract | Extend to assert the relocated search facts in the docstrings (respect its `_assert_clean_doc` stale-phrase guard) |
| `backend/tests/unit/api/test_tool_catalog_parity.py` + `fixtures/tool_catalog_baseline.json` | curator catalog parity (added by #446) | Regenerate baseline after bindings.yaml edits; review diff |
| `backend/tests/unit/test_gene_expression_prompt_policy.py` | gene_expression prompt contract | Keep `:112` immutability assertion passing (line relocated, not deleted) |
| `docs/design/2026-06-03-prompt-size-report-phaseB.md` | redundancy map + sizes | Create (Task 4) |

---

## Task 1: Enrich the search/read tool descriptions (model docstrings + backend copy + curator bindings.yaml)

The source-of-truth is already traced (see Architecture). No investigation step — edit the confirmed targets.

- [ ] **Step 1: Failing test — assert the relocated facts reach the model-visible docstrings.** Extend `backend/tests/unit/lib/openai_agents/tools/test_tool_descriptions.py` (it builds the tools and asserts on `inspect.getdoc`). Add assertions that the search/read tool docstrings contain the load-bearing search-backend facts being relocated, e.g.: `search_document` -> `lexical` AND `section_keywords`; `read_section`/`read_subsection` -> conveys "all chunks" of the named section (survey, not page order); `read_chunk` -> `evidence_spans` / `span_id`. Keep the existing `_assert_clean_doc` stale-phrase guard intact and ensure new text avoids those phrases. Run -> expect FAIL.

- [ ] **Step 2: Enrich the PACKAGE docstrings** in `packages/alliance/python/src/agr_ai_curation_alliance/tools/weaviate_search.py` for the 4 tools, mapping gene_expression's `<search_infrastructure>` facts to tools (1-4 crisp sentences each, NOT the whole ~2K block):
  - **search_document:** default `auto`/hybrid (semantic + BM25) bridges paraphrases; `search_mode="lexical"` for exact gene symbols/IDs/strains/alleles/probes/genotypes/PMIDs/DOIs; `search_mode="hybrid_lexical_first"` to retry lexical-heavy; results reranked by a cross-encoder then diversified via MMR; short queries (<=3 tokens) auto-boost lexical; `section_keywords` scopes to sections before search; returns ~1500-char chunk previews.
  - **read_section / read_subsection:** return ALL chunks of a named section/subsection via the LLM-resolved semantic hierarchy (not page order), full chunk text; use for complete coverage when search may miss low-scoring passages; figure legends are a rich source.
  - **read_chunk:** returns full chunk text + `evidence_spans[].span_id` values for `record_evidence`.
  Do not introduce new behavior; only relocate existing guidance.

- [ ] **Step 3: Mirror the same enrichment into the BACKEND copy** `backend/src/lib/openai_agents/tools/weaviate_search.py` (same 4 docstrings, adjusted only for that copy's existing wording), so `test_tool_descriptions.py` guards text that matches the runtime package copy.

- [ ] **Step 4: Mirror curator-voice equivalents into `packages/alliance/tools/bindings.yaml`** — the `description` and `metadata.documentation.summary` for `search_document` / `read_chunk` / `read_section` / `read_subsection`. Keep the curator-voice (plain-language) register #446 established (see [[feedback_curator_voice_tool_docs_vs_contract_tests]] — the model gets the precise tokens via the docstrings; curator text stays approachable). Do NOT reintroduce dev jargon into bindings.yaml.

- [ ] **Step 5: Run + re-baseline.** `test_tool_descriptions.py` -> PASS. Run `tests/unit/api/test_tool_catalog_parity.py` and `tests/unit/api/test_tool_documentation_completeness.py` + `tests/unit/test_record_evidence_prompt_contract.py` (the curator catalog contract). If the catalog parity baseline (`fixtures/tool_catalog_baseline.json`) changed because of the bindings.yaml edits, regenerate it per its in-test procedure and review the diff (should be only the 4 tools' description/summary). The curator catalog contract test must still pass (curator-voice phrases).

- [ ] **Step 6: Commit**
```bash
git add packages/alliance/python/src/agr_ai_curation_alliance/tools/weaviate_search.py backend/src/lib/openai_agents/tools/weaviate_search.py packages/alliance/tools/bindings.yaml backend/tests/unit/lib/openai_agents/tools/test_tool_descriptions.py <regenerated baseline if any>
git commit -m "feat(tools): carry document-search guidance in the search/read tool descriptions (model docstrings + curator bindings.yaml); prereq for prompt de-dup"
```

---

## Task 2: De-dup the gene_expression base prompt (+ relocate the immutability line)

**Files:** `packages/alliance/agents/gene_expression/prompt.yaml`; possibly `backend/tests/unit/test_gene_expression_prompt_policy.py`.

- [ ] **Step 1: Redundancy map.** `<search_infrastructure>` -> enriched tool descriptions (Task 1); `Available tools:` -> always-sent tool schemas + the kept required-tool-call line in `core_generated`. Confirm every distinct fact is in its new home before deleting.

- [ ] **Step 2: Relocate the evidence-immutability sentence.** `gene_expression/prompt.yaml:257` ("After evidence is recorded, source quote and provenance fields are immutable…") lives INSIDE the `<search_infrastructure>` block but is **evidence-policy, not search-infra**, and is asserted by `test_gene_expression_prompt_policy.py:112`. Move that sentence into the prompt's `<evidence_record_contract>` (or `<evidence_rules>`) section BEFORE removing the block, so the rule survives and the test still passes unchanged. Record this in the redundancy map.

- [ ] **Step 3: Remove the `<search_infrastructure>` block** (the entire `<search_infrastructure> … </search_infrastructure>` section, minus the relocated sentence).

- [ ] **Step 4: Remove the `Available tools:` re-listing** within `<tools_and_retrieval>` (the bare bulleted tool list). Keep surrounding retrieval-strategy guidance (preferred-order workflow, retrieval budget) that is curation guidance, not a bare tool enumeration; note any judgment calls.

- [ ] **Step 5: Run + size.** `tests/unit/lib/prompts/ tests/unit/test_gene_expression_prompt_policy.py tests/unit/test_record_evidence_prompt_contract.py tests/unit/api/test_agent_studio_domain_envelope_prompt_policy.py` -> PASS. The immutability assertion must still pass (relocated, not deleted); if another asserted phrase was relocated to a tool description, re-baseline that assertion to its new home (do NOT weaken). Capture `wc -c packages/alliance/agents/gene_expression/prompt.yaml`.

- [ ] **Step 6: Commit**
```bash
git add packages/alliance/agents/gene_expression/prompt.yaml <any re-baselined test>
git commit -m "refactor(prompts): drop search-infrastructure + tool re-listing from gene_expression base prompt (relocate evidence-immutability line; search facts now in tool descriptions)"
```

---

## Task 3: De-dup the pdf base prompt

**Files:** `packages/alliance/agents/pdf/prompt.yaml`.

- [ ] **Step 1: Redundancy map** — pdf `<search_infrastructure>` -> enriched tool descriptions (Task 1). Confirm every fact in pdf's block is covered by the enriched docstrings; if pdf has a fact NOT already relocated, add it to the relevant tool docstring (package + backend copy) + bindings.yaml first.
- [ ] **Step 2: Remove the `<search_infrastructure>` block** from `pdf/prompt.yaml`.
- [ ] **Step 3: Run + size.** `tests/unit/lib/prompts/` + any `pdf` prompt/contract test (grep `backend/tests/unit` for `pdf`). PASS (re-baseline only relocated-phrase assertions). Capture `wc -c packages/alliance/agents/pdf/prompt.yaml`.
- [ ] **Step 4: Commit**
```bash
git add packages/alliance/agents/pdf/prompt.yaml <any re-baselined test>
git commit -m "refactor(prompts): drop duplicated search-infrastructure block from pdf base prompt (now in tool descriptions)"
```

---

## Task 4: Per-agent redundancy-map artifact + before/after sizes

**Files:** Create `docs/design/2026-06-03-prompt-size-report-phaseB.md`.

- [ ] **Step 1: Scan every agent** under `packages/alliance/agents/*/` and record per-agent: the redundancy map (removed -> surviving home) for gene_expression/pdf; for `gene_extractor`/`allele_extractor`/`disease_extractor`/`phenotype_extractor` record their `<search_context>` blocks as **"same search-backend facts, now in the tool descriptions; block removal deferred to Phase C"**; the chunk-annotation/pipeline background as an explicit intentional-drop; the immutability-line relocation; and "no Phase-B redundancy" for the rest.
- [ ] **Step 2: Before/after base-prompt sizes** for gene_expression + pdf (before from the spec: gene_expression 31,675; pdf 9,707) vs after (`wc -c`), with delta. **Honest token framing:** this is net-save for gene_expression + pdf only; the four `<search_context>` extractors keep their blocks until Phase C (no shrink yet); the 4 tool descriptions grow modestly and are sent to every document-tool agent. The win is de-duplication + correct structure, not a uniform per-call reduction.
- [ ] **Step 3: Commit**
```bash
git add docs/design/2026-06-03-prompt-size-report-phaseB.md
git commit -m "docs(design): Phase B per-agent redundancy map + before/after base-prompt sizes"
```

---

## Phase B gate (after all tasks)

1. Dispatch the final **Opus 4.8** review of the Phase B diff (vs the Phase A tip), tasked to confirm: every removed line has a verified surviving home (no curation guidance lost); the relocated search guidance actually reaches the MODEL via the package docstrings (not just bindings.yaml); the two `weaviate_search.py` copies stayed in sync; tool descriptions stayed crisp (not bloated); curator bindings.yaml stayed plain-language.
2. Run **`/external-llm-code-review`** (Codex, gpt-5.5/high) on the Phase B diff with the same task; show Chris the output verbatim.
3. Address findings, then proceed to the Phase C plan.

---

## Self-Review

**Spec coverage:** relocate search guidance to tool descriptions before removal — Task 1 (confirmed dual-layer targets + a test on the model-visible docstrings); per-agent redundancy maps — Tasks 2/3 + Task 4; remove `<search_infrastructure>` (gene_expression+pdf) and `Available tools:` (gene_expression) — Tasks 2/3; the 4 `<search_context>` blocks recorded + Phase-C-deferred — Scope + Task 4; immutability line preserved + test kept green — Task 2; evidence-policy deferred to Phase C — Scope. Covered.

**Placeholder scan:** the pre-#446 "locate the source" investigation step is removed (the source is now confirmed); all targets are concrete file paths verified by the investigation. No TBDs.

**Consistency:** relocate-before-remove ordering across Tasks 1->2->3; the model-vs-curator dual-layer edit is consistent in Task 1 + the File Structure; the redundancy-map requirement is consistent in Tasks 2/3/4.
