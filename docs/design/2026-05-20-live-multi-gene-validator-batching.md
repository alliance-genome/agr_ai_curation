# Live Multi-Gene Validator Batching

Focused trial: `gene_drosophila_r8_tgfb_multi_gene`

Paper: "Parallel Activin and BMP signaling coordinates R7/R8 photoreceptor subtype pairing in the stochastic Drosophila retina"

- Article: https://elifesciences.org/articles/25301
- PDF: https://cdn.elifesciences.org/articles/25301/elife-25301-v2.pdf
- Species/provider: Drosophila melanogaster / FlyBase
- Candidate genes: babo/Baboon, dSmad2, Mad, wts/Warts, melt/Melted, Rh5, Rh6

## Runtime Shape

The focused live PDF run proved that the gene extractor can return one domain
envelope containing four `gene_mention_evidence` objects. In that shape,
`_dispatch_domain_envelope_validators_for_chat` calls
`dispatch_active_validator_bindings` once for the envelope, and the dispatcher
builds four `DomainValidationRequest` values in one call.

The batch-enabled runs also showed two extractor attempts before the flow ended
with `incomplete_flow_steps`, so validator dispatch was repeated twice.

## Change

The implementation keeps the existing dispatcher grouping path and tightens the
batch instructions sent to validator agents. The gene validator prompt now tells
batch mode to group compatible requests by provider/taxon/species context, call
`agr_curation_query` once with `method: "search_genes_bulk"` and a
`gene_symbols` list, then map each returned item back to its request.

## Benchmark Summary

| Run | Commit | Validator runs | Batch runs | Active validator dispatch |
| --- | --- | ---: | ---: | ---: |
| Pre-batch baseline | `f14d1a52` | 4 | 0 | 11.968s |
| Original batch probe | `e300d1e1` | 2 | 2 | 81.206s |
| Tightened batch post-change | `0629498e` | 2 | 2 | 58.679s |

The tightened prompt reduced batch-enabled active validator dispatch by 22.527s
(27.7%) compared with the original batch probe. It still regressed relative to
the pre-batch parallel singleton path in this benchmark.

Durable comparison JSON:
`docs/design/pdf-corpus-trials/gene-multi-validator-batching-comparison-2026-05-20.json`

## Remaining Bottleneck

The flow repeats the extractor attempt and therefore repeats validator dispatch
before failing with `incomplete_flow_steps`. The batch validator also still
spends roughly 27-32 seconds per four-request batch, so reducing duplicated
extractor attempts or exposing raw validator-agent tool-call counts would be the
next useful measurement target.
