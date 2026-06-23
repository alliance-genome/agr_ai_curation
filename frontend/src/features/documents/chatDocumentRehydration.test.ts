import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getChatLocalStorageKeys } from '@/lib/chatCacheKeys'

import { rehydrateChatDocument } from './chatDocumentRehydration'

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
})
