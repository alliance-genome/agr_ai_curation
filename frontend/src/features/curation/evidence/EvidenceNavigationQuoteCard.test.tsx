import { fireEvent, render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { expect, it, vi } from 'vitest'

import { onPDFViewerNavigateEvidence } from '@/components/pdfViewer/pdfEvents'
import theme from '@/theme'
import EvidenceNavigationQuoteCard from './EvidenceNavigationQuoteCard'

it('renders a disabled unavailable state when no navigation command exists', () => {
  const navigateEvidence = vi.fn()
  const unsubscribe = onPDFViewerNavigateEvidence(navigateEvidence)

  render(
    <ThemeProvider theme={theme}>
      <EvidenceNavigationQuoteCard
        appearance="workspace"
        ariaLabel="Evidence has no navigable PDF location"
        command={null}
        footerText="PDF navigation is unavailable for this evidence."
        quote="[missing evidence text]"
      />
    </ThemeProvider>,
  )

  const action = screen.getByRole('button', {
    name: 'Evidence has no navigable PDF location',
  })
  expect(action).toBeDisabled()
  expect(action).toHaveTextContent('PDF location unavailable')
  expect(action).toHaveTextContent('PDF navigation is unavailable for this evidence.')

  fireEvent.click(action)

  expect(navigateEvidence).not.toHaveBeenCalled()
  unsubscribe()
})
