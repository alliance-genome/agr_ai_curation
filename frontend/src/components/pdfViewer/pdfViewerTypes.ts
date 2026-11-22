import type { EvidenceNavigationCommand } from '@/features/curation/evidence'
import type { PdfViewerNavigationResult } from './pdfEvidenceNavigation'
import type { ViewerFile } from './viewerFiles'

export const VIEWER_BASE_PATH = '/pdfjs/web/viewer.html?v=20260614-tohex-polyfill'

/** The paper in the viewer: its files with a tab, and the one shown. */
export interface ViewerDocument {
  documentId: string
  /** Every file with a tab (a content or text URL), in tab order; never empty. */
  files: ViewerFile[]
  activeFileId: string
  /** When the active file started loading; a new value reloads the PDF frame. */
  loadedAt: string
}

export function activeViewerFile(document: ViewerDocument): ViewerFile {
  const file = document.files.find((candidate) => candidate.file_id === document.activeFileId)
  if (!file) {
    throw new Error(`The viewer's active file ${document.activeFileId} is not one of its tabs`)
  }
  return file
}

export interface ViewerState {
  currentPage: number
  zoomLevel: number
  scrollPosition: number
  lastInteraction: string
}

export type ViewerSession = ViewerDocument & ViewerState

export type ViewerStatus = 'idle' | 'loading' | 'ready' | 'error'

export interface ViewerTelemetry {
  lastLoadMs: number | null
  slowLoad: boolean
}

export interface PdfViewerProps {
  activeDocumentOwnerToken?: string
  storageUserId?: string | null
  variant?: 'default' | 'curation'
  pendingNavigation?: EvidenceNavigationCommand | null
  onNavigationComplete?: () => void
  onNavigationStateChange?: (result: PdfViewerNavigationResult | null) => void
}

export const DEFAULT_STATE: ViewerState = {
  currentPage: 1,
  zoomLevel: 100,
  scrollPosition: 0,
  lastInteraction: new Date().toISOString(),
}
