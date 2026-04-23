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
})
