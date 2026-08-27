import { describe, expect, it, vi } from 'vitest'

import {
  dispatchClearHighlights,
  onClearHighlights,
} from '@/components/pdfViewer/pdfEvents'

describe('highlight event contracts', () => {
  it('onClearHighlights wires up a typed handler', () => {
    const clearHandler = vi.fn()

    const stopClear = onClearHighlights((event) => clearHandler(event.detail))

    dispatchClearHighlights('new-query')
    expect(clearHandler).toHaveBeenCalledWith({ reason: 'new-query' })

    stopClear()
    clearHandler.mockClear()

    dispatchClearHighlights('user-action')

    expect(clearHandler).not.toHaveBeenCalled()
  })
})
