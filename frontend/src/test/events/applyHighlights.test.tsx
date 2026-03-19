import { describe, expect, it, vi } from 'vitest'

import {
  dispatchApplyHighlights,
  dispatchClearSnippetLocalization,
  dispatchClearHighlights,
  dispatchLocateSnippet,
  dispatchSnippetLocalizationResult,
  onClearSnippetLocalization,
  onApplyHighlights,
  onClearHighlights,
  onLocateSnippet,
  onSnippetLocalizationResult,
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

  it('dispatches and listens for snippet localization events', () => {
    const locateHandler = vi.fn()
    const resultHandler = vi.fn()
    const clearHandler = vi.fn()

    const stopLocate = onLocateSnippet((event) => locateHandler(event.detail))
    const stopResult = onSnippetLocalizationResult((event) => resultHandler(event.detail))
    const stopClear = onClearSnippetLocalization((event) => clearHandler(event.detail))

    dispatchLocateSnippet('request-1', 'Exact sentence match', 2)
    dispatchSnippetLocalizationResult({
      requestId: 'request-1',
      snippet: 'Exact sentence match',
      status: 'success',
      durationMs: 18.4,
      renderedPages: [1, 2],
      renderedPageCount: 2,
      totalPageCount: 5,
      matchCount: 1,
      selectedMatchIndex: 0,
      selectedMatch: {
        index: 0,
        excerpt: 'Exact sentence match',
        pages: [1, 2],
        rectCount: 3,
        crossPage: true,
      },
      matches: [
        {
          index: 0,
          excerpt: 'Exact sentence match',
          pages: [1, 2],
          rectCount: 3,
          crossPage: true,
        },
      ],
    })
    dispatchClearSnippetLocalization('user-action')

    expect(locateHandler).toHaveBeenCalledWith({
      requestId: 'request-1',
      snippet: 'Exact sentence match',
      matchIndex: 2,
    })
    expect(resultHandler).toHaveBeenCalledWith({
      requestId: 'request-1',
      snippet: 'Exact sentence match',
      status: 'success',
      durationMs: 18.4,
      renderedPages: [1, 2],
      renderedPageCount: 2,
      totalPageCount: 5,
      matchCount: 1,
      selectedMatchIndex: 0,
      selectedMatch: {
        index: 0,
        excerpt: 'Exact sentence match',
        pages: [1, 2],
        rectCount: 3,
        crossPage: true,
      },
      matches: [
        {
          index: 0,
          excerpt: 'Exact sentence match',
          pages: [1, 2],
          rectCount: 3,
          crossPage: true,
        },
      ],
    })
    expect(clearHandler).toHaveBeenCalledWith({ reason: 'user-action' })

    stopLocate()
    stopResult()
    stopClear()
  })
})
