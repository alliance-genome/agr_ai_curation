# PDF Evidence Fuzzy Anchoring

## Goal

Make evidence highlighting resilient when the stored quote is close to, but not identical to, the text rendered in the PDF viewer.

This is specifically for cases where:

- the extracted quote came from Weaviate / model output / tool output and drifted slightly from the PDF text
- scientific formatting differs between sources
- punctuation, symbols, italics, markup wrappers, or token boundaries differ
- exact quote search lands on the right page but only highlights part of the quote

The desired behavior is:

1. localize the right page using the current PDF.js find flow
2. align the noisy stored quote against the actual text on that page
3. recover the best matching span from the PDF page text itself
4. highlight that recovered PDF span

In other words: treat the stored quote as a noisy selector, not as the literal text that must be highlighted.

## Why This Change

The current pipeline does several useful things already:

- exact quote search
- normalized quote search
- fragment search
- fragment expansion from page text
- PDF.js text-layer rect reconstruction

Those help, but they still rely too much on getting the query string into the same normalized form as the PDF text. In scientific papers that is fragile:

- `±` vs `+/-`
- italics or markdown wrappers like `*actin*`
- spaces around punctuation
- token boundary shifts like `(80` vs `80`
- PDF extraction quirks inside numbers, symbols, slashes, and hyphenated terms

Trying to normalize every possible drift will keep growing complexity and still miss edge cases.

A better model is:

- use exact and normalized search only to find the likely page
- once the page is known, trust the PDF page text as the source of truth
- use fuzzy anchoring to recover the span that most plausibly corresponds to the quote

## Prior Art

### Hypothesis fuzzy anchoring

The closest prior art is Hypothesis's annotation anchoring work:

- https://web.hypothes.is/blog/fuzzy-anchoring/

The core idea is:

- keep the quote text
- keep prefix and suffix context
- optionally keep expected position
- first do context-first fuzzy matching near the expected location
- if that fails, fall back to selector-only fuzzy matching
- when a high-confidence match is found, anchor to the text actually present in the document

This is the same problem shape we have.

Two especially relevant details from Hypothesis:

- they model the problem as matching against a plain-text representation of the rendered document
- they keep a bidirectional mapping between document text and DOM/text ranges so the recovered match can be highlighted in the real document

That matches our current viewer architecture well because we already build text-layer ranges and map them back to highlight rects.

### W3C Web Annotation model

- https://www.w3.org/TR/annotation-model/

The relevant selector model is:

- `TextQuoteSelector`: exact text plus optional prefix/suffix
- `TextPositionSelector`: start/end offsets when available

Conceptually, our evidence anchor should behave like:

- quote text = noisy selector
- page hint = position hint
- recovered page span = final anchored selection

### Related implementation building blocks

- https://blog.jonudell.net/2021/09/03/notes-for-an-annotation-sdk/
- https://github.com/taleinat/levenshtein-search
- https://github.com/antfu/diff-match-patch-es
- https://rapidfuzz.github.io/RapidFuzz/

Notes:

- `diff-match-patch` style fuzzy anchoring is a proven fit for browser-side text anchoring
- `levenshtein-search` is useful prior art for fuzzy substring search that returns offsets
- RapidFuzz is strong on the Python side, but our final highlight mapping still needs to happen in the browser against the rendered PDF text layer

## Chosen Approach

Implement a lightweight Hypothesis-style fuzzy anchoring pass in the frontend PDF viewer.

This should run only after we have already localized a page through the existing quote/fragment search flow.

### High-level flow

1. Try current exact / normalized / fragment PDF.js search flow.
2. If that resolves a page and we can read the page text layer, run fuzzy anchoring on that page.
3. Fuzzy anchoring compares the stored quote against the actual page text and tries to recover the best page span.
4. If the recovered span is good enough, highlight that page span directly.
5. If not, keep current fallback behavior:
   - fragment highlight
   - section fallback
   - page fallback

### Why page-local only

This keeps the fuzzy matcher safe and fast.

We do not want to fuzzy search the entire document first. We already have a better mechanism for document/page localization: PDF.js find plus page hints plus section fallback.

Fuzzy alignment should only answer:

> Given that this is likely the right page, what text span on this page most closely corresponds to the stored quote?

## Proposed Algorithm

### Inputs

- raw page text from the rendered text layer
- stored quote from evidence
- optional matched fragment query from the earlier search phase
- optional page match index from PDF.js

### Normalization policy

Keep normalization minimal. We still need some canonicalization to compare strings and map offsets back to raw text.

Allowed minimal normalization:

- collapse whitespace
- normalize smart quotes / dashes already handled by existing text normalization
- strip lightweight markdown wrappers already handled by existing sanitization
- lowercase for comparison

Important: do not depend on normalization alone to make the strings equal.

### Token-level local alignment

Use word-token local alignment on the resolved page.

Implementation sketch:

1. Build normalized page text with `buildNormalizedTextSourceMap(rawPageText)`.
2. Tokenize normalized page text into words with start/end character offsets.
3. Tokenize normalized quote the same way.
4. Score token similarity with a tolerant comparator:
   - exact normalized token match: strongest
   - edge-punctuation-trimmed token match: strong
   - numeric-token equivalence after trimming wrappers: strong
   - high-similarity short edit-distance match: medium
   - otherwise mismatch
5. Run Smith-Waterman style local alignment on quote tokens vs page tokens.
6. Recover the best aligned contiguous page span.
7. Compute confidence metrics:
   - quote token coverage
   - alignment score
   - whether the span reaches near the beginning of the quote
   - whether the span reaches near the end of the quote
   - whether the earlier matched fragment sits inside the recovered span
8. Accept only if confidence crosses conservative thresholds.

### Why token-level local alignment

This is a good fit because:

- it tolerates insertions/deletions/substitutions
- it is much more stable than giant normalization tables
- it can recover the actual page span even when symbols and punctuation drift
- it supports the user’s desired behavior: "80% match plus strong start/end anchors should be enough"

### Why not full-document fuzzy search first

Because page-local alignment is easier to trust.

The existing viewer pipeline already does a good job of:

- document loading
- page hints
- section fallback
- PDF.js page-level search

The new anchoring pass should refine the span on the resolved page, not replace all navigation logic.

## Acceptance Heuristics

These should be conservative enough to avoid wild false positives.

Suggested signals:

- recovered span covers a high fraction of quote tokens
- alignment starts near the quote start and ends near the quote end
- the recovered span includes the previously matched fragment if we had one
- the recovered span is meaningfully longer than the original fragment when fragment recovery is the goal

Suggested initial policy:

- accept if coverage is high and both leading and trailing anchors are present
- accept if coverage is very high even if one boundary is slightly weak
- reject if coverage is low or only a short middle fragment aligns

These thresholds may need tuning from real traces.

## Debugging Workflow

When a quote still fails in the live viewer, use the browser-side PDF evidence debug mode first before changing thresholds.

### Enable debug mode

- add `?pdfEvidenceDebug=1` to the app URL, or
- run in the console:
  - `window.__pdfViewerEvidenceDebug?.setEnabled(true)`

### Useful console helpers

The viewer now exposes:

- `window.__pdfViewerEvidenceDebug.getEntries()`
  - returns the rolling debug log for the current browser session
- `window.__pdfViewerEvidenceDebug.getLastResult()`
  - returns the last navigation result committed to viewer state
- `window.__pdfViewerEvidenceDebug.clearEntries()`
  - clears the in-memory log so a single repro is easier to inspect

### What the debug logs now tell us

For each quote navigation attempt, the logs should now show:

- the incoming quote, page hints, section hints, and candidate search queries
- the exact PDF.js find events and whether PDF.js selected a real match or only reported a misleading state
- page-local anchored quote recovery details
- document-wide anchored fallback details
- the strongest rejected anchoring window on each scanned page
- alignment-level details for near misses:
  - coverage
  - score
  - boundary-anchor status
  - leading contiguous match count
  - trailing contiguous match count
  - first leading mismatch token
  - first trailing mismatch token
  - a preview of matched token pairs
- text-range-to-rect mapping details:
  - the raw matched range
  - which text-layer segments intersected that range
  - the start/end segment indices
  - the segment text previews used to build highlight rectangles

### Current working hypothesis from direct probes

The direct string probes on the two main failure examples are important:

- the transgenic-fly quote aligns successfully once we compare it against the actual page text
- the long Actin quote also aligns successfully, even with `±` vs `+/-` drift and the extra citation token

That means the fuzzy quote alignment layer is already able to recover the intended span in those examples.

The remaining likely failure surface is downstream of alignment, in one of these steps:

- page hint selection before we reach the correct page
- PDF.js-selected match offsets
- text-layer raw-range reconstruction
- mapping the recovered raw range back into DOM/PDF.js rectangles

So when debugging a remaining failure, prioritize checking:

1. Did we localize the correct page?
2. Did `findAnchoredEvidenceSpanForPage()` recover the right `rawQuery` and `rawRange`?
3. Did `buildTextRangeSegmentDebugSnapshot()` show the expected text segments for that range?
4. Did rect creation fail, or did it only create boxes for a prefix of the intended range?

## Integration Points

### Current viewer path

Relevant file:

- `frontend/src/components/pdfViewer/PdfViewer.tsx`

Current behavior:

- iterate quote candidates
- use PDF.js find
- reconstruct text layer rects
- sometimes expand a fragment by checking if a longer exact normalized quote exists on the page

### Planned viewer change

After a page has been localized and text-layer rects exist for some candidate:

1. build the raw page text and its DOM-backed segments
2. run fuzzy anchoring for the full desired quote against that page
3. if fuzzy anchoring returns a higher-confidence longer span:
   - replace the highlighted rects with rects for the recovered page span
   - set `matchedQuery` to the recovered PDF page text
   - update the note to explain that we highlighted the best matching PDF span
4. otherwise preserve current behavior

Important:

- the recovered span should come from the PDF page text, not from the stored quote
- rects should be built from raw page offsets mapped back through the text layer

## Data Structures To Add

Likely new frontend utility file:

- `frontend/src/components/pdfViewer/textAnchoring.ts`

Possible exports:

- `findAnchoredEvidenceSpan(rawPageText, desiredQuote, options?)`
- `tokenizeNormalizedText(value)`
- `scoreAnchorTokenPair(a, b)`
- `recoverAnchoredQuoteSpan(...)`

Likely result shape:

```ts
interface AnchoredEvidenceSpan {
  rawQuery: string
  normalizedQuery: string
  rawStart: number
  rawEndExclusive: number
  coverage: number
  score: number
  leadingAnchorMatched: boolean
  trailingAnchorMatched: boolean
  includesPreferredAnchor: boolean
}
```

## Tests To Add

### Unit tests

New file:

- `frontend/src/components/pdfViewer/textAnchoring.test.ts`

Key cases:

1. Exact page-text recovery returns the full span.
2. Stored quote with markdown wrappers aligns to plain PDF text.
3. Stored quote with `±` aligns to page text using `+/-` or similar numeric formatting drift.
4. Alignment recovers a long span even when only a middle fragment matched exactly.
5. Low-confidence alignment is rejected.

### Viewer integration tests

Extend:

- `frontend/src/components/pdfViewer/PdfViewer.evidence.test.tsx`

Key cases:

1. The Actin-style long quote recovers the full visible page span instead of stopping around `80`.
2. The viewer sets a note indicating a recovered best-matching PDF span.
3. When fuzzy anchoring is low confidence, the existing fragment fallback still works.

## Non-goals

- Replacing PDF.js find entirely
- Fuzzy-searching whole documents before page localization
- Building a giant normalization table for every scientific symbol
- Changing backend evidence extraction as part of this frontend fix

## Follow-up Opportunities

If the initial local implementation works well, follow-up options include:

- store prefix/suffix context in evidence records explicitly
- store PDF page text offsets when the backend verifies evidence
- evaluate whether a maintained text-anchoring package is worth adopting
- use fuzzy anchoring confidence in the UI for diagnostics

## Implementation Reminder After Context Reset

If context gets reset, the next steps should be:

1. Add `textAnchoring.ts` with a page-local fuzzy alignment helper.
2. Use `buildNormalizedTextSourceMap` so recovered normalized offsets can map back to raw page text.
3. In `PdfViewer.tsx`, after page localization, run the anchoring helper on the resolved page.
4. If the helper returns a high-confidence longer span, build rects directly from that span and highlight it.
5. Add the Actin regression and at least one low-confidence rejection test.
6. Run targeted frontend tests and `npm run build`.

## Sources

- Hypothesis fuzzy anchoring: https://web.hypothes.is/blog/fuzzy-anchoring/
- W3C Web Annotation model: https://www.w3.org/TR/annotation-model/
- Annotation SDK notes: https://blog.jonudell.net/2021/09/03/notes-for-an-annotation-sdk/
- `levenshtein-search`: https://github.com/taleinat/levenshtein-search
- `diff-match-patch-es`: https://github.com/antfu/diff-match-patch-es
- RapidFuzz docs: https://rapidfuzz.github.io/RapidFuzz/
