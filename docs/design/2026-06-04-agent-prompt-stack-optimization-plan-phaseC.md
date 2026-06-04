# Agent Prompt-Stack Optimization — Phase C Implementation Plan (outcome-first base-prompt rewrites, all agents)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`). Each per-agent rewrite is a fresh subagent + two-stage review. **Revised after Opus 4.8 + Codex gpt-5.5/high plan review** (both "execute with changes"); their must-fixes are folded in below.

**Goal:** Rewrite every agent's base prompt to a lean, outcome-first structure, preserving every load-bearing curation rule, guarded by a reusable no-DB mechanical-check harness + committed per-agent semantic-coverage checklists. **Loss-full** (changes instructions the model acts on; accepted); verification is **structural + the two LLM reviews (no live A/B)**.

## Prompt trees & scope (verified)

- **`packages/alliance/agents/` is the live committed source** (resolver path; production mounts it). Edit it for the 24 packaged agents.
- **`config/agents/` is an override layer that wins by folder name** and is on the production path. Three agents have a **prompt** override there and MUST be edited in `config/agents/`: **`supervisor`** (config-only, ~17.5K), **`curation_prep`** (config-only, ~3.5K), **`chat_output`** (in BOTH trees, currently byte-identical — edit the config copy AND keep the packages copy in sync). A harness guard asserts no agent's `config/agents` prompt diverges unexpectedly from packages.
- **`alliance_agents/` is a STALE legacy mirror — do NOT edit.** No `docs.yaml`, last commit 2026-05-30, already diverged from packages; `scripts/deploy_alliance.sh` (which would copy it into packages) is "Alliance-internal only" (`06_start_verify.sh:311`), not in the deploy path. **Flag:** do not run `deploy_alliance.sh` against this branch — it would revert this work from the stale mirror.
- **Total scope: 26 prompts** = 24 packaged + supervisor + curation_prep (chat_output counts once but is edited in two trees).

**Tech Stack:** Python 3.11, YAML, pytest. Run (no DB):
```bash
docker run --rm -v "$(pwd)/backend:/app/backend" -v "$(pwd)/packages:/app/packages:ro" -v "$(pwd)/config:/app/config:ro" -v "$(pwd)/alliance_agents:/app/alliance_agents" -v "$(pwd)/docs:/app/docs:ro" -v "$(pwd)/frontend:/app/frontend:ro" -w /app/backend -e OPENAI_API_KEY=test -e PYTHONUNBUFFERED=1 -e EMBEDDING_MODEL=text-embedding-3-small -e EMBEDDING_MODEL_TOKEN_LIMIT=8191 -e EMBEDDING_TOKEN_SAFETY_MARGIN=500 ai-curation-unit-tests:latest python -m pytest <paths> -q
```
(`test_pdf_corpus_trial_examples_do_not_teach_quote_submission` fails environmentally here — ignore.)

## Execution model: WAVES (per Codex)

Execute in waves; gate (Opus 4.8 + Codex gpt-5.5/high) after each wave before the next:
- **Wave 0 — Harness (Task 1)** + **prove it catches a planted loss** on one pilot.
- **Wave 1 — Pilot extractor: gene_extractor** (largest). Validate the full per-agent procedure end-to-end.
- **Wave 2 — Remaining extractors:** gene_expression, disease_extractor, allele_extractor, phenotype_extractor, pdf.
- **Wave 3 — Validators/lookups:** gene, allele, ontology_term, chemical, go_annotations, experimental_condition, disease, orthologs, reference, controlled_vocabulary, gene_ontology, data_provider, subject_entity, agm, supervisor.
- **Wave 4 — Output/formatters:** chat_output (dual-tree), tsv_formatter, json_formatter, csv_formatter, curation_prep.

## The outcome-first skeleton (every agent)

Restructure to: **Role → Goal → Success criteria → Constraints (invariants only) → Output/handoff → Stop rules.** Convert process lists to decision rules + stop criteria (keep exact path only where it matters — resolver loop, evidence-span workflow). Collapse repeats; resolve contradictions; reserve MUST/NEVER for true invariants. Curator-voice (zero-tech audience). Preserve domain-specific no-invention/resolver rules.

**Extractors additionally:** drop the `<search_context>` block (facts already in tool descriptions; DROP the inaccurate "~1500-char" sentence — do not relocate); relocate any non-search (evidence-policy) sentence into the evidence section; de-dup the evidence-span mechanic that duplicates `core_generated`'s `record_evidence` summary (keep the curation guidance).

---

## Task 1 (Wave 0): Build the no-DB mechanical-check harness

**Files:** `backend/tests/unit/lib/prompts/phase_c_harness.py` (helpers) + `backend/tests/unit/lib/prompts/test_phase_c_rewrite_guards.py` (tests) + `backend/tests/unit/lib/prompts/phase_c_inventories/<agent>[.<group>].txt` and `<agent>.dropped.json` per agent.

- [ ] **Step 1: Real no-DB assembled-render helper.** `build_agent_prompt_layers().render()` reads the DB-backed prompt cache (`assembly.py:151` → `cache.py:289` raises if uninitialized) — it does NOT read files. So write `assembled_prompt_text(agent_id, group_id=None)` that, with NO DB: resolves the agent via `resolve_agent_config_sources()` / `load_agent_definitions()`, reads `prompt.yaml` `content` (base) and the group_rules YAML for `group_id`, builds the locked core via `build_agent_core_prompt(agent_id)` (no DB), and concatenates `core.render() + "\n\n" + base_content + "\n\n" + group_content`. This is the real assembled text for retention checks. (Do NOT use the monkeypatched fake-content fixtures the existing assembly tests use.)

- [ ] **Step 2: Fragment-retention guard with group dimension.** Per inventory file `<agent>[.<group>].txt` (one load-bearing phrase per line; a `.<group>` suffix means render with that group), assert every phrase appears in `assembled_prompt_text(agent, group)`. The **checklist (fresh full read) is the authoritative inventory source**; the existing contract test's asserted phrases are a secondary must-include set (they cover only a small base-prompt subset). Seed initially with 2 agents to prove the guard FAILS on a missing phrase and PASSES when present.

- [ ] **Step 3: Machine-checked dropped-list.** `<agent>.dropped.json` entries each carry `{phrase, category: relocated|deleted, reason, old_source, new_home}`. For `category=relocated`, the harness ASSERTS the phrase (or a declared synonym) actually appears in `new_home` (the assembled render, a `bindings.yaml` tool description, or `get_agent_contract` output). For `category=deleted` (no home — truly redundant/inaccurate), no assertion, but the harness PRINTS the full deleted-with-no-home list as review output so it can't hide in the diff. A relocated entry whose home check fails = test failure (closes the "drop a rule and add it to the dropped-list" gaming hole).

- [ ] **Step 4: Workflow-invariant assertions (restored from spec).** Per agent, assert the named/ordered workflow steps survive in the assembled render: evidence-span workflow, resolver workflow, builder stage/finalize workflow (incl. counts/ordering where the existing contract test asserts them, e.g. `test_gene_extractor_domain_envelope_contract.py:376-384`), no-invention rule, validator delegation, output/handoff contract. Encode these as per-agent invariant assertions (stronger than phrase retention — they assert ordered steps).

- [ ] **Step 5: Reason-code survival.** For agents with canonical `reason_code` enums, assert the full set (sourced from the **domain pack** — e.g. `packages/alliance/python/.../domain_packs/gene_expression/{export,conversion}.py`, NOT by grepping the prompt) appears in the assembled render.

- [ ] **Step 6: Report-only contradiction dump + config-divergence + render/custom-agent smoke.** (a) Dump every MUST/NEVER/ALWAYS line per agent for the human reviewer (no automated semantic detection — current prompts have zero such tokens; Phase C introduces them). (b) Assert no agent's `config/agents/<a>/prompt.yaml` diverges from its `packages` copy except the intended config-only agents. (c) Render smoke: each agent's assembled bundle renders; a custom (curator-cloned) agent's render works (reuse the catalog/`_build_runtime_instructions` path).

- [ ] **Step 7:** Harness green on seeds. Commit `test(prompts): Phase C no-DB rewrite-guard harness (retention w/ groups, machine-checked dropped-list, workflow invariants, reason-code survival, contradiction dump, config-divergence + render smoke)`.

- [ ] **Step 8 (prove it):** On the pilot, deliberately drop one real rule WITHOUT updating the inventory/dropped-list and confirm the harness FAILS; then revert. Record this in the Wave-0 gate.

---

## Per-agent procedure (every rewrite task)

For agent `<A>` (tree per the scope section):
- [ ] **1. Semantic-coverage checklist** → `docs/design/phaseC-checklists/<A>.md`: read the current prompt + its seed contract test; list EVERY load-bearing rule (curation rules, no-invention, resolver discipline, reason_codes, evidence rules, output/handoff contract, group-rule hooks, field-path/count/ordering contracts) → its new home, or an explicit justified drop. **Each checklist line gets a stable ID.**
- [ ] **2. Inventory + dropped-list** for the harness (`phase_c_inventories/<A>...`), derived from the checklist (authoritative) + the contract-test phrases (secondary).
- [ ] **3. Rewrite** `prompt.yaml` `content` to the skeleton, preserving every checklist item, in curator voice. Extractors: `<search_context>` drop + evidence de-dup. For config-tree agents edit `config/agents/<A>/prompt.yaml` (and sync packages for chat_output).
- [ ] **4. Re-baseline rules (tight):** any edited/removed assertion in the seed contract test MUST be cross-referenced to a checklist ID and have a **replacement assertion in the SAME commit** (ideally targeting the new layer via the assembled render), OR a reviewed `deleted` dropped-entry. **Never** delete an assertion with no replacement; **never** weaken a count/ordering assertion (`==3`→`>=1` is forbidden — counts/ordering may move with their example but not loosen).
- [ ] **5. Run** the harness for `<A>` + the agent's contract/policy test + `tests/unit/lib/prompts/` + (after each extractor) the cross-cutting guards (`test_record_evidence_prompt_contract.py`, `test_domain_envelope_repair_prompt_contract.py`, `test_non_gene_evidence_prompt_policy.py`, `test_agent_studio_domain_envelope_prompt_policy.py`, `test_assembly.py`). Green. Capture before/after `wc -c`.
- [ ] **6. Commit** (explicit paths): prompt.yaml(s) + checklist + inventory + dropped-list + any re-baselined test.
- [ ] **7. Two-stage review** — spec (every checklist ID has a verified home; no assertion deleted without replacement; no count/ordering weakened; group phrases checked under their group) then code-quality (reads clean, curator-voice, outcome-first), **plus a scenario-card pass** (the reviewer walks the rewritten prompt against the agent's scenario cards — e.g. for extractors: prior-work citation, methods-only mention, rescue/marker exclusion, negative result, ambiguous ontology/provider — confirming each still resolves correctly).

---

## Per-wave gate + final

- After each wave: Opus 4.8 review + `/external-llm-code-review` (Codex gpt-5.5/high) over that wave's diff (no load-bearing rule lost; no contradictions; curator-voice; no weakened assertions; scenario cards hold). Output shown to Chris. Address findings before the next wave.
- **Final:** size artifact `docs/design/2026-06-04-prompt-size-report-phaseC.md` (per-agent before/after + checklist index + honest loss-full framing) and a closing gate review over the full Phase C diff.

## Self-Review

**Spec coverage:** outcome-first rewrite of all agents (incl. the config-tree supervisor/curation_prep/chat_output) — per-agent tasks across waves; harness with real no-DB render + group dimension + machine-checked dropped-list + workflow invariants + reason-code survival + contradiction dump + render/custom-agent/config-divergence smoke — Task 1; per-agent semantic-coverage checklist with IDs + tight re-baselining + scenario cards — procedure; `<search_context>` drop + evidence de-dup for extractors — skeleton; structural + two reviews per wave, no live A/B, loss-full accepted — gate. Covered. `alliance_agents` explicitly out (stale); deploy_alliance.sh landmine flagged.

**Placeholder scan:** the harness foundation is now concretely specified (no-DB render via `build_agent_core_prompt` + file-read base/group concat — corrects the prior false "reads files" claim); per-agent tasks share one fully-specified procedure with per-agent inputs in the wave lists. No TBDs.

**Consistency:** inventory/group format, checklist-ID re-baselining, and the skeleton are consistent across all per-agent tasks and waves.
