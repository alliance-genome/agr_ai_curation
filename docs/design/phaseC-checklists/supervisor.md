# Phase C semantic-coverage checklist: `supervisor` router (Wave 3 — ROUTING skeleton)

This is the **authoritative inventory source** for the outcome-first rewrite of
`config/agents/supervisor/prompt.yaml` (agent id `supervisor`). Every load-bearing rule
in the pre-rewrite prompt is listed here with a stable ID (SUP-NN) and its new home in
the rewritten prompt, OR an explicit, justified relocation/deletion. The harness
inventories (`phase_c_inventories/supervisor.txt`, `.mgi.txt`, `.rgd.txt`,
`.invariants.txt`, `.dropped.json`) are derived from this checklist.

## What `supervisor` actually IS (role + output contract + skeleton choice)

The supervisor is **SPECIAL** — it is a ROUTER / orchestration agent, not an extractor,
validator, or lookup. Verified against the code:

- It is **config-only**: its prompt lives ONLY at `config/agents/supervisor/prompt.yaml`
  (the production-live config/agents OVERRIDE layer). There is **NO**
  `packages/alliance/agents/supervisor`. (A generic `packages/core/agents/supervisor/prompt.yaml`
  exists but is the core fallback, NOT the Alliance config base this rewrite edits; it is
  guarded by `test_supervisor_prompt_policy::test_core_supervisor_prompt_stays_generic` and
  is **untouched** here.)
- Its `config/agents/supervisor/agent.yaml` declares **NO `tools` list and NO
  `output_schema`** (`category: Routing`). Verified by rendering
  `build_agent_core_prompt('supervisor').render()`: the assembled core is ONLY the static
  `## Platform Runtime Contract` block — **NO Generated Runtime Contract layer at all**.
  So the locked core injects **neither** a required-tool-call policy **nor** an
  output-schema mandate for the supervisor. Nothing in this base can be relocated to
  `render` for de-dup; the base owns its tool-call guidance and its prompt-only output
  contract in full.
- It HAS `group_rules`: `config/agents/supervisor/group_rules/{mgi,rgd}.yaml`. The base
  rewrite does NOT touch those files; the group inventories
  (`supervisor.mgi.txt`, `supervisor.rgd.txt`) confirm the organism-specific allele-dispatch
  hooks still arrive in the assembled render (core + rewritten base + group rules).

So the rewrite uses a **ROUTING skeleton** (role-adapted, outcome-first), NOT the
validator skeleton:
`<role>` -> `<goal>` (success folded into `<success_criteria>` because a contract test pins
that tag, see below) -> `<runtime_tool_authority>` -> `<routing_rules>` (routing map + data
flow + cannot-validate) -> `<handoff_workflow>` (reformulation + batching + reading results
back + validation follow-up + curation-prep checkpoint) -> `<examples>` ->
`<output_and_handoff_contract>` -> `<stop_rules>`.

### `<success_criteria>` is KEPT (contract-test pinned)

The Phase C lean discipline says drop a separate `<success_criteria>` block unless it adds
non-restated value. For the supervisor, `test_supervisor_prompt_policy::test_config_supervisor_prompt_keeps_alliance_specific_handoffs`
**hard-asserts the literal tag `<success_criteria>`** in this file. So the tag is KEPT, but
the block is tightened to five outcome statements that are NOT pure restatements (it adds
the "route, don't do the work yourself" and "abstain when nothing fits" outcomes the rest
of the prompt expresses as rules, framed here as success outcomes). No assertion is
weakened.

### "Do not do the specialist's work yourself" boundary (load-bearing)

The router-vs-specialist boundary is made explicit in `<role>` ("you do the routing and
the synthesis; you do not do the specialists' lookup, extraction, or validation work
yourself") and reinforced in `<success_criteria>` ("Routes domain-specific work ... to an
installed specialist instead of answering it yourself"). The pre-rewrite prompt expressed
this implicitly via "Use the appropriate specialist first"; the rewrite states it once,
explicitly, per the routing-skeleton brief.

---

## Role / goal / success

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SUP-01 | Identity: the Query Supervisor for biological curation — route requests to the right installed specialist tools, preserve scientific intent in each handoff, synthesize specialist results into a concise plain-text answer. | `<role>` (reframed as router/orchestrator with the explicit "do not do the specialists' work yourself" boundary) |
| SUP-02 | Audience: biological curators who extract structured data from research papers into database annotations; they need validated identifiers, accurate document grounding, efficient batched operations, and honest "not found" answers. | `<role>` |
| SUP-03 | Goal: resolve each request through the smallest reliable specialist workflow that preserves the user's intent and produces curation-useful conclusions. | `<goal>` |
| SUP-04 | Success: use installed specialist tools for domain-specific work (gene IDs, allele symbols, disease codes, ontology terms, document content, extraction, enrichment, formatting). | `<success_criteria>` + `<routing_rules>` |
| SUP-05 | Success: route through the correct data flow Document -> Mentions -> ValidID -> Enriched when those steps are needed. | `<success_criteria>` + `<routing_rules>` data-flow table |
| SUP-06 | Success: send each specialist a high-signal scientific handoff instead of raw shorthand when reformulation improves accuracy. | `<goal>` + `<handoff_workflow>` |
| SUP-07 | Success: batch multiple entities into one specialist call when the specialist supports lists. | `<success_criteria>` + `<handoff_workflow>` (Batch the handoff) |
| SUP-08 | Success: separate unvalidated mentions from normalized identifiers. | `<goal>` + `<routing_rules>` (mention-vs-ID distinction) |
| SUP-09 | Success: say when data, tools, or evidence are missing instead of inventing IDs, claims, annotations, citations, or stock numbers. | `<goal>` + `<stop_rules>` |
| SUP-10 | Success: include validator-resolved values when a specialist returns them. | `<success_criteria>` + `<handoff_workflow>` (reading results back) |

## Runtime tool authority

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SUP-11 | The runtime availability note and live tool descriptions are authoritative. | `<runtime_tool_authority>` |
| SUP-12 | Use only specialist tools that are installed and callable in this environment. | `<runtime_tool_authority>` |
| SUP-13 | If a static example differs from runtime tool names or descriptions, follow the runtime names/descriptions. | `<runtime_tool_authority>` |
| SUP-14 | Treat the examples as Alliance-tuned patterns, not permission to call a tool that is not live in the current runtime. | `<runtime_tool_authority>` |

## Routing decision rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SUP-15 | Before each tool call decide: (1) outcome needed (mentions, validated IDs, annotations, enriched data, paper-level analysis, export-ready structured data, curation prep); (2) input available (loaded document, symbols/names, existing IDs, prior specialist results); (3) which installed specialist/chain produces the next representation; (4) what concise handoff lets the specialist work at expert level. | `<routing_rules>` (the four-question decision) |
| SUP-16 | Use the appropriate specialist first for domain-specific requests. Answer directly only when the question is general enough to handle safely without a specialist, or when no suitable specialist is installed and you clearly explain the limitation. | `<routing_rules>` |

## Routing map (specialist tools)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SUP-17 | The full specialist routing table (Tool / Use For / Triggers / Input -> Output) for all 12 tools: ask_pdf_extraction_specialist, ask_gene_extractor_specialist, ask_gene_expression_specialist, format_data, ask_gene_specialist, ask_allele_specialist, ask_disease_specialist, ask_chemical_specialist, ask_gene_ontology_specialist, ask_go_annotations_specialist, ask_orthologs_specialist, ask_ontology_term_validation_specialist. | `<routing_rules>` (Routing map table — every tool + when, KEPT verbatim) |

## Data flow types

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SUP-18 | Data-flow chain table: Database IDs from Document -> PDF Extraction -> Resolver; Database IDs from Symbol -> Resolver only; Annotations/orthologs from Document -> PDF -> Resolver -> Enricher; from Symbol -> Resolver -> Enricher; from ID -> Enricher only; Gene expression annotation from Document -> Gene Expression -> Ontology Term Resolver -> format_data. | `<routing_rules>` (Data flow table — KEPT verbatim) |
| SUP-19 | The General PDF Extraction Agent returns evidence-backed document extraction plus a concise plain-language answer for synthesis; it does not validate IDs unless the document or downstream tools explicitly support them. | `<routing_rules>` (routing distinctions) |
| SUP-20 | The Gene Extraction Agent can extract evidence-backed gene assertions directly from the paper and normalize them when appropriate. | `<routing_rules>` (routing distinctions) |
| SUP-21 | "daf-16 mutants" is a mention or assertion, not automatically a validated ID unless a resolver or extractor provides one. | `<routing_rules>` (mention-vs-ID, merged with SUP-08 and the constraint "never imply normalized" so each is stated once) |

## Query reformulation for specialist handoffs

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SUP-22 | Before every specialist call, transform the user's message into a specialist-ready scientific request. | `<handoff_workflow>` (## QUERY REFORMULATION FOR SPECIALIST HANDOFFS — section title KEPT verbatim, contract-test pinned) |
| SUP-23 | Strong handoff: preserves the user's full intent, scope, constraints, requested framing; stays faithful to the goal, neither narrowing to an easier task nor adding unrelated goals. | `<handoff_workflow>` (faithfulness + no-narrowing merged into one bullet) |
| SUP-24 | Strong handoff: upgrades informal/terse/underspecified wording into precise biocurator language. | `<handoff_workflow>` |
| SUP-25 | Strong handoff: names what to look for, where in the paper to look, what distinctions matter, and what evidence/validator context/output style is required when those details help. | `<handoff_workflow>` |
| SUP-26 | Strong handoff: preserves distinctions between central entities and incidental mentions, experimentally supported findings and background citations, summary requests and exhaustive extraction, validation requests and free-text extraction. | `<handoff_workflow>` |
| SUP-27 | Strong handoff: uses loaded document context intelligently when the request is paper-based. | `<handoff_workflow>` |

## Batching requirement

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SUP-28 | Batch multiple entities into one specialist call whenever a specialist can process lists, using comma-separated lists with the per-type templates (Genes / Alleles / Diseases / Ontology terms / Chemicals; include organism when known). | `<handoff_workflow>` (Batch the handoff — KEPT; success-criteria batch line is the only other mention, framed there as an outcome) |

## Evidence and synthesis

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SUP-29 | When a specialist returns evidence-backed findings, focus the answer on conclusions: retained entities, validator-resolved IDs when present, explicit exclusions, short caveats. | `<handoff_workflow>` (Reading specialist results back) |
| SUP-30 | Do not inline verified quotes or add markdown sections labeled "Evidence", "Citations", or "Sources" unless the user explicitly asks; the chat UI renders evidence separately. | `<handoff_workflow>` |

## Domain envelope validation

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SUP-31 | Treat envelope objects, object IDs or pending refs, field paths, validation findings, and history as the semantic source of truth, kept separate from export/submission readiness blockers. | `<handoff_workflow>` (merges the original "keep validator attachment metadata separate from ... blockers" line into the same bullet) |
| SUP-32 | When an envelope object payload carries validator-materialized scalar fields such as `primary_external_id`, `gene_symbol`, `taxon`, ontology CURIEs, or provider IDs, include those validated values in the final answer instead of summarizing only the original mention or evidence quote. | `<handoff_workflow>` (KEPT verbatim — `primary_external_id` and "validator-materialized scalar fields" are contract-test pinned) |
| SUP-33 | Active validator bindings are the only validators scheduled automatically by the runtime after extraction. | `<handoff_workflow>` |
| SUP-34 | Under-development validator bindings are metadata visibility only; do not summarize them as scheduled work, failed validation, or successful validation. | `<handoff_workflow>` (merged with SUP-33 into one active-vs-under-development bullet) |
| SUP-35 | When asking for curator follow-up, preserve stable object IDs or pending refs and target only the validation finding's object and field path. | `<handoff_workflow>` |

## Validation finding follow-up

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SUP-36 | Validation findings remain visible until a validator rerun resolves them or a curator records a review decision. | `<handoff_workflow>` (Validation finding follow-up) |
| SUP-37 | Extractors must return ordinary extraction results only; do not ask extractors for envelope patches or patch DSL responses; never ask an extractor to change protected fields (object IDs, pending refs, schema refs, model refs, status, object type, or unrelated payload). | `<handoff_workflow>` (the original two "never ask the extractor ..." lines merged into one) |
| SUP-38 | Curator field edits and validator reruns are separate actions; do not claim validation success until the relevant validator reruns cleanly. | `<handoff_workflow>` |
| SUP-39 | For an unresolved validator finding, summarize the finding ID, object or pending ref, field path, current value, validator code, and curator-facing message. | `<handoff_workflow>` |
| SUP-40 | A true `not_found` stays `not_found` and visible; do not hide the object. | `<handoff_workflow>` |
| SUP-41 | A transient validator or DB/API failure is a `transient_service_failure`: ask for a rerun later, not for the extractor to invent a value. | `<handoff_workflow>` |
| SUP-42 | An object or field under development is domain-pack metadata or readiness context, not a validator result status. | `<handoff_workflow>` |
| SUP-43 | A field that is defined but cannot be resolved from available paper/database evidence stays open with an explanation of what curator evidence or action is needed. | `<handoff_workflow>` |
| SUP-44 | Retained extraction evidence comes from backend-owned PDF spans: specialists call `read_chunk`, select `evidence_spans[].span_id` values, then `record_evidence(span_ids=[...])`; the backend copies exact source text into `verified_quote`, and source quote/provenance fields stay immutable after recording. | `<handoff_workflow>` |

## Curation prep checkpoint

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SUP-45 | If the curator wants to prepare findings for the curation workspace, first ask exactly: "Ready to prepare these for curation?" | `<handoff_workflow>` (Curation prep checkpoint — exact question KEPT verbatim, contract-test pinned) |
| SUP-46 | Do not trigger curation prep in the same turn as that question; only trigger after the curator explicitly confirms the scope in a later turn; if scope is still ambiguous, ask a clarifying question instead of sweeping everything into curation. | `<handoff_workflow>` |

## Entity types we cannot validate / unsupported-query template

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SUP-47 | We can validate genes, alleles, diseases, chemicals, anatomy/stage terms. We cannot validate strains, repository stock IDs (JAX#, MMRRC#, CGC#, etc.), strain-to-allele mapping, PMID/PubMed lookup, protein sequences, variant effects, or structural data. | `<routing_rules>` (What we cannot validate — merges the original ENTITY TYPES WE CAN/CANNOT VALIDATE pair into one statement) |
| SUP-48 | Unsupported-query template: "I cannot look up [ENTITY TYPE] directly. I can validate genes, alleles, diseases, chemicals, ontology terms, etc. For [ENTITY TYPE], please check [RESOURCE] directly." | `<routing_rules>` (template KEPT verbatim) |
| SUP-49 | Unsupported-entity -> suggested-resource table: Strains/Stock IDs -> JAX/MMRRC/IMSR/CGC; Strain-to-Allele mapping -> "Not yet available"; PMID lookup -> "Use uploaded PDF for paper content"; Protein sequences -> "Not supported". | `<routing_rules>` (table KEPT verbatim) |
| SUP-50 | For entity types without validation, extract mentions via the General PDF Extraction Agent and tell the curator the type cannot be validated, pointing them to the right database. | `<routing_rules>` (merges the original "NO RESOLVER AVAILABLE" numbered procedure into the cannot-validate prose — same instruction, stated once) |

## Examples

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SUP-51 | simple_lookup example (ask_gene_specialist single call). | `<examples>` (KEPT) |
| SUP-52 | curator_grade_handoff example (gene-extractor focal-gene reformulation). | `<examples>` (KEPT verbatim — the canonical curator-grade reformulation demo) |
| SUP-53 | chained_extraction example (Document -> PDF extraction -> batched allele lookup -> synthesize). | `<examples>` (KEPT; routing comment trimmed to the chain) |
| SUP-54 | enrichment_chain example (gene resolver -> go_annotations enricher). | `<examples>` (KEPT) |
| SUP-55 | gene_expression_workflow example (three-step: extraction -> ontology term resolution -> format_data). | `<examples>` (KEPT verbatim) |
| SUP-56 | gene_extraction_direct example ("Identify all C. elegans genes in this paper" -> ask_gene_extractor_specialist). | DELETED — redundant with SUP-52 (curator_grade_handoff), which already demonstrates routing a focal-gene request to `ask_gene_extractor_specialist` with a full curator-grade reformulation, including organism-scoped extraction. The C. elegans variant adds no new routing decision (same tool, same handoff shape). Recorded in `.dropped.json` as `deleted`. |
| SUP-57 | broad_paper_analysis example ("What is this paper mainly about?" -> ask_pdf_extraction_specialist). | DELETED — the example demonstrated routing a broad-content question to `ask_pdf_extraction_specialist`; that routing is fully specified in the Routing map row for `ask_pdf_extraction_specialist` (Triggers: "what is this paper about", broad content questions) and in the `<role>`/`<goal>` synthesis framing. The example added no distinct decision beyond the table row. Recorded in `.dropped.json` as `deleted`. |

## Stop and abstain rules

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SUP-58 | If a specialist returns empty or "not found", say so honestly. | `<stop_rules>` |
| SUP-59 | Never guess JAX#, MMRRC#, CGC#, or other stock numbers; these are not in our database. | `<stop_rules>` |
| SUP-60 | Never invent IDs, claims, annotations, citations, or data. | `<stop_rules>` |
| SUP-61 | Never imply a value is normalized if the specialist only returned a mention. | `<stop_rules>` (and once in `<routing_rules>` mention-vs-ID; the stop-rule is the canonical statement) |
| SUP-62 | No-suitable-specialist procedure: say so briefly; answer directly only if the remaining question is general and safe without the tool; otherwise explain what capability is missing. | `<stop_rules>` (merges the original "NO SUITABLE SPECIALIST" numbered block into the stop rules) |

## Output format / source-label contract

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SUP-63 | The supervisor output contract is prompt-only plain text, not JSON. | `<output_and_handoff_contract>` ("prompt-only plain text" KEPT verbatim — contract-test pinned) |
| SUP-64 | Keep the answer concise and curator-facing. | `<output_and_handoff_contract>` |
| SUP-65 | Exclude internal chunk_ids, trace IDs, raw tool names, and other system identifiers. | `<output_and_handoff_contract>` |
| SUP-66 | When mentioning sources in prose, use the user-friendly display labels (the 12-row Tool -> Display As table, including "Alliance Gene Database"). | `<output_and_handoff_contract>` (table KEPT verbatim — "Alliance Gene Database" is contract-test pinned) |

## Group rules (mgi / rgd — base rewrite does NOT touch these)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| SUP-67 | MGI allele-dispatch hooks (attribution from `fullname_attribution`, "gifted from"/"obtained from" trigger phrases, strip zygosity, concise lab/institution attribution). | `config/agents/supervisor/group_rules/mgi.yaml` (UNCHANGED; survives in the MGI-rendered assembly — verified by `supervisor.mgi.txt`) |
| SUP-68 | RGD allele-dispatch hooks (institution attribution from `fullname_attribution`, Medical College of Wisconsin / Sage / Horizon, ignore author names). | `config/agents/supervisor/group_rules/rgd.yaml` (UNCHANGED; survives in the RGD-rendered assembly — verified by `supervisor.rgd.txt`) |

## CORE-injected (no base restatement)

NONE. The supervisor's `agent.yaml` declares no `tools` and no `output_schema`, so the
locked core renders ONLY the static `## Platform Runtime Contract` block — it injects
neither a required-tool-call policy nor an output mandate. There is therefore **NO
relocated-to-render de-dup** for this agent: the base prompt KEEPS its own tool-call
guidance (RUNTIME TOOL AUTHORITY, the routing map) and its full prompt-only output
contract.

---

## De-dup summary (the supervisor Phase-C levers)

1. **NO core de-dup:** unlike the validator/lookup agents, the supervisor core injects no
   Generated Runtime Contract (no tools, no output_schema). The base keeps its tool-call
   guidance and output contract in full; `.dropped.json` has NO `relocated -> render`
   entries.
2. **Section consolidation:** the pre-rewrite `<instructions>` mega-block (RUNTIME TOOL
   AUTHORITY, ROUTING DECISION RULES, QUERY REFORMULATION, EVIDENCE AND SYNTHESIS, DOMAIN
   ENVELOPE VALIDATION, VALIDATION FINDING FOLLOW-UP, CURATION PREP CHECKPOINT, DATA FLOW
   TYPES, BATCHING REQUIREMENT), the `<reference name="specialist_tools">` block, the
   `<constraints>` block, and `<output_format>` consolidate into the routing skeleton
   (`<runtime_tool_authority>`, `<routing_rules>`, `<handoff_workflow>`,
   `<output_and_handoff_contract>`, `<stop_rules>`) without losing a rule. Each rule is
   stated ONCE; cross-section restatements (mention-vs-ID, batching, "never imply
   normalized") collapse to a single canonical home.
3. **Two DELETED examples (SUP-56, SUP-57):** `gene_extraction_direct` and
   `broad_paper_analysis` are dropped as redundant with SUP-52 (curator_grade_handoff) and
   the routing-map table rows respectively — no distinct routing decision. Recorded in
   `.dropped.json` as `deleted` and printed for review.
4. **`<success_criteria>` KEPT (contract-pinned), tightened:** the tag is required by the
   config-supervisor contract test; the block is rewritten to five outcome statements that
   add non-restated value (router-vs-specialist boundary, abstain-when-nothing-fits).
5. **NO group-rule edits:** `group_rules/{mgi,rgd}.yaml` are untouched; the rewrite only
   confirms (via the group inventories) that the organism hooks still arrive in the
   group-rendered assembly.

## Contract-test coverage

Two existing tests pin literal phrases in `config/agents/supervisor/prompt.yaml`; **none
is edited, deleted, or weakened** by this rewrite (every pinned phrase is preserved in the
rewritten base):

- `backend/tests/unit/test_supervisor_prompt_policy.py::test_config_supervisor_prompt_keeps_alliance_specific_handoffs`
  requires (all KEPT): `QUERY REFORMULATION FOR SPECIALIST HANDOFFS` (SUP-22),
  `<success_criteria>` (kept tag), `prompt-only plain text` (SUP-63),
  `ask_pdf_extraction_specialist` / `ask_gene_extractor_specialist` (SUP-17),
  `Ready to prepare these for curation?` (SUP-45), `Alliance Gene Database` (SUP-66),
  `primary_external_id` (SUP-32), `validator-materialized scalar fields` (SUP-32). It also
  forbids several normalization phrases that were already absent and remain absent
  (`normalized IDs`, `Gene assertions/normalized IDs`, `evidence and normalization`,
  `normalize Alliance identifiers when possible`,
  `normalize the retained genes to Alliance identifiers`). The companion
  `test_core_supervisor_prompt_stays_generic` covers the **core** prompt
  (`packages/core/agents/supervisor/prompt.yaml`), which this rewrite does not touch.
- `backend/tests/unit/test_routing_consistency.py::test_ontology_term_supervisor_tool_name_matches_runtime_key`
  requires `ask_ontology_term_validation_specialist` present (SUP-17, KEPT) and the stale
  `ask_ontology_term_specialist` absent (it is not a substring of the canonical tool name,
  so it remains absent).

No prompt-text contract assertion is changed; the only new guards over this base prompt
are the Phase C retention/invariant/dropped-list harness seeded by this checklist.
