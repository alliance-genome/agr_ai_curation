import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DragEvent } from 'react'

import {
  dispatchChatDocumentChanged,
  loadDocumentForChat,
  uploadPdfDocument,
  validatePdfSelection,
  waitForDocumentProcessing,
} from '@/features/documents/pdfUploadFlow'
import { usePdfViewerUpload } from './usePdfViewerUpload'

vi.mock('@/features/documents/pdfUploadFlow', () => ({
  dispatchChatDocumentChanged: vi.fn(),
  loadDocumentForChat: vi.fn(),
  uploadPdfDocument: vi.fn(),
  validatePdfSelection: vi.fn(),
  waitForDocumentProcessing: vi.fn(),
}))

const pdfFile = new File(['%PDF-1.4'], 'paper.pdf', { type: 'application/pdf' })

const makeDropEvent = (files: File[] = [pdfFile]) => ({
  preventDefault: vi.fn(),
  stopPropagation: vi.fn(),
  dataTransfer: {
    files,
    dropEffect: 'none',
  },
  currentTarget: {
    contains: vi.fn(() => false),
  },
  relatedTarget: null,
}) as unknown as DragEvent<HTMLDivElement>

const makeDeferred = <T,>() => {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve
    reject = promiseReject
  })

  return { promise, resolve, reject }
}

describe('usePdfViewerUpload', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()

    vi.mocked(validatePdfSelection).mockImplementation((files) => ({
      ok: files.length > 0,
      files,
      error: files.length > 0 ? undefined : 'Please select a PDF file to upload.',
    }))
    vi.mocked(uploadPdfDocument).mockResolvedValue('doc-1')
    vi.mocked(waitForDocumentProcessing).mockImplementation(async (_documentId, options) => {
      options?.onProgress?.({
        stage: 'parsing',
        progress: 35,
        message: 'Parsing PDF...',
        final: false,
      })

      return {
        stage: 'completed',
        progress: 100,
        message: 'Processing completed successfully',
        final: true,
      }
    })
    vi.mocked(loadDocumentForChat).mockResolvedValue({
      active: true,
      document: {
        id: 'doc-1',
        filename: 'paper.pdf',
      },
    })
  })

  it('suppresses disabled drag events without activating the drop target', () => {
    const { result } = renderHook(() => usePdfViewerUpload({ disabled: true }))
    const event = makeDropEvent()

    act(() => {
      result.current.handleDragEnter(event)
    })

    expect(event.preventDefault).toHaveBeenCalled()
    expect(event.stopPropagation).toHaveBeenCalled()
    expect(result.current.dragActive).toBe(false)
    expect(result.current.dropError).toBeNull()
  })

  it('surfaces validation errors before upload starts', async () => {
    vi.mocked(validatePdfSelection).mockReturnValueOnce({
      ok: false,
      files: [],
      error: 'Please select PDF files only',
    })
    const { result } = renderHook(() => usePdfViewerUpload({ disabled: false }))

    act(() => {
      result.current.handleDrop(makeDropEvent([new File(['notes'], 'notes.txt', { type: 'text/plain' })]))
    })

    await waitFor(() => {
      expect(result.current.dropError).toBe('Please select PDF files only')
    })
    expect(uploadPdfDocument).not.toHaveBeenCalled()
  })

  it('loads the processed document for chat after a successful drop', async () => {
    const loadStartListener = vi.fn()
    window.addEventListener('document-load-start', loadStartListener)
    const { result } = renderHook(() => usePdfViewerUpload({ disabled: false }))

    act(() => {
      result.current.handleDrop(makeDropEvent())
    })

    await waitFor(() => {
      expect(uploadPdfDocument).toHaveBeenCalledWith(pdfFile)
    })
    await waitFor(() => {
      expect(loadDocumentForChat).toHaveBeenCalledWith('doc-1')
    })

    expect(sessionStorage.getItem('document-loading')).toBe('true')
    expect(loadStartListener).toHaveBeenCalledTimes(1)
    expect(dispatchChatDocumentChanged).toHaveBeenCalledWith({
      active: true,
      document: {
        id: 'doc-1',
        filename: 'paper.pdf',
      },
    })
    expect(result.current.uploadDialog).toMatchObject({
      open: true,
      stage: 'completed',
      progress: 100,
      message: 'Upload complete. Document loaded for chat.',
      documentId: 'doc-1',
    })

    window.removeEventListener('document-load-start', loadStartListener)
  })

  it('keeps progress hidden after the upload dialog is dismissed to the background', async () => {
    const uploadDeferred = makeDeferred<string>()
    vi.mocked(uploadPdfDocument).mockReturnValueOnce(uploadDeferred.promise)
    const { result } = renderHook(() => usePdfViewerUpload({ disabled: false }))

    act(() => {
      result.current.handleDrop(makeDropEvent())
    })

    await waitFor(() => {
      expect(result.current.uploadDialog).toMatchObject({
        open: true,
        dismissedToBackground: false,
        stage: 'uploading',
      })
    })

    act(() => {
      result.current.handleCloseUploadDialog()
    })

    expect(result.current.uploadDialog).toMatchObject({
      open: false,
      dismissedToBackground: true,
    })

    await act(async () => {
      uploadDeferred.resolve('doc-1')
      await uploadDeferred.promise
    })

    await waitFor(() => {
      expect(waitForDocumentProcessing).toHaveBeenCalledWith('doc-1', expect.any(Object))
    })
    await waitFor(() => {
      expect(result.current.uploadDialog).toMatchObject({
        open: false,
        dismissedToBackground: true,
        stage: 'completed',
        documentId: 'doc-1',
      })
    })
  })
})
