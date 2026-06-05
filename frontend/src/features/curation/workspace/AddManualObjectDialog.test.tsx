import userEvent from '@testing-library/user-event'
import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'

import theme from '@/theme'
import AddManualObjectDialog from './AddManualObjectDialog'

function renderDialog(
  props: Partial<React.ComponentProps<typeof AddManualObjectDialog>> = {},
) {
  const resolvedProps: React.ComponentProps<typeof AddManualObjectDialog> = {
    isCreating: false,
    onCancel: vi.fn(),
    onCreate: vi.fn(),
    open: true,
    ...props,
  }

  render(
    <ThemeProvider theme={theme}>
      <AddManualObjectDialog {...resolvedProps} />
    </ThemeProvider>,
  )

  return resolvedProps
}

describe('AddManualObjectDialog', () => {
  it('submits a manual object payload', async () => {
    const user = userEvent.setup()
    const onCreate = vi.fn()
    renderDialog({ onCreate })

    await user.type(screen.getByLabelText('Name'), 'manual gene')
    await user.click(screen.getByRole('combobox', { name: 'Type' }))
    await user.click(screen.getByRole('option', { name: 'gene' }))
    await user.type(screen.getByLabelText('Species'), 'NCBITaxon:6239')
    await user.type(screen.getByLabelText('Topic'), 'disease')
    await user.click(screen.getByRole('button', { name: 'Add object' }))

    expect(onCreate).toHaveBeenCalledWith({
      entity_name: 'manual gene',
      entity_type: 'ATP:0000005',
      species: 'NCBITaxon:6239',
      topic: 'disease',
    })
  })

  it('requires name and type before submit', async () => {
    const user = userEvent.setup()
    const onCreate = vi.fn()
    renderDialog({ onCreate })

    expect(screen.getByRole('button', { name: 'Add object' })).toBeDisabled()

    await user.type(screen.getByLabelText('Name'), 'manual gene')

    expect(screen.getByRole('button', { name: 'Add object' })).toBeDisabled()
    expect(onCreate).not.toHaveBeenCalled()
  })

  it('calls onCancel from the cancel button', async () => {
    const user = userEvent.setup()
    const onCancel = vi.fn()
    renderDialog({ onCancel })

    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(onCancel).toHaveBeenCalledTimes(1)
  })
})
