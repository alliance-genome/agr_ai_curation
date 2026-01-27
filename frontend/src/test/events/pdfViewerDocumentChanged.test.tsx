import { describe, expect, it, vi } from 'vitest'

import {
  dispatchPDFDocumentChanged,
  onPDFDocumentChanged,
} from '@/components/pdfViewer/pdfEvents'

const sampleEvent = {
  documentId: '11111111-2222-3333-4444-555555555555',
  viewerUrl: '/uploads/11111111-2222-3333-4444-555555555555/sample.pdf',
  filename: 'sample.pdf',
  pageCount: 12,
}

describe('pdf-viewer-document-changed event contract', () => {
  it('dispatches detail payload when helper is used', () => {
    const listener = vi.fn()
    window.addEventListener('pdf-viewer-document-changed', listener as EventListener)

    dispatchPDFDocumentChanged(
      sampleEvent.documentId,
      sampleEvent.viewerUrl,
      sampleEvent.filename,
      sampleEvent.pageCount,
    )

    expect(listener).toHaveBeenCalledTimes(1)
    const event = listener.mock.calls[0][0] as CustomEvent<typeof sampleEvent>
    expect(event.detail).toEqual(sampleEvent)

    window.removeEventListener('pdf-viewer-document-changed', listener as EventListener)
  })

  it('onPDFDocumentChanged registers and cleans up typed listener', () => {
    const handler = vi.fn()
    const unsubscribe = onPDFDocumentChanged((event) => {
      handler(event.detail)
    })

    dispatchPDFDocumentChanged(
      sampleEvent.documentId,
      sampleEvent.viewerUrl,
      sampleEvent.filename,
      sampleEvent.pageCount,
    )

    expect(handler).toHaveBeenCalledWith(sampleEvent)

    unsubscribe()
    handler.mockClear()

    dispatchPDFDocumentChanged(
      sampleEvent.documentId,
      sampleEvent.viewerUrl,
      sampleEvent.filename,
      sampleEvent.pageCount,
    )

    expect(handler).not.toHaveBeenCalled()
  })
})
