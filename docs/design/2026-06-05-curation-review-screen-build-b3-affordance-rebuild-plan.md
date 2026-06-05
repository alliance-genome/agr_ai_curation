# Build B3 — Rebuild Legacy Affordances on the Envelope Surface

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the four affordances unique to the legacy `EntityTagTable` — **delete candidate, add manual object, accept-all-validated, inline edit** — onto the new envelope work pane (B2), then retire `EntityTagTable` so there is a single review surface.

**Architecture:** Frontend-only and small. The page already defines working handlers (`handleDeleteTag`, `handleCreateManualTag`, `handleAcceptAllValidated`, `handleSaveTag`) and the backend routes already exist; today they're only wired to `EntityTagTable`. B3 surfaces them as actions in the new work pane (a work-pane toolbar + an object-row menu), satisfies inline-edit via the existing field editor, then deletes the legacy table and its runtime branch.

**Tech Stack:** React 18 + TS + MUI v5 + TanStack Query; vitest + RTL. **Depends on:** B2 (the new work pane / object selector). **Independent of:** B1 (works regardless of `render_as`).

**Key facts (from frontend reference):**
- Existing page handlers (all flush autosave + refresh): `handleDeleteTag` (`CurationWorkspacePage.tsx:349-375`), `handleAcceptAllValidated` (`:377-422`), `handleSaveTag` (`:424-469`), `handleCreateManualTag` (`:471-512`).
- Backend routes all present (`backend/src/api/curation_workspace.py`): manual create `:564`, delete `:661`, decision `:681`, draft patch `:584`, envelope field patch `:605`, validate `:701`. **B3 is UI-only.**
- Legacy affordance source: `entityTable/EntityTagToolbar.tsx` (accept-all + add), `EntityTagTable.tsx` (delete confirm dialog `:203-234`, accept-all filter `:109-120`, manual-row save `:122-140`), `EntityTagRow.tsx` (Accept/Reject/Edit/Delete), `InlineEditRow.tsx`.
- Inline edit on the envelope surface = the existing **`CandidateFieldEditor`** (3rd-panel form) — editing is already covered there; B3 does **not** need a separate inline-edit-on-card.
- `handleCreateManualTag`/`handleSaveTag` bridge `EntityTag` ↔ candidate via `buildManualCandidateDraft`/`buildEntityTagFieldChanges` (`CurationWorkspacePage.tsx:18-21`).

---

## File Structure

**Create:**
- `frontend/src/features/curation/workspace/WorkPaneToolbar.tsx` — "Accept all validated" + "Add object" buttons (envelope-surface analogue of `EntityTagToolbar`). + test.
- `frontend/src/features/curation/workspace/DeleteObjectDialog.tsx` — confirm dialog (analogue of the legacy `:203-234`). + test.
- `frontend/src/features/curation/workspace/AddManualObjectDialog.tsx` — minimal manual-object form. + test.

**Modify:**
- `frontend/src/features/curation/workspace/WorkspaceShell.tsx` / `frontend/src/pages/CurationWorkspacePage.tsx` — mount the toolbar; thread delete/add/accept-all into the work pane; per-object delete from the object selector menu.
- Delete: `frontend/src/features/curation/entityTable/*` and the legacy branch in `CurationWorkspacePage.tsx` — in Task 5 only.

---

## Task 1: Work-pane toolbar (accept-all-validated + add object)

**Files:**
- Create: `frontend/src/features/curation/workspace/WorkPaneToolbar.tsx` + `WorkPaneToolbar.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// WorkPaneToolbar.test.tsx
it('enables Accept all validated only when there are validated pending candidates', () => {
  const onAcceptAll = vi.fn()
  renderToolbar({ validatedPendingCount: 2, onAcceptAllValidated: onAcceptAll, onAddObject: vi.fn() })
  expect(screen.getByRole('button', { name: /accept all validated/i })).toBeEnabled()
})
it('disables Accept all validated when none are validated-pending', () => {
  renderToolbar({ validatedPendingCount: 0, onAcceptAllValidated: vi.fn(), onAddObject: vi.fn() })
  expect(screen.getByRole('button', { name: /accept all validated/i })).toBeDisabled()
})
it('calls onAddObject from Add object', async () => {
  const onAdd = vi.fn()
  renderToolbar({ validatedPendingCount: 0, onAcceptAllValidated: vi.fn(), onAddObject: onAdd })
  await userEvent.click(screen.getByRole('button', { name: /add object/i }))
  expect(onAdd).toHaveBeenCalled()
})
```

- [ ] **Step 2: Run → FAIL, implement `WorkPaneToolbar.tsx`**

Props `{ validatedPendingCount: number; onAcceptAllValidated: () => void; onAddObject: () => void }`. Two MUI buttons modeled on `EntityTagToolbar.tsx:46-60`: "Accept all validated" (`disabled={validatedPendingCount === 0}`), "Add object". Run → PASS.

- [ ] **Step 3: Compute `validatedPendingCount` for the envelope surface**

In `CurationWorkspacePage.tsx`, derive the count = candidates with `status === 'pending'` whose validation summary indicates validated/no-open-blockers (reuse the validation summary already on `CurationCandidate.validation`). Add a small helper `countValidatedPending(candidates)` + a unit test. Note: the legacy version keyed on `db_status === 'validated'`; the envelope equivalent keys on the candidate's validation summary (no open blocking findings).

- [ ] **Step 4: Mount the toolbar above the object selector / form**

Place `<WorkPaneToolbar>` in the work pane header, wired to `handleAcceptAllValidated` (adapt its signature: it currently takes `tagIds` — pass the validated-pending candidate ids) and a new `onAddObject` that opens the add dialog (Task 3). Run frontend suite.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/curation/workspace/WorkPaneToolbar.tsx frontend/src/features/curation/workspace/WorkPaneToolbar.test.tsx frontend/src/pages/CurationWorkspacePage.tsx
git commit -m "feat(curation-ui): work-pane toolbar (accept-all-validated + add object)"
```

---

## Task 2: Per-object delete (confirm dialog)

**Files:**
- Create: `frontend/src/features/curation/workspace/DeleteObjectDialog.tsx` + test
- Modify: `ObjectSelectorStrip.tsx` (add a per-object delete affordance in the "all objects" menu) + `CurationWorkspacePage.tsx`

- [ ] **Step 1: Write the failing test for the dialog**

```tsx
it('calls onConfirm with the candidate id', async () => {
  const onConfirm = vi.fn()
  renderDialog({ open: true, candidateLabel: 'pef-1', onConfirm, onCancel: vi.fn() })
  await userEvent.click(screen.getByRole('button', { name: /delete/i }))
  expect(onConfirm).toHaveBeenCalled()
})
```

- [ ] **Step 2: Run → FAIL, implement `DeleteObjectDialog.tsx`** (MUI `Dialog` modeled on `EntityTagTable.tsx:203-234`: title "Delete object?", body names the object, Cancel + Delete). Run → PASS.

- [ ] **Step 3: Wire delete into the object selector menu**

Add a delete `IconButton` to each row of the `ObjectSelectorStrip` "all objects" menu (and/or a delete action in the field-editor footer). On click → open `DeleteObjectDialog`; on confirm → call the existing `handleDeleteTag(candidateId)` (already calls `deleteCurationCandidate` + refresh). Add a test asserting the menu's delete → dialog → `handleDeleteTag`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/curation/workspace/DeleteObjectDialog.tsx frontend/src/features/curation/workspace/DeleteObjectDialog.test.tsx frontend/src/features/curation/workspace/ObjectSelectorStrip.tsx frontend/src/pages/CurationWorkspacePage.tsx
git commit -m "feat(curation-ui): delete object on the envelope surface (confirm dialog)"
```

---

## Task 3: Add manual object

**Files:**
- Create: `frontend/src/features/curation/workspace/AddManualObjectDialog.tsx` + test
- Modify: `CurationWorkspacePage.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
it('submits a manual object and calls onCreate', async () => {
  const onCreate = vi.fn()
  renderAdd({ open: true, onCreate, onCancel: vi.fn() })
  await userEvent.type(screen.getByLabelText(/name/i), 'manual gene')
  await userEvent.click(screen.getByRole('button', { name: /add/i }))
  expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({ entity_name: 'manual gene' }))
})
```

- [ ] **Step 2: Run → FAIL, implement `AddManualObjectDialog.tsx`**

A minimal form (reuse the legacy `InlineEditRow` fields: name, type, species, topic). On submit → `onCreate({ entity_name, entity_type, species, topic })`. (The page's `handleCreateManualTag` already maps this to `createManualCurationCandidate` via `buildManualCandidateDraft` and selects the new candidate.) Run → PASS.

- [ ] **Step 3: Wire to the toolbar's "Add object"**

`onAddObject` opens this dialog; `onCreate` → `handleCreateManualTag`. Add a test asserting the toolbar "Add object" → dialog → `handleCreateManualTag`. Run frontend suite.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/curation/workspace/AddManualObjectDialog.tsx frontend/src/features/curation/workspace/AddManualObjectDialog.test.tsx frontend/src/pages/CurationWorkspacePage.tsx
git commit -m "feat(curation-ui): add manual object on the envelope surface"
```

---

## Task 4: Confirm inline-edit parity (no new code)

- [ ] **Step 1:** Verify the new work pane covers inline edit. The legacy `InlineEditRow` edited `entity_name/type/species/topic`; on the envelope surface those map to draft fields edited in `CandidateFieldEditor` (Save draft / autosave already wired via `handleSaveTag`/autosave). Add a note + a test asserting that editing a draft field and Save draft persists (mock `patchCurationEnvelopeField`/`autosaveCurationCandidateDraft`). No new UI needed — record this as the parity decision.

- [ ] **Step 2: Commit (test only)**

```bash
git add frontend/src/features/curation/editor/CandidateFieldEditor.test.tsx
git commit -m "test(curation-ui): inline-edit parity covered by the field editor"
```

---

## Task 5: Retire `EntityTagTable` (single surface)

**Files:**
- Modify: `frontend/src/pages/CurationWorkspacePage.tsx` (remove the legacy branch)
- Delete: `frontend/src/features/curation/entityTable/` (table, row, toolbar, inline-edit, types) — only what's now unused

- [ ] **Step 1: Confirm parity is complete**

Checklist before deletion — every legacy affordance has an envelope-surface equivalent: accept/reject (B2 + editor footer) ✓; accept-all-validated (T1) ✓; add manual (T3) ✓; delete (T2) ✓; inline edit (T4 → field editor) ✓; evidence preview (B2 group/field evidence chips) ✓.

- [ ] **Step 2: Remove the runtime branch**

In `CurationWorkspacePage.tsx`, render the envelope work pane unconditionally for the workspace (remove the `hasEnvelopeObjectRows ? <Envelope…> : <EntityTagTable…>` split and the `EntityTagTable` import). Keep the envelope review-rows query.

> Safety: confirm no live session type is non-envelope. From the audit/diagnosis, **all current candidates are `source = extracted` and envelope-backed** (0 manual candidates DB-wide). If a non-envelope path is still possible (e.g. a legacy session), gate behind a feature check first; otherwise remove. Verify with: `grep -rn "EntityTagTable" frontend/src` returns nothing after removal.

- [ ] **Step 3: Delete now-unused legacy files**

Run: `grep -rn "entityTable/" frontend/src` — remove imports, then delete the files that are no longer referenced (`EntityTagTable.tsx`, `EntityTagRow.tsx`, `EntityTagToolbar.tsx`, `InlineEditRow.tsx`, and their tests). Leave shared types/helpers still referenced elsewhere (e.g. `buildManualCandidateDraft`) in place; move them out of `entityTable/` if needed so deletion doesn't break the manual-add bridge.

- [ ] **Step 4: Full suite + typecheck**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: green; no dangling imports.

- [ ] **Step 5: Manual regression on dev**

On `http://10.79.64.167:3900/curation/<session>`: accept/reject, accept-all-validated, add object, delete object, edit a field + save, evidence highlight — all work on the single surface.

- [ ] **Step 6: Commit**

```bash
git add -A frontend/src/features/curation frontend/src/pages/CurationWorkspacePage.tsx
git commit -m "refactor(curation-ui): retire EntityTagTable; single envelope review surface"
```

---

## gpt-5.5 Review Corrections (fold in before implementing)

Verdict: **Sound-with-corrections.** The central claim (handlers + routes exist; UI re-threading) is verified. Apply these:

1. **Manual candidates are real and non-envelope — the single surface must list ALL candidates.** `create_manual_candidate` creates `source = manual` candidates **without** envelope fields, so their `projection_ref` is `None` (`session_mutation_service.py:378`, `session_serializers.py:258`). The envelope review-rows builder **filters out** non-`projection_ref` candidates (`envelopeObjectReviewRows.ts:65`). So the new object selector / work pane (B2) must enumerate **`workspace.candidates`** (all of them), not only envelope review rows — otherwise a manually-added object disappears. Fix the Task 1/Task 3 wiring and the Task 5 retire-safety: "all candidates are envelope-backed" is a current **DB audit**, not a code invariant.

2. **Task 5 — moving shared helpers is mandatory, not optional.** `CurationWorkspacePage` imports `EntityTag`, `buildEntityTagFieldChanges`, `buildManualCandidateDraft` from `entityTable/`, and **`frontend/src/features/curation/types.ts` imports `EntityTag` from `entityTable/types.ts`** (`types.ts:43`). Deleting `entityTable/` wholesale breaks these. Before deletion, **move** `types.ts` (the `EntityTag` type), `literatureEntityTypeCatalog.ts`, and `workspaceEntityTags.ts` (the bridges) out of `entityTable/` into a kept location and update imports. Only then delete the table/row/toolbar/inline-edit components.

3. **Task 1 — define "validated pending" via envelope validation.** Legacy keyed on `db_status === 'validated'`; the envelope candidate has no `db_status`. Define `countValidatedPending` from the candidate's validation summary: `status === 'pending'` **and** no open blocking findings (e.g. `open_finding_count === 0` / strongest status not `unresolved`/`blocked`). Specify this concretely in Task 1 Step 3.

4. **Wording:** `handleSaveTag` does **not** flush autosave (the plan's "all four flush autosave" applies to accept/reject/delete, not save). And inline-edit parity runs through the field editor's `autosave.queueFieldChange` + "Save draft" (`autosave.flush()`, `CandidateFieldEditor.tsx:777,986`), **not** `handleSaveTag`. Correct Task 4's parity note.

5. **Sequencing confirmed:** B3 depends on B2 (the `ObjectSelectorStrip`/work pane don't exist yet). Page handlers (`:349/:377/:424/:471`), backend routes (`:564/:661/:681/:701`), and the legacy affordance sources are all verified present.

## Self-Review (completed)

- **Spec coverage:** the four legacy affordances (delete T2, manual-add T3, accept-all T1, inline-edit T4) rebuilt; single surface achieved (T5). Backend untouched (routes already exist). Matches Build B spec §3 "rebuild affordances before retiring the legacy table."
- **Placeholder scan:** each new component has props + an MUI analogue cited + an RTL test; the one runtime decision (is any non-envelope session still possible) is an explicit verify-then-gate step.
- **Type consistency:** `WorkPaneToolbar`/dialogs use `() => void` + `(payload) => void` callbacks wired to the existing `handleDeleteTag(id)` / `handleCreateManualTag(EntityTag-ish)` / `handleAcceptAllValidated(ids)`; the manual-object payload shape matches what `buildManualCandidateDraft` consumes (name/type/species/topic). Deletion step verifies no dangling `EntityTagTable`/`entityTable/` references remain.
