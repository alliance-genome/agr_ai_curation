# Chat + Curation Evidence Alignment Plan

**Date:** 2026-04-13
**Status:** Draft
**Target:** Production hotfix after verified frontend regression coverage and dev/prod validation

## Problem Statement

Production currently shows an inconsistent evidence-navigation experience:

- The same evidence quote can highlight correctly from the chat surface.
- The curation review surface can fail to highlight that same quote and degrade to a page-only navigation result.

This violates an important product invariant:

> If the same PDF, same quote, and same evidence anchor are available, chat and curation must behave identically when navigating the user to evidence in the viewer.

The concrete user-visible symptom is the curation review banner:

> "Evidence on this page. Quote text was not matched reliably enough to highlight."

That is not a separate curation-only error. It is the shared PDF viewer's degraded `page_only` result. The immediate conclusion is that chat and curation do not yet feed the shared viewer a truly identical navigation contract.

## Why This Matters

Evidence navigation is not a cosmetic feature. It is part of the curator trust loop:

1. The system extracts or proposes a statement.
2. The curator clicks the evidence.
3. The viewer shows the exact supporting quote in the paper.

If one surface succeeds and the other fails for the same quote, the curator cannot tell whether the issue is:

- bad retrieval
- bad evidence persistence
- bad workspace preparation
- bad PDF localization
- or just a UI mismatch

That ambiguity is unacceptable for a curation product. Chat and curation should not feel like two separate evidence systems.

## Reproduction Notes

The production repro that triggered this investigation involved the `crb` / `crumbs` paper. The same evidence was reported to work correctly in chat but fail in the curation interface.

One failing quote called out during investigation:

> "these, crb 11A22 (null allele) and crb 8F105 (point mutation encoding a truncated protein lacking 23 amino acids), display abnormal PRC morphology in adult eyes, with bulky and closely apposed rhabdomeres"

The user expectation is correct:

- chat and curation are both highlighting evidence in the same PDF viewer
- therefore they should use one shared quote-centric evidence-navigation process

## Current Architecture

### What Is Already Shared

Both chat and curation eventually route into the same PDF viewer engine:

- [frontend/src/components/pdfViewer/PdfViewer.tsx](../../frontend/src/components/pdfViewer/PdfViewer.tsx)

The key shared path is the viewer's evidence navigation pipeline, especially:

- `executeEvidenceNavigation(...)`
- `synchronizeNativePdfJsQuoteHighlight(...)`
- `verifyNativePdfJsOccurrenceMatchesTarget(...)`

This means the actual PDF quote localization and native PDF.js highlight synchronization are already shared.

### What Is Not Shared

The viewer is shared, but the input path is still split.

#### Chat-side command construction

- [frontend/src/components/Chat/chatEvidenceNavigation.ts](../../frontend/src/components/Chat/chatEvidenceNavigation.ts)

Chat currently:

- starts from `EvidenceRecord`
- takes `verified_quote`
- creates a quote-centric command
- passes page / section / subsection / chunk hints

#### Curation-side command construction

- [frontend/src/features/curation/entityTable/entityTagNavigation.ts](../../frontend/src/features/curation/entityTable/entityTagNavigation.ts)

Curation review currently:

- starts from `CurationEvidenceRecord` when available
- otherwise falls back to flattened preview evidence on `EntityTag`
- takes `sentence_text` or `snippet_text`
- creates a quote-centric command
- passes persisted anchor metadata from the workspace

#### Generic evidence hook path

- [frontend/src/features/curation/evidence/useEvidenceNavigation.ts](../../frontend/src/features/curation/evidence/useEvidenceNavigation.ts)

This hook still builds commands more directly from persisted anchor metadata and does not currently guarantee the same quote-rewriting behavior as the chat and entity-table builders.

## What We Learned In The Code

### 1. The curation review table is closer to the correct design than expected

The current entity-table path is already using the quote-centric helper:

- [frontend/src/features/curation/evidence/navigationCommandBuilder.ts](../../frontend/src/features/curation/evidence/navigationCommandBuilder.ts)

That helper intentionally rewrites:

- `snippet_text`
- `sentence_text`
- `normalized_text`
- `viewer_search_text`

to the exact quote chosen for navigation.

This is a good design choice. It reduces drift between persisted anchor metadata and the quote the viewer should search/highlight.

### 2. Chat and curation still use separate source adapters

Even though both paths call the same quote-centric helper, they do not share one canonical source adapter that says:

> given any evidence record intended for user-visible PDF navigation, this is the only valid way to turn it into a viewer command

Today, that adaptation logic is duplicated in multiple places:

- chat adapter
- curation entity-table adapter
- generic curation evidence hook

That duplication is enough for subtle drift to appear over time.

### 3. The failure happens after page localization, not before

The degraded curation behavior is the viewer's own `page_only` result. That tells us:

- the viewer probably found the page
- but strict quote verification failed

So this is not a pure "could not find the document" or "could not find the page" error. It is a quote verification mismatch after navigation got reasonably close.

### 4. A rescue retry is not the right first fix

One possible mitigation is:

- if hinted localization fails
- retry the same quote search without page hints

That could make some cases recover, but it is not the correct first move. It would hide divergence between surfaces instead of eliminating it.

This plan therefore rejects "try again without hints" as the primary fix.

## Probable Failure Mode

The most likely root cause is:

1. Chat and curation construct slightly different evidence-navigation commands for the same logical quote.
2. Those commands carry different combinations of quote text and anchor hints.
3. The shared viewer localizes and verifies them differently.
4. Chat succeeds while curation degrades to `page_only`.

This is especially plausible when:

- the persisted curation anchor carries a noisier `viewer_search_text`
- the sentence and snippet fields differ
- the page / section hints are stale or overly narrow
- or the curation path relies on a flattened preview evidence object rather than the richer `CurationEvidenceRecord`

## Product-Level Design Decision

We should treat evidence navigation as a single frontend contract, not as chat behavior plus curation behavior.

The contract should be:

1. A user-visible evidence click resolves to one canonical quote-centric navigation command.
2. That command is built the same way no matter whether the click came from chat, curation review, or a future evidence chip surface.
3. The shared viewer consumes that canonical command.

This means the right place to fix the problem is upstream of the viewer:

- unify the source adapters first
- only adjust viewer localization behavior if a true shared viewer bug remains after input alignment

## Implementation Strategy

### Slice 1: Canonicalize command construction

Create a single shared adapter module for evidence records intended for PDF navigation.

Target responsibilities:

- accept chat evidence records
- accept curation evidence records
- accept the temporary flattened `EntityTag.evidence` shape if necessary
- normalize the user-visible quote text
- build the canonical quote-centric `EvidenceNavigationCommand`

This slice should remove command-building duplication from:

- `chatEvidenceNavigation.ts`
- `entityTagNavigation.ts`
- and any curation hooks that still build direct viewer commands

Expected outcome:

- the same quote and same logical anchor yield the same viewer command structure regardless of surface

### Slice 2: Force the curation entity-table path onto canonical evidence records

The review surface should prefer full `CurationEvidenceRecord` objects whenever available and treat flattened preview evidence as temporary compatibility only.

Goals:

- always navigate from the richer evidence record path in the normal review flow
- keep the fallback path small, explicit, and easy to remove later
- avoid silently using lower-fidelity evidence when better evidence is already loaded

### Slice 3: Add cross-surface regression coverage

Add tests that prove the architectural invariant directly:

- same logical quote from chat and curation produces equivalent viewer commands
- noisier persisted anchor fields do not change the chosen viewer search text
- the crb reproduction quote is preserved as the navigation quote

This should include:

- command-builder unit tests
- entity-table regression tests
- chat navigation tests

If practical, add a viewer-level regression test that feeds two equivalent commands from chat and curation and expects the same `PdfViewerNavigationResult`.

### Slice 4: Only then evaluate viewer-level fixes

After command alignment is in place:

- reproduce the original failing quote again
- inspect whether the shared viewer still degrades

If a mismatch remains after canonical input alignment, then and only then should we consider a shared viewer fix. That fix must improve both surfaces together, not rescue only curation.

## Files In Scope

Primary implementation files:

- [frontend/src/components/Chat/chatEvidenceNavigation.ts](../../frontend/src/components/Chat/chatEvidenceNavigation.ts)
- [frontend/src/features/curation/entityTable/entityTagNavigation.ts](../../frontend/src/features/curation/entityTable/entityTagNavigation.ts)
- [frontend/src/features/curation/evidence/navigationCommandBuilder.ts](../../frontend/src/features/curation/evidence/navigationCommandBuilder.ts)
- [frontend/src/features/curation/evidence/useEvidenceNavigation.ts](../../frontend/src/features/curation/evidence/useEvidenceNavigation.ts)

Primary regression targets:

- [frontend/src/features/curation/entityTable/entityTagNavigation.test.ts](../../frontend/src/features/curation/entityTable/entityTagNavigation.test.ts)
- chat evidence-navigation tests to be added or expanded
- [frontend/src/components/pdfViewer/PdfViewer.evidence.test.tsx](../../frontend/src/components/pdfViewer/PdfViewer.evidence.test.tsx)

Reference runtime files:

- [frontend/src/features/curation/entityTable/EntityTagTable.tsx](../../frontend/src/features/curation/entityTable/EntityTagTable.tsx)
- [frontend/src/components/Chat/EvidenceQuote.tsx](../../frontend/src/components/Chat/EvidenceQuote.tsx)

## Explicit Non-Goals For This Hotfix

These are intentionally out of scope for the first production fix:

- adding a second localization retry when a hinted quote fails
- broad viewer-search heuristic rewrites
- backend evidence resolver changes
- changing curation evidence persistence contracts
- redesigning the evidence UI

Those may become follow-up work, but they should not be mixed into the first alignment hotfix unless the canonical-input slice proves insufficient.

## Validation Plan

### Required local validation

- focused frontend unit tests for chat evidence navigation
- focused frontend unit tests for curation entity-table navigation
- focused viewer evidence tests
- frontend production-style build

### Required functional validation

1. Reproduce the `crb` quote from chat.
2. Open the same quote from the curation review page.
3. Confirm both paths:
   - navigate to the same page
   - highlight the same quote
   - avoid the degraded "quote not matched reliably enough" banner

### Production hotfix criteria

Only consider a prod hotfix ready if:

- the canonical command path is merged locally
- targeted regression coverage is green
- the exact observed prod quote works from both chat and curation
- no new viewer regressions appear on nearby evidence examples

## Recommended Next Steps

1. Implement the canonical evidence-navigation adapter.
2. Refactor chat and curation entity-table to use it.
3. Add regression tests for cross-surface equivalence and the `crb` quote family.
4. Reproduce the failing workflow again.
5. If it is fixed, prepare a narrow production hotfix.
6. If it still fails, continue with a shared viewer investigation using the now-aligned command input.
