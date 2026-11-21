import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getChatLocalStorageKeys } from '@/lib/chatCacheKeys'
import { LatestIntent } from '@/lib/latestIntent'

import { rehydrateChatDocument } from './chatDocumentRehydration'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('chatDocumentRehydration', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.endsWith('/url')) {
        return new Response(JSON.stringify({ viewer_url: '/viewer/doc-1.pdf', viewer_mode: 'local_pdf' }), { status: 200 })
      }

      return new Response(JSON.stringify({
        filename: 'FBrf0265363_5753.pdf',
        page_count: 1,
      }), { status: 200 })
    }))
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('dispatches the PDF viewer restore event when localStorage quota is exceeded', async () => {
    const storageKeys = getChatLocalStorageKeys('user-1')
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('The quota has been exceeded.', 'QuotaExceededError')
    })
    const documentChanged = vi.fn()
    window.addEventListener('pdf-viewer-document-changed', documentChanged)

    await expect(rehydrateChatDocument({
      document: {
        id: 'doc-1',
        filename: 'fallback.pdf',
      },
      chatStorageKeys: storageKeys,
    })).resolves.toMatchObject({
      viewerUrl: '/viewer/doc-1.pdf',
      filename: 'FBrf0265363_5753.pdf',
      pageCount: 1,
    })

    expect(setItemSpy).toHaveBeenCalled()
    expect(documentChanged).toHaveBeenCalledTimes(1)
    expect((documentChanged.mock.calls[0][0] as CustomEvent).detail).toMatchObject({
      documentId: 'doc-1',
      viewerUrl: '/viewer/doc-1.pdf',
      filename: 'FBrf0265363_5753.pdf',
      pageCount: 1,
    })

    window.removeEventListener('pdf-viewer-document-changed', documentChanged)
  })

  it('keeps text-only documents active for chat without restoring the PDF viewer', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.endsWith('/url')) {
        return new Response(JSON.stringify({
          viewer_url: null,
          viewer_mode: 'text',
        }), { status: 200 })
      }

      // What the backend returns for a text paper since P5: no pages.
      return new Response(JSON.stringify({
        filename: 'provider-paper.md',
        page_count: null,
      }), { status: 200 })
    }))

    const storageKeys = getChatLocalStorageKeys('user-1')
    localStorage.setItem(storageKeys.pdfViewerSession, JSON.stringify({
      documentId: 'old-doc',
      viewerUrl: '/viewer/old-doc.pdf',
      filename: 'old-doc.pdf',
      pageCount: 4,
    }))
    const documentChanged = vi.fn()
    const documentCleared = vi.fn()
    window.addEventListener('pdf-viewer-document-changed', documentChanged)
    window.addEventListener('pdf-viewer-document-cleared', documentCleared)

    await expect(rehydrateChatDocument({
      document: {
        id: 'doc-text-only',
        filename: 'provider-paper.md',
      },
      chatStorageKeys: storageKeys,
    })).resolves.toMatchObject({
      viewerUrl: null,
      viewerMode: 'text',
      filename: 'provider-paper.md',
      pageCount: null,
    })

    expect(documentChanged).not.toHaveBeenCalled()
    expect(documentCleared).toHaveBeenCalledTimes(1)
    expect((documentCleared.mock.calls[0][0] as CustomEvent).detail).toEqual({ ownerToken: 'home' })
    expect(localStorage.getItem(storageKeys.activeDocument)).toContain('doc-text-only')
    expect(localStorage.getItem(storageKeys.pdfViewerSession)).toBeNull()

    window.removeEventListener('pdf-viewer-document-changed', documentChanged)
    window.removeEventListener('pdf-viewer-document-cleared', documentCleared)
  })

  function stubViewerApi(urlPayload: unknown, detailPayload: unknown) {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => (
      url.endsWith('/url') ? jsonResponse(urlPayload) : jsonResponse(detailPayload)
    )))
  }

  it('still refuses a PDF paper without a page count', async () => {
    stubViewerApi(
      { viewer_url: '/viewer/doc-pdf.pdf', viewer_mode: 'local_pdf' },
      { filename: 'paper.pdf', page_count: null },
    )
    const documentChanged = vi.fn()
    window.addEventListener('pdf-viewer-document-changed', documentChanged)

    await expect(rehydrateChatDocument({
      document: { id: 'doc-pdf', filename: 'paper.pdf' },
      chatStorageKeys: getChatLocalStorageKeys('user-1'),
    })).rejects.toThrow('Document page count unavailable')
    expect(documentChanged).not.toHaveBeenCalled()

    window.removeEventListener('pdf-viewer-document-changed', documentChanged)
  })

  it.each([undefined, null, 'text_only', 'TEXT'])(
    'refuses a viewer mode the backend does not report (%s)',
    async (viewerMode) => {
      stubViewerApi(
        { viewer_url: '/viewer/doc-1.pdf', viewer_mode: viewerMode },
        { filename: 'paper.pdf', page_count: 3 },
      )

      await expect(rehydrateChatDocument({
        document: { id: 'doc-1', filename: 'paper.pdf' },
        chatStorageKeys: getChatLocalStorageKeys('user-1'),
      })).rejects.toThrow('Document viewer mode unavailable')
    },
  )

  it('restores a provider PDF paper to the viewer with its page count', async () => {
    stubViewerApi(
      { viewer_url: '/api/pdf-viewer/documents/doc-proxy/content', viewer_mode: 'provider_pdf_proxy' },
      { filename: 'imported.pdf', page_count: 12 },
    )
    const documentChanged = vi.fn()
    window.addEventListener('pdf-viewer-document-changed', documentChanged)

    await expect(rehydrateChatDocument({
      document: { id: 'doc-proxy', filename: 'imported.pdf' },
      chatStorageKeys: getChatLocalStorageKeys('user-1'),
    })).resolves.toMatchObject({ viewerMode: 'provider_pdf_proxy', pageCount: 12 })
    expect((documentChanged.mock.calls[0][0] as CustomEvent).detail).toMatchObject({
      documentId: 'doc-proxy',
      pageCount: 12,
    })

    window.removeEventListener('pdf-viewer-document-changed', documentChanged)
  })

  it('keeps backend, cache, and viewer on the latest document after reverse completion', async () => {
    const storageKeys = getChatLocalStorageKeys('user-1')
    const firstDetail = deferred<Response>()
    const firstUrl = deferred<Response>()
    const secondDetail = deferred<Response>()
    const secondUrl = deferred<Response>()
    const loadRequests: RequestInit[] = []
    const documentChanged = vi.fn()
    window.addEventListener('pdf-viewer-document-changed', documentChanged)

    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/chat/document/load') {
        loadRequests.push(init ?? {})
        return Promise.resolve(new Response(JSON.stringify({ active: true }), { status: 200 }))
      }
      if (url === '/api/pdf-viewer/documents/doc-a') return firstDetail.promise
      if (url === '/api/pdf-viewer/documents/doc-a/url') return firstUrl.promise
      if (url === '/api/pdf-viewer/documents/doc-b') return secondDetail.promise
      if (url === '/api/pdf-viewer/documents/doc-b/url') return secondUrl.promise
      throw new Error(`Unexpected fetch: ${url}`)
    }))

    const intents = new LatestIntent()
    const firstOperation = intents.begin()
    const firstRestore = rehydrateChatDocument({
      document: { id: 'doc-a', filename: 'a.pdf' },
      chatStorageKeys: storageKeys,
      ensureLoadedForChat: true,
      operation: firstOperation,
    })
    await vi.waitFor(() => expect(loadRequests).toHaveLength(1))

    const secondOperation = intents.begin()
    const secondRestore = rehydrateChatDocument({
      document: { id: 'doc-b', filename: 'b.pdf' },
      chatStorageKeys: storageKeys,
      ensureLoadedForChat: true,
      operation: secondOperation,
    })
    await vi.waitFor(() => expect(loadRequests).toHaveLength(2))

    secondDetail.resolve(jsonResponse({ filename: 'b.pdf', page_count: 2 }))
    secondUrl.resolve(jsonResponse({ viewer_url: '/viewer/doc-b', viewer_mode: 'local_pdf' }))
    await secondRestore

    firstDetail.resolve(jsonResponse({ filename: 'a.pdf', page_count: 1 }))
    firstUrl.resolve(jsonResponse({ viewer_url: '/viewer/doc-a', viewer_mode: 'local_pdf' }))
    await expect(firstRestore).rejects.toMatchObject({ name: 'AbortError' })

    expect(firstOperation.signal.aborted).toBe(true)
    expect(JSON.parse(String(loadRequests[0].body))).toMatchObject({
      document_id: 'doc-a',
      intent_owner: firstOperation.owner,
      intent_generation: firstOperation.generation,
    })
    expect(JSON.parse(String(loadRequests[1].body))).toMatchObject({
      document_id: 'doc-b',
      intent_owner: secondOperation.owner,
      intent_generation: secondOperation.generation,
    })
    expect(localStorage.getItem(storageKeys.activeDocument)).toContain('doc-b')
    expect(localStorage.getItem(storageKeys.pdfViewerSession)).toContain('doc-b')
    expect(documentChanged).toHaveBeenCalledTimes(1)
    expect((documentChanged.mock.calls[0][0] as CustomEvent).detail.documentId).toBe('doc-b')

    window.removeEventListener('pdf-viewer-document-changed', documentChanged)
  })
})
