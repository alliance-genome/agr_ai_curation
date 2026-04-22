import {
  HOME_PDF_VIEWER_OWNER,
  dispatchPDFDocumentChanged,
  type PDFViewerDocumentChangedDetail,
} from '@/components/pdfViewer/pdfEvents'
import { loadDocumentForChat } from '@/features/documents/pdfUploadFlow'
import type { ChatLocalStorageKeys } from '@/lib/chatCacheKeys'
import { normalizeChatHistoryValue } from '@/lib/chatHistoryNormalization'

export interface RehydratableChatDocument {
  id: string
  filename?: string | null
}

interface PdfViewerDocumentMetadata {
  filename?: unknown
  page_count?: unknown
}

interface PdfViewerDocumentUrlPayload {
  viewer_url?: unknown
}

export interface RehydrateChatDocumentOptions {
  document: RehydratableChatDocument
  chatStorageKeys: ChatLocalStorageKeys | null
  ensureLoadedForChat?: boolean
  ownerToken?: string
  viewerState?: PDFViewerDocumentChangedDetail['viewerState']
}

export interface RehydratedChatDocumentResult {
  viewerUrl: string
  filename: string
  pageCount: number
  loadedAt: string
}

export interface RehydrateFromSourceOptions {
  loadDocument: () => Promise<RehydratableChatDocument | null | undefined>
  chatStorageKeys: ChatLocalStorageKeys | null
  ownerToken?: string
  ensureLoadedForChat?: boolean
  onDocument?: (
    document: RehydratableChatDocument
  ) => Promise<boolean | void> | boolean | void
  onMissingDocument?: () => Promise<void> | void
}

function readPageCount(detail: PdfViewerDocumentMetadata): number | null {
  const rawPageCount = detail.page_count

  if (
    typeof rawPageCount !== 'number'
    || !Number.isFinite(rawPageCount)
    || rawPageCount < 1
  ) {
    return null
  }

  return rawPageCount
}

export async function rehydrateChatDocument(
  options: RehydrateChatDocumentOptions,
): Promise<RehydratedChatDocumentResult> {
  const {
    document,
    chatStorageKeys,
    ensureLoadedForChat = false,
    ownerToken = HOME_PDF_VIEWER_OWNER,
    viewerState,
  } = options

  if (chatStorageKeys) {
    localStorage.setItem(chatStorageKeys.activeDocument, JSON.stringify(document))
  }

  if (ensureLoadedForChat) {
    await loadDocumentForChat(document.id)
  }

  const [detailResponse, urlResponse] = await Promise.all([
    fetch(`/api/pdf-viewer/documents/${document.id}`),
    fetch(`/api/pdf-viewer/documents/${document.id}/url`),
  ])

  if (!detailResponse.ok || !urlResponse.ok) {
    throw new Error('Failed to fetch document viewer metadata')
  }

  const detail = await detailResponse.json() as PdfViewerDocumentMetadata
  const urlData = await urlResponse.json() as PdfViewerDocumentUrlPayload
  const viewerUrl = typeof urlData.viewer_url === 'string' ? urlData.viewer_url : null

  if (!viewerUrl) {
    throw new Error('Document viewer URL unavailable')
  }

  const filename = normalizeChatHistoryValue(detail.filename)
    ?? normalizeChatHistoryValue(document.filename)
  if (!filename) {
    throw new Error('Document filename unavailable')
  }

  const pageCount = readPageCount(detail)
  if (!pageCount) {
    throw new Error('Document page count unavailable')
  }

  const loadedAt = new Date().toISOString()

  if (chatStorageKeys) {
    localStorage.setItem(chatStorageKeys.pdfViewerSession, JSON.stringify({
      documentId: document.id,
      viewerUrl,
      filename,
      pageCount,
      loadedAt,
      currentPage: viewerState?.currentPage ?? 1,
      zoomLevel: 1,
      scrollPosition: viewerState?.scrollPosition ?? 0,
      lastInteraction: loadedAt,
    }))
  }

  dispatchPDFDocumentChanged(
    document.id,
    viewerUrl,
    filename,
    pageCount,
    {
      ownerToken,
      viewerState,
    },
  )

  return {
    viewerUrl,
    filename,
    pageCount,
    loadedAt,
  }
}

export async function rehydrateChatDocumentFromSource(
  options: RehydrateFromSourceOptions,
): Promise<RehydratableChatDocument | null> {
  const {
    loadDocument,
    chatStorageKeys,
    ownerToken = HOME_PDF_VIEWER_OWNER,
    ensureLoadedForChat = false,
    onDocument,
    onMissingDocument,
  } = options

  const document = await loadDocument()

  if (!document || !document.id) {
    await onMissingDocument?.()
    return null
  }

  const shouldContinue = await onDocument?.(document)
  if (shouldContinue === false) {
    return document
  }

  await rehydrateChatDocument({
    document,
    chatStorageKeys,
    ensureLoadedForChat,
    ownerToken,
  })

  return document
}
