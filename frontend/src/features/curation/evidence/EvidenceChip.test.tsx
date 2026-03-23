import type { ComponentProps } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'

import type { CurationEvidenceRecord } from '../types'
import theme from '@/theme'
import EvidenceChip from './EvidenceChip'

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
      page_number: 3,
      section_title: 'Results',
      chunk_ids: [`chunk-${anchorId}`],
      ...anchorOverrides,
    },
    created_at: '2026-03-20T12:00:00Z',
    updated_at: '2026-03-20T12:00:00Z',
    warnings: [],
    ...recordOverrides,
  }
}

function renderEvidenceChip(
  props: Partial<ComponentProps<typeof EvidenceChip>> = {}
) {
  const evidence = props.evidence ?? createEvidenceRecord('anchor-1')
  const onClick = props.onClick ?? vi.fn()
  const onHoverStart = props.onHoverStart ?? vi.fn()
  const onHoverEnd = props.onHoverEnd ?? vi.fn()

  render(
    <ThemeProvider theme={theme}>
      <EvidenceChip
        evidence={evidence}
        isHovered={props.isHovered ?? false}
        isSelected={props.isSelected ?? false}
        label={props.label ?? 'p.3'}
        onClick={onClick}
        onHoverEnd={onHoverEnd}
        onHoverStart={onHoverStart}
        quality={props.quality ?? evidence.anchor.locator_quality}
      />
    </ThemeProvider>
  )

  return {
    evidence,
    onClick,
    onHoverEnd,
    onHoverStart,
  }
}

describe('EvidenceChip', () => {
  it('renders an active chip and dispatches selection on click', async () => {
    const user = userEvent.setup()
    const { evidence, onClick } = renderEvidenceChip({
      isSelected: true,
      label: 'p.7',
    })

    const chip = screen.getByTestId(`evidence-chip-${evidence.anchor_id}`)

    expect(chip).toHaveAttribute('aria-pressed', 'true')
    expect(chip).toHaveAttribute('data-selected', 'true')
    expect(chip).toHaveTextContent('p.7')

    await user.click(chip)

    expect(onClick).toHaveBeenCalledTimes(1)
    expect(onClick).toHaveBeenCalledWith(evidence)
  })

  it('shows snippet preview text on hover and clears transient state on unhover', async () => {
    const user = userEvent.setup()
    const { evidence, onHoverEnd, onHoverStart } = renderEvidenceChip({
      evidence: createEvidenceRecord('anchor-2', {
        anchor: {
          snippet_text: null,
          sentence_text: 'Sentence fallback preview',
        },
      }),
    })

    const chip = screen.getByTestId(`evidence-chip-${evidence.anchor_id}`)

    await user.hover(chip)

    expect(onHoverStart).toHaveBeenCalledTimes(1)
    expect(onHoverStart).toHaveBeenCalledWith(evidence)
    expect(await screen.findByText('Sentence fallback preview')).toBeInTheDocument()

    await user.unhover(chip)

    expect(onHoverEnd).toHaveBeenCalledTimes(1)
  })
})
