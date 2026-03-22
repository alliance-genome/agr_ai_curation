import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { debug, getEnvFlag } from '@/utils/env'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Paper,
  Stack,
  Typography,
} from '@mui/material'
import UploadProgressDialog from '@/components/weaviate/UploadProgressDialog'
import {
  dispatchChatDocumentChanged,
  loadDocumentForChat,
  uploadPdfDocument,
  validatePdfSelection,
  waitForDocumentProcessing,
} from '@/features/documents/pdfUploadFlow'

import {
  ApplyHighlightsEvent,
  ClearHighlightsEvent,
  HighlightSettingsChangedEvent,
  PDFViewerDocumentChangedEvent,
  onApplyHighlights,
  onClearHighlights,
  onHighlightSettingsChanged,
  onPDFDocumentChanged,
} from '@/components/pdfViewer/pdfEvents'
import type { EvidenceAnchor, EvidenceLocatorQuality } from '@/features/curation/contracts'
import type { EvidenceNavigationCommand } from '@/features/curation/evidence'

const VIEWER_BASE_PATH = '/pdfjs/web/viewer.html'
const SESSION_STORAGE_KEY = 'pdf-viewer-session'
const SETTINGS_STORAGE_KEY = 'pdf-viewer-settings'
const PDFJS_FIND_STATE_FOUND = 0
const PDFJS_FIND_STATE_NOT_FOUND = 1
const PDFJS_FIND_STATE_WRAPPED = 2
const PDFJS_FIND_STATE_PENDING = 3
const PDFJS_FIND_TIMEOUT_MS = 3500
const PDFJS_FIND_RESULT_SETTLE_MS = 75
const EVIDENCE_SPIKE_EVENT_NAME = 'pdf-viewer-evidence-spike'
const EVIDENCE_SPIKE_RESULT_EVENT_NAME = 'pdf-viewer-evidence-spike-result'
const EVIDENCE_SPIKE_FRAGMENT_WORDS = 24

interface HighlightSettings {
  highlightColor: string
  highlightOpacity: number
  clearOnNewQuery: boolean
}

interface ViewerDocument {
  documentId: string
  viewerUrl: string
  filename: string
  pageCount: number
  loadedAt: string
}

interface ViewerState {
  currentPage: number
  zoomLevel: number
  scrollPosition: number
  lastInteraction: string
}

type ViewerSession = ViewerDocument & ViewerState

type ViewerStatus = 'idle' | 'loading' | 'ready' | 'error'

interface ViewerTelemetry {
  lastLoadMs: number | null
  lastHighlightMs: number | null
  slowLoad: boolean
  slowHighlight: boolean
}

export interface OverlayDocItem {
  page?: number
  page_no?: number
  bbox?: {
    left: number
    top: number
    right: number
    bottom: number
    coord_origin?: string
  }
  doc_item_label?: string
  element_id?: string
}

export interface OverlayPayload {
  chunkId: string
  documentId?: string | null
  docItems: OverlayDocItem[]
}

type PdfEvidenceSpikeCandidateReason =
  | 'raw'
  | 'whitespace-normalized'
  | 'ascii-normalized'
  | 'search-text'
  | 'first-sentence'
  | 'leading-fragment'
  | 'trailing-fragment'
  | 'section-title'
  | 'section-path'

type PdfEvidenceSpikeStatus =
  | 'matched'
  | 'section-fallback'
  | 'page-fallback'
  | 'document-fallback'
  | 'not-found'
  | 'viewer-not-ready'

type PdfEvidenceSpikeStrategy =
  | PdfEvidenceSpikeCandidateReason
  | 'page-hint'
  | 'document'

export interface PdfEvidenceSpikeInput {
  quote: string
  pageNumber?: number | null
  pageNumbers?: number[]
  sectionTitle?: string | null
  sectionPath?: string[] | null
}

export interface PdfEvidenceSpikeCandidate {
  query: string
  reason: PdfEvidenceSpikeCandidateReason
}

export interface PdfViewerNavigationResult {
  status: PdfEvidenceSpikeStatus
  strategy: PdfEvidenceSpikeStrategy
  locatorQuality: EvidenceLocatorQuality
  degraded: boolean
  mode: EvidenceNavigationCommand['mode']
  documentId: string | null
  quote: string
  pageHints: number[]
  sectionTitle: string | null
  matchedQuery: string | null
  matchedPage: number | null
  matchesTotal: number
  currentMatch: number
  attemptedQueries: string[]
  note: string
}

export type PdfEvidenceSpikeResult = PdfViewerNavigationResult

export type OverlayDocItemDropReason = 'missing-page' | 'missing-bbox' | 'invalid-bbox'

export interface OverlayDocItemDropDiagnostic {
  index: number
  reason: OverlayDocItemDropReason
  page?: number
  page_no?: number
  bbox?: OverlayDocItem['bbox']
  invalidFields?: string[]
}

export interface OverlayDocItemInspection {
  normalizedDocItems: OverlayDocItem[]
  droppedItems: OverlayDocItemDropDiagnostic[]
}

interface UploadDialogState {
  open: boolean
  dismissedToBackground: boolean
  fileName: string
  stage: string
  progress: number
  message: string
  documentId?: string
}

export interface PdfViewerProps {
  pendingNavigation?: EvidenceNavigationCommand | null
  onNavigationComplete?: () => void
  onNavigationStateChange?: (result: PdfViewerNavigationResult | null) => void
}

const DEFAULT_SETTINGS: HighlightSettings = {
  highlightColor: '#1565c0',
  highlightOpacity: 0.65,
  clearOnNewQuery: true,
}

const DEFAULT_STATE: ViewerState = {
  currentPage: 1,
  zoomLevel: 100,
  scrollPosition: 0,
  lastInteraction: new Date().toISOString(),
}

declare global {
  interface Window {
    __pdfViewerEvidenceSpike?: (input: PdfEvidenceSpikeInput) => Promise<PdfEvidenceSpikeResult>
    __pdfViewerEvidenceSpikeLastResult?: PdfEvidenceSpikeResult | null
  }
}

const uniqueTerms = (terms: string[]): string[] => {
  const seen = new Set<string>()
  return terms.filter((term) => {
    const normalized = term.trim()
    if (!normalized) return false
    const key = normalized.toLowerCase()
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

const uniqueEvidenceSpikeCandidates = (candidates: PdfEvidenceSpikeCandidate[]): PdfEvidenceSpikeCandidate[] => {
  const seen = new Set<string>()
  return candidates.filter((candidate) => {
    const key = candidate.query.trim().toLowerCase()
    if (!key || seen.has(key)) {
      return false
    }
    seen.add(key)
    return true
  })
}

export const normalizeEvidenceSpikeText = (value: string): string => {
  return value
    .normalize('NFKC')
    .replace(/\u00ad/g, '')
    .replace(/[\u2010\u2011\u2012\u2013\u2014\u2212]/g, '-')
    .replace(/[\u2018\u2019\u201A\u201B]/g, "'")
    .replace(/[\u201C\u201D\u201E\u201F]/g, '"')
    .replace(/\u00a0/g, ' ')
    .replace(/\s*\n\s*/g, ' ')
    .replace(/[ \t\f\v\r]+/g, ' ')
    .replace(/\s+([,.;:!?])/g, '$1')
    .replace(/([([{])\s+/g, '$1')
    .replace(/\s+([)\]}])/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
}

const splitEvidenceSpikeWords = (value: string): string[] => {
  const normalized = normalizeEvidenceSpikeText(value)
  return normalized.length > 0 ? normalized.split(/\s+/).filter(Boolean) : []
}

const extractEvidenceSpikeSentence = (value: string): string | null => {
  const normalized = normalizeEvidenceSpikeText(value)
  const match = normalized.match(/^(.{40,}?[.!?])(?:\s|$)/)
  return match?.[1]?.trim() ?? null
}

export const normalizeEvidenceSpikePageHints = (input: Pick<PdfEvidenceSpikeInput, 'pageNumber' | 'pageNumbers'>): number[] => {
  const rawHints = [
    ...(Array.isArray(input.pageNumbers) ? input.pageNumbers : []),
    input.pageNumber,
  ]

  const seen = new Set<number>()
  return rawHints.reduce<number[]>((acc, value) => {
    const page = Number(value)
    if (!Number.isInteger(page) || page <= 0 || seen.has(page)) {
      return acc
    }
    seen.add(page)
    acc.push(page)
    return acc
  }, [])
}

export const buildEvidenceSpikeQuoteCandidates = (
  quote: string,
  options?: {
    searchText?: string | null
    normalizedText?: string | null
  },
): PdfEvidenceSpikeCandidate[] => {
  const trimmed = quote.trim()
  const trimmedSearchText = options?.searchText?.trim() ?? ''
  const trimmedNormalizedText = options?.normalizedText?.trim() ?? ''
  const baseQuote = trimmed || trimmedSearchText || trimmedNormalizedText

  if (!baseQuote) {
    return []
  }

  const whitespaceNormalized = baseQuote.replace(/\s+/g, ' ').trim()
  const asciiNormalized = normalizeEvidenceSpikeText(baseQuote)
  const normalizedCandidate = normalizeEvidenceSpikeText(trimmedNormalizedText) || asciiNormalized
  const normalizedSearchText = normalizeEvidenceSpikeText(trimmedSearchText)
  const fragmentSource = trimmed || baseQuote
  const firstSentence = extractEvidenceSpikeSentence(fragmentSource)
  const words = splitEvidenceSpikeWords(fragmentSource)

  const candidates: PdfEvidenceSpikeCandidate[] = [
    { query: baseQuote, reason: 'raw' },
    { query: whitespaceNormalized, reason: 'whitespace-normalized' },
    { query: normalizedCandidate, reason: 'ascii-normalized' },
  ]

  if (normalizedSearchText) {
    candidates.push({ query: normalizedSearchText, reason: 'search-text' })
  }

  if (firstSentence) {
    candidates.push({ query: firstSentence, reason: 'first-sentence' })
  }

  if (words.length > EVIDENCE_SPIKE_FRAGMENT_WORDS + 6) {
    candidates.push({
      query: words.slice(0, EVIDENCE_SPIKE_FRAGMENT_WORDS).join(' '),
      reason: 'leading-fragment',
    })
    candidates.push({
      query: words.slice(-EVIDENCE_SPIKE_FRAGMENT_WORDS).join(' '),
      reason: 'trailing-fragment',
    })
  }

  return uniqueEvidenceSpikeCandidates(candidates)
}

export const buildEvidenceSpikeSectionCandidates = (
  sectionTitle?: string | null,
  sectionPath?: string[] | null,
): PdfEvidenceSpikeCandidate[] => {
  const candidates: PdfEvidenceSpikeCandidate[] = []

  const normalizedTitle = normalizeEvidenceSpikeText(sectionTitle ?? '')
  if (normalizedTitle) {
    candidates.push({ query: normalizedTitle, reason: 'section-title' })
  }

  for (const segment of sectionPath ?? []) {
    const normalizedSegment = normalizeEvidenceSpikeText(segment)
    if (!normalizedSegment || normalizedSegment === normalizedTitle) {
      continue
    }
    candidates.push({ query: normalizedSegment, reason: 'section-path' })
  }

  return uniqueEvidenceSpikeCandidates(candidates)
}

const publishEvidenceSpikeResult = (result: PdfEvidenceSpikeResult) => {
  window.__pdfViewerEvidenceSpikeLastResult = result
  window.dispatchEvent(
    new CustomEvent<PdfEvidenceSpikeResult>(EVIDENCE_SPIKE_RESULT_EVENT_NAME, {
      detail: result,
    }),
  )
}

const getSelectedEvidenceSpikePage = (pdfApp: any): number | null => {
  const pageIdx = pdfApp?.findController?.selected?.pageIdx
  return typeof pageIdx === 'number' && pageIdx >= 0 ? pageIdx + 1 : null
}

const setEvidenceSpikePage = (pdfApp: any, pageNumber: number): boolean => {
  const normalizedPage = Math.max(1, Math.floor(pageNumber))

  try {
    if (pdfApp?.pdfViewer) {
      pdfApp.pdfViewer.currentPageNumber = normalizedPage
      return true
    }
    if (typeof pdfApp?.page === 'number') {
      pdfApp.page = normalizedPage
      return true
    }
  } catch (error) {
    console.warn('Unable to set PDF viewer page for evidence spike', error)
  }

  return false
}

const clearPdfJsFindHighlights = (pdfApp: any): void => {
  try {
    pdfApp?.eventBus?.dispatch?.('findbarclose', {
      source: 'pdf-evidence-navigation',
    })
  } catch (error) {
    console.warn('Unable to clear PDF.js find highlights', error)
  }
}

const isDegradedLocatorQuality = (quality: EvidenceLocatorQuality): boolean => {
  return quality === 'section_only'
    || quality === 'page_only'
    || quality === 'document_only'
    || quality === 'unresolved'
}

const resolveQuoteMatchLocatorQuality = (
  anchorQuality: EvidenceLocatorQuality,
  candidateReason: PdfEvidenceSpikeCandidateReason,
): EvidenceLocatorQuality => {
  // Quote fallback can degrade an exact anchor, but should not upgrade a
  // normalized anchor just because the first attempted query happens to match.
  if (anchorQuality === 'normalized_quote') {
    return 'normalized_quote'
  }

  return candidateReason === 'raw' ? 'exact_quote' : 'normalized_quote'
}

const buildEvidenceSpikeAnchor = (input: PdfEvidenceSpikeInput): EvidenceAnchor => {
  const rawQuote = input.quote?.trim() ?? ''
  return {
    anchor_kind: 'snippet',
    locator_quality: 'unresolved',
    supports_decision: 'neutral',
    snippet_text: rawQuote || null,
    normalized_text: rawQuote ? normalizeEvidenceSpikeText(rawQuote) : null,
    viewer_search_text: rawQuote || null,
    page_number: input.pageNumber ?? null,
    section_title: input.sectionTitle ?? null,
    chunk_ids: [],
  }
}

const buildNavigationCommandKey = (command: EvidenceNavigationCommand): string => {
  return JSON.stringify([
    command.anchor.anchor_kind,
    command.anchor.locator_quality,
    command.anchor.supports_decision,
    command.anchor.snippet_text ?? null,
    command.anchor.sentence_text ?? null,
    command.anchor.normalized_text ?? null,
    command.anchor.viewer_search_text ?? null,
    command.anchor.page_number ?? null,
    command.anchor.section_title ?? null,
    command.anchor.subsection_title ?? null,
    command.searchText ?? null,
    command.pageNumber ?? null,
    command.sectionTitle ?? null,
    command.mode,
    command.anchor.chunk_ids,
  ])
}

const getNavigationBadgeColor = (
  result: PdfViewerNavigationResult,
): 'error' | 'warning' | 'success' => {
  if (result.locatorQuality === 'unresolved') {
    return 'error'
  }

  return result.degraded ? 'warning' : 'success'
}

const formatLocatorQualityLabel = (quality: EvidenceLocatorQuality): string => {
  switch (quality) {
    case 'exact_quote':
      return 'Exact quote'
    case 'normalized_quote':
      return 'Approximate quote'
    case 'section_only':
      return 'Section fallback'
    case 'page_only':
      return 'Page fallback'
    case 'document_only':
      return 'Document only'
    case 'unresolved':
      return 'Unresolved'
    default:
      return quality
  }
}

interface PdfEvidenceSpikeFindOutcome extends Pick<PdfEvidenceSpikeResult, 'matchedPage' | 'matchesTotal' | 'currentMatch'> {
  found: boolean
  matchState: number | null
}

const isSuccessfulEvidenceSpikeFindState = (state: number | null | undefined): boolean => {
  return state === PDFJS_FIND_STATE_FOUND || state === PDFJS_FIND_STATE_WRAPPED
}

const waitForEvidenceSpikeFindResult = (pdfApp: any, query: string): Promise<PdfEvidenceSpikeFindOutcome> => {
  const eventBus = pdfApp?.eventBus

  if (!eventBus?.on || !eventBus?.off) {
    return Promise.resolve({
      matchedPage: getSelectedEvidenceSpikePage(pdfApp),
      matchesTotal: 0,
      currentMatch: 0,
      found: false,
      matchState: null,
    })
  }

  return new Promise((resolve) => {
    let latestCurrent = 0
    let latestTotal = 0
    let latestState: number | null = null
    let settleTimeoutId: number | null = null

    const finish = (detail?: { currentMatch?: number; matchesTotal?: number; matchedPage?: number | null; matchState?: number | null }) => {
      const matchState = detail?.matchState ?? latestState
      const found = isSuccessfulEvidenceSpikeFindState(matchState)
      const resolvedCurrent = detail?.currentMatch ?? latestCurrent
      const resolvedTotal = detail?.matchesTotal ?? latestTotal

      window.clearTimeout(timeoutId)
      if (settleTimeoutId !== null) {
        window.clearTimeout(settleTimeoutId)
      }
      eventBus.off('updatefindmatchescount', handleCount)
      eventBus.off('updatefindcontrolstate', handleState)
      resolve({
        matchedPage: detail?.matchedPage ?? getSelectedEvidenceSpikePage(pdfApp),
        matchesTotal: found && resolvedTotal === 0 ? 1 : resolvedTotal,
        currentMatch: found && resolvedCurrent === 0 ? 1 : resolvedCurrent,
        found,
        matchState,
      })
    }

    const handleCount = (event: any) => {
      if (event?.source !== pdfApp?.findController) {
        return
      }
      latestCurrent = event?.matchesCount?.current ?? latestCurrent
      latestTotal = event?.matchesCount?.total ?? latestTotal

      if (settleTimeoutId !== null && isSuccessfulEvidenceSpikeFindState(latestState) && latestTotal > 0) {
        finish()
      }
    }

    const handleState = (event: any) => {
      if (event?.source !== pdfApp?.findController || event?.rawQuery !== query) {
        return
      }
      latestState = typeof event?.state === 'number' ? event.state : latestState
      latestCurrent = event?.matchesCount?.current ?? latestCurrent
      latestTotal = event?.matchesCount?.total ?? latestTotal
      if (latestState === PDFJS_FIND_STATE_PENDING) {
        return
      }

      if (isSuccessfulEvidenceSpikeFindState(latestState)) {
        if (settleTimeoutId !== null) {
          window.clearTimeout(settleTimeoutId)
        }
        // PDF.js can report FOUND/WRAPPED before match counts settle. Give count
        // events a brief window to arrive before finalizing the outcome.
        settleTimeoutId = window.setTimeout(() => {
          finish({
            currentMatch: latestCurrent,
            matchesTotal: latestTotal,
            matchedPage: getSelectedEvidenceSpikePage(pdfApp),
            matchState: latestState,
          })
        }, PDFJS_FIND_RESULT_SETTLE_MS)
        return
      }

      if (latestState === PDFJS_FIND_STATE_NOT_FOUND) {
        finish({
          currentMatch: latestCurrent,
          matchesTotal: latestTotal,
          matchedPage: getSelectedEvidenceSpikePage(pdfApp),
          matchState: latestState,
        })
        return
      }

      finish({
        currentMatch: latestCurrent,
        matchesTotal: latestTotal,
        matchedPage: getSelectedEvidenceSpikePage(pdfApp),
        matchState: latestState,
      })
    }

    const timeoutId = window.setTimeout(() => {
      finish({
        currentMatch: latestCurrent,
        matchesTotal: latestTotal,
        matchedPage: getSelectedEvidenceSpikePage(pdfApp),
      })
    }, PDFJS_FIND_TIMEOUT_MS)

    eventBus.on('updatefindmatchescount', handleCount)
    eventBus.on('updatefindcontrolstate', handleState)
  })
}

const dispatchEvidenceSpikeFind = async (pdfApp: any, candidate: PdfEvidenceSpikeCandidate) => {
  const resultPromise = waitForEvidenceSpikeFindResult(pdfApp, candidate.query)
  pdfApp.eventBus.dispatch('find', {
    source: 'pdf-evidence-spike',
    type: '',
    query: candidate.query,
    caseSensitive: false,
    entireWord: false,
    highlightAll: true,
    findPrevious: false,
    matchDiacritics: false,
  })

  return resultPromise
}

const loadStoredSettings = (): HighlightSettings => {
  try {
    const raw = localStorage.getItem(SETTINGS_STORAGE_KEY)
    if (!raw) return DEFAULT_SETTINGS
    const parsed = JSON.parse(raw) as Partial<HighlightSettings>
    return {
      highlightColor: parsed.highlightColor ?? DEFAULT_SETTINGS.highlightColor,
      highlightOpacity: typeof parsed.highlightOpacity === 'number' ? parsed.highlightOpacity : DEFAULT_SETTINGS.highlightOpacity,
      clearOnNewQuery: typeof parsed.clearOnNewQuery === 'boolean' ? parsed.clearOnNewQuery : DEFAULT_SETTINGS.clearOnNewQuery,
    }
  } catch (error) {
    console.warn('Failed to load viewer settings', error)
    return DEFAULT_SETTINGS
  }
}

const persistSession = (doc: ViewerDocument, state: ViewerState) => {
  const session: ViewerSession = {
    ...doc,
    ...state,
    lastInteraction: new Date().toISOString(),
  }
  try {
    localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session))
  } catch (error) {
    console.warn('Unable to persist viewer session', error)
  }
}

const ensureMarkInjected = (iframeDoc: Document, settings: HighlightSettings) => {
  if (!iframeDoc.getElementById('mark-js-script')) {
    const script = iframeDoc.createElement('script')
    script.id = 'mark-js-script'
    script.src = 'https://cdn.jsdelivr.net/npm/mark.js@8.11.1/dist/mark.min.js'
    iframeDoc.head.appendChild(script)
  }

  let styleEl = iframeDoc.getElementById('pdf-highlight-styles') as HTMLStyleElement | null
  if (!styleEl) {
    styleEl = iframeDoc.createElement('style')
    styleEl.id = 'pdf-highlight-styles'
    iframeDoc.head.appendChild(styleEl)
  }

  styleEl.textContent = `
    mark.pdf-highlight {
      background-color: ${settings.highlightColor};
      color: inherit !important;
      padding: 0 !important;
      margin: 0 !important;
      border-radius: 2px;
      opacity: ${settings.highlightOpacity};
      mix-blend-mode: multiply;
    }
  `
}

const getTextLayers = (iframeDoc: Document, specificLayer?: HTMLElement): HTMLElement[] => {
  if (specificLayer) {
    return [specificLayer]
  }
  return Array.from(iframeDoc.querySelectorAll<HTMLElement>('.textLayer'))
}

const getInvalidBboxFields = (bbox: NonNullable<OverlayDocItem['bbox']>): string[] => {
  const invalidFields: string[] = []
  const left = Number(bbox.left)
  const top = Number(bbox.top)
  const right = Number(bbox.right)
  const bottom = Number(bbox.bottom)

  if (!Number.isFinite(left)) invalidFields.push('left')
  if (!Number.isFinite(top)) invalidFields.push('top')
  if (!Number.isFinite(right)) invalidFields.push('right')
  if (!Number.isFinite(bottom)) invalidFields.push('bottom')

  if (Number.isFinite(left) && Number.isFinite(right) && left === right) {
    invalidFields.push('zero-width')
  }
  if (Number.isFinite(top) && Number.isFinite(bottom) && top === bottom) {
    invalidFields.push('zero-height')
  }

  return invalidFields
}

export const inspectOverlayDocItems = (docItems: OverlayDocItem[] | undefined): OverlayDocItemInspection => {
  if (!Array.isArray(docItems)) {
    return {
      normalizedDocItems: [],
      droppedItems: [],
    }
  }

  return docItems.reduce<OverlayDocItemInspection>(
    (acc, item, index) => {
      const pageValue = typeof item.page === 'number' ? item.page : typeof item.page_no === 'number' ? item.page_no : undefined
      // PDF.js pages are 1-indexed, so page 0 is treated as invalid input.
      if (pageValue === undefined || !Number.isFinite(pageValue) || pageValue <= 0) {
        acc.droppedItems.push({
          index,
          reason: 'missing-page',
          page: item.page,
          page_no: item.page_no,
          bbox: item.bbox,
        })
        return acc
      }

      if (!item.bbox) {
        acc.droppedItems.push({
          index,
          reason: 'missing-bbox',
          page: item.page,
          page_no: item.page_no,
        })
        return acc
      }

      const invalidFields = getInvalidBboxFields(item.bbox)
      if (invalidFields.length > 0) {
        acc.droppedItems.push({
          index,
          reason: 'invalid-bbox',
          page: item.page,
          page_no: item.page_no,
          bbox: item.bbox,
          invalidFields,
        })
        return acc
      }

      acc.normalizedDocItems.push({
        ...item,
        page: pageValue,
      })
      return acc
    },
    {
      normalizedDocItems: [],
      droppedItems: [],
    },
  )
}

export const normalizeOverlayDocItems = (docItems: OverlayDocItem[] | undefined): OverlayDocItem[] => {
  return inspectOverlayDocItems(docItems).normalizedDocItems
}

export const reduceOverlayUpdate = (
  detail: OverlayPayload | null | undefined,
  activeDocumentId?: string | null,
  normalizedDocItemsInput?: OverlayDocItem[],
): OverlayPayload[] | null => {
  if (!detail) {
    return null
  }

  if (typeof detail.chunkId !== 'string' || detail.chunkId.trim().length === 0) {
    return null
  }

  const normalizedDocItems = normalizedDocItemsInput ?? normalizeOverlayDocItems(detail.docItems)
  if (normalizedDocItems.length === 0) {
    return []
  }

  // The viewer should track the most recently selected chunk only.
  // Retaining prior overlays is what made highlights appear stuck on older pages.
  return [
    {
      chunkId: detail.chunkId,
      documentId: detail.documentId ?? activeDocumentId ?? null,
      docItems: normalizedDocItems,
    },
  ]
}

export function PdfViewer({
  pendingNavigation = null,
  onNavigationComplete,
  onNavigationStateChange,
}: PdfViewerProps) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const pdfAppRef = useRef<any>(null)
  const cleanupRefs = useRef<(() => void)[]>([])
  const uploadAbortRef = useRef<AbortController | null>(null)
  const highlightTermsRef = useRef<string[]>([])
  const settingsRef = useRef<HighlightSettings>(DEFAULT_SETTINGS)
  const viewerStateRef = useRef<ViewerState>({ ...DEFAULT_STATE })
  const loadStartRef = useRef<number | null>(null)
  const handledNavigationKeyRef = useRef<string | null>(null)

  const [status, setStatus] = useState<ViewerStatus>('idle')
  const [activeDocument, setActiveDocument] = useState<ViewerDocument | null>(null)
  const [highlightTerms, setHighlightTerms] = useState<string[]>([])
  const [_highlightSettings, setHighlightSettings] = useState<HighlightSettings>(DEFAULT_SETTINGS)
  const [error, setError] = useState<string | null>(null)
  const [retryKey, setRetryKey] = useState(0)
  const [_telemetry, setTelemetry] = useState<ViewerTelemetry>({
    lastLoadMs: null,
    lastHighlightMs: null,
    slowLoad: false,
    slowHighlight: false,
  })
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
  const [overlays, setOverlays] = useState<OverlayPayload[]>([])
  const [overlayRenderKey, setOverlayRenderKey] = useState(0)
  const [navigationResult, setNavigationResult] = useState<PdfViewerNavigationResult | null>(null)

  const commitNavigationResult = useCallback((result: PdfViewerNavigationResult | null) => {
    setNavigationResult(result)
    onNavigationStateChange?.(result)
  }, [onNavigationStateChange])

  const logOverlayNormalizationDiagnostics = useCallback(
    (detail: OverlayPayload, inspection: OverlayDocItemInspection) => {
      if (inspection.droppedItems.length === 0) {
        return
      }

      const reasonCounts = inspection.droppedItems.reduce<Record<string, number>>((acc, item) => {
        acc[item.reason] = (acc[item.reason] ?? 0) + 1
        return acc
      }, {})

      console.warn('[PDF OVERLAY DIAGNOSTICS] Dropped highlight doc_items during normalization', {
        chunkId: detail.chunkId,
        documentId: detail.documentId ?? activeDocument?.documentId ?? null,
        activeDocumentId: activeDocument?.documentId ?? null,
        receivedDocItems: detail.docItems?.length ?? 0,
        normalizedDocItems: inspection.normalizedDocItems.length,
        droppedDocItems: inspection.droppedItems.length,
        reasonCounts,
        samples: inspection.droppedItems.slice(0, 3),
      })
    },
    [activeDocument?.documentId],
  )

  /**
   * Signal that document loading is complete (whether success or failure).
   * Clears the sessionStorage flag and dispatches the event to dismiss the loading overlay.
   */
  const signalLoadComplete = useCallback(() => {
    sessionStorage.removeItem('document-loading')
    window.dispatchEvent(new CustomEvent('document-load-complete'))
  }, [])

  const handleCloseUploadDialog = useCallback(() => {
    setUploadDialog((prev) => ({ ...prev, open: false, dismissedToBackground: true }))
  }, [])

  const suppressDragEvent = useCallback((event: React.DragEvent<HTMLDivElement>) => {
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

  const handleDragEnter = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    suppressDragEvent(event)
    if (activeDocument || uploadInFlight) {
      return
    }
    setDropError(null)
    setDragActive(true)
  }, [activeDocument, suppressDragEvent, uploadInFlight])

  const handleDragOver = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    suppressDragEvent(event)
    if (activeDocument || uploadInFlight) {
      return
    }
    event.dataTransfer.dropEffect = 'copy'
    setDragActive(true)
  }, [activeDocument, suppressDragEvent, uploadInFlight])

  const handleDragLeave = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    suppressDragEvent(event)
    if (activeDocument || uploadInFlight) {
      return
    }
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) {
      return
    }
    setDragActive(false)
  }, [activeDocument, suppressDragEvent, uploadInFlight])

  const handleDrop = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    suppressDragEvent(event)
    if (activeDocument || uploadInFlight) {
      return
    }
    setDragActive(false)
    const files = Array.from(event.dataTransfer.files ?? [])
    void handleDroppedFiles(files)
  }, [activeDocument, handleDroppedFiles, suppressDragEvent, uploadInFlight])

  useEffect(() => {
    const handleOverlayUpdate = (event: Event) => {
      const detail = (event as CustomEvent<OverlayPayload>).detail
      debug.log('🔍 [PDF VIEWER DEBUG] Received pdf-overlay-update event:', {
        hasDetail: !!detail,
        chunkId: detail?.chunkId,
        documentId: detail?.documentId,
        docItemsCount: detail?.docItems?.length || 0,
        activeDocumentId: activeDocument?.documentId,
        detail: detail
      })

      if (!detail) {
        debug.log('🔍 [PDF VIEWER DEBUG] No detail in event, skipping')
        return
      }

      if (detail.documentId && activeDocument?.documentId && detail.documentId !== activeDocument.documentId) {
        console.error('❌ [PDF VIEWER DEBUG] Document ID mismatch - OVERLAYS BLOCKED!', {
          receivedId: detail.documentId,
          activeId: activeDocument.documentId,
          match: detail.documentId === activeDocument.documentId,
          receivedType: typeof detail.documentId,
          activeType: typeof activeDocument.documentId
        })
        return
      }

      // Log successful pass-through
      debug.log('✅ [PDF VIEWER DEBUG] Document ID check passed, processing overlays', {
        receivedId: detail.documentId,
        activeId: activeDocument?.documentId
      })

      debug.log('🔍 [PDF VIEWER DEBUG] Processing doc items for normalization:', {
        rawCount: detail.docItems?.length || 0,
        firstThreeItems: detail.docItems?.slice(0, 3)
      })

      const inspection = inspectOverlayDocItems(detail.docItems)
      const normalizedDocItems = inspection.normalizedDocItems

      logOverlayNormalizationDiagnostics(detail, inspection)

      normalizedDocItems.forEach((item, idx) => {
        debug.log(`🔍 [PDF VIEWER DEBUG] Normalized item ${idx}:`, {
          page: item.page,
          bbox: item.bbox,
          label: item.doc_item_label
        })
      })

      debug.log('🔍 [PDF VIEWER DEBUG] Normalization complete:', {
        inputCount: detail.docItems?.length || 0,
        outputCount: normalizedDocItems.length,
        normalizedItems: normalizedDocItems.slice(0, 3) // First 3 for brevity
      })

      setOverlays((prev) => {
        const nextOverlays = reduceOverlayUpdate(detail, activeDocument?.documentId, normalizedDocItems)
        if (nextOverlays === null) {
          debug.log('🔍 [PDF VIEWER DEBUG] Invalid overlay payload, skipping:', detail)
          return prev
        }

        debug.log('🔍 [PDF VIEWER DEBUG] Updated overlays state:', {
          previousCount: prev.length,
          newCount: nextOverlays.length,
          chunkIds: nextOverlays.map(o => o.chunkId),
          totalDocItems: nextOverlays.reduce((sum, o) => sum + o.docItems.length, 0)
        })

        return nextOverlays
      })
      setOverlayRenderKey((prev) => prev + 1)
    }

    const handleOverlayClear = () => {
      debug.log('🔍 [PDF VIEWER DEBUG] Clearing all overlays (pdf-overlay-clear event)')
      setOverlays([])
      setOverlayRenderKey((prev) => prev + 1)
    }

    window.addEventListener('pdf-overlay-update', handleOverlayUpdate)
    window.addEventListener('pdf-overlay-clear', handleOverlayClear)

    return () => {
      window.removeEventListener('pdf-overlay-update', handleOverlayUpdate)
      window.removeEventListener('pdf-overlay-clear', handleOverlayClear)
    }
  }, [activeDocument?.documentId, logOverlayNormalizationDiagnostics])

  const viewerSrc = useMemo(() => {
    if (!activeDocument) return 'about:blank'
    const url = new URL(VIEWER_BASE_PATH, window.location.origin)
    url.searchParams.set('file', activeDocument.viewerUrl)
    // Append a cache-busting token so repeated loads of the same URL still trigger iframe reloads
    const cacheBustToken = activeDocument.loadedAt ?? Date.now().toString()
    url.searchParams.set('ts', cacheBustToken)
    const nextSrc = url.toString()
    console.debug('[PDF DEBUG] viewerSrc computed', {
      documentId: activeDocument.documentId,
      viewerUrl: activeDocument.viewerUrl,
      cacheBustToken,
      iframeSrc: nextSrc,
    })
    return nextSrc
  }, [activeDocument])

  const clearAllHighlights = useCallback(() => {
    const iframeWindow = iframeRef.current?.contentWindow as any
    const iframeDoc = iframeWindow?.document as Document | undefined
    if (!iframeDoc || !iframeWindow?.Mark) return

    const textLayers = getTextLayers(iframeDoc)
    textLayers.forEach((layer) => {
      const markInstance = new iframeWindow.Mark(layer)
      markInstance.unmark()
    })
  }, [])

  const executeEvidenceNavigation = useCallback(async (
    command: EvidenceNavigationCommand,
    options?: {
      pageHints?: number[]
      sectionPath?: string[]
    },
  ): Promise<PdfViewerNavigationResult> => {
    const anchor = command.anchor
    const quote = (
      anchor.snippet_text
      ?? anchor.sentence_text
      ?? command.searchText
      ?? anchor.viewer_search_text
      ?? ''
    ).trim()
    const searchText = (
      command.searchText
      ?? anchor.viewer_search_text
      ?? ''
    ).trim()
    const pageHints = normalizeEvidenceSpikePageHints({
      pageNumbers: options?.pageHints,
      pageNumber: command.pageNumber ?? anchor.page_number ?? null,
    })
    const sectionTitle = normalizeEvidenceSpikeText(command.sectionTitle ?? anchor.section_title ?? '') || null
    const sectionPath = uniqueTerms(
      [
        anchor.subsection_title,
        ...(options?.sectionPath ?? []),
      ]
        .map((segment) => normalizeEvidenceSpikeText(segment ?? ''))
        .filter(Boolean),
    )
    const attemptedQueries: string[] = []
    const baseResult = {
      documentId: activeDocument?.documentId ?? null,
      quote,
      pageHints,
      sectionTitle,
      attemptedQueries,
      mode: command.mode,
    }
    const iframeWindow = iframeRef.current?.contentWindow as any
    const pdfApp = pdfAppRef.current ?? iframeWindow?.PDFViewerApplication ?? null

    if (pdfApp && pdfAppRef.current !== pdfApp) {
      pdfAppRef.current = pdfApp
    }

    if (!pdfApp?.eventBus || !pdfApp?.findController || !pdfApp?.pdfViewer) {
      return {
        ...baseResult,
        status: 'viewer-not-ready',
        strategy: 'document',
        locatorQuality: anchor.locator_quality,
        degraded: isDegradedLocatorQuality(anchor.locator_quality),
        matchedQuery: null,
        matchedPage: null,
        matchesTotal: 0,
        currentMatch: 0,
        note: 'The PDF viewer is not ready yet. Load a PDF and wait for the iframe viewer to finish initialising.',
      }
    }

    clearPdfJsFindHighlights(pdfApp)

    const quoteCandidates = searchText
      ? buildEvidenceSpikeQuoteCandidates(searchText, {
          searchText,
          normalizedText: anchor.normalized_text ?? null,
        })
      : []
    const preferredPage = pageHints[0] ?? null

    for (const candidate of quoteCandidates) {
      if (preferredPage !== null) {
        setEvidenceSpikePage(pdfApp, preferredPage)
      }
      attemptedQueries.push(candidate.query)
      const outcome = await dispatchEvidenceSpikeFind(pdfApp, candidate)
      if (outcome.found || outcome.matchesTotal > 0) {
        const locatorQuality = resolveQuoteMatchLocatorQuality(anchor.locator_quality, candidate.reason)
        return {
          ...baseResult,
          status: 'matched',
          strategy: candidate.reason,
          locatorQuality,
          degraded: isDegradedLocatorQuality(locatorQuality),
          matchedQuery: candidate.query,
          matchedPage: outcome.matchedPage,
          matchesTotal: outcome.matchesTotal,
          currentMatch: outcome.currentMatch,
          note: candidate.reason.includes('fragment')
            ? 'Resolved with a shortened quote fragment after the full PDFX quote did not produce a direct text-layer match.'
            : 'Resolved with a quote-derived search candidate in the PDF.js text layer.',
        }
      }
    }

    const sectionCandidates = buildEvidenceSpikeSectionCandidates(sectionTitle, sectionPath)
    for (const candidate of sectionCandidates) {
      if (preferredPage !== null) {
        setEvidenceSpikePage(pdfApp, preferredPage)
      }
      attemptedQueries.push(candidate.query)
      const outcome = await dispatchEvidenceSpikeFind(pdfApp, candidate)
      if (outcome.found || outcome.matchesTotal > 0) {
        return {
          ...baseResult,
          status: 'section-fallback',
          strategy: candidate.reason,
          locatorQuality: 'section_only',
          degraded: true,
          matchedQuery: candidate.query,
          matchedPage: outcome.matchedPage,
          matchesTotal: outcome.matchesTotal,
          currentMatch: outcome.currentMatch,
          note: 'The quote itself did not match, but section metadata located a relevant page in the PDF viewer.',
        }
      }
    }

    if (preferredPage !== null) {
      setEvidenceSpikePage(pdfApp, preferredPage)
      clearPdfJsFindHighlights(pdfApp)
      return {
        ...baseResult,
        status: 'page-fallback',
        strategy: 'page-hint',
        locatorQuality: 'page_only',
        degraded: true,
        matchedQuery: null,
        matchedPage: preferredPage,
        matchesTotal: 0,
        currentMatch: 0,
        note: 'Navigated to the hinted page because quote and section search did not resolve a reliable text-layer match.',
      }
    }

    const documentQuality = anchor.locator_quality === 'document_only'
      ? 'document_only'
      : 'unresolved'

    clearPdfJsFindHighlights(pdfApp)

    return {
      ...baseResult,
      status: documentQuality === 'document_only' ? 'document-fallback' : 'not-found',
      strategy: 'document',
      locatorQuality: documentQuality,
      degraded: true,
      matchedQuery: null,
      matchedPage: null,
      matchesTotal: 0,
      currentMatch: 0,
      note: documentQuality === 'document_only'
        ? 'Opened the document without a precise page or text target because this anchor is intentionally document-scoped.'
        : 'The PDF viewer could not localize this evidence to quote, section, or page-level text in the current document.',
    }
  }, [activeDocument?.documentId])

  const executeEvidenceSpike = useCallback(async (input: PdfEvidenceSpikeInput): Promise<PdfEvidenceSpikeResult> => {
    const result = await executeEvidenceNavigation(
      {
        anchor: buildEvidenceSpikeAnchor(input),
        searchText: input.quote?.trim() || null,
        pageNumber: input.pageNumber ?? null,
        sectionTitle: input.sectionTitle ?? null,
        mode: 'select',
      },
      {
        pageHints: input.pageNumbers,
        sectionPath: Array.isArray(input.sectionPath) ? input.sectionPath : [],
      },
    )

    commitNavigationResult(result)
    publishEvidenceSpikeResult(result)
    return result
  }, [commitNavigationResult, executeEvidenceNavigation])

  const applyHighlights = useCallback((specificTextLayer?: HTMLElement) => {
    const iframeWindow = iframeRef.current?.contentWindow as any
    const iframeDoc = iframeWindow?.document as Document | undefined
    if (!iframeDoc || !highlightTermsRef.current.length) {
      setTelemetry((prev) => ({
        ...prev,
        lastHighlightMs: null,
        slowHighlight: false,
      }))
      return
    }

    if (!iframeWindow?.Mark) {
      setTimeout(() => applyHighlights(specificTextLayer), 200)
      return
    }

    ensureMarkInjected(iframeDoc, settingsRef.current)

    const terms = highlightTermsRef.current
    const textLayers = getTextLayers(iframeDoc, specificTextLayer)
    const measurementStart = performance.now()
    textLayers.forEach((layer) => {
      const markInstance = new iframeWindow.Mark(layer)
      markInstance.unmark({
        done: () => {
          terms.forEach((term) => {
            markInstance.mark(term, {
              className: 'pdf-highlight',
              separateWordSearch: false,
              caseSensitive: false,
              acrossElements: true,
            })
          })
        },
      })
    })

    const recordDuration = () => {
      const duration = performance.now() - measurementStart
      setTelemetry((prev) => ({
        ...prev,
        lastHighlightMs: duration,
        slowHighlight: duration > 500,
      }))
    }

    if (typeof window.requestAnimationFrame === 'function') {
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(recordDuration)
      })
    } else {
      setTimeout(recordDuration, 0)
    }
  }, [])

  const updateViewerState = useCallback(
    (updates: Partial<ViewerState>) => {
      if (!activeDocument) return
      viewerStateRef.current = {
        ...viewerStateRef.current,
        ...updates,
        lastInteraction: new Date().toISOString(),
      }
      persistSession(activeDocument, viewerStateRef.current)
    },
    [activeDocument],
  )

  const attachPdfEventListeners = useCallback(
    (pdfApp: any) => {
      const eventBus = pdfApp?.eventBus
      if (!eventBus) return

      const onTextLayerRendered = (event: any) => {
        const iframeDoc = iframeRef.current?.contentWindow?.document
        if (!iframeDoc) return
        const pageDiv = iframeDoc.querySelector<HTMLElement>(`.page[data-page-number="${event.pageNumber}"]`)
        const textLayer = pageDiv?.querySelector<HTMLElement>('.textLayer')
        if (textLayer) {
          // pdf.js may emit the event before glyphs settle; delay slightly as in legacy viewer
          window.setTimeout(() => {
            applyHighlights(textLayer)
            setOverlayRenderKey((prev) => prev + 1)
          }, 50)
        }
      }

      const onDocumentLoaded = () => {
        debug.log('🔍 [PDF VIEWER DEBUG] Document loaded event fired - setting status to ready')
        setStatus('ready')
        // Signal that document loading is complete
        sessionStorage.removeItem('document-loading')
        window.dispatchEvent(new CustomEvent('document-load-complete'))
        if (loadStartRef.current !== null) {
          const duration = performance.now() - loadStartRef.current
          setTelemetry((prev) => ({
            ...prev,
            lastLoadMs: duration,
            slowLoad: duration > 3000,
          }))
          loadStartRef.current = null
        }
        if (highlightTermsRef.current.length) {
          applyHighlights()
        }
        setOverlayRenderKey((prev) => prev + 1)
      }

      const onPageChanging = (event: any) => {
        if (typeof event.pageNumber === 'number') {
          updateViewerState({ currentPage: event.pageNumber })
          setOverlayRenderKey((prev) => prev + 1)
        }
      }

      const onScaleChanging = (event: any) => {
        if (typeof event.scale === 'number') {
          // event.scale is a decimal like 1.0 for 100%, 1.5 for 150%, etc.
          // Clamp to reasonable values (10% to 500%) to prevent extreme zoom bugs
          const newZoomLevel = Math.round(Math.max(10, Math.min(500, event.scale * 100)))
          updateViewerState({ zoomLevel: newZoomLevel })
          setOverlayRenderKey((prev) => prev + 1)
        }
      }

      eventBus.on('textlayerrendered', onTextLayerRendered)
      eventBus.on('documentloaded', onDocumentLoaded)
      eventBus.on('pagechanging', onPageChanging)
      eventBus.on('scalechanging', onScaleChanging)

      cleanupRefs.current.push(() => {
        eventBus.off('textlayerrendered', onTextLayerRendered)
        eventBus.off('documentloaded', onDocumentLoaded)
        eventBus.off('pagechanging', onPageChanging)
        eventBus.off('scalechanging', onScaleChanging)
      })

      // CRITICAL FIX: Check if PDF is already loaded (race condition fix)
      // If the PDF loaded before we attached the 'documentloaded' listener, we need to manually trigger it
      if (pdfApp.pdfDocument || pdfApp.pdfViewer?.pdfDocument) {
        debug.log('🔍 [PDF VIEWER DEBUG] PDF already loaded when attaching listeners - manually triggering onDocumentLoaded')
        onDocumentLoaded()
      }

      const viewerContainer = pdfApp?.appConfig?.viewerContainer as HTMLElement | undefined
      if (viewerContainer) {
        const onScroll = () => {
          updateViewerState({ scrollPosition: viewerContainer.scrollTop })
        }
        viewerContainer.addEventListener('scroll', onScroll, { passive: true })
        cleanupRefs.current.push(() => viewerContainer.removeEventListener('scroll', onScroll))

        // Apply stored scroll position if rehydrating
        const { scrollPosition } = viewerStateRef.current
        if (typeof scrollPosition === 'number' && scrollPosition > 0) {
          viewerContainer.scrollTop = scrollPosition
        }
      }

      pdfAppRef.current = pdfApp
    },
    [applyHighlights, updateViewerState, setOverlayRenderKey],
  )

  const initialisePdfApplication = useCallback(() => {
    if (cleanupRefs.current.length > 0) {
      cleanupRefs.current.forEach((fn) => {
        try {
          fn()
        } catch (error) {
          console.warn('Failed to clean up previous PDF listeners', error)
        }
      })
      cleanupRefs.current = []
    }

    const iframeWindow = iframeRef.current?.contentWindow as any
    const iframeDoc = iframeWindow?.document
    if (!iframeWindow || !iframeDoc) return

    ensureMarkInjected(iframeDoc, settingsRef.current)

    const handshakeTimeout = window.setTimeout(() => {
      if (loadStartRef.current !== null) {
        setStatus('error')
        setError('Timed out waiting for the PDF viewer to initialise.')
        loadStartRef.current = null
        signalLoadComplete()
      }
    }, 8000)

    const intervalId = window.setInterval(() => {
      const pdfApp = iframeWindow.PDFViewerApplication
      if (pdfApp && pdfApp.eventBus) {
        console.debug('[PDF DEBUG] PDFViewerApplication detected with eventBus', {
          hasPdfViewer: Boolean(pdfApp.pdfViewer),
        })
        window.clearInterval(intervalId)
        window.clearTimeout(handshakeTimeout)
        attachPdfEventListeners(pdfApp)

        // Apply persisted state when available
        const { currentPage } = viewerStateRef.current
        if (pdfApp.pdfViewer) {
          try {
            if (currentPage > 1) {
              pdfApp.pdfViewer.currentPageNumber = currentPage
            }
            // Always set zoom to automatic for consistent experience
            pdfApp.pdfViewer.currentScaleValue = 'auto'
          } catch (error) {
            console.warn('Unable to restore viewer state', error)
          }
        }

        if (highlightTermsRef.current.length) {
          console.debug('[PDF DEBUG] Reapplying highlight terms after load', highlightTermsRef.current)
          applyHighlights()
        }
      }
    }, 150)

    cleanupRefs.current.push(() => window.clearInterval(intervalId))
    cleanupRefs.current.push(() => window.clearTimeout(handshakeTimeout))
  }, [applyHighlights, attachPdfEventListeners])

  const beginDocumentLoad = useCallback((document: ViewerDocument) => {
    console.debug('[PDF DEBUG] beginDocumentLoad invoked', document)
    loadStartRef.current = performance.now()
    handledNavigationKeyRef.current = null
    setStatus('loading')
    setError(null)
    setTelemetry((prev) => ({
      ...prev,
      lastLoadMs: null,
      slowLoad: false,
      lastHighlightMs: null,
      slowHighlight: false,
    }))
    setActiveDocument(document)
    setOverlays([])
    commitNavigationResult(null)
    setOverlayRenderKey((prev) => prev + 1)
    persistSession(document, viewerStateRef.current)
  }, [commitNavigationResult])

  useEffect(() => {
    const storedSettings = loadStoredSettings()
    settingsRef.current = storedSettings
    setHighlightSettings(storedSettings)

    // DO NOT auto-load stored session on mount
    // The PDF viewer is passive and only loads when it receives a 'pdf-viewer-document-changed' event
    // This event is dispatched by:
    // 1. DocumentsPage when user selects a document
    // 2. Chat component on mount if backend has an active document (preserves doc across refreshes)
  }, [])

  useEffect(() => {
    const unregisterDocument = onPDFDocumentChanged((event: PDFViewerDocumentChangedEvent) => {
      console.debug('[PDF DEBUG] pdf-viewer-document-changed event received', event.detail)
      const nextDoc: ViewerDocument = {
        documentId: event.detail.documentId,
        viewerUrl: event.detail.viewerUrl,
        filename: event.detail.filename,
        pageCount: event.detail.pageCount,
        loadedAt: new Date().toISOString(),
      }
      console.debug('[PDF DEBUG] beginDocumentLoad called with', nextDoc)

      // Always start fresh: page 1, auto zoom, no scroll
      viewerStateRef.current = { ...DEFAULT_STATE, lastInteraction: new Date().toISOString() }
      highlightTermsRef.current = []
      setHighlightTerms([])
      beginDocumentLoad(nextDoc)
    })

    const unregisterHighlights = onApplyHighlights((event: ApplyHighlightsEvent) => {
      const unique = uniqueTerms(event.detail.terms)
      highlightTermsRef.current = unique
      setHighlightTerms(unique)
      applyHighlights()
    })

    const unregisterClear = onClearHighlights((event: ClearHighlightsEvent) => {
      if (event.detail?.reason === 'new-query' && !settingsRef.current.clearOnNewQuery) {
        return
      }
      highlightTermsRef.current = []
      setHighlightTerms([])
      clearAllHighlights()
    })

    const unregisterSettings = onHighlightSettingsChanged((event: HighlightSettingsChangedEvent) => {
      const nextSettings: HighlightSettings = {
        highlightColor: event.detail?.color ?? settingsRef.current.highlightColor,
        highlightOpacity: typeof event.detail?.opacity === 'number' ? event.detail.opacity : settingsRef.current.highlightOpacity,
        clearOnNewQuery: typeof event.detail?.clearOnNewQuery === 'boolean'
          ? event.detail.clearOnNewQuery
          : settingsRef.current.clearOnNewQuery,
      }
      settingsRef.current = nextSettings
      setHighlightSettings(nextSettings)
      if (iframeRef.current?.contentWindow?.document) {
        ensureMarkInjected(iframeRef.current.contentWindow.document, nextSettings)
        if (highlightTermsRef.current.length) {
          applyHighlights()
        }
      }
    })

    // Listen for chat document changes (including unload)
    const handleChatDocumentChange = (event: Event) => {
      const customEvent = event as CustomEvent
      const detail = customEvent.detail || {}

      // If document is being unloaded (active=false), clear the viewer
      if (!detail?.active || !detail.document) {
        console.debug('[PDF DEBUG] Document unloaded via chat-document-changed event')
        handledNavigationKeyRef.current = null
        setActiveDocument(null)
        setStatus('idle')
        setError(null)
        highlightTermsRef.current = []
        setHighlightTerms([])
        setOverlays([])
        commitNavigationResult(null)
        localStorage.removeItem(SESSION_STORAGE_KEY)
      } else {
        // Document is being loaded - show loading state immediately
        console.debug('[PDF DEBUG] Document loading started via chat-document-changed event')
        setStatus('loading')
      }
    }
    window.addEventListener('chat-document-changed', handleChatDocumentChange)

    return () => {
      unregisterDocument()
      unregisterHighlights()
      unregisterClear()
      unregisterSettings()
      window.removeEventListener('chat-document-changed', handleChatDocumentChange)
    }
  }, [applyHighlights, beginDocumentLoad, clearAllHighlights, commitNavigationResult, signalLoadComplete])

  useEffect(() => {
    return () => {
      cleanupRefs.current.forEach((fn) => {
        try {
          fn()
        } catch (error) {
          console.warn('Failed to clean up PDF viewer listener', error)
        }
      })
      cleanupRefs.current = []
      uploadAbortRef.current?.abort()
      uploadAbortRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!iframeRef.current) {
      console.debug('[PDF DEBUG] iframeRef null in load listener effect')
      return
    }
    const iframe = iframeRef.current

    console.debug('[PDF DEBUG] Attaching load/error listeners to iframe', {
      iframeSrc: iframe?.src,
      activeDocument,
    })

    const handleLoad = () => {
      console.debug('[PDF DEBUG] iframe load event triggered', {
        iframeSrc: iframe?.src,
      })
      initialisePdfApplication()
    }

    const handleError = () => {
      console.error('[PDF DEBUG] iframe error event triggered', {
        iframeSrc: iframe?.src,
      })
      setStatus('error')
      setError('Failed to load the PDF viewer frame.')
      loadStartRef.current = null
      signalLoadComplete()
    }

    iframe.addEventListener('load', handleLoad)
    iframe.addEventListener('error', handleError)

    if (activeDocument && (iframe.contentWindow as any)?.PDFViewerApplication?.eventBus) {
      // Recover when the iframe finished loading before this effect reattached listeners.
      initialisePdfApplication()
    }

    return () => {
      iframe.removeEventListener('load', handleLoad)
      iframe.removeEventListener('error', handleError)
    }
  }, [activeDocument, initialisePdfApplication, viewerSrc, signalLoadComplete])

  useEffect(() => {
    if (activeDocument) {
      setDragActive(false)
      setDropError(null)
      debug.log('🔍 [PDF VIEWER DEBUG] Active document exists, persisting session:', activeDocument.documentId)
      persistSession(activeDocument, viewerStateRef.current)
    } else {
      debug.log('🔍 [PDF VIEWER DEBUG] No active document, resetting to idle')
      handledNavigationKeyRef.current = null
      localStorage.removeItem(SESSION_STORAGE_KEY)
      setStatus('idle')
      setError(null)
      setTelemetry({
        lastLoadMs: null,
        lastHighlightMs: null,
        slowLoad: false,
        slowHighlight: false,
      })
      commitNavigationResult(null)
    }
  }, [activeDocument?.documentId, commitNavigationResult])

  useEffect(() => {
    if (status === 'ready' && highlightTermsRef.current.length) {
      applyHighlights()
    }
  }, [status, applyHighlights])

  useEffect(() => {
    if (!pendingNavigation) {
      handledNavigationKeyRef.current = null
      return
    }

    if (!activeDocument || status !== 'ready') {
      return
    }

    const navigationKey = buildNavigationCommandKey(pendingNavigation)
    if (handledNavigationKeyRef.current === navigationKey) {
      return
    }

    handledNavigationKeyRef.current = navigationKey
    let cancelled = false

    void executeEvidenceNavigation(pendingNavigation)
      .then((result) => {
        if (cancelled) {
          return
        }
        commitNavigationResult(result)
        onNavigationComplete?.()
      })
      .catch((error) => {
        console.warn('Failed to execute typed PDF evidence navigation', error)
        if (cancelled) {
          return
        }
        commitNavigationResult({
          status: 'not-found',
          strategy: 'document',
          locatorQuality: 'unresolved',
          degraded: true,
          mode: pendingNavigation.mode,
          documentId: activeDocument.documentId,
          quote: pendingNavigation.anchor.snippet_text?.trim()
            ?? pendingNavigation.anchor.sentence_text?.trim()
            ?? pendingNavigation.searchText?.trim()
            ?? '',
          pageHints: normalizeEvidenceSpikePageHints({
            pageNumber: pendingNavigation.pageNumber ?? pendingNavigation.anchor.page_number ?? null,
          }),
          sectionTitle: normalizeEvidenceSpikeText(
            pendingNavigation.sectionTitle ?? pendingNavigation.anchor.section_title ?? '',
          ) || null,
          matchedQuery: null,
          matchedPage: null,
          matchesTotal: 0,
          currentMatch: 0,
          attemptedQueries: [],
          note: 'Typed evidence navigation failed unexpectedly before the viewer could localize the requested anchor.',
        })
        onNavigationComplete?.()
      })

    return () => {
      cancelled = true
    }
  }, [
    activeDocument,
    commitNavigationResult,
    executeEvidenceNavigation,
    onNavigationComplete,
    pendingNavigation,
    status,
  ])

  useEffect(() => {
    const evidenceSpikeEnabled = getEnvFlag(['VITE_DEV_MODE', 'DEV_MODE', 'VITE_DEBUG', 'DEBUG'], false)
    if (!evidenceSpikeEnabled) {
      return
    }

    const handleEvidenceSpike = (event: Event) => {
      const detail = (event as CustomEvent<PdfEvidenceSpikeInput>).detail
      if (!detail) {
        return
      }
      void executeEvidenceSpike(detail)
    }

    window.__pdfViewerEvidenceSpike = executeEvidenceSpike
    window.__pdfViewerEvidenceSpikeLastResult = null
    window.addEventListener(EVIDENCE_SPIKE_EVENT_NAME, handleEvidenceSpike)

    return () => {
      if (window.__pdfViewerEvidenceSpike === executeEvidenceSpike) {
        delete window.__pdfViewerEvidenceSpike
      }
      delete window.__pdfViewerEvidenceSpikeLastResult
      window.removeEventListener(EVIDENCE_SPIKE_EVENT_NAME, handleEvidenceSpike)
    }
  }, [executeEvidenceSpike])

  useEffect(() => {
    const iframeDoc = iframeRef.current?.contentWindow?.document
    const pdfApp = pdfAppRef.current

    debug.log('🔍 [PDF OVERLAY RENDER] Starting overlay render effect:', {
      hasIframeDoc: !!iframeDoc,
      hasPdfApp: !!pdfApp,
      hasPdfViewer: !!pdfApp?.pdfViewer,
      status,
      overlaysCount: overlays.length,
      overlayRenderKey
    })

    if (!iframeDoc || !pdfApp?.pdfViewer) {
      debug.log('🔍 [PDF OVERLAY RENDER] Missing iframe doc or PDF app, skipping')
      return
    }

    // Remove existing overlays
    const existingOverlays = iframeDoc.querySelectorAll('.chunk-overlay-layer')
    debug.log('🔍 [PDF OVERLAY RENDER] Removing existing overlay layers:', existingOverlays.length)
    existingOverlays.forEach((node) => node.remove())

    if (status !== 'ready' || overlays.length === 0) {
      debug.log('🔍 [PDF OVERLAY RENDER] Not ready or no overlays to render:', {
        status,
        overlaysCount: overlays.length
      })
      return
    }

    debug.log('🔍 [PDF OVERLAY RENDER] Processing overlays for rendering:', {
      totalOverlays: overlays.length,
      totalDocItems: overlays.reduce((sum, o) => sum + o.docItems.length, 0),
      overlays: overlays.map(o => ({
        chunkId: o.chunkId,
        docItemsCount: o.docItems.length
      }))
    })

    const layersByPage = new Map<number, HTMLElement>()
    let renderedRectCount = 0
    let skippedItemCount = 0

    overlays.forEach((overlay, overlayIdx) => {
      debug.log(`🔍 [PDF OVERLAY RENDER] Processing overlay ${overlayIdx}:`, {
        chunkId: overlay.chunkId,
        docItemsCount: overlay.docItems.length
      })

      overlay.docItems.forEach((item, itemIdx) => {
        const pageNumber = typeof item.page === 'number' ? item.page : typeof item.page_no === 'number' ? item.page_no : undefined
        const bbox = item.bbox

        if (!pageNumber || !bbox) {
          console.warn('[PDF OVERLAY DIAGNOSTICS] Skipping overlay render for incomplete doc_item', {
            chunkId: overlay.chunkId,
            documentId: overlay.documentId ?? activeDocument?.documentId ?? null,
            itemIndex: itemIdx,
            hasPageNumber: !!pageNumber,
            hasBbox: !!bbox,
            item,
          })
          skippedItemCount++
          return
        }

        const left = Number(bbox.left)
        const top = Number(bbox.top)
        const right = Number(bbox.right)
        const bottom = Number(bbox.bottom)

        if (!Number.isFinite(left) || !Number.isFinite(top) || !Number.isFinite(right) || !Number.isFinite(bottom)) {
          console.warn('[PDF OVERLAY DIAGNOSTICS] Skipping overlay render for invalid bbox coordinates', {
            chunkId: overlay.chunkId,
            documentId: overlay.documentId ?? activeDocument?.documentId ?? null,
            itemIndex: itemIdx,
            pageNumber,
            left,
            top,
            right,
            bottom,
          })
          skippedItemCount++
          return
        }

        const pageView = pdfApp.pdfViewer.getPageView(pageNumber - 1)
        const pageDiv = pageView?.div
        const viewport = pageView?.viewport

        if (!pageDiv || !viewport) {
          debug.log(`🔍 [PDF OVERLAY RENDER] Skipping item ${itemIdx} - page not ready:`, {
            pageNumber,
            hasPageView: !!pageView,
            hasPageDiv: !!pageDiv,
            hasViewport: !!viewport
          })
          skippedItemCount++
          return
        }

        let layer = layersByPage.get(pageNumber)
        if (!layer) {
          layer = iframeDoc.createElement('div')
          layer.className = 'chunk-overlay-layer'
          layer.style.position = 'absolute'
          layer.style.inset = '0'
          layer.style.pointerEvents = 'none'
          layer.style.zIndex = '5'
          pageDiv.appendChild(layer)
          layersByPage.set(pageNumber, layer)
          debug.log(`🔍 [PDF OVERLAY RENDER] Created overlay layer for page ${pageNumber}`)
        }

        const rect = viewport.convertToViewportRectangle([left, top, right, bottom])
        const [x1, y1, x2, y2] = rect

        const overlayRect = iframeDoc.createElement('div')
        overlayRect.className = 'chunk-overlay-rect'
        overlayRect.style.position = 'absolute'
        overlayRect.style.left = `${Math.min(x1, x2)}px`
        overlayRect.style.top = `${Math.min(y1, y2)}px`
        overlayRect.style.width = `${Math.abs(x2 - x1)}px`
        overlayRect.style.height = `${Math.abs(y2 - y1)}px`
        overlayRect.style.background = 'rgba(21, 101, 192, 0.25)'
        overlayRect.style.border = '2px solid rgba(21, 101, 192, 0.85)'
        overlayRect.style.borderRadius = '2px'
        overlayRect.style.pointerEvents = 'none'
        layer.appendChild(overlayRect)
        renderedRectCount++

        debug.log(`🔍 [PDF OVERLAY RENDER] Rendered rect ${itemIdx} on page ${pageNumber}:`, {
          originalBbox: bbox,
          convertedRect: { x1, y1, x2, y2 },
          finalPosition: {
            left: Math.min(x1, x2),
            top: Math.min(y1, y2),
            width: Math.abs(x2 - x1),
            height: Math.abs(y2 - y1)
          }
        })
      })
    })

    debug.log('🔍 [PDF OVERLAY RENDER] Rendering complete:', {
      totalOverlays: overlays.length,
      totalDocItems: overlays.reduce((sum, o) => sum + o.docItems.length, 0),
      renderedRects: renderedRectCount,
      skippedItems: skippedItemCount,
      pagesWithOverlays: Array.from(layersByPage.keys()).sort()
    })

    return () => {
      const docCleanup = iframeRef.current?.contentWindow?.document
      if (!docCleanup) return
      const toRemove = docCleanup.querySelectorAll('.chunk-overlay-layer')
      debug.log('🔍 [PDF OVERLAY RENDER] Cleanup - removing overlay layers:', toRemove.length)
      toRemove.forEach((node) => node.remove())
    }
  // Re-render overlays when the active document changes so stale rectangles from
  // the previous PDF are cleared even if the overlay payload did not change.
  }, [activeDocument?.documentId, overlays, status, overlayRenderKey])

  useEffect(() => {
    if (!activeDocument) return

    let cancelled = false
    const controller = new AbortController()
    const timeoutId = window.setTimeout(() => controller.abort(), 8000)

    const verifyDocument = async () => {
      try {
        const response = await fetch(activeDocument.viewerUrl, {
          method: 'HEAD',
          signal: controller.signal,
          credentials: 'same-origin',
        })
        if (!response.ok) {
          throw new Error(`Unexpected status ${response.status}`)
        }
      } catch (fetchError) {
        if (!cancelled) {
          setStatus('error')
          setError('Unable to reach the PDF document. Please retry or re-upload.')
          loadStartRef.current = null
          signalLoadComplete()
        }
      } finally {
        window.clearTimeout(timeoutId)
      }
    }

    verifyDocument()

    return () => {
      cancelled = true
      controller.abort()
      window.clearTimeout(timeoutId)
    }
  }, [activeDocument, signalLoadComplete])

  return (
    <Paper
      elevation={3}
      sx={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        backgroundColor: 'background.paper',
      }}
    >
      <Box sx={{ padding: 2, borderBottom: 1, borderColor: 'divider' }}>
        {activeDocument ? (
          <Stack spacing={0.5}>
            <Typography variant="h6">{activeDocument.filename}</Typography>
            <Typography variant="body2" color="text.secondary">
              {activeDocument.pageCount} pages · Serving from {activeDocument.viewerUrl}
            </Typography>
            {navigationResult && (
              <>
                <Stack direction="row" spacing={1} flexWrap="wrap">
                  <Chip
                    size="small"
                    color={getNavigationBadgeColor(navigationResult)}
                    label={formatLocatorQualityLabel(navigationResult.locatorQuality)}
                    sx={{ marginTop: 0.5 }}
                  />
                  <Chip
                    size="small"
                    variant="outlined"
                    label={navigationResult.mode === 'hover' ? 'Hover sync' : 'Selection sync'}
                    sx={{ marginTop: 0.5 }}
                  />
                  {navigationResult.matchedPage !== null && (
                    <Chip
                      size="small"
                      variant="outlined"
                      label={`Page ${navigationResult.matchedPage}`}
                      sx={{ marginTop: 0.5 }}
                    />
                  )}
                </Stack>
                <Typography
                  variant="body2"
                  color={navigationResult.degraded ? 'warning.main' : 'text.secondary'}
                >
                  {navigationResult.note}
                </Typography>
              </>
            )}
            {highlightTerms.length > 0 && (
              <Stack direction="row" spacing={1} flexWrap="wrap">
                {highlightTerms.map((term) => (
                  <Chip key={term} size="small" label={term} color="secondary" sx={{ marginTop: 0.5 }} />
                ))}
              </Stack>
            )}
          </Stack>
        ) : (
          <Typography variant="h6">No document loaded</Typography>
        )}
      </Box>

      <Box sx={{ flex: 1, position: 'relative' }}>
        {!activeDocument && (
          <Box
            role="region"
            aria-label="PDF drop zone"
            onDragEnter={handleDragEnter}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            sx={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'text.secondary',
              textAlign: 'center',
              px: 3,
              border: '2px dashed',
              borderColor: dragActive ? 'primary.main' : 'divider',
              backgroundColor: dragActive ? 'action.hover' : 'transparent',
              transition: 'border-color 120ms ease, background-color 120ms ease',
            }}
          >
            <Typography variant="body1" sx={{ mb: 2 }}>
              {dragActive ? 'Drop PDF to upload and load for chat' : 'Drag and drop a PDF here to upload'}
            </Typography>
            {uploadInFlight && (
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2 }}>
                <CircularProgress size={16} />
                <Typography variant="body2" color="text.secondary">
                  Upload in progress...
                </Typography>
              </Stack>
            )}
            {dropError && (
              <Alert severity="error" sx={{ mb: 2, maxWidth: 640 }}>
                {dropError}
              </Alert>
            )}
            <Typography variant="body2" component="div">
              <ul style={{ textAlign: 'left', margin: 0, paddingLeft: '1.5rem' }}>
                <li style={{ marginBottom: '0.75rem' }}>
                  Drop a PDF here to upload and load it for chat.
                </li>
                <li style={{ marginBottom: '0.75rem' }}>
                  For one or multiple uploads, open <strong>Documents</strong> and use Upload.
                </li>
                <li>To load a PDF you already uploaded, open <strong>Documents</strong> and click the green file icon in that row.</li>
              </ul>
            </Typography>
          </Box>
        )}

      {activeDocument && status === 'loading' && (
        <Box
          sx={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: 'rgba(0,0,0,0.35)',
            zIndex: 1,
          }}
        >
          <Stack spacing={2} alignItems="center">
            <CircularProgress color="inherit" size={48} />
            <Typography variant="body2" color="inherit">
              Loading PDF...
            </Typography>
          </Stack>
        </Box>
      )}

      {activeDocument && status === 'error' && (
        <Box
          sx={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: 'rgba(18, 18, 18, 0.9)',
            zIndex: 2,
            padding: 3,
          }}
        >
          <Alert
            severity="error"
            sx={{ width: '100%' }}
            action={(
              <Button
                color="inherit"
                size="small"
                onClick={() => {
                  if (!activeDocument) return
                  const refreshedDocument: ViewerDocument = {
                    ...activeDocument,
                    loadedAt: new Date().toISOString(),
                  }
                  setRetryKey((key) => key + 1)
                  beginDocumentLoad(refreshedDocument)
                }}
              >
                Retry
              </Button>
            )}
          >
            {error ?? 'Something went wrong while loading the PDF viewer.'}
          </Alert>
        </Box>
      )}

        <iframe
        key={`${activeDocument?.documentId}::${retryKey}`}
          ref={iframeRef}
          title="PDF Viewer"
          src={viewerSrc}
          style={{
            border: 'none',
            width: '100%',
            height: '100%',
            backgroundColor: '#1d1d1d',
          }}
        />
      </Box>
      <UploadProgressDialog
        open={uploadDialog.open}
        fileName={uploadDialog.fileName}
        stage={uploadDialog.stage}
        progress={uploadDialog.progress}
        message={uploadDialog.message}
        documentId={uploadDialog.documentId}
        onClose={handleCloseUploadDialog}
      />
    </Paper>
  )
}

export default PdfViewer
