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
    onAcceptAllValidated: vi.fn(),
    onAddObject: vi.fn(),
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
    renderToolbar({ onAcceptAllValidated, onAddObject })

    await user.click(screen.getByRole('button', { name: /accept all validated/i }))
    await user.click(screen.getByRole('button', { name: /add object/i }))

    expect(onAcceptAllValidated).toHaveBeenCalledTimes(1)
    expect(onAddObject).toHaveBeenCalledTimes(1)
  })
})
