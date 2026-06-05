import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it } from 'vitest'

import theme from '@/theme'
import ChipFieldValue from './ChipFieldValue'

function renderChipValue(ui: React.ReactNode) {
  return render(<ThemeProvider theme={theme}>{ui}</ThemeProvider>)
}

describe('ChipFieldValue', () => {
  it('renders one chip per list item', () => {
    renderChipValue(<ChipFieldValue value={['UBERON:0000966', 'UBERON:0001017']} />)

    expect(screen.getByText('UBERON:0000966')).toBeInTheDocument()
    expect(screen.getByText('UBERON:0001017')).toBeInTheDocument()
  })

  it('renders nothing for an empty list', () => {
    const { container } = renderChipValue(<ChipFieldValue value={[]} />)

    expect(container.querySelectorAll('.MuiChip-root').length).toBe(0)
  })
})
