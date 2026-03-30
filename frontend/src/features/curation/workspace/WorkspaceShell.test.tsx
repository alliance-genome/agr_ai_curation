import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it } from 'vitest'

import theme from '@/theme'
import WorkspaceShell from './WorkspaceShell'

describe('WorkspaceShell', () => {
  it('renders the two-panel desktop layout with PDF and entity table', () => {
    render(
      <ThemeProvider theme={theme}>
        <WorkspaceShell
          headerSlot={<div>Header slot</div>}
          pdfSlot={<div>PDF slot</div>}
          entityTableSlot={<div>Entity table slot</div>}
        />
      </ThemeProvider>,
    )

    expect(screen.getByText('Header slot')).toBeInTheDocument()
    expect(screen.getByText('PDF slot')).toBeInTheDocument()
    expect(screen.getByText('Entity table slot')).toBeInTheDocument()

    expect(screen.getByTestId('workspace-shell-pdf-panel')).toBeInTheDocument()
    expect(screen.getByTestId('workspace-shell-entity-table-panel')).toBeInTheDocument()

    expect(screen.getByTestId('workspace-shell-handle-pdf-table')).toBeInTheDocument()
  })
})
