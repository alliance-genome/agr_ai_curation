import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  HOME_PDF_VIEWER_OWNER,
  onPDFDocumentChanged,
  onPDFViewerDocumentCleared,
  showPaperFilesInViewer,
} from '@/components/pdfViewer/pdfEvents'
import {
  DOCUMENT_ID,
  FOUR_FILES_PAYLOAD,
  MAIN_FILE_ID,
  PDF_SUPPLEMENT_FILE_ID,
  SUPPLEMENTS_ONLY_PAYLOAD,
  TEXT_SUPPLEMENT_FILE_ID,
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
    expect(cleared).toHaveBeenCalledWith({ ownerToken: HOME_PDF_VIEWER_OWNER })
  })

  it('stops calling a listener once it is unregistered', () => {
    const handler = vi.fn()
    const unsubscribe = onPDFDocumentChanged(handler)
    unsubscribe()

    showPaperFilesInViewer(DOCUMENT_ID, FOUR_FILES_PAYLOAD.files)

    expect(handler).not.toHaveBeenCalled()
  })
})
