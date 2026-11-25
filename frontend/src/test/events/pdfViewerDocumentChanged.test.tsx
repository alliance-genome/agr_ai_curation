import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ALL_FILES_FAILED_MESSAGE,
  FILES_NOT_READY_MESSAGE,
  HOME_PDF_VIEWER_OWNER,
  MAIN_FILE_UNAVAILABLE_MESSAGE,
  PAPER_FILES_REFRESH_CHANNEL,
  PAPER_FILES_REFRESH_STORAGE_KEY,
  onPDFDocumentChanged,
  onPDFViewerDocumentCleared,
  onPaperFilesRefresh,
  publishPaperFilesRefresh,
  showPaperFilesInViewer,
} from '@/components/pdfViewer/pdfEvents'
import {
  DOCUMENT_ID,
  FOUR_FILES_PAYLOAD,
  MAIN_FILE_ID,
  PDF_SUPPLEMENT_FILE_ID,
  SUPPLEMENTS_ONLY_PAYLOAD,
  TEXT_SUPPLEMENT_FILE_ID,
  mainPdfFileNotReady,
} from '@/components/pdfViewer/viewerFilesFixtures'

describe('pdf-viewer-document-changed event contract (paper files)', () => {
  const cleanups: Array<() => void> = []
  afterEach(() => {
    cleanups.splice(0).forEach((cleanup) => cleanup())
  })

  it('sends the files with a tab, in tab order, with the main file active', () => {
    const handler = vi.fn()
    cleanups.push(onPDFDocumentChanged((event) => handler(event.detail)))

    expect(showPaperFilesInViewer(DOCUMENT_ID, FOUR_FILES_PAYLOAD.files)).toBe(true)

    expect(handler).toHaveBeenCalledTimes(1)
    const detail = handler.mock.calls[0]![0]
    expect(detail.documentId).toBe(DOCUMENT_ID)
    expect(detail.files.map((file: { file_id: string }) => file.file_id)).toEqual([
      MAIN_FILE_ID,
      TEXT_SUPPLEMENT_FILE_ID,
      PDF_SUPPLEMENT_FILE_ID,
    ])
    expect(detail.activeFileId).toBe(MAIN_FILE_ID)
    expect(detail.mainFileUnavailable).toBe(false)
    expect(detail).not.toHaveProperty('viewerUrl')
    expect(detail).not.toHaveProperty('pageCount')
  })

  it('opens the first supplement of a paper without a main file', () => {
    const handler = vi.fn()
    cleanups.push(onPDFDocumentChanged((event) => handler(event.detail)))

    showPaperFilesInViewer(DOCUMENT_ID, SUPPLEMENTS_ONLY_PAYLOAD.files)

    expect(handler.mock.calls[0]![0].activeFileId).toBe(TEXT_SUPPLEMENT_FILE_ID)
  })

  it('passes the owner token and the resume state through', () => {
    const handler = vi.fn()
    cleanups.push(onPDFDocumentChanged((event) => handler(event.detail)))

    showPaperFilesInViewer(DOCUMENT_ID, FOUR_FILES_PAYLOAD.files, {
      ownerToken: HOME_PDF_VIEWER_OWNER,
      viewerState: { scrollPosition: 240 },
    })

    expect(handler.mock.calls[0]![0]).toMatchObject({
      ownerToken: HOME_PDF_VIEWER_OWNER,
      viewerState: { scrollPosition: 240 },
    })
  })

  it('clears the viewer instead when no file of the paper has a URL yet', () => {
    const changed = vi.fn()
    const cleared = vi.fn()
    cleanups.push(onPDFDocumentChanged(changed))
    cleanups.push(onPDFViewerDocumentCleared((event) => cleared(event.detail)))

    const shown = showPaperFilesInViewer(DOCUMENT_ID, [FOUR_FILES_PAYLOAD.files[3]!], {
      ownerToken: HOME_PDF_VIEWER_OWNER,
    })

    expect(shown).toBe(false)
    expect(changed).not.toHaveBeenCalled()
    expect(cleared).toHaveBeenCalledWith({ ownerToken: HOME_PDF_VIEWER_OWNER, notice: FILES_NOT_READY_MESSAGE })
  })

  it('reports all files failed when the only file is a failed main file', () => {
    const cleared = vi.fn()
    cleanups.push(onPDFViewerDocumentCleared((event) => cleared(event.detail)))

    showPaperFilesInViewer(DOCUMENT_ID, [mainPdfFileNotReady('failed')])

    expect(cleared).toHaveBeenCalledWith({ ownerToken: undefined, notice: ALL_FILES_FAILED_MESSAGE })
  })

  it('keeps the main-file notice when a supplement is still pending', () => {
    const cleared = vi.fn()
    cleanups.push(onPDFViewerDocumentCleared((event) => cleared(event.detail)))

    showPaperFilesInViewer(DOCUMENT_ID, [mainPdfFileNotReady('failed'), FOUR_FILES_PAYLOAD.files[3]!])

    expect(cleared).toHaveBeenCalledWith({ ownerToken: undefined, notice: MAIN_FILE_UNAVAILABLE_MESSAGE })
  })

  it('shows the supplements and flags the main file when it is not ready', () => {
    const handler = vi.fn()
    cleanups.push(onPDFDocumentChanged((event) => handler(event.detail)))

    showPaperFilesInViewer(DOCUMENT_ID, [mainPdfFileNotReady(), ...FOUR_FILES_PAYLOAD.files.slice(1)])

    expect(handler.mock.calls[0]![0]).toMatchObject({ activeFileId: TEXT_SUPPLEMENT_FILE_ID, mainFileUnavailable: true })
  })

  it('propagates refreshOnlyIfOpen and never clears the viewer when no file has a URL during refresh', () => {
    const changed = vi.fn()
    const cleared = vi.fn()
    cleanups.push(onPDFDocumentChanged((event) => changed(event.detail)))
    cleanups.push(onPDFViewerDocumentCleared((event) => cleared(event.detail)))

    expect(
      showPaperFilesInViewer(DOCUMENT_ID, FOUR_FILES_PAYLOAD.files, { refreshOnlyIfOpen: true }),
    ).toBe(true)
    expect(changed).toHaveBeenCalledTimes(1)
    expect(changed.mock.calls[0]![0]).toMatchObject({
      documentId: DOCUMENT_ID,
      refreshOnlyIfOpen: true,
    })

    expect(
      showPaperFilesInViewer(DOCUMENT_ID, [FOUR_FILES_PAYLOAD.files[3]!], { refreshOnlyIfOpen: true }),
    ).toBe(false)
    expect(cleared).not.toHaveBeenCalled()
  })

  it('broadcasts cross-tab refresh via BroadcastChannel and falls back to localStorage storage events', () => {
    const received: Array<{ documentId: string; nonce: string }> = []
    const posted: Array<{ channel: string; payload: unknown }> = []
    const teardownOrder: string[] = []

    class FakeBroadcastChannel {
      static instances: FakeBroadcastChannel[] = []
      readonly name: string
      private listeners = new Set<(event: MessageEvent) => void>()

      constructor(name: string) {
        this.name = name
        FakeBroadcastChannel.instances.push(this)
      }

      addEventListener(type: string, listener: (event: MessageEvent) => void) {
        if (type === 'message') {
          this.listeners.add(listener)
        }
      }

      removeEventListener(type: string, listener: (event: MessageEvent) => void) {
        if (type === 'message') {
          this.listeners.delete(listener)
          teardownOrder.push('removeEventListener')
        }
      }

      postMessage(data: unknown) {
        posted.push({ channel: this.name, payload: data })
        for (const instance of FakeBroadcastChannel.instances) {
          if (instance !== this && instance.name === this.name) {
            instance.listeners.forEach((listener) => listener({ data } as MessageEvent))
          }
        }
      }

      close() {
        teardownOrder.push('close')
        this.listeners.clear()
        FakeBroadcastChannel.instances = FakeBroadcastChannel.instances.filter((item) => item !== this)
      }
    }

    vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel)
    try {
      const unsubscribe = onPaperFilesRefresh((signal) => {
        received.push({ documentId: signal.documentId, nonce: signal.nonce })
      })

      // Same-tab publish posts to BroadcastChannel, and our own tab ignores its own senderId.
      showPaperFilesInViewer(DOCUMENT_ID, FOUR_FILES_PAYLOAD.files, { refreshOnlyIfOpen: true })
      expect(posted).toHaveLength(1)
      expect(posted[0]).toMatchObject({
        channel: PAPER_FILES_REFRESH_CHANNEL,
        payload: { documentId: DOCUMENT_ID },
      })
      expect(received).toHaveLength(0)

      // Passing broadcast: false prevents ping-pong loops when applying a received refresh.
      showPaperFilesInViewer(DOCUMENT_ID, FOUR_FILES_PAYLOAD.files, {
        refreshOnlyIfOpen: true,
        broadcast: false,
      })
      expect(posted).toHaveLength(1)

      // Message from another tab (different senderId) IS delivered.
      const remoteChannel = new FakeBroadcastChannel(PAPER_FILES_REFRESH_CHANNEL)
      remoteChannel.postMessage({
        documentId: DOCUMENT_ID,
        timestamp: Date.now(),
        nonce: 'remote-nonce-1',
        senderId: 'other-tab',
      })
      remoteChannel.close()
      expect(received).toEqual([{ documentId: DOCUMENT_ID, nonce: 'remote-nonce-1' }])

      // StorageEvent from another tab is also delivered.
      window.dispatchEvent(
        new StorageEvent('storage', {
          key: PAPER_FILES_REFRESH_STORAGE_KEY,
          newValue: JSON.stringify({
            documentId: DOCUMENT_ID,
            timestamp: Date.now(),
            nonce: 'storage-nonce-2',
            senderId: 'other-tab-2',
          }),
        }),
      )
      expect(received).toEqual([
        { documentId: DOCUMENT_ID, nonce: 'remote-nonce-1' },
        { documentId: DOCUMENT_ID, nonce: 'storage-nonce-2' },
      ])

      // Unsubscribing removes the message listener before closing the BroadcastChannel.
      teardownOrder.length = 0
      unsubscribe()
      expect(teardownOrder).toEqual(['removeEventListener', 'close'])
    } finally {
      vi.unstubAllGlobals()
    }

    // When BroadcastChannel is absent, publishPaperFilesRefresh falls back to localStorage.
    vi.stubGlobal('BroadcastChannel', undefined)
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem')
    try {
      publishPaperFilesRefresh(DOCUMENT_ID)
      expect(setItemSpy).toHaveBeenCalledWith(
        PAPER_FILES_REFRESH_STORAGE_KEY,
        expect.stringContaining(`"documentId":"${DOCUMENT_ID}"`),
      )
    } finally {
      setItemSpy.mockRestore()
      vi.unstubAllGlobals()
    }
  })

  it('stops calling a listener once it is unregistered', () => {
    const handler = vi.fn()
    const unsubscribe = onPDFDocumentChanged(handler)
    unsubscribe()

    showPaperFilesInViewer(DOCUMENT_ID, FOUR_FILES_PAYLOAD.files)

    expect(handler).not.toHaveBeenCalled()
  })
})
