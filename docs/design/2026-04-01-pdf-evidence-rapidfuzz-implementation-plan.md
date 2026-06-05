# 2026-04-01 PDF Evidence RapidFuzz Implementation Plan

## Goal

Replace the current quote-localization path in the PDF viewer with a leaner `RapidFuzz`-based implementation that:

- localizes evidence quotes against real PDF.js search text
- keeps native PDF.js highlighting as the final user-visible source of truth
- removes the current custom TypeScript quote-anchoring complexity where possible
- preserves section/page fallback behavior when quote localization is weak or cannot be verified

This plan is intentionally verbose so it can be used as a restart document if implementation context is lost.

## Why We Are Changing Direction

The current viewer quote flow is too complicated and too brittle.

It currently includes a combination of:

- exact / sanitized / normalized candidate ladder
- bounded window fragments
- PDF.js find-controller state interpretation
- custom stitched text recovery
- custom `textAnchoring.ts` alignment logic
- text-layer / DOM remapping logic
- transactional native re-sync after custom recovery

That stack was built to work around quote drift, but the April 1 benchmark work showed a cleaner answer:

- refreshed upstream quote quality already improved literal/native matching a lot
- the remaining problem is still fundamentally quote-text-to-PDF.js-text matching
- the strongest matching engine we benchmarked was Python `RapidFuzz`

The refreshed 100-quote bakeoff currently says:

- exact literal search: `67/100`
- `RapidFuzz`: `100/100` page matches
- `RapidFuzz` mean span F1: `0.9918`
- `RapidFuzz` had `99/100` matches with both reference and candidate coverage `>= 0.95`

`edlib` was slightly tighter on span boundaries when it succeeded, but `RapidFuzz` remained the strongest overall first-choice matcher because it did not miss pages in the refreshed benchmark.

## Core Design Decision

Use `RapidFuzz` on the backend as the quote-localization engine.

Do not try to force the benchmark-winning Python library into the browser.

The frontend still owns:

- PDF.js page text extraction
- native PDF.js highlight verification
- section/page fallback
- stale-request handling

The backend will own:

- quote-to-page-text fuzzy localization
- page/window scoring
- anchor-page and span selection
- cross-page stitched-range reconstruction

## Intended Runtime Flow

### Quote path

1. Frontend extracts the PDF.js page corpus from `_pageContents`.
2. Frontend sends the quote plus page text to a small backend matcher endpoint.
3. Backend uses `RapidFuzz` to find the best matching span.
4. Backend returns:
   - best score
   - matched page
   - anchor-page query
   - exact anchor-page raw range
   - cross-page page ranges when applicable
5. Frontend uses the returned anchor-page query and raw range to ask PDF.js for a native highlight.
6. Frontend verifies the selected native occurrence matches the intended raw range.
7. If native verification succeeds:
   - commit native quote highlight
8. If native verification fails:
   - degrade to section/page fallback

### Section/page fallback path

This remains frontend-side and should stay much simpler than the quote path:

- section title search
- section text-layer localization when available
- page fallback when quote and section cannot be trusted

## High-Level Simplification Targets

The point is not to bolt `RapidFuzz` onto the old architecture.

The point is to remove old quote-recovery machinery and leave a much narrower path.

### Keep

- native PDF.js quote verification and selection logic
- request-token / stale async protection
- section fallback and page fallback behavior
- viewer result reporting / debug logging

### Remove or sharply reduce

- multi-step quote candidate ladder for quote localization
- window fragment quote retries
- custom `textAnchoring.ts` quote recovery
- quote upgrade from reconstructed text-layer strings
- bespoke quote expansion heuristics in `PdfViewer.tsx`

### Likely remaining quote flow after the cutover

- one backend `RapidFuzz` localization request
- one native PDF.js verification step
- fallback if verification fails

## Proposed Architecture

## 1. Backend matcher module

Create a small backend matcher module, likely under:

- `backend/src/lib/pdf_viewer/rapidfuzz_matcher.py`

Responsibilities:

- accept quote text plus ordered PDF.js page text
- optionally bias toward preferred page hints
- evaluate single-page and stitched page-window candidates
- return the best candidate span in a deterministic, serializable shape

### Matching behavior

Initial implementation should mirror the benchmarked winner as closely as possible:

- `RapidFuzz` partial ratio alignment
- raw quote text, not aggressive rewrite-heavy preprocessing
- deterministic tie-breaking

### Candidate corpora

Support at least:

- single-page corpora
- page-centered stitched corpora using previous page tail + current page + next page head

This keeps cross-page support without recreating the old TypeScript anchoring logic.

### Mapping requirements

The backend matcher must return raw span positions that are meaningful against the exact PDF.js page text provided by the frontend.

This is important because:

- we no longer want to trust `pageMatches/pageMatchesLength` as slice boundaries
- we want backend and frontend to agree on one text space: the PDF.js `_pageContents` strings sent by the frontend

## 2. Backend API endpoint

Likely home:

- `backend/src/api/pdf_viewer.py`

Add a small PDF-viewer-scoped POST endpoint for fuzzy evidence matching.

Suggested direction:

- `POST /api/pdf-viewer/evidence/fuzzy-match`

Request should contain:

- quote text
- page hints
- page corpus from PDF.js
- optional threshold settings if we want them tunable later

Response should contain:

- `found`
- matcher score
- match strategy
- matched page
- anchor-page query
- anchor-page raw range
- full matched query
- page ranges
- cross-page flag

## 3. Frontend service layer

Add a small frontend service wrapper rather than calling `fetch` inline from `PdfViewer.tsx`.

Likely file:

- `frontend/src/features/curation/services/curationWorkspaceService.ts`
  or
- a new PDF-viewer-specific service file if that is cleaner

The goal is to keep the network boundary explicit and testable.

## 4. Frontend viewer cutover

Primary file:

- `frontend/src/components/pdfViewer/PdfViewer.tsx`

The quote path should be rewritten so that:

- it gathers page text
- calls the new backend matcher
- uses the returned anchor-page query/range for native PDF.js verification
- only falls back to section/page if that fails

### Expected removals / reductions in `PdfViewer.tsx`

These are the strongest candidates to delete or stop using:

- `buildEvidenceSpikeQuoteCandidates`
- `buildEvidenceSpikeWindowFragments`
- `expandNativeEvidenceQuoteFromPageContents`
- `findExpandedEvidenceQueryFromPageText`
- all quote-path use of `findAnchoredEvidenceSpan`

The exact delete set may shift during implementation, but the intended direction is:

- no more frontend custom quote anchoring algorithm
- no more quote window-fragment ladder

## 5. Remove or retire `textAnchoring.ts`

Current file:

- `frontend/src/components/pdfViewer/textAnchoring.ts`

This file exists to solve the quote-localization problem in the frontend.

If the new backend matcher fully replaces that need, the intended end state is:

- remove the file entirely, or
- leave only any tiny utilities still truly needed elsewhere

The implementation should prefer deletion over keeping dead complexity around.

## Runtime quality rules

The runtime matcher should behave conservatively.

We are not trying to make fuzzy matching “always return something.”

We are trying to make it:

- reliable
- explainable
- easy to degrade when confidence is weak

### Acceptance intent

Prefer returning no fuzzy quote match over returning a tiny or misleading span.

### Native rendering rule

Even after fuzzy localization succeeds, the user-visible quote highlight should still be native PDF.js only.

The backend matcher should localize the span.
The frontend viewer should still verify and render via PDF.js.

## Planned file changes

### New or updated docs

- `docs/design/2026-04-01-pdf-evidence-rapidfuzz-implementation-plan.md`
- update `docs/design/2026-04-01-pdf-evidence-fuzzy-search-experiments.md` if implementation outcomes materially refine the experiment takeaways

### Backend

- `backend/requirements.txt`
- `backend/requirements.lock.txt`
- `backend/src/api/pdf_viewer.py`
- likely new matcher module under `backend/src/lib/pdf_viewer/`
- tests under `backend/tests/unit/` and maybe `backend/tests/contract/`

### Frontend

- `frontend/src/components/pdfViewer/PdfViewer.tsx`
- likely new fetch helper/service file
- `frontend/src/components/pdfViewer/PdfViewer.evidence.test.tsx`
- remove or retire `frontend/src/components/pdfViewer/textAnchoring.ts`
- remove or retire `frontend/src/components/pdfViewer/textAnchoring.test.ts`

## Implementation checklist

### Phase 1: backend matcher foundation

- [ ] Add `RapidFuzz` dependency to backend requirements
- [ ] Create backend matcher request/response models
- [ ] Implement single-page `RapidFuzz` alignment helper
- [ ] Implement stitched page-window support
- [ ] Implement deterministic candidate ranking / tie-breaking
- [ ] Add score / acceptance guardrails
- [ ] Unit test the matcher logic against representative quotes

### Phase 2: backend API

- [ ] Add PDF-viewer fuzzy-match endpoint
- [ ] Add contract coverage for the new endpoint
- [ ] Add API unit/integration tests for request/response behavior

### Phase 3: frontend integration

- [ ] Add frontend service wrapper for fuzzy-match API
- [ ] Extract PDF.js page corpus from the live viewer
- [ ] Replace quote candidate ladder with one backend matcher request
- [ ] Feed returned anchor-page query/range into native PDF.js verification
- [ ] Keep stale-request protection intact
- [ ] Preserve section/page fallback behavior

### Phase 4: simplification / deletion

- [ ] Remove quote-path use of `textAnchoring.ts`
- [ ] Remove quote window-fragment machinery
- [ ] Remove old quote upgrade / expansion helpers that are no longer needed
- [ ] Delete obsolete tests that only describe the old anchoring behavior
- [ ] Add focused tests for the new quote path

### Phase 5: validation

- [ ] Backend syntax validation
- [ ] Backend unit tests for matcher/API
- [ ] Frontend evidence tests
- [ ] Frontend build
- [ ] Re-run the benchmark utilities if the runtime implementation changes how spans are interpreted

### Phase 6: final review

- [ ] Run a `gpt-5.4-mini` `xhigh` sub-agent code review on the completed implementation
- [ ] Address any actionable findings from that review

## Testing priorities

The new tests should focus on outcomes, not legacy implementation details.

### Must-have runtime cases

- quote localizes and verifies natively on the preferred page
- quote localizes to a nearby page when page hint is wrong
- quote localizes across a page boundary and keeps best native anchor-page highlight
- quote score is weak and degrades cleanly to section/page fallback
- stale async quote result does not override a newer navigation
- repeated substring occurrence verifies the intended raw range, not just any identical text

## Risks to watch

### 1. Over-trusting fuzzy scores

The backend matcher may still produce a best candidate even when the quote is bad.

We need conservative runtime acceptance.

### 2. Shipping too much page text over the wire

Sending the full PDF.js corpus to the backend per click is probably acceptable for current paper sizes, but we should keep an eye on payload size and latency.

### 3. Keeping too much old code around

The biggest non-functional risk is ending up with:

- `RapidFuzz` matcher
- plus all the old quote candidate machinery

That would be worse than the current state.

This implementation should prefer deletion and replacement rather than additive layering.

### 4. Accidentally mixing text spaces again

The new backend matcher should only operate on the exact page text strings provided by the frontend from PDF.js.

Do not reintroduce reconstructed text-layer-builder strings as a second source of truth.

## Success criteria

The cutover is successful when:

- the quote path is visibly simpler
- `textAnchoring.ts` is no longer needed for quote localization
- the viewer still ends with native PDF.js highlighting
- section/page fallback still works
- tests pass
- the runtime behavior aligns with the benchmark evidence that made `RapidFuzz` the best first-choice matcher

## Restart note

If implementation context is lost, restart from these files first:

- `docs/design/2026-04-01-pdf-evidence-rapidfuzz-implementation-plan.md`
- `docs/design/2026-04-01-pdf-evidence-fuzzy-search-experiments.md`
- `scripts/utilities/pdf_text_matcher_bakeoff.py`
- `frontend/src/components/pdfViewer/PdfViewer.tsx`
- `frontend/src/components/pdfViewer/textAnchoring.ts`
- `backend/src/api/pdf_viewer.py`
