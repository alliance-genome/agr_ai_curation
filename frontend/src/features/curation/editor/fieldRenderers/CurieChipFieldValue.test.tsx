import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it } from 'vitest'

import theme from '@/theme'
import CurieChipFieldValue from './CurieChipFieldValue'

function renderCurieValue(ui: React.ReactNode) {
  return render(<ThemeProvider theme={theme}>{ui}</ThemeProvider>)
}

describe('CurieChipFieldValue', () => {
  it('renders an object label with the CURIE as secondary text', () => {
    renderCurieValue(
      <CurieChipFieldValue value={{ curie: 'DOID:0050200', name: 'amyotrophic lateral sclerosis' }} />,
    )

    expect(screen.getByText('amyotrophic lateral sclerosis')).toBeInTheDocument()
    expect(screen.getByText('DOID:0050200')).toBeInTheDocument()
  })

  it('renders plain CURIE strings', () => {
    renderCurieValue(<CurieChipFieldValue value="WBPhenotype:0001191" />)

    expect(screen.getByText('WBPhenotype:0001191')).toBeInTheDocument()
  })
})
