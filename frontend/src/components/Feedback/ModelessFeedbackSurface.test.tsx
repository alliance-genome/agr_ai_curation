import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import ModelessFeedbackSurface from './ModelessFeedbackSurface'

describe('ModelessFeedbackSurface', () => {
  it('renders as a non-modal dialog without blocking the page behind it', () => {
    const behindClick = vi.fn()

    render(
      <>
        <button type="button" onClick={behindClick}>
          Underlying action
        </button>
        <ModelessFeedbackSurface open title="Floating Feedback" onClose={vi.fn()}>
          <input aria-label="Draft" defaultValue="draft text" />
        </ModelessFeedbackSurface>
      </>
    )

    const dialog = screen.getByRole('dialog', { name: 'Floating Feedback' })
    expect(dialog).toHaveAttribute('aria-modal', 'false')
    expect(document.querySelector('.MuiBackdrop-root')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Underlying action' }))

    expect(behindClick).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('Draft')).toHaveValue('draft text')
  })

  it('closes with Escape and the close control', () => {
    const onClose = vi.fn()

    render(
      <ModelessFeedbackSurface open title="Escapable Feedback" onClose={onClose}>
        Feedback form
      </ModelessFeedbackSurface>
    )

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: 'Close feedback popup' }))
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('keeps desktop drag movement inside the viewport bounds', () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 500 })
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 360 })

    render(
      <ModelessFeedbackSurface open title="Movable Feedback" onClose={vi.fn()}>
        Feedback form
      </ModelessFeedbackSurface>
    )

    const dialog = screen.getByTestId('modeless-feedback-surface')
    dialog.getBoundingClientRect = vi.fn(() => ({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 220,
      bottom: 120,
      width: 220,
      height: 120,
      toJSON: () => ({}),
    }))

    const handle = screen.getByRole('button', { name: 'Move feedback popup' })
    fireEvent.pointerDown(handle, { clientX: 10, clientY: 10, pointerId: 1 })
    fireEvent.pointerMove(window, { clientX: 1200, clientY: 900 })
    fireEvent.pointerUp(window)

    expect(dialog).toHaveStyle({
      left: '264px',
      top: '224px',
    })
  })
})
