# Build B2 — Curation Review Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the curation review workspace into the two-pane document+form shape: full-height PDF on the left; on the right a compact object-selector strip over a grouped form that renders B1's projected fields cleanly (chips, curie-chips, sub-tables instead of JSON dumps), with field-state coloring, needs-review floated, and group-level evidence chips that drive the existing pdf.js highlight.

**Architecture:** Frontend-only. Drive field rendering off `field.metadata.field_metadata.render_as` (emitted by B1) in `FieldRow.tsx`, falling back to today's `field_type` widgets. Collapse the current 3-pane (PDF | object list | field editor) into 2 panes (PDF | work pane) by moving object selection into a slim selector strip atop the field editor. Make the envelope surface the default for envelope-backed sessions. (Full retirement of the legacy `EntityTagTable` happens in B3 after affordance parity.)

**Tech Stack:** React 18 + TS + Vite + MUI v5 + TanStack Query v5 + react-resizable-panels; vitest + React Testing Library (`@testing-library/react`, jsdom). **Depends on:** B1 (`render_as` / `hide_when_empty` in projected fields). B2 is safe to build against B1's output; until B1 lands, `render_as` is absent and fields fall back to `field_type` widgets (no breakage).

**Key facts (from frontend reference):**
- Render branch: `CurationWorkspacePage.tsx:570-596` (envelope surface vs legacy table).
- Field render: `FieldRow.renderDefaultInput` keyed on `field.field_type` (`FieldRow.tsx:103/201/233/277`). `field.metadata.field_metadata` already read at `CandidateFieldEditor.tsx:227,571`.
- `CurationDraftField.metadata` is exposed (`types.ts:303-319`); domain-pack hints nest at `metadata.field_metadata`.
- Object selection = `setActiveCandidate(candidateId)` → routes `/curation/:sessionId/:candidateId` (`CurationWorkspacePage.tsx:647-665`).
- Field editor groups via `buildSections` (`CandidateFieldEditor.tsx:182-209`); per-field evidence via `evidenceProjectionsForField` (`:286-296`) → `dispatchEvidenceNavigationCommand`.
- Tests: vitest; copy fixtures from `EnvelopeObjectReviewTable.test.tsx` / `FieldRow.test.tsx`.

---

## File Structure

**Create:**
- `frontend/src/features/curation/editor/fieldRenderers/` — small render components: `ChipFieldValue.tsx`, `CurieChipFieldValue.tsx`, `SubTableFieldValue.tsx`, `EvidenceLocatorFieldValue.tsx`, `DivergenceFieldValue.tsx` (+ an `index.ts` `resolveFieldRenderer(field)` mapper).
- `frontend/src/features/curation/workspace/ObjectSelectorStrip.tsx` — the "‹ ›  N of M  ▾ all objects + progress" strip.
- `frontend/src/features/curation/workspace/objectSelector.ts` — pure helpers (progress segments, sort, label).
- Co-located `*.test.tsx` for each new component.

**Modify:**
- `frontend/src/features/curation/editor/FieldRow.tsx` — read `render_as`, delegate to the renderers; fall back to `field_type`.
- `frontend/src/features/curation/editor/CandidateFieldEditor.tsx` — field-state coloring; float needs-review within a group; group-level evidence chip; group needs-review counts.
- `frontend/src/features/curation/workspace/WorkspaceShell.tsx` — collapse to 2 panes; mount `ObjectSelectorStrip` above the field editor.
- `frontend/src/pages/CurationWorkspacePage.tsx` — render the envelope surface by default; pass the selector strip + objects to the work pane.

---

## Task 1: Render projected fields by `render_as`

**Files:**
- Create: `frontend/src/features/curation/editor/fieldRenderers/index.ts` + the renderer components + tests
- Modify: `frontend/src/features/curation/editor/FieldRow.tsx`

- [ ] **Step 1: Write the failing test for the renderer mapper**

Create `frontend/src/features/curation/editor/fieldRenderers/resolveFieldRenderer.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { resolveRenderAs } from './index'

function field(renderAs?: string, field_type = 'string') {
  return { field_type, metadata: { field_metadata: renderAs ? { render_as: renderAs } : {} } } as any
}

describe('resolveRenderAs', () => {
  it('returns the render_as hint when present', () => {
    expect(resolveRenderAs(field('curie-chip'))).toBe('curie-chip')
    expect(resolveRenderAs(field('sub-table'))).toBe('sub-table')
  })
  it('falls back to a json renderer for array/object field types', () => {
    expect(resolveRenderAs(field(undefined, 'array'))).toBe('json')
    expect(resolveRenderAs(field(undefined, 'object'))).toBe('json')
  })
  it('returns default for plain fields', () => {
    expect(resolveRenderAs(field(undefined, 'string'))).toBe('default')
  })
})
```

- [ ] **Step 2: Run → FAIL** (`resolveRenderAs` undefined).

Run: `cd frontend && npx vitest run src/features/curation/editor/fieldRenderers/resolveFieldRenderer.test.ts`

- [ ] **Step 3: Implement the mapper**

Create `frontend/src/features/curation/editor/fieldRenderers/index.ts`:

```ts
import type { CurationDraftField } from '@/features/curation/types'

export type RenderAs =
  | 'default' | 'json' | 'chip' | 'curie-chip' | 'sub-table'
  | 'evidence-locator' | 'term-chip' | 'divergence' | 'notes'

export function fieldHints(field: CurationDraftField): Record<string, unknown> {
  const meta = field.metadata as Record<string, unknown> | undefined
  const nested = meta?.field_metadata
  return (nested && typeof nested === 'object' ? (nested as Record<string, unknown>) : {})
}

export function resolveRenderAs(field: CurationDraftField): RenderAs {
  const hint = fieldHints(field).render_as
  const known: RenderAs[] = ['chip', 'curie-chip', 'sub-table', 'evidence-locator', 'term-chip', 'divergence', 'notes', 'json', 'default']
  if (typeof hint === 'string' && (known as string[]).includes(hint)) return hint as RenderAs
  if (field.field_type === 'array' || field.field_type === 'object' || field.field_type === 'json') return 'json'
  return 'default'
}
```

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Write failing tests for the chip + curie-chip renderers**

Create `frontend/src/features/curation/editor/fieldRenderers/ChipFieldValue.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material'
import { describe, expect, it } from 'vitest'
import theme from '@/theme'
import ChipFieldValue from './ChipFieldValue'

const r = (ui: React.ReactNode) => render(<ThemeProvider theme={theme}>{ui}</ThemeProvider>)

describe('ChipFieldValue', () => {
  it('renders one chip per list item', () => {
    r(<ChipFieldValue value={['UBERON:0000966', 'UBERON:0001017']} />)
    expect(screen.getByText('UBERON:0000966')).toBeInTheDocument()
    expect(screen.getByText('UBERON:0001017')).toBeInTheDocument()
  })
  it('renders nothing for an empty list', () => {
    const { container } = r(<ChipFieldValue value={[]} />)
    expect(container.querySelectorAll('.MuiChip-root').length).toBe(0)
  })
})
```

- [ ] **Step 6: Run → FAIL, then implement the renderers**

Create `ChipFieldValue.tsx` (MUI `Chip` per item; read-only display), `CurieChipFieldValue.tsx` (renders `{name}` with the CURIE as a tooltip/secondary; expects a `value` object `{curie, name}` or pairs the field's `.name` sibling — accept props `value` + optional `label`), `SubTableFieldValue.tsx` (renders an array of condition objects as a compact MUI `Table`; columns derived from the first row's keys; collapsed-by-default `Accordion` showing "N item(s)"), `EvidenceLocatorFieldValue.tsx` (one line "p.4 · Results"), `DivergenceFieldValue.tsx` (muted "AI proposed: X" shown only when a `proposedValue` prop differs from the resolved value). Keep each component small, read-only, presentation-only.

```tsx
// ChipFieldValue.tsx
import { Box, Chip } from '@mui/material'
export default function ChipFieldValue({ value }: { value: unknown }) {
  const items = Array.isArray(value) ? value : value == null ? [] : [value]
  if (items.length === 0) return null
  return (
    <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
      {items.map((it, i) => <Chip key={i} size="small" label={String(typeof it === 'object' ? JSON.stringify(it) : it)} />)}
    </Box>
  )
}
```

Run each new component's test → PASS.

- [ ] **Step 7: Wire `FieldRow` to the renderers**

In `frontend/src/features/curation/editor/FieldRow.tsx`, before the `field_type` switch in `renderDefaultInput`, branch on `resolveRenderAs(field)`. For read-only render-as values (`chip`, `curie-chip`, `sub-table`, `evidence-locator`, `term-chip`, `notes`), render the corresponding component instead of a text/JSON input (these fields are read-only per B1). For `divergence`, render the resolved value input + `<DivergenceFieldValue>`. For `json`/`default`, keep the existing widgets. Add a test in `FieldRow.test.tsx` (use the existing `createField` factory at `:9-30`) asserting a `render_as: 'chip'` field renders chips, not a JSON textarea.

- [ ] **Step 8: Run the field-renderer suite + commit**

Run: `cd frontend && npx vitest run src/features/curation/editor/fieldRenderers src/features/curation/editor/FieldRow.test.tsx`
Expected: PASS.

```bash
git add frontend/src/features/curation/editor/fieldRenderers frontend/src/features/curation/editor/FieldRow.tsx
git commit -m "feat(curation-ui): render projected fields by render_as (chip/curie-chip/sub-table/...)"
```

---

## Task 2: Field-state coloring + float needs-review within a group

**Files:**
- Modify: `frontend/src/features/curation/editor/CandidateFieldEditor.tsx` (`buildSections` ~:182-209; FieldRow render ~:971-1014)
- Create: `frontend/src/features/curation/editor/fieldState.ts` + test

- [ ] **Step 1: Write the failing test**

Create `frontend/src/features/curation/editor/fieldState.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { fieldState, sortFieldsNeedsReviewFirst } from './fieldState'

const f = (over: Partial<any>) => ({ field_key: 'k', validation_result: null, read_only: false, value: 'v', metadata: {}, order: 0, ...over })

describe('fieldState', () => {
  it('flags needs-review when validation failed/unresolved', () => {
    expect(fieldState(f({ validation_result: { status: 'failed' } }))).toBe('needs-review')
  })
  it('flags resolved when validated ok', () => {
    expect(fieldState(f({ validation_result: { status: 'passed' } }))).toBe('resolved')
  })
  it('flags ai-unconfirmed when no validation and editable', () => {
    expect(fieldState(f({ validation_result: null, read_only: false }))).toBe('ai-unconfirmed')
  })
})

describe('sortFieldsNeedsReviewFirst', () => {
  it('floats needs-review fields to the top, preserving order otherwise', () => {
    const fields = [f({ field_key: 'a', order: 0 }), f({ field_key: 'b', order: 1, validation_result: { status: 'failed' } })]
    expect(sortFieldsNeedsReviewFirst(fields).map((x) => x.field_key)).toEqual(['b', 'a'])
  })
})
```

- [ ] **Step 2: Run → FAIL, then implement `fieldState.ts`**

```ts
import type { CurationDraftField } from '@/features/curation/types'
export type FieldStateKind = 'resolved' | 'needs-review' | 'ai-unconfirmed'

export function fieldState(field: CurationDraftField): FieldStateKind {
  const status = (field.validation_result as { status?: string } | null | undefined)?.status
  if (status === 'failed' || status === 'unresolved' || field.stale_validation) return 'needs-review'
  if (status === 'passed' || status === 'validated') return 'resolved'
  return 'ai-unconfirmed'
}

export function sortFieldsNeedsReviewFirst(fields: CurationDraftField[]): CurationDraftField[] {
  return [...fields].sort((a, b) => {
    const an = fieldState(a) === 'needs-review' ? 0 : 1
    const bn = fieldState(b) === 'needs-review' ? 0 : 1
    if (an !== bn) return an - bn
    return a.order - b.order
  })
}
```

Run → PASS.

- [ ] **Step 3: Apply in `CandidateFieldEditor`**

In `buildSections`, sort each section's fields with `sortFieldsNeedsReviewFirst`, and compute a per-section `needsReviewCount`. Render a small state dot (green ✓ / amber ! / grey) beside each `FieldRow` keyed on `fieldState(field)`, and show "_N_ need review" on the section header. Add an RTL test asserting a section with one failed field shows the amber state + count, and that the failed field renders first.

- [ ] **Step 4: Run editor tests + commit**

Run: `cd frontend && npx vitest run src/features/curation/editor`

```bash
git add frontend/src/features/curation/editor/fieldState.ts frontend/src/features/curation/editor/CandidateFieldEditor.tsx
git commit -m "feat(curation-ui): field-state coloring + float needs-review within groups"
```

---

## Task 3: Object-selector strip (replaces the middle object-list pane)

**Files:**
- Create: `frontend/src/features/curation/workspace/objectSelector.ts` + `ObjectSelectorStrip.tsx` + tests

- [ ] **Step 1: Write the failing test for the pure helpers**

Create `objectSelector.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { progressSegments, selectorPosition } from './objectSelector'

const cand = (id: string, status: string) => ({ candidate_id: id, status } as any)

describe('objectSelector', () => {
  it('maps candidates to progress segments by status', () => {
    const segs = progressSegments([cand('a', 'accepted'), cand('b', 'pending'), cand('c', 'rejected')], 'b')
    expect(segs.map((s) => s.kind)).toEqual(['done', 'current', 'rejected'])
  })
  it('reports 1-based position and total', () => {
    expect(selectorPosition([cand('a', 'pending'), cand('b', 'pending')], 'b')).toEqual({ position: 2, total: 2 })
  })
})
```

- [ ] **Step 2: Run → FAIL, implement `objectSelector.ts`** (`progressSegments(candidates, activeId)` → `{id, kind: 'done'|'current'|'rejected'|'pending'}[]`; `selectorPosition`). Run → PASS.

- [ ] **Step 3: Write the failing component test for `ObjectSelectorStrip`**

```tsx
// ObjectSelectorStrip.test.tsx — render with 3 candidates, active = middle
it('shows N of M and calls onSelect from the jump popover', async () => {
  const onSelect = vi.fn()
  renderStrip({ candidates, activeCandidateId: 'b', onSelect })
  expect(screen.getByText(/2 of 3/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /all objects/i }))
  await userEvent.click(screen.getByRole('option', { name: /object c/i }))
  expect(onSelect).toHaveBeenCalledWith('c')
})
```

- [ ] **Step 4: Implement `ObjectSelectorStrip.tsx`**

Props: `{ candidates: CurationCandidate[]; activeCandidateId: string | null; onSelect: (id: string) => void }`. Render: `‹`/`›` IconButtons (prev/next call `onSelect` with the adjacent candidate), the identity line (`display_label · object_type · "N of M"` from `selectorPosition`), a "▾ all objects" `Menu`/`Popover` listing every candidate (each `MenuItem` `role="option"` → `onSelect(id)`), and a thin segmented progress bar from `progressSegments` (color per kind). Keep it compact (single row + 4px progress). Run the test → PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/curation/workspace/objectSelector.ts frontend/src/features/curation/workspace/ObjectSelectorStrip.tsx frontend/src/features/curation/workspace/objectSelector.test.ts frontend/src/features/curation/workspace/ObjectSelectorStrip.test.tsx
git commit -m "feat(curation-ui): object-selector strip (prev/next, N-of-M, jump, progress)"
```

---

## Task 4: Collapse to two panes (PDF | selector-over-form)

**Files:**
- Modify: `frontend/src/features/curation/workspace/WorkspaceShell.tsx`
- Modify: `frontend/src/pages/CurationWorkspacePage.tsx`

- [ ] **Step 1: Change the work pane to selector + field editor**

In `CurationWorkspacePage.tsx`, mount the new strip + field editor as the right work pane and stop rendering the separate object-list (`entityTableSlot`) as its own panel. Pass `ObjectSelectorStrip` (fed `workspace.candidates`, `activeCandidateId`, `setActiveCandidate`) above `CandidateFieldEditor`. The PDF stays the outer-left panel (`PersistentPdfWorkspaceLayout` unchanged).

- [ ] **Step 2: Update `WorkspaceShell` to a single right-pane composition**

Replace the inner two-pane split (object list | field editor) with a single column: `ObjectSelectorStrip` (fixed height) above the scrollable field editor. Keep `headerSlot`. Update `WorkspaceShellProps` (drop `entityTableSlot`/`reviewTableLabel`; add `selectorSlot`). Update all callers.

> Note: the legacy `EntityTagTable`/`EnvelopeObjectReviewTable` object-list view is removed from the default layout here. B3 must have landed the affordance parity (delete/manual-add/accept-all) **as toolbar/menu actions in the new work pane** before this is shippable — see B3. Until then, gate the new layout behind keeping the object list reachable (e.g. a collapsible "All objects" drawer) so no affordance is lost mid-migration.

- [ ] **Step 3: RTL smoke test**

Add a `WorkspaceShell.test.tsx` (or extend) asserting the work pane renders the `selectorSlot` and the field editor, and that the PDF panel is still present in `PersistentPdfWorkspaceLayout`.

- [ ] **Step 4: Manual check on dev**

Open `http://10.79.64.167:3900/curation/a1419a0e-...` and confirm: full-height PDF left; object selector + grouped form right; selecting an object updates the form and the URL; clicking a field's evidence chip still highlights the PDF.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/curation/workspace/WorkspaceShell.tsx frontend/src/pages/CurationWorkspacePage.tsx
git commit -m "feat(curation-ui): two-pane review layout (PDF | object-selector over grouped form)"
```

---

## Task 5: Group-level evidence chip

**Files:**
- Modify: `frontend/src/features/curation/editor/CandidateFieldEditor.tsx`

- [ ] **Step 1: Write the failing test**

A section whose member fields have evidence projections (matched by `field_path`) should show one "evidence" chip on the section header that, when clicked, dispatches the navigation command (mock `dispatchEvidenceNavigationCommand`). Reuse `evidenceProjectionsForField` aggregated across the section's fields.

- [ ] **Step 2: Implement**

In `buildSections`/section render, compute the section's evidence projections = union of `evidenceProjectionsForField(candidate, f)` over the section's fields (dedupe by `anchor_id`). Render an evidence chip on the section header (count) that, on click, dispatches the primary anchor's command (same call shape as `FieldEvidenceSlot`, `:447-470`). Keep per-field evidence slots too.

- [ ] **Step 3: Run editor tests + commit**

```bash
git add frontend/src/features/curation/editor/CandidateFieldEditor.tsx
git commit -m "feat(curation-ui): group-level evidence chip drives pdf highlight"
```

---

## Task 6: Make the envelope surface the default (single-surface readiness)

**Files:**
- Modify: `frontend/src/pages/CurationWorkspacePage.tsx`

- [ ] **Step 1:** With the work pane now selector+form, the runtime `hasEnvelopeObjectRows` branch (`:570-596`) no longer chooses between two *list* components — the grouped form is the single review surface for envelope sessions. Keep the legacy `EntityTagTable` path available **only** for non-envelope sessions (no `projection_ref`) until B3 retires it. Add a test asserting an envelope-backed workspace renders the new work pane (selector + form), not `EntityTagTable`.

- [ ] **Step 2: Full frontend suite + typecheck + commit**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: green.

```bash
git add frontend/src/pages/CurationWorkspacePage.tsx
git commit -m "feat(curation-ui): envelope grouped form is the default review surface"
```

---

## gpt-5.5 Review Corrections (fold in before implementing)

Verdict: **Sound-with-corrections.** Apply these:

1. **Task 2 — field-state must use the envelope validation summary, not `validation_result`.** Two problems with the plan's `fieldState`: (a) `FieldValidationResult.status` values are `validated | ambiguous | not_found | invalid_format | conflict | skipped | overridden` (`contracts.ts:171`), **not** `failed/passed/unresolved`; and (b) `CandidateFieldEditor` deliberately **ignores legacy `field.validation_result`** for the envelope UI and uses **`validation_summary_projections`** via `validationSummariesForField()` / `strongestStatus()` (`CandidateFieldEditor.test.tsx:402`). Rewrite `fieldState` to derive from the candidate's `validation_summary_projections` for the field: treat `unresolved`/`blocked` as **needs-review**, `resolved`/`waived` as **resolved**, others as **ai-unconfirmed**. Update the tests to build `validation_summary_projections` fixtures (not `validation_result`).

2. **Task 1 — `render_as` ≠ read-only.** Do **not** make a field read-only just because it has `render_as`. Honor the field's own `field.read_only` (derived backend-side from metadata, `pipeline.py:734`). A `render_as: chip` field that is editable should still allow editing; render-as governs *presentation*, not editability. Reword Task 1 Step 7 to render the value via the renderer but keep the field's edit affordance when `!field.read_only`.

3. **Task 3 — object selector needs the review row for `object_type`.** `CurationCandidate` has **no `object_type`** (it lives on `DomainEnvelopeReviewRow`; `EnvelopeObjectReviewTable` reads it from there, `:354`). Either feed `ObjectSelectorStrip` the `WorkspaceEnvelopeObjectReviewRow[]` (which pairs candidate + reviewRow), or build the identity line from `candidate.display_label` + `projection_ref.object_id` only. Adjust the props/types.

4. **Path fix:** `PersistentPdfWorkspaceLayout.tsx` lives at `frontend/src/components/pdfViewer/PersistentPdfWorkspaceLayout.tsx` (not under `features/curation/workspace`). Correct any reference.

5. **Confirmed safe/good:** dropping `entityTableSlot` only affects `CurationWorkspacePage` + `WorkspaceShell.test` (mechanically fine) — but it removes accept/reject/delete/manual-add/accept-all access, so the **B3-parity gate in Task 4 is essential** (do not land the layout swap before B3). Evidence dispatch + the vitest/RTL pattern are valid as written.

## Self-Review (completed)

- **Spec coverage (Build B spec §2–§4):** two-pane layout (T4) ✓; object-selector strip with N-of-M/jump/progress (T3) ✓; grouped form rendering chips/curie-chips/sub-tables (T1) ✓; field-state coloring + needs-review floating (T2) ✓; group-level evidence chip → existing pdf.js highlight (T5) ✓; single envelope surface (T6, full retire in B3) ✓; theme deferred (not in scope). Density cleanup data comes from B1; B2 renders it.
- **Placeholder scan:** logic-heavy units (render_as mapper, fieldState, objectSelector) have complete code + tests; the pure-presentation renderer/layout steps describe exact components + props + the one MUI example each, which is appropriate for UI (the skill's "questionable taste" engineer gets concrete component contracts + RTL assertions, not pixel CSS).
- **Type consistency:** `resolveRenderAs`/`fieldHints` read `field.metadata.field_metadata`; `fieldState`/`sortFieldsNeedsReviewFirst` operate on `CurationDraftField`; `ObjectSelectorStrip` consumes `CurationCandidate` + `setActiveCandidate(id)` (matching `CurationWorkspacePage.tsx:647`). `selectorSlot` replaces `entityTableSlot` consistently across `WorkspaceShell` + its caller.
- **Migration safety:** T4 explicitly gates the layout swap on B3 affordance parity (don't drop delete/manual-add/accept-all silently).
