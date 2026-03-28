import userEvent from '@testing-library/user-event'
import { render, screen, waitFor } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'

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
  it('renders collapsed by default with the header and entity chips', () => {
    renderEvidenceCard()

    expect(screen.getByText('3 evidence quotes')).toBeInTheDocument()
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
})
