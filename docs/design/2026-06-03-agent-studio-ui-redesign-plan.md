# Agent Studio UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Chris-approved Agent Studio redesign (Workshop section-tabs layout + curator-voice prompt-layer labels + model cleanups + an app-wide visual theme + component upgrades), turning the validated throwaway prototype into clean, tested production code.

**Architecture:** The approved design was validated as a hot-reload prototype whose edits are ALREADY in the working tree (uncommitted) on branch `agent-studio-phase1-doc-migration`: `frontend/index.html`, `frontend/src/theme.ts`, `frontend/src/App.tsx`, `frontend/src/components/AgentStudio/PromptWorkshop/PromptWorkshop.tsx`, `frontend/src/components/AgentStudio/ToolDetailsDialog.tsx`, `frontend/src/components/AgentStudio/AgentDetailsPanel.tsx`, and a dev-only `frontend/vite.config.ts` proxy env var. This plan does NOT rebuild from scratch — it verifies each prototype change against the spec, productionizes the one hacky bit (the model allow-list), adds focused vitest coverage, and commits in logical units. Spec: `docs/design/2026-06-03-agent-studio-ui-redesign-design.md`.

**Tech Stack:** React 18 + MUI v5 + styled-components, Vite, vitest + @testing-library/react. Dark theme default.

**Scope:** This plan delivers the parts that were fully designed and prototyped — (A) theme, (B) Workshop, (C) component upgrades — each tested. The **Agents-tab detail view restructure** Chris flagged is only partly addressed here (it already inherits the app-wide theme + the Tool-Details slide-over + composed empty state); a deeper restructure of that panel is a **separate follow-up** that needs its own short design pass and is intentionally NOT planned in detail here (Task 8 covers verifying it benefits + a light density check, and records the follow-up).

---

## File structure

- `frontend/index.html` — add the Geist webfont (or @fontsource import; see Task 1).
- `frontend/src/theme.ts` — font family, dark palette (vivid accent + deep-blue AppBar + layered surfaces), tinted shadows, tabular figures. One responsibility: the MUI theme.
- `frontend/src/theme.test.ts` — NEW: asserts the key design tokens so the palette/font can't silently regress.
- `frontend/src/App.tsx` — version-chip color fix for the colored AppBar (already applied).
- `frontend/src/components/AgentStudio/PromptWorkshop/PromptWorkshop.tsx` — section-tabs layout, identity crown, grouped Setup, compact fields, curator-voice labels, model cleanups.
- `frontend/src/components/AgentStudio/PromptWorkshop/PromptWorkshop.test.tsx` — extend: section gating, labels, model allow-list, no Output Schema Key.
- `frontend/src/components/AgentStudio/PromptWorkshop/workshopModels.ts` — NEW: the documented model allow-list constant (productionizes the prototype's inline filter).
- `frontend/src/components/AgentStudio/ToolDetailsDialog.tsx` — Dialog → right-anchored Drawer.
- `frontend/src/components/AgentStudio/AgentDetailsPanel.tsx` — composed empty state (already applied).
- `frontend/src/components/AgentStudio/AgentDetailsPanel.test.tsx` — extend: composed empty state.

---

## Task 1: Theme — font, palette, shadows, tabular figures

**Files:**
- Modify: `frontend/src/theme.ts` (already changed by prototype; verify against spec)
- Modify: `frontend/index.html` (font load)
- Modify: `frontend/src/App.tsx` (version-chip color; already changed)
- Test: `frontend/src/theme.test.ts` (create)

- [ ] **Step 1: Decide + standardize the font load.** Check whether the project self-hosts fonts: `grep -rn "@fontsource" frontend/package.json frontend/src/main.tsx`. If `@fontsource/*` is already used, prefer self-hosting Geist the same way (`npm i @fontsource/geist` then `import '@fontsource/geist/400.css'` … in `main.tsx`) and remove the CDN `<link>` from index.html. If the project uses CDN font links, keep the prototype's Google Fonts `<link>` in `index.html`. Either way the theme `fontFamily` stays `'"Geist","Inter","Roboto","Helvetica","Arial",sans-serif'`.

- [ ] **Step 2: Write the theme token test.**

```ts
// frontend/src/theme.test.ts
import { describe, it, expect } from 'vitest'
import { createAppTheme } from './theme'

describe('app theme (dark)', () => {
  const t = createAppTheme('dark')
  it('uses the Geist font stack', () => {
    expect(t.typography.fontFamily).toMatch(/^"Geist"/)
  })
  it('uses the vivid (not neon, not washed-out) blue accent', () => {
    expect(t.palette.primary.main.toLowerCase()).toBe('#3b82f6')
  })
  it('keeps layered surfaces with real contrast', () => {
    expect(t.palette.background.default.toLowerCase()).toBe('#0f1217')
    expect(t.palette.background.paper.toLowerCase()).toBe('#1b212b')
    expect(t.palette.background.default).not.toBe(t.palette.background.paper)
  })
  it('renders a colored AppBar (not flat dark)', () => {
    const appBar = (t.components?.MuiAppBar?.styleOverrides?.root ?? {}) as Record<string, unknown>
    expect(String(appBar.backgroundColor).toLowerCase()).toBe('#1c5fb8')
  })
  it('enables tabular figures on the body', () => {
    const body = (t.components?.MuiCssBaseline?.styleOverrides as { body?: Record<string, unknown> } | undefined)?.body ?? {}
    expect(body.fontVariantNumeric).toBe('tabular-nums')
  })
})
```

- [ ] **Step 3: Run it.** `cd frontend && npx vitest run src/theme.test.ts` → Expected: PASS (theme.ts already carries these values from the prototype). If a token differs, fix `theme.ts` to match the spec values, not the test.

- [ ] **Step 4: Typecheck touched files.** `cd frontend && npx tsc --noEmit 2>&1 | grep -iE "theme|App\.tsx" || echo clean` → Expected: `clean`.

- [ ] **Step 5: Commit.**

```bash
git add frontend/src/theme.ts frontend/src/theme.test.ts frontend/index.html frontend/src/App.tsx frontend/package.json frontend/src/main.tsx
git commit -m "feat(agent-studio): app-wide visual theme — Geist font, vivid accent, deep-blue AppBar, layered surfaces, tinted shadows, tabular figures"
```

---

## Task 2: Workshop section-tabs layout (structure)

**Files:**
- Modify: `frontend/src/components/AgentStudio/PromptWorkshop/PromptWorkshop.tsx` (already changed by prototype)
- Test: `frontend/src/components/AgentStudio/PromptWorkshop/PromptWorkshop.test.tsx` (extend)

- [ ] **Step 1: Read the existing test file** to reuse its render harness/mocks (it already mounts PromptWorkshop with the needed providers/mocks). Match its imports and any `vi.mock` setup.

- [ ] **Step 2: Add a section-gating test.** Using the existing harness, assert: the fixed header shows the "Configure your agent" overline and the four tabs; on load only the Setup section is shown; clicking "Reference" hides Setup content and shows the read-only intro.

```tsx
it('shows one workshop section at a time via the fixed nav', async () => {
  renderWorkshop() // existing helper in this file
  expect(screen.getByText(/configure your agent/i)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Setup' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Prompt' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Tools' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Reference' })).toBeInTheDocument()
  // Setup is default
  expect(screen.getByText('Starting point')).toBeInTheDocument()
  // Switch to Reference
  fireEvent.click(screen.getByRole('button', { name: 'Reference' }))
  expect(screen.getByText(/built-in instruction layers that make up this agent/i)).toBeInTheDocument()
  expect(screen.queryByText('Starting point')).not.toBeInTheDocument()
})
```

- [ ] **Step 3: Run it.** `cd frontend && npx vitest run src/components/AgentStudio/PromptWorkshop/PromptWorkshop.test.tsx -t "one workshop section"` → Expected: PASS. If the render helper needs more mocks, add them following the existing file's pattern.

- [ ] **Step 4: Commit.**

```bash
git add frontend/src/components/AgentStudio/PromptWorkshop/PromptWorkshop.tsx frontend/src/components/AgentStudio/PromptWorkshop/PromptWorkshop.test.tsx
git commit -m "feat(agent-studio): Workshop section-tabs layout (fixed header + Configure-Your-Agent crown + grouped Setup, compact fields)"
```

---

## Task 3: Workshop curator-voice labels + wording

**Files:**
- Modify: `frontend/src/components/AgentStudio/PromptWorkshop/PromptWorkshop.tsx` (already changed)
- Test: `frontend/src/components/AgentStudio/PromptWorkshop/PromptWorkshop.test.tsx` (extend)

- [ ] **Step 1: Add a labels test.** Assert the curator-voice names are present and the old jargon is gone.

```tsx
it('uses curator-friendly prompt-layer labels', () => {
  renderWorkshop()
  fireEvent.click(screen.getByRole('button', { name: 'Reference' }))
  expect(screen.getByText('Built-in instructions')).toBeInTheDocument()
  expect(screen.getByText('Output structure')).toBeInTheDocument()
  expect(screen.getByText('Template instructions')).toBeInTheDocument()
  expect(screen.getByText('Species & group rules')).toBeInTheDocument()
  expect(screen.queryByText('Core Prompt')).not.toBeInTheDocument()
  expect(screen.queryByText('Generated Contract')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Prompt' }))
  expect(screen.getByText('Your custom instructions')).toBeInTheDocument()
  expect(screen.getByText('Final instructions (preview)')).toBeInTheDocument()
  expect(screen.queryByText('Curator Overlay')).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run it.** `cd frontend && npx vitest run src/components/AgentStudio/PromptWorkshop/PromptWorkshop.test.tsx -t "curator-friendly"` → Expected: PASS.

- [ ] **Step 3: Commit.**

```bash
git add frontend/src/components/AgentStudio/PromptWorkshop/PromptWorkshop.test.tsx
git commit -m "test(agent-studio): lock curator-voice prompt-layer labels in the Workshop"
```

---

## Task 4: Model cleanups (allow-list, drop Output Schema Key, tidy box)

**Files:**
- Create: `frontend/src/components/AgentStudio/PromptWorkshop/workshopModels.ts`
- Modify: `frontend/src/components/AgentStudio/PromptWorkshop/PromptWorkshop.tsx` (the prototype filters models inline; replace with the constant)
- Test: `frontend/src/components/AgentStudio/PromptWorkshop/PromptWorkshop.test.tsx` (extend)

- [ ] **Step 1: Extract the model allow-list to a documented constant** (productionizes the prototype's inline filter).

```ts
// frontend/src/components/AgentStudio/PromptWorkshop/workshopModels.ts
// Models offered to curators in the Workshop. Keep in sync with the models
// actually wired in packages/core/config/models.yaml. We intentionally hide
// experimental/unused entries (e.g. gpt-oss-120b) from the curator-facing picker.
export const WORKSHOP_MODEL_IDS = ['gpt-5.5', 'gpt-5.4-mini'] as const

export function isWorkshopModel(modelId: string): boolean {
  return (WORKSHOP_MODEL_IDS as readonly string[]).includes(modelId)
}
```

- [ ] **Step 2: Use the constant in PromptWorkshop.tsx** — replace the inline `modelOptions.filter(...)` with `modelOptions.filter((m) => isWorkshopModel(m.model_id) || m.model_id === selectedModelId)` (keep a selected-but-filtered model visible so nothing breaks). Import `isWorkshopModel` from `./workshopModels`.

- [ ] **Step 3: Add tests** (allow-list + no Output Schema Key field).

```tsx
import { WORKSHOP_MODEL_IDS, isWorkshopModel } from './workshopModels'

it('only offers the supported models', () => {
  expect(WORKSHOP_MODEL_IDS).toEqual(['gpt-5.5', 'gpt-5.4-mini'])
  expect(isWorkshopModel('openai/gpt-oss-120b')).toBe(false)
})

it('does not show an Output Schema Key field', () => {
  renderWorkshop()
  expect(screen.queryByLabelText(/output schema key/i)).not.toBeInTheDocument()
})
```

- [ ] **Step 4: Run it.** `cd frontend && npx vitest run src/components/AgentStudio/PromptWorkshop/PromptWorkshop.test.tsx -t "supported models"` and `-t "Output Schema Key"` → Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add frontend/src/components/AgentStudio/PromptWorkshop/workshopModels.ts frontend/src/components/AgentStudio/PromptWorkshop/PromptWorkshop.tsx frontend/src/components/AgentStudio/PromptWorkshop/PromptWorkshop.test.tsx
git commit -m "feat(agent-studio): Workshop model picker = supported models only; drop Output Schema Key; tidy model box"
```

---

## Task 5: Tool Details slide-over (Drawer)

**Files:**
- Modify: `frontend/src/components/AgentStudio/ToolDetailsDialog.tsx` (already changed)
- Test: `frontend/src/components/AgentStudio/ToolDetailsDialog.test.tsx` (create if absent, else extend)

- [ ] **Step 1: Add a render test** confirming it opens as a panel with the tool content + a Close control. Mock the tool-details fetch the component uses (check the component's imports for the service to mock; mirror AgentDetailsPanel.test.tsx's `vi.mock` style).

```tsx
it('renders tool details in a slide-over with a close control', async () => {
  // mock the tool-details service per the component's import
  render(<ToolDetailsDialog open toolId="search_document" agentId="gene_extractor" onClose={vi.fn()} />)
  expect(await screen.findByText('Tool Details')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument()
})
```

- [ ] **Step 2: Run it.** `cd frontend && npx vitest run src/components/AgentStudio/ToolDetailsDialog.test.tsx` → Expected: PASS. Adjust the mock to match the component's actual service/props.

- [ ] **Step 3: Commit.**

```bash
git add frontend/src/components/AgentStudio/ToolDetailsDialog.tsx frontend/src/components/AgentStudio/ToolDetailsDialog.test.tsx
git commit -m "feat(agent-studio): Tool Details as a right-anchored slide-over instead of a modal"
```

---

## Task 6: Agent Browser composed empty state

**Files:**
- Modify: `frontend/src/components/AgentStudio/AgentDetailsPanel.tsx` (already changed)
- Test: `frontend/src/components/AgentStudio/AgentDetailsPanel.test.tsx` (extend)

- [ ] **Step 1: Add an empty-state test.**

```tsx
it('shows a composed empty state when no agent is selected', () => {
  render(<AgentDetailsPanel agent={null} selectedGroupId={null} onGroupSelect={vi.fn()} />)
  expect(screen.getByText('Browse your agents')).toBeInTheDocument()
  expect(screen.getByText(/pick an agent on the left/i)).toBeInTheDocument()
})
```

- [ ] **Step 2: Run it.** `cd frontend && npx vitest run src/components/AgentStudio/AgentDetailsPanel.test.tsx -t "composed empty state"` → Expected: PASS.

- [ ] **Step 3: Commit.**

```bash
git add frontend/src/components/AgentStudio/AgentDetailsPanel.tsx frontend/src/components/AgentStudio/AgentDetailsPanel.test.tsx
git commit -m "feat(agent-studio): composed 'Browse your agents' empty state"
```

---

## Task 7: dev affordance + full verification

**Files:**
- Modify: `frontend/vite.config.ts` (decide on the dev proxy env var)

- [ ] **Step 1: Keep the dev proxy override.** The prototype made the Vite dev proxy target overridable via `process.env.VITE_API_PROXY_TARGET` with the existing `'http://backend:8000'` default — backwards-compatible and useful for running the dev server against a remote/sandbox backend. Keep it; it does not affect the production build.

- [ ] **Step 2: Run the full Agent Studio frontend test set.** `cd frontend && npx vitest run src/components/AgentStudio src/theme.test.ts` → Expected: all PASS.

- [ ] **Step 3: Typecheck the touched files.** `cd frontend && npx tsc --noEmit 2>&1 | grep -iE "PromptWorkshop|ToolDetailsDialog|AgentDetailsPanel|theme|workshopModels|App\.tsx" || echo clean` → Expected: `clean` (the repo has a large PRE-EXISTING tsc baseline in unrelated files; ignore those).

- [ ] **Step 4: Manual spot-check across pages** (theme is app-wide). With the stack up, open Home, Curation, Documents, Batch, and Agent Studio; confirm the dark theme reads well everywhere (colored bar, readable contrast, no broken light-on-light or dark-on-dark), and the Workshop / tool slide-over / empty state look right. Fix any contrast regressions in `theme.ts`.

- [ ] **Step 5: Commit any fixes.**

```bash
git add frontend/vite.config.ts
git commit -m "chore(agent-studio): keep dev proxy override; finalize redesign verification"
```

---

## Task 8: Agents-tab detail view — verify + record follow-up

**Files:** none required beyond verification.

- [ ] **Step 1: Verify the detail view benefits from the shared work.** Open an agent (e.g. a Data Validation agent) and confirm it inherits the new theme, the Tool-Details slide-over, and reads better than before. Note any remaining density problems.

- [ ] **Step 2: Record the follow-up.** The deeper Agents-tab detail restructure (identity treatment for the selected agent, density reduction beyond the theme) was flagged by Chris but never concretely designed/prototyped. Capture it as a separate brainstorm → spec → plan effort (do NOT improvise a restructure here). Add a short note to the design spec's "Agents-detail" section listing the concrete pain points observed in Step 1, so the follow-up has a starting point.

---

## Self-review notes
- **Spec coverage:** theme (Task 1), Workshop structure (Task 2), labels/wording (Task 3), model cleanups (Task 4), Tool slide-over (Task 5), empty state (Task 6), verification (Task 7), Agents-detail (Task 8 = verify + follow-up, intentionally not a deep restructure). All spec sections mapped; the Agents-detail deep restructure is explicitly deferred (under-designed).
- **Reuse, not rebuild:** every code change already exists in the working tree from the validated prototype; tasks verify-against-spec + test + commit, plus the one productionization (Task 4 model constant).
- **Tests:** theme tokens, section gating, labels, model allow-list, empty state, tool slide-over — the behaviors most likely to regress.
