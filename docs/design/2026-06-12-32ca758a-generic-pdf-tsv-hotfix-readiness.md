# 32ca758a Generic PDF TSV Hotfix Readiness

Curator feedback `32ca758a` exposed a PDF extraction plus TSV export failure:
the PDF agent extracted many genetic reagent records, but TSV export emitted a
single artifact/projection summary row instead of one row per retained object.

Readiness criteria before production:

- `pdf_extraction` is converted in place to builder-backed generic extraction.
- Generic extraction finalization emits canonical backend curation data
  (`curatable_objects[]` plus metadata), including a valid empty result when no
  evidence-backed object should be retained.
- TSV curation exports read canonical backend extraction object rows only.
- Prose answers, artifact summaries, legacy `items[]`, `raw_mentions[]`,
  `exclusions[]`, and `ambiguities[]` cannot become TSV curation rows.
- Multi-source TSV exports require explicit source binding by persisted
  `extraction_result_id` or pre-persistence `source_key`, with an explicit
  combined strategy such as `object_ledger` or `wide_union`.
- A production-like PDF-to-TSV replay must show one TSV row per extracted object,
  not a one-row artifact summary, before deployment.

Validation evidence from this hotfix branch should include the focused Docker
unit and contract commands plus a dev-stack replay/smoke result.
