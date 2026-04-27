import { useCallback, useEffect, useRef, useState, type DragEvent } from 'react'
import {
  dispatchChatDocumentChanged,
  loadDocumentForChat,
  uploadPdfDocument,
  validatePdfSelection,
  waitForDocumentProcessing,
} from '@/features/documents/pdfUploadFlow'

export interface UploadDialogState {
  open: boolean
  dismissedToBackground: boolean
  fileName: string
  stage: string
  progress: number
  message: string
  documentId?: string
}

interface UsePdfViewerUploadOptions {
  disabled: boolean
}

export const usePdfViewerUpload = ({ disabled }: UsePdfViewerUploadOptions) => {
  const uploadAbortRef = useRef<AbortController | null>(null)
  const [uploadInFlight, setUploadInFlight] = useState(false)
  const [dragActive, setDragActive] = useState(false)
  const [dropError, setDropError] = useState<string | null>(null)
  const [uploadDialog, setUploadDialog] = useState<UploadDialogState>({
    open: false,
    dismissedToBackground: false,
    fileName: '',
    stage: 'uploading',
    progress: 0,
    message: '',
  })

  const handleCloseUploadDialog = useCallback(() => {
    setUploadDialog((prev) => ({ ...prev, open: false, dismissedToBackground: true }))
  }, [])

  const clearDropState = useCallback(() => {
    setDragActive(false)
    setDropError(null)
  }, [])

  const suppressDragEvent = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.stopPropagation()
  }, [])

  const handleDroppedFiles = useCallback(async (files: File[]) => {
    if (uploadInFlight) {
      setDropError('An upload is already in progress. Please wait for it to finish.')
      return
    }

    const validation = validatePdfSelection(files, { allowMultiple: false, maxFiles: 1 })
    if (!validation.ok) {
      setDropError(validation.error ?? 'Please select PDF files only')
      return
    }

    const file = validation.files[0]
    const controller = new AbortController()
    uploadAbortRef.current = controller
    setDropError(null)
    setUploadInFlight(true)
    setUploadDialog({
      open: true,
      dismissedToBackground: false,
      fileName: file.name,
      stage: 'uploading',
      progress: 8,
      message: `Uploading “${file.name}”…`,
    })

    try {
      const documentId = await uploadPdfDocument(file)
      if (controller.signal.aborted) {
        return
      }

      setUploadDialog((prev) => ({
        ...prev,
        open: prev.dismissedToBackground ? false : true,
        documentId,
        stage: 'pending',
        progress: 12,
        message: 'Upload complete. Waiting for processing updates…',
      }))

      const finalProgress = await waitForDocumentProcessing(documentId, {
        signal: controller.signal,
        onProgress: (update) => {
          setUploadDialog((prev) => ({
            ...prev,
            open: prev.dismissedToBackground ? false : true,
            stage: update.stage,
            progress: update.progress,
            message: update.message,
            documentId,
          }))
        },
      })

      if (controller.signal.aborted) {
        return
      }

      if (finalProgress.stage !== 'completed') {
        setUploadDialog((prev) => ({
          ...prev,
          open: prev.dismissedToBackground ? false : true,
          stage: finalProgress.stage,
          progress: finalProgress.progress,
          message: finalProgress.message,
          documentId,
        }))
        return
      }

      sessionStorage.setItem('document-loading', 'true')
      window.dispatchEvent(new CustomEvent('document-load-start'))
      const payload = await loadDocumentForChat(documentId)
      dispatchChatDocumentChanged(payload)

      setUploadDialog((prev) => ({
        ...prev,
        open: prev.dismissedToBackground ? false : true,
        stage: 'completed',
        progress: 100,
        message: 'Upload complete. Document loaded for chat.',
        documentId,
      }))
    } catch (uploadError) {
      if (controller.signal.aborted) {
        return
      }

      setUploadDialog((prev) => ({
        ...prev,
        open: prev.dismissedToBackground ? false : true,
        stage: 'error',
        progress: 100,
        message: uploadError instanceof Error ? uploadError.message : 'Failed to upload PDF.',
      }))
    } finally {
      if (uploadAbortRef.current === controller) {
        uploadAbortRef.current = null
      }
      setUploadInFlight(false)
    }
  }, [uploadInFlight])

  const handleDragEnter = useCallback((event: DragEvent<HTMLDivElement>) => {
    suppressDragEvent(event)
    if (disabled || uploadInFlight) {
      return
    }
    setDropError(null)
    setDragActive(true)
  }, [disabled, suppressDragEvent, uploadInFlight])

  const handleDragOver = useCallback((event: DragEvent<HTMLDivElement>) => {
    suppressDragEvent(event)
    if (disabled || uploadInFlight) {
      return
    }
    event.dataTransfer.dropEffect = 'copy'
    setDragActive(true)
  }, [disabled, suppressDragEvent, uploadInFlight])

  const handleDragLeave = useCallback((event: DragEvent<HTMLDivElement>) => {
    suppressDragEvent(event)
    if (disabled || uploadInFlight) {
      return
    }
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) {
      return
    }
    setDragActive(false)
  }, [disabled, suppressDragEvent, uploadInFlight])

  const handleDrop = useCallback((event: DragEvent<HTMLDivElement>) => {
    suppressDragEvent(event)
    if (disabled || uploadInFlight) {
      return
    }
    setDragActive(false)
    const files = Array.from(event.dataTransfer.files ?? [])
    void handleDroppedFiles(files)
  }, [disabled, handleDroppedFiles, suppressDragEvent, uploadInFlight])

  useEffect(() => {
    return () => {
      uploadAbortRef.current?.abort()
      uploadAbortRef.current = null
    }
  }, [])

  return {
    uploadInFlight,
    dragActive,
    dropError,
    uploadDialog,
    handleCloseUploadDialog,
    handleDragEnter,
    handleDragOver,
    handleDragLeave,
    handleDrop,
    clearDropState,
  }
}
