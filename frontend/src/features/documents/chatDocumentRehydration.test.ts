import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  DOCUMENT_ID,
  FOUR_FILES_PAYLOAD,
  MAIN_FILE_ID,
  SUPPLEMENTS_ONLY_PAYLOAD,
  TEXT_SUPPLEMENT_FILE_ID,
  singleMainPdfFile,
  singleMainTextFile,
} from '@/components/pdfViewer/viewerFilesFixtures'
import type { ViewerFile } from '@/components/pdfViewer/viewerFiles'
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

/** The viewer API for one paper: its detail (file name) and its files route. */
function stubViewerApi(documentId: string, files: unknown[], filename = 'paper.pdf') {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === `/api/pdf-viewer/documents/${documentId}/files`) {
      return jsonResponse({ document_id: documentId, files })
    }
    if (url === `/api/pdf-viewer/documents/${documentId}`) {
      return jsonResponse({ filename })
    }
    if (url === '/api/chat/document/load') {
      return jsonResponse({ success: true, document: { id: documentId, filename } })
    }
    throw new Error(`Unexpected fetch: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function listen(eventName: string) {
  const listener = vi.fn()
  window.addEventListener(eventName, listener)
  return {
    listener,
    detail: (call = 0) => (listener.mock.calls[call]![0] as CustomEvent).detail,
    remove: () => window.removeEventListener(eventName, listener),
  }
}

describe('chatDocumentRehydration (paper files)', () => {
  const removals: Array<() => void> = []
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    removals.splice(0).forEach((remove) => remove())
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('reads the files route and shows the paper\'s files, main file first', async () => {
    const fetchMock = stubViewerApi(DOCUMENT_ID, FOUR_FILES_PAYLOAD.files, 'made-up-paper.pdf')
    const changed = listen('pdf-viewer-document-changed')
    removals.push(changed.remove)

    await expect(rehydrateChatDocument({
      document: { id: DOCUMENT_ID, filename: 'made-up-paper.pdf' },
      chatStorageKeys: getChatLocalStorageKeys('user-1'),
    })).resolves.toMatchObject({ activeFileId: MAIN_FILE_ID, filename: 'made-up-paper.pdf' })

    expect(fetchMock.mock.calls.map(([url]) => url)).not.toContain(`/api/pdf-viewer/documents/${DOCUMENT_ID}/url`)
    expect(changed.listener).toHaveBeenCalledTimes(1)
    expect(changed.detail()).toMatchObject({ documentId: DOCUMENT_ID, activeFileId: MAIN_FILE_ID, ownerToken: 'home' })
    expect(changed.detail().files.map((file: ViewerFile) => file.display_name)).toEqual([
      'made-up-paper.pdf',
      'Table_S1.xlsx',
      'Figure_S2.pdf',
    ])
  })

  it('still shows the paper when localStorage quota is exceeded', async () => {
    stubViewerApi('doc-1', [singleMainPdfFile({ documentId: 'doc-1', displayName: 'FBrf0265363_5753.pdf', pageCount: 1 })], 'FBrf0265363_5753.pdf')
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('The quota has been exceeded.', 'QuotaExceededError')
    })
    const changed = listen('pdf-viewer-document-changed')
    removals.push(changed.remove)

    await expect(rehydrateChatDocument({
      document: { id: 'doc-1', filename: 'fallback.pdf' },
      chatStorageKeys: getChatLocalStorageKeys('user-1'),
    })).resolves.toMatchObject({ filename: 'FBrf0265363_5753.pdf', activeFileId: MAIN_FILE_ID })

    expect(setItemSpy).toHaveBeenCalled()
    expect(changed.listener).toHaveBeenCalledTimes(1)
  })

  // #1088 and #1089: a text paper restores with no page count, ends any PDF load and
  // never leaves the previous PDF in the viewer. Since P9b it shows its text tab.
  it('restores a text paper with its text file active, with no page count required', async () => {
    const textFile = singleMainTextFile({ documentId: 'doc-text-only', displayName: 'provider-paper.md' })
    stubViewerApi('doc-text-only', [textFile], 'provider-paper.md')
    const storageKeys = getChatLocalStorageKeys('user-1')
    localStorage.setItem(storageKeys.pdfViewerSession, JSON.stringify({
      documentId: 'old-doc', viewerUrl: '/viewer/old-doc.pdf', filename: 'old-doc.pdf', pageCount: 4,
    }))
    const changed = listen('pdf-viewer-document-changed')
    const cleared = listen('pdf-viewer-document-cleared')
    removals.push(changed.remove, cleared.remove)

    await expect(rehydrateChatDocument({
      document: { id: 'doc-text-only', filename: 'provider-paper.md' },
      chatStorageKeys: storageKeys,
    })).resolves.toMatchObject({ filename: 'provider-paper.md', activeFileId: textFile.file_id })

    expect(cleared.listener).not.toHaveBeenCalled()
    expect(changed.detail()).toMatchObject({ documentId: 'doc-text-only', activeFileId: textFile.file_id })
    expect(localStorage.getItem(storageKeys.activeDocument)).toContain('doc-text-only')
    // The stored viewer session takes the new shape; the old one is gone.
    const session = JSON.parse(localStorage.getItem(storageKeys.pdfViewerSession) ?? 'null')
    expect(session).toMatchObject({ documentId: 'doc-text-only', activeFileId: textFile.file_id })
    expect(session).not.toHaveProperty('viewerUrl')
  })

  // #1094: a paper that holds supplements only has no main file.
  it('restores a paper without a main file, showing its supplement', async () => {
    stubViewerApi(DOCUMENT_ID, SUPPLEMENTS_ONLY_PAYLOAD.files, 'supplements-only-paper')
    const changed = listen('pdf-viewer-document-changed')
    removals.push(changed.remove)

    await expect(rehydrateChatDocument({
      document: { id: DOCUMENT_ID, filename: 'supplements-only-paper' },
      chatStorageKeys: getChatLocalStorageKeys('user-1'),
    })).resolves.toMatchObject({ activeFileId: TEXT_SUPPLEMENT_FILE_ID })

    expect(changed.detail().activeFileId).toBe(TEXT_SUPPLEMENT_FILE_ID)
  })

  it('loads a paper without a main file for chat before restoring it', async () => {
    const fetchMock = stubViewerApi(DOCUMENT_ID, SUPPLEMENTS_ONLY_PAYLOAD.files, 'supplements-only-paper')

    await expect(rehydrateChatDocument({
      document: { id: DOCUMENT_ID, filename: 'supplements-only-paper' },
      chatStorageKeys: getChatLocalStorageKeys('user-1'),
      ensureLoadedForChat: true,
    })).resolves.toMatchObject({ activeFileId: TEXT_SUPPLEMENT_FILE_ID })
    const urls = fetchMock.mock.calls.map(([url]) => String(url))
    expect(urls.indexOf('/api/chat/document/load')).toBeLessThan(urls.indexOf(`/api/pdf-viewer/documents/${DOCUMENT_ID}/files`))
  })

  it('keeps the paper active for chat with the viewer cleared while no file has a URL yet', async () => {
    stubViewerApi(DOCUMENT_ID, [FOUR_FILES_PAYLOAD.files[3]], 'made-up-paper.pdf')
    const storageKeys = getChatLocalStorageKeys('user-1')
    localStorage.setItem(storageKeys.pdfViewerSession, JSON.stringify({ documentId: 'old-doc' }))
    const changed = listen('pdf-viewer-document-changed')
    const cleared = listen('pdf-viewer-document-cleared')
    removals.push(changed.remove, cleared.remove)

    await expect(rehydrateChatDocument({
      document: { id: DOCUMENT_ID, filename: 'made-up-paper.pdf' },
      chatStorageKeys: storageKeys,
    })).resolves.toMatchObject({ activeFileId: null })

    expect(changed.listener).not.toHaveBeenCalled()
    expect(cleared.detail()).toEqual({ ownerToken: 'home', notice: 'This paper\'s files are still being added.' })
    expect(localStorage.getItem(storageKeys.pdfViewerSession)).toBeNull()
  })

  it.each([
    ['a PDF file without a page count', { ...singleMainPdfFile({ documentId: 'doc-1' }), page_count: null }],
    ['a display mode the backend does not report', { ...singleMainPdfFile({ documentId: 'doc-1' }), display_mode: 'TEXT' }],
    ['a text file with a PDF URL', { ...singleMainTextFile({ documentId: 'doc-1' }), content_url: '/x' }],
  ])('refuses %s and shows nothing', async (_label, file) => {
    stubViewerApi('doc-1', [file])
    const changed = listen('pdf-viewer-document-changed')
    removals.push(changed.remove)

    await expect(rehydrateChatDocument({
      document: { id: 'doc-1', filename: 'paper.pdf' },
      chatStorageKeys: getChatLocalStorageKeys('user-1'),
    })).rejects.toThrow()
    expect(changed.listener).not.toHaveBeenCalled()
  })

  it('restores a provider PDF paper to the viewer', async () => {
    const proxied: ViewerFile = { ...singleMainPdfFile({ documentId: 'doc-proxy', displayName: 'imported.pdf' }), display_mode: 'provider_pdf_proxy' }
    stubViewerApi('doc-proxy', [proxied], 'imported.pdf')
    const changed = listen('pdf-viewer-document-changed')
    removals.push(changed.remove)

    await rehydrateChatDocument({
      document: { id: 'doc-proxy', filename: 'imported.pdf' },
      chatStorageKeys: getChatLocalStorageKeys('user-1'),
    })

    expect(changed.detail().files[0]).toMatchObject({ display_mode: 'provider_pdf_proxy', page_count: 12 })
  })

  it('keeps backend, cache, and viewer on the latest document after reverse completion', async () => {
    const storageKeys = getChatLocalStorageKeys('user-1')
    const firstDetail = deferred<Response>()
    const firstFiles = deferred<Response>()
    const secondDetail = deferred<Response>()
    const secondFiles = deferred<Response>()
    const loadRequests: RequestInit[] = []
    const changed = listen('pdf-viewer-document-changed')
    removals.push(changed.remove)

    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url === '/api/chat/document/load') {
        loadRequests.push(init ?? {})
        return Promise.resolve(new Response(JSON.stringify({ active: true }), { status: 200 }))
      }
      if (url === '/api/pdf-viewer/documents/doc-a') return firstDetail.promise
      if (url === '/api/pdf-viewer/documents/doc-a/files') return firstFiles.promise
      if (url === '/api/pdf-viewer/documents/doc-b') return secondDetail.promise
      if (url === '/api/pdf-viewer/documents/doc-b/files') return secondFiles.promise
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

    secondDetail.resolve(jsonResponse({ filename: 'b.pdf' }))
    secondFiles.resolve(jsonResponse({ document_id: 'doc-b', files: [singleMainPdfFile({ documentId: 'doc-b', pageCount: 2 })] }))
    await secondRestore

    firstDetail.resolve(jsonResponse({ filename: 'a.pdf' }))
    firstFiles.resolve(jsonResponse({ document_id: 'doc-a', files: [singleMainPdfFile({ documentId: 'doc-a', pageCount: 1 })] }))
    await expect(firstRestore).rejects.toMatchObject({ name: 'AbortError' })

    expect(firstOperation.signal.aborted).toBe(true)
    expect(JSON.parse(String(loadRequests[0]!.body))).toMatchObject({
      document_id: 'doc-a',
      intent_owner: firstOperation.owner,
      intent_generation: firstOperation.generation,
    })
    expect(JSON.parse(String(loadRequests[1]!.body))).toMatchObject({
      document_id: 'doc-b',
      intent_owner: secondOperation.owner,
      intent_generation: secondOperation.generation,
    })
    expect(localStorage.getItem(storageKeys.activeDocument)).toContain('doc-b')
    expect(localStorage.getItem(storageKeys.pdfViewerSession)).toContain('doc-b')
    expect(changed.listener).toHaveBeenCalledTimes(1)
    expect(changed.detail().documentId).toBe('doc-b')
  })
})
