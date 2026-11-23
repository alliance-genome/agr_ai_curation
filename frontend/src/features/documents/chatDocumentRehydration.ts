import {
  HOME_PDF_VIEWER_OWNER,
  showPaperFilesInViewer,
  type PDFViewerDocumentChangedDetail,
} from '@/components/pdfViewer/pdfEvents'
import {
  defaultActiveFileId,
  fetchViewerFiles,
  viewerTabFiles,
  type ViewerFile,
} from '@/components/pdfViewer/viewerFiles'
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
  /** Every file of the paper, in tab order (the viewer's files route). */
  files: ViewerFile[]
  /** The file the viewer shows; null when no file has a URL yet. */
  activeFileId: string | null
  filename: string
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

  // The files route is the one source for what the viewer shows, read fresh on each
  // restore: supplements can join a paper after chat stored it.
  const [detailResponse, filesPayload] = await Promise.all([
    fetch(`/api/pdf-viewer/documents/${document.id}`, { signal: operation?.signal }),
    fetchViewerFiles(document.id, operation?.signal),
  ])

  if (!detailResponse.ok) {
    throw new Error('Failed to fetch document viewer metadata')
  }

  const detail = await detailResponse.json() as PdfViewerDocumentMetadata
  const metadataFilename = typeof detail.filename === 'string' ? detail.filename : null
  const filename = normalizeChatHistoryValue(metadataFilename)
    ?? normalizeChatHistoryValue(document.filename)
  if (!filename) {
    throw new Error('Document filename unavailable')
  }

  const files = filesPayload.files
  const tabFiles = viewerTabFiles(files)
  const activeFileId = defaultActiveFileId(tabFiles)
  const loadedAt = new Date().toISOString()
  const result: RehydratedChatDocumentResult = {
    files,
    activeFileId,
    filename,
    loadedAt,
  }

  if (operation && !operation.ownsLatest()) {
    throw new DOMException('Document restore superseded', 'AbortError')
  }

  if (chatStorageKeys) {
    if (activeFileId !== null) {
      safeSetJson(() => window.localStorage, chatStorageKeys.pdfViewerSession, {
        documentId: document.id,
        files: tabFiles,
        activeFileId,
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

  // Every file with a URL gets a tab (a text paper shows its text tab, ending any PDF
  // load); a paper with no file to show yet clears the viewer.
  showPaperFilesInViewer(document.id, files, { ownerToken, viewerState })

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
