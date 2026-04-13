import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'
import { onPDFViewerNavigateEvidence } from '@/components/pdfViewer/pdfEvents'
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
    render(<EvidencePreviewPane tag={null} />, { wrapper })
    expect(screen.getByText('Select a row to view evidence.')).toBeInTheDocument()
  })

  it('shows the sentence quote for a selected tag', () => {
    render(<EvidencePreviewPane tag={makeTag()} />, { wrapper })
    expect(
      screen.getByRole('button', {
        name: /Highlight evidence on PDF: The daf-2 receptor regulates lifespan\./i,
      }),
    ).toBeInTheDocument()
  })

  it('shows page and section metadata', () => {
    render(<EvidencePreviewPane tag={makeTag()} />, { wrapper })
    expect(screen.getByText('p. 3 · Results')).toBeInTheDocument()
  })

  it('shows db entity id when available', () => {
    render(<EvidencePreviewPane tag={makeTag()} />, { wrapper })
    expect(screen.getByText(/WBGene00000898/)).toBeInTheDocument()
  })

  it('dispatches PDF navigation when the evidence quote is clicked', async () => {
    const user = userEvent.setup()
    const onNavigateEvidence = vi.fn()
    const unsubscribe = onPDFViewerNavigateEvidence(onNavigateEvidence)

    render(<EvidencePreviewPane tag={makeTag()} />, { wrapper })
    await user.click(
      screen.getByRole('button', {
        name: /Highlight evidence on PDF: The daf-2 receptor regulates lifespan\./i,
      }),
    )

    expect(onNavigateEvidence).toHaveBeenCalledTimes(1)
    expect(onNavigateEvidence.mock.calls[0][0].detail.command).toEqual(
      expect.objectContaining({
        pageNumber: 3,
        sectionTitle: 'Results',
        searchText: 'The daf-2 receptor regulates lifespan.',
        anchor: expect.objectContaining({
          snippet_text: 'The daf-2 receptor regulates lifespan.',
          page_number: 3,
          section_title: 'Results',
        }),
      }),
    )

    unsubscribe()
  })

  it('shows manual tag message when evidence is null', () => {
    render(<EvidencePreviewPane tag={makeTag({ evidence: null, source: 'manual' })} />, { wrapper })
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
      />,
      { wrapper },
    )

    expect(screen.getByText(/2 evidence quotes/)).toBeInTheDocument()
    expect(
      screen.getByRole('button', {
        name: /Highlight evidence on PDF: A second daf-2 evidence sentence from the PDF\./i,
      }),
    ).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /Highlight evidence on PDF:/i })).toHaveLength(2)
  })

  it('dispatches the richer workspace evidence record when the quote is clicked', async () => {
    const user = userEvent.setup()
    const onNavigateEvidence = vi.fn()
    const unsubscribe = onPDFViewerNavigateEvidence(onNavigateEvidence)
    const evidenceRecord = makeEvidenceRecord({
      anchor_id: 'anchor-rich-1',
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'normalized_quote',
        supports_decision: 'supports',
        sentence_text: 'The curated quote from the workspace evidence record.',
        snippet_text: 'The curated quote from the workspace evidence record.',
        viewer_search_text: 'Results: The curated quote from the workspace evidence record.',
        viewer_highlightable: true,
        page_number: 6,
        section_title: 'Results',
        chunk_ids: ['c-rich-1'],
      },
    })

    render(
      <EvidencePreviewPane
        tag={makeTag({
          evidence: {
            sentence_text: 'Results: The curated quote from the row preview.',
            page_number: 6,
            section_title: 'Results',
            chunk_ids: ['c-rich-1'],
          },
        })}
        evidenceRecords={[evidenceRecord]}
      />,
      { wrapper },
    )

    await user.click(
      screen.getByRole('button', {
        name: /Highlight evidence on PDF: The curated quote from the workspace evidence record\./i,
      }),
    )

    expect(onNavigateEvidence).toHaveBeenCalledTimes(1)
    expect(onNavigateEvidence.mock.calls[0][0].detail.command).toEqual(
      expect.objectContaining({
        anchorId: 'anchor-rich-1',
        pageNumber: 6,
        sectionTitle: 'Results',
        searchText: 'The curated quote from the workspace evidence record.',
        anchor: expect.objectContaining({
          locator_quality: 'exact_quote',
          sentence_text: 'The curated quote from the workspace evidence record.',
          snippet_text: 'The curated quote from the workspace evidence record.',
          viewer_search_text: 'The curated quote from the workspace evidence record.',
          chunk_ids: ['c-rich-1'],
        }),
      }),
    )

    unsubscribe()
  })
})
