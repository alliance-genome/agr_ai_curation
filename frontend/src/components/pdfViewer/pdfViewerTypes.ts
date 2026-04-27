import type { EvidenceNavigationCommand } from '@/features/curation/evidence'
import type { PdfViewerNavigationResult } from './pdfEvidenceNavigation'

export const VIEWER_BASE_PATH = '/pdfjs/web/viewer.html'

export interface ViewerDocument {
  documentId: string
  viewerUrl: string
  filename: string
  pageCount: number
  loadedAt: string
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
  lastHighlightMs: number | null
  slowLoad: boolean
  slowHighlight: boolean
}

export interface PdfViewerProps {
  activeDocumentOwnerToken?: string
  storageUserId?: string | null
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
