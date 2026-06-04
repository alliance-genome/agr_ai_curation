# Curation Review-Screen Redesign (Build B) — Design

**Date:** 2026-06-04
**Status:** Design / spec (approved layout; implementation pending)
**Scope:** The curation **review workspace** screen (`/curation/:sessionId`) only — where a curator
reviews extracted objects, views the PDF, sees evidence, and accepts/rejects. The inventory table
is out of scope (separate; overlaps ALL-557 / KANBAN-1342).
**Companion docs:** Builds on `docs/design/2026-06-04-curation-interface-diagnosis.md` (the diagnosis;
this is "Build B" from its §10). Build A (auto-push curation handoff) is independent.

## 1. Goal

Make the review screen genuinely approachable for biologist-curators verifying AI-extracted objects
against a paper, for the upcoming release. Today the screen is congested (three cramped columns, a
fourth "evidence" panel fighting for space, ~32 flattened fields per gene-expression object, two
*different* review surfaces chosen at runtime). The redesign adopts the proven
document-processing review pattern (source document + extracted form, evidence as a document
highlight) validated against Rossum, Azure Document Intelligence, and Sensible HITL guidance.

## 2. Approved layout — "document + form"

Two panes, not four:

- **Left — full-height PDF.** The source paper at full height, and it doubles as the **evidence
  surface**. Selecting a field highlights its supporting passage in the PDF, and there is **no separate
  evidence column**. *Reality check (gpt-5.5 review): the native pdf.js quote-highlight mechanism
  exists, but today it fires only when the curator clicks a field's evidence chip/control
  (`CandidateFieldEditor.tsx:447`, `pdfEvents.ts`, `PdfViewer.tsx`), **not** on plain field selection.
  Wiring "select field → highlight" is **new work in this build**, not a free reuse.*
- **Right — the work pane**, which contains, top to bottom:
  1. **Object selector strip** (sits over the form *only*, never over the PDF): `‹ ›` prev/next, the
     current object identity ("pef-1 · gene-expression annotation · 3 of 14"), a **"▾ all objects"
     popover to jump** directly to any object, and a compact **N-segment progress bar** colored by
     state (done / current / needs-review). The PDF stays full-height and unobstructed.
  2. **Object header:** object title, a "_N_ fields need review" chip, and **Accept / Reject** for
     the whole object. A session-level **"Accept all validated"** bulk action remains available.
  3. **Grouped form:** the domain-pack field groups (e.g. gene-expression's Subject gene · Expression
     site · Temporal stage · Assay & method · Evidence & reference · Relation & provider · Experiment
     context) as **collapsible sections**, with only the groups needing attention expanded by default.

Field rows carry **state coloring**: resolved/validated (green ✓), needs-review/unresolved (amber !),
AI-suggested-unconfirmed (grey). **Needs-review fields float to the top of their group and are counted
in the object header**, so the curator's eye goes to what matters instead of treating all ~32 fields
equally. On the selected field, a **small inline evidence quote chip** appears (with a "highlighted in
PDF →" affordance) so the curator can confirm at a glance without always looking left. *(Review note:
today field evidence shows a location label with the quote in a tooltip, and object-level evidence uses
quote cards in a separate panel — the inline quote chip is **new** here.)*

**Theme:** mockups rendered light for dense-reading legibility; final theme (light vs the current dark
"workbench") is a deferred polish decision, not a structural one.

## 3. Single review surface (collapse the two)

Today the app picks between a modern envelope-backed card list (`EnvelopeObjectReviewTable`) and a
legacy 7-column table (`EntityTagTable`) at runtime via `hasEnvelopeObjectRows`
(= `envelopeReviewRequests.length > 0`, i.e. any candidate carrying a `projection_ref`). Build B
standardizes on the **envelope-driven surface** described above and **retires the legacy table** (after
confirming no live session type depends on it). The surface is **driven by the domain pack's projection
metadata** (`workspace_display` groups/summary fields), so a new domain/envelope shape renders correctly
with no bespoke per-domain UI — keeping the screen project-agnostic.

**Must-do before retiring `EntityTagTable` (gpt-5.5 review):** the legacy table uniquely provides
**add-manual-entity, delete-row, inline legacy-row edit, an "Accept all validated" toolbar, and the
legacy evidence preview**; `EnvelopeObjectReviewTable` currently has select/search/filter/accept/reject
+ an evidence panel but **lacks delete, manual-add, inline-row-edit, and accept-all-validated**. These
affordances must be **rebuilt on the envelope surface** (or consciously dropped) before the legacy table
is removed — this is real scope, not a free deletion.

## 4. Field-density cleanup (depends on the field-coverage audit)

The review form must render the envelope cleanly, not dump it. From the field audit (required before
Build B; see diagnosis §6), and confirmed against real gene-expression data (32 fields / 7 groups per
candidate):

- **Hide redundant mirror fields** — `expression_annotation_subject.*` is duplicated as
  `expression_experiment.entity_assayed.*`; show once.
- **Hide `under_development` fields** from curators (detection reagents, specimen model/alleles).
- **Render nested arrays/objects as chips or compact sub-tables**, not raw JSON blobs
  (`condition_relations`, UBERON term lists, `cellular_component_qualifiers`, etc.).
- **De-duplicate** repeated references (`single_reference.reference_id` vs
  `expression_experiment.single_reference.reference_id`).

The audit produces, per domain pack, the keep/hide/collapse decision and read-only-vs-editable
designation that drives `workspace_display`. *Review note: this is genuine build work — today
array/object fields default to a JSON textarea (`FieldRow.tsx:201`), and the materializer does **not**
auto-hide `definition_state_category: under_development` fields, so the chip/sub-table rendering and the
hide rules must be implemented.*

## 5. Field editing & ontology-term lookup — roadmap (design-for, build incrementally)

This is the agreed "ultimate shape." We design with room for it now; we do not build all of it now.

- **Phase 1 (now):** the redesigned layout + single surface + density cleanup + field-state +
  evidence-in-PDF. Existing simple-field editing (autosave drafts) is retained. Ontology/term fields
  may stay **read-only or direct-CURIE-editable** for now. Leave a per-field **"⌕ Browse terms"
  affordance** on ontology fields (may be disabled / "coming soon") so the layout needs no rework
  later.
- **Phase 2:** **direct edit** of ontology fields by typing the CURIE (e.g. fix `WBbt:0005800` →
  `WBbt:0005805`), validated against the curation database on save.
- **Phase 3 — context-aware term browser ("coming soon"):** clicking "⌕ Browse terms" opens a
  **movable, resizable, position-remembered popup window**, **scoped to that field's ontology**
  (anatomy → WBbt, stage → stage ontology, GO, etc.). The curator searches/browses valid terms and
  clicks one to populate the field. It **floats over the PDF rather than sliding over it**, so the
  paper stays referenceable; **position and size persist per user**.
  - **Dependency:** requires **new backend tools that query the curation database** for valid
    ontology terms (search/browse per ontology). This is a real build dependency and can be
    messaged to curators as "coming soon."

## 6. Curator feedback affordance (thumbs + message)

The curator should be able to tell us quickly when the AI got an object right or wrong, and send a
free-text note — directly from the review screen:

- **Thumbs up / thumbs down** on each object (in the object header, beside Accept/Reject) — a
  lightweight "AI got this right / wrong" signal. This is **distinct from the Accept/Reject curation
  decision**: a curator can accept a corrected object yet still thumbs-down the AI's first pass.
- **A feedback-message icon** opening a short free-text message to the team — the same kind of
  "Provide Feedback" entry the chat offers.

Both **reuse the existing curator-feedback pipeline** — frontend `FeedbackDialog` → `submitFeedback`
→ `POST /api/feedback/submit` → persisted report → background trace capture / email / TraceReview.
*Precision (gpt-5.5 review): in chat this lives under the message `MoreVert` menu as "Provide
Feedback" (`MessageActions.tsx`, `FeedbackDialog.tsx`, `backend/src/api/feedback.py`); there is **no**
object-level thumbs API or literal "chat-bubble" control to drop in. So the review screen needs its own
thumbs + feedback-entry UI wired to that existing `/api/feedback/submit` pipeline — reusing the backend,
building the surface.*

**Phasing:** design-for now (reserve the spot in the object header so the layout doesn't shift later);
build the wiring as a fast follow — **not required for Phase 1**.

## 7. In scope now vs deferred

**Now (Phase 1):** two-pane document+form layout; object selector strip + jump popover + progress;
grouped collapsible form; field-state coloring + needs-review floating/counting; object Accept/Reject
+ bulk accept-all-validated; evidence-as-PDF-highlight + inline quote chip; collapse to a single
review surface (retire legacy table); field-density cleanup from the audit; "Browse terms"
affordance present (possibly disabled).

**Deferred:** Phase 2 direct ontology-CURIE editing; Phase 3 ontology term-browser popup + the
curation-DB term-lookup tools it needs; **curator feedback wiring** (thumbs up/down + feedback-message
icon — space reserved now per §6, wired as a fast follow reusing the existing chat feedback mechanism);
final theme decision; inventory-table rework (ALL-557).

## 8. Resolved sub-decisions

- **Object selector:** `‹ ›` + "_N_ of _M_" + N-segment state progress bar + "▾ all objects" jump
  popover. (Approved.)
- **Evidence:** PDF highlight on field-select **plus** a small inline quote chip on the selected
  field. (Approved — keep both.)
- **Nested/array fields:** chips / compact expanders (e.g. "1 condition"); a small sub-table for
  condition relations is acceptable. (Approved.)

## 9. Dependencies & cross-references

- **Field-coverage audit** (diagnosis §6) is a prerequisite input to §4.
- **Build A** (auto-push handoff) is independent and can proceed in parallel.
- **ALL-557 / KANBAN-1342** (inventory scoping) is the inventory surface — out of scope here.
- **Existing strengths to preserve:** native pdf.js evidence highlighting
  (`components/pdfViewer/PdfViewer.tsx`), per-field evidence anchors, autosave drafts.

## 10. Acceptance criteria

- [ ] Review screen renders as two panes: full-height PDF + work pane; no separate evidence column.
- [ ] Object selector strip (prev/next, N-of-M, progress, jump-to popover) sits over the form only;
      PDF remains full-height.
- [ ] One review surface for all session types (legacy 7-column table retired); rendering is driven
      by domain-pack projection metadata.
- [ ] Fields grouped/collapsible; needs-review fields floated and counted; field-state coloring.
- [ ] Selecting a field highlights its evidence in the PDF and shows the inline quote chip.
- [ ] Density cleanup applied per the field audit (mirrors/under-development hidden; nested data not
      raw JSON).
- [ ] Object Accept/Reject + bulk accept-all-validated work.
- [ ] A per-field "Browse terms" affordance exists on ontology fields (may be disabled/"coming soon").
- [ ] Object header reserves space for a thumbs up/down + feedback-message icon (reusing the existing
      chat curator-feedback mechanism); wiring may be a fast-follow rather than Phase 1.
- [ ] Existing behavior outside the review screen is unchanged.

## 11. Validation

- [ ] Frontend component/interaction tests for: object navigation, group expand/collapse,
      field-select → PDF highlight, accept/reject, single-surface rendering for an envelope session.
- [ ] Visual/manual smoke on a real gene-expression session (e.g. sandbox session
      `a1419a0e-…`, 14 candidates) at a laptop width.
- [ ] Confirm the legacy table's removal doesn't break any live session type.

## 12. Open questions

- Final theme (light vs dark) — defer to a polish pass.
- Exact "Browse terms" affordance state in Phase 1 (visible-disabled vs hidden-until-Phase-3).
- Whether direct-CURIE editing (Phase 2) lands with Phase 1 or after.

## Appendix — mockups

Interactive wireframes produced during brainstorming (document+form layout; editing + term-browser
"ultimate shape") are preserved in the brainstorming session directory and can be committed into the
repo as design reference on request.
