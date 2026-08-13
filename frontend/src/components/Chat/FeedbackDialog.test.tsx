import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import FeedbackDialog from './FeedbackDialog'

describe('FeedbackDialog', () => {
  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
  })

  it('clears the close-reset timer on unmount', () => {
    vi.useFakeTimers()

    const { unmount } = render(
      <FeedbackDialog
        open={false}
        onClose={vi.fn()}
        sessionId="session-1"
        onSubmit={vi.fn().mockResolvedValue(undefined)}
      />
    )

    expect(vi.getTimerCount()).toBe(1)

    unmount()

    expect(vi.getTimerCount()).toBe(0)
  })

  it('clears the auto-close timer on unmount after a successful submit', async () => {
    vi.useFakeTimers()

    const onClose = vi.fn()
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    const { unmount } = render(
      <FeedbackDialog
        open
        onClose={onClose}
        sessionId="session-1"
        onSubmit={onSubmit}
      />
    )

    fireEvent.change(screen.getByPlaceholderText(/enter your detailed feedback here/i), {
      target: { value: 'Looks good' },
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /send/i }))
      await Promise.resolve()
    })

    expect(onSubmit).toHaveBeenCalledWith({
      session_id: 'session-1',
      curator_id: 'curator@example.com',
      feedback_text: 'Looks good',
      trace_ids: [],
    })

    const pendingTimersBeforeUnmount = vi.getTimerCount()
    expect(pendingTimersBeforeUnmount).toBeGreaterThanOrEqual(1)

    unmount()

    expect(vi.getTimerCount()).toBeLessThan(pendingTimersBeforeUnmount)

    vi.advanceTimersByTime(2000)

    expect(onClose).not.toHaveBeenCalled()
  })

  it('keeps the feedback draft mounted while underlying chat controls are used', () => {
    const behindClick = vi.fn()

    render(
      <>
        <button type="button" onClick={behindClick}>
          Underlying chat action
        </button>
        <FeedbackDialog
          open
          onClose={vi.fn()}
          sessionId="session-1"
          traceIds={['trace-1']}
          onSubmit={vi.fn().mockResolvedValue(undefined)}
        />
      </>
    )

    const dialog = screen.getByRole('dialog', { name: 'Provide Feedback' })
    expect(dialog).toHaveAttribute('aria-modal', 'false')
    expect(document.querySelector('.MuiBackdrop-root')).not.toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText(/enter your detailed feedback here/i), {
      target: { value: 'I need to inspect the chat while writing this.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Underlying chat action' }))

    expect(behindClick).toHaveBeenCalledTimes(1)
    expect(screen.getByPlaceholderText(/enter your detailed feedback here/i)).toHaveValue(
      'I need to inspect the chat while writing this.'
    )
  })

  it('uses feedback-specific labels for its move and close controls', () => {
    render(
      <FeedbackDialog
        open
        onClose={vi.fn()}
        sessionId="session-1"
        onSubmit={vi.fn().mockResolvedValue(undefined)}
      />
    )

    expect(screen.getByRole('button', { name: 'Move feedback popup' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Close feedback popup' })).toBeInTheDocument()
  })

  it('ignores the close control while feedback submission is in flight, then allows it after submission', async () => {
    let resolveSubmit!: () => void
    const onClose = vi.fn()
    const onSubmit = vi.fn(() => new Promise<void>((resolve) => {
      resolveSubmit = resolve
    }))

    render(
      <FeedbackDialog
        open
        onClose={onClose}
        sessionId="session-1"
        onSubmit={onSubmit}
      />
    )

    fireEvent.change(screen.getByPlaceholderText(/enter your detailed feedback here/i), {
      target: { value: 'Pending feedback' },
    })
    fireEvent.click(screen.getByRole('button', { name: /send/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Close feedback popup' }))

    expect(onClose).not.toHaveBeenCalled()

    await act(async () => {
      resolveSubmit()
      await Promise.resolve()
    })
    fireEvent.click(screen.getByRole('button', { name: 'Close feedback popup' }))

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('ignores Escape while feedback submission is in flight', async () => {
    let resolveSubmit!: () => void
    const onClose = vi.fn()
    const onSubmit = vi.fn(() => new Promise<void>((resolve) => {
      resolveSubmit = resolve
    }))

    render(
      <FeedbackDialog
        open
        onClose={onClose}
        sessionId="session-1"
        onSubmit={onSubmit}
      />
    )

    fireEvent.change(screen.getByPlaceholderText(/enter your detailed feedback here/i), {
      target: { value: 'Pending feedback' },
    })
    fireEvent.click(screen.getByRole('button', { name: /send/i }))
    fireEvent.keyDown(window, { key: 'Escape' })

    expect(onClose).not.toHaveBeenCalled()

    await act(async () => {
      resolveSubmit()
      await Promise.resolve()
    })
  })
})
