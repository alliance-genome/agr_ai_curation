import userEvent from '@testing-library/user-event'
import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'

import { onPDFViewerNavigateEvidence } from '@/components/pdfViewer/pdfEvents'
import EvidenceQuote from '@/components/Chat/EvidenceQuote'
import type { EvidenceRecord, CurationEvidenceRecord } from '@/features/curation/types'
import type { EntityTag } from '@/features/curation/entityTable/types'
import EvidencePreviewPane from '@/features/curation/entityTable/EvidencePreviewPane'
import theme from '@/theme'

const sharedQuote =
  'these, crb 11A22 (null allele) and c rb 8F105 (point mutation encoding a truncated protein lacking 23 amino acids), display abnormal PRC morphology in adult eyes, with bulky and closely apposed rhabdomeres'

const chatEvidenceRecord: EvidenceRecord = {
  entity: 'crb',
  verified_quote: sharedQuote,
  page: 6,
  section: '2. Results and Discussion',
  subsection: 'Linking Phenotype to Genotype through Molecular Abundance of Eye Proteins',
  chunk_id: 'chunk-crb-6',
}

const curationEvidenceRecord: CurationEvidenceRecord = {
  anchor_id: 'anchor-crb-6',
  candidate_id: 'candidate-crb-1',
  source: 'extracted',
  field_keys: ['gene_symbol'],
  field_group_keys: ['identity'],
  is_primary: true,
  anchor: {
    anchor_kind: 'snippet',
    locator_quality: 'normalized_quote',
    supports_decision: 'supports',
    sentence_text: sharedQuote,
    snippet_text: sharedQuote,
    normalized_text: sharedQuote,
    viewer_search_text: `2. Results and Discussion: ${sharedQuote}`,
    viewer_highlightable: true,
    page_number: 6,
    section_title:
      '2. Results and Discussion > Linking Phenotype to Genotype through Molecular Abundance of Eye Proteins',
    subsection_title: null,
    chunk_ids: ['chunk-crb-6'],
  },
  created_at: '2026-04-13T00:00:00Z',
  updated_at: '2026-04-13T00:00:00Z',
  warnings: [],
}

const curationTag: EntityTag = {
  tag_id: 'candidate-crb-1',
  entity_name: 'crb',
  entity_type: 'ATP:0000005',
  species: 'NCBITaxon:7227',
  topic: 'gene expression',
  db_status: 'validated',
  db_entity_id: 'FBgn0000392',
  source: 'ai',
  decision: 'pending',
  evidence: null,
  notes: null,
}

const wrapper = ({ children }: { children: React.ReactNode }) => (
  <ThemeProvider theme={theme}>{children}</ThemeProvider>
)

describe('shared evidence interaction parity', () => {
  it('emits equivalent viewer commands from chat and curation quote clicks', async () => {
    const user = userEvent.setup()
    const onNavigateEvidence = vi.fn()
    const unsubscribe = onPDFViewerNavigateEvidence(onNavigateEvidence)

    render(
      <>
        <EvidenceQuote
          evidenceRecord={chatEvidenceRecord}
          borderColor="#64b5f6"
        />
        <EvidencePreviewPane
          tag={curationTag}
          evidenceRecords={[curationEvidenceRecord]}
        />
      </>,
      { wrapper },
    )

    const quoteButtons = screen.getAllByRole('button', {
      name: new RegExp(`^Highlight evidence on PDF: ${sharedQuote.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`, 'i'),
    })

    await user.click(quoteButtons[0])
    await user.click(quoteButtons[1])

    expect(onNavigateEvidence).toHaveBeenCalledTimes(2)

    const chatCommand = onNavigateEvidence.mock.calls[0][0].detail.command
    const curationCommand = onNavigateEvidence.mock.calls[1][0].detail.command

    expect(chatCommand.searchText).toBe(curationCommand.searchText)
    expect(chatCommand.pageNumber).toBe(curationCommand.pageNumber)
    expect(chatCommand.sectionTitle).toBe(curationCommand.sectionTitle)
    expect(chatCommand.anchor.section_title).toBe(curationCommand.anchor.section_title)
    expect(chatCommand.anchor.subsection_title).toBe(curationCommand.anchor.subsection_title)
    expect(chatCommand.anchor.sentence_text).toBe(curationCommand.anchor.sentence_text)
    expect(chatCommand.anchor.snippet_text).toBe(curationCommand.anchor.snippet_text)
    expect(chatCommand.anchor.viewer_search_text).toBe(curationCommand.anchor.viewer_search_text)
    expect(chatCommand.anchor.chunk_ids).toEqual(curationCommand.anchor.chunk_ids)

    unsubscribe()
  })
})
