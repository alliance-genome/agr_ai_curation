import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'
import type { CurationEvidenceRecord } from '@/features/curation/types'
import theme from '@/theme'
import EntityTagTable from './EntityTagTable'
import type { EntityTag } from './types'

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ThemeProvider theme={theme}>{children}</ThemeProvider>
)

const makeTags = (): EntityTag[] => [
  {
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
      chunk_ids: ['c1'],
    },
    notes: null,
  },
  {
    tag_id: 'tag-2',
    entity_name: 'ins-1',
    entity_type: 'ATP:0000005',
    species: 'NCBITaxon:6239',
    topic: 'gene expression',
    db_status: 'ambiguous',
    db_entity_id: null,
    source: 'ai',
    decision: 'pending',
    evidence: {
      sentence_text: 'ins-1 is an insulin peptide.',
      page_number: 5,
      section_title: 'Discussion',
      chunk_ids: ['c2'],
    },
    notes: null,
  },
]

const makeEvidenceRecordsByTagId = (): Record<string, CurationEvidenceRecord[]> => ({
  'tag-1': [
    {
      anchor_id: 'anchor-1',
      candidate_id: 'tag-1',
      source: 'extracted',
      field_keys: ['gene_symbol'],
      field_group_keys: ['primary'],
      is_primary: true,
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'exact_quote',
        supports_decision: 'supports',
        sentence_text: 'The daf-2 receptor regulates lifespan.',
        snippet_text: 'The daf-2 receptor regulates lifespan.',
        viewer_search_text: 'The daf-2 receptor regulates lifespan.',
        viewer_highlightable: true,
        page_number: 3,
        section_title: 'Results',
        chunk_ids: ['c1'],
      },
      created_at: '2026-03-31T00:00:00Z',
      updated_at: '2026-03-31T00:00:00Z',
      warnings: [],
    },
    {
      anchor_id: 'anchor-2',
      candidate_id: 'tag-1',
      source: 'extracted',
      field_keys: ['gene_symbol'],
      field_group_keys: ['primary'],
      is_primary: false,
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'exact_quote',
        supports_decision: 'supports',
        sentence_text: 'A second daf-2 evidence sentence.',
        snippet_text: 'A second daf-2 evidence sentence.',
        viewer_search_text: 'A second daf-2 evidence sentence.',
        viewer_highlightable: true,
        page_number: 4,
        section_title: 'Discussion',
        chunk_ids: ['c1b'],
      },
      created_at: '2026-03-31T00:00:01Z',
      updated_at: '2026-03-31T00:00:01Z',
      warnings: [],
    },
  ],
  'tag-2': [
    {
      anchor_id: 'anchor-3',
      candidate_id: 'tag-2',
      source: 'extracted',
      field_keys: ['gene_symbol'],
      field_group_keys: ['primary'],
      is_primary: true,
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'exact_quote',
        supports_decision: 'supports',
        sentence_text: 'ins-1 is an insulin peptide.',
        snippet_text: 'ins-1 is an insulin peptide.',
        viewer_search_text: 'ins-1 is an insulin peptide.',
        viewer_highlightable: true,
        page_number: 5,
        section_title: 'Discussion',
        chunk_ids: ['c2'],
      },
      created_at: '2026-03-31T00:00:00Z',
      updated_at: '2026-03-31T00:00:00Z',
      warnings: [],
    },
  ],
})

function ControlledTable({
  onAcceptTag = vi.fn(),
  onRejectTag = vi.fn(),
  onSaveTag = vi.fn(),
  onCreateManualTag = vi.fn(async () => 'manual-1'),
  candidateEvidenceByTagId = makeEvidenceRecordsByTagId(),
}: {
  onAcceptTag?: (tagId: string) => Promise<void> | void
  onRejectTag?: (tagId: string) => Promise<void> | void
  onSaveTag?: (tagId: string, updates: Partial<EntityTag>) => Promise<void> | void
  onCreateManualTag?: (tag: EntityTag) => Promise<string> | string
  candidateEvidenceByTagId?: Record<string, CurationEvidenceRecord[]>
}) {
  const [selectedTagId, setSelectedTagId] = useState<string | null>(null)
  const [tags, setTags] = useState(makeTags())

  return (
    <EntityTagTable
      tags={tags}
      candidateEvidenceByTagId={candidateEvidenceByTagId}
      selectedTagId={selectedTagId}
      onSelectTag={setSelectedTagId}
      onAcceptTag={async (tagId) => {
        await onAcceptTag(tagId)
        setTags((currentTags) =>
          currentTags.map((tag) => (
            tag.tag_id === tagId ? { ...tag, decision: 'accepted' } : tag
          )),
        )
      }}
      onRejectTag={async (tagId) => {
        await onRejectTag(tagId)
        setTags((currentTags) =>
          currentTags.map((tag) => (
            tag.tag_id === tagId ? { ...tag, decision: 'rejected' } : tag
          )),
        )
      }}
      onAcceptAllValidated={async (tagIds) => {
        setTags((currentTags) =>
          currentTags.map((tag) => (
            tagIds.includes(tag.tag_id) ? { ...tag, decision: 'accepted' } : tag
          )),
        )
      }}
      onSaveTag={onSaveTag}
      onCreateManualTag={onCreateManualTag}
    />
  )
}

describe('EntityTagTable', () => {
  it('renders toolbar with counts', () => {
    render(<ControlledTable />, { wrapper })

    expect(screen.getByText(/2 entities/)).toBeInTheDocument()
    expect(screen.getByText(/2 pending/)).toBeInTheDocument()
  })

  it('shows evidence pane empty state initially', () => {
    render(<ControlledTable />, { wrapper })

    expect(screen.getByText('Select a row to view evidence.')).toBeInTheDocument()
  })

  it('shows evidence when a row is clicked', async () => {
    render(<ControlledTable />, { wrapper })

    fireEvent.click(screen.getByText('daf-2'))

    await waitFor(() => {
      expect(
        screen.getByText((_, element) =>
          element?.tagName.toLowerCase() === 'p'
          && (element.textContent?.includes('The daf-2 receptor regulates lifespan.') ?? false),
        ),
      ).toBeInTheDocument()
    })

    expect(screen.getByText(/2 evidence quotes/)).toBeInTheDocument()
    expect(
      screen.getByText((_, element) =>
        element?.tagName.toLowerCase() === 'p'
        && (element.textContent?.includes('A second daf-2 evidence sentence.') ?? false),
      ),
    ).toBeInTheDocument()
  })

  it('dispatches PDF navigation when a row is selected', async () => {
    const listener = vi.fn()
    window.addEventListener('pdf-viewer-navigate-evidence', listener)

    render(<ControlledTable />, { wrapper })

    fireEvent.click(screen.getByText('daf-2'))

    await waitFor(() => {
      expect(listener).toHaveBeenCalled()
    })

    window.removeEventListener('pdf-viewer-navigate-evidence', listener)
  })

  it('does not redispatch PDF navigation when the selected tag object is refreshed with the same id', async () => {
    const listener = vi.fn()
    window.addEventListener('pdf-viewer-navigate-evidence', listener)

    const initialTags = makeTags()
    const { rerender } = render(
      <EntityTagTable
        tags={initialTags}
        candidateEvidenceByTagId={makeEvidenceRecordsByTagId()}
        selectedTagId="tag-1"
        onSelectTag={vi.fn()}
        onAcceptTag={vi.fn()}
        onRejectTag={vi.fn()}
        onAcceptAllValidated={vi.fn()}
        onSaveTag={vi.fn()}
        onCreateManualTag={vi.fn(async () => 'manual-1')}
      />,
      { wrapper },
    )

    await waitFor(() => {
      expect(listener).toHaveBeenCalledTimes(1)
    })

    rerender(
      <EntityTagTable
        tags={initialTags.map((tag) => ({ ...tag }))}
        candidateEvidenceByTagId={makeEvidenceRecordsByTagId()}
        selectedTagId="tag-1"
        onSelectTag={vi.fn()}
        onAcceptTag={vi.fn()}
        onRejectTag={vi.fn()}
        onAcceptAllValidated={vi.fn()}
        onSaveTag={vi.fn()}
        onCreateManualTag={vi.fn(async () => 'manual-1')}
      />,
    )

    await waitFor(() => {
      expect(listener).toHaveBeenCalledTimes(1)
    })

    window.removeEventListener('pdf-viewer-navigate-evidence', listener)
  })

  it('calls the accept callback and reflects the updated row state', async () => {
    const onAcceptTag = vi.fn()
    render(<ControlledTable onAcceptTag={onAcceptTag} />, { wrapper })

    fireEvent.click(screen.getAllByRole('button', { name: 'Accept' })[0]!)

    await waitFor(() => {
      expect(onAcceptTag).toHaveBeenCalledWith('tag-1')
      expect(screen.getByText('Accepted')).toBeInTheDocument()
    })
  })

  it('opens a blank manual row when Add Entity is clicked', () => {
    render(<ControlledTable />, { wrapper })

    fireEvent.click(screen.getByRole('button', { name: /\+ Add Entity/i }))

    expect(screen.getByLabelText('Entity name')).toHaveValue('')
    expect(screen.getByRole('combobox', { name: 'Entity type' })).toBeInTheDocument()
  })
})
