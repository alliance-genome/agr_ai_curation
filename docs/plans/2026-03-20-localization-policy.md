# ALL-95 Localization Policy: PDFX Markdown to PDF Text-Layer Localization

## Purpose

This document is the canonical localization policy for downstream tickets:

- `ALL-106` backend evidence-anchor resolver
- `ALL-108` locator-quality scoring and unresolved reporting
- `ALL-114` typed PdfViewer evidence inputs
- `ALL-126` PDF text-layer highlighting

It turns the `ALL-94` spike into implementation rules for how PDFX-derived evidence should be localized against the real PDF.js text layer.

## Decision Summary

- Treat PDFX markdown as a locator hint layer, not as a pixel-accurate representation of the PDF.
- Treat the real PDF text layer as the source of truth for highlightable text.
- Use a page-biased ranked search chain, not a raw string lookup.
- Keep the existing `EvidenceAnchor` contract in this ticket; do not add `page_numbers`, `section_path`, `leading_fragment`, `trailing_fragment`, or `is_cross_page_candidate` yet.
- Classify quote-derived fragment hits as `normalized_quote`, not as a new locator-quality enum.
- Degrade cross-page quotes to single-page fragment highlight plus page navigation; do not promise contiguous whole-quote highlighting across pages.

## Inputs Audited

- `docs/plans/2026-03-20-all-94-pdfx-markdown-to-pdf-text-layer-spike.md`
- `backend/src/lib/pipeline/pdfx_parser.py`
- `backend/src/schemas/pdfx_schema.py`
- `backend/src/schemas/curation_workspace.py`
- `frontend/src/features/curation/contracts.ts`
- `frontend/src/components/pdfViewer/PdfViewer.tsx`

## Current Representation Boundary

### Production parse path

`PDFXParser.parse_pdf_document()` downloads merged markdown from the extraction service and converts it with `markdown_to_pipeline_elements()` in `backend/src/lib/pipeline/pdfx_parser.py`.

That parser emits element dictionaries with this shape:

- `index`
- `type`
- `text`
- `metadata`

### How quote/snippet text is represented today

- Headings become `type="Title"` and `text=<heading text>`.
- Paragraphs become `type="NarrativeText"` and adjacent markdown lines are joined with a single space.
- List items become `type="ListItem"` and preserve the visible bullet/number prefix in `text`.
- Tables become `type="Table"` and preserve row breaks with newline joins.
- Code fences become `type="NarrativeText"` with the fenced block text preserved.

Implication:

- The parsed text is already a normalized markdown representation, not a byte-for-byte copy of the PDF text layer.
- Paragraph wrapping differences are expected because markdown lines are flattened before downstream localization.

### How page numbers are represented today

- `markdown_to_pipeline_elements()` tracks `current_page`.
- Page changes come only from markdown page markers:
  - `<!-- page: N -->`
  - `[page N]`
- Every emitted element gets `metadata.page_number=<current_page>`.
- If no page marker has appeared yet, the parser defaults to page `1`.

### How section headings are represented today

- The parser maintains a heading stack as `section_path`.
- Every emitted element gets:
  - `metadata.section_title`: the current leaf heading
  - `metadata.section_path`: the full heading stack
  - `metadata.hierarchy_level`: stack depth, defaulting to `1`

Important contract boundary:

- The pipeline parser carries `section_path` in element metadata.
- The shared `EvidenceAnchor` contract does not currently expose `section_path`; it exposes `section_title` and `subsection_title` only.

## What the Spike Established

From `ALL-94` on a 44-element fixture:

- Raw quote match: `8 / 44` (`18%`)
- Normalized quote match: `28 / 44` (`64%`)
- Candidate-chain match: `37 / 44` (`84%`)
- Section fallback recovered: `6 / 44` (`14%`)
- Unresolved: `1 / 44` (`2%`)

Operational conclusions:

- raw PDFX quote text alone is not reliable enough
- lightweight normalization is required
- a short fragment chain is required for long and cross-page quotes
- section and page metadata remain necessary for degraded mode
- page bias is required for repeated text

## Canonical Field Semantics

These rules define how downstream tickets should populate and consume the existing anchor fields.

### `snippet_text`

- Curator-visible quote/snippet sourced from PDFX markdown or a derived evidence span.
- Preserve the human-facing text as extracted.
- Do not overwrite it with normalized or fragmented search text.

### `sentence_text`

- Optional sentence-sized evidence text when a sentence boundary is available and useful.
- Preserve it as curator-visible source text, not as a normalization artifact.

### `normalized_text`

- Canonically normalized form of the full quote-derived source text.
- Preserve full-quote intent even when the eventual viewer search uses a fragment.
- Do not lower-case as part of the shared contract.
- Comparisons may be case-insensitive, but the stored normalized string should preserve original letter case after normalization.

### `viewer_search_text`

- The exact quote-derived string the viewer should search for in the PDF text layer.
- Required for `exact_quote` and `normalized_quote`.
- May equal the raw quote, the canonically normalized quote, or the winning fragment candidate.
- Must be `null` for `section_only`, `page_only`, `document_only`, and `unresolved`.

### `page_number`

- Primary 1-based bias page for search and fallback.
- When a quote spans two pages, store the first page in the span.
- The viewer should reset to this page before beginning any search family.

### `section_title` and `subsection_title`

- Section-level degraded fallback hints.
- `section_title` is the primary fallback.
- `subsection_title` is a secondary fallback when it is materially more specific than the section title.

## Canonical Text Normalization Contract

The backend resolver and frontend viewer must apply the same normalization rules whenever they compare PDFX-derived quote text to PDF text-layer text.

Apply these transforms in order:

1. Normalize Unicode with `NFKC`.
2. Remove soft hyphen `U+00AD`.
3. Replace non-breaking space `U+00A0` with a regular space.
4. Replace Unicode dashes `U+2010`, `U+2011`, `U+2012`, `U+2013`, `U+2014`, and `U+2212` with ASCII `-`.
5. Replace curly apostrophes `U+2018`, `U+2019`, `U+201A`, `U+201B` with ASCII `'`.
6. Replace curly double quotes `U+201C`, `U+201D`, `U+201E`, `U+201F` with ASCII `"`.
7. Replace newline boundaries with a single space.
8. Collapse runs of horizontal and vertical whitespace to a single space.
9. Remove accidental spaces immediately before `, . ; : ! ?`.
10. Remove accidental spaces just inside opening or closing brackets.
11. Trim leading and trailing whitespace.

Rules that are intentionally out of scope for the shared normalization contract:

- no lower-casing requirement
- no stemming or tokenization
- no heuristic removal of footer/header words
- no fuzzy edit-distance matching

Those may be implementation details in resolvers, but they are not part of the cross-stack contract.

## Why Text Normalization Mismatches Happen

These are the expected mismatch hotspots when searching the real PDF from PDFX markdown:

- PDFX markdown often preserves repeated spaces or line-break joins that the PDF text layer flattens.
- Smart quotes and Unicode dashes are commonly converted to ASCII equivalents in the PDF text layer or in search behavior.
- Soft hyphens and NBSPs appear in one representation but not the other.
- Long captions and paragraphs may not appear as one contiguous searchable run in the text layer.
- Cross-page quotes break contiguity at the page boundary.
- Repeated phrases can resolve to the wrong page unless search begins from the hinted page.
- Header/footer artifacts can look searchable in extracted text but are not reliable evidence targets.

## Ranked Fallback Chain

This is the required fallback order for localization:

1. Exact quote
2. Normalized quote
3. Fragment candidate
4. Section-title search
5. Page navigation
6. Document-level unresolved

### Exact quote

- Start from the raw quote-derived source text.
- Bias the search from `page_number` when available.
- If the raw quote resolves in the PDF text layer, stop here.

### Normalized quote

- Apply the canonical normalization contract to the full quote.
- Bias the search from `page_number` again before searching.
- If the normalized full quote resolves, stop here.

### Fragment candidate

This is still quote-derived matching. It is not section fallback.

Use a small deterministic chain only, for example:

- first sentence, when a long quote contains a natural sentence boundary
- leading fragment
- trailing fragment

Rules:

- fragment search must be deterministic and short
- fragment search still counts as quote-derived localization
- the winning fragment becomes `viewer_search_text`
- the full normalized quote remains in `normalized_text`

### Section-title search

- Only begin this step after all quote-derived candidates fail.
- Use `section_title` first.
- Use `subsection_title` second when distinct and more specific.
- Reset the viewer to `page_number` before section fallback when a page hint exists.

### Page navigation

- Only use after quote-derived and section-derived search both fail.
- Navigate to `page_number`.
- Do not show a quote highlight.

### Document-only unresolved

- If no quote, section, or page locator can be used, open the document without a precise subdocument target.
- Distinguish the intentional `document_only` state from the failure state `unresolved` using the matrix below.

## `locator_quality` Scoring Matrix

`locator_quality` describes the best durable localization result, not every attempt that happened internally.

| `locator_quality` | When to assign it | Required anchor data | Viewer outcome |
|---|---|---|---|
| `exact_quote` | The raw quote-derived string resolved in the PDF text layer without needing canonical normalization or fragment shortening. | `viewer_search_text`; quote source text; `page_number` when known. | Strong quote highlight on the matched page. |
| `normalized_quote` | Quote-derived localization succeeded only after canonical normalization and/or deterministic fragment fallback. | `normalized_text`; `viewer_search_text`; quote source text; `page_number` when known. | Strong quote highlight, but the matched string may be normalized or fragmentary. |
| `section_only` | No quote-derived candidate resolved, but section metadata located a relevant page/heading. | `section_title` or `subsection_title`; `page_number` recommended; `viewer_search_text=null`. | Section-targeted degraded mode. |
| `page_only` | Quote-derived and section-derived search both failed, but a page hint remains reliable. | `page_number`; `viewer_search_text=null`. | Page jump only, no text highlight. |
| `document_only` | The anchor is intentionally only document-scoped and no reliable subdocument target is available or required. | `viewer_search_text=null`; page/section fields optional but generally absent. | Open document without precise navigation. |
| `unresolved` | Localization was attempted but no durable quote, section, page, or deliberate document-only anchor could be produced. | `viewer_search_text=null`; page/section fields usually absent. | Explicit unresolved state with no trusted highlight. |

Important compatibility rule:

- Because the shared enum does not include `fragment_quote`, every successful fragment-based quote match is classified as `normalized_quote`.

## Degraded-Mode UX Contract

### Shared expectations

- Always show a visible quality badge derived from `locator_quality`.
- Clear any previous evidence highlight before applying a new localization result.
- Never render a quote highlight when the system only has section, page, document, or unresolved quality.

### `exact_quote`

- Render the normal quote highlight.
- No degraded styling is needed.

### `normalized_quote`

- Render the normal quote highlight.
- The badge may indicate an approximate or normalized match, but this is still a highlightable quote state, not a degraded fallback.

### `section_only`

- Prefer a dimmed or secondary highlight style if the section title itself can be highlighted in the text layer.
- If the section text is not highlightable but section search lands on a relevant page, treat it as navigational fallback only.
- Surface a clear note that the quote itself was not matched.

### `page_only`

- Jump to the page.
- Do not show any text highlight.
- Surface a clear note such as "Navigated to the hinted page; quote text not matched."

### `document_only`

- Open the document.
- Do not force a quote or section highlight.
- Preserve the current/default page if the document is already open; otherwise let the viewer start from its normal initial page.

### `unresolved`

- Do not navigate to an untrusted location.
- Do not show any text highlight.
- Show an explicit unresolved badge/message so curators know localization failed rather than silently doing nothing.

## Cross-Page Quote Policy

Cross-page quotes are expected to fail as contiguous full-quote text-layer matches.

Required policy:

- do not promise a single contiguous highlight across multiple PDF pages
- keep `page_number` biased to the first page of the span
- try quote-derived fragment fallback after full-quote attempts fail
- prefer a fragment on the first page when available
- allow a trailing fragment on the next page when that is the only viable text-layer match
- classify successful fragment highlighting as `normalized_quote`

If the resolver can detect a likely cross-page quote internally, it may use adjacent-page text to confirm that condition, but the viewer contract remains single-search, single-highlight behavior.

## Page-Bias Requirements

Page bias is required for both resolver logic and viewer behavior.

Rules:

- When `page_number` is known, the viewer must set the current page to that page before dispatching quote-derived search.
- Before section fallback, reset the viewer to `page_number` again when a page hint exists.
- The resolver should prefer a hit on the hinted page over identical text elsewhere in the document.
- If repeated text appears on multiple pages and page bias changes which occurrence wins, the hinted page wins.

This is required to avoid wrong-page matches for repeated titles, captions, or boilerplate phrases.

## Contract Decision: No New EvidenceAnchor Fields in ALL-95

The spike suggested these possible additions:

- `page_numbers`
- `section_path`
- `leading_fragment`
- `trailing_fragment`
- `is_cross_page_candidate`

Decision for this ticket:

- do not add them now

Rationale:

- `viewer_search_text` can already carry the winning fragment candidate
- `normalized_text` can preserve the full normalized quote
- `page_number` is sufficient as the primary bias page when cross-page handling degrades to first-page navigation plus fragment highlight
- `section_title` and `subsection_title` are sufficient for the required degraded section search behavior
- downstream tickets should first implement the policy with the existing contract before expanding the shared schema

If `ALL-106`, `ALL-114`, or `ALL-126` finds a concrete blocker that cannot be solved with the current fields, that should be handled as an explicit follow-up contract change rather than inferred here.

## Implementation Notes for Downstream Tickets

- `ALL-106` should populate `normalized_text`, `viewer_search_text`, `page_number`, `section_title`, and offsets according to this policy.
- `ALL-108` should score quality from the best successful localization tier, not from intermediate failed attempts.
- `ALL-114` should type the viewer around quote search text, page bias, section fallback, and quality badge state.
- `ALL-126` should implement two highlight styles only:
  - primary quote highlight for `exact_quote` and `normalized_quote`
  - secondary/dimmed section highlight for `section_only` when section text itself is highlightable

## Bottom Line

The official localization contract is a page-biased, quote-first search policy over the real PDF text layer, with PDFX markdown supplying quote text plus page/section hints. Exact matches are ideal, normalized or fragment quote matches are acceptable and still count as quote localization, and all weaker outcomes must degrade visibly and honestly instead of pretending that precise highlighting succeeded.
