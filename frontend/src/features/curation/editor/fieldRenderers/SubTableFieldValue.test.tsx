import { fireEvent, render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it } from 'vitest'

import theme from '@/theme'
import SubTableFieldValue from './SubTableFieldValue'

function renderSubTable(ui: React.ReactNode) {
  return render(<ThemeProvider theme={theme}>{ui}</ThemeProvider>)
}

describe('SubTableFieldValue', () => {
  it('renders condition rows in a compact expandable table', () => {
    renderSubTable(
      <SubTableFieldValue
        value={[
          { relation_type: 'has_condition', condition: 'heat shock' },
          { relation_type: 'has_condition', condition: 'RNAi' },
        ]}
      />,
    )

    expect(screen.getByText('2 items')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /2 items/i }))

    expect(screen.getByText('relation_type')).toBeInTheDocument()
    expect(screen.getByText('heat shock')).toBeInTheDocument()
    expect(screen.getByText('RNAi')).toBeInTheDocument()
  })

  it('renders nothing for empty values', () => {
    const { container } = renderSubTable(<SubTableFieldValue value={[]} />)

    expect(container.textContent).toBe('')
  })
})
