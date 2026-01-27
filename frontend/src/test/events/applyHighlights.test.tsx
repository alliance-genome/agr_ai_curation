import { describe, expect, it, vi } from 'vitest'

import {
  dispatchApplyHighlights,
  dispatchClearHighlights,
  onApplyHighlights,
  onClearHighlights,
} from '@/components/pdfViewer/pdfEvents'

describe('highlight event contracts', () => {
  it('dispatchApplyHighlights emits highlights payload', () => {
    const listener = vi.fn()
    window.addEventListener('apply-highlights', listener as EventListener)

    const payload = {
      messageId: 'message-123',
      terms: ['plasma', 'fusion'],
      pages: [2, 3],
    }

    dispatchApplyHighlights(payload.messageId, payload.terms, payload.pages)

    expect(listener).toHaveBeenCalledTimes(1)
    const event = listener.mock.calls[0][0] as CustomEvent<typeof payload>
    expect(event.detail).toEqual(payload)

    window.removeEventListener('apply-highlights', listener as EventListener)
  })

  it('onApplyHighlights and onClearHighlights wire up typed handlers', () => {
    const highlightHandler = vi.fn()
    const clearHandler = vi.fn()

    const stopHighlight = onApplyHighlights((event) => highlightHandler(event.detail))
    const stopClear = onClearHighlights((event) => clearHandler(event.detail))

    dispatchApplyHighlights('message-abc', ['term'])
    expect(highlightHandler).toHaveBeenCalledWith({ messageId: 'message-abc', terms: ['term'] })

    dispatchClearHighlights('new-query')
    expect(clearHandler).toHaveBeenCalledWith({ reason: 'new-query' })

    stopHighlight()
    stopClear()

    highlightHandler.mockClear()
    clearHandler.mockClear()

    dispatchApplyHighlights('message-def', ['term'])
    dispatchClearHighlights('user-action')

    expect(highlightHandler).not.toHaveBeenCalled()
    expect(clearHandler).not.toHaveBeenCalled()
  })
})
