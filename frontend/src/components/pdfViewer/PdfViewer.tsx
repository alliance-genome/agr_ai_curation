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
  dispatchPDFViewerEvidenceAnchorSelected,
  onApplyHighlights,
  onClearHighlights,
  onHighlightSettingsChanged,
  onPDFDocumentChanged,
  onPDFViewerNavigateEvidence,
} from '@/components/pdfViewer/pdfEvents'
import {
  buildNormalizedTextSourceMap,
  extractSentenceCandidate,
  normalizeTextForEvidenceMatch,
  sanitizeEvidenceSearchText,
  splitNormalizedWords,
} from '@/components/pdfViewer/textNormalization'
import {
  findAnchoredEvidenceSpan,
  getAnchoredEvidenceSpanDebugInfo,
} from '@/components/pdfViewer/textAnchoring'
import type { AnchoredEvidenceSpanWindowDebug } from '@/components/pdfViewer/textAnchoring'
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
const PDF_TEXT_LAYER_MATCH_TIMEOUT_MS = 2000
const PDF_TEXT_LAYER_RETRY_TIMEOUT_MS = 300
const EVIDENCE_SPIKE_EVENT_NAME = 'pdf-viewer-evidence-spike'
const EVIDENCE_SPIKE_RESULT_EVENT_NAME = 'pdf-viewer-evidence-spike-result'
const PDF_EVIDENCE_DEBUG_STORAGE_KEY = 'pdf-evidence-debug'
const PDF_EVIDENCE_DEBUG_URL_PARAM = 'pdfEvidenceDebug'
const PDF_EVIDENCE_DEBUG_MAX_ENTRIES = 800
const EVIDENCE_SPIKE_FRAGMENT_WORDS = 24
const EVIDENCE_SPIKE_WINDOW_FRAGMENT_MIN_WORDS = 8
const EVIDENCE_SPIKE_WINDOW_FRAGMENT_TARGET_WORDS = 12

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
  | 'sanitized-quote'
  | 'exact-quote'
  | 'normalized-quote'
  | 'first-sentence-fragment'
  | 'window-fragment'
  | 'leading-fragment'
  | 'trailing-fragment'
  | 'section-title'
  | 'subsection-title'

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

interface PdfEvidenceDebugEntry {
  timestamp: string
  message: string
  detail?: unknown
}

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

interface EvidenceTextLayerHighlight {
  anchorId: string
  kind: 'quote' | 'section'
  mode: EvidenceNavigationCommand['mode']
  pageNumber: number
  query: string
  pageMatchIndex: number | null
  rects: EvidenceTextLayerRect[] | null
  renderOverlay: boolean
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

const pdfEvidenceDebugEntries: PdfEvidenceDebugEntry[] = []
let lastPdfEvidenceNavigationResult: PdfViewerNavigationResult | null = null

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
    __pdfViewerEvidenceDebug?: {
      enabled: boolean
      storageKey: string
      setEnabled: (enabled: boolean) => boolean
      getEntries: () => PdfEvidenceDebugEntry[]
      clearEntries: () => void
      getLastResult: () => PdfViewerNavigationResult | null
    }
  }
}

const truncateDebugText = (value: string, maxLength: number = 240): string => {
  const normalized = value.replace(/\s+/g, ' ').trim()
  if (normalized.length <= maxLength) {
    return normalized
  }

  return `${normalized.slice(0, maxLength)}...`
}

const sanitizePdfEvidenceDebugDetail = (
  value: unknown,
  depth: number = 0,
  seen: WeakSet<object> = new WeakSet(),
): unknown => {
  if (value === null || value === undefined) {
    return value
  }

  if (typeof value === 'string') {
    return value.length > 1200 ? `${value.slice(0, 1200)}...` : value
  }

  if (typeof value === 'number' || typeof value === 'boolean') {
    return value
  }

  if (depth >= 5) {
    return '[max-depth]'
  }

  if (Array.isArray(value)) {
    const items = value.slice(0, 25).map((entry) => sanitizePdfEvidenceDebugDetail(entry, depth + 1, seen))
    return value.length > 25 ? [...items, `[+${value.length - 25} more]`] : items
  }

  if (typeof value === 'object') {
    if (value instanceof DOMRect) {
      return {
        left: value.left,
        top: value.top,
        width: value.width,
        height: value.height,
      }
    }

    if (value instanceof HTMLElement) {
      return {
        type: 'HTMLElement',
        tagName: value.tagName,
        className: value.className,
        text: truncateDebugText(value.textContent ?? '', 120),
      }
    }

    if (seen.has(value as object)) {
      return '[circular]'
    }
    seen.add(value as object)

    const entries = Object.entries(value as Record<string, unknown>).slice(0, 40)
    return Object.fromEntries(
      entries.map(([key, entryValue]) => [
        key,
        sanitizePdfEvidenceDebugDetail(entryValue, depth + 1, seen),
      ]),
    )
  }

  return String(value)
}

const appendPdfEvidenceDebugEntry = (message: string, detail?: unknown) => {
  pdfEvidenceDebugEntries.push({
    timestamp: new Date().toISOString(),
    message,
    detail: detail === undefined ? undefined : sanitizePdfEvidenceDebugDetail(detail),
  })

  if (pdfEvidenceDebugEntries.length > PDF_EVIDENCE_DEBUG_MAX_ENTRIES) {
    pdfEvidenceDebugEntries.splice(0, pdfEvidenceDebugEntries.length - PDF_EVIDENCE_DEBUG_MAX_ENTRIES)
  }
}

const parseDebugFlag = (value: string | null | undefined): boolean | null => {
  if (value === null || value === undefined) {
    return null
  }

  switch (String(value).toLowerCase()) {
    case 'true':
    case '1':
    case 'yes':
    case 'on':
      return true
    case 'false':
    case '0':
    case 'no':
    case 'off':
      return false
    default:
      return null
  }
}

const isPdfEvidenceDebugEnabled = (): boolean => {
  if (typeof window !== 'undefined') {
    try {
      const url = new URL(window.location.href)
      const fromUrl = parseDebugFlag(url.searchParams.get(PDF_EVIDENCE_DEBUG_URL_PARAM))
      if (fromUrl !== null) {
        return fromUrl
      }
    } catch {
      // Ignore URL parsing issues and continue with other flag sources.
    }

    try {
      const fromLocalStorage = parseDebugFlag(window.localStorage.getItem(PDF_EVIDENCE_DEBUG_STORAGE_KEY))
      if (fromLocalStorage !== null) {
        return fromLocalStorage
      }
    } catch {
      // Ignore storage access issues and continue with env flags.
    }
  }

  return getEnvFlag(['VITE_DEV_MODE', 'DEV_MODE', 'VITE_DEBUG', 'DEBUG'], false)
}

const setPdfEvidenceDebugEnabled = (enabled: boolean): boolean => {
  if (typeof window === 'undefined') {
    return enabled
  }

  try {
    window.localStorage.setItem(PDF_EVIDENCE_DEBUG_STORAGE_KEY, enabled ? '1' : '0')
  } catch {
    // Ignore storage failures; the console helper still returns the requested state.
  }

  return enabled
}

const logPdfEvidenceDebug = (message: string, detail?: unknown) => {
  if (!isPdfEvidenceDebugEnabled()) {
    return
  }

  appendPdfEvidenceDebugEntry(message, detail)

  if (detail === undefined) {
    console.info(`[PDF EVIDENCE DEBUG] ${message}`)
    return
  }

  console.info(`[PDF EVIDENCE DEBUG] ${message}`, detail)
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

const buildEvidenceSpikeWindowFragments = (words: string[]): string[] => {
  if (words.length < EVIDENCE_SPIKE_WINDOW_FRAGMENT_MIN_WORDS + 2) {
    return []
  }

  const windowSize = Math.min(
    EVIDENCE_SPIKE_WINDOW_FRAGMENT_TARGET_WORDS,
    Math.max(EVIDENCE_SPIKE_WINDOW_FRAGMENT_MIN_WORDS, Math.floor(words.length * 0.7)),
  )
  const step = Math.max(3, Math.floor(windowSize / 2))
  const fragments: string[] = []

  for (let start = 0; start + windowSize <= words.length; start += step) {
    fragments.push(words.slice(start, start + windowSize).join(' '))
  }

  const trailingStart = Math.max(0, words.length - windowSize)
  const trailingFragment = words.slice(trailingStart).join(' ')
  // The trailing fragment keeps the tail of the quote searchable when the
  // sliding window does not land exactly on the end of the passage.
  if (trailingFragment && trailingFragment !== fragments[fragments.length - 1]) {
    fragments.push(trailingFragment)
  }

  return fragments
}

export const normalizeEvidenceSpikeText = normalizeTextForEvidenceMatch

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
  const exactQuote = trimmedSearchText || trimmed || trimmedNormalizedText
  const sanitizedQuote = sanitizeEvidenceSearchText(exactQuote).trim()
  const normalizedCandidate = normalizeEvidenceSpikeText(
    sanitizeEvidenceSearchText(trimmedNormalizedText || sanitizedQuote || exactQuote),
  )
  const fragmentSource = sanitizeEvidenceSearchText(
    trimmed || trimmedNormalizedText || sanitizedQuote || exactQuote,
  ).trim() || trimmed || trimmedNormalizedText || sanitizedQuote || exactQuote

  if (!exactQuote) {
    return []
  }

  const firstSentence = extractSentenceCandidate(fragmentSource)
  const words = splitNormalizedWords(fragmentSource)

  const candidates: PdfEvidenceSpikeCandidate[] = []

  if (sanitizedQuote && sanitizedQuote !== exactQuote) {
    candidates.push({ query: sanitizedQuote, reason: 'sanitized-quote' })
  }

  candidates.push({ query: exactQuote, reason: 'exact-quote' })

  if (normalizedCandidate) {
    candidates.push({ query: normalizedCandidate, reason: 'normalized-quote' })
  }

  if (firstSentence && firstSentence !== normalizedCandidate) {
    candidates.push({ query: firstSentence, reason: 'first-sentence-fragment' })
  }

  buildEvidenceSpikeWindowFragments(words).forEach((fragment) => {
    candidates.push({ query: fragment, reason: 'window-fragment' })
  })

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
  subsectionTitle?: string | null,
): PdfEvidenceSpikeCandidate[] => {
  const candidates: PdfEvidenceSpikeCandidate[] = []

  const normalizedSubsectionTitle = normalizeEvidenceSpikeText(subsectionTitle ?? '')
  const normalizedTitle = normalizeEvidenceSpikeText(sectionTitle ?? '')

  if (normalizedSubsectionTitle) {
    candidates.push({ query: normalizedSubsectionTitle, reason: 'subsection-title' })
  }

  if (normalizedTitle && normalizedTitle !== normalizedSubsectionTitle) {
    candidates.push({ query: normalizedTitle, reason: 'section-title' })
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
  if (typeof pageIdx === 'number' && pageIdx >= 0) {
    return pageIdx + 1
  }

  const currentPageNumber = pdfApp?.pdfViewer?.currentPageNumber
  return typeof currentPageNumber === 'number' && currentPageNumber >= 1
    ? currentPageNumber
    : null
}

const getSelectedEvidenceSpikeMatchedPage = (pdfApp: any): number | null => {
  const pageIdx = pdfApp?.findController?.selected?.pageIdx
  return typeof pageIdx === 'number' && pageIdx >= 0
    ? pageIdx + 1
    : null
}

const getSelectedEvidenceSpikeMatchIndex = (pdfApp: any): number | null => {
  const matchIdx = pdfApp?.findController?.selected?.matchIdx
  return typeof matchIdx === 'number' && matchIdx >= 0 ? matchIdx : null
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

const maybeClearPdfJsFindHighlights = (
  pdfApp: any,
  options?: {
    preserveNativeHighlight?: boolean
    reason?: string
  },
): void => {
  if (options?.preserveNativeHighlight) {
    logPdfEvidenceDebug('Preserving native PDF.js highlight', {
      reason: options.reason ?? 'localized-match',
    })
    return
  }

  clearPdfJsFindHighlights(pdfApp)
}

interface EvidenceTextLayerRect {
  left: number
  top: number
  width: number
  height: number
}

interface EvidenceTextLayerMatchResult {
  rects: EvidenceTextLayerRect[]
  matchedPage: number
}

export interface ExpandedEvidenceQuery {
  query: string
  wordCount: number
  startWordIndex: number
  endWordIndexExclusive: number
}

interface TextLayerTextSegment {
  node: Text
  container: HTMLElement
  start: number
  end: number
}

interface TextLayerMatchRange {
  rawStart: number
  rawEndExclusive: number
}

interface PdfJsTextMatchBoundary {
  divIdx: number
  offset: number
}

interface PdfJsTextMatch {
  begin: PdfJsTextMatchBoundary
  end: PdfJsTextMatchBoundary
}

const getPageContainer = (iframeDoc: Document, pageNumber: number): HTMLElement | null => {
  return iframeDoc.querySelector<HTMLElement>(`.page[data-page-number="${pageNumber}"]`)
}

const getPageTextLayer = (iframeDoc: Document, pageNumber: number): HTMLElement | null => {
  return getPageContainer(iframeDoc, pageNumber)?.querySelector<HTMLElement>('.textLayer') ?? null
}

const getPdfJsTextLayerBuilder = (
  pdfApp: any,
  pageNumber: number,
): {
  pageContainer: HTMLElement
  textContentItemsStr: string[]
  textDivs: Array<HTMLElement | Text>
} | null => {
  const pageView = pdfApp?.pdfViewer?.getPageView?.(pageNumber - 1)
  const pageContainer = pageView?.div
  const textLayer = pageView?.textLayer

  if (
    !pageContainer
    || !textLayer
    || !Array.isArray(textLayer.textDivs)
    || !Array.isArray(textLayer.textContentItemsStr)
    || textLayer.textDivs.length === 0
    || textLayer.textContentItemsStr.length === 0
  ) {
    return null
  }

  return {
    pageContainer,
    textContentItemsStr: textLayer.textContentItemsStr,
    textDivs: textLayer.textDivs,
  }
}

const findPdfJsSelectedHighlightRects = (
  iframeDoc: Document,
  pageNumber: number,
): EvidenceTextLayerRect[] => {
  const pageContainer = getPageContainer(iframeDoc, pageNumber)
  const textLayer = getPageTextLayer(iframeDoc, pageNumber)

  if (!pageContainer || !textLayer) {
    return []
  }

  const selectedHighlights = Array.from(
    textLayer.querySelectorAll<HTMLElement>('.highlight.selected'),
  )
  if (selectedHighlights.length === 0) {
    return []
  }

  const seen = new Set<string>()
  return selectedHighlights.reduce<EvidenceTextLayerRect[]>((acc, node) => {
    pushUniqueEvidenceTextLayerRect(
      acc,
      seen,
      normalizeEvidenceTextLayerRect(pageContainer, node.getBoundingClientRect()),
    )
    return acc
  }, [])
}

const convertPdfJsMatches = (
  matches: number[] | undefined | null,
  matchesLength: number[] | undefined | null,
  textContentItemsStr: string[],
): PdfJsTextMatch[] => {
  if (!matches || !matchesLength || matches.length === 0 || textContentItemsStr.length === 0) {
    return []
  }

  let divIndex = 0
  let itemStart = 0
  const end = textContentItemsStr.length - 1
  const converted: PdfJsTextMatch[] = []

  for (let matchIndex = 0; matchIndex < matches.length; matchIndex += 1) {
    let startIndex = matches[matchIndex] ?? 0

    while (divIndex !== end && startIndex >= itemStart + textContentItemsStr[divIndex]!.length) {
      itemStart += textContentItemsStr[divIndex]!.length
      divIndex += 1
    }

    if (divIndex >= textContentItemsStr.length) {
      break
    }

    const match: PdfJsTextMatch = {
      begin: {
        divIdx: divIndex,
        offset: startIndex - itemStart,
      },
      end: {
        divIdx: divIndex,
        offset: 0,
      },
    }

    startIndex += matchesLength[matchIndex] ?? 0

    while (divIndex !== end && startIndex > itemStart + textContentItemsStr[divIndex]!.length) {
      itemStart += textContentItemsStr[divIndex]!.length
      divIndex += 1
    }

    match.end = {
      divIdx: divIndex,
      offset: startIndex - itemStart,
    }
    converted.push(match)
  }

  return converted
}

const collectTextNodesForEvidenceMatch = (container: HTMLElement | Text): Text[] => {
  if (container.nodeType === Node.TEXT_NODE) {
    return [container as Text]
  }

  const walker = container.ownerDocument.createTreeWalker(container, NodeFilter.SHOW_TEXT)
  const textNodes: Text[] = []
  for (let current = walker.nextNode(); current !== null; current = walker.nextNode()) {
    if (current.textContent && current.textContent.length > 0) {
      textNodes.push(current as Text)
    }
  }
  return textNodes
}

const buildRectsFromPdfJsTextDivOffsets = (
  pageContainer: HTMLElement,
  container: HTMLElement | Text,
  startOffset: number,
  endOffset: number,
  acc: EvidenceTextLayerRect[],
  seen: Set<string>,
): void => {
  if (endOffset <= startOffset) {
    return
  }

  const textNodes = collectTextNodesForEvidenceMatch(container)
  let cumulativeOffset = 0

  for (const textNode of textNodes) {
    const textLength = textNode.textContent?.length ?? 0
    if (textLength === 0) {
      continue
    }

    const nodeStart = Math.max(0, startOffset - cumulativeOffset)
    const nodeEnd = Math.min(textLength, endOffset - cumulativeOffset)

    if (nodeStart < nodeEnd) {
      const range = textNode.ownerDocument.createRange()
      range.setStart(textNode, nodeStart)
      range.setEnd(textNode, nodeEnd)

      const clientRects = Array.from(range.getClientRects?.() ?? [])
      if (clientRects.length > 0) {
        clientRects.forEach((rect) => {
          pushUniqueEvidenceTextLayerRect(
            acc,
            seen,
            normalizeEvidenceTextLayerRect(pageContainer, rect),
          )
        })
      } else {
        pushUniqueEvidenceTextLayerRect(
          acc,
          seen,
          normalizeEvidenceTextLayerRect(pageContainer, range.getBoundingClientRect()),
        )
      }
    }

    cumulativeOffset += textLength
    if (cumulativeOffset >= endOffset) {
      return
    }
  }
}

const findPdfJsMappedMatchRects = (
  pdfApp: any,
  pageNumber: number,
  pageMatchIndex: number | null = null,
): EvidenceTextLayerRect[] => {
  const textLayerBuilder = getPdfJsTextLayerBuilder(pdfApp, pageNumber)
  if (!textLayerBuilder) {
    return []
  }

  const pageMatches = pdfApp?.findController?.pageMatches?.[pageNumber - 1]
  const pageMatchesLength = pdfApp?.findController?.pageMatchesLength?.[pageNumber - 1]
  const convertedMatches = convertPdfJsMatches(
    pageMatches,
    pageMatchesLength,
    textLayerBuilder.textContentItemsStr,
  )

  if (convertedMatches.length === 0) {
    return []
  }

  const selectedIndex = pageMatchIndex ?? pdfApp?.findController?.selected?.matchIdx ?? 0
  const match = convertedMatches[selectedIndex] ?? convertedMatches[0]
  if (!match) {
    return []
  }

  const seen = new Set<string>()
  const rects: EvidenceTextLayerRect[] = []

  if (match.begin.divIdx === match.end.divIdx) {
    const container = textLayerBuilder.textDivs[match.begin.divIdx]
    if (!container) {
      return []
    }
    buildRectsFromPdfJsTextDivOffsets(
      textLayerBuilder.pageContainer,
      container,
      match.begin.offset,
      match.end.offset,
      rects,
      seen,
    )
    return rects
  }

  const beginContainer = textLayerBuilder.textDivs[match.begin.divIdx]
  if (beginContainer) {
    buildRectsFromPdfJsTextDivOffsets(
      textLayerBuilder.pageContainer,
      beginContainer,
      match.begin.offset,
      textLayerBuilder.textContentItemsStr[match.begin.divIdx]?.length ?? match.begin.offset,
      rects,
      seen,
    )
  }

  for (let divIdx = match.begin.divIdx + 1; divIdx < match.end.divIdx; divIdx += 1) {
    const container = textLayerBuilder.textDivs[divIdx]
    if (!container) {
      continue
    }
    buildRectsFromPdfJsTextDivOffsets(
      textLayerBuilder.pageContainer,
      container,
      0,
      textLayerBuilder.textContentItemsStr[divIdx]?.length ?? 0,
      rects,
      seen,
    )
  }

  const endContainer = textLayerBuilder.textDivs[match.end.divIdx]
  if (endContainer) {
    buildRectsFromPdfJsTextDivOffsets(
      textLayerBuilder.pageContainer,
      endContainer,
      0,
      match.end.offset,
      rects,
      seen,
    )
  }

  return rects
}

const buildTextLayerSegments = (textLayer: HTMLElement): {
  rawText: string
  segments: TextLayerTextSegment[]
} => {
  const segments: TextLayerTextSegment[] = []
  const walker = textLayer.ownerDocument.createTreeWalker(textLayer, NodeFilter.SHOW_TEXT)
  let rawText = ''

  for (let current = walker.nextNode(); current !== null; current = walker.nextNode()) {
    const node = current as Text
    const value = node.textContent ?? ''
    if (value.length === 0) {
      continue
    }

    segments.push({
      node,
      container: node.parentElement ?? textLayer,
      start: rawText.length,
      end: rawText.length + value.length,
    })
    rawText += value
  }

  return { rawText, segments }
}

const buildPdfJsTextLayerSegments = (
  textLayerBuilder: {
    pageContainer: HTMLElement
    textContentItemsStr: string[]
    textDivs: Array<HTMLElement | Text>
  },
): {
  rawText: string
  segments: TextLayerTextSegment[]
} => {
  const segments: TextLayerTextSegment[] = []
  let rawText = ''

  textLayerBuilder.textDivs.forEach((container, index) => {
    const itemText = textLayerBuilder.textContentItemsStr[index] ?? ''
    if (!itemText) {
      return
    }

    const textNodes = collectTextNodesForEvidenceMatch(container)
    let itemOffset = 0

    textNodes.forEach((textNode) => {
      const value = textNode.textContent ?? ''
      if (!value) {
        return
      }

      const containerElement = textNode.parentElement
        ?? (container.nodeType === Node.ELEMENT_NODE ? container as HTMLElement : textLayerBuilder.pageContainer)

      segments.push({
        node: textNode,
        container: containerElement,
        start: rawText.length + itemOffset,
        end: rawText.length + itemOffset + value.length,
      })
      itemOffset += value.length
    })

    rawText += itemText
  })

  return { rawText, segments }
}

const buildPageTextDebugSnapshot = (
  iframeDoc: Document,
  pdfApp: any,
  pageNumber: number,
): {
  source: 'pdfjs-page-contents' | 'pdfjs-text-layer-builder' | 'dom-text-layer' | 'unavailable'
  length: number
  preview: string
} => {
  const pageContents = pdfApp?.findController?._pageContents?.[pageNumber - 1]
  if (typeof pageContents === 'string' && pageContents.trim().length > 0) {
    return {
      source: 'pdfjs-page-contents',
      length: pageContents.length,
      preview: truncateDebugText(pageContents, 320),
    }
  }

  const textLayerBuilder = getPdfJsTextLayerBuilder(pdfApp, pageNumber)
  if (textLayerBuilder) {
    const joinedText = textLayerBuilder.textContentItemsStr.join('')
    if (joinedText.trim().length > 0) {
      return {
        source: 'pdfjs-text-layer-builder',
        length: joinedText.length,
        preview: truncateDebugText(joinedText, 320),
      }
    }
  }

  const textLayer = getPageTextLayer(iframeDoc, pageNumber)
  if (textLayer) {
    const { rawText } = buildTextLayerSegments(textLayer)
    if (rawText.trim().length > 0) {
      return {
        source: 'dom-text-layer',
        length: rawText.length,
        preview: truncateDebugText(rawText, 320),
      }
    }
  }

  return {
    source: 'unavailable',
    length: 0,
    preview: '',
  }
}

const chooseBetterAnchoredDebugWindow = (
  currentBest: AnchoredEvidenceSpanWindowDebug | null,
  candidate: AnchoredEvidenceSpanWindowDebug,
): AnchoredEvidenceSpanWindowDebug => {
  if (!currentBest) {
    return candidate
  }

  if (candidate.passesThreshold !== currentBest.passesThreshold) {
    return candidate.passesThreshold ? candidate : currentBest
  }

  const candidateCoverage = candidate.coverage ?? -1
  const currentCoverage = currentBest.coverage ?? -1
  if (candidateCoverage !== currentCoverage) {
    return candidateCoverage > currentCoverage ? candidate : currentBest
  }

  const candidateScore = candidate.score ?? -1
  const currentScore = currentBest.score ?? -1
  if (candidateScore !== currentScore) {
    return candidateScore > currentScore ? candidate : currentBest
  }

  if (candidate.matchedPairCount !== currentBest.matchedPairCount) {
    return candidate.matchedPairCount > currentBest.matchedPairCount ? candidate : currentBest
  }

  return candidate.searchWindow.rawStart < currentBest.searchWindow.rawStart ? candidate : currentBest
}

const summarizeAnchoredDebugWindow = (
  windowDebug: AnchoredEvidenceSpanWindowDebug,
): Record<string, unknown> => ({
  resolution: windowDebug.resolution,
  searchWindow: windowDebug.searchWindow,
  adjustedPreferredRawRange: windowDebug.adjustedPreferredRawRange,
  quoteTokenCount: windowDebug.quoteTokenCount,
  pageTokenCount: windowDebug.pageTokenCount,
  exactMatchCount: windowDebug.exactMatchCount,
  alignmentScore: windowDebug.alignmentScore,
  matchedPairCount: windowDebug.matchedPairCount,
  boundaryMatchCount: windowDebug.boundaryMatchCount,
  leadingContiguousMatchCount: windowDebug.leadingContiguousMatchCount,
  trailingContiguousMatchCount: windowDebug.trailingContiguousMatchCount,
  coverage: windowDebug.coverage,
  score: windowDebug.score,
  spanDensity: windowDebug.spanDensity,
  largestMatchedPageGap: windowDebug.largestMatchedPageGap,
  leadingAnchorMatched: windowDebug.leadingAnchorMatched,
  trailingAnchorMatched: windowDebug.trailingAnchorMatched,
  firstLeadingMismatch: windowDebug.firstLeadingMismatch,
  firstTrailingMismatch: windowDebug.firstTrailingMismatch,
  matchedTokenPreview: windowDebug.matchedTokenPreview,
  includesPreferredAnchor: windowDebug.includesPreferredAnchor,
  containsPreferredRawRange: windowDebug.containsPreferredRawRange,
  candidateRawRange: windowDebug.candidateRawRange,
  candidateRawQuery: windowDebug.candidateRawQuery
    ? truncateDebugText(windowDebug.candidateRawQuery, 220)
    : null,
  passesThreshold: windowDebug.passesThreshold,
  rejectionReasons: windowDebug.rejectionReasons,
})

const summarizeAnchoredDebugWindows = (
  windows: AnchoredEvidenceSpanWindowDebug[],
  limit: number = 3,
): Record<string, unknown>[] => {
  const bestWindows = windows.reduce<AnchoredEvidenceSpanWindowDebug[]>((selected, windowDebug) => {
    const next = [...selected, windowDebug]
      .sort((left, right) => {
        const better = chooseBetterAnchoredDebugWindow(left, right)
        return better === left ? -1 : 1
      })
      .slice(0, limit)
    return next
  }, [])

  return bestWindows.map((windowDebug) => summarizeAnchoredDebugWindow(windowDebug))
}

const getEvidenceAnchoringPageText = (
  iframeDoc: Document,
  pdfApp: any,
  pageNumber: number,
): {
  rawText: string
  source: 'pdfjs-page-contents' | 'pdfjs-text-layer-builder' | 'dom-text-layer'
} | null => {
  const pageContents = pdfApp?.findController?._pageContents?.[pageNumber - 1]
  if (typeof pageContents === 'string' && pageContents.trim().length > 0) {
    return {
      rawText: pageContents,
      source: 'pdfjs-page-contents',
    }
  }

  const textLayerBuilder = getPdfJsTextLayerBuilder(pdfApp, pageNumber)
  if (textLayerBuilder) {
    const { rawText } = buildPdfJsTextLayerSegments(textLayerBuilder)
    if (rawText.trim().length > 0) {
      return {
        rawText,
        source: 'pdfjs-text-layer-builder',
      }
    }
  }

  const textLayer = getPageTextLayer(iframeDoc, pageNumber)
  if (textLayer) {
    const { rawText } = buildTextLayerSegments(textLayer)
    if (rawText.trim().length > 0) {
      return {
        rawText,
        source: 'dom-text-layer',
      }
    }
  }

  return null
}

const getEvidenceAnchoringPageNumbers = (
  iframeDoc: Document,
  pdfApp: any,
  pageCountHint?: number | null,
): number[] => {
  const hintedPageCount = typeof pageCountHint === 'number' && pageCountHint > 0
    ? Math.max(0, Math.floor(pageCountHint))
    : 0
  const pdfDocumentPageCount = typeof pdfApp?.pdfDocument?.numPages === 'number' && pdfApp.pdfDocument.numPages > 0
    ? Math.floor(pdfApp.pdfDocument.numPages)
    : 0
  const pageContentsCount = Array.isArray(pdfApp?.findController?._pageContents)
    ? pdfApp.findController._pageContents.length
    : 0
  const domPageNumbers = Array.from(
    iframeDoc.querySelectorAll<HTMLElement>('.page[data-page-number]'),
  )
    .map((page) => Number.parseInt(page.dataset.pageNumber ?? '', 10))
    .filter((pageNumber) => Number.isFinite(pageNumber) && pageNumber >= 1)
  const maxDomPageNumber = domPageNumbers.length > 0 ? Math.max(...domPageNumbers) : 0
  const resolvedPageCount = Math.max(
    hintedPageCount,
    pdfDocumentPageCount,
    pageContentsCount,
    maxDomPageNumber,
  )

  return Array.from({ length: resolvedPageCount }, (_, index) => index + 1)
}

const chooseBetterDocumentAnchoredCandidate = (
  currentBest: {
    pageNumber: number
    query: string
    coverage: number
    score: number
    queryLength: number
  } | null,
  candidate: {
    pageNumber: number
    query: string
    coverage: number
    score: number
    queryLength: number
  },
  preferredPage: number | null,
): {
  pageNumber: number
  query: string
  coverage: number
  score: number
  queryLength: number
} => {
  if (!currentBest) {
    return candidate
  }

  if (candidate.coverage !== currentBest.coverage) {
    return candidate.coverage > currentBest.coverage ? candidate : currentBest
  }

  if (candidate.score !== currentBest.score) {
    return candidate.score > currentBest.score ? candidate : currentBest
  }

  if (candidate.queryLength !== currentBest.queryLength) {
    return candidate.queryLength > currentBest.queryLength ? candidate : currentBest
  }

  if (preferredPage !== null) {
    const candidateDistance = Math.abs(candidate.pageNumber - preferredPage)
    const currentDistance = Math.abs(currentBest.pageNumber - preferredPage)
    if (candidateDistance !== currentDistance) {
      return candidateDistance < currentDistance ? candidate : currentBest
    }
  }

  return candidate.pageNumber < currentBest.pageNumber ? candidate : currentBest
}

const findAnchoredEvidenceSpanAcrossDocument = (
  iframeDoc: Document,
  pdfApp: any,
  desiredQuote: string,
  options?: {
    pageCountHint?: number | null
    preferredPage?: number | null
  },
): {
  pageNumber: number
  query: string
  coverage: number
  score: number
} | null => {
  const preferredPage = options?.preferredPage ?? null
  const pageNumbers = getEvidenceAnchoringPageNumbers(iframeDoc, pdfApp, options?.pageCountHint)
  let bestCandidate: {
    pageNumber: number
    query: string
    coverage: number
    score: number
    queryLength: number
  } | null = null
  const pageDiagnostics: Array<{
    pageNumber: number
    source: 'pdfjs-page-contents' | 'pdfjs-text-layer-builder' | 'dom-text-layer' | 'unavailable'
    rawTextLength: number
    matched: boolean
    coverage: number | null
    score: number | null
    queryPreview: string | null
    bestWindow: Record<string, unknown> | null
  }> = []

  pageNumbers.forEach((pageNumber) => {
    const pageText = getEvidenceAnchoringPageText(iframeDoc, pdfApp, pageNumber)
    if (!pageText) {
      pageDiagnostics.push({
        pageNumber,
        source: 'unavailable',
        rawTextLength: 0,
        matched: false,
        coverage: null,
        score: null,
        queryPreview: null,
        bestWindow: null,
      })
      return
    }

    const anchoredSpanDebug = getAnchoredEvidenceSpanDebugInfo(pageText.rawText, desiredQuote)
    const bestMatch = anchoredSpanDebug.bestMatch
    const bestWindowSummary = summarizeAnchoredDebugWindows(anchoredSpanDebug.windows, 1)[0] ?? null
    pageDiagnostics.push({
      pageNumber,
      source: pageText.source,
      rawTextLength: pageText.rawText.length,
      matched: Boolean(bestMatch),
      coverage: bestMatch?.coverage ?? null,
      score: bestMatch?.score ?? null,
      queryPreview: bestMatch ? truncateDebugText(bestMatch.rawQuery, 160) : null,
      bestWindow: bestWindowSummary,
    })

    if (!bestMatch) {
      return
    }

    bestCandidate = chooseBetterDocumentAnchoredCandidate(
      bestCandidate,
      {
        pageNumber,
        query: bestMatch.rawQuery,
        coverage: bestMatch.coverage,
        score: bestMatch.score,
        queryLength: bestMatch.rawEndExclusive - bestMatch.rawStart,
      },
      preferredPage,
    )
  })

  if (isPdfEvidenceDebugEnabled()) {
    logPdfEvidenceDebug(
      bestCandidate
        ? 'Document-wide anchored quote scan found a best matching page'
        : 'Document-wide anchored quote scan found no reliable page candidate',
      {
        desiredQuotePreview: truncateDebugText(desiredQuote, 220),
        preferredPage,
        pageCount: pageNumbers.length,
        bestCandidate: bestCandidate
          ? {
            pageNumber: bestCandidate.pageNumber,
            query: truncateDebugText(bestCandidate.query, 200),
            coverage: bestCandidate.coverage,
            score: bestCandidate.score,
          }
          : null,
        pages: pageDiagnostics,
      },
    )
  }

  return bestCandidate
    ? {
      pageNumber: bestCandidate.pageNumber,
      query: bestCandidate.query,
      coverage: bestCandidate.coverage,
      score: bestCandidate.score,
    }
    : null
}

const buildFindControllerDebugSnapshot = (
  pdfApp: any,
  pageNumber: number,
): Record<string, unknown> => {
  const pageMatches = pdfApp?.findController?.pageMatches?.[pageNumber - 1]
  const pageMatchesLength = pdfApp?.findController?.pageMatchesLength?.[pageNumber - 1]
  return {
    selectedPage: getSelectedEvidenceSpikePage(pdfApp),
    selectedMatchIndex: getSelectedEvidenceSpikeMatchIndex(pdfApp),
    pageMatchesCount: Array.isArray(pageMatches) ? pageMatches.length : 0,
    pageMatchesSample: Array.isArray(pageMatches) ? pageMatches.slice(0, 5) : [],
    pageMatchesLengthSample: Array.isArray(pageMatchesLength) ? pageMatchesLength.slice(0, 5) : [],
  }
}

const normalizeEvidenceTextLayerRect = (
  pageContainer: HTMLElement,
  rect: Pick<DOMRect, 'left' | 'top' | 'width' | 'height'>,
): EvidenceTextLayerRect | null => {
  const pageRect = pageContainer.getBoundingClientRect()
  if (rect.width <= 0 || rect.height <= 0) {
    return null
  }

  return {
    left: rect.left - pageRect.left,
    top: rect.top - pageRect.top,
    width: rect.width,
    height: rect.height,
  }
}

const pushUniqueEvidenceTextLayerRect = (
  acc: EvidenceTextLayerRect[],
  seen: Set<string>,
  rect: EvidenceTextLayerRect | null,
): void => {
  if (!rect) {
    return
  }

  const key = `${rect.left}:${rect.top}:${rect.width}:${rect.height}`
  if (seen.has(key)) {
    return
  }

  seen.add(key)
  acc.push(rect)
}

const getSourceCodeUnitLength = (value: string, index: number): number => {
  const codePoint = value.codePointAt(index)
  return codePoint !== undefined && codePoint > 0xffff ? 2 : 1
}

const buildTextMatchRangeDebugSnapshot = (
  rawText: string,
  query: string,
  pageMatchIndex: number | null,
): Record<string, unknown> => {
  const normalizedQuery = normalizeEvidenceSpikeText(query)
  const sourceMap = buildNormalizedTextSourceMap(rawText)
  const normalizedPageText = sourceMap.text.toLocaleLowerCase()
  const normalizedCandidate = normalizedQuery.toLocaleLowerCase()
  const matchRanges: Array<{
    rawStart: number
    rawEndExclusive: number
    preview: string
  }> = []

  if (normalizedPageText && normalizedCandidate) {
    let searchStart = 0
    while (searchStart <= normalizedPageText.length - normalizedCandidate.length) {
      const matchIndex = normalizedPageText.indexOf(normalizedCandidate, searchStart)
      if (matchIndex < 0) {
        break
      }

      const rawStart = sourceMap.sourceIndices[matchIndex]
      const rawEnd = sourceMap.sourceIndices[matchIndex + normalizedCandidate.length - 1]
      if (rawStart !== undefined && rawEnd !== undefined) {
        const rawEndExclusive = rawEnd + getSourceCodeUnitLength(rawText, rawEnd)
        matchRanges.push({
          rawStart,
          rawEndExclusive,
          preview: truncateDebugText(
            rawText.slice(
              Math.max(0, rawStart - 60),
              Math.min(rawText.length, rawEndExclusive + 60),
            ),
            220,
          ),
        })
      }

      searchStart = matchIndex + normalizedCandidate.length
    }
  }

  const selectedIndex = pageMatchIndex ?? 0
  return {
    rawTextLength: rawText.length,
    normalizedPageTextLength: normalizedPageText.length,
    queryLength: query.length,
    normalizedQueryLength: normalizedCandidate.length,
    normalizedQueryPreview: truncateDebugText(normalizedCandidate, 220),
    matchCount: matchRanges.length,
    selectedIndex,
    selectedMatch: matchRanges[selectedIndex] ?? matchRanges[0] ?? null,
    sampleMatches: matchRanges.slice(0, 4),
  }
}

const buildTextLayerStructureDebugSnapshot = (
  iframeDoc: Document,
  pageNumber: number,
  pdfApp?: any,
): Record<string, unknown> => {
  const pageContainer = getPageContainer(iframeDoc, pageNumber)
  const textLayer = getPageTextLayer(iframeDoc, pageNumber)
  const textLayerBuilder = getPdfJsTextLayerBuilder(pdfApp, pageNumber)
  const domText = textLayer ? buildTextLayerSegments(textLayer) : null

  return {
    pageNumber,
    hasPageContainer: Boolean(pageContainer),
    hasTextLayer: Boolean(textLayer),
    domRawTextLength: domText?.rawText.length ?? 0,
    domSegmentCount: domText?.segments.length ?? 0,
    hasPdfJsTextLayerBuilder: Boolean(textLayerBuilder),
    pdfJsTextDivCount: textLayerBuilder?.textDivs?.length ?? 0,
    pdfJsItemCount: textLayerBuilder?.textContentItemsStr?.length ?? 0,
  }
}

const findTextLayerMatchRange = (
  rawText: string,
  query: string,
  pageMatchIndex: number | null,
): TextLayerMatchRange | null => {
  const normalizedQuery = normalizeEvidenceSpikeText(query)
  if (!normalizedQuery) {
    return null
  }

  const sourceMap = buildNormalizedTextSourceMap(rawText)
  const normalizedPageText = sourceMap.text.toLocaleLowerCase()
  const normalizedCandidate = normalizedQuery.toLocaleLowerCase()
  if (!normalizedPageText || !normalizedCandidate) {
    return null
  }

  const matchRanges: TextLayerMatchRange[] = []
  let searchStart = 0

  while (searchStart <= normalizedPageText.length - normalizedCandidate.length) {
    const matchIndex = normalizedPageText.indexOf(normalizedCandidate, searchStart)
    if (matchIndex < 0) {
      break
    }

    const rawStart = sourceMap.sourceIndices[matchIndex]
    const rawEnd = sourceMap.sourceIndices[matchIndex + normalizedCandidate.length - 1]
    if (rawStart !== undefined && rawEnd !== undefined) {
      matchRanges.push({
        rawStart,
        rawEndExclusive: rawEnd + getSourceCodeUnitLength(rawText, rawEnd),
      })
    }

    searchStart = matchIndex + normalizedCandidate.length
  }

  if (matchRanges.length === 0) {
    if (isPdfEvidenceDebugEnabled()) {
      logPdfEvidenceDebug('Normalized text-layer query did not produce any candidate ranges', {
        ...buildTextMatchRangeDebugSnapshot(rawText, query, pageMatchIndex),
      })
    }
    return null
  }

  const normalizedMatchIndex = pageMatchIndex ?? 0
  const selectedRange = matchRanges[normalizedMatchIndex] ?? matchRanges[0]
  if (isPdfEvidenceDebugEnabled()) {
    logPdfEvidenceDebug('Normalized text-layer query produced candidate ranges', {
      ...buildTextMatchRangeDebugSnapshot(rawText, query, pageMatchIndex),
      selectedRange,
    })
  }
  return selectedRange
}

const findPdfJsMatchRawRange = (
  pdfApp: any,
  pageNumber: number,
  pageMatchIndex: number | null,
): TextLayerMatchRange | null => {
  const pageMatches = pdfApp?.findController?.pageMatches?.[pageNumber - 1]
  const pageMatchesLength = pdfApp?.findController?.pageMatchesLength?.[pageNumber - 1]
  if (!Array.isArray(pageMatches) || !Array.isArray(pageMatchesLength) || pageMatches.length === 0) {
    if (isPdfEvidenceDebugEnabled()) {
      logPdfEvidenceDebug('PDF.js find controller had no raw match range for page', {
        pageNumber,
        pageMatchIndex,
        ...buildFindControllerDebugSnapshot(pdfApp, pageNumber),
      })
    }
    return null
  }

  const selectedIndex = pageMatchIndex ?? pdfApp?.findController?.selected?.matchIdx ?? 0
  const rawStart = pageMatches[selectedIndex] ?? pageMatches[0]
  const rawLength = pageMatchesLength[selectedIndex] ?? pageMatchesLength[0]
  if (
    typeof rawStart !== 'number'
    || rawStart < 0
    || typeof rawLength !== 'number'
    || rawLength <= 0
  ) {
    if (isPdfEvidenceDebugEnabled()) {
      logPdfEvidenceDebug('PDF.js find controller reported an invalid raw match range', {
        pageNumber,
        pageMatchIndex,
        selectedIndex,
        rawStart,
        rawLength,
        ...buildFindControllerDebugSnapshot(pdfApp, pageNumber),
      })
    }
    return null
  }

  const range = {
    rawStart,
    rawEndExclusive: rawStart + rawLength,
  }
  if (isPdfEvidenceDebugEnabled()) {
    logPdfEvidenceDebug('PDF.js find controller reported a raw match range', {
      pageNumber,
      pageMatchIndex,
      selectedIndex,
      range,
      ...buildFindControllerDebugSnapshot(pdfApp, pageNumber),
    })
  }
  return range
}

const buildTextRangeSegmentDebugSnapshot = (
  segments: TextLayerTextSegment[],
  matchRange: TextLayerMatchRange,
): Record<string, unknown> => {
  const intersectingSegments = segments.flatMap((segment, index) => {
    const segmentStart = Math.max(matchRange.rawStart, segment.start)
    const segmentEnd = Math.min(matchRange.rawEndExclusive, segment.end)
    if (segmentStart >= segmentEnd) {
      return []
    }

    return [{
      index,
      rawStart: segment.start,
      rawEndExclusive: segment.end,
      matchedRawStart: segmentStart,
      matchedRawEndExclusive: segmentEnd,
      text: truncateDebugText(segment.node.textContent ?? '', 120),
    }]
  })

  return {
    matchRange,
    matchedSegmentCount: intersectingSegments.length,
    startSegmentIndex: intersectingSegments[0]?.index ?? null,
    endSegmentIndex: intersectingSegments[intersectingSegments.length - 1]?.index ?? null,
    matchedSegments: intersectingSegments.slice(0, 8),
  }
}

const buildRectsFromTextRange = (
  pageContainer: HTMLElement,
  segments: TextLayerTextSegment[],
  matchRange: TextLayerMatchRange,
): EvidenceTextLayerRect[] => {
  const seen = new Set<string>()

  return segments.reduce<EvidenceTextLayerRect[]>((acc, segment) => {
    const segmentStart = Math.max(matchRange.rawStart, segment.start)
    const segmentEnd = Math.min(matchRange.rawEndExclusive, segment.end)
    if (segmentStart >= segmentEnd) {
      return acc
    }

    const startOffset = segmentStart - segment.start
    const endOffset = segmentEnd - segment.start
    const createRange = segment.node.ownerDocument.createRange?.bind(segment.node.ownerDocument)

    if (createRange) {
      const range = createRange()
      range.setStart(segment.node, startOffset)
      range.setEnd(segment.node, endOffset)

      const rangeClientRects = typeof (range as Range).getClientRects === 'function'
        ? Array.from((range as Range).getClientRects())
        : []
      if (rangeClientRects.length > 0) {
        rangeClientRects.forEach((rect) => {
          pushUniqueEvidenceTextLayerRect(
            acc,
            seen,
            normalizeEvidenceTextLayerRect(pageContainer, rect),
          )
        })
        return acc
      }

      const rangeBoundingRect = typeof (range as Range).getBoundingClientRect === 'function'
        ? (range as Range).getBoundingClientRect()
        : null
      const normalizedRangeRect = rangeBoundingRect
        ? normalizeEvidenceTextLayerRect(pageContainer, rangeBoundingRect)
        : null
      if (normalizedRangeRect) {
        pushUniqueEvidenceTextLayerRect(acc, seen, normalizedRangeRect)
        return acc
      }
    }

    const containerRect = segment.container.getBoundingClientRect()
    pushUniqueEvidenceTextLayerRect(
      acc,
      seen,
      normalizeEvidenceTextLayerRect(pageContainer, containerRect),
    )
    return acc
  }, [])
}

const findTextLayerMatchRects = (
  iframeDoc: Document,
  pageNumber: number,
  query: string,
  pageMatchIndex: number | null = null,
): EvidenceTextLayerRect[] => {
  const pageContainer = getPageContainer(iframeDoc, pageNumber)
  const textLayer = getPageTextLayer(iframeDoc, pageNumber)

  if (!pageContainer || !textLayer) {
    if (isPdfEvidenceDebugEnabled()) {
      logPdfEvidenceDebug('Text-layer rect mapping could not start because page DOM is incomplete', {
        pageNumber,
        query,
        pageMatchIndex,
        ...buildTextLayerStructureDebugSnapshot(iframeDoc, pageNumber),
      })
    }
    return []
  }

  const { rawText, segments } = buildTextLayerSegments(textLayer)
  if (!rawText || segments.length === 0) {
    if (isPdfEvidenceDebugEnabled()) {
      logPdfEvidenceDebug('Text-layer rect mapping found no DOM text content on the candidate page', {
        pageNumber,
        query,
        pageMatchIndex,
        rawTextLength: rawText.length,
        segmentCount: segments.length,
      })
    }
    return []
  }

  const matchRange = findTextLayerMatchRange(rawText, query, pageMatchIndex)
  if (!matchRange) {
    if (isPdfEvidenceDebugEnabled()) {
      logPdfEvidenceDebug('Text-layer rect mapping found text content but no candidate range for the query', {
        pageNumber,
        query,
        pageMatchIndex,
        ...buildTextMatchRangeDebugSnapshot(rawText, query, pageMatchIndex),
      })
    }
    return []
  }

  const rects = buildRectsFromTextRange(pageContainer, segments, matchRange)
  if (isPdfEvidenceDebugEnabled()) {
    logPdfEvidenceDebug('Text-layer rect mapping completed', {
      pageNumber,
      query,
      pageMatchIndex,
      matchRange,
      rectCount: rects.length,
      rangeSegments: buildTextRangeSegmentDebugSnapshot(segments, matchRange),
      ...buildTextMatchRangeDebugSnapshot(rawText, query, pageMatchIndex),
    })
  }
  return rects
}

const countNormalizedEvidenceWords = (value: string): number => {
  return splitNormalizedWords(value).length
}

const findNormalizedWordSequenceStarts = (
  haystackWords: string[],
  needleWords: string[],
): number[] => {
  if (needleWords.length === 0 || needleWords.length > haystackWords.length) {
    return []
  }

  const starts: number[] = []
  const normalizedHaystack = haystackWords.map((word) => word.toLocaleLowerCase())
  const normalizedNeedle = needleWords.map((word) => word.toLocaleLowerCase())

  for (let index = 0; index <= normalizedHaystack.length - normalizedNeedle.length; index += 1) {
    const matches = normalizedNeedle.every((word, offset) => normalizedHaystack[index + offset] === word)
    if (matches) {
      starts.push(index)
    }
  }

  return starts
}

export const findExpandedEvidenceQueryFromPageText = (
  rawPageText: string,
  desiredQuote: string,
  matchedFragmentQuery: string,
): ExpandedEvidenceQuery | null => {
  const normalizedPageText = normalizeEvidenceSpikeText(rawPageText).trim().toLocaleLowerCase()
  const desiredWords = splitNormalizedWords(sanitizeEvidenceSearchText(desiredQuote))
  const anchorWords = splitNormalizedWords(sanitizeEvidenceSearchText(matchedFragmentQuery))

  if (
    !normalizedPageText
    || desiredWords.length === 0
    || anchorWords.length === 0
    || anchorWords.length >= desiredWords.length
  ) {
    return null
  }

  const anchorStarts = findNormalizedWordSequenceStarts(desiredWords, anchorWords)
  if (anchorStarts.length === 0) {
    return null
  }

  let bestMatch: ExpandedEvidenceQuery | null = null

  anchorStarts.forEach((anchorStart) => {
    const anchorEnd = anchorStart + anchorWords.length

    for (let startIndex = 0; startIndex <= anchorStart; startIndex += 1) {
      for (let endIndex = desiredWords.length; endIndex >= anchorEnd; endIndex -= 1) {
        if (endIndex - startIndex <= anchorWords.length) {
          break
        }

        const candidateQuery = desiredWords.slice(startIndex, endIndex).join(' ')
        if (!normalizedPageText.includes(candidateQuery.toLocaleLowerCase())) {
          continue
        }

        const candidate: ExpandedEvidenceQuery = {
          query: candidateQuery,
          wordCount: endIndex - startIndex,
          startWordIndex: startIndex,
          endWordIndexExclusive: endIndex,
        }

        const isBetterCandidate = (
          bestMatch === null
          || candidate.wordCount > bestMatch.wordCount
          || (
            candidate.wordCount === bestMatch.wordCount
            && candidate.startWordIndex < bestMatch.startWordIndex
          )
        )

        if (isBetterCandidate) {
          bestMatch = candidate
        }

        break
      }
    }
  })

  return bestMatch
}

const findExpandedEvidenceQueryForPage = (
  iframeDoc: Document,
  pdfApp: any,
  pageNumber: number,
  desiredQuote: string,
  matchedFragmentQuery: string,
): ExpandedEvidenceQuery | null => {
  const normalizedPageText = pdfApp?.findController?._pageContents?.[pageNumber - 1]
  if (typeof normalizedPageText === 'string' && normalizedPageText.trim().length > 0) {
    const expanded = findExpandedEvidenceQueryFromPageText(
      normalizedPageText,
      desiredQuote,
      matchedFragmentQuery,
    )
    if (isPdfEvidenceDebugEnabled()) {
      logPdfEvidenceDebug('Tried to expand a matched fragment using PDF.js page contents', {
        pageNumber,
        desiredQuotePreview: truncateDebugText(desiredQuote, 220),
        matchedFragmentQuery,
        pageTextSource: 'pdfjs-page-contents',
        pageTextLength: normalizedPageText.length,
        expanded,
      })
    }
    return expanded
  }

  const textLayerBuilder = getPdfJsTextLayerBuilder(pdfApp, pageNumber)
  if (textLayerBuilder) {
    const joinedText = textLayerBuilder.textContentItemsStr.join('')
    if (joinedText.trim().length > 0) {
      const expanded = findExpandedEvidenceQueryFromPageText(
        joinedText,
        desiredQuote,
        matchedFragmentQuery,
      )
      if (isPdfEvidenceDebugEnabled()) {
        logPdfEvidenceDebug('Tried to expand a matched fragment using the PDF.js text layer builder', {
          pageNumber,
          desiredQuotePreview: truncateDebugText(desiredQuote, 220),
          matchedFragmentQuery,
          pageTextSource: 'pdfjs-text-layer-builder',
          pageTextLength: joinedText.length,
          expanded,
        })
      }
      return expanded
    }
  }

  const textLayer = getPageTextLayer(iframeDoc, pageNumber)
  if (!textLayer) {
    if (isPdfEvidenceDebugEnabled()) {
      logPdfEvidenceDebug('Could not expand a matched fragment because the page text layer is missing', {
        pageNumber,
        desiredQuotePreview: truncateDebugText(desiredQuote, 220),
        matchedFragmentQuery,
      })
    }
    return null
  }

  const { rawText } = buildTextLayerSegments(textLayer)
  if (!rawText) {
    if (isPdfEvidenceDebugEnabled()) {
      logPdfEvidenceDebug('Could not expand a matched fragment because the DOM text layer is empty', {
        pageNumber,
        desiredQuotePreview: truncateDebugText(desiredQuote, 220),
        matchedFragmentQuery,
      })
    }
    return null
  }

  const expanded = findExpandedEvidenceQueryFromPageText(rawText, desiredQuote, matchedFragmentQuery)
  if (isPdfEvidenceDebugEnabled()) {
    logPdfEvidenceDebug('Tried to expand a matched fragment using the DOM text layer', {
      pageNumber,
      desiredQuotePreview: truncateDebugText(desiredQuote, 220),
      matchedFragmentQuery,
      pageTextSource: 'dom-text-layer',
      pageTextLength: rawText.length,
      expanded,
    })
  }
  return expanded
}

const findAnchoredEvidenceSpanForPage = (
  iframeDoc: Document,
  pageNumber: number,
  desiredQuote: string,
  options?: {
    preferredAnchor?: string | null
    pageMatchIndex?: number | null
    pdfApp?: any
  },
): {
  query: string
  rects: EvidenceTextLayerRect[]
  coverage: number
  score: number
  rawStart: number
  rawEndExclusive: number
} | null => {
  const pageContainer = getPageContainer(iframeDoc, pageNumber)
  const textLayer = getPageTextLayer(iframeDoc, pageNumber)
  if (!pageContainer || !textLayer) {
    if (isPdfEvidenceDebugEnabled()) {
      logPdfEvidenceDebug('Anchored quote recovery could not start because the page DOM is incomplete', {
        pageNumber,
        desiredQuotePreview: truncateDebugText(desiredQuote, 220),
        ...buildTextLayerStructureDebugSnapshot(iframeDoc, pageNumber, options?.pdfApp),
      })
    }
    return null
  }

  const textLayerBuilder = getPdfJsTextLayerBuilder(options?.pdfApp, pageNumber)
  const pageText = textLayerBuilder
    ? buildPdfJsTextLayerSegments(textLayerBuilder)
    : buildTextLayerSegments(textLayer)
  const { rawText, segments } = pageText
  if (!rawText || segments.length === 0) {
    if (isPdfEvidenceDebugEnabled()) {
      logPdfEvidenceDebug('Anchored quote recovery found no page text to align against', {
        pageNumber,
        desiredQuotePreview: truncateDebugText(desiredQuote, 220),
        pageTextSource: textLayerBuilder ? 'pdfjs-text-layer-builder' : 'dom-text-layer',
        rawTextLength: rawText.length,
        segmentCount: segments.length,
      })
    }
    return null
  }

  const preferredAnchorRange = options?.preferredAnchor
    ? (
        findPdfJsMatchRawRange(options?.pdfApp, pageNumber, options?.pageMatchIndex ?? null)
        ?? findTextLayerMatchRange(rawText, options.preferredAnchor, options?.pageMatchIndex ?? null)
      )
    : null
  const anchoringWindowRadius = Math.max(desiredQuote.length, 240)
  const rawWindowStart = preferredAnchorRange
    ? Math.max(0, preferredAnchorRange.rawStart - anchoringWindowRadius)
    : 0
  const rawWindowEnd = preferredAnchorRange
    ? Math.min(rawText.length, preferredAnchorRange.rawEndExclusive + anchoringWindowRadius)
    : rawText.length
  const preferredWindowRange = preferredAnchorRange
    ? {
        rawStart: Math.max(0, preferredAnchorRange.rawStart - rawWindowStart),
        rawEndExclusive: Math.min(
          rawWindowEnd - rawWindowStart,
          preferredAnchorRange.rawEndExclusive - rawWindowStart,
        ),
      }
    : null

  const anchoredSpan = findAnchoredEvidenceSpan(rawText.slice(rawWindowStart, rawWindowEnd), desiredQuote, {
    preferredAnchor: options?.preferredAnchor,
    preferredRawRange: preferredWindowRange,
  })
  if (isPdfEvidenceDebugEnabled()) {
    const anchoredSpanDebug = getAnchoredEvidenceSpanDebugInfo(
      rawText.slice(rawWindowStart, rawWindowEnd),
      desiredQuote,
      {
        preferredAnchor: options?.preferredAnchor,
        preferredRawRange: preferredWindowRange,
      },
    )
    logPdfEvidenceDebug(
      anchoredSpan
        ? 'Anchored quote recovery produced a candidate span from the page text'
        : 'Anchored quote recovery could not produce a candidate span from the page text',
      {
        pageNumber,
        desiredQuotePreview: truncateDebugText(desiredQuote, 220),
        preferredAnchor: options?.preferredAnchor ?? null,
        pageMatchIndex: options?.pageMatchIndex ?? null,
        pageTextSource: textLayerBuilder ? 'pdfjs-text-layer-builder' : 'dom-text-layer',
        rawTextLength: rawText.length,
        searchWindow: {
          rawStart: rawWindowStart,
          rawEndExclusive: rawWindowEnd,
        },
        preferredAnchorRange,
        preferredWindowRange,
        bestMatch: anchoredSpanDebug.bestMatch
          ? {
            rawStart: anchoredSpanDebug.bestMatch.rawStart + rawWindowStart,
            rawEndExclusive: anchoredSpanDebug.bestMatch.rawEndExclusive + rawWindowStart,
            rawQuery: truncateDebugText(anchoredSpanDebug.bestMatch.rawQuery, 240),
            coverage: anchoredSpanDebug.bestMatch.coverage,
            score: anchoredSpanDebug.bestMatch.score,
          }
          : null,
        topWindows: summarizeAnchoredDebugWindows(anchoredSpanDebug.windows),
        windows: anchoredSpanDebug.windows,
        pageTextSnapshot: {
          source: textLayerBuilder ? 'pdfjs-text-layer-builder' : 'dom-text-layer',
          preview: truncateDebugText(rawText.slice(rawWindowStart, rawWindowEnd), 360),
        },
      },
    )
  }
  if (!anchoredSpan) {
    return null
  }

  const rects = buildRectsFromTextRange(pageContainer, segments, {
    rawStart: anchoredSpan.rawStart + rawWindowStart,
    rawEndExclusive: anchoredSpan.rawEndExclusive + rawWindowStart,
  })
  if (rects.length === 0) {
    if (isPdfEvidenceDebugEnabled()) {
      logPdfEvidenceDebug('Anchored quote recovery found a span but rect mapping for that span returned no boxes', {
        pageNumber,
        desiredQuotePreview: truncateDebugText(desiredQuote, 220),
        query: anchoredSpan.rawQuery,
        rawStart: anchoredSpan.rawStart + rawWindowStart,
        rawEndExclusive: anchoredSpan.rawEndExclusive + rawWindowStart,
        rangeSegments: buildTextRangeSegmentDebugSnapshot(segments, {
          rawStart: anchoredSpan.rawStart + rawWindowStart,
          rawEndExclusive: anchoredSpan.rawEndExclusive + rawWindowStart,
        }),
      })
    }
    return null
  }

  return {
    query: anchoredSpan.rawQuery,
    rects,
    coverage: anchoredSpan.coverage,
    score: anchoredSpan.score,
    rawStart: anchoredSpan.rawStart + rawWindowStart,
    rawEndExclusive: anchoredSpan.rawEndExclusive + rawWindowStart,
  }
}

const getEvidenceTextLayerCandidatePages = (pageNumber: number, pdfApp: any): number[] => {
  const liveSelectedPage = getSelectedEvidenceSpikePage(pdfApp)
  return liveSelectedPage !== null && liveSelectedPage !== pageNumber
    ? [pageNumber, liveSelectedPage]
    : [pageNumber]
}

const tryResolveTextLayerMatch = (
  iframeDoc: Document,
  pageNumber: number,
  query: string,
  pageMatchIndex: number | null = null,
  options?: {
    pdfApp?: any
  },
): EvidenceTextLayerMatchResult | null => {
  for (const candidatePage of getEvidenceTextLayerCandidatePages(pageNumber, options?.pdfApp)) {
    const nativeHighlightRects = findPdfJsSelectedHighlightRects(iframeDoc, candidatePage)
    if (nativeHighlightRects.length > 0) {
      logPdfEvidenceDebug('Resolved text-layer match from native PDF.js highlight rects', {
        initialPageNumber: pageNumber,
        candidatePage,
        query,
        pageMatchIndex,
        rectCount: nativeHighlightRects.length,
      })
      return {
        rects: nativeHighlightRects,
        matchedPage: candidatePage,
      }
    }

    const mappedRects = findPdfJsMappedMatchRects(
      options?.pdfApp,
      candidatePage,
      pageMatchIndex,
    )
    if (mappedRects.length > 0) {
      logPdfEvidenceDebug('Resolved text-layer match from PDF.js match-offset rect mapping', {
        initialPageNumber: pageNumber,
        candidatePage,
        query,
        pageMatchIndex,
        rectCount: mappedRects.length,
        ...buildFindControllerDebugSnapshot(options?.pdfApp, candidatePage),
      })
      return {
        rects: mappedRects,
        matchedPage: candidatePage,
      }
    }

    const rects = findTextLayerMatchRects(iframeDoc, candidatePage, query, pageMatchIndex)
    if (rects.length > 0) {
      logPdfEvidenceDebug('Resolved text-layer match from DOM text-layer remapping', {
        initialPageNumber: pageNumber,
        candidatePage,
        query,
        pageMatchIndex,
        rectCount: rects.length,
      })
      return {
        rects,
        matchedPage: candidatePage,
      }
    }

    if (isPdfEvidenceDebugEnabled()) {
      logPdfEvidenceDebug('A candidate page did not yield quote highlight rects from any text-layer strategy', {
        initialPageNumber: pageNumber,
        candidatePage,
        query,
        pageMatchIndex,
        ...buildTextLayerStructureDebugSnapshot(iframeDoc, candidatePage, options?.pdfApp),
        pageTextSnapshot: buildPageTextDebugSnapshot(iframeDoc, options?.pdfApp, candidatePage),
        findController: buildFindControllerDebugSnapshot(options?.pdfApp, candidatePage),
      })
    }
  }

  return null
}

const waitForTextLayerMatch = async (
  iframeDoc: Document,
  pageNumber: number,
  query: string,
  pageMatchIndex: number | null = null,
  timeoutMs: number = PDF_TEXT_LAYER_MATCH_TIMEOUT_MS,
  options?: {
    pdfApp?: any
  },
): Promise<EvidenceTextLayerMatchResult> => {
  const startedAt = Date.now()
  if (isPdfEvidenceDebugEnabled()) {
    logPdfEvidenceDebug('Waiting for quote highlight rects on the text layer', {
      pageNumber,
      query,
      pageMatchIndex,
      timeoutMs,
      pageTextSnapshot: buildPageTextDebugSnapshot(iframeDoc, options?.pdfApp, pageNumber),
      textLayer: buildTextLayerStructureDebugSnapshot(iframeDoc, pageNumber, options?.pdfApp),
      findController: buildFindControllerDebugSnapshot(options?.pdfApp, pageNumber),
    })
  }
  const immediateMatch = tryResolveTextLayerMatch(
    iframeDoc,
    pageNumber,
    query,
    pageMatchIndex,
    options,
  )
  if (immediateMatch) {
    logPdfEvidenceDebug('Text-layer match resolved immediately', {
      pageNumber: immediateMatch.matchedPage,
      query,
      pageMatchIndex,
      rectCount: immediateMatch.rects.length,
      elapsedMs: Date.now() - startedAt,
      initialPageNumber: pageNumber,
    })
    return immediateMatch
  }

  const eventBus = options?.pdfApp?.eventBus
  if (!eventBus?.on || !eventBus?.off) {
    logPdfEvidenceDebug('PDF.js event bus unavailable while waiting for quote highlight rects', {
      pageNumber,
      query,
      pageMatchIndex,
      elapsedMs: Date.now() - startedAt,
    })
    return {
      rects: [],
      matchedPage: getSelectedEvidenceSpikePage(options?.pdfApp) ?? pageNumber,
    }
  }

  return new Promise((resolve) => {
    let settled = false
    let attemptTimeoutId: number | null = null

    const finish = (result: EvidenceTextLayerMatchResult) => {
      if (settled) {
        return
      }

      settled = true
      window.clearTimeout(timeoutId)
      if (attemptTimeoutId !== null) {
        window.clearTimeout(attemptTimeoutId)
      }
      eventBus.off('textlayerrendered', handleTextLayerRendered)
      eventBus.off('updatetextlayermatches', handleTextLayerMatchesUpdated)
      eventBus.off('pagerendered', handlePageRendered)
      resolve(result)
    }

    const scheduleAttempt = (
      eventName: 'listener-attached' | 'pagerendered' | 'textlayerrendered' | 'updatetextlayermatches',
      detail: Record<string, unknown> = {},
    ) => {
      if (settled || attemptTimeoutId !== null) {
        return
      }

      logPdfEvidenceDebug('Scheduling another text-layer quote localization attempt', {
        eventName,
        pageNumber,
        query,
        pageMatchIndex,
        elapsedMs: Date.now() - startedAt,
        ...detail,
      })

      // Let PDF.js finish any same-tick DOM updates before we inspect the text layer.
      attemptTimeoutId = window.setTimeout(() => {
        attemptTimeoutId = null
        const nextMatch = tryResolveTextLayerMatch(
          iframeDoc,
          pageNumber,
          query,
          pageMatchIndex,
          options,
        )
        if (!nextMatch) {
          return
        }

        logPdfEvidenceDebug('Event-driven text-layer match resolved', {
          eventName,
          pageNumber: nextMatch.matchedPage,
          query,
          pageMatchIndex,
          rectCount: nextMatch.rects.length,
          elapsedMs: Date.now() - startedAt,
          initialPageNumber: pageNumber,
          ...detail,
        })
        finish(nextMatch)
      }, 0)
    }

    const handleTextLayerRendered = (event: any) => {
      const eventPageNumber = typeof event?.pageNumber === 'number' ? event.pageNumber : null
      if (
        eventPageNumber !== null
        && !getEvidenceTextLayerCandidatePages(pageNumber, options?.pdfApp).includes(eventPageNumber)
      ) {
        return
      }

      scheduleAttempt('textlayerrendered', {
        eventPageNumber,
      })
    }

    const handleTextLayerMatchesUpdated = (event: any) => {
      if (event?.source && event.source !== options?.pdfApp?.findController) {
        return
      }

      const eventPageIndex = typeof event?.pageIndex === 'number' ? event.pageIndex : null
      const eventPageNumber = eventPageIndex !== null && eventPageIndex >= 0
        ? eventPageIndex + 1
        : null
      if (
        eventPageIndex !== -1
        && eventPageNumber !== null
        && !getEvidenceTextLayerCandidatePages(pageNumber, options?.pdfApp).includes(eventPageNumber)
      ) {
        return
      }

      scheduleAttempt('updatetextlayermatches', {
        eventPageIndex,
        eventPageNumber,
      })
    }

    const handlePageRendered = (event: any) => {
      const eventPageNumber = typeof event?.pageNumber === 'number' ? event.pageNumber : null
      if (
        eventPageNumber !== null
        && !getEvidenceTextLayerCandidatePages(pageNumber, options?.pdfApp).includes(eventPageNumber)
      ) {
        return
      }

      scheduleAttempt('pagerendered', {
        eventPageNumber,
      })
    }

    const timeoutId = window.setTimeout(() => {
      if (isPdfEvidenceDebugEnabled()) {
        const effectivePageNumber = getSelectedEvidenceSpikePage(options?.pdfApp) ?? pageNumber
        logPdfEvidenceDebug('Timed out waiting for quote highlight rects', {
          pageNumber,
          query,
          pageMatchIndex,
          timeoutMs,
          liveSelectedPage: getSelectedEvidenceSpikePage(options?.pdfApp),
          pageTextSnapshot: buildPageTextDebugSnapshot(
            iframeDoc,
            options?.pdfApp,
            effectivePageNumber,
          ),
          textLayer: buildTextLayerStructureDebugSnapshot(
            iframeDoc,
            effectivePageNumber,
            options?.pdfApp,
          ),
          findController: buildFindControllerDebugSnapshot(
            options?.pdfApp,
            effectivePageNumber,
          ),
        })
      } else {
        logPdfEvidenceDebug('Timed out waiting for quote highlight rects', {
          pageNumber,
          query,
          pageMatchIndex,
          timeoutMs,
          liveSelectedPage: getSelectedEvidenceSpikePage(options?.pdfApp),
        })
      }
      finish({
        rects: [],
        matchedPage: getSelectedEvidenceSpikePage(options?.pdfApp) ?? pageNumber,
      })
    }, timeoutMs)

    eventBus.on('textlayerrendered', handleTextLayerRendered)
    eventBus.on('updatetextlayermatches', handleTextLayerMatchesUpdated)
    eventBus.on('pagerendered', handlePageRendered)
    scheduleAttempt('listener-attached')
  })
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

  return candidateReason === 'exact-quote' ? 'exact_quote' : 'normalized_quote'
}

const buildQuoteMatchNavigationNote = (
  candidateReason: PdfEvidenceSpikeCandidateReason,
  options?: {
    nativeOnly?: boolean
    expandedAroundFragment?: boolean
    anchoredToPageText?: boolean
  },
): string => {
  const suffix = options?.nativeOnly
    ? 'using the viewer native text selection.'
    : 'on the PDF text layer.'

  if (options?.anchoredToPageText) {
    return `Recovered the best matching quote span from the PDF page text ${suffix}`
  }

  if (options?.expandedAroundFragment) {
    return `Recovered a longer contiguous quote around the matched fragment ${suffix}`
  }

  if (candidateReason === 'normalized-quote') {
    return `Highlighted a normalized quote match ${suffix}`
  }

  if (candidateReason === 'sanitized-quote') {
    return `Highlighted a formatting-normalized quote match ${suffix}`
  }

  if (candidateReason.includes('fragment')) {
    return `Highlighted a contiguous quote fragment ${suffix}`
  }

  return `Highlighted the requested quote ${suffix}`
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
    command.anchorId,
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

const getEvidenceHighlightRectStyles = (highlight: EvidenceTextLayerHighlight): Record<string, string> => {
  if (highlight.kind === 'section') {
    return highlight.mode === 'hover'
      ? {
          background: 'rgba(120, 144, 156, 0.12)',
          border: '1px dashed rgba(96, 125, 139, 0.55)',
          boxShadow: '0 0 0 1px rgba(255, 255, 255, 0.2)',
        }
      : {
          background: 'rgba(120, 144, 156, 0.18)',
          border: '2px solid rgba(96, 125, 139, 0.72)',
          boxShadow: '0 0 0 1px rgba(255, 255, 255, 0.25)',
        }
  }

  return highlight.mode === 'hover'
    ? {
        background: 'rgba(21, 101, 192, 0.16)',
        border: '1px dashed rgba(21, 101, 192, 0.7)',
        boxShadow: '0 0 0 1px rgba(21, 101, 192, 0.15)',
      }
    : {
        background: 'rgba(21, 101, 192, 0.28)',
        border: '2px solid rgba(21, 101, 192, 0.92)',
        boxShadow: '0 0 0 1px rgba(255, 255, 255, 0.22)',
      }
}

const getNavigationBannerSeverity = (
  result: PdfViewerNavigationResult,
): 'warning' | 'info' | 'error' => {
  if (result.locatorQuality === 'unresolved') {
    return 'error'
  }

  if (result.locatorQuality === 'document_only') {
    return 'info'
  }

  return 'warning'
}

const getNavigationBannerMessage = (
  result: PdfViewerNavigationResult,
  highlight: EvidenceTextLayerHighlight | null,
): string => {
  switch (result.locatorQuality) {
    case 'section_only':
      return highlight?.kind === 'section'
        ? 'Evidence context highlighted from the nearest section heading. The quote itself was not matched.'
        : 'Evidence likely appears on this page. Section context was found, but the quote itself was not matched.'
    case 'page_only':
      return 'Evidence on this page. Quote text was not matched reliably enough to highlight.'
    case 'document_only':
      return 'Evidence is document-scoped only. No precise page or text highlight is available.'
    case 'unresolved':
      return 'Evidence localization is unresolved. No trusted page or text highlight could be produced.'
    default:
      return result.note
  }
}

interface PdfEvidenceSpikeFindOutcome extends Pick<PdfEvidenceSpikeResult, 'matchedPage' | 'matchesTotal' | 'currentMatch'> {
  pageMatchIndex: number | null
  found: boolean
  matchState: number | null
}

const isSuccessfulEvidenceSpikeFindState = (state: number | null | undefined): boolean => {
  return state === PDFJS_FIND_STATE_FOUND || state === PDFJS_FIND_STATE_WRAPPED
}

const waitForEvidenceSpikeFindResult = (pdfApp: any, query: string): Promise<PdfEvidenceSpikeFindOutcome> => {
  const eventBus = pdfApp?.eventBus

  if (!eventBus?.on || !eventBus?.off) {
    logPdfEvidenceDebug('PDF.js event bus unavailable for evidence find', {
      query,
    })
    return Promise.resolve({
      matchedPage: getSelectedEvidenceSpikePage(pdfApp),
      matchesTotal: 0,
      currentMatch: 0,
      pageMatchIndex: getSelectedEvidenceSpikeMatchIndex(pdfApp),
      found: false,
      matchState: null,
    })
  }

  return new Promise((resolve) => {
    let latestCurrent = 0
    let latestTotal = 0
    let latestState: number | null = null
    let settleTimeoutId: number | null = null

    const finish = (detail?: { currentMatch?: number; matchesTotal?: number; matchedPage?: number | null; pageMatchIndex?: number | null; matchState?: number | null }) => {
      const matchState = detail?.matchState ?? latestState
      const resolvedCurrent = detail?.currentMatch ?? latestCurrent
      const resolvedTotal = detail?.matchesTotal ?? latestTotal
      const selectedMatchedPage = getSelectedEvidenceSpikeMatchedPage(pdfApp)
      const selectedMatchIndex = getSelectedEvidenceSpikeMatchIndex(pdfApp)
      const hasConcreteSelection = selectedMatchedPage !== null && selectedMatchIndex !== null
      const found = isSuccessfulEvidenceSpikeFindState(matchState)
        && (resolvedTotal > 0 || hasConcreteSelection)
      const finalOutcome = {
        matchedPage: found
          ? (detail?.matchedPage ?? selectedMatchedPage)
          : null,
        matchesTotal: resolvedTotal > 0
          ? resolvedTotal
          : (found && hasConcreteSelection ? 1 : 0),
        currentMatch: resolvedCurrent > 0
          ? resolvedCurrent
          : (found && hasConcreteSelection ? (selectedMatchIndex ?? 0) + 1 : 0),
        pageMatchIndex: found
          ? (detail?.pageMatchIndex ?? selectedMatchIndex)
          : null,
        found,
        matchState,
      }

      window.clearTimeout(timeoutId)
      if (settleTimeoutId !== null) {
        window.clearTimeout(settleTimeoutId)
      }
      eventBus.off('updatefindmatchescount', handleCount)
      eventBus.off('updatefindcontrolstate', handleState)
      logPdfEvidenceDebug('Settled PDF.js find request for evidence navigation', {
        query,
        outcome: finalOutcome,
        ...buildFindControllerDebugSnapshot(
          pdfApp,
          finalOutcome.matchedPage ?? getSelectedEvidenceSpikePage(pdfApp) ?? 1,
        ),
      })
      resolve(finalOutcome)
    }

    const handleCount = (event: any) => {
      if (event?.source !== pdfApp?.findController) {
        return
      }
      latestCurrent = event?.matchesCount?.current ?? latestCurrent
      latestTotal = event?.matchesCount?.total ?? latestTotal
      logPdfEvidenceDebug('PDF.js find count update', {
        query,
        currentMatch: latestCurrent,
        matchesTotal: latestTotal,
        selectedPage: getSelectedEvidenceSpikePage(pdfApp),
        selectedMatchIndex: getSelectedEvidenceSpikeMatchIndex(pdfApp),
      })

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
      logPdfEvidenceDebug('PDF.js find control update', {
        query,
        state: latestState,
        currentMatch: latestCurrent,
        matchesTotal: latestTotal,
        selectedPage: getSelectedEvidenceSpikePage(pdfApp),
        selectedMatchIndex: getSelectedEvidenceSpikeMatchIndex(pdfApp),
      })
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
            pageMatchIndex: getSelectedEvidenceSpikeMatchIndex(pdfApp),
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
          pageMatchIndex: getSelectedEvidenceSpikeMatchIndex(pdfApp),
          matchState: latestState,
        })
        return
      }

      finish({
        currentMatch: latestCurrent,
        matchesTotal: latestTotal,
        matchedPage: getSelectedEvidenceSpikePage(pdfApp),
        pageMatchIndex: getSelectedEvidenceSpikeMatchIndex(pdfApp),
        matchState: latestState,
      })
    }

    const timeoutId = window.setTimeout(() => {
      logPdfEvidenceDebug('Timed out while waiting for PDF.js find state to settle', {
        query,
        latestCurrent,
        latestTotal,
        latestState,
        selectedPage: getSelectedEvidenceSpikePage(pdfApp),
        selectedMatchIndex: getSelectedEvidenceSpikeMatchIndex(pdfApp),
      })
      finish({
        currentMatch: latestCurrent,
        matchesTotal: latestTotal,
        matchedPage: getSelectedEvidenceSpikePage(pdfApp),
        pageMatchIndex: getSelectedEvidenceSpikeMatchIndex(pdfApp),
      })
    }, PDFJS_FIND_TIMEOUT_MS)

    eventBus.on('updatefindmatchescount', handleCount)
    eventBus.on('updatefindcontrolstate', handleState)
  })
}

const dispatchEvidenceSpikeFind = async (pdfApp: any, candidate: PdfEvidenceSpikeCandidate) => {
  logPdfEvidenceDebug('Dispatching PDF.js find request', {
    query: candidate.query,
    reason: candidate.reason,
    queryLength: candidate.query.length,
    normalizedQueryLength: normalizeEvidenceSpikeText(candidate.query).length,
    selectedPageBeforeDispatch: getSelectedEvidenceSpikePage(pdfApp),
    selectedMatchIndexBeforeDispatch: getSelectedEvidenceSpikeMatchIndex(pdfApp),
  })
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

const ensureNativePdfJsQuoteHighlight = async (
  iframeDoc: Document,
  pdfApp: any,
  query: string,
  pageNumber: number,
  options?: {
    currentQuery?: string | null
    reason?: string
  },
): Promise<{
  preserved: boolean
  matchedPage: number | null
  matchesTotal: number
  currentMatch: number
  pageMatchIndex: number | null
}> => {
  const currentQuery = options?.currentQuery ?? null
  const trimmedCurrentQuery = (currentQuery ?? '').trim()
  const trimmedTargetQuery = query.trim()
  const selectedMatchedPage = getSelectedEvidenceSpikeMatchedPage(pdfApp)
  const selectedMatchIndex = getSelectedEvidenceSpikeMatchIndex(pdfApp)
  const selectedPageMatches = selectedMatchedPage !== null
    ? pdfApp?.findController?.pageMatches?.[selectedMatchedPage - 1]
    : null
  const hasVisibleNativeSelection = (
    selectedMatchedPage === pageNumber
    && selectedMatchIndex !== null
    && findPdfJsSelectedHighlightRects(iframeDoc, pageNumber).length > 0
  )

  if (trimmedCurrentQuery === trimmedTargetQuery && hasVisibleNativeSelection) {
    const pageMatchesByPage = Array.isArray(pdfApp?.findController?.pageMatches)
      ? pdfApp.findController.pageMatches
      : []
    const selectedPageMatchCount = Array.isArray(selectedPageMatches)
      ? selectedPageMatches.length
      : 0
    const globalMatchesTotal = pageMatchesByPage.reduce((sum: number, pageMatches: unknown) => (
      sum + (Array.isArray(pageMatches) ? pageMatches.length : 0)
    ), 0)
    const matchesBeforeSelectedPage = pageMatchesByPage
      .slice(0, Math.max(0, selectedMatchedPage - 1))
      .reduce((sum: number, pageMatches: unknown) => (
        sum + (Array.isArray(pageMatches) ? pageMatches.length : 0)
      ), 0)
    const effectiveCurrentMatch = selectedPageMatchCount > 0
      ? matchesBeforeSelectedPage + Math.min(selectedMatchIndex, selectedPageMatchCount - 1) + 1
      : selectedMatchIndex + 1
    const effectiveMatchesTotal = globalMatchesTotal > 0
      ? globalMatchesTotal
      : (selectedPageMatchCount > 0 ? selectedPageMatchCount : 1)

    logPdfEvidenceDebug('Reusing existing native PDF.js quote highlight', {
      query,
      pageNumber,
      reason: options?.reason ?? 'quote-match',
      pageMatchIndex: selectedMatchIndex,
      currentMatch: effectiveCurrentMatch,
      matchesTotal: effectiveMatchesTotal,
    })
    return {
      preserved: true,
      matchedPage: pageNumber,
      matchesTotal: effectiveMatchesTotal,
      currentMatch: effectiveCurrentMatch,
      pageMatchIndex: selectedMatchIndex,
    }
  }

  setEvidenceSpikePage(pdfApp, pageNumber)
  const nativeOutcome = await dispatchEvidenceSpikeFind(pdfApp, {
    query,
    reason: 'exact-quote',
  })
  const matchedPage = nativeOutcome.matchedPage ?? getSelectedEvidenceSpikeMatchedPage(pdfApp)
  if (!(nativeOutcome.found || nativeOutcome.matchesTotal > 0) || matchedPage !== pageNumber) {
    logPdfEvidenceDebug('Recovered quote span could not be synchronized back into native PDF.js highlight state', {
      query,
      pageNumber,
      matchedPage,
      reason: options?.reason ?? 'quote-match',
      outcome: nativeOutcome,
    })
    return {
      preserved: false,
      matchedPage,
      matchesTotal: nativeOutcome.matchesTotal,
      currentMatch: nativeOutcome.currentMatch,
      pageMatchIndex: nativeOutcome.pageMatchIndex,
    }
  }

  await waitForTextLayerMatch(
    iframeDoc,
    pageNumber,
    query,
    nativeOutcome.pageMatchIndex,
    PDF_TEXT_LAYER_RETRY_TIMEOUT_MS,
    { pdfApp },
  )

  const nativeRects = findPdfJsSelectedHighlightRects(iframeDoc, pageNumber)
  const preserved = nativeRects.length > 0
  logPdfEvidenceDebug(
    preserved
      ? 'Synchronized recovered quote span into native PDF.js highlight state'
      : 'Recovered quote span re-query settled but native PDF.js highlight is still not visibly rendered',
    {
      query,
      pageNumber,
      reason: options?.reason ?? 'quote-match',
      pageMatchIndex: nativeOutcome.pageMatchIndex,
      rectCount: nativeRects.length,
      outcome: nativeOutcome,
    },
  )

  return {
    preserved,
    matchedPage,
    matchesTotal: nativeOutcome.matchesTotal,
    currentMatch: nativeOutcome.currentMatch,
    pageMatchIndex: nativeOutcome.pageMatchIndex,
  }
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
  const [evidenceHighlight, setEvidenceHighlight] = useState<EvidenceTextLayerHighlight | null>(null)
  const [eventPendingNavigation, setEventPendingNavigation] =
    useState<EvidenceNavigationCommand | null>(null)
  const effectivePendingNavigation = pendingNavigation ?? eventPendingNavigation

  const commitNavigationResult = useCallback((result: PdfViewerNavigationResult | null) => {
    lastPdfEvidenceNavigationResult = result
    logPdfEvidenceDebug('Committed evidence navigation result to viewer state', {
      result,
    })
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
      renderOverlay?: boolean
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
    const renderOverlay = options?.renderOverlay ?? true
    const sectionTitle = normalizeEvidenceSpikeText(command.sectionTitle ?? anchor.section_title ?? '') || null
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
    const iframeDoc = iframeWindow?.document as Document | undefined
    const pdfApp = pdfAppRef.current ?? iframeWindow?.PDFViewerApplication ?? null

    if (pdfApp && pdfAppRef.current !== pdfApp) {
      pdfAppRef.current = pdfApp
    }

    if (!pdfApp?.eventBus || !pdfApp?.findController || !pdfApp?.pdfViewer) {
      setEvidenceHighlight(null)
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

    setEvidenceHighlight(null)
    clearPdfJsFindHighlights(pdfApp)

    const quoteCandidates = searchText
      ? buildEvidenceSpikeQuoteCandidates(quote || searchText, {
          searchText,
          normalizedText: anchor.normalized_text ?? null,
        })
      : []
    const preferredPage = pageHints[0] ?? null
    let quoteMatchedPageContext: {
      currentMatch: number
      matchedPage: number | null
      matchedQuery: string
      matchesTotal: number
    } | null = null

    logPdfEvidenceDebug('Starting evidence navigation', {
      anchorId: command.anchorId,
      mode: command.mode,
      quote,
      searchText,
      pageHints,
      preferredPage,
      sectionTitle,
      renderOverlay,
      quoteCandidates: quoteCandidates.map((candidate) => ({
        reason: candidate.reason,
        query: candidate.query,
      })),
    })

    for (const candidate of quoteCandidates) {
      if (preferredPage !== null) {
        setEvidenceSpikePage(pdfApp, preferredPage)
      }
      attemptedQueries.push(candidate.query)
      const outcome = await dispatchEvidenceSpikeFind(pdfApp, candidate)
      logPdfEvidenceDebug('Quote candidate find result', {
        anchorId: command.anchorId,
        query: candidate.query,
        reason: candidate.reason,
        found: outcome.found,
        matchState: outcome.matchState,
        matchesTotal: outcome.matchesTotal,
        currentMatch: outcome.currentMatch,
        matchedPage: outcome.matchedPage,
        pageMatchIndex: outcome.pageMatchIndex,
        selectedPageAfterFind: getSelectedEvidenceSpikePage(pdfApp),
      })
      if (outcome.found || outcome.matchesTotal > 0) {
        let matchedPage = outcome.matchedPage
          ?? getSelectedEvidenceSpikePage(pdfApp)
          ?? preferredPage
        const locatorQuality = resolveQuoteMatchLocatorQuality(anchor.locator_quality, candidate.reason)
        const textLayerMatchTimeoutMs = quoteMatchedPageContext === null
          ? PDF_TEXT_LAYER_MATCH_TIMEOUT_MS
          : PDF_TEXT_LAYER_RETRY_TIMEOUT_MS
        let textLayerMatch = iframeDoc && matchedPage !== null
          ? await waitForTextLayerMatch(
            iframeDoc,
            matchedPage,
            candidate.query,
            outcome.pageMatchIndex,
            textLayerMatchTimeoutMs,
            { pdfApp },
          )
          : null
        let textLayerRects = textLayerMatch?.rects ?? []
        matchedPage = textLayerMatch?.matchedPage ?? matchedPage
        let effectiveQuery = candidate.query
        let effectiveCurrentMatch = outcome.currentMatch
        let effectiveMatchesTotal = outcome.matchesTotal
        let effectivePageMatchIndex = outcome.pageMatchIndex
        let expandedAroundFragment = false
        let anchoredToPageText = false

        const liveMatchedPage = getSelectedEvidenceSpikePage(pdfApp)
        if (
          textLayerRects.length === 0
          && iframeDoc
          && liveMatchedPage !== null
          && liveMatchedPage !== matchedPage
        ) {
          logPdfEvidenceDebug('Retrying text-layer localization on late-selected page', {
            anchorId: command.anchorId,
            query: candidate.query,
            initialMatchedPage: matchedPage,
            liveMatchedPage,
          })
          matchedPage = liveMatchedPage
          textLayerMatch = await waitForTextLayerMatch(
            iframeDoc,
            matchedPage,
            candidate.query,
            outcome.pageMatchIndex,
            PDF_TEXT_LAYER_RETRY_TIMEOUT_MS,
            { pdfApp },
          )
          textLayerRects = textLayerMatch.rects
          matchedPage = textLayerMatch.matchedPage
        }

        const anchoredSpanCandidate = (
          iframeDoc
          && matchedPage !== null
          && (quote || searchText).trim().length > 0
        )
          ? findAnchoredEvidenceSpanForPage(
            iframeDoc,
            matchedPage,
            quote || searchText,
            {
              preferredAnchor: candidate.query,
              pageMatchIndex: outcome.pageMatchIndex,
              pdfApp,
            },
          )
          : null
        const anchoredQueryWordCount = anchoredSpanCandidate
          ? countNormalizedEvidenceWords(anchoredSpanCandidate.query)
          : 0
        const candidateQueryWordCount = countNormalizedEvidenceWords(candidate.query)

        if (
          anchoredSpanCandidate
          && (
            textLayerRects.length === 0
            || anchoredQueryWordCount > candidateQueryWordCount
            || (
              anchoredQueryWordCount >= candidateQueryWordCount
              && anchoredSpanCandidate.query.trim() !== candidate.query.trim()
            )
            || (
              anchoredQueryWordCount >= candidateQueryWordCount
              && normalizeEvidenceSpikeText(anchoredSpanCandidate.query) !== normalizeEvidenceSpikeText(candidate.query)
            )
          )
        ) {
        effectiveQuery = anchoredSpanCandidate.query
        textLayerRects = anchoredSpanCandidate.rects
        anchoredToPageText = true
        expandedAroundFragment = anchoredQueryWordCount > candidateQueryWordCount
        logPdfEvidenceDebug('Recovered a best-match quote span from the PDF page text', {
            anchorId: command.anchorId,
            candidateQuery: candidate.query,
            anchoredQuery: anchoredSpanCandidate.query,
          matchedPage,
          coverage: anchoredSpanCandidate.coverage,
          score: anchoredSpanCandidate.score,
          rectCount: anchoredSpanCandidate.rects.length,
          queryPreview: truncateDebugText(anchoredSpanCandidate.query, 220),
          rawRange: {
            rawStart: anchoredSpanCandidate.rawStart,
            rawEndExclusive: anchoredSpanCandidate.rawEndExclusive,
          },
        })
      }

        const fragmentExpansionCandidate = (
          textLayerRects.length > 0
          && !anchoredToPageText
          && iframeDoc
          && matchedPage !== null
          && countNormalizedEvidenceWords(candidate.query) < countNormalizedEvidenceWords(quote || searchText)
        )
          ? findExpandedEvidenceQueryForPage(
            iframeDoc,
            pdfApp,
            matchedPage,
            quote || searchText,
            candidate.query,
          )
          : null

        if (
          fragmentExpansionCandidate
          && fragmentExpansionCandidate.query !== candidate.query
          && fragmentExpansionCandidate.wordCount > countNormalizedEvidenceWords(candidate.query)
        ) {
          logPdfEvidenceDebug('Attempting to recover a longer quote span around the matched fragment', {
            anchorId: command.anchorId,
            candidateQuery: candidate.query,
            expandedQuery: fragmentExpansionCandidate.query,
            matchedPage,
            candidateReason: candidate.reason,
          })

          attemptedQueries.push(fragmentExpansionCandidate.query)
          setEvidenceSpikePage(pdfApp, matchedPage)
          const expandedOutcome = await dispatchEvidenceSpikeFind(pdfApp, {
            query: fragmentExpansionCandidate.query,
            reason: candidate.reason,
          })
          logPdfEvidenceDebug('Expanded quote candidate find result', {
            anchorId: command.anchorId,
            query: fragmentExpansionCandidate.query,
            candidateQuery: candidate.query,
            reason: candidate.reason,
            found: expandedOutcome.found,
            matchState: expandedOutcome.matchState,
            matchesTotal: expandedOutcome.matchesTotal,
            currentMatch: expandedOutcome.currentMatch,
            matchedPage: expandedOutcome.matchedPage,
            pageMatchIndex: expandedOutcome.pageMatchIndex,
          })

          if (expandedOutcome.found || expandedOutcome.matchesTotal > 0) {
            const expandedMatchedPage = (
              expandedOutcome.matchedPage
              ?? getSelectedEvidenceSpikePage(pdfApp)
              ?? matchedPage
            )
            const expandedTextLayerMatch = await waitForTextLayerMatch(
              iframeDoc,
              expandedMatchedPage,
              fragmentExpansionCandidate.query,
              expandedOutcome.pageMatchIndex,
              PDF_TEXT_LAYER_RETRY_TIMEOUT_MS,
              { pdfApp },
            )

            if (expandedTextLayerMatch.rects.length > 0) {
              effectiveQuery = fragmentExpansionCandidate.query
              effectiveCurrentMatch = expandedOutcome.currentMatch
              effectiveMatchesTotal = expandedOutcome.matchesTotal
              effectivePageMatchIndex = expandedOutcome.pageMatchIndex
              matchedPage = expandedTextLayerMatch.matchedPage
              textLayerRects = expandedTextLayerMatch.rects
              expandedAroundFragment = true
              logPdfEvidenceDebug('Recovered a longer quote span around the matched fragment', {
                anchorId: command.anchorId,
                matchedPage,
                candidateQuery: candidate.query,
                expandedQuery: effectiveQuery,
                rectCount: textLayerRects.length,
              })
            }
          }
        }

        if (textLayerRects.length === 0) {
          // PDF.js can resolve the quote to the right page before the text
          // layer is ready enough for stable overlay rects. Keep trying other
          // quote-localization strategies, but do not later degrade this into
          // a section jump that overrides the already-correct page context.
          const latestMatchedPage = getSelectedEvidenceSpikePage(pdfApp) ?? matchedPage
          if (quoteMatchedPageContext === null) {
            quoteMatchedPageContext = {
              currentMatch: outcome.currentMatch,
              matchedPage: latestMatchedPage,
              matchedQuery: effectiveQuery,
              matchesTotal: outcome.matchesTotal,
            }
          }
          logPdfEvidenceDebug('Quote resolved to a page but no stable highlight rects were derived yet', {
            anchorId: command.anchorId,
            query: candidate.query,
            reason: candidate.reason,
            matchedPage: latestMatchedPage,
            currentMatch: outcome.currentMatch,
            matchesTotal: outcome.matchesTotal,
          })
          clearPdfJsFindHighlights(pdfApp)
          continue
        }

        let preservedNativeHighlight = false
        if (!renderOverlay && iframeDoc) {
          const nativeHighlight = await ensureNativePdfJsQuoteHighlight(
            iframeDoc,
            pdfApp,
            effectiveQuery,
            matchedPage,
            {
              currentQuery: candidate.query,
              reason: 'quote-match',
            },
          )
          preservedNativeHighlight = nativeHighlight.preserved
          if (nativeHighlight.preserved) {
            effectiveCurrentMatch = nativeHighlight.currentMatch
            effectiveMatchesTotal = nativeHighlight.matchesTotal
            effectivePageMatchIndex = nativeHighlight.pageMatchIndex
          }
        }
        const shouldRenderOverlay = renderOverlay || !preservedNativeHighlight

        setEvidenceHighlight({
          anchorId: command.anchorId,
          kind: 'quote',
          mode: command.mode,
          pageNumber: matchedPage,
          query: effectiveQuery,
          pageMatchIndex: effectivePageMatchIndex,
          rects: textLayerRects,
          renderOverlay: shouldRenderOverlay,
        })
        maybeClearPdfJsFindHighlights(pdfApp, {
          preserveNativeHighlight: preservedNativeHighlight,
          reason: 'quote-match',
        })
        logPdfEvidenceDebug('Evidence navigation matched successfully', {
          anchorId: command.anchorId,
          query: effectiveQuery,
          reason: candidate.reason,
          matchedPage,
          rectCount: textLayerRects.length,
          locatorQuality,
          renderOverlay: shouldRenderOverlay,
          preservedNativeHighlight,
          anchoredToPageText,
          expandedAroundFragment,
        })
        return {
          ...baseResult,
          status: 'matched',
          strategy: candidate.reason,
          locatorQuality,
          degraded: isDegradedLocatorQuality(locatorQuality),
          matchedQuery: effectiveQuery,
          matchedPage,
          matchesTotal: effectiveMatchesTotal,
          currentMatch: effectiveCurrentMatch,
          note: buildQuoteMatchNavigationNote(candidate.reason, {
            nativeOnly: preservedNativeHighlight,
            anchoredToPageText,
            expandedAroundFragment,
          }),
        }
      }
    }

    const documentAnchoredQuote = iframeDoc && (quote || searchText).trim().length > 0
      ? findAnchoredEvidenceSpanAcrossDocument(
        iframeDoc,
        pdfApp,
        quote || searchText,
        {
          pageCountHint: activeDocument?.pageCount ?? null,
          preferredPage,
        },
      )
      : null

    if (documentAnchoredQuote && iframeDoc) {
      logPdfEvidenceDebug('Attempting document-wide anchored quote recovery after page-local matching failed', {
        anchorId: command.anchorId,
        preferredPage,
        matchedPage: documentAnchoredQuote.pageNumber,
        query: documentAnchoredQuote.query,
        coverage: documentAnchoredQuote.coverage,
        score: documentAnchoredQuote.score,
      })

      setEvidenceSpikePage(pdfApp, documentAnchoredQuote.pageNumber)
      const documentTextLayerMatch = await waitForTextLayerMatch(
        iframeDoc,
        documentAnchoredQuote.pageNumber,
        documentAnchoredQuote.query,
        null,
        PDF_TEXT_LAYER_MATCH_TIMEOUT_MS,
        { pdfApp },
      )
      const documentAnchoredSpan = documentTextLayerMatch.rects.length > 0
        ? null
        : findAnchoredEvidenceSpanForPage(
          iframeDoc,
          documentAnchoredQuote.pageNumber,
          quote || searchText,
          {
            preferredAnchor: documentAnchoredQuote.query,
            pdfApp,
          },
        )
      const documentRects = documentTextLayerMatch.rects.length > 0
        ? documentTextLayerMatch.rects
        : (documentAnchoredSpan?.rects ?? [])
      const effectiveDocumentQuery = documentAnchoredSpan?.query ?? documentAnchoredQuote.query
      const matchedDocumentPage = documentTextLayerMatch.matchedPage ?? documentAnchoredQuote.pageNumber

      if (documentRects.length > 0) {
        const locatorQuality = resolveQuoteMatchLocatorQuality(anchor.locator_quality, 'exact-quote')
        let preservedNativeHighlight = false
        let effectiveCurrentMatch = 1
        let effectiveMatchesTotal = 1
        let effectivePageMatchIndex: number | null = null
        if (!renderOverlay) {
          const nativeHighlight = await ensureNativePdfJsQuoteHighlight(
            iframeDoc,
            pdfApp,
            effectiveDocumentQuery,
            matchedDocumentPage,
            {
              currentQuery: documentAnchoredQuote.query,
              reason: 'document-anchored-quote-match',
            },
          )
          preservedNativeHighlight = nativeHighlight.preserved
          if (nativeHighlight.preserved) {
            effectiveCurrentMatch = nativeHighlight.currentMatch
            effectiveMatchesTotal = nativeHighlight.matchesTotal
            effectivePageMatchIndex = nativeHighlight.pageMatchIndex
          }
        }
        const shouldRenderOverlay = renderOverlay || !preservedNativeHighlight
        setEvidenceHighlight({
          anchorId: command.anchorId,
          kind: 'quote',
          mode: command.mode,
          pageNumber: matchedDocumentPage,
          query: effectiveDocumentQuery,
          pageMatchIndex: effectivePageMatchIndex,
          rects: documentRects,
          renderOverlay: shouldRenderOverlay,
        })
        maybeClearPdfJsFindHighlights(pdfApp, {
          preserveNativeHighlight: preservedNativeHighlight,
          reason: 'document-anchored-quote-match',
        })
        logPdfEvidenceDebug('Document-wide anchored quote recovery matched successfully', {
          anchorId: command.anchorId,
          preferredPage,
          matchedPage: matchedDocumentPage,
          query: effectiveDocumentQuery,
          rectCount: documentRects.length,
          coverage: documentAnchoredSpan?.coverage ?? documentAnchoredQuote.coverage,
          score: documentAnchoredSpan?.score ?? documentAnchoredQuote.score,
          locatorQuality,
          preservedNativeHighlight,
        })
        return {
          ...baseResult,
          status: 'matched',
          strategy: 'document',
          locatorQuality,
          degraded: isDegradedLocatorQuality(locatorQuality),
          matchedQuery: effectiveDocumentQuery,
          matchedPage: matchedDocumentPage,
          matchesTotal: effectiveMatchesTotal,
          currentMatch: effectiveCurrentMatch,
          note: buildQuoteMatchNavigationNote('exact-quote', {
            nativeOnly: preservedNativeHighlight,
            anchoredToPageText: true,
          }),
        }
      }

      logPdfEvidenceDebug('Document-wide anchored quote recovery found the right page text but could not derive stable highlight rects', {
        anchorId: command.anchorId,
        preferredPage,
        matchedPage: documentAnchoredQuote.pageNumber,
        query: documentAnchoredQuote.query,
      })
    }

    const sectionCandidates = buildEvidenceSpikeSectionCandidates(sectionTitle, anchor.subsection_title)
    const quoteContextPage = quoteMatchedPageContext?.matchedPage
      ?? getSelectedEvidenceSpikePage(pdfApp)
      ?? preferredPage
    const sectionPreferredPage = quoteContextPage ?? preferredPage
    for (const candidate of sectionCandidates) {
      if (sectionPreferredPage !== null) {
        setEvidenceSpikePage(pdfApp, sectionPreferredPage)
      }
      attemptedQueries.push(candidate.query)
      const outcome = await dispatchEvidenceSpikeFind(pdfApp, candidate)
      logPdfEvidenceDebug('Section candidate find result', {
        anchorId: command.anchorId,
        query: candidate.query,
        reason: candidate.reason,
        found: outcome.found,
        matchState: outcome.matchState,
        matchesTotal: outcome.matchesTotal,
        currentMatch: outcome.currentMatch,
        matchedPage: outcome.matchedPage,
        pageMatchIndex: outcome.pageMatchIndex,
      })
      if (outcome.found || outcome.matchesTotal > 0) {
        const matchedPage = outcome.matchedPage ?? preferredPage
        const textLayerMatch = iframeDoc && matchedPage !== null
          ? await waitForTextLayerMatch(
            iframeDoc,
            matchedPage,
            candidate.query,
            outcome.pageMatchIndex,
            PDF_TEXT_LAYER_MATCH_TIMEOUT_MS,
            { pdfApp },
          )
          : null
        const textLayerRects = textLayerMatch?.rects ?? []
        const localizedPage = textLayerMatch?.matchedPage ?? matchedPage

        if (textLayerRects.length > 0 && localizedPage !== null) {
          setEvidenceHighlight({
            anchorId: command.anchorId,
            kind: 'section',
            mode: command.mode,
            pageNumber: localizedPage,
            query: candidate.query,
            pageMatchIndex: outcome.pageMatchIndex,
            rects: textLayerRects,
            renderOverlay,
          })
        } else {
          setEvidenceHighlight(null)
        }

        maybeClearPdfJsFindHighlights(pdfApp, {
          preserveNativeHighlight: !renderOverlay,
          reason: 'section-match',
        })
        logPdfEvidenceDebug('Falling back to section context', {
          anchorId: command.anchorId,
          query: candidate.query,
          matchedPage: localizedPage,
          rectCount: textLayerRects.length,
        })
        return {
          ...baseResult,
          status: 'section-fallback',
          strategy: candidate.reason,
          locatorQuality: 'section_only',
          degraded: true,
          matchedQuery: candidate.query,
          matchedPage: localizedPage,
          matchesTotal: outcome.matchesTotal,
          currentMatch: outcome.currentMatch,
          note: textLayerRects.length > 0
            ? 'The quote itself did not match, but a section heading on the hinted page was highlighted as degraded context.'
            : 'The quote itself did not match, but section metadata navigated to a relevant page in the PDF viewer.',
        }
      }
    }

    if (quoteMatchedPageContext && quoteContextPage !== null) {
      setEvidenceSpikePage(pdfApp, quoteContextPage)
      clearPdfJsFindHighlights(pdfApp)
      setEvidenceHighlight(null)
      logPdfEvidenceDebug('Falling back to matched page without stable highlight rects after section localization failed', {
        anchorId: command.anchorId,
        quoteContextPage,
        quoteMatchedPageContext,
      })
      return {
        ...baseResult,
        status: 'page-fallback',
        strategy: 'page-hint',
        locatorQuality: 'page_only',
        degraded: true,
        matchedQuery: quoteMatchedPageContext.matchedQuery,
        matchedPage: quoteContextPage,
        matchesTotal: quoteMatchedPageContext.matchesTotal,
        currentMatch: quoteMatchedPageContext.currentMatch,
        note: 'Quote search resolved this evidence to the correct page, but the viewer could not derive a stable text highlight or a section-level fallback. Staying on the matched page instead.',
      }
    }

    if (anchor.locator_quality === 'document_only') {
      clearPdfJsFindHighlights(pdfApp)
      setEvidenceHighlight(null)
      logPdfEvidenceDebug('Falling back to document-only context by anchor design', {
        anchorId: command.anchorId,
      })
      return {
        ...baseResult,
        status: 'document-fallback',
        strategy: 'document',
        locatorQuality: 'document_only',
        degraded: true,
        matchedQuery: null,
        matchedPage: null,
        matchesTotal: 0,
        currentMatch: 0,
        note: 'Opened the document without a precise page or text target because this anchor is intentionally document-scoped.',
      }
    }

    if (preferredPage !== null) {
      setEvidenceSpikePage(pdfApp, preferredPage)
      clearPdfJsFindHighlights(pdfApp)
      setEvidenceHighlight(null)
      logPdfEvidenceDebug('Falling back to page hint only', {
        anchorId: command.anchorId,
        pageNumber: preferredPage,
        attemptedQueries,
      })
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

    clearPdfJsFindHighlights(pdfApp)
    setEvidenceHighlight(null)
    logPdfEvidenceDebug('Evidence navigation failed to localize anchor', {
      anchorId: command.anchorId,
      attemptedQueries,
    })

    return {
      ...baseResult,
      status: 'not-found',
      strategy: 'document',
      locatorQuality: 'unresolved',
      degraded: true,
      matchedQuery: null,
      matchedPage: null,
      matchesTotal: 0,
      currentMatch: 0,
      note: 'The PDF viewer could not localize this evidence to quote, section, or page-level text in the current document.',
    }
  }, [activeDocument?.documentId, activeDocument?.pageCount])

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
        const resolvedPageCount =
          pdfApp?.pdfDocument?.numPages ?? pdfApp?.pdfViewer?.pdfDocument?.numPages ?? null
        if (typeof resolvedPageCount === 'number' && resolvedPageCount > 0) {
          setActiveDocument((current) =>
            current && current.pageCount !== resolvedPageCount
              ? { ...current, pageCount: resolvedPageCount }
              : current,
          )
        }
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
    setEvidenceHighlight(null)
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

      viewerStateRef.current = {
        ...DEFAULT_STATE,
        currentPage: event.detail.viewerState?.currentPage ?? DEFAULT_STATE.currentPage,
        scrollPosition: event.detail.viewerState?.scrollPosition ?? DEFAULT_STATE.scrollPosition,
        lastInteraction: new Date().toISOString(),
      }
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
        setEvidenceHighlight(null)
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
      setEvidenceHighlight(null)
      commitNavigationResult(null)
    }
  }, [activeDocument?.documentId, commitNavigationResult])

  useEffect(() => {
    if (status === 'ready' && highlightTermsRef.current.length) {
      applyHighlights()
    }
  }, [status, applyHighlights])

  useEffect(() => {
    if (!effectivePendingNavigation) {
      handledNavigationKeyRef.current = null
      return
    }

    if (!activeDocument || status !== 'ready') {
      return
    }

    const navigationKey = buildNavigationCommandKey(effectivePendingNavigation)
    if (handledNavigationKeyRef.current === navigationKey) {
      return
    }

    handledNavigationKeyRef.current = navigationKey
    const usesEventNavigation =
      pendingNavigation === null && eventPendingNavigation === effectivePendingNavigation
    let cancelled = false

    void executeEvidenceNavigation(effectivePendingNavigation, {
      renderOverlay: !usesEventNavigation,
    })
      .then((result) => {
        if (cancelled) {
          return
        }
        commitNavigationResult(result)
        if (usesEventNavigation) {
          setEventPendingNavigation(null)
        }
        onNavigationComplete?.()
      })
      .catch((error) => {
        console.warn('Failed to execute typed PDF evidence navigation', error)
        if (cancelled) {
          return
        }
        setEvidenceHighlight(null)
        commitNavigationResult({
          status: 'not-found',
          strategy: 'document',
          locatorQuality: 'unresolved',
          degraded: true,
          mode: effectivePendingNavigation.mode,
          documentId: activeDocument.documentId,
          quote: effectivePendingNavigation.anchor.snippet_text?.trim()
            ?? effectivePendingNavigation.anchor.sentence_text?.trim()
            ?? effectivePendingNavigation.searchText?.trim()
            ?? '',
          pageHints: normalizeEvidenceSpikePageHints({
            pageNumber:
              effectivePendingNavigation.pageNumber
              ?? effectivePendingNavigation.anchor.page_number
              ?? null,
          }),
          sectionTitle: normalizeEvidenceSpikeText(
            effectivePendingNavigation.sectionTitle
            ?? effectivePendingNavigation.anchor.section_title
            ?? '',
          ) || null,
          matchedQuery: null,
          matchedPage: null,
          matchesTotal: 0,
          currentMatch: 0,
          attemptedQueries: [],
          note: 'Typed evidence navigation failed unexpectedly before the viewer could localize the requested anchor.',
        })
        if (usesEventNavigation) {
          setEventPendingNavigation(null)
        }
        onNavigationComplete?.()
      })

    return () => {
      cancelled = true
    }
  }, [
    activeDocument,
    commitNavigationResult,
    effectivePendingNavigation,
    eventPendingNavigation,
    executeEvidenceNavigation,
    onNavigationComplete,
    pendingNavigation,
    status,
  ])

  useEffect(() => {
    return onPDFViewerNavigateEvidence((event) => {
      const command = event.detail?.command
      if (!command) {
        return
      }

      logPdfEvidenceDebug('Received typed evidence navigation event', {
        anchorId: command.anchorId,
        mode: command.mode,
        pageNumber: command.pageNumber,
        sectionTitle: command.sectionTitle,
        searchText: command.searchText,
      })
      setEventPendingNavigation(command)
    })
  }, [])

  useEffect(() => {
    const setEnabled = (enabled: boolean) => {
      const nextEnabled = setPdfEvidenceDebugEnabled(enabled)
      window.__pdfViewerEvidenceDebug = {
        enabled: nextEnabled,
        storageKey: PDF_EVIDENCE_DEBUG_STORAGE_KEY,
        setEnabled,
        getEntries: () => [...pdfEvidenceDebugEntries],
        clearEntries: () => {
          pdfEvidenceDebugEntries.splice(0, pdfEvidenceDebugEntries.length)
        },
        getLastResult: () => lastPdfEvidenceNavigationResult,
      }
      console.info('[PDF EVIDENCE DEBUG] Browser evidence tracing', nextEnabled ? 'enabled' : 'disabled')
      return nextEnabled
    }

    window.__pdfViewerEvidenceDebug = {
      enabled: isPdfEvidenceDebugEnabled(),
      storageKey: PDF_EVIDENCE_DEBUG_STORAGE_KEY,
      setEnabled,
      getEntries: () => [...pdfEvidenceDebugEntries],
      clearEntries: () => {
        pdfEvidenceDebugEntries.splice(0, pdfEvidenceDebugEntries.length)
      },
      getLastResult: () => lastPdfEvidenceNavigationResult,
    }

    return () => {
      delete window.__pdfViewerEvidenceDebug
    }
  }, [])

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
    const iframeDoc = iframeRef.current?.contentWindow?.document
    const existingLayers = iframeDoc?.querySelectorAll('.pdf-evidence-highlight-layer')
    existingLayers?.forEach((node) => node.remove())

    if (
      !iframeDoc
      || !activeDocument
      || status !== 'ready'
      || !evidenceHighlight
      || !evidenceHighlight.renderOverlay
    ) {
      return
    }

    const pageContainer = getPageContainer(iframeDoc, evidenceHighlight.pageNumber)
    const rects = evidenceHighlight.rects && evidenceHighlight.rects.length > 0
      ? evidenceHighlight.rects
      : findTextLayerMatchRects(
        iframeDoc,
        evidenceHighlight.pageNumber,
        evidenceHighlight.query,
        evidenceHighlight.pageMatchIndex,
      )
    if (!pageContainer || rects.length === 0) {
      return
    }

    const highlightLayer = iframeDoc.createElement('div')
    highlightLayer.className = 'pdf-evidence-highlight-layer'
    highlightLayer.dataset.anchorId = evidenceHighlight.anchorId
    highlightLayer.dataset.mode = evidenceHighlight.mode
    highlightLayer.dataset.kind = evidenceHighlight.kind
    highlightLayer.style.position = 'absolute'
    highlightLayer.style.inset = '0'
    highlightLayer.style.pointerEvents = 'none'
    highlightLayer.style.zIndex = '6'

    const rectStyles = getEvidenceHighlightRectStyles(evidenceHighlight)
    const rectCleanupFns: Array<() => void> = []
    const handleAnchorSelection = () => {
      dispatchPDFViewerEvidenceAnchorSelected(evidenceHighlight.anchorId)
    }

    rects.forEach((rect) => {
      const rectNode = iframeDoc.createElement('div')
      rectNode.className = 'pdf-evidence-highlight-rect'
      rectNode.dataset.anchorId = evidenceHighlight.anchorId
      rectNode.dataset.mode = evidenceHighlight.mode
      rectNode.dataset.kind = evidenceHighlight.kind
      rectNode.style.position = 'absolute'
      rectNode.style.left = `${rect.left}px`
      rectNode.style.top = `${rect.top}px`
      rectNode.style.width = `${rect.width}px`
      rectNode.style.height = `${rect.height}px`
      rectNode.style.borderRadius = '2px'
      rectNode.style.cursor = 'pointer'
      rectNode.style.pointerEvents = 'auto'
      Object.assign(rectNode.style, rectStyles)

      rectNode.setAttribute('aria-label', 'Jump to linked annotation field')
      rectNode.setAttribute('role', 'button')
      rectNode.tabIndex = 0

      const handleKeyDown = (event: KeyboardEvent) => {
        if (event.key !== 'Enter' && event.key !== ' ') {
          return
        }

        event.preventDefault()
        handleAnchorSelection()
      }

      rectNode.addEventListener('click', handleAnchorSelection)
      rectNode.addEventListener('keydown', handleKeyDown)
      rectCleanupFns.push(() => {
        rectNode.removeEventListener('click', handleAnchorSelection)
        rectNode.removeEventListener('keydown', handleKeyDown)
      })
      highlightLayer.appendChild(rectNode)
    })

    pageContainer.appendChild(highlightLayer)

    return () => {
      rectCleanupFns.forEach((cleanup) => cleanup())
      highlightLayer.remove()
    }
  }, [activeDocument?.documentId, evidenceHighlight, overlayRenderKey, status])

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

  const navigationBannerMessage = navigationResult
    ? getNavigationBannerMessage(navigationResult, evidenceHighlight)
    : null

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
                  {navigationBannerMessage}
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
        {activeDocument && navigationResult?.degraded && navigationBannerMessage && (
          <Box
            sx={{
              position: 'absolute',
              top: 12,
              left: 12,
              right: 12,
              zIndex: 3,
            }}
          >
            <Alert severity={getNavigationBannerSeverity(navigationResult)} variant="filled">
              {navigationBannerMessage}
            </Alert>
          </Box>
        )}

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
