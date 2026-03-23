import userEvent from '@testing-library/user-event'
import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'

import theme from '@/theme'
import RevertButton from './RevertButton'

function renderButton(props: {
  canRevert: boolean
  onRevert: () => void
}) {
  return render(
    <ThemeProvider theme={theme}>
      <RevertButton {...props} />
    </ThemeProvider>,
  )
}

describe('RevertButton', () => {
  it('hides the action when the field does not differ from the AI seed', () => {
    const { container } = renderButton({
      canRevert: false,
      onRevert: vi.fn(),
    })

    expect(container).toBeEmptyDOMElement()
  })

  it('calls the revert callback when the action is available', async () => {
    const user = userEvent.setup()
    const onRevert = vi.fn()

    renderButton({
      canRevert: true,
      onRevert,
    })

    await user.click(screen.getByRole('button', { name: /revert to ai/i }))

    expect(onRevert).toHaveBeenCalledTimes(1)
  })
})
