import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it } from 'vitest'

import theme from '@/theme'
import WorkspaceShell from './WorkspaceShell'

describe('WorkspaceShell', () => {
  it('renders the curation content shell with header, selector, and field editor', () => {
    render(
      <ThemeProvider theme={theme}>
        <WorkspaceShell
          headerSlot={<div>Header slot</div>}
          selectorSlot={<div>Object selector slot</div>}
          fieldEditorSlot={<div>Field editor slot</div>}
        />
      </ThemeProvider>,
    )

    expect(screen.getByText('Header slot')).toBeInTheDocument()
    expect(screen.getByText('Object selector slot')).toBeInTheDocument()
    expect(screen.getByText('Field editor slot')).toBeInTheDocument()

    expect(screen.getByTestId('workspace-shell-work-pane')).toBeInTheDocument()
    expect(screen.getByTestId('workspace-shell-selector')).toBeInTheDocument()
    expect(screen.getByTestId('workspace-shell-field-editor')).toBeInTheDocument()
  })
})
