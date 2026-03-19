# PDFX Bbox Availability Audit

This note records the current backend contract for PDF-based evidence localization as audited in `ALL-95`. It is descriptive of the code that exists today, not an aspirational design.

## Current Code Path

- `backend/src/lib/pipeline/pdfx_parser.py` submits the PDFX job, polls for completion, downloads `/{process_id}/download/{variant}`, and rebuilds pipeline elements with `markdown_to_pipeline_elements()`.
- `markdown_to_pipeline_elements()` preserves page markers but intentionally does not synthesize `metadata.bbox` or `metadata.provenance` from markdown.
- `backend/src/schemas/pdfx_schema.py` can preserve upstream structured metadata, including `metadata.provenance[*].bbox` and root-level `metadata.bbox`, when a structured PDFX response is normalized through `normalize_elements()` and `build_pipeline_elements()`.
- `backend/src/lib/pipeline/chunk.py` turns that structured provenance into chunk `doc_items`, which is the data shape used later by Weaviate search results and the PDF viewer overlay.
- `backend/src/lib/openai_agents/streaming_tools.py` currently degrades `search_document` hits with no `doc_items` to page-only payloads (`{"page": page_number}`); `read_section` emits no provenance event at all when `doc_items` are missing.

## What Is Reliable Today

| Source path | Verified evidence in repo | Bbox reliability | Reviewer experience |
|---|---|---|---|
| Structured PDFX JSON normalized through `normalize_elements()` + `build_pipeline_elements()` | `backend/tests/fixtures/micropub-biology-001725_pdfx.json`, `backend/tests/unit/pipeline/test_chunk.py`, `backend/tests/unit/pipeline/test_pdfx_schema.py` | Reliable when upstream provenance already contains page + bbox data. | Exact region highlighting is possible. |
| Live parser path rebuilt from downloaded markdown | `backend/src/lib/pipeline/pdfx_parser.py`, `backend/tests/unit/pipeline/test_pdfx_parser.py` | No bbox synthesis. Page markers only. | Degraded page-only localization. |
| Search result hit with neither `doc_items` nor `page_number` | `backend/src/lib/openai_agents/streaming_tools.py` | None. | Unresolved. |
| Section read with missing `doc_items` | `backend/src/lib/openai_agents/streaming_tools.py` | None. | Unresolved for the current viewer event contract. |

## Curator-Target Fixture Coverage

The repository contains one structured curator-target fixture generated from a micropub article:

- `backend/tests/fixtures/micropub-biology-001725_pdfx.json`

Observed coverage in that fixture:

- 44/44 normalized elements retain at least one bbox.
- Verified element families in this sample: section headers (`SectionHeaderItem`), narrative text (`TextItem`), and one caption-labeled text block.
- Two elements contain multiple provenance entries, so multi-box localization is already representable in the stored metadata.

Not yet verified from repo evidence:

- Tables.
- Figures or image regions.
- Equations.
- List-heavy papers.
- Other publisher/layout families outside the stored micropub sample.

## Degraded Localization Contract

Current degraded behavior should be treated as:

1. `bbox-exact`: at least one usable bbox and a usable page number are present in structured provenance.
2. `page-only`: a usable page number exists, but no valid bbox survives normalization or storage.
3. `unresolved`: no usable page number exists, or the viewer event receives no provenance payload at all.

Rules:

- Do not invent bounding boxes from markdown text.
- If a bbox is missing, non-finite, or collapses to zero area, downgrade to `page-only` when a page is still known.
- If neither bbox nor page survives, mark the locator unresolved instead of guessing.
- `page-only` should still let the UI jump to the cited page, but it should not render a rectangle highlight.

## Proposed `locator_quality` Inputs For ALL-108

Later resolver work can score localization from these deterministic inputs:

| Score | Label | Required inputs | Notes |
|---|---|---|---|
| `1.0` | `bbox-exact` | `page` present, `valid_bbox_count >= 1`, source provenance came from structured PDFX metadata | Best available anchor quality. |
| `0.7` | `bbox-multi-span` | `page` present, `valid_bbox_count > 1` or boxes span multiple nearby regions | Still highlightable, but reviewers may need to inspect multiple boxes. |
| `0.4` | `page-only` | `page` present, `valid_bbox_count == 0` | Degraded localization; no rectangle overlay. |
| `0.0` | `unresolved` | no usable `page` | Resolver should flag the anchor as unresolved. |

Recommended raw inputs:

- `source_format`: structured PDFX vs markdown-rebuilt.
- `page_count`: number of unique pages represented in provenance.
- `valid_bbox_count`: bbox entries with finite coordinates and positive width/height.
- `invalid_bbox_count`: malformed or collapsed boxes that forced a downgrade.
- `doc_item_count`: number of provenance entries carried into the chunk.

Guardrail:

- Markdown-rebuilt elements should never score above `page-only` unless a future implementation adds a real geometry synthesizer and tests for it.
