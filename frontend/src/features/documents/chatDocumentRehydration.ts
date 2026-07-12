import {
  HOME_PDF_VIEWER_OWNER,
  dispatchPDFDocumentChanged,
  type PDFViewerDocumentChangedDetail,
} from '@/components/pdfViewer/pdfEvents'
import { loadDocumentForChat } from '@/features/documents/pdfUploadFlow'
import type { ChatLocalStorageKeys } from '@/lib/chatCacheKeys'
import { safeRemoveItem, safeSetJson } from '@/lib/browserStorage'
import { normalizeChatHistoryValue } from '@/lib/chatHistoryNormalization'
import type { LatestIntentOperation } from '@/lib/latestIntent'

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
  viewer_mode?: unknown
}

export interface RehydrateChatDocumentOptions {
  document: RehydratableChatDocument
  chatStorageKeys: ChatLocalStorageKeys | null
  ensureLoadedForChat?: boolean
  ownerToken?: string
  operation?: LatestIntentOperation
  viewerState?: PDFViewerDocumentChangedDetail['viewerState']
}

export interface RehydratedChatDocumentResult {
  viewerUrl: string | null
  viewerMode: string
  filename: string
  pageCount: number
  loadedAt: string
}

export interface RehydrateFromSourceOptions {
  loadDocument: () => Promise<RehydratableChatDocument | null | undefined>
  chatStorageKeys: ChatLocalStorageKeys | null
  ownerToken?: string
  ensureLoadedForChat?: boolean
  operation?: LatestIntentOperation
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
    operation,
    viewerState,
  } = options

  if (operation && !operation.ownsLatest()) {
    throw new DOMException('Document restore superseded', 'AbortError')
  }

  if (chatStorageKeys) {
    safeSetJson(() => window.localStorage, chatStorageKeys.activeDocument, document, {
      owner: 'chat',
      workflowCritical: true,
    })
  }

  if (ensureLoadedForChat) {
    await loadDocumentForChat(document.id, {
      signal: operation?.signal,
      intentOwner: operation?.owner,
      intentGeneration: operation?.generation,
    })
    if (operation && !operation.ownsLatest()) {
      throw new DOMException('Document restore superseded', 'AbortError')
    }
  }

  const [detailResponse, urlResponse] = await Promise.all([
    fetch(`/api/pdf-viewer/documents/${document.id}`, { signal: operation?.signal }),
    fetch(`/api/pdf-viewer/documents/${document.id}/url`, { signal: operation?.signal }),
  ])

  if (!detailResponse.ok || !urlResponse.ok) {
    throw new Error('Failed to fetch document viewer metadata')
  }

  const detail = await detailResponse.json() as PdfViewerDocumentMetadata
  const urlData = await urlResponse.json() as PdfViewerDocumentUrlPayload
  const viewerUrl = typeof urlData.viewer_url === 'string' ? urlData.viewer_url : null
  const viewerMode = typeof urlData.viewer_mode === 'string' && urlData.viewer_mode.trim()
    ? urlData.viewer_mode.trim().toLowerCase()
    : 'local_pdf'
  const isTextOnlyDocument = viewerMode === 'text_only'

  if (!viewerUrl && !isTextOnlyDocument) {
    throw new Error('Document viewer URL unavailable')
  }

  const metadataFilename = typeof detail.filename === 'string' ? detail.filename : null
  const filename = normalizeChatHistoryValue(metadataFilename)
    ?? normalizeChatHistoryValue(document.filename)
  if (!filename) {
    throw new Error('Document filename unavailable')
  }

  const pageCount = readPageCount(detail)
  if (!pageCount) {
    throw new Error('Document page count unavailable')
  }

  const loadedAt = new Date().toISOString()
  const result: RehydratedChatDocumentResult = {
    viewerUrl,
    viewerMode,
    filename,
    pageCount,
    loadedAt,
  }

  if (operation && !operation.ownsLatest()) {
    throw new DOMException('Document restore superseded', 'AbortError')
  }

  if (chatStorageKeys) {
    if (viewerUrl) {
      safeSetJson(() => window.localStorage, chatStorageKeys.pdfViewerSession, {
        documentId: document.id,
        viewerUrl,
        filename,
        pageCount,
        loadedAt,
        currentPage: viewerState?.currentPage ?? 1,
        zoomLevel: 1,
        scrollPosition: viewerState?.scrollPosition ?? 0,
        lastInteraction: loadedAt,
      }, {
        owner: 'pdf-viewer',
        workflowCritical: true,
      })
    } else {
      safeRemoveItem(() => window.localStorage, chatStorageKeys.pdfViewerSession, {
        owner: 'pdf-viewer',
        workflowCritical: true,
      })
    }
  }

  if (viewerUrl) {
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
  }

  return result
}

export async function rehydrateChatDocumentFromSource(
  options: RehydrateFromSourceOptions,
): Promise<RehydratableChatDocument | null> {
  const {
    loadDocument,
    chatStorageKeys,
    ownerToken = HOME_PDF_VIEWER_OWNER,
    ensureLoadedForChat = false,
    operation,
    onDocument,
    onMissingDocument,
  } = options

  const document = await loadDocument()

  if (operation && !operation.ownsLatest()) {
    return null
  }

  if (!document || !document.id) {
    if (!operation || operation.ownsLatest()) {
      await onMissingDocument?.()
    }
    return null
  }

  const shouldContinue = await onDocument?.(document)
  if (shouldContinue === false || (operation && !operation.ownsLatest())) {
    return document
  }

  await rehydrateChatDocument({
    document,
    chatStorageKeys,
    ensureLoadedForChat,
    ownerToken,
    operation,
  })

  return document
}
