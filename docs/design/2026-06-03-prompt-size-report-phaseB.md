# Phase B — Cross-Layer De-dup: Redundancy Map + Before/After

Phase B relocated the document-search guidance out of the base prompts and into the search/read **tool descriptions** (where tool-usage guidance belongs, per OpenAI's GPT-5.5 guidance), then removed the now-redundant blocks. The dual source-of-truth (confirmed by a post-#446 code trace):
- **Model-facing** tool description = the `@function_tool` **docstrings** in `packages/alliance/python/src/agr_ai_curation_alliance/tools/weaviate_search.py` (mirrored in the backend copy that `test_tool_descriptions.py` guards).
- **Curator-facing** catalog = `packages/alliance/tools/bindings.yaml` `description` / `metadata.documentation.summary` (post-#446, `CURATED_TOOL_REGISTRY` is gone).

Both were enriched in Task 1; the base-prompt blocks were removed in Tasks 2-3.

## Per-agent redundancy map

| Agent | Removed in Phase B | Surviving home |
|---|---|---|
| **gene_expression** | `<search_infrastructure>` block | search/read tool descriptions (Task 1) |
| | `Available tools:` bare list | always-sent tool schemas + the kept required-tool-call line in `core_generated`; the 4 evidence-workspace tools folded into the `<evidence_record_contract>` "Final evidence review" workflow as procedural guidance |
| | evidence-immutability sentence | **relocated** into `<evidence_record_contract>` (evidence-policy, asserted by `test_gene_expression_prompt_policy.py:112`) |
| | "~1500-char preview" claim | **intentionally dropped (inaccurate)** — `search_document` returns full chunk text; the ~1600-char preview is internal-only |
| **pdf** | `<search_infrastructure>` block | search/read tool descriptions (Task 1) |
| | "Multiple `span_ids` … one evidence record" sentence | **relocated** into `<evidence_rules>` (evidence-policy, not search-infra) |
| **gene_extractor, allele_extractor, disease_extractor, phenotype_extractor** | `<search_context>` blocks — **NOT removed in Phase B** | Their search-backend facts are the same as gene_expression's and are **already relocated** into the tool descriptions (Task 1). Block removal is **deferred to Phase C**, where the holistic per-agent rewrite handles them (the blocks are interwoven with extractor-specific guidance). |
| all other agents | (none) | no Phase-B-removable cross-layer redundancy |

## Before/after base-prompt sizes

| Agent | before (chars) | after (chars) | delta |
|---|---:|---:|---:|
| gene_expression | 31,675 | 26,936 | −4,739 (−15%) |
| pdf | 9,707 | 6,514 | −3,193 (−33%) |

The four `<search_context>` extractors are **unchanged in Phase B** (their blocks remain; deferred to Phase C): gene_extractor (~28.2K), disease_extractor (~19.8K), allele_extractor (~17.0K), phenotype_extractor (~16.4K).

## Token reality (honest accounting)

- **Net base-prompt reduction lands now only for gene_expression + pdf** (~−7.9K chars, ~−2K tokens combined) — the two `<search_infrastructure>` carriers.
- The four `<search_context>` extractors do **not** shrink yet (Phase C removes their blocks). Their facts are already in the tool descriptions, so Phase C can drop them cleanly.
- The four search/read **tool descriptions grew modestly** (the relocated facts) and are sent in the tools array of **every** document-tool agent. So Phase B's win is **de-duplication + correct structure** — consolidating the guidance (previously duplicated across up to six base prompts) into one shared tool-description source where the model expects tool guidance — **not** a uniform per-call token cut. The full per-call reduction across all six extractors arrives after Phase C.

## Verification (structural + the two reviews, per the spec)

- **Model-facing guidance guarded:** `test_tool_descriptions.py` asserts the search/read docstrings carry the relocated facts (`BM25`, `cross-encoder`, `MMR`, hierarchy/page, `evidence_spans`/`span_id`) — non-vacuous (would fail on a revert).
- **Curator catalog:** `bindings.yaml` summaries rewritten in plain language (no dev jargon); `tool_catalog_baseline.json` parity regenerated (only the 4 tool summaries changed).
- **Nothing load-bearing lost:** per-agent redundancy maps confirm every removed fact's surviving home; `test_tool_descriptions.py` guards the relocated facts in the model docstrings; `test_gene_expression_prompt_policy.py` green (immutability relocated, not lost). (The `<search_infrastructure>` forbidden-fragment guard in `test_assembly.py` is scoped to phenotype_extractor, which never carried such a block, so it does not itself exercise the gene_expression/pdf removals — the real evidence is the size deltas + the relocation and tool-description tests.)

## Carry-overs for Phase C

- `gene_extractor`'s `<search_context>` block still contains the inaccurate "~1500 characters per chunk hit" claim — Phase C must **drop** this sentence (as Phase B did for gene_expression/pdf), NOT relocate it. The other three deferred extractors carry the shorter `<search_context>` variant without this claim.
- Two-copy architecture: `test_tool_descriptions.py` guards the legacy backend `weaviate_search.py` copy while the runtime uses the package copy; they're kept in sync manually (the Phase B enrichment is identical in both). A future cleanup could point the test at the package copy (or consolidate the copies) so the guard tracks the runtime directly.
- The only failing test in this checkout is the environmental `test_pdf_corpus_trial_examples_do_not_teach_quote_submission` (gitignored on-disk corpus artifacts) — passes in CI; not a Phase-B regression.
