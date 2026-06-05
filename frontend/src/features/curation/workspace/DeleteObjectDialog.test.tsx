import userEvent from '@testing-library/user-event'
import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'

import theme from '@/theme'
import DeleteObjectDialog from './DeleteObjectDialog'

function renderDialog(
  props: Partial<React.ComponentProps<typeof DeleteObjectDialog>> = {},
) {
  const resolvedProps: React.ComponentProps<typeof DeleteObjectDialog> = {
    candidateLabel: 'pef-1',
    isDeleting: false,
    onCancel: vi.fn(),
    onConfirm: vi.fn(),
    open: true,
    ...props,
  }

  render(
    <ThemeProvider theme={theme}>
      <DeleteObjectDialog {...resolvedProps} />
    </ThemeProvider>,
  )

  return resolvedProps
}

describe('DeleteObjectDialog', () => {
  it('calls onConfirm after confirmation', async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn()
    renderDialog({ onConfirm })

    await user.click(screen.getByRole('button', { name: 'Delete object' }))

    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('calls onCancel from the cancel button', async () => {
    const user = userEvent.setup()
    const onCancel = vi.fn()
    renderDialog({ onCancel })

    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('disables actions while deleting', () => {
    renderDialog({ isDeleting: true })

    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Delete object' })).toBeDisabled()
  })
})
