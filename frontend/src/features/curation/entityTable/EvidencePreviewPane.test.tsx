import { render, screen, fireEvent } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'
import type { CurationEvidenceRecord } from '@/features/curation/types'
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

const makeEvidenceRecord = (
  overrides: Partial<CurationEvidenceRecord> = {},
): CurationEvidenceRecord => ({
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
  ...overrides,
})

describe('EvidencePreviewPane', () => {
  it('shows empty state when no tag is selected', () => {
    render(<EvidencePreviewPane tag={null} onShowInPdf={vi.fn()} />, { wrapper })
    expect(screen.getByText('Select a row to view evidence.')).toBeInTheDocument()
  })

  it('shows the sentence quote for a selected tag', () => {
    render(<EvidencePreviewPane tag={makeTag()} onShowInPdf={vi.fn()} />, { wrapper })
    expect(
      screen.getByText((_, element) =>
        element?.tagName.toLowerCase() === 'p'
        && (element.textContent?.includes('The daf-2 receptor regulates lifespan.') ?? false),
      ),
    ).toBeInTheDocument()
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
    expect(onShowInPdf).toHaveBeenCalledWith(makeTag(), null)
  })

  it('shows manual tag message when evidence is null', () => {
    render(<EvidencePreviewPane tag={makeTag({ evidence: null, source: 'manual' })} onShowInPdf={vi.fn()} />, { wrapper })
    expect(screen.getByText(/No AI evidence/)).toBeInTheDocument()
  })

  it('shows multiple candidate evidence records when provided', () => {
    render(
      <EvidencePreviewPane
        tag={makeTag({ evidence: null })}
        evidenceRecords={[
          makeEvidenceRecord(),
          makeEvidenceRecord({
            anchor_id: 'anchor-2',
            is_primary: false,
            anchor: {
              anchor_kind: 'snippet',
              locator_quality: 'exact_quote',
              supports_decision: 'supports',
              sentence_text: 'A second daf-2 evidence sentence from the PDF.',
              snippet_text: 'A second daf-2 evidence sentence from the PDF.',
              viewer_search_text: 'A second daf-2 evidence sentence from the PDF.',
              viewer_highlightable: true,
              page_number: 5,
              section_title: 'Discussion',
              chunk_ids: ['c2'],
            },
          }),
        ]}
        onShowInPdf={vi.fn()}
      />,
      { wrapper },
    )

    expect(screen.getByText(/2 evidence quotes/)).toBeInTheDocument()
    expect(
      screen.getByText((_, element) =>
        element?.tagName.toLowerCase() === 'p'
        && (element.textContent?.includes('A second daf-2 evidence sentence from the PDF.') ?? false),
      ),
    ).toBeInTheDocument()
    expect(screen.getAllByText('Show in PDF')).toHaveLength(2)
  })
})
