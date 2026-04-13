# Persistent PDF.js Host Strategy

## Status

This document is the source of truth for fixing Home/Curation PDF evidence
drift the correct way.

The goal is not to add more viewer-side rescue logic. The goal is to make
curation use the same live PDF.js host that chat already uses successfully.

## Goal

Make chat evidence navigation and curation-workspace evidence navigation behave
identically because they are talking to the same mounted PDF.js viewer.

In practice, that means:

- one mounted `PdfViewer`
- one live `PDFViewerApplication`
- one `findController`
- one text-layer lifecycle
- one current selected match
- one native PDF.js highlight path

The same quote on the same document should not behave differently depending on
whether the click came from Home or Curation Workspace.

Persistence scope for this refactor:

- persistence is required across `Home <-> Curation Workspace` transitions
- persistence is **not** required when leaving viewer-bearing routes entirely
  for unrelated routes like Agent Studio or Batch

## Current Problem

We already share most of the evidence-navigation stack:

- chat and curation both build quote-centric commands through
  `frontend/src/features/curation/evidence/navigationSourceAdapters.ts`
- both end up in the same viewer implementation in
  `frontend/src/components/pdfViewer/PdfViewer.tsx`
- both use the same global event layer in
  `frontend/src/components/pdfViewer/pdfEvents.ts`

But we still mount two different viewers:

- `frontend/src/pages/HomePage.tsx` mounts `<PdfViewer />`
- `frontend/src/pages/CurationWorkspacePage.tsx` mounts `<PdfViewer />`

That means the routes do **not** share:

- rendered text-layer DOM
- current page
- selected native PDF.js occurrence
- warmed page-text corpus
- `findController` internal state
- iframe lifecycle timing

That split is the real source of route drift.

## Secondary Ownership Seam

Viewer drift is not the only route seam.

Today, active-document ownership is also split:

- Home drives `dispatchPDFDocumentChanged(...)` indirectly from
  `frontend/src/components/Chat.tsx`
- Curation drives `dispatchPDFDocumentChanged(...)` directly from
  `frontend/src/pages/CurationWorkspacePage.tsx`

That means a one-host refactor also needs an explicit contract for who is
allowed to drive the shared viewer's active document during Home/Curation route
transitions.

The refactor should preserve the current event contract at first, but it must
add tests that prove stale document-change events from one route do not stomp
the active document selected by the other route.

## What We Are Not Doing

We are not trying to "improve" the quote verifier first.

We are not trying to give curation a new overlay-only exact-match mode.

We are not trying to keep two viewer instances synchronized with more rescue
branches.

Chat already represents the working baseline. This plan is about making
curation use that same live PDF.js path, not inventing a third behavior.

## Key Architecture Finding

The earlier "app-level provider + portal into the active slot" idea is not the
best primary design.

Why:

- if the portal target DOM node changes between Home and Curation, React can
  recreate the portal subtree
- if the subtree is recreated, the `PdfViewer` remounts
- if the viewer remounts, we lose the very state continuity we are trying to
  preserve

So a provider/slot abstraction may still be useful later, but it should not be
the first architectural anchor for persistence.

## Recommended Host Placement

Use a persistent **route layout host** in `frontend/src/App.tsx` that wraps the
viewer-bearing routes only.

Recommended shape:

- `App.tsx` owns a nested layout route for Home and Curation Workspace
- that layout route mounts one stable `PdfViewer`
- the child routes render only the non-viewer content via `<Outlet />`
- route transitions between Home and Curation keep the same viewer instance

This is the least invasive insertion point because:

- it keeps the stable mount above both routes that need the viewer
- it avoids portal target churn
- it avoids mounting a viewer on unrelated routes like Agent Studio or Batch
- it uses React Router's native "layout route stays mounted while children
  change" behavior

## Recommended Shape

### New Layout Route

Add a persistent layout component, for example:

- `frontend/src/components/pdfViewer/PersistentPdfWorkspaceLayout.tsx`

Responsibilities:

- own the single `PdfViewer`
- own the outer desktop/mobile viewer-bearing layout
- render an `<Outlet />` for route-specific non-viewer content
- keep the viewer mounted while switching between Home and Curation Workspace
- define the route-level ownership boundary for the active document

### Route Structure

In `frontend/src/App.tsx`, move these routes under that layout:

- `/`
- `/curation/:sessionId`
- `/curation/:sessionId/:candidateId`

All other routes stay outside the viewer layout.

### Home Page After Refactor

`HomePage.tsx` should stop mounting `PdfViewer` directly.

Instead, it should become "Home right-side content":

- chat panel
- right panel
- session handling
- document-change dispatches

The persistent layout owns the left viewer panel.

### Curation Page After Refactor

`CurationWorkspacePage.tsx` should stop mounting `PdfViewer` directly.

Instead, it should become "Curation right-side content":

- workspace header
- entity table
- workspace actions
- document-change dispatches

The persistent layout owns the left viewer panel.

### WorkspaceShell Simplification

`WorkspaceShell.tsx` should stop owning `pdfSlot`.

After migration it should represent the curation-side content layout only, not a
full "viewer plus table" shell.

That is cleaner compositionally:

- the viewer host belongs to the persistent route layout
- the workspace shell belongs to curation content

## Why This Is Better Than A Generic Provider First

This route-layout approach is:

- more concrete
- easier to test
- less likely to remount the viewer accidentally
- less dependent on container registration order
- better aligned with the actual requirement: keep the same live PDF.js host
  while navigating between Home and Curation

If we later need a provider for diagnostics or explicit viewer metadata, we can
add one around the stable host. But the host location itself should be stable
first.

## Layout Topology Risk

The host refactor also crosses a layout boundary:

- Home currently owns a three-panel top-level `react-resizable-panels` layout
- Curation currently owns a two-panel `WorkspaceShell` layout

That means the migration must preserve:

- desktop resizing behavior
- saved panel sizes where practical
- mobile stacking behavior
- minimum-size constraints

The first implementation should favor correctness and one-host persistence over
perfect preservation of historical panel proportions, but the plan must still
test that the new nested panel structure remains usable and does not collapse
the viewer pane.

## Active Document Contract

The first implementation should keep `dispatchPDFDocumentChanged(...)` as the
public event contract so we do not change viewer semantics and route ownership
at the same time.

But the plan should explicitly enforce:

- only the active route surface should drive document changes
- route transitions must not replay stale document-change events after the new
  route has already selected a document
- Home restore flows and Curation hydration flows must both work against the
  same shared host without double-loading or cross-route stomping

This can remain event-based in the first pass. It just needs tests and
acceptance criteria instead of being implicit.

## Design Constraints

The refactor must preserve these invariants:

- exactly one mounted `PdfViewer` on Home/Curation routes
- no duplicate PDF.js iframe
- no new overlay-only highlight mode for curation
- chat remains the baseline for native PDF.js highlighting behavior
- existing global PDF document/evidence events continue to work
- mobile layout remains usable
- unrelated routes do not pay for a permanently mounted PDF viewer

## Non-Goals

These are out of scope for the first architecture pass:

- redesigning chat UX
- redesigning curation UX
- rewriting quote localization
- changing the evidence-navigation contract again
- making Agent Studio, Batch, or Weaviate share the same viewer shell
- production hotfixing before the new layout is validated on dev

## Slices

### Slice 0: Freeze The Baseline

Goal:

- capture the current "chat is correct" behavior before the layout refactor

Work:

- document the working chat-native highlight expectations
- add or tighten tests that treat native PDF.js chat behavior as the baseline
- explicitly mark overlay-only exact-match behavior as not acceptable for this
  project goal

Acceptance:

- the plan and tests clearly define "identical to chat" as the requirement

Review:

- run one `gpt-5.4 xhigh` architecture review on the plan before code changes

### Slice 1: Introduce The Persistent Layout Route

Status:

- completed on April 13, 2026
- `gpt-5.4 xhigh` review passed after adding route-owner scoping for both
  `pdf-viewer-document-changed` and `chat-document-changed`
- validated with:
  - `PdfViewer.evidence.test.tsx`
  - `PdfViewer.owner.test.tsx`
  - `Chat.test.tsx`
  - `pdfViewerDocumentChanged.test.tsx`
  - `PersistentPdfWorkspaceLayout.test.tsx`
  - `CurationWorkspacePage.test.tsx`
  - `WorkspaceShell.test.tsx`
  - `App.test.tsx`
  - `App.logout.test.tsx`
  - alternate frontend production build

Goal:

- add the stable route-level host without changing Home/Curation logic yet

Work:

- add `PersistentPdfWorkspaceLayout`
- move Home and Curation routes under a common layout route in `App.tsx`
- keep one real `PdfViewer` mounted there
- render placeholder outlet content in the right pane
- define a documented temporary ownership rule for active-document events
- establish the new outer panel topology used by both viewer-bearing routes

Acceptance:

- the layout route persists across Home/Curation child route changes
- only one `PdfViewer` is mounted
- other routes do not mount the viewer
- the host does not react to stale document events when the child route changes
- the outer viewer pane remains usable on desktop and mobile

Tests:

- new layout-route test proving only one viewer mount
- route switch test proving the layout stays mounted while child content changes
- route switch test proving stale document events do not overwrite the new
  active document
- layout test proving the shared viewer pane remains present in both desktop and
  compact/mobile modes

Review:

- run `gpt-5.4 xhigh` review on the slice

### Slice 2: Migrate Home Onto The Shared Host

Goal:

- make Home use the persistent viewer instead of owning its own viewer

Work:

- split Home into "viewer host" vs "right-side content"
- keep chat, session handling, right panel, and document events intact
- remove direct `PdfViewer` mount from `HomePage.tsx`

Acceptance:

- Home chat/document flows still work
- Home evidence clicks still use the same native PDF.js behavior
- there is still only one viewer instance
- Home document-restore flows do not fight with Curation hydration when
  switching routes
- Home chat/right-panel nested resizing remains usable after the outer viewer
  panel moves to the shared layout

Tests:

- Home route render test with shared host
- existing Home/chat evidence tests still pass unchanged

Review:

- run `gpt-5.4 xhigh` review on the slice

Status:

- completed on April 13, 2026
- `HomePage.tsx` no longer mounts `PdfViewer` directly
- Home document restore and chat-side document events now target the shared
  host through explicit owner tokens
- Home chat/right-panel content remains nested under the shared viewer layout

### Slice 3: Migrate Curation Onto The Same Host

Goal:

- make Curation Workspace use the same mounted viewer instance as Home

Work:

- remove direct `PdfViewer` mount from `CurationWorkspacePage.tsx`
- simplify `WorkspaceShell.tsx` so it no longer owns `pdfSlot`
- keep header/table/autosave/workspace flows intact
- keep curation hydration-driven document ownership explicit

Acceptance:

- curation evidence clicks use the same mounted viewer as Home
- no second viewer is mounted
- the same quote/document pair no longer diverges because of route-level viewer
  state
- curation hydration does not reload the wrong document after a Home-originated
  restore flow
- `WorkspaceShell` remains usable after the PDF pane is removed from its local
  ownership model

Tests:

- Curation workspace render test with shared host
- curation evidence interaction test proving the host viewer receives the event

Review:

- run `gpt-5.4 xhigh` review on the slice

Status:

- completed on April 13, 2026
- `CurationWorkspacePage.tsx` no longer mounts `PdfViewer` directly
- `WorkspaceShell.tsx` now owns curation content only
- curation hydration drives the shared viewer through the same host with an
  explicit session-scoped owner token

### Slice 4: Route Transition Parity

Goal:

- prove the same live viewer survives route transitions between Home and
  Curation

Critical scenarios:

- load paper on Home, then enter Curation for the same document
- click the same evidence quote from chat and curation
- return to Home and verify the viewer did not remount
- transition between routes while asynchronous document restore/hydration work
  is still settling

Acceptance:

- the same viewer host instance survives Home/Curation route transitions
- current page and selected match are not lost just because the route changed
- the same quote from both surfaces produces the same highlight path
- asynchronous Home/Curation document selection cannot stomp each other

Tests:

- route integration test with a mount counter for `PdfViewer`
- route integration test proving one iframe only
- parity test asserting identical viewer result for the same quote/document pair
- route integration test covering document-ownership races between chat restore
  and curation hydration

Review:

- run `gpt-5.4 xhigh` review on the completed route-parity slice

Status:

- automated coverage expanded on April 13, 2026
- `PersistentPdfWorkspaceLayout.test.tsx` now proves the shared host remains
  accessible and mounted in compact/mobile layout
- `PersistentPdfWorkspaceLayout.routeParity.test.tsx` now proves stale
  Home-owned document events and Home unload events cannot stomp the active
  curation document after a route transition
- `PersistentPdfWorkspaceLayout.routeParity.test.tsx` also now proves
  same-document curation hydration does not reload the already loaded PDF
  session after a route transition
- `PdfViewer.tsx` now preserves the live PDF.js session across Home/Curation
  owner changes and ignores redundant same-document hydration events unless a
  real replacement document arrives
- existing `PdfViewer.evidence.test.tsx` and
  `navigationSourceAdapters.test.ts` continue to cover identical chat/curation
  quote navigation behavior on the shared viewer path
- remaining work in this slice is manual dev validation of the long CRB quotes
  on the live shared host

### Slice 5: Cleanup

Goal:

- remove code and docs that only existed to keep split viewers aligned

Potential cleanup:

- obsolete viewer-ownership comments
- dead two-viewer assumptions
- temporary instrumentation used only for migration
- any route-specific rescue logic that became unnecessary after one-host parity

Acceptance:

- no remaining route-local viewer mounts
- no stale architecture comments describing two-viewer consistency work

## Validation Strategy

### 1. Focused Frontend Tests

Keep the existing viewer/navigation tests, but add architecture coverage for:

- persistent layout route
- mount-count invariants
- route transition persistence
- Home and Curation sharing one viewer host

### 2. Manual Dev Validation

Required manual checks:

- load a paper on Home and click chat evidence
- open the corresponding curation workspace for the same paper
- click the same evidence sentence from curation
- confirm the highlight behavior matches chat exactly
- repeat with the long CRB quotes that previously drifted
- move quickly between Home and Curation while the document is still restoring
  and confirm the correct paper remains loaded
- verify desktop resizing still feels correct on Home and Curation
- verify compact/mobile layout still keeps the shared viewer accessible

### 3. Reviewer Gate

After each significant slice:

- run a `gpt-5.4 xhigh` review
- treat findings as part of the slice completion criteria

## Acceptance Criteria

The plan is complete only when all of the following are true:

- Home and Curation no longer mount separate `PdfViewer` instances
- the viewer-bearing routes share one live PDF.js host
- curation does not rely on a distinct rendering path to "match" chat
- the same quote on the same document highlights the same way from both
  surfaces
- Home and Curation cannot overwrite each other's active document during route
  transitions
- the fix is validated on dev before any further production rollout

## Decision Summary

We are no longer trying to make two viewers "consistent enough."

We are moving to:

- one stable viewer host
- one live PDF.js instance
- one route-layout ownership model
- one native highlight behavior shared by chat and curation

That is the correct architectural fix for the drift problem.
