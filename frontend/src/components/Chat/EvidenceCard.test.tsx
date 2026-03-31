import userEvent from '@testing-library/user-event'
import { render, screen, waitFor } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { onPDFViewerNavigateEvidence } from '@/components/pdfViewer/pdfEvents'
import theme from '@/theme'
import type { EvidenceRecord } from '@/features/curation/types'

import EvidenceCard from './EvidenceCard'

const EVIDENCE_RECORDS: EvidenceRecord[] = [
  {
    entity: 'crumb',
    verified_quote: 'Crumb is essential for maintaining epithelial polarity.',
    page: 4,
    section: 'Results',
    subsection: 'Gene Expression Analysis',
    chunk_id: 'chunk-1',
    figure_reference: 'Figure 2A',
  },
  {
    entity: 'crumb',
    verified_quote: 'Crumb expression increased in the mutant embryo.',
    page: 6,
    section: 'Discussion',
    chunk_id: 'chunk-2',
  },
  {
    entity: 'notch',
    verified_quote: 'Notch signaling remained unchanged in the treatment arm.',
    page: 8,
    section: 'Results',
    chunk_id: 'chunk-3',
  },
]

function renderEvidenceCard() {
  const onReviewAndCurateClick = vi.fn()

  render(
    <ThemeProvider theme={theme}>
      <EvidenceCard
        evidenceRecords={EVIDENCE_RECORDS}
        onReviewAndCurateClick={onReviewAndCurateClick}
        reviewAndCurateTarget={{
          documentId: 'doc-1',
          originSessionId: 'session-1',
        }}
      />
    </ThemeProvider>
  )

  return { onReviewAndCurateClick }
}

describe('EvidenceCard', () => {
  const originalScrollIntoView = HTMLElement.prototype.scrollIntoView
  let scrollIntoViewMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    scrollIntoViewMock = vi.fn()
    HTMLElement.prototype.scrollIntoView = scrollIntoViewMock
  })

  afterEach(() => {
    HTMLElement.prototype.scrollIntoView = originalScrollIntoView
    vi.useRealTimers()
  })
  it('renders collapsed by default with the header and entity chips', () => {
    renderEvidenceCard()

    expect(screen.getByText('3 evidence quotes')).toBeInTheDocument()
    expect(screen.getByTestId('evidence-card-header-icon')).toHaveStyle({
      width: '14px',
      height: '14px',
      display: 'block',
    })
    expect(screen.getByRole('button', { name: 'crumb 2' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: 'notch 1' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByText(/Full evidence review with PDF highlighting/i)).not.toBeInTheDocument()
  })

  it('expands quotes for the clicked entity chip and exposes the review action', async () => {
    const user = userEvent.setup()
    const { onReviewAndCurateClick } = renderEvidenceCard()

    await user.click(screen.getByRole('button', { name: 'crumb 2' }))

    expect(
      await screen.findByText('"Crumb is essential for maintaining epithelial polarity."')
    ).toBeInTheDocument()
    expect(screen.getByText('p. 4 · Results › Gene Expression Analysis')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Review & Curate/i }))

    expect(onReviewAndCurateClick).toHaveBeenCalledTimes(1)
  })

  it('dispatches PDF evidence navigation when a quote is clicked', async () => {
    const user = userEvent.setup()
    const onNavigateEvidence = vi.fn()
    const unsubscribe = onPDFViewerNavigateEvidence(onNavigateEvidence)

    renderEvidenceCard()
    await user.click(screen.getByRole('button', { name: 'crumb 2' }))
    await user.click(
      await screen.findByRole('button', {
        name: /Highlight evidence on PDF: Crumb is essential for maintaining epithelial polarity\./i,
      }),
    )

    expect(onNavigateEvidence).toHaveBeenCalledTimes(1)
    expect(onNavigateEvidence.mock.calls[0][0].detail.command).toEqual(
      expect.objectContaining({
        anchorId: expect.stringContaining('chat-evidence:chunk-1:p4:crumb:'),
        searchText: 'Crumb is essential for maintaining epithelial polarity.',
        pageNumber: 4,
        sectionTitle: 'Results',
        mode: 'select',
        anchor: expect.objectContaining({
          anchor_kind: 'snippet',
          locator_quality: 'exact_quote',
          snippet_text: 'Crumb is essential for maintaining epithelial polarity.',
          section_title: 'Results',
          subsection_title: 'Gene Expression Analysis',
          chunk_ids: ['chunk-1'],
        }),
      }),
    )

    unsubscribe()
  })

  it('copies the evidence location and quote without dispatching navigation', async () => {
    const user = userEvent.setup()
    const writeTextSpy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(undefined)
    const onNavigateEvidence = vi.fn()
    const unsubscribe = onPDFViewerNavigateEvidence(onNavigateEvidence)

    renderEvidenceCard()
    await user.click(screen.getByRole('button', { name: 'crumb 2' }))
    await user.click(await screen.findByTestId('copy-evidence-quote-chunk-1'))

    expect(writeTextSpy).toHaveBeenCalledWith(
      'p. 4 · Results › Gene Expression Analysis\n"Crumb is essential for maintaining epithelial polarity."',
    )
    expect(onNavigateEvidence).not.toHaveBeenCalled()

    unsubscribe()
    writeTextSpy.mockRestore()
  })
  it('collapses the active entity when the same chip is clicked again', async () => {
    const user = userEvent.setup()
    renderEvidenceCard()

    const crumbChip = screen.getByRole('button', { name: 'crumb 2' })

    await user.click(crumbChip)
    expect(
      await screen.findByText('"Crumb expression increased in the mutant embryo."')
    ).toBeInTheDocument()

    await user.click(crumbChip)

    await waitFor(() => {
      expect(
        screen.queryByText('"Crumb expression increased in the mutant embryo."')
      ).not.toBeInTheDocument()
    })
  })

  it('switches the expanded quote list when a different entity chip is clicked', async () => {
    const user = userEvent.setup()
    renderEvidenceCard()

    await user.click(screen.getByRole('button', { name: 'crumb 2' }))
    expect(
      await screen.findByText('"Crumb is essential for maintaining epithelial polarity."')
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'notch 1' }))

    expect(
      await screen.findByText('"Notch signaling remained unchanged in the treatment arm."')
    ).toBeInTheDocument()

    await waitFor(() => {
      expect(
        screen.queryByText('"Crumb is essential for maintaining epithelial polarity."')
      ).not.toBeInTheDocument()
    })
  })

  it('scrolls the expanded evidence quotes into view when an entity chip is opened', async () => {
    const user = userEvent.setup()
    renderEvidenceCard()

    await user.click(screen.getByRole('button', { name: 'crumb 2' }))

    expect(
      await screen.findByText('"Crumb is essential for maintaining epithelial polarity."')
    ).toBeInTheDocument()

    await waitFor(() => {
      expect(scrollIntoViewMock).toHaveBeenCalledWith({
        behavior: 'smooth',
        block: 'end',
        inline: 'nearest',
      })
    })
  })
})
