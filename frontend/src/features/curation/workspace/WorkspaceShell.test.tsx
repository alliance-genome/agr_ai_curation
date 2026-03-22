import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it } from 'vitest'

import theme from '@/theme'
import WorkspaceShell from './WorkspaceShell'

describe('WorkspaceShell', () => {
  it('renders the desktop workspace regions and resize handles', () => {
    render(
      <ThemeProvider theme={theme}>
        <WorkspaceShell
          editorSlot={<div>Editor slot</div>}
          evidenceSlot={<div>Evidence slot</div>}
          headerSlot={<div>Header slot</div>}
          pdfSlot={<div>PDF slot</div>}
          queueSlot={<div>Queue slot</div>}
          toolbarSlot={<div>Toolbar slot</div>}
        />
      </ThemeProvider>,
    )

    expect(screen.getByText('Header slot')).toBeInTheDocument()
    expect(screen.getByText('PDF slot')).toBeInTheDocument()
    expect(screen.getByText('Queue slot')).toBeInTheDocument()
    expect(screen.getByText('Toolbar slot')).toBeInTheDocument()
    expect(screen.getByText('Editor slot')).toBeInTheDocument()
    expect(screen.getByText('Evidence slot')).toBeInTheDocument()

    expect(screen.getByTestId('workspace-shell-pdf-panel')).toBeInTheDocument()
    expect(screen.getByTestId('workspace-shell-queue-panel')).toBeInTheDocument()
    expect(screen.getByTestId('workspace-shell-toolbar-panel')).toBeInTheDocument()
    expect(screen.getByTestId('workspace-shell-editor-panel')).toBeInTheDocument()
    expect(screen.getByTestId('workspace-shell-evidence-panel')).toBeInTheDocument()

    expect(screen.getByTestId('workspace-shell-handle-pdf-queue')).toBeInTheDocument()
    expect(screen.getByTestId('workspace-shell-handle-queue-editor')).toBeInTheDocument()
    expect(screen.getByTestId('workspace-shell-handle-editor-evidence')).toBeInTheDocument()
  })
})
