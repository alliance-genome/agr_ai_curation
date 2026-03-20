# ALL-94 Spike: PDFX Markdown to PDF Text-Layer Viewer Navigation

## Scope

This spike tests whether PDFX markdown quotes can drive navigation and highlighting in the current PDF.js iframe viewer without shipping the production evidence UX.

Prototype location:

- `frontend/src/components/pdfViewer/PdfViewer.tsx`

Prototype entry point:

- Dev-only harness: `window.__pdfViewerEvidenceSpike({ quote, pageNumber, pageNumbers, sectionTitle, sectionPath })`

Prototype behavior:

- Derive search candidates from the PDFX quote.
- Run PDF.js `findController` searches against the loaded PDF text layer.
- Prefer quote-derived matches first.
- Fall back to section metadata search.
- Fall back to page navigation when text-layer search does not resolve.

## Method

Representative document:

- PDF: `backend/tests/fixtures/micropub-biology-001725.pdf`
- PDFX fixture: `backend/tests/fixtures/micropub-biology-001725_pdfx.json`

Validation path:

- Real browser session against the checked-in PDF.js viewer served locally from the repo root.
- Programmatic search through `window.PDFViewerApplication.eventBus.dispatch('find', ...)`.
- Comparison against 44 searchable PDFX fixture elements with page metadata.

Normalization/candidate rules tested in the prototype:

- Collapse repeated whitespace and newlines to single spaces.
- Normalize curly quotes/apostrophes and Unicode dashes to ASCII equivalents.
- Remove soft hyphens and normalize NBSPs.
- Try shorter sentence/fragment candidates for long quotes.

## Results

Coverage on the 44 searchable PDFX elements in the fixture:

- Raw quote found on the hinted page text: `8 / 44` (18%).
- Normalized quote found on the hinted page text: `28 / 44` (64%).
- Quote candidate chain found on the hinted page text: `37 / 44` (84%).
- Section-title fallback recovered: `6 / 44` (14%).
- Unresolved after quote + section fallback: `1 / 44` (2%).

The single unresolved example was a footer artifact:

- Page 3: `7/10/2025 Open Access`

Interpretation:

- Raw PDFX markdown strings are not reliable enough by themselves.
- Lightweight normalization materially improves hit rate.
- A small fragment fallback closes most of the remaining gap.
- Section/page metadata is still needed for the tail of failures and for repeated text.

## Match Examples

Normalization success:

- PDFX title text contains repeated spaces:
  - `Analysis  of  Transcripts  in  the  Fly  Cell  Atlas  Reveals  Additional  Cell Populations ...`
- The rendered PDF text layer contains the same title with normalized spacing.
- Raw match failed, normalized match succeeded on page 1.

Fragment fallback success:

- A long Figure 1 caption on page 2 failed as a full quote even after normalization.
- The trailing fragment
  - `marker genes across all annotated follicle cell clusters. The size of each dot represents the percentage of cells within a cluster expressing a given`
  matched in PDF.js.
- PDF.js rendered live text-layer highlights for that fragment in the viewer.
- Observed viewer result during the browser probe:
  - selected page: 2
  - highlighted spans: 2

Section fallback success:

- Six fixture elements still missed after quote candidates but their `section_title` resolved a relevant page.
- This is viable as a degraded mode even when the exact quote cannot be highlighted.

## Page Bias and Repeated Text

Repeated text can select the wrong occurrence unless the viewer starts from a hinted page.

Observed behavior with the repeated title string:

- Without resetting the viewer page, PDF.js selected page 6 after a previous search left the viewer near the end of the document.
- After explicitly setting the viewer to page 1 first, the same normalized title search selected page 1.

Implication:

- Page metadata is not optional if the same phrase can occur multiple times.
- The viewer should bias search from the best known page before dispatching `find`.

## Cross-Page Behavior

The fixture did not contain a natural single PDFX element that already spanned two pages, so I constructed a page-break stress case from the end of page 2 plus the start of page 3.

Constructed quote:

- `dot represents the percentage of cells within a cluster expressing a given 7/10/2025 - Open Access gene, while the color scale indicates average expression level. F) UMAP visualization displaying annotated cell populations,`

Observed behavior:

- Not present on page 2 alone.
- Not present on page 3 alone.
- Present only when page 2 and page 3 normalized text are concatenated.
- The leading fragment matched page 2.
- The trailing fragment matched page 3.

Implication:

- Exact whole-quote matching does not handle page breaks by itself.
- Production highlight logic should not expect one contiguous highlight across multiple pages.
- Viable degraded behavior is:
  - detect/page-pair search when needed, then
  - navigate to the first page, and
  - highlight a leading or trailing fragment instead of the whole quote.

## Performance Notes

Observed in the real PDF.js browser probe on this 6-page fixture:

- Individual PDF.js search attempts settled in roughly `250-385 ms`.
- Candidate chains multiply linearly with the number of retries.
- Repeated search is acceptable for a single evidence focus action, but production code should avoid long retry lists.

Practical guidance:

- Keep the candidate list small and deterministic.
- Start from the hinted page to reduce ambiguous matches.
- Cache derived candidates for the active evidence anchor rather than recomputing during repeated focus events.
- Treat PDF.js `updatefindcontrolstate` as authoritative for `FOUND`/`WRAPPED` even if `updatefindmatchescount` arrives a tick later; the viewer can visibly highlight a hit before `matchesCount.total` is populated.

## Failure Modes

- Repeated text can land on the wrong page without page bias.
- Long captions/paragraphs can fail as whole-quote searches even when shorter fragments are present.
- Footer/header artifacts may be too noisy to justify highlight behavior.
- Cross-page quotes need fragment fallback; a single whole-quote highlight is not realistic in the current viewer.

## Recommendation

Go/no-go:

- `Go` on production behavior that tries exact/normalized PDF.js text-layer search first.
- `No-go` on relying on raw PDFX markdown quote strings alone.
- `Go` on degraded fallback to section metadata and page navigation when quote search does not resolve cleanly.
- `No-go` on promising full multi-page contiguous highlighting in the current viewer.

Recommended minimum viewer-consumed evidence fields for later tickets:

- `viewer_search_text`
  - The primary quote string to search.
- `page_number` or `page_numbers`
  - Used to bias repeated-text resolution and page fallback.
- `section_title`
  - Primary degraded fallback when quote search fails.
- `section_path`
  - Secondary degraded fallback when the leaf section title is too generic.
- `locator_quality`
  - Indicates whether the anchor is exact, normalized, fragment, or fallback-only.

Strongly helpful optional fields:

- `leading_fragment`
  - Precomputed short excerpt from the start of a long quote.
- `trailing_fragment`
  - Precomputed short excerpt from the end of a long quote.
- `is_cross_page_candidate`
  - Lets the viewer skip unrealistic full-quote expectations and go straight to fragment/page-pair logic.

Downstream ticket guidance:

- `ALL-95`
  - Specify normalization rules and fallback ordering explicitly.
- `ALL-114`
  - Type the viewer input around search text, page hints, section hints, and locator quality.
- `ALL-126`
  - Implement exact/normalized text-layer highlighting first, then degrade to section/page navigation when matching is weak or absent.

## Bottom Line

PDFX markdown can drive the current PDF.js viewer effectively if the viewer treats quote search as a ranked search problem rather than a raw-string lookup. Exact/normalized text-layer match should be the first attempt, page/section metadata should guide the search and fallback, and long/cross-page quotes should degrade to fragment highlighting plus page navigation instead of blocking the UX.
