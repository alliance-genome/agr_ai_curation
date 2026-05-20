# Validator Batch Comparison

Date: 2026-05-21

Durable evidence:

- Baseline corpus: `docs/design/pdf-corpus-trials/baseline-2026-05-20/`
- Focused gene baseline rerun: `docs/design/pdf-corpus-trials/baseline-2026-05-20-gene-rerun-1/`
- Post-change corpus: `docs/design/pdf-corpus-trials/batch-validator-2026-05-21/`
- Comparison JSON: `docs/design/pdf-corpus-trials/validator-batch-comparison-2026-05-21.json`

The implementation adds opt-in batch validator dispatch and unit coverage proves
that multi-request gene envelopes use one `gene_validation` batch run. The live
corpus did not exercise that path for the gene trial: the post-change run
emitted two separate single-request gene validator dispatches, so
`batchValidatorRunCount` was `0`.

| Scope | Baseline | Post-change | Result |
| --- | ---: | ---: | --- |
| Full corpus total duration | 486.387s | 563.602s | +15.9% |
| Focused gene duration | 42.304s | 72.099s | +70.4% |
| Focused gene flow duration | 42.188s | 53.177s | +26.0% |
| Focused gene active validator dispatch | 17.435s | 20.525s | +17.7% |
| Focused gene validator agent runs | 2 | 2 | unchanged |
| Focused gene batch validator runs | n/a | 0 | no live batch opportunity |

Accuracy signals did not show a batch parser regression: post-change validator
problem events stayed at `0`. The remaining performance target is upstream of
the batch executor: coalesce gene extractor outputs before chat-time validator
dispatch, or add a short-lived request-scoped validator batch queue across
consecutive extractor outputs.
