# Phase A — `core_generated` Size: Before / After

Reproducible via `src.lib.prompts.size_report.core_layer_sizes()` (no DB). Sizes are characters of the `core_generated` prompt layer (the "Generated Contract" / "Output structure" panel). `core_static` is a flat 234 chars for every agent and is unchanged. Phase A edited only the backend assembler (`assembly.py`); no base prompt, tool, or validator logic changed.

## What changed (Phase A / A1)

Removed from the inlined runtime contract for **every** agent: the full tool-inventory enumeration. Removed for domain-pack (validator-bound) agents: the schema/provider refs, the envelope-object `required[...]` dump, the per-field `field -> validator_binding` map, and the per-binding `targets … policy … selectors …` lines (which inlined literal CURIE allow-lists). Kept: the required-tool-call policy, the evidence policy, the `get_agent_contract` pointer, the output-contract line, a single capped "Validators own these fields; do not invent" line, and the runtime safety rule. All removed detail remains retrievable via `get_agent_contract` (guarded by `test_core_generated_retrievability.py`).

## Per-agent `core_generated` (chars)

| agent | before | after | delta | % |
|---|---:|---:|---:|---:|
| gene_expression | 9023 | 1894 | -7129 | -79% |
| disease_extractor | 7860 | 1812 | -6048 | -77% |
| phenotype_extractor | 5748 | 1915 | -3833 | -67% |
| allele_extractor | 2512 | 1121 | -1391 | -55% |
| gene_extractor | 2515 | 1396 | -1119 | -44% |
| pdf_extraction | 1565 | 1454 | -111 | -7% |
| controlled_vocabulary | 1270 | 1196 | -74 | -6% |
| experimental_condition | 1273 | 1199 | -74 | -6% |
| subject_entity | 1249 | 1175 | -74 | -6% |
| data_provider | 1246 | 1172 | -74 | -6% |
| ontology_term_validation | 1246 | 1172 | -74 | -6% |
| disease | 1231 | 1157 | -74 | -6% |
| gene | 1216 | 1142 | -74 | -6% |
| allele | 1222 | 1148 | -74 | -6% |
| agm | 1219 | 1145 | -74 | -6% |
| reference | 1160 | 1073 | -87 | -7% |
| chemical | 1140 | 1070 | -70 | -6% |
| gene_ontology | 993 | 941 | -52 | -5% |
| go_annotations | 985 | 938 | -47 | -5% |
| orthologs | 979 | 926 | -53 | -5% |
| curation_prep | 950 | 950 | 0 | 0% |
| json_formatter | 79 | 29 | -50 | -63% |
| csv_formatter | 78 | 29 | -49 | -63% |
| tsv_formatter | 78 | 29 | -49 | -63% |
| chat_output | 0 | 0 | 0 | — |
| supervisor | 0 | 0 | 0 | — |

## Summary

- **Biggest win, gene_expression: 9,023 → 1,894 chars (~1,780 tokens saved per call).** The five extractors drop 44–79%.
- The change benefits **all tool-bearing agents**, not just the extractors: every agent lost the inlined tool-inventory line (validators/lookups −5–7%, formatters −63%), and the validator-bound agents additionally lost the per-binding enumeration.
- `curation_prep` is unchanged (no tools/domain pack contributing removable lines); `chat_output`/`supervisor` have no `core_generated` layer.
- Largest remaining `core_generated` is phenotype_extractor at 1,915 chars, well under the 2,500-char soft budget enforced by `test_prompt_size_budget.py`.

## Verification (no behavioral A/B per the spec)

- Deletion-only for the runtime contract; nothing the model acts on was removed (the kept "validators own these fields" line + retained safety rule preserve no-invention; base-prompt rules untouched).
- Every removed datum is still served by `get_agent_contract` — proven by `test_core_generated_retrievability.py` (topics `tools`, `validator_bindings`+detail, `domain_envelope`+detail, `ontology_constraints`).
- Full prompt/contract + agent-studio/catalog test surface green (143 + 89 tests); no code or fixture references the removed strings.
