# Curation Workspace UI Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current curation workspace right panel (candidate queue + annotation editor + evidence panel) with a literature-UI-aligned entity tag table + evidence preview pane.

**Architecture:** The right panel becomes two vertically-split zones: an editable MUI table showing entity tags (AI-extracted or manually added) with inline accept/reject actions, and a slim evidence preview pane showing the selected row's sentence quote with a link to highlight it in the PDF viewer. The existing PDF-to-evidence event system (`pdfEvents.ts`) is reused without modification.

**Tech Stack:** React 18, MUI v5 (dark theme), TypeScript, Vitest + React Testing Library, react-resizable-panels

**Spec:** `docs/design/2026-03-30-curation-ui-redesign-design.md`

---

## File Structure

### New files

| File | Responsibility |
|------|---------------|
| `frontend/src/features/curation/entityTable/types.ts` | EntityTag interface, decision/source/status enums |
| `frontend/src/features/curation/entityTable/entityTagNavigation.ts` | Build EvidenceNavigationCommand from EntityTag evidence |
| `frontend/src/features/curation/entityTable/useEntityTagState.ts` | State hook: tag array, selection, editing, decisions |
| `frontend/src/features/curation/entityTable/EntityTagToolbar.tsx` | Header bar with counts, "Accept All Validated", "+ Add Entity" |
| `frontend/src/features/curation/entityTable/EntityTagRow.tsx` | Single table row: display mode with decision buttons |
| `frontend/src/features/curation/entityTable/InlineEditRow.tsx` | Single table row: edit mode with inputs, save/cancel |
| `frontend/src/features/curation/entityTable/EvidencePreviewPane.tsx` | Quote card, metadata, "Show in PDF" link |
| `frontend/src/features/curation/entityTable/EntityTagTable.tsx` | Composed table: toolbar + rows + preview pane |
| `frontend/src/features/curation/entityTable/index.ts` | Public exports |

### Modified files

| File | Change |
|------|--------|
| `frontend/src/features/curation/workspace/WorkspaceShell.tsx` | Remove queue/toolbar slots, simplify to PDF + table + evidence two-panel layout |
| `frontend/src/features/curation/workspace/WorkspaceShell.test.tsx` | Update test expectations for new slot names and panel structure |
| `frontend/src/pages/CurationWorkspacePage.tsx` | Wire EntityTagTable into WorkspaceShell instead of old components |

### Test files (co-located with source)

| File |
|------|
| `frontend/src/features/curation/entityTable/types.test.ts` |
| `frontend/src/features/curation/entityTable/entityTagNavigation.test.ts` |
| `frontend/src/features/curation/entityTable/useEntityTagState.test.ts` |
| `frontend/src/features/curation/entityTable/EntityTagToolbar.test.tsx` |
| `frontend/src/features/curation/entityTable/EntityTagRow.test.tsx` |
| `frontend/src/features/curation/entityTable/InlineEditRow.test.tsx` |
| `frontend/src/features/curation/entityTable/EvidencePreviewPane.test.tsx` |
| `frontend/src/features/curation/entityTable/EntityTagTable.test.tsx` |

---

## Task 1: Define EntityTag Types

**Files:**
- Create: `frontend/src/features/curation/entityTable/types.ts`
- Test: `frontend/src/features/curation/entityTable/types.test.ts`

- [ ] **Step 1: Write the type validation test**

```typescript
// frontend/src/features/curation/entityTable/types.test.ts
import { describe, expect, it } from 'vitest'
import {
  ENTITY_TAG_DECISIONS,
  ENTITY_TAG_SOURCES,
  DB_VALIDATION_STATUSES,
  ENTITY_TYPE_CODES,
  type EntityTag,
} from './types'

describe('EntityTag type constants', () => {
  it('defines three decision states', () => {
    expect(ENTITY_TAG_DECISIONS).toEqual(['pending', 'accepted', 'rejected'])
  })

  it('defines two source types', () => {
    expect(ENTITY_TAG_SOURCES).toEqual(['ai', 'manual'])
  })

  it('defines three DB validation statuses', () => {
    expect(DB_VALIDATION_STATUSES).toEqual(['validated', 'ambiguous', 'not_found'])
  })

  it('defines the literature UI entity type ATP codes', () => {
    expect(ENTITY_TYPE_CODES).toContain('ATP:0000005') // gene
    expect(ENTITY_TYPE_CODES).toContain('ATP:0000006') // allele
    expect(ENTITY_TYPE_CODES).toContain('ATP:0000123') // species
  })

  it('allows constructing a valid EntityTag object', () => {
    const tag: EntityTag = {
      tag_id: 'tag-1',
      entity_name: 'daf-2',
      entity_type: 'ATP:0000005',
      species: 'NCBITaxon:6239',
      topic: 'gene expression',
      db_status: 'validated',
      db_entity_id: 'WBGene00000898',
      source: 'ai',
      decision: 'pending',
      evidence: {
        sentence_text: 'The daf-2 receptor regulates lifespan.',
        page_number: 3,
        section_title: 'Results',
        chunk_ids: ['chunk-1'],
      },
      notes: null,
    }
    expect(tag.tag_id).toBe('tag-1')
    expect(tag.evidence?.sentence_text).toContain('daf-2')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/curation/entityTable/types.test.ts`
Expected: FAIL — module not found

- [ ] **Step 3: Write the types module**

```typescript
// frontend/src/features/curation/entityTable/types.ts

export const ENTITY_TAG_DECISIONS = ['pending', 'accepted', 'rejected'] as const
export type EntityTagDecision = (typeof ENTITY_TAG_DECISIONS)[number]

export const ENTITY_TAG_SOURCES = ['ai', 'manual'] as const
export type EntityTagSource = (typeof ENTITY_TAG_SOURCES)[number]

export const DB_VALIDATION_STATUSES = ['validated', 'ambiguous', 'not_found'] as const
export type DbValidationStatus = (typeof DB_VALIDATION_STATUSES)[number]

export const ENTITY_TYPE_CODES = [
  'ATP:0000005',  // gene
  'ATP:0000006',  // allele
  'ATP:0000123',  // species
  'ATP:0000027',  // strain
  'ATP:0000025',  // genotype
  'ATP:0000026',  // fish
  'ATP:0000013',  // transgenic construct
  'ATP:0000110',  // transgenic allele
  'ATP:0000285',  // classical allele
  'ATP:0000093',  // sequence targeting reagent
] as const
export type EntityTypeCode = (typeof ENTITY_TYPE_CODES)[number]

export const ENTITY_TYPE_LABELS: Record<EntityTypeCode, string> = {
  'ATP:0000005': 'gene',
  'ATP:0000006': 'allele',
  'ATP:0000123': 'species',
  'ATP:0000027': 'strain',
  'ATP:0000025': 'genotype',
  'ATP:0000026': 'fish',
  'ATP:0000013': 'transgenic construct',
  'ATP:0000110': 'transgenic allele',
  'ATP:0000285': 'classical allele',
  'ATP:0000093': 'sequence targeting reagent',
}

export interface EntityTagEvidence {
  sentence_text: string
  page_number: number | null
  section_title: string | null
  chunk_ids: string[]
}

export interface EntityTag {
  tag_id: string
  entity_name: string
  entity_type: EntityTypeCode | string
  species: string
  topic: string
  db_status: DbValidationStatus
  db_entity_id: string | null
  source: EntityTagSource
  decision: EntityTagDecision
  evidence: EntityTagEvidence | null
  notes: string | null
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/curation/entityTable/types.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/curation/entityTable/types.ts frontend/src/features/curation/entityTable/types.test.ts
git commit -m "feat(curation): define EntityTag types for entity table redesign"
```

---

## Task 2: Build Entity Tag Navigation (PDF Highlighting Bridge)

**Files:**
- Create: `frontend/src/features/curation/entityTable/entityTagNavigation.ts`
- Test: `frontend/src/features/curation/entityTable/entityTagNavigation.test.ts`
- Reference: `frontend/src/components/Chat/chatEvidenceNavigation.ts` (existing pattern)

- [ ] **Step 1: Write the navigation command builder test**

```typescript
// frontend/src/features/curation/entityTable/entityTagNavigation.test.ts
import { describe, expect, it } from 'vitest'
import { buildEntityTagNavigationCommand } from './entityTagNavigation'
import type { EntityTag } from './types'

const makeTag = (overrides: Partial<EntityTag> = {}): EntityTag => ({
  tag_id: 'tag-1',
  entity_name: 'daf-2',
  entity_type: 'ATP:0000005',
  species: 'NCBITaxon:6239',
  topic: 'gene expression',
  db_status: 'validated',
  db_entity_id: 'WBGene00000898',
  source: 'ai',
  decision: 'pending',
  evidence: {
    sentence_text: 'The daf-2 receptor regulates lifespan.',
    page_number: 3,
    section_title: 'Results',
    chunk_ids: ['chunk-1'],
  },
  notes: null,
  ...overrides,
})

describe('buildEntityTagNavigationCommand', () => {
  it('builds a navigation command from a tag with evidence', () => {
    const command = buildEntityTagNavigationCommand(makeTag())

    expect(command.anchorId).toContain('entity-tag:tag-1')
    expect(command.searchText).toBe('The daf-2 receptor regulates lifespan.')
    expect(command.pageNumber).toBe(3)
    expect(command.sectionTitle).toBe('Results')
    expect(command.mode).toBe('select')
    expect(command.anchor.anchor_kind).toBe('sentence')
    expect(command.anchor.locator_quality).toBe('exact_quote')
    expect(command.anchor.sentence_text).toBe('The daf-2 receptor regulates lifespan.')
    expect(command.anchor.page_number).toBe(3)
    expect(command.anchor.section_title).toBe('Results')
    expect(command.anchor.viewer_search_text).toBe('The daf-2 receptor regulates lifespan.')
    expect(command.anchor.viewer_highlightable).toBe(true)
    expect(command.anchor.chunk_ids).toEqual(['chunk-1'])
  })

  it('returns null for a tag without evidence', () => {
    const command = buildEntityTagNavigationCommand(
      makeTag({ evidence: null }),
    )
    expect(command).toBeNull()
  })

  it('returns null for a tag with empty sentence text', () => {
    const command = buildEntityTagNavigationCommand(
      makeTag({ evidence: { sentence_text: '  ', page_number: 3, section_title: 'Results', chunk_ids: [] } }),
    )
    expect(command).toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/curation/entityTable/entityTagNavigation.test.ts`
Expected: FAIL — module not found

- [ ] **Step 3: Write the navigation command builder**

```typescript
// frontend/src/features/curation/entityTable/entityTagNavigation.ts
import type { EvidenceNavigationCommand } from '@/features/curation/evidence'
import type { EntityTag } from './types'

export function buildEntityTagNavigationCommand(
  tag: EntityTag,
): EvidenceNavigationCommand | null {
  if (!tag.evidence) return null

  const quote = tag.evidence.sentence_text.trim()
  if (!quote) return null

  return {
    anchorId: `entity-tag:${tag.tag_id}`,
    anchor: {
      anchor_kind: 'sentence',
      locator_quality: 'exact_quote',
      supports_decision: 'supports',
      snippet_text: quote,
      sentence_text: quote,
      normalized_text: quote,
      viewer_search_text: quote,
      viewer_highlightable: true,
      page_number: tag.evidence.page_number,
      section_title: tag.evidence.section_title,
      chunk_ids: tag.evidence.chunk_ids,
    },
    searchText: quote,
    pageNumber: tag.evidence.page_number,
    sectionTitle: tag.evidence.section_title,
    mode: 'select',
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/curation/entityTable/entityTagNavigation.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/curation/entityTable/entityTagNavigation.ts frontend/src/features/curation/entityTable/entityTagNavigation.test.ts
git commit -m "feat(curation): build entity tag to PDF navigation command bridge"
```

---

## Task 3: Build useEntityTagState Hook

**Files:**
- Create: `frontend/src/features/curation/entityTable/useEntityTagState.ts`
- Test: `frontend/src/features/curation/entityTable/useEntityTagState.test.ts`

- [ ] **Step 1: Write the state hook test**

```typescript
// frontend/src/features/curation/entityTable/useEntityTagState.test.ts
import { renderHook, act } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useEntityTagState } from './useEntityTagState'
import type { EntityTag } from './types'

const makeTags = (): EntityTag[] => [
  {
    tag_id: 'tag-1', entity_name: 'daf-2', entity_type: 'ATP:0000005',
    species: 'NCBITaxon:6239', topic: 'gene expression', db_status: 'validated',
    db_entity_id: 'WBGene00000898', source: 'ai', decision: 'pending',
    evidence: { sentence_text: 'daf-2 regulates lifespan.', page_number: 3, section_title: 'Results', chunk_ids: ['c1'] },
    notes: null,
  },
  {
    tag_id: 'tag-2', entity_name: 'ins-1', entity_type: 'ATP:0000005',
    species: 'NCBITaxon:6239', topic: 'gene expression', db_status: 'ambiguous',
    db_entity_id: null, source: 'ai', decision: 'pending',
    evidence: { sentence_text: 'ins-1 is an insulin peptide.', page_number: 5, section_title: 'Discussion', chunk_ids: ['c2'] },
    notes: null,
  },
]

describe('useEntityTagState', () => {
  it('initializes with tags and no selection', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags()))
    expect(result.current.tags).toHaveLength(2)
    expect(result.current.selectedTagId).toBeNull()
    expect(result.current.editingTagId).toBeNull()
  })

  it('selects a tag by id', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags()))
    act(() => result.current.selectTag('tag-1'))
    expect(result.current.selectedTagId).toBe('tag-1')
    expect(result.current.selectedTag?.entity_name).toBe('daf-2')
  })

  it('accepts a pending tag', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags()))
    act(() => result.current.acceptTag('tag-1'))
    const tag = result.current.tags.find(t => t.tag_id === 'tag-1')
    expect(tag?.decision).toBe('accepted')
  })

  it('rejects a pending tag', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags()))
    act(() => result.current.rejectTag('tag-2'))
    const tag = result.current.tags.find(t => t.tag_id === 'tag-2')
    expect(tag?.decision).toBe('rejected')
  })

  it('accepts all validated tags', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags()))
    act(() => result.current.acceptAllValidated())
    const tag1 = result.current.tags.find(t => t.tag_id === 'tag-1')
    const tag2 = result.current.tags.find(t => t.tag_id === 'tag-2')
    expect(tag1?.decision).toBe('accepted')  // validated + pending
    expect(tag2?.decision).toBe('pending')   // ambiguous, not auto-accepted
  })

  it('enters and exits edit mode', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags()))
    act(() => result.current.startEditing('tag-1'))
    expect(result.current.editingTagId).toBe('tag-1')
    act(() => result.current.cancelEditing())
    expect(result.current.editingTagId).toBeNull()
  })

  it('updates a tag via saveEdit', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags()))
    act(() => result.current.startEditing('tag-1'))
    act(() => result.current.saveEdit('tag-1', { entity_name: 'daf-2 (edited)', topic: 'phenotype' }))
    const tag = result.current.tags.find(t => t.tag_id === 'tag-1')
    expect(tag?.entity_name).toBe('daf-2 (edited)')
    expect(tag?.topic).toBe('phenotype')
    expect(result.current.editingTagId).toBeNull()
  })

  it('adds a new manual tag', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags()))
    act(() => result.current.addManualTag())
    expect(result.current.tags).toHaveLength(3)
    const newTag = result.current.tags[2]
    expect(newTag.source).toBe('manual')
    expect(newTag.decision).toBe('pending')
    expect(newTag.entity_name).toBe('')
    expect(result.current.editingTagId).toBe(newTag.tag_id)
  })

  it('returns pending count', () => {
    const { result } = renderHook(() => useEntityTagState(makeTags()))
    expect(result.current.pendingCount).toBe(2)
    act(() => result.current.acceptTag('tag-1'))
    expect(result.current.pendingCount).toBe(1)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/curation/entityTable/useEntityTagState.test.ts`
Expected: FAIL

- [ ] **Step 3: Write the state hook**

```typescript
// frontend/src/features/curation/entityTable/useEntityTagState.ts
import { useCallback, useMemo, useState } from 'react'
import type { EntityTag } from './types'

let nextManualId = 1

function generateManualTagId(): string {
  return `manual-${Date.now()}-${nextManualId++}`
}

export function useEntityTagState(initialTags: EntityTag[]) {
  const [tags, setTags] = useState<EntityTag[]>(initialTags)
  const [selectedTagId, setSelectedTagId] = useState<string | null>(null)
  const [editingTagId, setEditingTagId] = useState<string | null>(null)

  const selectedTag = useMemo(
    () => tags.find((t) => t.tag_id === selectedTagId) ?? null,
    [tags, selectedTagId],
  )

  const pendingCount = useMemo(
    () => tags.filter((t) => t.decision === 'pending').length,
    [tags],
  )

  const selectTag = useCallback((tagId: string) => {
    setSelectedTagId(tagId)
  }, [])

  const updateTagDecision = useCallback(
    (tagId: string, decision: EntityTag['decision']) => {
      setTags((prev) =>
        prev.map((t) => (t.tag_id === tagId ? { ...t, decision } : t)),
      )
    },
    [],
  )

  const acceptTag = useCallback(
    (tagId: string) => updateTagDecision(tagId, 'accepted'),
    [updateTagDecision],
  )

  const rejectTag = useCallback(
    (tagId: string) => updateTagDecision(tagId, 'rejected'),
    [updateTagDecision],
  )

  const acceptAllValidated = useCallback(() => {
    setTags((prev) =>
      prev.map((t) =>
        t.decision === 'pending' && t.db_status === 'validated'
          ? { ...t, decision: 'accepted' }
          : t,
      ),
    )
  }, [])

  const startEditing = useCallback((tagId: string) => {
    setEditingTagId(tagId)
  }, [])

  const cancelEditing = useCallback(() => {
    setEditingTagId(null)
  }, [])

  const saveEdit = useCallback(
    (tagId: string, updates: Partial<EntityTag>) => {
      setTags((prev) =>
        prev.map((t) => (t.tag_id === tagId ? { ...t, ...updates } : t)),
      )
      setEditingTagId(null)
    },
    [],
  )

  const addManualTag = useCallback(() => {
    const newTag: EntityTag = {
      tag_id: generateManualTagId(),
      entity_name: '',
      entity_type: 'ATP:0000005',
      species: '',
      topic: '',
      db_status: 'not_found',
      db_entity_id: null,
      source: 'manual',
      decision: 'pending',
      evidence: null,
      notes: null,
    }
    setTags((prev) => [...prev, newTag])
    setEditingTagId(newTag.tag_id)
  }, [])

  return {
    tags,
    selectedTagId,
    selectedTag,
    editingTagId,
    pendingCount,
    selectTag,
    acceptTag,
    rejectTag,
    acceptAllValidated,
    startEditing,
    cancelEditing,
    saveEdit,
    addManualTag,
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/curation/entityTable/useEntityTagState.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/curation/entityTable/useEntityTagState.ts frontend/src/features/curation/entityTable/useEntityTagState.test.ts
git commit -m "feat(curation): add useEntityTagState hook for entity table state management"
```

---

## Task 4: Build EvidencePreviewPane Component

**Files:**
- Create: `frontend/src/features/curation/entityTable/EvidencePreviewPane.tsx`
- Test: `frontend/src/features/curation/entityTable/EvidencePreviewPane.test.tsx`

- [ ] **Step 1: Write the test**

```tsx
// frontend/src/features/curation/entityTable/EvidencePreviewPane.test.tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'
import theme from '@/theme'
import EvidencePreviewPane from './EvidencePreviewPane'
import type { EntityTag } from './types'

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ThemeProvider theme={theme}>{children}</ThemeProvider>
)

const makeTag = (overrides: Partial<EntityTag> = {}): EntityTag => ({
  tag_id: 'tag-1', entity_name: 'daf-2', entity_type: 'ATP:0000005',
  species: 'NCBITaxon:6239', topic: 'gene expression', db_status: 'validated',
  db_entity_id: 'WBGene00000898', source: 'ai', decision: 'pending',
  evidence: { sentence_text: 'The daf-2 receptor regulates lifespan.', page_number: 3, section_title: 'Results', chunk_ids: ['c1'] },
  notes: null,
  ...overrides,
})

describe('EvidencePreviewPane', () => {
  it('shows empty state when no tag is selected', () => {
    render(<EvidencePreviewPane tag={null} onShowInPdf={vi.fn()} />, { wrapper })
    expect(screen.getByText('Select a row to view evidence.')).toBeInTheDocument()
  })

  it('shows the sentence quote for a selected tag', () => {
    render(<EvidencePreviewPane tag={makeTag()} onShowInPdf={vi.fn()} />, { wrapper })
    expect(screen.getByText(/daf-2 receptor regulates lifespan/)).toBeInTheDocument()
  })

  it('shows page and section metadata', () => {
    render(<EvidencePreviewPane tag={makeTag()} onShowInPdf={vi.fn()} />, { wrapper })
    expect(screen.getByText(/Page 3/)).toBeInTheDocument()
    expect(screen.getByText(/Results/)).toBeInTheDocument()
  })

  it('shows db entity id when available', () => {
    render(<EvidencePreviewPane tag={makeTag()} onShowInPdf={vi.fn()} />, { wrapper })
    expect(screen.getByText(/WBGene00000898/)).toBeInTheDocument()
  })

  it('calls onShowInPdf when link is clicked', () => {
    const onShowInPdf = vi.fn()
    render(<EvidencePreviewPane tag={makeTag()} onShowInPdf={onShowInPdf} />, { wrapper })
    fireEvent.click(screen.getByText('Show in PDF'))
    expect(onShowInPdf).toHaveBeenCalledWith(makeTag())
  })

  it('shows manual tag message when evidence is null', () => {
    render(<EvidencePreviewPane tag={makeTag({ evidence: null, source: 'manual' })} onShowInPdf={vi.fn()} />, { wrapper })
    expect(screen.getByText(/No AI evidence/)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/curation/entityTable/EvidencePreviewPane.test.tsx`
Expected: FAIL

- [ ] **Step 3: Write the component**

```tsx
// frontend/src/features/curation/entityTable/EvidencePreviewPane.tsx
import { Box, Link, Typography } from '@mui/material'
import { alpha, useTheme } from '@mui/material/styles'
import type { EntityTag } from './types'

interface EvidencePreviewPaneProps {
  tag: EntityTag | null
  onShowInPdf: (tag: EntityTag) => void
}

export default function EvidencePreviewPane({ tag, onShowInPdf }: EvidencePreviewPaneProps) {
  const theme = useTheme()

  if (!tag) {
    return (
      <Box sx={{ p: 2, display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
        <Typography variant="body2" color="text.secondary">
          Select a row to view evidence.
        </Typography>
      </Box>
    )
  }

  if (!tag.evidence) {
    return (
      <Box sx={{ p: 2 }}>
        <Typography variant="overline" color="text.secondary">
          Evidence for <strong>{tag.entity_name}</strong>
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          No AI evidence — manually added.
        </Typography>
      </Box>
    )
  }

  return (
    <Box sx={{ p: 1.5, height: '100%', display: 'flex', flexDirection: 'column', overflow: 'auto' }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
        <Typography variant="overline" color="text.secondary" sx={{ letterSpacing: 0.5, fontSize: '0.65rem' }}>
          Evidence for <strong style={{ color: theme.palette.text.primary }}>{tag.entity_name}</strong>
        </Typography>
        <Link
          component="button"
          variant="caption"
          onClick={() => onShowInPdf(tag)}
          sx={{ fontSize: '0.7rem' }}
        >
          Show in PDF
        </Link>
      </Box>

      <Box
        sx={{
          backgroundColor: alpha(theme.palette.background.default, 0.5),
          borderLeft: `3px solid ${theme.palette.primary.main}`,
          borderRadius: '0 4px 4px 0',
          p: 1.5,
          mb: 1,
        }}
      >
        <Typography variant="body2" sx={{ lineHeight: 1.6, fontSize: '0.8rem' }}>
          &ldquo;{tag.evidence.sentence_text}&rdquo;
        </Typography>
      </Box>

      <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
        {tag.evidence.page_number != null && (
          <Typography variant="caption" color="text.secondary">Page {tag.evidence.page_number}</Typography>
        )}
        {tag.evidence.section_title && (
          <Typography variant="caption" color="text.secondary">Section: {tag.evidence.section_title}</Typography>
        )}
        <Typography variant="caption" color="text.secondary">
          {tag.source === 'ai' ? 'AI-extracted' : 'Manually added'}
        </Typography>
        {tag.db_entity_id && (
          <Typography variant="caption" sx={{ color: theme.palette.success.main }}>
            {tag.db_entity_id}
          </Typography>
        )}
      </Box>
    </Box>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/curation/entityTable/EvidencePreviewPane.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/curation/entityTable/EvidencePreviewPane.tsx frontend/src/features/curation/entityTable/EvidencePreviewPane.test.tsx
git commit -m "feat(curation): add EvidencePreviewPane with quote display and PDF link"
```

---

## Task 5: Build EntityTagRow Component

**Files:**
- Create: `frontend/src/features/curation/entityTable/EntityTagRow.tsx`
- Test: `frontend/src/features/curation/entityTable/EntityTagRow.test.tsx`

- [ ] **Step 1: Write the test**

```tsx
// frontend/src/features/curation/entityTable/EntityTagRow.test.tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'
import theme from '@/theme'
import EntityTagRow from './EntityTagRow'
import type { EntityTag } from './types'

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ThemeProvider theme={theme}>
    <table><tbody>{children}</tbody></table>
  </ThemeProvider>
)

const makeTag = (overrides: Partial<EntityTag> = {}): EntityTag => ({
  tag_id: 'tag-1', entity_name: 'daf-2', entity_type: 'ATP:0000005',
  species: 'NCBITaxon:6239', topic: 'gene expression', db_status: 'validated',
  db_entity_id: 'WBGene00000898', source: 'ai', decision: 'pending',
  evidence: { sentence_text: 'The daf-2 receptor regulates lifespan.', page_number: 3, section_title: 'Results', chunk_ids: ['c1'] },
  notes: null,
  ...overrides,
})

const defaultProps = {
  tag: makeTag(),
  isSelected: false,
  onSelect: vi.fn(),
  onAccept: vi.fn(),
  onReject: vi.fn(),
  onEdit: vi.fn(),
}

describe('EntityTagRow', () => {
  it('renders entity name, type, species, topic', () => {
    render(<EntityTagRow {...defaultProps} />, { wrapper })
    expect(screen.getByText('daf-2')).toBeInTheDocument()
    expect(screen.getByText('gene')).toBeInTheDocument()
    expect(screen.getByText('gene expression')).toBeInTheDocument()
  })

  it('shows Accept and Reject buttons for pending tags', () => {
    render(<EntityTagRow {...defaultProps} />, { wrapper })
    expect(screen.getByRole('button', { name: 'Accept' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument()
  })

  it('shows Accepted badge for accepted tags', () => {
    render(<EntityTagRow {...defaultProps} tag={makeTag({ decision: 'accepted' })} />, { wrapper })
    expect(screen.getByText('Accepted')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Accept' })).not.toBeInTheDocument()
  })

  it('shows Rejected badge for rejected tags', () => {
    render(<EntityTagRow {...defaultProps} tag={makeTag({ decision: 'rejected' })} />, { wrapper })
    expect(screen.getByText('Rejected')).toBeInTheDocument()
  })

  it('shows validated badge for db_status validated', () => {
    render(<EntityTagRow {...defaultProps} />, { wrapper })
    expect(screen.getByText('validated')).toBeInTheDocument()
  })

  it('calls onSelect when row is clicked', () => {
    const onSelect = vi.fn()
    render(<EntityTagRow {...defaultProps} onSelect={onSelect} />, { wrapper })
    fireEvent.click(screen.getByText('daf-2'))
    expect(onSelect).toHaveBeenCalledWith('tag-1')
  })

  it('calls onAccept when Accept button is clicked', () => {
    const onAccept = vi.fn()
    render(<EntityTagRow {...defaultProps} onAccept={onAccept} />, { wrapper })
    fireEvent.click(screen.getByRole('button', { name: 'Accept' }))
    expect(onAccept).toHaveBeenCalledWith('tag-1')
  })

  it('shows edit icon that calls onEdit', () => {
    const onEdit = vi.fn()
    render(<EntityTagRow {...defaultProps} onEdit={onEdit} />, { wrapper })
    fireEvent.click(screen.getByLabelText('Edit'))
    expect(onEdit).toHaveBeenCalledWith('tag-1')
  })

  it('shows source label', () => {
    render(<EntityTagRow {...defaultProps} />, { wrapper })
    expect(screen.getByText('AI')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/curation/entityTable/EntityTagRow.test.tsx`
Expected: FAIL

- [ ] **Step 3: Write the component**

```tsx
// frontend/src/features/curation/entityTable/EntityTagRow.tsx
import { Button, Chip, IconButton, TableCell, TableRow, Typography } from '@mui/material'
import EditIcon from '@mui/icons-material/Edit'
import { alpha, useTheme } from '@mui/material/styles'
import type { EntityTag } from './types'
import { ENTITY_TYPE_LABELS, type EntityTypeCode } from './types'

interface EntityTagRowProps {
  tag: EntityTag
  isSelected: boolean
  onSelect: (tagId: string) => void
  onAccept: (tagId: string) => void
  onReject: (tagId: string) => void
  onEdit: (tagId: string) => void
}

const DB_STATUS_COLOR: Record<string, 'success' | 'warning' | 'error'> = {
  validated: 'success',
  ambiguous: 'warning',
  not_found: 'error',
}

export default function EntityTagRow({
  tag,
  isSelected,
  onSelect,
  onAccept,
  onReject,
  onEdit,
}: EntityTagRowProps) {
  const theme = useTheme()

  const rowSx = {
    cursor: 'pointer',
    ...(isSelected && {
      borderLeft: `3px solid ${theme.palette.primary.main}`,
    }),
    ...(tag.decision === 'accepted' && {
      backgroundColor: alpha(theme.palette.success.main, 0.06),
    }),
    ...(tag.decision === 'rejected' && {
      opacity: 0.5,
    }),
  }

  const cellSx = { py: 0.75, px: 1, fontSize: '0.75rem' }

  const typeLabel = ENTITY_TYPE_LABELS[tag.entity_type as EntityTypeCode] ?? tag.entity_type

  return (
    <TableRow hover onClick={() => onSelect(tag.tag_id)} selected={isSelected} sx={rowSx}>
      <TableCell sx={{ ...cellSx, fontWeight: 600 }}>{tag.entity_name}</TableCell>
      <TableCell sx={cellSx}>{typeLabel}</TableCell>
      <TableCell sx={{ ...cellSx, fontStyle: 'italic' }}>{tag.species}</TableCell>
      <TableCell sx={cellSx}>{tag.topic}</TableCell>
      <TableCell sx={cellSx}>
        <Chip
          label={tag.db_status}
          size="small"
          color={DB_STATUS_COLOR[tag.db_status] ?? 'default'}
          variant="outlined"
          sx={{ fontSize: '0.65rem', height: 20 }}
        />
      </TableCell>
      <TableCell sx={{ ...cellSx, color: 'text.secondary', fontSize: '0.65rem' }}>
        {tag.source === 'ai' ? 'AI' : 'Manual'}
      </TableCell>
      <TableCell sx={cellSx} onClick={(e) => e.stopPropagation()}>
        {tag.decision === 'pending' ? (
          <>
            <Button
              size="small"
              variant="outlined"
              color="success"
              onClick={() => onAccept(tag.tag_id)}
              sx={{ fontSize: '0.65rem', mr: 0.5, minWidth: 0, px: 1, py: 0.25 }}
            >
              Accept
            </Button>
            <Button
              size="small"
              variant="outlined"
              color="error"
              onClick={() => onReject(tag.tag_id)}
              sx={{ fontSize: '0.65rem', mr: 0.5, minWidth: 0, px: 1, py: 0.25 }}
            >
              Reject
            </Button>
          </>
        ) : (
          <Typography
            variant="caption"
            sx={{
              color: tag.decision === 'accepted' ? 'success.main' : 'text.secondary',
              fontWeight: 500,
              fontSize: '0.65rem',
            }}
          >
            {tag.decision === 'accepted' ? 'Accepted' : 'Rejected'}
          </Typography>
        )}
        <IconButton
          size="small"
          onClick={() => onEdit(tag.tag_id)}
          aria-label="Edit"
          sx={{ ml: 0.5, p: 0.25 }}
        >
          <EditIcon sx={{ fontSize: 14 }} />
        </IconButton>
      </TableCell>
    </TableRow>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/curation/entityTable/EntityTagRow.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/curation/entityTable/EntityTagRow.tsx frontend/src/features/curation/entityTable/EntityTagRow.test.tsx
git commit -m "feat(curation): add EntityTagRow with accept/reject/edit actions"
```

---

## Task 6: Build InlineEditRow Component

**Files:**
- Create: `frontend/src/features/curation/entityTable/InlineEditRow.tsx`
- Test: `frontend/src/features/curation/entityTable/InlineEditRow.test.tsx`

- [ ] **Step 1: Write the test**

```tsx
// frontend/src/features/curation/entityTable/InlineEditRow.test.tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'
import theme from '@/theme'
import InlineEditRow from './InlineEditRow'
import type { EntityTag } from './types'

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ThemeProvider theme={theme}>
    <table><tbody>{children}</tbody></table>
  </ThemeProvider>
)

const makeTag = (): EntityTag => ({
  tag_id: 'tag-1', entity_name: 'daf-2', entity_type: 'ATP:0000005',
  species: 'NCBITaxon:6239', topic: 'gene expression', db_status: 'validated',
  db_entity_id: 'WBGene00000898', source: 'ai', decision: 'pending',
  evidence: { sentence_text: 'daf-2 regulates lifespan.', page_number: 3, section_title: 'Results', chunk_ids: ['c1'] },
  notes: null,
})

describe('InlineEditRow', () => {
  it('renders input fields pre-filled with tag values', () => {
    render(<InlineEditRow tag={makeTag()} onSave={vi.fn()} onCancel={vi.fn()} />, { wrapper })
    const entityInput = screen.getByDisplayValue('daf-2')
    expect(entityInput).toBeInTheDocument()
    expect(screen.getByDisplayValue('gene expression')).toBeInTheDocument()
  })

  it('renders entity type as a select dropdown', () => {
    render(<InlineEditRow tag={makeTag()} onSave={vi.fn()} onCancel={vi.fn()} />, { wrapper })
    expect(screen.getByRole('combobox', { name: 'Entity type' })).toBeInTheDocument()
  })

  it('calls onSave with updated values when Save is clicked', () => {
    const onSave = vi.fn()
    render(<InlineEditRow tag={makeTag()} onSave={onSave} onCancel={vi.fn()} />, { wrapper })
    const entityInput = screen.getByDisplayValue('daf-2')
    fireEvent.change(entityInput, { target: { value: 'daf-16' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSave).toHaveBeenCalledWith('tag-1', expect.objectContaining({ entity_name: 'daf-16' }))
  })

  it('calls onCancel when Cancel is clicked', () => {
    const onCancel = vi.fn()
    render(<InlineEditRow tag={makeTag()} onSave={vi.fn()} onCancel={onCancel} />, { wrapper })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/curation/entityTable/InlineEditRow.test.tsx`
Expected: FAIL

- [ ] **Step 3: Write the component**

```tsx
// frontend/src/features/curation/entityTable/InlineEditRow.tsx
import { useState } from 'react'
import {
  Button,
  MenuItem,
  Select,
  TableCell,
  TableRow,
  TextField,
} from '@mui/material'
import type { EntityTag } from './types'
import { ENTITY_TYPE_CODES, ENTITY_TYPE_LABELS, type EntityTypeCode } from './types'

interface InlineEditRowProps {
  tag: EntityTag
  onSave: (tagId: string, updates: Partial<EntityTag>) => void
  onCancel: () => void
}

export default function InlineEditRow({ tag, onSave, onCancel }: InlineEditRowProps) {
  const [entityName, setEntityName] = useState(tag.entity_name)
  const [entityType, setEntityType] = useState(tag.entity_type)
  const [species, setSpecies] = useState(tag.species)
  const [topic, setTopic] = useState(tag.topic)

  const cellSx = { py: 0.5, px: 0.75 }
  const inputSx = { fontSize: '0.75rem' }

  const handleSave = () => {
    onSave(tag.tag_id, {
      entity_name: entityName,
      entity_type: entityType,
      species,
      topic,
    })
  }

  return (
    <TableRow sx={{ backgroundColor: 'action.hover' }}>
      <TableCell sx={cellSx}>
        <TextField
          size="small"
          value={entityName}
          onChange={(e) => setEntityName(e.target.value)}
          inputProps={{ 'aria-label': 'Entity name', sx: inputSx }}
          fullWidth
        />
      </TableCell>
      <TableCell sx={cellSx}>
        <Select
          size="small"
          value={entityType}
          onChange={(e) => setEntityType(e.target.value)}
          inputProps={{ 'aria-label': 'Entity type' }}
          sx={{ fontSize: '0.75rem' }}
          fullWidth
        >
          {ENTITY_TYPE_CODES.map((code) => (
            <MenuItem key={code} value={code} sx={{ fontSize: '0.75rem' }}>
              {ENTITY_TYPE_LABELS[code]}
            </MenuItem>
          ))}
        </Select>
      </TableCell>
      <TableCell sx={cellSx}>
        <TextField
          size="small"
          value={species}
          onChange={(e) => setSpecies(e.target.value)}
          inputProps={{ 'aria-label': 'Species', sx: inputSx }}
          fullWidth
        />
      </TableCell>
      <TableCell sx={cellSx}>
        <TextField
          size="small"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          inputProps={{ 'aria-label': 'Topic', sx: inputSx }}
          fullWidth
        />
      </TableCell>
      <TableCell sx={cellSx} />
      <TableCell sx={cellSx} />
      <TableCell sx={cellSx}>
        <Button size="small" variant="contained" onClick={handleSave} sx={{ fontSize: '0.65rem', mr: 0.5, minWidth: 0, px: 1, py: 0.25 }}>
          Save
        </Button>
        <Button size="small" variant="text" onClick={onCancel} sx={{ fontSize: '0.65rem', minWidth: 0, px: 1, py: 0.25 }}>
          Cancel
        </Button>
      </TableCell>
    </TableRow>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/curation/entityTable/InlineEditRow.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/curation/entityTable/InlineEditRow.tsx frontend/src/features/curation/entityTable/InlineEditRow.test.tsx
git commit -m "feat(curation): add InlineEditRow with entity type dropdown and save/cancel"
```

---

## Task 7: Build EntityTagToolbar Component

**Files:**
- Create: `frontend/src/features/curation/entityTable/EntityTagToolbar.tsx`
- Test: `frontend/src/features/curation/entityTable/EntityTagToolbar.test.tsx`

- [ ] **Step 1: Write the test**

```tsx
// frontend/src/features/curation/entityTable/EntityTagToolbar.test.tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'
import theme from '@/theme'
import EntityTagToolbar from './EntityTagToolbar'

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ThemeProvider theme={theme}>{children}</ThemeProvider>
)

describe('EntityTagToolbar', () => {
  it('shows total and pending counts', () => {
    render(
      <EntityTagToolbar totalCount={5} pendingCount={2} onAcceptAllValidated={vi.fn()} onAddEntity={vi.fn()} />,
      { wrapper },
    )
    expect(screen.getByText(/5 entities/)).toBeInTheDocument()
    expect(screen.getByText(/2 pending/)).toBeInTheDocument()
  })

  it('calls onAcceptAllValidated when button is clicked', () => {
    const onAcceptAllValidated = vi.fn()
    render(
      <EntityTagToolbar totalCount={5} pendingCount={2} onAcceptAllValidated={onAcceptAllValidated} onAddEntity={vi.fn()} />,
      { wrapper },
    )
    fireEvent.click(screen.getByRole('button', { name: /Accept All Validated/ }))
    expect(onAcceptAllValidated).toHaveBeenCalled()
  })

  it('calls onAddEntity when button is clicked', () => {
    const onAddEntity = vi.fn()
    render(
      <EntityTagToolbar totalCount={5} pendingCount={2} onAcceptAllValidated={vi.fn()} onAddEntity={onAddEntity} />,
      { wrapper },
    )
    fireEvent.click(screen.getByRole('button', { name: /Add Entity/ }))
    expect(onAddEntity).toHaveBeenCalled()
  })

  it('disables accept all when no pending tags', () => {
    render(
      <EntityTagToolbar totalCount={3} pendingCount={0} onAcceptAllValidated={vi.fn()} onAddEntity={vi.fn()} />,
      { wrapper },
    )
    expect(screen.getByRole('button', { name: /Accept All Validated/ })).toBeDisabled()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/curation/entityTable/EntityTagToolbar.test.tsx`
Expected: FAIL

- [ ] **Step 3: Write the component**

```tsx
// frontend/src/features/curation/entityTable/EntityTagToolbar.tsx
import { Box, Button, Chip, Typography } from '@mui/material'

interface EntityTagToolbarProps {
  totalCount: number
  pendingCount: number
  onAcceptAllValidated: () => void
  onAddEntity: () => void
}

export default function EntityTagToolbar({
  totalCount,
  pendingCount,
  onAcceptAllValidated,
  onAddEntity,
}: EntityTagToolbarProps) {
  return (
    <Box
      sx={{
        px: 1.5,
        py: 0.75,
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        borderBottom: 1,
        borderColor: 'divider',
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
          Entity Tags
        </Typography>
        <Chip
          label={`${totalCount} entities \u00b7 ${pendingCount} pending`}
          size="small"
          color="primary"
          variant="outlined"
          sx={{ fontSize: '0.65rem', height: 22 }}
        />
      </Box>
      <Box sx={{ display: 'flex', gap: 0.75 }}>
        <Button
          size="small"
          variant="outlined"
          color="success"
          disabled={pendingCount === 0}
          onClick={onAcceptAllValidated}
          sx={{ fontSize: '0.65rem', textTransform: 'none' }}
        >
          Accept All Validated
        </Button>
        <Button
          size="small"
          variant="outlined"
          onClick={onAddEntity}
          sx={{ fontSize: '0.65rem', textTransform: 'none' }}
        >
          + Add Entity
        </Button>
      </Box>
    </Box>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/curation/entityTable/EntityTagToolbar.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/curation/entityTable/EntityTagToolbar.tsx frontend/src/features/curation/entityTable/EntityTagToolbar.test.tsx
git commit -m "feat(curation): add EntityTagToolbar with batch accept and add entity buttons"
```

---

## Task 8: Build EntityTagTable (Composed Component)

**Files:**
- Create: `frontend/src/features/curation/entityTable/EntityTagTable.tsx`
- Create: `frontend/src/features/curation/entityTable/index.ts`
- Test: `frontend/src/features/curation/entityTable/EntityTagTable.test.tsx`

- [ ] **Step 1: Write the integration test**

```tsx
// frontend/src/features/curation/entityTable/EntityTagTable.test.tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'
import theme from '@/theme'
import EntityTagTable from './EntityTagTable'
import type { EntityTag } from './types'

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ThemeProvider theme={theme}>{children}</ThemeProvider>
)

const makeTags = (): EntityTag[] => [
  {
    tag_id: 'tag-1', entity_name: 'daf-2', entity_type: 'ATP:0000005',
    species: 'NCBITaxon:6239', topic: 'gene expression', db_status: 'validated',
    db_entity_id: 'WBGene00000898', source: 'ai', decision: 'pending',
    evidence: { sentence_text: 'The daf-2 receptor regulates lifespan.', page_number: 3, section_title: 'Results', chunk_ids: ['c1'] },
    notes: null,
  },
  {
    tag_id: 'tag-2', entity_name: 'ins-1', entity_type: 'ATP:0000005',
    species: 'NCBITaxon:6239', topic: 'gene expression', db_status: 'ambiguous',
    db_entity_id: null, source: 'ai', decision: 'pending',
    evidence: { sentence_text: 'ins-1 is an insulin peptide.', page_number: 5, section_title: 'Discussion', chunk_ids: ['c2'] },
    notes: null,
  },
]

describe('EntityTagTable', () => {
  it('renders toolbar with counts', () => {
    render(<EntityTagTable tags={makeTags()} />, { wrapper })
    expect(screen.getByText(/2 entities/)).toBeInTheDocument()
    expect(screen.getByText(/2 pending/)).toBeInTheDocument()
  })

  it('renders all entity rows', () => {
    render(<EntityTagTable tags={makeTags()} />, { wrapper })
    expect(screen.getByText('daf-2')).toBeInTheDocument()
    expect(screen.getByText('ins-1')).toBeInTheDocument()
  })

  it('shows evidence pane empty state initially', () => {
    render(<EntityTagTable tags={makeTags()} />, { wrapper })
    expect(screen.getByText('Select a row to view evidence.')).toBeInTheDocument()
  })

  it('shows evidence when a row is clicked', () => {
    render(<EntityTagTable tags={makeTags()} />, { wrapper })
    fireEvent.click(screen.getByText('daf-2'))
    expect(screen.getByText(/daf-2 receptor regulates lifespan/)).toBeInTheDocument()
  })

  it('dispatches PDF navigation event when Show in PDF is clicked', () => {
    const listener = vi.fn()
    window.addEventListener('pdf-viewer-navigate-evidence', listener)
    render(<EntityTagTable tags={makeTags()} />, { wrapper })
    fireEvent.click(screen.getByText('daf-2'))
    fireEvent.click(screen.getByText('Show in PDF'))
    expect(listener).toHaveBeenCalled()
    window.removeEventListener('pdf-viewer-navigate-evidence', listener)
  })

  it('accepts a tag when Accept is clicked', () => {
    render(<EntityTagTable tags={makeTags()} />, { wrapper })
    const acceptButtons = screen.getAllByRole('button', { name: 'Accept' })
    fireEvent.click(acceptButtons[0])
    expect(screen.getByText('Accepted')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/curation/entityTable/EntityTagTable.test.tsx`
Expected: FAIL

- [ ] **Step 3: Write the composed table component**

```tsx
// frontend/src/features/curation/entityTable/EntityTagTable.tsx
import { useCallback } from 'react'
import { Box, Table, TableBody, TableCell, TableContainer, TableHead, TableRow } from '@mui/material'
import { dispatchPDFViewerNavigateEvidence } from '@/components/pdfViewer/pdfEvents'
import { useEntityTagState } from './useEntityTagState'
import { buildEntityTagNavigationCommand } from './entityTagNavigation'
import EntityTagToolbar from './EntityTagToolbar'
import EntityTagRow from './EntityTagRow'
import InlineEditRow from './InlineEditRow'
import EvidencePreviewPane from './EvidencePreviewPane'
import type { EntityTag } from './types'

interface EntityTagTableProps {
  tags: EntityTag[]
}

const HEADER_CELLS = ['Entity', 'Type', 'Species', 'Topic', 'DB Status', 'Src', 'Decision']

export default function EntityTagTable({ tags: initialTags }: EntityTagTableProps) {
  const state = useEntityTagState(initialTags)

  const handleSelect = useCallback(
    (tagId: string) => {
      state.selectTag(tagId)
      const tag = state.tags.find((t) => t.tag_id === tagId)
      if (tag) {
        const command = buildEntityTagNavigationCommand(tag)
        if (command) dispatchPDFViewerNavigateEvidence(command)
      }
    },
    [state],
  )

  const handleShowInPdf = useCallback(
    (tag: EntityTag) => {
      const command = buildEntityTagNavigationCommand(tag)
      if (command) dispatchPDFViewerNavigateEvidence(command)
    },
    [],
  )

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <EntityTagToolbar
        totalCount={state.tags.length}
        pendingCount={state.pendingCount}
        onAcceptAllValidated={state.acceptAllValidated}
        onAddEntity={state.addManualTag}
      />

      <TableContainer sx={{ flex: 1, overflow: 'auto' }}>
        <Table size="small" stickyHeader>
          <TableHead>
            <TableRow>
              {HEADER_CELLS.map((label) => (
                <TableCell
                  key={label}
                  sx={{ fontSize: '0.7rem', fontWeight: 600, py: 0.75, px: 1 }}
                >
                  {label}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {state.tags.map((tag) =>
              state.editingTagId === tag.tag_id ? (
                <InlineEditRow
                  key={tag.tag_id}
                  tag={tag}
                  onSave={state.saveEdit}
                  onCancel={state.cancelEditing}
                />
              ) : (
                <EntityTagRow
                  key={tag.tag_id}
                  tag={tag}
                  isSelected={state.selectedTagId === tag.tag_id}
                  onSelect={handleSelect}
                  onAccept={state.acceptTag}
                  onReject={state.rejectTag}
                  onEdit={state.startEditing}
                />
              ),
            )}
          </TableBody>
        </Table>
      </TableContainer>

      <Box sx={{ flex: '0 0 auto', minHeight: 120, borderTop: 1, borderColor: 'divider' }}>
        <EvidencePreviewPane tag={state.selectedTag} onShowInPdf={handleShowInPdf} />
      </Box>
    </Box>
  )
}
```

- [ ] **Step 4: Write the barrel export**

```typescript
// frontend/src/features/curation/entityTable/index.ts
export { default as EntityTagTable } from './EntityTagTable'
export type { EntityTag, EntityTagEvidence, EntityTagDecision, EntityTagSource, DbValidationStatus } from './types'
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/curation/entityTable/EntityTagTable.test.tsx`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/curation/entityTable/EntityTagTable.tsx frontend/src/features/curation/entityTable/EntityTagTable.test.tsx frontend/src/features/curation/entityTable/index.ts
git commit -m "feat(curation): compose EntityTagTable with toolbar, rows, and evidence preview"
```

---

## Task 9: Update WorkspaceShell Layout

**Files:**
- Modify: `frontend/src/features/curation/workspace/WorkspaceShell.tsx`
- Modify: `frontend/src/features/curation/workspace/WorkspaceShell.test.tsx`

- [ ] **Step 1: Update the test to expect the new two-panel layout**

Replace the contents of `WorkspaceShell.test.tsx`:

```tsx
// frontend/src/features/curation/workspace/WorkspaceShell.test.tsx
import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it } from 'vitest'

import theme from '@/theme'
import WorkspaceShell from './WorkspaceShell'

describe('WorkspaceShell', () => {
  it('renders the two-panel desktop layout with PDF and entity table', () => {
    render(
      <ThemeProvider theme={theme}>
        <WorkspaceShell
          headerSlot={<div>Header slot</div>}
          pdfSlot={<div>PDF slot</div>}
          entityTableSlot={<div>Entity table slot</div>}
        />
      </ThemeProvider>,
    )

    expect(screen.getByText('Header slot')).toBeInTheDocument()
    expect(screen.getByText('PDF slot')).toBeInTheDocument()
    expect(screen.getByText('Entity table slot')).toBeInTheDocument()

    expect(screen.getByTestId('workspace-shell-pdf-panel')).toBeInTheDocument()
    expect(screen.getByTestId('workspace-shell-entity-table-panel')).toBeInTheDocument()

    expect(screen.getByTestId('workspace-shell-handle-pdf-table')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/curation/workspace/WorkspaceShell.test.tsx`
Expected: FAIL — old slot props no longer match

- [ ] **Step 3: Update WorkspaceShell to the new two-panel layout**

Replace the `WorkspaceShellProps` interface and the `WorkspaceShell` component body. Keep all styled components (`ShellRoot`, `PanelSection`, `PanelSurface`, `SlotFrame`, `DesktopPanels`, `MobilePanels`, `ToolbarSurface`, `StyledResizeHandle`, `WorkspaceResizeHandle`, `WorkspacePane`) unchanged. Replace the interface and default export:

```tsx
export interface WorkspaceShellProps {
  headerSlot?: ReactNode
  pdfSlot: ReactNode
  entityTableSlot: ReactNode
  outerAutoSaveId?: string
}

const DEFAULT_OUTER_AUTO_SAVE_ID = 'curation-workspace-shell-panels'

// ... keep all styled components and helper components as-is ...

export default function WorkspaceShell({
  headerSlot,
  pdfSlot,
  entityTableSlot,
  outerAutoSaveId = DEFAULT_OUTER_AUTO_SAVE_ID,
}: WorkspaceShellProps) {
  const theme = useTheme()
  const isCompactLayout = useMediaQuery(theme.breakpoints.down('md'))

  return (
    <ShellRoot data-testid="workspace-shell">
      {headerSlot ? (
        <Box data-testid="workspace-shell-header">{headerSlot}</Box>
      ) : null}

      {isCompactLayout ? (
        <MobilePanels spacing={1.5}>
          <WorkspacePane label="PDF panel" testId="workspace-shell-pdf-panel">
            {pdfSlot}
          </WorkspacePane>
          <WorkspacePane label="Entity table panel" testId="workspace-shell-entity-table-panel">
            {entityTableSlot}
          </WorkspacePane>
        </MobilePanels>
      ) : (
        <DesktopPanels>
          <PanelGroup autoSaveId={outerAutoSaveId} direction="horizontal">
            <Panel defaultSize={45} minSize={28} order={1}>
              <PanelSection>
                <WorkspacePane label="PDF panel" testId="workspace-shell-pdf-panel">
                  {pdfSlot}
                </WorkspacePane>
              </PanelSection>
            </Panel>

            <WorkspaceResizeHandle
              groupDirection="horizontal"
              label="Resize PDF and entity table panels"
              testId="workspace-shell-handle-pdf-table"
            />

            <Panel defaultSize={55} minSize={30} order={2}>
              <PanelSection>
                <WorkspacePane
                  label="Entity table panel"
                  testId="workspace-shell-entity-table-panel"
                >
                  {entityTableSlot}
                </WorkspacePane>
              </PanelSection>
            </Panel>
          </PanelGroup>
        </DesktopPanels>
      )}
    </ShellRoot>
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/curation/workspace/WorkspaceShell.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/curation/workspace/WorkspaceShell.tsx frontend/src/features/curation/workspace/WorkspaceShell.test.tsx
git commit -m "refactor(curation): simplify WorkspaceShell to two-panel PDF + entity table layout"
```

---

## Task 10: Integrate EntityTagTable into CurationWorkspacePage

**Files:**
- Modify: `frontend/src/pages/CurationWorkspacePage.tsx`

- [ ] **Step 1: Read the current CurationWorkspacePage**

Run: `cat frontend/src/pages/CurationWorkspacePage.tsx`

Understand how it currently passes slots to WorkspaceShell. The old slots were: `pdfSlot`, `queueSlot`, `toolbarSlot`, `editorSlot`, `evidenceSlot`. We need to replace all right-panel slots with a single `entityTableSlot`.

- [ ] **Step 2: Update the page to use EntityTagTable**

Replace the old slot wiring. The PDF slot stays unchanged. Remove imports for `CandidateQueue`, `CuratorDecisionToolbar`, `AnnotationEditor`, `EvidencePanel`. Add import for `EntityTagTable`. Pass a single `entityTableSlot` to `WorkspaceShell`:

```tsx
import { EntityTagTable } from '@/features/curation/entityTable'

// In the render, replace the old slots:
<WorkspaceShell
  headerSlot={<WorkspaceHeader /* existing props */ />}
  pdfSlot={<PdfViewer /* existing props */ />}
  entityTableSlot={<EntityTagTable tags={entityTags} />}
/>
```

Where `entityTags` comes from the workspace context/API.

Superseded note:
The initial implementation used a temporary page-level candidate-to-entity-tag bridge. That transition layer has been removed. The backend workspace payload now owns native `entity_tags`, and the page should consume `workspace.entity_tags` directly instead of rebuilding tags from `workspace.candidates`.

- [ ] **Step 3: Run the full test suite to check for breakage**

Run: `cd frontend && npx vitest run`
Expected: All new tests pass. Some existing tests for removed components (CandidateQueue, AnnotationEditor, CuratorDecisionToolbar) may need to be updated or removed if they test integration points that no longer exist in CurationWorkspacePage.

- [ ] **Step 4: Fix any failing tests**

Remove or update tests that reference removed slots (`queueSlot`, `toolbarSlot`, `editorSlot`, `evidenceSlot`) in `CurationWorkspacePage.test.tsx`. Keep tests for components that still exist independently.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/CurationWorkspacePage.tsx frontend/src/pages/CurationWorkspacePage.test.tsx
git commit -m "feat(curation): integrate EntityTagTable into workspace page, replace old panel layout"
```

---

## Self-Review

**Spec coverage check:**
- Two-panel layout (PDF + right): Task 9
- Entity tag table with 7 columns: Task 5 (EntityTagRow) + Task 8 (EntityTagTable)
- Row states (pending/accepted/rejected): Task 5
- Click-to-select with PDF highlighting: Task 2 (navigation) + Task 8 (handleSelect)
- Click-only, no hover: Task 2 (mode is always 'select')
- Inline editing: Task 6
- Manual addition: Task 3 (addManualTag) + Task 7 (+ Add Entity button)
- Batch accept all validated: Task 3 + Task 7
- Evidence preview pane: Task 4
- "Show in PDF" (not "Jump to page"): Task 4
- Dark theme: No changes needed, all components use MUI theme tokens
- Components replaced: Task 9 (WorkspaceShell) + Task 10 (CurationWorkspacePage)
- Reuse existing PDF event system: Task 2 + Task 8

**Placeholder scan:** All tasks contain complete code. No TBDs or TODOs.

**Type consistency:** `EntityTag` is defined in Task 1 and used consistently through Tasks 2-10. `buildEntityTagNavigationCommand` returns `EvidenceNavigationCommand | null` in Task 2 and is null-checked in Task 8. `useEntityTagState` return shape in Task 3 matches usage in Task 8.
