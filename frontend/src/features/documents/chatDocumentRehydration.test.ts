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
        return new Response(JSON.stringify({ viewer_url: '/viewer/doc-1.pdf', viewer_mode: 'local_pdf', has_main_file: true }), { status: 200 })
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
          has_main_file: true,
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
      { viewer_url: '/viewer/doc-pdf.pdf', viewer_mode: 'local_pdf', has_main_file: true },
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
        { viewer_url: '/viewer/doc-1.pdf', viewer_mode: viewerMode, has_main_file: true },
        { filename: 'paper.pdf', page_count: 3 },
      )

      await expect(rehydrateChatDocument({
        document: { id: 'doc-1', filename: 'paper.pdf' },
        chatStorageKeys: getChatLocalStorageKeys('user-1'),
      })).rejects.toThrow('Document viewer mode unavailable')
    },
  )

  // What the backend returns for a paper that holds supplements only (no main file):
  // /url and the detail both say has_main_file false, with every main-file field null.
  const NO_MAIN_FILE_URL = { viewer_url: null, viewer_mode: null, has_main_file: false }
  const NO_MAIN_FILE_DETAIL = {
    filename: 'supplements-only-paper',
    page_count: null,
    file_size: null,
    file_hash: null,
    viewer_url: null,
    viewer_mode: null,
    has_main_file: false,
  }

  it('keeps a paper without a main file active for chat with the viewer cleared', async () => {
    stubViewerApi(NO_MAIN_FILE_URL, NO_MAIN_FILE_DETAIL)
    const storageKeys = getChatLocalStorageKeys('user-1')
    localStorage.setItem(storageKeys.pdfViewerSession, JSON.stringify({
      documentId: 'old-doc', viewerUrl: '/viewer/old-doc.pdf', filename: 'old-doc.pdf', pageCount: 4,
    }))
    const documentChanged = vi.fn()
    const documentCleared = vi.fn()
    window.addEventListener('pdf-viewer-document-changed', documentChanged)
    window.addEventListener('pdf-viewer-document-cleared', documentCleared)

    await expect(rehydrateChatDocument({
      document: { id: 'doc-no-main', filename: 'supplements-only-paper' },
      chatStorageKeys: storageKeys,
    })).resolves.toMatchObject({
      viewerUrl: null,
      viewerMode: null,
      filename: 'supplements-only-paper',
      pageCount: null,
    })

    expect(documentChanged).not.toHaveBeenCalled()
    expect(documentCleared).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem(storageKeys.activeDocument)).toContain('doc-no-main')
    expect(localStorage.getItem(storageKeys.pdfViewerSession)).toBeNull()

    window.removeEventListener('pdf-viewer-document-changed', documentChanged)
    window.removeEventListener('pdf-viewer-document-cleared', documentCleared)
  })

  it('loads a paper without a main file for chat before restoring it', async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url.endsWith('/url')) return jsonResponse(NO_MAIN_FILE_URL)
      if (url.includes('/api/pdf-viewer/documents/')) return jsonResponse(NO_MAIN_FILE_DETAIL)
      return jsonResponse({ success: true, document: { id: 'doc-no-main', filename: 'supplements-only-paper' } })
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(rehydrateChatDocument({
      document: { id: 'doc-no-main', filename: 'supplements-only-paper' },
      chatStorageKeys: getChatLocalStorageKeys('user-1'),
      ensureLoadedForChat: true,
    })).resolves.toMatchObject({ viewerMode: null, pageCount: null })
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/pdf-viewer/documents/doc-no-main/url'))).toBe(true)
  })

  it.each([
    ['no has_main_file at all', { viewer_url: null, viewer_mode: null }],
    ['has_main_file not a boolean', { viewer_url: null, viewer_mode: null, has_main_file: 'false' }],
    ['no main file but a viewer mode', { viewer_url: null, viewer_mode: 'text', has_main_file: false }],
  ])('refuses an ambiguous viewer answer (%s)', async (_label, urlPayload) => {
    stubViewerApi(urlPayload, NO_MAIN_FILE_DETAIL)

    await expect(rehydrateChatDocument({
      document: { id: 'doc-no-main', filename: 'supplements-only-paper' },
      chatStorageKeys: getChatLocalStorageKeys('user-1'),
    })).rejects.toThrow('Document viewer mode unavailable')
  })

  it('restores a provider PDF paper to the viewer with its page count', async () => {
    stubViewerApi(
      { viewer_url: '/api/pdf-viewer/documents/doc-proxy/content', viewer_mode: 'provider_pdf_proxy', has_main_file: true },
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
    secondUrl.resolve(jsonResponse({ viewer_url: '/viewer/doc-b', viewer_mode: 'local_pdf', has_main_file: true }))
    await secondRestore

    firstDetail.resolve(jsonResponse({ filename: 'a.pdf', page_count: 1 }))
    firstUrl.resolve(jsonResponse({ viewer_url: '/viewer/doc-a', viewer_mode: 'local_pdf', has_main_file: true }))
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
