import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it } from 'vitest'

import theme from '@/theme'
import WorkspaceShell from './WorkspaceShell'

describe('WorkspaceShell', () => {
  it('renders the curation content shell with a single canonical work pane', () => {
    render(
      <ThemeProvider theme={theme}>
        <WorkspaceShell
          headerSlot={<div>Header slot</div>}
          workPaneSlot={<div>Horizontal grid slot</div>}
        />
      </ThemeProvider>,
    )

    expect(screen.getByText('Header slot')).toBeInTheDocument()
    expect(screen.getByText('Horizontal grid slot')).toBeInTheDocument()

    expect(screen.getByTestId('workspace-shell-work-pane')).toBeInTheDocument()
    expect(screen.getByTestId('workspace-shell-work-pane-content')).toBeInTheDocument()
    expect(screen.queryByTestId('workspace-shell-selector')).not.toBeInTheDocument()
    expect(screen.queryByTestId('workspace-shell-field-editor')).not.toBeInTheDocument()
  })
})
