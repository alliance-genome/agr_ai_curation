# Phase C semantic-coverage checklist: `pdf` (Wave 2, the last extractor)

This is the **authoritative inventory source** for the outcome-first rewrite of
`packages/alliance/agents/pdf/prompt.yaml`. Every load-bearing rule in the
pre-rewrite prompt is listed here with a stable ID (PDF-NN) and its new home in
the rewritten prompt, OR an explicit, justified relocation/deletion. The harness
inventories (`phase_c_inventories/pdf.txt`, `.invariants.txt`, `.dropped.json`)
are derived from this checklist.

Legend for "New home":
- A `<section>` name is a section of the **rewritten** base prompt.
- `CORE` means the locked Generated Runtime Contract
  (`assembly.py::_build_core_generated_content`) already injects this exact fact;
  the base prompt does NOT restate the core's phrasing (template rule "no core
  duplication").
- `RELOCATED -> <home>` / `DELETED` mean the rule's fact moves elsewhere on the
  production path (a `bindings.yaml` tool description) or is dropped with no home;
  recorded in `.dropped.json` as `relocated` (machine-checked home) / `deleted`.

## What `pdf` actually IS (role + output contract + skeleton choice)

`pdf` (canonical agent id `pdf_extraction`) is **NOT a domain staging extractor**.
It is a **document-reading / evidence-gathering QA agent** that answers a curator's
free-form question about the loaded paper and emits a structured envelope directly.

Verified against the code:

- `packages/alliance/agents/pdf/agent.yaml` sets `output_schema:
  PdfExtractionResultEnvelope` (NOT `null`), so it is an **envelope agent**, not a
  builder extractor. `_is_builder_extractor("pdf")` is False
  (`test_non_gene_evidence_prompt_policy.py`), but pdf is also **out of scope** for
  that test's parametrization (it keys on `gene_expression` or `*_extractor` folder
  names; the folder is `pdf`).
- Its tools (`agent.yaml`) are `search_document`, `read_chunk`, `read_section`,
  `read_subsection`, `record_evidence`. There are **no** `stage_*` / `finalize_*`
  builder tools, **no** validator-dispatch tools, and **no** domain-field grounding
  tools. So there is **no stage/finalize workflow** and **no `<validator_handoff>`**
  to write.
- `PdfExtractionResultEnvelope` (`backend/src/lib/openai_agents/models.py:262`) is
  **hand-authored by the model**. Its audit lists -- `items`, `raw_mentions`,
  `evidence_records`, `normalization_notes`, `exclusions`, `ambiguities`,
  `run_summary` -- are all **top-level fields the model fills directly**. There is
  **NO `metadata.*` field** on this envelope.

So the rewrite uses a **role-adapted, outcome-first envelope-QA skeleton**, NOT the
builder-extractor skeleton:

`<role>` -> `<goal>` -> `<success_criteria>` -> `<operating_constraints>`
(no-invention) -> `<evidence_and_curation_rules>` (verified-quote definition +
include/exclude/ambiguity policy + the de-duped span-recording mechanic) ->
`<retrieval_strategy>` (tool roster compacted + simple-vs-comprehensive stopping) ->
`<output_contract>` (the envelope fields the model authors). No
`<validator_handoff>`, no `<stage/finalize workflow>` -- they do not apply to pdf's
contract. The outcome-first ORDER (Role -> Goal -> Success -> Rules -> Retrieval ->
Output) is preserved.

---

## Template rules applied (Phase C)

### Template rule -- metadata exclude=don't-stage: **N/A (verified)**

The "if pdf stages into a builder with hard-coded `metadata.*`, apply the
exclude=don't-stage rewrite" rule **does not apply** to pdf. Verified against
`PdfExtractionResultEnvelope` + pdf's tool set:

- pdf does **not** stage into a builder. It has no `stage_*`/`finalize_*` tools;
  there is no `materialize_*` conversion path for pdf -- the model's JSON envelope
  IS the output.
- The envelope has **no `metadata.*` field**. `exclusions[]`, `ambiguities[]`,
  `raw_mentions[]`, and `run_summary` are **real top-level, model-authored
  channels** (`PdfExclusionRecord`, `PdfAmbiguityRecord`, `PdfMentionCandidate`,
  `PdfExtractionRunSummary`). The model EXPRESSES an exclusion by writing an
  `exclusions[]` entry (with a `reason_code`), and an ambiguity by writing an
  `ambiguities[]` entry -- the exact opposite of the builder pattern's "express an
  exclusion by NOT staging".

So the rewrite **preserves** pdf's real mechanism (move excluded candidates to
`exclusions[]` with a `reason_code`; put unresolved candidates in `ambiguities[]`)
rather than forcing the don't-stage rewrite. No `metadata.*` instruction exists in
the pre-rewrite prompt to remove (verified -- the pre-rewrite prompt routes
exclusions to `exclusions[]`, never to any `metadata.*`).

### Template rule -- no core duplication (de-dup, the main pdf lever)

`assembly.py::_build_core_generated_content` already injects, for `pdf_extraction`
(verified by rendering `build_agent_core_prompt('pdf_extraction')`):

- the **required-tool-call policy**: "call at least one document retrieval tool
  (search_document, read_section, or read_subsection) before final output";
- the **evidence policy**: "retained PDF evidence must come from
  `read_chunk.evidence_spans[].span_id` values. Use `record_evidence` with
  `span_ids`; the backend copies exact source text into `verified_quote` and
  preserves source span provenance. Before final output, review the active-run
  evidence workspace and keep only intended active evidence records.";
- the **output contract**: "produce JSON matching PdfExtractionResultEnvelope; the
  structured-output layer below is authoritative for final response shape" PLUS the
  "CRITICAL: ALWAYS PRODUCE STRUCTURED OUTPUT AS VALID JSON ... must be valid JSON
  matching the PdfExtractionResultEnvelope schema" block.

The pre-rewrite BASE prompt restated several of these, which the rewrite **removes**
(de-dup, recorded in `.dropped.json` as `relocated -> render` because the fact
survives in the core half of the render):

- "Returns JSON only, matching `PdfExtractionResultEnvelope`." (`<success_criteria>`)
  -> core's structured-output block (PDF-08, de-dup).
- "Return JSON only, matching `PdfExtractionResultEnvelope`." (`<output_contract>`
  opener) -> core's structured-output block (PDF-26, de-dup).
- "Use document retrieval tools before answering." (`<tools>` retrieval-budget
  step 1) -> core's required-tool-call policy (PDF-22, de-dup; the curator-facing
  "You retrieved from the document before answering" success line is KEPT once).

The base prompt KEEPS the **curation-specific** span-recording discipline once (pick
spans that directly support one evidence unit; one call = one record; separate calls
for disjoint units; no paraphrase; recover/drop on span-resolution failure), because
the cross-cutting contract test `test_record_evidence_prompt_contract.py` requires
the literal tokens `read_chunk.evidence_spans[].span_id`,
`record_evidence(span_ids=[...])`, and `evidence unit` in the **effective** prompt
(core + base), and the base is the natural carrier of the curation guidance.

### Template rule -- reason_codes: **none (no `.reason_codes.txt`)**

pdf gets **no** `pdf.reason_codes.txt`, following the disease/allele/phenotype
precedent. Verified:

- `PdfExclusionRecord.reason_code` is a **free `str`** ("Short machine-friendly
  reason code explaining the exclusion"), with **no enum constraint**
  (`models.py:194`). There is no `ExclusionReasonCode`-style canonical enumeration
  bound to the pdf envelope.
- The pre-rewrite pdf BASE prompt **does not enumerate** any canonical reason-code
  list (`_listed_reason_codes(content)` returns the empty set; there is no
  `<exclusion_reason_codes>` block or "Exclude with canonical reason_code when
  applicable:" header). It only says exclusions carry "explicit reason_code values".
- pdf is **out of scope** for `test_extractor_prompt_reason_codes_match_schema_contract`
  (parametrized on `*_extractor`/`gene_expression` only; pdf's folder is `pdf`).

A `.reason_codes.txt` is created ONLY when the prompt already enumerates canonical
codes AND the schema defines them. Neither holds for pdf, so none is created;
introducing one would ADD a rule the prompt never carried.

---

## Role / goal / success

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PDF-01 | Agent identity: General PDF Extraction Agent for biological curation at the Alliance of Genome Resources. | `<role>` (retained verbatim) |
| PDF-02 | Answer curator questions from the loaded paper using verified document evidence. | `<role>` |
| PDF-03 | Scope: broad paper questions (entity inventory, methods, results, figures, conclusions, general summary) when a more specific extractor is not required. | `<role>` (was split across `<goal>` + `<success_criteria>`; consolidated into `<role>`, which is also the agent's behavioral scope, not just routing) |
| PDF-04 | Goal: return a concise paper-grounded answer plus an audit trail of retained items, considered candidates, exclusions, ambiguities, and verified quotes. The chat UI may show the answer directly and expose the structured evidence separately. | `<goal>` |
| PDF-05 | Evidence is backend-verified source text selected from `read_chunk.evidence_spans[].span_id` values, so a curator can inspect the exact paper passage behind each retained item. | `<goal>` (KEPT once; the literal `read_chunk.evidence_spans[].span_id` token is a cross-cutting contract-test requirement) |
| PDF-06 | Uses document retrieval before answering. | `<success_criteria>` (curator-facing success line KEPT; the imperative "use retrieval before answering" is core's required-tool-call policy, PDF-22) |
| PDF-07 | Answers the user's actual question, whether it asks for an entity inventory, method detail, figure-specific claim, result, conclusion, or broad paper summary. | `<success_criteria>` |
| PDF-08 | Returns JSON only, matching `PdfExtractionResultEnvelope`. | DE-DUP -> CORE (`render`). The locked core's structured-output block already mandates valid JSON matching `PdfExtractionResultEnvelope`; the base no longer restates it. Recorded in `.dropped.json` as relocated->render. |
| PDF-09 | Retains only items or claim labels that directly support the requested answer. | `<success_criteria>` + `<evidence_and_curation_rules>` |
| PDF-10 | Creates every evidence record for retained items from backend-generated `read_chunk.evidence_spans[].span_id` values. | `<success_criteria>` |
| PDF-11 | Keeps background-only mentions, prior-work citations, incidental references, and methods-only setup out of retained results unless the user explicitly asked about that scope. | `<success_criteria>` + `<evidence_and_curation_rules>` |
| PDF-12 | Makes exclusions and ambiguities auditable when they affect the answer. | `<success_criteria>` |

## Operating constraints (no-invention discipline)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PDF-13 | Do not invent quotes, page numbers, identifiers, exclusions, or document findings. | `<operating_constraints>` (verbatim) |
| PDF-14 | Do not treat nearby generic context as evidence for a retained item; the verified quote must explicitly support the entity, method, result, or claim label. | `<operating_constraints>` |
| PDF-15 | Do not include chunk IDs in `answer`; keep chunk IDs only inside `evidence_records[]`. | `<operating_constraints>` |
| PDF-16 | Populate `normalized_id` only when the paper or available tooling provides a stable identifier. | `<operating_constraints>` |
| PDF-17 | Apply group-specific rules for organism conventions, nomenclature, and MOD-specific curation scope when a group overlay is present. | `<operating_constraints>` (group hook retained) |

## Evidence rules (verified-quote definition + include/exclude/ambiguity + span mechanic)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PDF-18 | Evidence is a verified quote from the paper, not a paraphrase. | `<evidence_and_curation_rules>` (verbatim) |
| PDF-19 | Include evidence when it directly states the retained entity/experimental setup/method/figure-table result/conclusion/other requested claim; comes from where the claim is stated most explicitly; or helps explain a non-trivial exclusion or ambiguity. | `<evidence_and_curation_rules>` |
| PDF-20 | Move a candidate to `exclusions[]` (with an explicit `reason_code`) when it appears only as: background/prior-work/review/discussion-speculation/contextual-pathway narrative; a parenthetical author-year citation with no new data from this paper; a methods-only reagent/setup mention outside the requested scope; an incidental mention not needed for the final answer. | `<evidence_and_curation_rules>` (the metadata-mechanism N/A: pdf's `exclusions[]` is a real model-authored top-level channel, so the move-to-`exclusions[]` instruction is the correct, preserved mechanism -- NOT rewritten to don't-stage) |
| PDF-21 | If evidence is missing, say so honestly in `answer`, set `kept_count` to 0, and summarize the searches/sections checked in `summary`, `normalization_notes[]`, or `run_summary.warnings`. | `<evidence_and_curation_rules>` |
| PDF-21b | A candidate that cannot be resolved confidently goes into `ambiguities[]` for curator follow-up rather than being guessed. | `<evidence_and_curation_rules>` (made explicit as a curation rule; the pre-rewrite prompt carried `ambiguities[]` only in the output-field list, PDF-31) |
| PDF-22 | Use document retrieval tools before answering. | DE-DUP -> CORE (`render`). Core's required-tool-call policy ("call at least one document retrieval tool ... before final output") carries this. The base no longer restates the imperative; the curator-facing success line "You retrieved from the document before answering" (PDF-06) stays. |
| PDF-23 | For each retained evidence unit, call `read_chunk(chunk_id)` on the relevant chunk, select the `evidence_spans[].span_id` values that directly support one evidence unit, and call `record_evidence(span_ids=[...])`. | `<evidence_and_curation_rules>` (KEPT once -- cross-cutting tokens `read_chunk.evidence_spans[].span_id`, `record_evidence(span_ids=[...])`, "evidence unit") |
| PDF-24 | Multiple `span_ids` in one `record_evidence` call produce one evidence record; use separate `record_evidence` calls for truly disjoint evidence units. | `<evidence_and_curation_rules>` (KEPT once -- curation guidance; the one-call-one-record tool mechanic also lives in `bindings:record_evidence`) |
| PDF-25 | If span resolution fails, call `read_chunk` again for the current span IDs or drop that evidence. | `<evidence_and_curation_rules>` |
| PDF-25b | Do not write, reconstruct, trim, or paraphrase source quote text yourself (the backend copies exact source text into `verified_quote`). | `<evidence_and_curation_rules>` (no-paraphrase discipline; the backend-copies-exact-text fact is also in CORE evidence policy) |

## Tools / retrieval / stopping

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PDF-26 | Return JSON only, matching `PdfExtractionResultEnvelope` (output-contract opener restating the JSON-only mandate). | DE-DUP -> CORE (`render`). Same as PDF-08: core's structured-output block is authoritative for response shape; the base no longer restates "Return JSON only". |
| PDF-27 | `search_document`: document search across the paper; use default/auto for concepts and lexical-heavy modes for exact controlled tokens. | `<retrieval_strategy>` (compacted: "prefer lexical-heavy search modes for exact controlled tokens"); the full per-tool capability prose RELOCATED -> `bindings:search_document`. |
| PDF-28 | `read_section`/`read_subsection`: read a full top-level section (Methods, Results) or a targeted subsection when you know where the answer should be. | `<retrieval_strategy>` (compacted); full coverage prose RELOCATED -> `bindings:read_section`. |
| PDF-29 | `read_chunk`: inspect precise source text and choose `evidence_spans[].span_id` values for final evidence selection. | `<retrieval_strategy>` + `<evidence_and_curation_rules>` |
| PDF-30 | `record_evidence`: create verified evidence records from selected span IDs before keeping them. | `<retrieval_strategy>` + `<evidence_and_curation_rules>` |
| PDF-30a | For a simple factual question, start with the most discriminative term or section and stop once you have a clear verified answer. | `<retrieval_strategy>` |
| PDF-30b | For comprehensive requests ("all genes", "every strain", "everything about X"), search/read systematically across relevant sections before stopping; use `read_section`/`read_subsection` when complete coverage of a known section matters; stop once further searches return only duplicate or background-only candidates. | `<retrieval_strategy>` |
| PDF-30c | Prefer parallel search batches when looking for multiple terms, aliases, or variants; try exact terms and common variations in the same batch before reporting a negative result. | `<retrieval_strategy>` |

## Output contract (PdfExtractionResultEnvelope -- model-authored envelope)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PDF-31 | Populate the envelope fields rather than adding extra prose-only sections: `answer` (concise user-facing answer, no markdown Evidence/Citations/Sources sections); `summary` (short audit-style caveat); `items[]` (retained items/claim labels supported by verified evidence); `raw_mentions[]` (candidates considered); `evidence_records[]` (all verified evidence quotes); `normalization_notes[]` (interpretation/grouping/identifier caveats); `exclusions[]` (excluded candidates with explicit `reason_code`); `ambiguities[]` (unresolved candidates for follow-up); `run_summary` (candidate/kept/excluded/ambiguous counts + warnings). | `<output_contract>` (full field roster retained, compacted into running prose) |
| PDF-32 | If `kept_count` > 0, `evidence_records[]` must not be empty. | `<output_contract>` |
| PDF-33 | Reference retained evidence from `items`, `raw_mentions`, `exclusions`, or `ambiguities` with `evidence_record_ids` when the schema provides that field. | `<output_contract>` |
| PDF-34 | For broad summary-style questions, use short claim labels such as "main question", "core method", or "principal finding" to make the evidence registry easier to audit. | `<output_contract>` |
| PDF-35 | If nothing relevant is found, answer honestly and set `kept_count` to 0. | `<output_contract>` + `<evidence_and_curation_rules>` |

## `<search_context>` / `<search_infrastructure>` block

There is **no** `<search_context>`/`<search_infrastructure>` block left to drop.
It was **already removed in Phase B** (commit `24144d52`, "drop duplicated
search-infrastructure block from pdf base prompt (now in tool descriptions)"), and
the "Multiple `span_ids` -> one evidence record" sentence was relocated into
`<evidence_rules>` at that time. Verified: the pre-Phase-C (HEAD `b7e3a415`) pdf
prompt has no such block. So this Phase-C lever is already spent for pdf; the
remaining lever is the core-duplication de-dup above.

## Group-rule hooks (rendered with the group)

| ID | Load-bearing rule | New home |
|----|-------------------|----------|
| PDF-36 | The 7 group rules (FB/HGNC/MGI/RGD/SGD/WB/ZFIN) carry organism-specific nomenclature/curation-scope overlays; the base rewrite must keep rendering cleanly under each. The `<operating_constraints>` group-overlay hook (PDF-17) signals their application. | Group rules (`group_rules/*.yaml`) -- UNCHANGED in this task. No `.<group>.txt` inventory is added: pdf's group rules carry no Phase-C-asserted phrases (no contract test asserts pdf group-rule content), and the base rewrite's group hook is verified by the render-smoke guard rendering cleanly. |

---

## De-dup summary (the pdf Phase-C lever)

The pre-rewrite prompt restated three facts the locked core already injects: the
JSON-only output mandate (twice: `<success_criteria>` + `<output_contract>` opener)
and the retrieval-before-answering imperative (`<tools>` step 1). The rewrite drops
those restatements (recorded as `relocated -> render` in `.dropped.json`, satisfied
by the core half of the assembled render), and relocates the per-tool capability
prose for `search_document`/`read_section` to their `bindings.yaml` descriptions.
Every curation-specific rule -- the verified-quote definition, the include/exclude
policy, the `exclusions[]`/`ambiguities[]`/`reason_code` channels, the span-recording
discipline (one call = one evidence unit; no paraphrase; recover/drop on span
failure), the retrieval/stopping strategy, the no-invention constraints, and the
full envelope field roster -- is preserved. The cross-cutting contract tokens
(`read_chunk.evidence_spans[].span_id`, `record_evidence(span_ids=[...])`, "evidence
unit") are retained once in the base prompt, satisfying
`test_record_evidence_prompt_contract.py` (the one test that constrains pdf's base
prompt content).

## Contract-test re-baseline

**No test assertion is edited, deleted, or weakened by this rewrite.** The only test
that constrains pdf's base prompt content is
`backend/tests/unit/test_record_evidence_prompt_contract.py`, which (for pdf, via
`EXTRACTOR_PROMPT_PATHS`) requires:

- **present** in the effective (core + base) prompt:
  `read_chunk.evidence_spans[].span_id`, `record_evidence(span_ids=[...])`,
  "evidence unit" -- all retained in the rewritten base prompt (verified);
- **absent** (stale-phrase guard): `claimed_quote`, "verbatim or lightly trimmed",
  "performs fuzzy quote", "fuzzy quote matching", "matching against the stored chunk
  text", "Verify a claimed quote against a specific chunk", "exact contiguous source
  text copied from that chunk", "omitted, inserted, changed, paraphrased, or
  normalized quote text returns" -- none introduced (verified).

pdf is **out of scope** for `test_non_gene_evidence_prompt_policy.py` (parametrized
on `gene_expression`/`*_extractor`) and for
`test_agent_studio_domain_envelope_prompt_policy.py` (its pdf-relevant assertion
targets `agent_studio_system_prompt.md`, not pdf's prompt.yaml). No re-baseline was
needed.
