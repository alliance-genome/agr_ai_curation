import type { EvidenceNavigationCommand } from '@/features/curation/evidence'
import { defaultActiveFileId, parseViewerFiles, viewerTabFiles, type ViewerFile } from './viewerFiles'

export interface PDFViewerDocumentChangedDetail {
  documentId: string
  /** The paper's files with a tab (a content or text URL), in tab order; never empty. */
  files: ViewerFile[]
  /** The file to show; one of `files`. */
  activeFileId: string
  /** The paper has a main file the viewer cannot show (not ready, so no URL). */
  mainFileUnavailable: boolean
  /** Listed files without a tab, including their status for evidence navigation. */
  unavailableFiles: ViewerFile[]
  ownerToken?: string
  viewerState?: {
    currentPage?: number
    scrollPosition?: number
  }
}

export type PDFViewerDocumentChangedEvent = CustomEvent<PDFViewerDocumentChangedDetail>

export interface PDFViewerEvidenceAnchorSelectedDetail {
  anchorId: string
  documentId: string
  ownerToken: string | null
}

export type PDFViewerEvidenceAnchorSelectedEvent =
  CustomEvent<PDFViewerEvidenceAnchorSelectedDetail>

export interface PDFViewerNavigateEvidenceDetail {
  command: EvidenceNavigationCommand
}

export type PDFViewerNavigateEvidenceEvent =
  CustomEvent<PDFViewerNavigateEvidenceDetail>

export const HOME_PDF_VIEWER_OWNER = 'home'

export function buildCurationPDFViewerOwner(sessionId: string): string {
  return `curation:${sessionId}`
}

/** Approved by Chris (23 Nov 2026). */
export const MAIN_FILE_UNAVAILABLE_MESSAGE = 'This paper\'s main file is not available to show.'
export const FILES_NOT_READY_MESSAGE = 'This paper\'s files are still being added.'
export const ALL_FILES_FAILED_MESSAGE = "None of this paper's files could be added."

export interface PaperFilesViewerOptions {
  ownerToken?: string
  viewerState?: PDFViewerDocumentChangedDetail['viewerState']
}

/**
 * Show a paper's files in the viewer: one tab per file with a URL, the main file
 * first (or the first file with a URL). When no file has a URL yet (all being added,
 * or failed), the viewer is cleared instead. Returns whether a file is shown.
 */
export function showPaperFilesInViewer(
  documentId: string,
  servedFiles: readonly ViewerFile[],
  options?: PaperFilesViewerOptions,
): boolean {
  // The one parser for both paths (chat restore and the curation session's files).
  const files = parseViewerFiles(servedFiles)
  const tabFiles = viewerTabFiles(files)
  const activeFileId = defaultActiveFileId(tabFiles)
  const mainFileUnavailable = files.some((file) => file.role === 'main' && !tabFiles.includes(file))
  if (activeFileId === null) {
    let notice = FILES_NOT_READY_MESSAGE
    if (files.length > 0 && files.every((file) => file.status === 'failed')) {
      notice = ALL_FILES_FAILED_MESSAGE
    } else if (mainFileUnavailable) {
      notice = MAIN_FILE_UNAVAILABLE_MESSAGE
    }
    dispatchPDFViewerDocumentCleared(options?.ownerToken, notice)
    return false
  }
  window.dispatchEvent(
    new CustomEvent<PDFViewerDocumentChangedDetail>('pdf-viewer-document-changed', {
      detail: {
        documentId,
        files: tabFiles,
        activeFileId,
        mainFileUnavailable,
        unavailableFiles: files.filter((file) => !tabFiles.includes(file)),
        ownerToken: options?.ownerToken,
        viewerState: options?.viewerState,
      },
    }),
  )
  return true
}

export function onPDFDocumentChanged(
  handler: (event: PDFViewerDocumentChangedEvent) => void,
): () => void {
  const listener = (event: Event) => handler(event as PDFViewerDocumentChangedEvent)
  window.addEventListener('pdf-viewer-document-changed', listener)
  return () => window.removeEventListener('pdf-viewer-document-changed', listener)
}

export function dispatchPDFViewerEvidenceAnchorSelected(
  anchorId: string,
  documentId: string,
  ownerToken: string | null,
): void {
  window.dispatchEvent(
    new CustomEvent<PDFViewerEvidenceAnchorSelectedDetail>(
      'pdf-viewer-evidence-anchor-selected',
      {
        detail: { anchorId, documentId, ownerToken },
      },
    ),
  )
}

export function onPDFViewerEvidenceAnchorSelected(
  handler: (event: PDFViewerEvidenceAnchorSelectedEvent) => void,
): () => void {
  const listener = (event: Event) => handler(event as PDFViewerEvidenceAnchorSelectedEvent)
  window.addEventListener('pdf-viewer-evidence-anchor-selected', listener)
  return () => window.removeEventListener('pdf-viewer-evidence-anchor-selected', listener)
}

export function dispatchPDFViewerNavigateEvidence(
  command: EvidenceNavigationCommand,
): void {
  window.dispatchEvent(
    new CustomEvent<PDFViewerNavigateEvidenceDetail>('pdf-viewer-navigate-evidence', {
      detail: { command },
    }),
  )
}

export function onPDFViewerNavigateEvidence(
  handler: (event: PDFViewerNavigateEvidenceEvent) => void,
): () => void {
  const listener = (event: Event) => handler(event as PDFViewerNavigateEvidenceEvent)
  window.addEventListener('pdf-viewer-navigate-evidence', listener)
  return () => window.removeEventListener('pdf-viewer-navigate-evidence', listener)
}

export interface PDFViewerDocumentClearedDetail {
  ownerToken?: string
  /** What to tell the curator instead of the empty viewer (a paper with nothing to show yet). */
  notice?: string
}

/**
 * The active document has nothing for the viewer to show (unloaded, or no file of
 * the paper has a URL yet). The viewer ends any pending load and clears what it showed.
 */
export function dispatchPDFViewerDocumentCleared(ownerToken?: string, notice?: string): void {
  window.dispatchEvent(
    new CustomEvent<PDFViewerDocumentClearedDetail>('pdf-viewer-document-cleared', {
      detail: notice === undefined ? { ownerToken } : { ownerToken, notice },
    }),
  )
}

export function onPDFViewerDocumentCleared(
  handler: (event: CustomEvent<PDFViewerDocumentClearedDetail>) => void,
): () => void {
  const listener = (event: Event) => handler(event as CustomEvent<PDFViewerDocumentClearedDetail>)
  window.addEventListener('pdf-viewer-document-cleared', listener)
  return () => window.removeEventListener('pdf-viewer-document-cleared', listener)
}
