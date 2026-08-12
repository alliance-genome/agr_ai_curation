import userEvent from '@testing-library/user-event'
import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'

import theme from '@/theme'
import WorkPaneToolbar from './WorkPaneToolbar'

function renderToolbar(
  props: Partial<React.ComponentProps<typeof WorkPaneToolbar>> = {},
) {
  const resolvedProps: React.ComponentProps<typeof WorkPaneToolbar> = {
    pendingCount: 2,
    totalCount: 5,
    validatedPendingCount: 1,
    validationCounts: {
      blocking: 2,
      openFindings: 3,
      stale: 1,
      validated: 7,
    },
    isPdfVisible: true,
    isValidatingAll: false,
    onAcceptAllValidated: vi.fn(),
    onAddObject: vi.fn(),
    onTogglePdf: vi.fn(),
    onValidateAll: vi.fn(),
    ...props,
  }

  render(
    <ThemeProvider theme={theme}>
      <WorkPaneToolbar {...resolvedProps} />
    </ThemeProvider>,
  )

  return resolvedProps
}

describe('WorkPaneToolbar', () => {
  it('shows total and pending counts', () => {
    renderToolbar({ totalCount: 5, pendingCount: 2 })

    expect(screen.getByText(/5 objects/)).toBeInTheDocument()
    expect(screen.getByText(/2 pending/)).toBeInTheDocument()
    expect(screen.getByLabelText('Authoritative validation summary')).toHaveTextContent(
      '7 validated · 2 blocking · 1 stale · 3 open findings',
    )
  })

  it('enables Accept all validated only when there are validated pending candidates', () => {
    renderToolbar({ validatedPendingCount: 2 })

    expect(screen.getByRole('button', { name: /accept all validated/i })).toBeEnabled()
  })

  it('disables Accept all validated when none are validated-pending', () => {
    renderToolbar({ validatedPendingCount: 0 })

    expect(screen.getByRole('button', { name: /accept all validated/i })).toBeDisabled()
  })

  it('calls toolbar actions', async () => {
    const user = userEvent.setup()
    const onAcceptAllValidated = vi.fn()
    const onAddObject = vi.fn()
    const onTogglePdf = vi.fn()
    const onValidateAll = vi.fn()
    renderToolbar({ onAcceptAllValidated, onAddObject, onTogglePdf, onValidateAll })

    await user.click(screen.getByRole('button', { name: /accept all validated/i }))
    await user.click(screen.getByRole('button', { name: /add object/i }))
    await user.click(screen.getByRole('button', { name: /focus grid/i }))
    await user.click(screen.getByRole('button', { name: /validate all/i }))

    expect(onAcceptAllValidated).toHaveBeenCalledTimes(1)
    expect(onAddObject).toHaveBeenCalledTimes(1)
    expect(onTogglePdf).toHaveBeenCalledTimes(1)
    expect(onValidateAll).toHaveBeenCalledTimes(1)
  })

  it('shows PDF restore and session validation progress states', () => {
    renderToolbar({ isPdfVisible: false, isValidatingAll: true })

    expect(screen.getByRole('button', { name: 'Show PDF' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Validating all…' })).toBeDisabled()
  })
})
