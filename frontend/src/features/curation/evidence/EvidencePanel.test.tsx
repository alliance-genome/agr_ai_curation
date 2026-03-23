import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'

import type { CurationEvidenceRecord } from '../types'
import theme from '@/theme'
import EvidencePanel, { type EvidencePanelProps } from './EvidencePanel'

type EvidenceRecordOverrides = Partial<Omit<CurationEvidenceRecord, 'anchor'>> & {
  anchor?: Partial<CurationEvidenceRecord['anchor']>
}

function createEvidenceRecord(
  anchorId: string,
  overrides: EvidenceRecordOverrides = {},
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

function buildEvidenceByGroup(
  candidateEvidence: CurationEvidenceRecord[],
): EvidencePanelProps['evidenceByGroup'] {
  return candidateEvidence.reduce<EvidencePanelProps['evidenceByGroup']>(
    (groups, record) => {
      for (const groupKey of record.field_group_keys) {
        if (groups[groupKey] === undefined) {
          groups[groupKey] = []
        }

        groups[groupKey].push(record)
      }

      return groups
    },
    {},
  )
}

function renderEvidencePanel(
  props: Partial<EvidencePanelProps> = {},
) {
  const candidateEvidence = props.candidateEvidence ?? [
    createEvidenceRecord('anchor-1'),
    createEvidenceRecord('anchor-2', {
      field_group_keys: ['relation'],
      anchor: {
        locator_quality: 'section_only',
        page_number: 7,
        section_title: 'Discussion',
      },
    }),
  ]
  const selectEvidence = props.selectEvidence ?? vi.fn()
  const hoverEvidence = props.hoverEvidence ?? vi.fn()

  const resolvedProps: EvidencePanelProps = {
    candidateEvidence,
    evidenceByGroup: props.evidenceByGroup ?? buildEvidenceByGroup(candidateEvidence),
    hoverEvidence,
    hoveredEvidence: props.hoveredEvidence ?? null,
    selectedEvidence: props.selectedEvidence ?? null,
    selectEvidence,
  }

  const renderResult = render(
    <ThemeProvider theme={theme}>
      <EvidencePanel {...resolvedProps} />
    </ThemeProvider>,
  )

  return {
    ...renderResult,
    hoverEvidence,
    props: resolvedProps,
    selectEvidence,
  }
}

describe('EvidencePanel', () => {
  it('renders evidence cards, adapter-defined group filters, and active indicators', () => {
    const selectedEvidence = createEvidenceRecord('anchor-selected')
    const hoveredEvidence = createEvidenceRecord('anchor-hovered', {
      field_group_keys: ['relation'],
      anchor: {
        locator_quality: 'normalized_quote',
        page_number: 9,
        section_title: 'Methods',
      },
    })

    renderEvidencePanel({
      candidateEvidence: [selectedEvidence, hoveredEvidence],
      hoveredEvidence,
      selectedEvidence,
    })

    expect(screen.getByText('Evidence Anchors (2)')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'All' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'identity' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'relation' })).toBeInTheDocument()

    const selectedCard = screen.getByTestId('evidence-card-anchor-selected')
    const hoveredCard = screen.getByTestId('evidence-card-anchor-hovered')

    expect(within(selectedCard).getByText('Focused in viewer')).toBeInTheDocument()
    expect(within(selectedCard).getByText('p.3 §Results')).toBeInTheDocument()
    expect(within(selectedCard).getByText('identity')).toBeInTheDocument()

    expect(within(hoveredCard).getByText('Highlighted in PDF')).toBeInTheDocument()
    expect(within(hoveredCard).getByText('p.9 §Methods')).toBeInTheDocument()
    expect(within(hoveredCard).getByText('relation')).toBeInTheDocument()
  })

  it('maps locator quality badges to success, warning, and error tones', () => {
    const exactQuoteEvidence = createEvidenceRecord('anchor-exact', {
      anchor: { locator_quality: 'exact_quote' },
    })
    const sectionEvidence = createEvidenceRecord('anchor-section', {
      anchor: { locator_quality: 'section_only' },
    })
    const normalizedEvidence = createEvidenceRecord('anchor-normalized', {
      anchor: { locator_quality: 'normalized_quote' },
    })
    const unresolvedEvidence = createEvidenceRecord('anchor-unresolved', {
      anchor: { locator_quality: 'unresolved' },
    })
    const pageEvidence = createEvidenceRecord('anchor-page', {
      anchor: { locator_quality: 'page_only' },
    })

    renderEvidencePanel({
      candidateEvidence: [
        exactQuoteEvidence,
        sectionEvidence,
        normalizedEvidence,
        unresolvedEvidence,
        pageEvidence,
      ],
    })

    const exactCard = screen.getByTestId('evidence-card-anchor-exact')
    const warningCard = screen.getByTestId('evidence-card-anchor-section')
    const normalizedCard = screen.getByTestId('evidence-card-anchor-normalized')
    const unresolvedCard = screen.getByTestId('evidence-card-anchor-unresolved')
    const pageCard = screen.getByTestId('evidence-card-anchor-page')

    expect(
      within(exactCard)
        .getByText('exact_quote')
        .closest('[data-quality-tone]'),
    ).toHaveAttribute('data-quality-tone', 'success')
    expect(
      within(warningCard)
        .getByText('section_only')
        .closest('[data-quality-tone]'),
    ).toHaveAttribute('data-quality-tone', 'warning')
    expect(
      within(normalizedCard)
        .getByText('normalized_quote')
        .closest('[data-quality-tone]'),
    ).toHaveAttribute('data-quality-tone', 'warning')
    expect(
      within(unresolvedCard)
        .getByText('unresolved')
        .closest('[data-quality-tone]'),
    ).toHaveAttribute('data-quality-tone', 'error')
    expect(
      within(pageCard)
        .getByText('page_only')
        .closest('[data-quality-tone]'),
    ).toHaveAttribute('data-quality-tone', 'error')
  })

  it('renders snippet from snippet_text or sentence_text fallback order', () => {
    renderEvidencePanel({
      candidateEvidence: [
        createEvidenceRecord('anchor-snippet-first', {
          anchor: {
            snippet_text: 'Explicit snippet text',
            sentence_text: 'Fallback sentence text',
          },
        }),
        createEvidenceRecord('anchor-sentence-fallback', {
          anchor: {
            snippet_text: null,
            sentence_text: 'Fallback sentence text',
          },
        }),
      ],
    })

    expect(screen.getByText('Explicit snippet text')).toBeInTheDocument()
    expect(screen.getByText('Fallback sentence text')).toBeInTheDocument()
  })

  it('filters evidence cards by adapter-defined group', async () => {
    const user = userEvent.setup()
    const identityEvidence = createEvidenceRecord('anchor-identity', {
      anchor: { snippet_text: 'Identity snippet' },
    })
    const relationEvidence = createEvidenceRecord('anchor-relation', {
      field_group_keys: ['relation'],
      anchor: { snippet_text: 'Relation snippet' },
    })

    renderEvidencePanel({
      candidateEvidence: [identityEvidence, relationEvidence],
    })

    expect(screen.getByText('Identity snippet')).toBeInTheDocument()
    expect(screen.getByText('Relation snippet')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'relation' }))

    expect(screen.queryByText('Identity snippet')).not.toBeInTheDocument()
    expect(screen.getByText('Relation snippet')).toBeInTheDocument()
    expect(screen.getByText('Showing 1 in relation')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'All' }))

    expect(screen.getByText('Identity snippet')).toBeInTheDocument()
    expect(screen.getByText('Relation snippet')).toBeInTheDocument()
  })

  it('dispatches selection when an evidence card is clicked', async () => {
    const user = userEvent.setup()
    const clickableEvidence = createEvidenceRecord('anchor-clickable')
    const selectEvidence = vi.fn()

    renderEvidencePanel({
      candidateEvidence: [clickableEvidence],
      selectEvidence,
    })

    await user.click(screen.getByTestId('evidence-card-anchor-clickable'))

    expect(selectEvidence).toHaveBeenCalledTimes(1)
    expect(selectEvidence).toHaveBeenCalledWith(clickableEvidence)
  })

  it('dispatches transient hover state when an evidence card is hovered', async () => {
    const user = userEvent.setup()
    const hoverableEvidence = createEvidenceRecord('anchor-hoverable')
    const hoverEvidence = vi.fn()

    renderEvidencePanel({
      candidateEvidence: [hoverableEvidence],
      hoverEvidence,
    })

    const card = screen.getByTestId('evidence-card-anchor-hoverable')

    await user.hover(card)
    expect(hoverEvidence).toHaveBeenNthCalledWith(1, hoverableEvidence)

    await user.unhover(card)
    expect(hoverEvidence).toHaveBeenNthCalledWith(2, null)
  })

  it('shows the degraded locator warning for page-level and unresolved anchors', () => {
    const pageOnlyEvidence = createEvidenceRecord('anchor-page-only', {
      anchor: { locator_quality: 'page_only' },
    })
    const documentOnlyEvidence = createEvidenceRecord('anchor-document-only', {
      anchor: { locator_quality: 'document_only' },
    })
    const unresolvedEvidence = createEvidenceRecord('anchor-unresolved', {
      anchor: { locator_quality: 'unresolved' },
    })

    renderEvidencePanel({
      candidateEvidence: [pageOnlyEvidence, documentOnlyEvidence, unresolvedEvidence],
    })

    expect(
      screen.getAllByText(
        'Could not resolve exact quote - will jump to best available location',
      ),
    ).toHaveLength(3)
  })
})
