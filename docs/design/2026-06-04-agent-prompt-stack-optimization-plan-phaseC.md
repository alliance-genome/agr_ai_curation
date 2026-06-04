# Agent Prompt-Stack Optimization — Phase C Implementation Plan (outcome-first base-prompt rewrites, all 24 agents)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. Each per-agent task is a fresh subagent + two-stage review.

**Goal:** Rewrite every agent's base prompt (`packages/alliance/agents/<agent>/prompt.yaml`) to a lean, outcome-first structure, preserving every load-bearing curation rule, guarded uniformly by a reusable mechanical-check harness + a committed per-agent semantic-coverage checklist.

**Architecture:** Phase C is **loss-full** (it changes instructions the model acts on) and accepted as such; verification is **structural + the two LLM reviews (no live A/B)** per the spec. A shared harness (Task 1) makes the rewrites guardable without live runs; Tasks 2-25 rewrite one agent each, following ONE procedure, ordered by base-prompt size (largest/highest-value first). Each rewrite preserves the agent's load-bearing inventory (seeded from its existing contract test + a fresh read), restructures to the skeleton, runs the mechanical checks, and goes through spec + code-quality review.

**Tech Stack:** Python 3.11, YAML, pytest. Run command (no DB):
```bash
docker run --rm -v "$(pwd)/backend:/app/backend" -v "$(pwd)/packages:/app/packages:ro" -v "$(pwd)/config:/app/config:ro" -v "$(pwd)/alliance_agents:/app/alliance_agents" -v "$(pwd)/docs:/app/docs:ro" -v "$(pwd)/frontend:/app/frontend:ro" -w /app/backend -e OPENAI_API_KEY=test -e PYTHONUNBUFFERED=1 -e EMBEDDING_MODEL=text-embedding-3-small -e EMBEDDING_MODEL_TOKEN_LIMIT=8191 -e EMBEDDING_TOKEN_SAFETY_MARGIN=500 ai-curation-unit-tests:latest python -m pytest <paths> -q
```
(`test_pdf_corpus_trial_examples_do_not_teach_quote_submission` fails environmentally in this checkout — gitignored on-disk corpus artifacts; ignore it.)

---

## The outcome-first skeleton (applied to EVERY agent)

Restructure each base prompt to this order, scaled to the agent:
**Role → Goal → Success criteria → Constraints (invariants only) → Output / handoff → Stop rules.**

Rules:
- Convert step-by-step PROCESS lists into outcome statements + decision rules + explicit stop criteria. Keep the exact path only where it genuinely matters (e.g., the resolver loop, the evidence-span workflow).
- Collapse repeated rule statements into one clearly-placed rule. Resolve contradictions (reserve `MUST`/`NEVER`/`ALWAYS` for true invariants — safety, required fields, no-invention, resolver discipline).
- Preserve curator-voice (plain language for biologists; see [[feedback_curator_friendly_docs]] and [[feedback_curator_voice_tool_docs_vs_contract_tests]]). Preserve domain-specific no-invention/resolver rules — do NOT lean on `core_generated`'s single validator line.
- **For the extractors** (gene_expression, gene_extractor, disease_extractor, allele_extractor, phenotype_extractor, pdf): additionally
  - **Drop the `<search_context>` block** — its search-backend facts are already in the search/read tool descriptions (Phase B). DROP the inaccurate "~1500 characters per chunk hit" sentence (search returns full text), do NOT relocate it. Relocate any genuinely non-search sentence (evidence-policy) into the evidence section, like Phase B did for gene_expression/pdf.
  - **De-dup the evidence-policy mechanics** that duplicate `core_generated`'s `record_evidence` summary (the literal `read_chunk.evidence_spans[].span_id -> record_evidence -> verified_quote` mechanic) — keep the curation guidance (what counts as strong/weak evidence, the workflow, exclusions), remove only the verbatim mechanic restatement.

---

## Task 1: Build the shared mechanical-check harness

**Files:** Create `backend/tests/unit/lib/prompts/test_phase_c_rewrite_guards.py` + a per-agent inventory dir `backend/tests/unit/lib/prompts/phase_c_inventories/` (one `<agent>.txt` per agent: load-bearing phrases that MUST appear in the assembled prompt, one per line; plus an optional `<agent>.dropped.txt` for explicit intentional-drops with reasons).

- [ ] **Step 1: Inventory loader + retention guard.** A test that, for each agent with an inventory file, builds the assembled prompt (`build_agent_prompt_layers(agent_id).render()`) and asserts every inventory phrase appears in it. (No DB needed; `build_agent_prompt_layers` reads files. If a group-scoped phrase is needed, assemble with the relevant group.) A phrase that legitimately moved out of the base prompt (e.g. to a tool description or core_generated) is recorded in `<agent>.dropped.txt` with its new home and excluded from the retention set. Write the loader + a parametrized test; seed it initially with a couple of agents' inventories to prove it works (FAIL on a missing phrase, PASS when present).
- [ ] **Step 2: Reason-code survival guard.** For agents that enumerate canonical `reason_code` values (gene_expression, and any extractor exposing `metadata.exclusions[].reason_code`), assert the full set appears in the assembled prompt. Source the canonical set from the domain pack / the current prompt; commit it as part of the inventory.
- [ ] **Step 3: Contradiction lint.** A check that greps each rewritten prompt for co-occurring contradictory absolutes on the same field/subject (a heuristic: flag if the same field path appears under both a MUST/ALWAYS and a NEVER within the prompt). Keep it simple and report-only if a clean automatic rule is hard; the per-agent review is the backstop.
- [ ] **Step 4: Render smoke.** Assert each agent's assembled bundle renders without error and `core_generated` is present where expected (reuses the Phase A smoke pattern).
- [ ] **Step 5: Run + commit.** Harness green on the seeded agents. Commit `test(prompts): Phase C rewrite-guard harness (per-agent retention, reason-code survival, contradiction lint, render smoke)`.

---

## Per-agent procedure (Tasks 2-25 each follow this)

For agent `<A>` (inputs in the table below):
- [ ] **Step 1: Semantic-coverage checklist.** Read `packages/alliance/agents/<A>/prompt.yaml` and its seed contract test. Extract EVERY load-bearing rule (curation rules, no-invention, resolver discipline, reason_codes, evidence rules, output/handoff contract, group-rule hooks, field-path contracts). Write `docs/design/phaseC-checklists/<A>.md` mapping each rule -> its new home in the rewrite (or an explicit, justified "intentionally dropped as redundant/inaccurate").
- [ ] **Step 2: Build the agent's harness inventory.** From the checklist + the seed contract test's asserted phrases, write `backend/tests/unit/lib/prompts/phase_c_inventories/<A>.txt` (load-bearing phrases to retain) and `<A>.dropped.txt` (relocated/dropped, with reasons).
- [ ] **Step 3: Rewrite** `prompt.yaml` `content` to the skeleton, preserving every checklist item, collapsing repeats, resolving contradictions, in curator voice. For extractors, apply the `<search_context>` drop + evidence-policy de-dup.
- [ ] **Step 4: Run** the harness for `<A>` + the agent's existing contract/policy test + `tests/unit/lib/prompts/`. Green. Re-baseline an existing contract assertion ONLY where the checklist documents a legitimate move; NEVER weaken. Capture before/after `wc -c`.
- [ ] **Step 5: Commit** (explicit paths): the prompt.yaml + the checklist + the inventory files + any re-baselined test. Message: `refactor(prompts): outcome-first rewrite of <A> base prompt (-<delta> chars; semantic-coverage checklist committed)`.
- [ ] **Step 6: Two-stage review** — spec (every checklist item has a verified home; no load-bearing rule lost; no test weakened) then code-quality (reads clean, curator-voice, outcome-first, no contradictions, no orphaned refs).

---

## Agent order + per-agent inputs

Ordered largest-first (highest value + biggest reduction). "Seed test" = the contract/policy test whose asserted phrases seed the retention inventory (if none listed, build the inventory fresh from the prompt).

| # | agent | chars | seed test | notes |
|---|---|---:|---|---|
| 2 | gene_extractor | 28222 | test_gene_extractor_domain_envelope_contract | extractor: `<search_context>` drop (53-line near-twin; DROP ~1500 claim) + evidence de-dup; handoff/envelope contract |
| 3 | gene_expression | 26936 | test_gene_expression_prompt_policy | extractor: evidence de-dup; canonical reason_codes; `<search_infrastructure>` already removed (Phase B) |
| 4 | disease_extractor | 19826 | test_disease_extractor_domain_envelope_contract | extractor: `<search_context>` drop + evidence de-dup |
| 5 | allele_extractor | 17031 | test_allele_extractor_mgi_prompt_policy | extractor: `<search_context>` drop + evidence de-dup; MGI group hook |
| 6 | phenotype_extractor | 16367 | test_phenotype_extractor_domain_envelope_contract | extractor: `<search_context>` drop + evidence de-dup; phenotype reason_codes |
| 7 | gene | 16172 | test_gene_allele_validator_result_contract | validator: no-invention/resolver; validator-result contract |
| 8 | allele | 12679 | test_gene_allele_validator_result_contract | validator |
| 9 | ontology_term | 10649 | test_ontology_term_validator_contract | lookup/validator |
| 10 | chemical | 10031 | test_disease_chemical_validator_result_contract | validator |
| 11 | go_annotations | 8564 | (fresh) | lookup |
| 12 | experimental_condition | 8054 | (fresh; cross-type — see test_record_evidence/domain_envelope) | validator; composite-condition rules |
| 13 | disease | 7642 | test_disease_chemical_validator_result_contract | validator |
| 14 | orthologs | 7555 | (fresh) | lookup |
| 15 | reference | 7349 | test_reference_validator_result_contract | validator |
| 16 | controlled_vocabulary | 6843 | (fresh) | lookup |
| 17 | gene_ontology | 6712 | (fresh) | lookup |
| 18 | pdf | 6514 | test_record_evidence_prompt_contract | extractor (output_schema); `<search_infrastructure>` already removed (Phase B); evidence de-dup |
| 19 | data_provider | 5822 | test_provider_contract_guardrail | validator |
| 20 | subject_entity | 5262 | test_subject_entity_validator_result_contract | validator |
| 21 | chat_output | 5052 | (fresh) | output agent (has output_schema) |
| 22 | tsv_formatter | 4779 | test_tsv_formatter_prompt_policy | formatter — likely minimal change; record "already lean" if so |
| 23 | agm | 3750 | (fresh) | validator |
| 24 | json_formatter | 3260 | test_json_formatter_prompt_policy | formatter — likely minimal |
| 25 | csv_formatter | 3196 | test_csv_formatter_prompt_policy | formatter — likely minimal |

Also re-run the cross-cutting guards after each extractor rewrite: `test_record_evidence_prompt_contract.py`, `test_domain_envelope_repair_prompt_contract.py`, `test_non_gene_evidence_prompt_policy.py`, `test_agent_studio_domain_envelope_prompt_policy.py`, `test_assembly.py` (the editable-prompt forbidden-fragment guard).

---

## Final task: Phase C size artifact + gate

- [ ] **Size artifact** `docs/design/2026-06-04-prompt-size-report-phaseC.md`: per-agent before/after `wc -c`, total reduction, and the per-agent checklist index. Honest framing: each rewrite preserved its checklist; loss-full risk is mitigated by the harness + checklists + the two reviews, not live A/B.
- [ ] **Phase C gate:** Opus 4.8 review + `/external-llm-code-review` (Codex gpt-5.5/high) over the whole Phase C diff, tasked to confirm per-agent: no load-bearing rule lost (spot-check checklists against diffs), no contradictions introduced, curator-voice preserved, no test weakened to hide a loss. Output shown to Chris verbatim. Address findings.

---

## Self-Review

**Spec coverage:** outcome-first rewrite of all agents — Tasks 2-25; mechanical non-live checks (fragment-retention, reason-code survival, contradiction lint, render smoke) — Task 1 harness + per-agent inventories; per-agent semantic-coverage checklist — Step 1 each; `<search_context>` drop + evidence de-dup for extractors — skeleton + per-agent notes; structural + two reviews, no live A/B, loss-full accepted — Architecture + gate. Covered.

**Placeholder scan:** the per-agent tasks intentionally share ONE concrete procedure (the skeleton + harness + checklist are fully specified) rather than 24× verbatim duplication — this is the right granularity for repetitive-but-careful rewrites; each task's per-agent inputs (size, seed test, notes) are in the table. The "(fresh)" seed entries mean "build the inventory from the prompt itself" (explicit, not a TBD).

**Consistency:** the harness inventory format, the checklist requirement, and the skeleton are consistent across all per-agent tasks; the extractor-specific `<search_context>`/evidence-de-dup steps are consistent in the skeleton + the table notes.
