import { Stack, Typography } from '@mui/material'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it } from 'vitest'

import type { CurationEvidenceRecord } from '../types'
import theme from '@/theme'
import EvidenceChipGroup from './EvidenceChipGroup'
import EvidencePanel from './EvidencePanel'
import { useEvidenceNavigation } from './useEvidenceNavigation'

type EvidenceRecordOverrides = Partial<Omit<CurationEvidenceRecord, 'anchor'>> & {
  anchor?: Partial<CurationEvidenceRecord['anchor']>
}

function createEvidenceRecord(
  anchorId: string,
  overrides: EvidenceRecordOverrides = {}
): CurationEvidenceRecord {
  const { anchor: anchorOverrides, ...recordOverrides } = overrides

  return {
    anchor_id: anchorId,
    candidate_id: 'candidate-1',
    source: 'extracted',
    field_keys: ['gene_symbol'],
    field_group_keys: ['identity'],
    is_primary: anchorId === 'anchor-1',
    anchor: {
      anchor_kind: 'snippet',
      locator_quality: 'exact_quote',
      supports_decision: 'supports',
      snippet_text: `Snippet for ${anchorId}`,
      sentence_text: `Sentence for ${anchorId}`,
      viewer_search_text: `Search text for ${anchorId}`,
      page_number: anchorId === 'anchor-1' ? 3 : 7,
      section_title: anchorId === 'anchor-1' ? 'Results' : 'Discussion',
      chunk_ids: [`chunk-${anchorId}`],
      ...anchorOverrides,
    },
    created_at: '2026-03-20T12:00:00Z',
    updated_at: '2026-03-20T12:00:00Z',
    warnings: [],
    ...recordOverrides,
  }
}

const candidateEvidence = [
  createEvidenceRecord('anchor-1'),
  createEvidenceRecord('anchor-2', {
    field_group_keys: ['relation'],
  }),
]

function EvidenceInteractionHarness() {
  const navigation = useEvidenceNavigation({ evidence: candidateEvidence })

  return (
    <ThemeProvider theme={theme}>
      <Stack spacing={2}>
        <EvidenceChipGroup
          evidenceAnchorIds={candidateEvidence.map((record) => record.anchor_id)}
          evidenceByAnchorId={navigation.evidenceByAnchorId}
          hoverEvidence={navigation.hoverEvidence}
          hoveredEvidence={navigation.hoveredEvidence}
          selectEvidence={navigation.selectEvidence}
          selectedEvidence={navigation.selectedEvidence}
        />
        <EvidencePanel
          candidateEvidence={navigation.candidateEvidence}
          evidenceByGroup={navigation.evidenceByGroup}
          hoverEvidence={navigation.hoverEvidence}
          hoveredEvidence={navigation.hoveredEvidence}
          selectEvidence={navigation.selectEvidence}
          selectedEvidence={navigation.selectedEvidence}
        />
        <Typography data-testid="selected-anchor">
          {navigation.selectedEvidence?.anchor_id ?? 'none'}
        </Typography>
        <Typography data-testid="hovered-anchor">
          {navigation.hoveredEvidence?.anchor_id ?? 'none'}
        </Typography>
        <Typography data-testid="pending-mode">
          {navigation.pendingNavigation?.mode ?? 'none'}
        </Typography>
      </Stack>
    </ThemeProvider>
  )
}

describe('Evidence interaction sync', () => {
  it('keeps chip and panel selection synchronized across clicks', async () => {
    const user = userEvent.setup()

    render(<EvidenceInteractionHarness />)

    const firstChip = screen.getByTestId('evidence-chip-anchor-1')
    const secondChip = screen.getByTestId('evidence-chip-anchor-2')
    const firstCard = screen.getByTestId('evidence-card-anchor-1')
    const secondCard = screen.getByTestId('evidence-card-anchor-2')

    await user.click(firstChip)

    expect(screen.getByTestId('selected-anchor')).toHaveTextContent('anchor-1')
    expect(firstChip).toHaveAttribute('data-selected', 'true')
    expect(firstCard).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByTestId('pending-mode')).toHaveTextContent('select')

    await user.click(secondCard)

    expect(screen.getByTestId('selected-anchor')).toHaveTextContent('anchor-2')
    expect(secondChip).toHaveAttribute('data-selected', 'true')
    expect(secondCard).toHaveAttribute('aria-pressed', 'true')
    expect(firstChip).toHaveAttribute('data-selected', 'false')
  })

  it('treats chip hover as transient and restores the selected evidence on exit', async () => {
    const user = userEvent.setup()

    render(<EvidenceInteractionHarness />)

    const firstChip = screen.getByTestId('evidence-chip-anchor-1')
    const secondChip = screen.getByTestId('evidence-chip-anchor-2')
    const secondCard = screen.getByTestId('evidence-card-anchor-2')

    await user.click(firstChip)
    await user.hover(secondChip)

    expect(screen.getByTestId('hovered-anchor')).toHaveTextContent('anchor-2')
    expect(secondChip).toHaveAttribute('data-hovered', 'true')
    expect(secondCard).toHaveAttribute('data-hovered', 'true')
    expect(screen.getByTestId('pending-mode')).toHaveTextContent('hover')

    await user.unhover(secondChip)

    expect(screen.getByTestId('hovered-anchor')).toHaveTextContent('none')
    expect(screen.getByTestId('selected-anchor')).toHaveTextContent('anchor-1')
    expect(screen.getByTestId('pending-mode')).toHaveTextContent('select')
  })

  it('mirrors panel hover state back into the editor chips', async () => {
    const user = userEvent.setup()

    render(<EvidenceInteractionHarness />)

    const secondChip = screen.getByTestId('evidence-chip-anchor-2')
    const secondCard = screen.getByTestId('evidence-card-anchor-2')

    await user.hover(secondCard)

    expect(screen.getByTestId('hovered-anchor')).toHaveTextContent('anchor-2')
    expect(secondCard).toHaveAttribute('data-hovered', 'true')
    expect(secondChip).toHaveAttribute('data-hovered', 'true')

    await user.unhover(secondCard)

    expect(screen.getByTestId('hovered-anchor')).toHaveTextContent('none')
    expect(screen.getByTestId('pending-mode')).toHaveTextContent('none')
  })
})
