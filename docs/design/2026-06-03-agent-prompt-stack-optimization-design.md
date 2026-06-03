# Agent Prompt-Stack Optimization — Design Spec

Date: 2026-06-03. Status: **DESIGN APPROVED by Chris.** Scope: full (Phase A + B + C). Verification: structural + two LLM reviews (no live A/B extraction). Branch: `agent-prompt-stack-optimization` (off `main`, independent of the doc-migration/UI-redesign branch in PR #446).

## Context & problem

The curation agents — extractors especially — carry a very large fixed system-prompt overhead on every model call. Measured per-layer sizes for the **gene_expression extractor** (the worst case), before document content and tool schemas:

| Layer (UI label) | chars | ~tokens | Editable? |
|---|---:|---:|---|
| Built-in instructions (`core_static`) | 234 | ~60 | locked |
| Generated Contract / "Output structure" (`core_generated`) | 9,023 | ~2,255 | locked |
| Template instructions (`base_prompt`, from `prompt.yaml`) | 31,665 | ~7,916 | editable |
| Species & group rules (WB) | 7,304 | ~1,826 | editable |
| Your custom instructions (overlay) | varies | — | editable |
| **Total (gene_expression + WB, no overlay)** | **~48.2K** | **~12,050** | |

Cross-agent pattern: the **extractor base prompts are the dominant cost** (gene_expression ~7.9K tok, gene_extractor ~7.1K, disease_extractor ~5.0K, allele_extractor ~4.3K, phenotype_extractor ~4.1K); validators/lookups are far leaner (~1.2K generated + 1.5–4K base).

Two distinct problems were confirmed by reading the assembled text verbatim:

1. **The generated contract (9K) is almost entirely audit detail the model does not act on.** It is *not* a schema blob (the structured-output injection is 0 chars for this agent — it finalizes via a tool, not `response_format`). It is: the full 21-tool inventory (already supplied as tool schemas, and re-listed again in the base prompt), the evidence policy (repeated near-verbatim in the base prompt), ~37 `field -> validator_binding` mappings, and ~13 active-validator-binding lines — several carrying literal CURIE allow-lists (one has 26 UBERON IDs inline). The model does not choose or run validators; the backend does. Notably, the contract itself says *"Detailed field, tool, schema, validator, and ontology facts are served by the read-only `get_agent_contract` helper"* — and then inlines all of those facts anyway, contradicting its own design.
2. **The base prompts are large and overlap the other layers.** Evidence rules, the full tool list, and a generic ~2K `<search_infrastructure>` block are repeated across the generated contract, the base prompt, and (for tools) the tool schemas now sourced from `bindings.yaml`.

### OpenAI GPT-5.5 guidance (load-bearing, official sources)

- *"Start with the smallest prompt that preserves the product contract… shorter, outcome-first prompts usually work better than process-heavy stacks."*
- *"Poorly-constructed prompts containing contradictory or vague instructions can be more damaging to GPT-5 than to other models, as it expends reasoning tokens searching to reconcile the contradictions."* — bloat can *lower* accuracy, not just raise cost.
- *"Put most tool-specific guidance in the tool descriptions themselves."*
- *"Don't include output schema definitions in the prompt — use Structured Outputs instead"*; don't restate the field list in prose.
- Control length/verbosity via `text.verbosity` / `reasoning_effort`, not prose; reserve MUST/NEVER for true invariants.

Sources: `developers.openai.com/api/docs/guides/prompt-guidance`, `/guides/latest-model`, the GPT-5 and GPT-5.2 cookbook prompting guides, and `/guides/structured-outputs`.

## Goals / non-goals

**Goals.** Reduce system-prompt overhead (extractors first); align the stack with the GPT-5.5 guidance; lose **no** load-bearing curation rule; preserve all audit/provenance capability.

**Non-goals.** Changing curation semantics or accuracy *intent*; validator logic; the Agent Studio UI; live A/B extraction evals (deferred, available as a later per-agent opt-in). Submission-gating, schema, and validator bindings are unchanged.

## Principles (the rules we apply everywhere)

1. Smallest prompt that preserves the contract.
2. Outcome-first: success criteria + decision rules + stop rules over step-by-step process.
3. No duplication and no contradiction across layers.
4. Tool-usage detail lives in tool descriptions (`bindings.yaml`), not restated in the system prompt.
5. Schema / field / validator / ontology detail lives in structured outputs + the on-demand `get_agent_contract` tool, not inlined.
6. `MUST` / `NEVER` / `ALWAYS` only for true invariants (safety, required fields, no-invention).

## Current architecture (what assembles the prompt)

`backend/src/lib/prompts/assembly.py` builds an ordered `PromptLayerBundle`:
`core_static` -> `core_generated` -> `base_prompt` -> `group_rules` -> `curator_overlay` -> `runtime_context`.

- `core_generated` = `_build_compact_runtime_contract(agent)` (the `## Generated Runtime Contract` block, built from tool inventory, tool-call policy, `TOOL_POLICY_SUMMARIES`, and `_build_domain_pack_contract_lines`) **plus** an optional structured-output instruction (only when `agent.output_schema` is set — empty for the builder-tool extractors).
- `base_prompt` is the agent's `packages/alliance/agents/<agent>/prompt.yaml` `content`, seeded into `PromptTemplate` rows.
- `group_rules` come from `packages/alliance/agents/<agent>/group_rules/<group>.yaml`.
- `get_agent_contract` (`backend/src/lib/agent_contracts.py`, tool wrapper in `backend/src/lib/openai_agents/tools/agent_contract.py`) already serves topics `tools`, `output_schema`, `domain_envelope`, `validator_bindings`, `ontology_constraints`, `field` — i.e. exactly the detail Phase A removes from the prompt.

## Phase A — Slim the generated runtime contract (`core_generated`) — backend only

Edit `_build_compact_runtime_contract` and `_build_domain_pack_contract_lines` (and `TOOL_POLICY_SUMMARIES`) in `assembly.py`.

**Keep (the model acts on these):**
- The `## Generated Runtime Contract` heading.
- The required-tool-call policy line (e.g. "call at least one document retrieval tool before final output").
- The evidence policy summary (the `record_evidence` block) — kept **here only**; removed from the base prompt in Phase B.
- The runtime safety rule (no inventing CURIEs; unresolved validator-bound candidates allowed through).
- A **single** compact line: "Validators own these fields; do not invent their identifiers — <short list of object.field, capped, "+N more">." This is the only validator information the model needs to act on (it tells the model not to fabricate validator-owned values).
- The existing one-line pointer: detailed field/tool/schema/validator/ontology facts are available via `get_agent_contract`.

**Remove from the prompt text (still served by `get_agent_contract` + retained in the layer manifest for audit):**
- The full tool-inventory enumeration (the model already receives tool schemas).
- Schema/provider refs line.
- The extractor-envelope-objects `required[...]` dump.
- The full `field -> binding` map.
- The per-binding `targets … policy … selectors …` lines, including the literal CURIE allow-lists.

**Nothing is deleted from the system** — only from the model-facing prompt text. `get_agent_contract` topics map 1:1 to every removed item; the `PromptLayer` manifest (hash, `source_ref`) is unchanged in shape.

**Acceptance (Phase A):**
- `gene_expression` `core_generated` ≤ ~1,500 chars (from ~9,023); other extractors cut proportionally.
- For each removed datum, a test asserts it is still returned by the corresponding `get_agent_contract` topic.
- Tool-catalog parity, documentation-completeness, and manifest tests stay green.
- Assembly unit tests updated to the new compact contract.

## Phase B — De-duplicate across layers (base prompts + tool descriptions)

For each extractor `prompt.yaml`, remove blocks now redundant with another layer, justified by this **redundancy map** (every removal must trace to a surviving home):

| Removed from base prompt | Now lives in |
|---|---|
| `Available tools:` re-listing | tool schemas (always sent) + the kept required-tool-call line in `core_generated` |
| Duplicated evidence-policy prose (`record_evidence` mechanics already in `core_generated`) | `core_generated` evidence policy (Phase A keep) |
| Generic `<search_infrastructure>` platform explanation (~2K) | tool-specific parts -> `search_document` / `read_section` / `read_subsection` / `read_chunk` descriptions in `packages/alliance/tools/bindings.yaml`; non-tool-specific generic background -> dropped |

Files: each extractor `packages/alliance/agents/<agent>/prompt.yaml`; `packages/alliance/tools/bindings.yaml` (search/read tool descriptions).

**Acceptance (Phase B):** the redundancy map is complete (no instruction removed without a surviving home); tool documentation-completeness + parity tests green; before/after layer diff shows only redundant content removed.

## Phase C — Outcome-first rewrite of the big extractor base prompts — per agent, careful

This phase is **loss-full** (it changes instructions the model acts on) and is accepted as such; the goal is a leaner, clearer prompt that preserves every load-bearing rule.

Per agent, restructure `prompt.yaml` `content` to the recommended skeleton:
**Role -> Goal -> Success criteria -> Constraints (invariants only) -> Output/handoff -> Stop rules.**
Convert step-by-step process lists into outcome statements + decision rules + explicit stop criteria; collapse repeated rule statements; resolve contradictions; reserve MUST/NEVER for true invariants. Curator-voice and the zero-tech-biologist audience constraint still apply (these are curator-editable).

**Order:** gene_expression, gene_extractor, disease_extractor, allele_extractor, phenotype_extractor; other agents only if clearly warranted.

**Required artifact per agent — semantic-coverage checklist.** A committed table mapping every load-bearing rule in the old prompt to its location in the new prompt (or an explicit, justified "intentionally dropped as redundant"). This is the primary guard against silent rule loss under structural-only verification, and the primary input to the LLM reviews.

**Length/verbosity:** prefer API-level control (`text.verbosity`, reasoning effort) over prose length instructions where the run configuration allows. The exact run-config location is to be confirmed during planning; if API control is not readily wired, leave prose length rules as-is rather than expanding scope.

**Acceptance (Phase C, per agent):** semantic-coverage checklist complete and committed; measurable size reduction; the two LLM reviews (below) find no lost load-bearing rule and no introduced contradiction.

## Verification & review (applies per phase)

Chris's choices: **structural + the two reviews; no live A/B extraction.**

- **Existing guards stay green:** tool-catalog parity, documentation-completeness, manifest test, assembly tests.
- **New tooling:** a committed prompt-size report utility (productizing the per-layer measurement used in analysis) that prints per-layer sizes and enforces a *soft* budget to catch regressions; and an **exact before/after prompt-layer diff** artifact per affected agent, committed for review.
- **Per task:** the subagent-driven two-stage review — spec-compliance, then code-quality — using **Opus 4.8**.
- **Per phase:** an **Opus 4.8 full review** of the phase, and **`/external-llm-code-review`** run with **Codex on gpt-5.5 / high reasoning** (the same model family that runs these prompts). Both reviewers are explicitly tasked to confirm no load-bearing rule was lost and no contradiction was introduced; the external reviewer also assesses behavioral risk for Phase C. The external review output is shown to Chris verbatim (per that skill's rules).

## Risks

- **Phase C is loss-full (accepted).** Structural tests cannot prove extraction quality is unchanged. Mitigations: the per-agent semantic-coverage checklist, the gpt-5.5-high external review hunting behavioral regressions, and the Opus 4.8 review. A live A/B eval remains available as a later per-agent opt-in.
- **`get_agent_contract` reliance.** Removed detail is fetched on demand; most of it (validator selectors, CURIE allow-lists, per-field binding maps) is audit detail the model never acted on. The kept "validators own these fields; do not invent" line preserves the only behavior that depended on inlined validator info (no-invention). The reviews verify this is sufficient.
- **Snapshot churn.** Assembly/contract snapshot fixtures will change; updates are part of each phase, regenerated and reviewed, not blindly accepted.

## Logistics

- Branch: `agent-prompt-stack-optimization` (off `main`). Independent of PR #446.
- Phases ship as independent commit sets, each gated by the per-phase reviews before the next begins.
- Execution: subagent-driven (Opus implementers + two-stage reviews per task).

## Decisions resolved

- Scope: **A + B + C** (Chris).
- Verification: **structural + Opus 4.8 review + gpt-5.5-high external review**; **no live A/B** (Chris). Phase C loss-full **accepted** (Chris).
- Branch: **new branch off main** (Chris).
