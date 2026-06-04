# Phase C — Base-Prompt Rewrites: Closing Size Report (all ~26 agents)

Phase C rewrote every agent's `prompt.yaml` base prompt to a lean, outcome-first
skeleton (Role → Goal → Success criteria → Constraints → Output/handoff → Stop
rules), preserving every load-bearing curation rule under a no-DB rewrite-guard
harness and per-wave Opus 4.8 + Codex (gpt-5.5/high) gates. Net base-prompt
change is **−5,634 chars** across the 26 agents (chat_output counted once): the
extractors shrank, the formatters/supervisor shrank, and the terse validators
**grew** as they gained explicit lean structure. This is the *loss-full* phase
(it changes instructions the model acts on); verification is structural plus the
two LLM reviews, with no live A/B.

Sizes below are the **`content`-field character count** of each `prompt.yaml`
(the editable base-prompt body, parsed with PyYAML — not whole-file `wc -c`,
which would add the `agent_id`/comment lines). "Before Phase C" is the prompt as
of commit `f8c26dfa` (the harness commit — post-Phase-A/B, immediately preceding
the first Phase C rewrite `021a724d`); "after" is branch HEAD `2245f6e4`. All 26
prompts already existed at `f8c26dfa`, so there are no missing-file caveats.

## Per-agent base-prompt before/after

| Agent | before | after | delta | delta% |
|---|---:|---:|---:|---:|
| **Extractors** | | | | |
| gene_extractor | 27,454 | 20,611 | −6,843 | −25% |
| gene_expression | 26,172 | 23,846 | −2,326 | −9% |
| disease_extractor | 19,323 | 20,404 | +1,081 | +6% |
| phenotype_extractor | 15,944 | 17,927 | +1,983 | +12% |
| allele_extractor | 16,659 | 16,284 | −375 | −2% |
| pdf | 5,792 | 5,778 | −14 | −0% |
| **Validators / lookups** | | | | |
| gene | 15,213 | 12,383 | −2,830 | −19% |
| allele | 11,840 | 10,677 | −1,163 | −10% |
| disease | 6,939 | 6,380 | −559 | −8% |
| chemical | 9,212 | 8,706 | −506 | −5% |
| gene_ontology | 5,893 | 5,579 | −314 | −5% |
| go_annotations | 7,767 | 6,901 | −866 | −11% |
| orthologs | 6,778 | 6,568 | −210 | −3% |
| ontology_term | 10,221 | 11,302 | +1,081 | +11% |
| subject_entity | 4,913 | 5,629 | +716 | +15% |
| data_provider | 5,495 | 6,824 | +1,329 | +24% |
| reference | 6,978 | 7,346 | +368 | +5% |
| experimental_condition | 7,677 | 9,953 | +2,276 | +30% |
| controlled_vocabulary | 6,494 | 7,798 | +1,304 | +20% |
| agm | 3,447 | 5,131 | +1,684 | +49% |
| **Supervisor** | | | | |
| supervisor (config-only) | 17,487 | 16,629 | −858 | −5% |
| **Output / formatters** | | | | |
| chat_output (dual-tree, once) | 4,243 | 4,013 | −230 | −5% |
| curation_prep (config-only) | 3,472 | 3,547 | +75 | +2% |
| tsv_formatter | 3,990 | 3,507 | −483 | −12% |
| json_formatter | 2,522 | 2,515 | −7 | −0% |
| csv_formatter | 2,474 | 2,527 | +53 | +2% |
| **TOTAL (26 agents, chat_output once)** | **254,399** | **248,765** | **−5,634** | **−2%** |

(chat_output is byte-identical in `packages/alliance/agents/` and
`config/agents/`; both trees were rewritten in sync and it is counted once.
Supervisor and curation_prep are config-only.)

## Per-group subtotals (`content`-field chars)

| Group | agents | before | after | delta |
|---|---:|---:|---:|---:|
| Extractors | 6 | 111,344 | 104,850 | **−6,494** |
| Validators / lookups | 14 | 108,867 | 111,177 | **+2,310** |
| Supervisor | 1 | 17,487 | 16,629 | **−858** |
| Output / formatters | 5 | 16,701 | 16,109 | **−592** |
| **Total** | **26** | **254,399** | **248,765** | **−5,634** |

The extractor group carries the win; the validator group is the one that grew
(see "Where it grew and why").

## Phase A + B context (from the prior reports)

Phase C is the third of three phases. The earlier two cut different layers and
are not re-measured here — their numbers are quoted from
`2026-06-03-prompt-size-report-phaseA.md` and
`2026-06-03-prompt-size-report-phaseB.md`.

**Phase A — `core_generated` per-call reduction (locked layer, backend
assembler only).** Removed the inlined tool inventory, schema/provider refs,
envelope `required[...]` dump, the per-field `field -> validator_binding` map, and
the per-binding CURIE allow-lists from the runtime contract; all remain
retrievable via `get_agent_contract`. Headline:

- gene_expression `core_generated` **9,023 → 1,894** chars (−7,129, −79%, ~1,780 tokens/call).
- The five extractors dropped 44–79% (disease_extractor −6,048, phenotype_extractor −3,833, allele_extractor −1,391, gene_extractor −1,119).
- Every tool-bearing agent lost the inlined tool line (validators/lookups −5–7%); the three formatters dropped to 0 chars.

**Phase B — cross-layer search de-dup (relocate to tool descriptions).** Moved
the document-search guidance out of base prompts and into the search/read tool
descriptions (where tool guidance belongs), then removed the redundant blocks:

- gene_expression base prompt **31,675 → 26,936** (−4,739, −15%).
- pdf base prompt **9,707 → 6,514** (−3,193, −33%).
- The four `<search_context>` extractors (gene_extractor, disease_extractor,
  allele_extractor, phenotype_extractor) were **deferred to Phase C** — their
  search facts were already relocated into the tool descriptions, but the blocks
  were interwoven with extractor-specific guidance, so block removal was folded
  into the Phase C holistic rewrite.

## Combined Phase A + B + C accounting (honest)

For the **hot path** (the extractors, run per paper), the per-call reduction
stacks across all three phases. Taking gene_expression (the worst case) as the
worked example:

| Layer | Phase | reduction |
|---|---|---:|
| `core_generated` (locked runtime contract) | A | −7,129 chars (~−1,780 tok) |
| base prompt (`<search_infrastructure>` relocation) | B | −4,739 chars |
| base prompt (outcome-first rewrite + lean re-audit) | C | −2,326 chars |
| **gene_expression total, per call** | A+B+C | **−14,194 chars (~−3,500 tok)** |

Note the Phase C gene_expression delta (−2,326) is measured against the
post-Phase-B prompt at `f8c26dfa` (26,172), so it does **not** double-count the
Phase B search relocation. The other extractors stack the same way: Phase A cuts
their `core_generated`, Phase B relocates their search facts into the shared tool
descriptions, and Phase C removes the now-orphaned `<search_context>` blocks plus
rewrites the body.

Across **all 26 base prompts**, Phase C alone nets **−5,634 chars**. The picture
by group: extractors **−6,494**, supervisor **−858**, formatters/curation_prep
**−592**, and validators/lookups **+2,310**. Combined with the locked-layer
Phase A cut and the Phase B tool-description relocation, the total program moves
real per-call cost off the hot path while making the prompts read clearly; it is
**not** a uniform per-agent token cut, and the validators are genuinely larger
than they were (next section).

## Where it grew, and why

The validator/lookup group **grew +2,310 chars** net across its 14 agents, and
several individual agents grew (agm +1,684, experimental_condition +2,276,
data_provider +1,329, controlled_vocabulary +1,304, ontology_term +1,081,
subject_entity +716). This is expected and was a deliberate choice, not drift:

- **The originals were terse, not lean.** Several validators (notably agm,
  subject_entity, data_provider) were short because they were under-specified —
  they leaned on implicit shared behavior. Giving them the explicit
  Role/Goal/Success/Constraints/Output skeleton, in curator voice, costs
  characters but makes each agent's contract legible and self-contained.
- **The Wave 3 gate confirmed the group is roughly flat.** Across the 15 Wave-3
  agents (the 14 validators plus supervisor), the net was **~+1,452 chars** —
  the "~+1.3K across 15 agents" recorded at the Wave 3 gate. That is a rounding-
  level change spread over 15 prompts, not bloat.
- **Chris chose lean over the full skeleton.** A first-pass outcome-first
  rewrite of the smallest validators roughly doubled their size (e.g. agm
  +4,850, subject_entity +3,393 at the initial Wave 3 pass). Those were then cut
  back to a lean skeleton (agm settled at +1,696, subject_entity at +740). The
  decision was to keep the lean structure — clear contract, no redundant
  success-criteria restatement — rather than ship the 2x-bloated full skeleton.
  The same lean discipline dropped the redundant `success_criteria` blocks from
  the gene and allele validators (gene −1,686, allele −1,960 in the lean cut).
- **A leanness re-audit then shaved the extractors further.** After the Wave-2
  rewrites, a cross-section restatement re-audit removed duplicated guidance from
  the extractors: allele_extractor **−713**, gene_extractor **−415**,
  gene_expression **−1,260**, disease_extractor **−354**, phenotype_extractor
  **−430**. These are reflected in the final numbers above. (disease_extractor
  and phenotype_extractor still net positive vs `f8c26dfa` because their
  outcome-first rewrites added the metadata-mechanism and cardinality-template
  rules that the originals only implied.)

## Verification

Phase C is loss-full, so verification is structural plus the two LLM reviews, not
live extraction A/B:

- **No-DB rewrite-guard harness** (`backend/tests/unit/lib/prompts/`, commit
  `f8c26dfa`): builds the real assembled prompt for every rewritten agent with no
  database, then asserts (a) per-agent fragment retention of every load-bearing
  phrase, with a group dimension; (b) a machine-checked dropped-list — every
  `relocated` phrase must actually appear in its declared new home (tool
  description, `get_agent_contract`, or another section), and every `deleted`
  phrase is printed for human review so it cannot hide in the diff; (c) workflow
  invariants — evidence-span, resolver, and builder stage/finalize steps survive
  with their counts and ordering; (d) reason-code survival sourced from the domain
  packs; plus a contradiction dump, config-divergence guard, and render/
  custom-agent smoke. The harness was proven to fail on a planted rule drop and
  pass on revert.
- **Per-agent semantic-coverage checklists** (`docs/design/phaseC-checklists/`):
  every load-bearing rule mapped to its new home or an explicit justified drop,
  with stable IDs; any re-baselined contract-test assertion cross-references a
  checklist ID and ships a replacement assertion in the same commit (no count or
  ordering loosened).
- **Per-wave gates:** Opus 4.8 review + Codex (gpt-5.5/high) over each wave's
  diff (no load-bearing rule lost, no contradictions, curator voice, no weakened
  assertions, scenario cards hold). Wave-2 and Wave-3 gate follow-ups are in the
  history (e.g. supervisor validatable-entity list, orthologs empty-set
  conditional, truthful exclusion wording, gene-search-order relocated to the
  query tool doc).
