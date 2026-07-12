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
        return new Response(JSON.stringify({ viewer_url: '/viewer/doc-1.pdf' }), { status: 200 })
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
          viewer_mode: 'text_only',
        }), { status: 200 })
      }

      return new Response(JSON.stringify({
        filename: 'provider-paper.md',
        page_count: 1,
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
    window.addEventListener('pdf-viewer-document-changed', documentChanged)

    await expect(rehydrateChatDocument({
      document: {
        id: 'doc-text-only',
        filename: 'provider-paper.md',
      },
      chatStorageKeys: storageKeys,
    })).resolves.toMatchObject({
      viewerUrl: null,
      viewerMode: 'text_only',
      filename: 'provider-paper.md',
      pageCount: 1,
    })

    expect(documentChanged).not.toHaveBeenCalled()
    expect(localStorage.getItem(storageKeys.activeDocument)).toContain('doc-text-only')
    expect(localStorage.getItem(storageKeys.pdfViewerSession)).toBeNull()

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
    secondUrl.resolve(jsonResponse({ viewer_url: '/viewer/doc-b' }))
    await secondRestore

    firstDetail.resolve(jsonResponse({ filename: 'a.pdf', page_count: 1 }))
    firstUrl.resolve(jsonResponse({ viewer_url: '/viewer/doc-a' }))
    await expect(firstRestore).rejects.toMatchObject({ name: 'AbortError' })

    expect(firstOperation.signal.aborted).toBe(true)
    expect(JSON.parse(String(loadRequests[0].body))).toMatchObject({
      document_id: 'doc-a',
      intent_token: firstOperation.token,
    })
    expect(JSON.parse(String(loadRequests[1].body))).toMatchObject({
      document_id: 'doc-b',
      intent_token: secondOperation.token,
    })
    expect(localStorage.getItem(storageKeys.activeDocument)).toContain('doc-b')
    expect(localStorage.getItem(storageKeys.pdfViewerSession)).toContain('doc-b')
    expect(documentChanged).toHaveBeenCalledTimes(1)
    expect((documentChanged.mock.calls[0][0] as CustomEvent).detail.documentId).toBe('doc-b')

    window.removeEventListener('pdf-viewer-document-changed', documentChanged)
  })
})
