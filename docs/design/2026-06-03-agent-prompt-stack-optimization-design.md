# Agent Prompt-Stack Optimization — Design Spec

Date: 2026-06-03. Status: **DESIGN APPROVED by Chris**, revised after Opus 4.8 + Codex (gpt-5.5/high) spec reviews. Scope: full (Phase A + B + C) across **ALL agents** (extractors, validators, lookups, output/formatter agents) — Chris expanded scope from extractors-only. Verification: structural + the two LLM reviews (no live A/B extraction). Branch: `agent-prompt-stack-optimization` (off `main`, independent of the doc-migration/UI-redesign branch in PR #446).

## Context & problem

The curation agents carry a large fixed system-prompt overhead on every model call. Measured per-layer sizes for the **gene_expression extractor** (the worst case), before document content and tool schemas:

| Layer (UI label) | chars | ~tokens | Editable? |
|---|---:|---:|---|
| Built-in instructions (`core_static`) | 234 | ~60 | locked |
| Generated Contract / "Output structure" (`core_generated`) | 9,023 | ~2,255 | locked |
| Template instructions (`base_prompt`, from `prompt.yaml`) | 31,665 | ~7,916 | editable |
| Species & group rules (WB) | 7,304 | ~1,826 | editable |
| Your custom instructions (overlay) | varies | — | editable |
| **Total (gene_expression + WB, no overlay)** | **~48.2K** | **~12,050** | |

(Figures are from a one-off measurement; Phase A's size-report utility will replace them with reproducible numbers.) The extractor base prompts are the dominant cost (gene_expression ~7.9K tok, gene_extractor ~7.1K, disease_extractor ~5.0K, allele_extractor ~4.3K, phenotype_extractor ~4.1K); validators/lookups are leaner (~1.2K generated + 1.5–4K base) but the same principles apply to them and are **in scope**.

Two problems were confirmed against the code:

1. **The runtime-contract portion of `core_generated` is largely audit detail the model does not act on** — the full tool inventory (already supplied as tool schemas, and re-listed in the base prompt), the evidence policy (repeated in the base prompt), ~37 `field -> validator_binding` mappings, and ~13 active-validator-binding lines with literal CURIE allow-lists. The model does not choose or run validators; the backend does. The contract even advertises `get_agent_contract` for these facts and then inlines them anyway.
2. **The base prompts are large and overlap the other layers** — evidence rules, the full tool list, and a generic `<search_infrastructure>` block repeat across layers and the tool schemas.

A third opportunity applies to every agent that sets an `output_schema` (validators + `pdf`): `core_generated` injects a verbose structured-output instruction ("your final response MUST be valid JSON matching the {name} schema EXACTLY…"). The runtime already enforces the schema via the SDK — `Agent(..., output_type=output_schema)` at `catalog_service.py:2138` — so per OpenAI's guidance that prose is redundant restatement of structure.

### OpenAI GPT-5.5 guidance (load-bearing, official sources)

- *"Start with the smallest prompt that preserves the product contract… shorter, outcome-first prompts usually work better than process-heavy stacks."*
- *"Contradictory or vague instructions can be more damaging to GPT-5 than to other models, as it expends reasoning tokens reconciling them."* — bloat can lower accuracy, not just raise cost.
- *"Put most tool-specific guidance in the tool descriptions themselves."*
- *"Don't include output schema definitions in the prompt — use Structured Outputs instead"*; don't restate the field list in prose.
- Control length/verbosity via `text.verbosity` / `reasoning_effort`, not prose; reserve MUST/NEVER for true invariants.

Sources: `developers.openai.com/api/docs/guides/prompt-guidance`, `/guides/latest-model`, the GPT-5 and GPT-5.2 cookbook prompting guides, and `/guides/structured-outputs`.

## Scope: which agents

**All agents** with a base prompt and/or a `core_generated` layer are in scope — the extractors (`gene_expression`, `gene_extractor`, `disease_extractor`, `allele_extractor`, `phenotype_extractor`, `pdf`), the validators/lookups (`gene`, `allele`, `disease`, `chemical`, `gene_ontology`, `go_annotations`, `orthologs`, `ontology_term`, `subject_entity`, `data_provider`, `reference`, `experimental_condition`, `controlled_vocabulary`, `agm`, `supervisor`, …), and the output/formatter agents (`json_formatter`, `csv_formatter`, `tsv_formatter`, `chat_output`).

Per-agent effort scales with the agent's measured size: the big extractors get a full Phase C rewrite; an already-lean prompt may pass through with little or no change. "Minimal change" is a valid outcome, but it is **recorded** in that agent's semantic-coverage checklist — no agent is skipped silently.

## Goals / non-goals

**Goals.** Reduce system-prompt overhead across all agents; align the stack with the GPT-5.5 guidance; lose **no** load-bearing curation rule; preserve all audit/provenance capability.

**Non-goals.** Changing curation semantics or accuracy *intent*; validator logic; the Agent Studio UI; live A/B extraction evals (deferred, available as a later per-agent opt-in). Submission-gating, schema, and validator bindings are unchanged.

## Principles (applied everywhere)

1. Smallest prompt that preserves the contract.
2. Outcome-first: success criteria + decision rules + stop rules over step-by-step process.
3. No duplication and no contradiction across layers.
4. Tool-usage detail lives in tool descriptions (`bindings.yaml`), not restated in the system prompt.
5. Schema / field / validator / ontology detail lives in Structured Outputs + the on-demand `get_agent_contract` tool, not inlined.
6. `MUST` / `NEVER` / `ALWAYS` only for true invariants (safety, required fields, no-invention, resolver discipline).

## Current architecture (what assembles the prompt)

`backend/src/lib/prompts/assembly.py` builds an ordered `PromptLayerBundle`:
`core_static` -> `core_generated` -> `base_prompt` -> `group_rules` -> `curator_overlay` -> `runtime_context`; `render()` joins non-empty layer content with `\n\n`.

- `core_generated` = `_build_compact_runtime_contract(agent)` (built from tool inventory, tool-call policy, `TOOL_POLICY_SUMMARIES`, and `_build_domain_pack_contract_lines`) **plus** an optional structured-output instruction injected only when `agent.output_schema` is set (`assembly.py:285-307`; the sole caller of `inject_structured_output_instruction`). Empty for the builder-tool extractors (`output_schema: null`); present for the `output_schema` agents (validators + `pdf`).
- `base_prompt` is the agent's `packages/alliance/agents/<agent>/prompt.yaml` `content`, seeded into `PromptTemplate` rows.
- Runtime DB agents pass the rendered bundle as `Agent(instructions=..., output_type=output_schema)` (`catalog_service.py:2132-2138`); the supervisor uses the same assembler. **Custom (curator-cloned) agents** inherit their parent's locked layers plus the curator overlay via `_build_runtime_instructions()` — Phase A changes propagate to them automatically; the plan must include a custom-agent smoke check.
- `get_agent_contract` (`backend/src/lib/agent_contracts.py`; tool wrapper in `…/tools/agent_contract.py`) serves topics `tools`, `output_schema`, `domain_envelope`, `validator_bindings`, `ontology_constraints`, `field` — the on-demand path for the detail Phase A removes from the prompt.

**Audit note (review correction):** removed prompt text is **not** "retained in the layer manifest" — the `PromptLayer` manifest stores the rendered `content`/`hash`/`source_ref`, so text removed from `content` is gone from the manifest too. The removed detail is retained in **source domain-pack/agent config** and remains retrievable through **`get_agent_contract`**; that is the audit path, not the manifest.

## Phase A — Slim `core_generated` (backend only; all agents)

Edit `_build_compact_runtime_contract`, `_build_domain_pack_contract_lines`, `TOOL_POLICY_SUMMARIES`, and the structured-output injection path in `assembly.py` / `prompt_utils.py`.

### A1 — Runtime-contract enumeration (agents with tools/domain packs)

**Keep (the model acts on these):** the `## Generated Runtime Contract` heading; the required-tool-call policy line; the evidence policy summary (`record_evidence`) — kept **here only**, removed from base prompts in Phase B; the runtime safety rule (no inventing CURIEs); a **single** compact line "Validators own these fields; do not invent their identifiers — <short capped list>"; and the existing one-line pointer to `get_agent_contract`.

**Remove from the prompt text** (retained in source config + retrievable via `get_agent_contract`): the full tool-inventory enumeration; schema/provider refs; the envelope-objects `required[...]` dump; the full `field -> binding` map; the per-binding `targets … policy … selectors …` lines including literal CURIE allow-lists.

### A2 — Structured-output instruction (agents with `output_schema`: validators + `pdf`) — now in scope

The schema is already enforced via `Agent(output_type=…)`, so the format-restatement prose is redundant. Slim the `STRUCTURED_OUTPUT_INSTRUCTION_TEMPLATE` to the minimum: keep only the few sentences of semantics a strict schema cannot encode (e.g. "set absent fields to null rather than guessing", evidence-grounding rules, and the genuinely semantic parts of the domain-envelope template such as `object_role`/`evidence_record_ids` intent). Drop the "MUST be valid JSON matching the schema EXACTLY / do not wrap in markdown" boilerplate that `output_type` already guarantees.

### Acceptance (Phase A)

- Measurable reduction in `core_generated` for every affected agent (extractor runtime-contract cut + validator structured-output-prose cut), reported by the new size utility.
- **The verbose fragments Phase A removes are asserted by name in existing tests** — these encode the current contract and must be re-baselined, not treated as incidental churn: `test_assembly.py::test_core_generated_contract_summarizes_tool_and_domain_metadata` (asserts `"Tool inventory from agent.yaml"`, the `field->binding` line, `accepted_prefixes<-literal:[…]`, `"Pending unresolved shapes"`), and the per-extractor `*_domain_envelope_contract` / `*_prompt_policy` tests. Re-baseline against the new compact contract; do not weaken the guards' intent.
- For each removed datum, an acceptance test confirms it is still returned by the matching `get_agent_contract` topic, using **`detail_level="detail"`** where required (e.g. envelope `required[]` flags; per-`field_path` binding lookups). Note: the field→binding map is *reconstructable* via the `field`/`validator_bindings` topics, not returned in the old inline shape — that is acceptable because the model never acted on the bulk map.
- The Phase A layer diff is **deletion-only** for A1 (no semantic additions); A2 may rewrite the structured-output sentences but each change is justified in the diff notes.
- Tool-catalog parity, documentation-completeness, and manifest tests stay green.

## Phase B — De-duplicate across layers (per-agent; all agents + tool descriptions)

Each agent gets its **own** redundancy map (old block -> surviving home); a block is removed only when its content provably lives in another layer or in a tool description. The maps differ per agent — verified that **only `gene_expression/prompt.yaml`** currently contains the large `<search_infrastructure>` block and an `Available tools:` re-listing; the other extractors and the validators have neither, so their maps are smaller and derived from their actual content.

Representative map (gene_expression):

| Removed from base prompt | Now lives in |
|---|---|
| `Available tools:` re-listing | tool schemas (always sent) + the kept required-tool-call line in `core_generated` |
| Duplicated evidence-policy prose | `core_generated` evidence policy (Phase A keep) |
| Generic `<search_infrastructure>` block (~2K) | tool-specific parts -> `search_document` / `read_section` / `read_subsection` / `read_chunk` descriptions in `bindings.yaml`; non-tool-specific background -> dropped |

**Critical sequencing (review finding):** the `bindings.yaml` document-tool descriptions are currently terse and do **not** yet carry the load-bearing `<search_infrastructure>` guidance (Weaviate/MMR behavior, `section_keywords`, lexical-mode strategy, section-hierarchy implications). **Enrich those tool descriptions first**, then remove the block from the prompt — or explicitly mark specific details "intentionally dropped" with a reason. `pdf` has its own `<search_infrastructure>` block and is included here.

**Acceptance (Phase B):** per-agent redundancy map complete (no instruction removed without a surviving home); search-infra detail relocated into tool descriptions before removal (or explicitly dropped); `test_*_editable_prompts_do_not_duplicate_generated_contract_facts` guard reviewed (its forbidden-fragment list may need extending, not just passing); B layer diffs deletion-only; tool documentation-completeness + parity tests green.

## Phase C — Outcome-first rewrite of base prompts (per agent; all agents)

Loss-full (it changes instructions the model acts on) and accepted as such. Per agent, restructure `prompt.yaml` `content` to the skeleton **Role -> Goal -> Success criteria -> Constraints (invariants only) -> Output/handoff -> Stop rules**; convert process lists to decision rules + stop criteria; collapse repeats; resolve contradictions; reserve MUST/NEVER for true invariants. Curator-voice and the zero-tech-biologist audience constraint apply (these are curator-editable).

**Preserve domain-specific behavior (review finding):** do **not** lean on the single compact "validators own these fields" line — the per-agent base prompts carry the load-bearing no-invention and resolver-workflow rules (e.g. `gene_expression/prompt.yaml:49, :202, :447`). Phase C must keep these, restructured, not collapsed.

**Order:** by measured size/value — gene_expression, gene_extractor, disease_extractor, allele_extractor, phenotype_extractor first; then `gene`/`allele`/`chemical`/`ontology_term`/the other validators/lookups; then the formatters (likely near-zero change). Every agent gets a checklist regardless.

**Required artifact + mechanical checks per agent** (the LLM-read checklist is the weak link, so it is backed by automated, non-live checks):
- **Semantic-coverage checklist** mapping every load-bearing rule to its new home, or an explicit, justified "intentionally dropped as redundant".
- **Fragment-retention test** seeded from the phrase inventory the existing contract tests already assert: each load-bearing phrase must still appear somewhere in the assembled bundle (any layer) post-rewrite, or be on the explicit "intentionally dropped" list.
- **Reason-code-set survival check** for agents enumerating canonical `reason_code` values (e.g. gene_expression lines 69-81) — load-bearing controlled values, not prose.
- **MUST/NEVER contradiction grep** — no co-occurring contradictory absolutes on the same field.
- **Assembled-prompt workflow-invariant assertions** per agent: evidence-span workflow, resolver workflow, stage/finalize workflow, exclusion reason codes, no-invention, validator delegation still present in the rendered bundle.
- **`get_agent_contract` detail-level contract tests** (shared with Phase A).
- **Agent Studio catalog/combined-prompt smoke** so layer rendering stays sane.
- A short **scenario-card checklist** per agent drawn from the existing prompt examples (prior-work citation, methods-only mention, rescue/marker exclusion, negative expression, ambiguous ontology/provider) — a manual sanity pass, not a live run.

**Length/verbosity:** prefer API-level control (`text.verbosity`, reasoning effort) over prose where the run config allows; exact wiring confirmed during planning. If API control is not readily available, leave prose length rules as-is rather than expanding scope.

**Acceptance (Phase C, per agent):** checklist + mechanical checks above all pass/committed; measurable size reduction (or recorded "already lean"); the two LLM reviews find no lost load-bearing rule and no introduced contradiction.

## Verification & review (per phase)

Chris's choices: **structural + the two reviews; no live A/B extraction.**

- **Existing guards stay green / re-baselined:** tool-catalog parity, documentation-completeness, manifest, and the contract-encoding tests named in Phase A acceptance.
- **New tooling:** a committed prompt-size report utility (per-layer sizes + a soft budget to catch regressions) and the per-agent mechanical checks listed in Phase C.
- **Per task:** the subagent-driven two-stage review — spec-compliance, then code-quality — using **Opus 4.8**.
- **Per phase:** an **Opus 4.8 full review** and **`/external-llm-code-review`** with **Codex on gpt-5.5 / high reasoning**, both explicitly tasked to confirm no load-bearing rule was lost and no contradiction introduced; the external reviewer also assesses Phase C behavioral risk. External output shown to Chris verbatim.
- **UI:** Agent Studio renders `core_generated` as "Generated Contract" / "Output structure" (`AgentDetailsPanel.tsx`, `PromptWorkshop.tsx`) — display-only, no code change, but the combined-prompt smoke verifies it still renders sanely.

## Risks

- **Phase C is loss-full across all agents (accepted).** Structural tests cannot prove extraction quality is unchanged. Mitigations: the per-agent semantic-coverage checklist *backed by the mechanical checks above* (the key hardening from review — interaction regressions, not just missing lines), the gpt-5.5-high external review, and the Opus 4.8 review. A live A/B eval remains available as a later per-agent opt-in.
- **Expanded scope = more agents = longer effort.** Mitigated by phasing, size-proportional effort, and review gates between phases.
- **Snapshot/contract-test churn is core work, not incidental** (see Phase A acceptance). Fixtures are regenerated and reviewed, never blindly accepted.

## Logistics

- Branch: `agent-prompt-stack-optimization` (off `main`). Independent of PR #446.
- Phases ship as independent commit sets, each gated by the per-phase reviews before the next begins.
- Execution: subagent-driven (Opus implementers + two-stage reviews per task).

## Decisions resolved

- Scope: **A + B + C across ALL agents** — extractors, validators, lookups, formatters (Chris, expanded this turn from extractors-only).
- Structured-output prose (A2) is **in scope** for all `output_schema` agents (schema already enforced via `output_type`, so the prose is redundant).
- Verification: **structural + Opus 4.8 review + gpt-5.5-high external review**; **no live A/B** (Chris). Phase C loss-full **accepted** (Chris).
- Branch: **new branch off main** (Chris).
- Spec reviewed by Opus 4.8 + Codex gpt-5.5/high; all must-fix findings folded in (manifest-claim correction, all-agents/`output_schema` scope, `detail_level="detail"` acceptance, enrich-tool-descriptions-before-removal, soften "1:1", mechanical Phase C checks, deletion-only A/B diffs, custom-agent + UI notes).
