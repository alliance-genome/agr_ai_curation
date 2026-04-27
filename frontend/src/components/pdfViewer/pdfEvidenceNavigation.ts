import { alpha, type Theme } from '@mui/material/styles'
import {
  buildNormalizedTextSourceMap,
  normalizeTextForEvidenceMatch,
  sanitizeEvidenceSearchText,
  splitNormalizedWords,
} from '@/components/pdfViewer/textNormalization'
import type { EvidenceAnchor, EvidenceLocatorQuality } from '@/features/curation/contracts'
import type { EvidenceNavigationCommand } from '@/features/curation/evidence'
import type {
  PdfEvidenceFuzzyMatchPage,
  PdfEvidenceFuzzyMatchResult,
  PdfEvidenceFuzzyMatchStrategy,
} from '@/features/curation/services/pdfEvidenceMatcherService'
import {
  isPdfEvidenceDebugEnabled,
  logPdfEvidenceDebug,
  truncateDebugText,
} from './pdfViewerDebug'
import { uniqueTerms } from './pdfViewerHighlighting'

const PDFJS_FIND_STATE_FOUND = 0
const PDFJS_FIND_STATE_NOT_FOUND = 1
const PDFJS_FIND_STATE_WRAPPED = 2
const PDFJS_FIND_STATE_PENDING = 3
const PDFJS_FIND_TIMEOUT_MS = 3500
const PDFJS_FIND_RESULT_SETTLE_MS = 75
export const PDF_TEXT_LAYER_MATCH_TIMEOUT_MS = 2000
export const EVIDENCE_SPIKE_EVENT_NAME = 'pdf-viewer-evidence-spike'
const EVIDENCE_SPIKE_RESULT_EVENT_NAME = 'pdf-viewer-evidence-spike-result'
export const PDF_EVIDENCE_FUZZY_MATCH_MIN_SCORE = 70
const EVIDENCE_SPIKE_WINDOW_FRAGMENT_MIN_WORDS = 8
const EVIDENCE_SPIKE_WINDOW_FRAGMENT_TARGET_WORDS = 12
const EVIDENCE_SPIKE_WINDOW_FRAGMENT_MAX_COUNT = 6
const PDF_NATIVE_SELECTION_TIMEOUT_MS = 1200

export type PdfEvidenceSpikeCandidateReason =
  | 'sanitized-quote'
  | 'exact-quote'
  | 'normalized-quote'
  | 'window-fragment'
  | 'section-title'
  | 'subsection-title'
  | 'warm-search-corpus'

export type PdfEvidenceSpikeStatus =
  | 'matched'
  | 'section-fallback'
  | 'page-fallback'
  | 'document-fallback'
  | 'not-found'
  | 'viewer-not-ready'

export type PdfEvidenceSpikeStrategy =
  | PdfEvidenceSpikeCandidateReason
  | PdfEvidenceFuzzyMatchStrategy
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

export interface EvidenceTextLayerHighlight {
  anchorId: string
  kind: 'quote' | 'section'
  mode: EvidenceNavigationCommand['mode']
  pageNumber: number
  query: string
  pageMatchIndex: number | null
  rects: EvidenceTextLayerRect[] | null
  renderOverlay: boolean
  nativeTarget?: NativePdfJsQuoteTarget | null
}

export interface PdfEvidencePageTextCorpusCache {
  cacheKey: string | null
  pages: PdfEvidenceFuzzyMatchPage[] | null
  promise: Promise<PdfEvidenceFuzzyMatchPage[]> | null
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

const capEvidenceSpikeWindowFragments = (fragments: string[]): string[] => {
  const uniqueFragments = uniqueTerms(fragments)
  if (uniqueFragments.length <= EVIDENCE_SPIKE_WINDOW_FRAGMENT_MAX_COUNT) {
    return uniqueFragments
  }

  const capped: string[] = []
  for (let index = 0; index < EVIDENCE_SPIKE_WINDOW_FRAGMENT_MAX_COUNT; index += 1) {
    const sourceIndex = Math.round(
      (index * (uniqueFragments.length - 1)) / (EVIDENCE_SPIKE_WINDOW_FRAGMENT_MAX_COUNT - 1),
    )
    capped.push(uniqueFragments[sourceIndex] ?? uniqueFragments[uniqueFragments.length - 1]!)
  }

  return uniqueTerms(capped)
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

  return capEvidenceSpikeWindowFragments(fragments)
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

  const words = splitNormalizedWords(fragmentSource)

  const candidates: PdfEvidenceSpikeCandidate[] = []

  candidates.push({ query: exactQuote, reason: 'exact-quote' })

  if (sanitizedQuote && sanitizedQuote !== exactQuote) {
    candidates.push({ query: sanitizedQuote, reason: 'sanitized-quote' })
  }

  if (normalizedCandidate) {
    candidates.push({ query: normalizedCandidate, reason: 'normalized-quote' })
  }

  buildEvidenceSpikeWindowFragments(words).forEach((fragment) => {
    candidates.push({ query: fragment, reason: 'window-fragment' })
  })

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

export const publishEvidenceSpikeResult = (result: PdfEvidenceSpikeResult) => {
  window.__pdfViewerEvidenceSpikeLastResult = result
  window.dispatchEvent(
    new CustomEvent<PdfEvidenceSpikeResult>(EVIDENCE_SPIKE_RESULT_EVENT_NAME, {
      detail: result,
    }),
  )
}

export const getSelectedEvidenceSpikePage = (pdfApp: any): number | null => {
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

export const joinPdfJsTextContentItems = (
  textContent: {
    items?: Array<{
      str?: string
      hasEOL?: boolean
    }>
  } | null | undefined,
): string => {
  if (!Array.isArray(textContent?.items)) {
    return ''
  }

  const parts: string[] = []
  textContent.items.forEach((item) => {
    parts.push(item?.str ?? '')
    if (item?.hasEOL) {
      parts.push('\n')
    }
  })
  return parts.join('')
}

export const createPdfJsQuoteSearchAdapter = (
  pdfApp: any,
  options?: {
    getPageText?: (pageNumber: number) => string | null
  },
): PdfJsQuoteSearchAdapter => {
  const getPageContents = (pageNumber: number): string | null => {
    const pageContents = pdfApp?.findController?._pageContents?.[pageNumber - 1]
    if (typeof pageContents === 'string') {
      return pageContents
    }

    const externalPageText = options?.getPageText?.(pageNumber)
    return typeof externalPageText === 'string' ? externalPageText : null
  }

  const getPageOccurrences = (pageNumber: number): PdfJsQuoteMatchOccurrence[] => {
    const pageContents = getPageContents(pageNumber)
    const pageMatches = pdfApp?.findController?.pageMatches?.[pageNumber - 1]
    const pageMatchesLength = pdfApp?.findController?.pageMatchesLength?.[pageNumber - 1]

    if (!pageContents || !Array.isArray(pageMatches) || !Array.isArray(pageMatchesLength)) {
      return []
    }

    return pageMatches.reduce<PdfJsQuoteMatchOccurrence[]>((acc, rawStart, pageMatchIndex) => {
      const rawLength = pageMatchesLength[pageMatchIndex]
      if (
        typeof rawStart !== 'number'
        || rawStart < 0
        || typeof rawLength !== 'number'
        || rawLength <= 0
      ) {
        return acc
      }

      const rawEndExclusive = rawStart + rawLength
      acc.push({
        pageNumber,
        pageMatchIndex,
        rawStart,
        rawEndExclusive,
        query: pageContents.slice(rawStart, rawEndExclusive),
      })
      return acc
    }, [])
  }

  return {
    getPageContents,
    getPageCount: () => {
      const fromDocument = typeof pdfApp?.pdfDocument?.numPages === 'number'
        ? pdfApp.pdfDocument.numPages
        : 0
      const fromPageContents = Array.isArray(pdfApp?.findController?._pageContents)
        ? pdfApp.findController._pageContents.length
        : 0
      return Math.max(fromDocument, fromPageContents, 0)
    },
    getPageOccurrences,
    getSelectedOccurrence: () => {
      const pageNumber = getSelectedEvidenceSpikeMatchedPage(pdfApp)
      const pageMatchIndex = getSelectedEvidenceSpikeMatchIndex(pdfApp)
      if (pageNumber === null || pageMatchIndex === null) {
        return null
      }

      return getPageOccurrences(pageNumber)[pageMatchIndex] ?? null
    },
  }
}

export const setEvidenceSpikePage = (pdfApp: any, pageNumber: number): boolean => {
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

export const clearPdfJsFindHighlights = (pdfApp: any): void => {
  try {
    pdfApp?.eventBus?.dispatch?.('findbarclose', {
      source: 'pdf-evidence-navigation',
    })
  } catch (error) {
    console.warn('Unable to clear PDF.js find highlights', error)
  }
}

export const maybeClearPdfJsFindHighlights = (
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

export interface EvidenceTextLayerRect {
  left: number
  top: number
  width: number
  height: number
}

interface EvidenceTextLayerMatchResult {
  rects: EvidenceTextLayerRect[]
  matchedPage: number
}

export interface PdfJsQuoteMatchOccurrence {
  pageNumber: number
  pageMatchIndex: number
  rawStart: number
  rawEndExclusive: number
  query: string
}

export interface PdfJsQuoteSearchAdapter {
  getPageContents: (pageNumber: number) => string | null
  getPageCount: () => number
  getPageOccurrences: (pageNumber: number) => PdfJsQuoteMatchOccurrence[]
  getSelectedOccurrence: () => PdfJsQuoteMatchOccurrence | null
}

export interface NativePdfJsQuoteTarget {
  query: string
  pageNumber: number
  expectedRange: TextLayerMatchRange
}

export interface NativePdfJsQuoteSyncResult {
  success: boolean
  matchedPage: number | null
  matchesTotal: number
  currentMatch: number
  pageMatchIndex: number | null
  occurrence: PdfJsQuoteMatchOccurrence | null
}

export interface NativePdfJsOccurrenceVerification {
  matched: boolean
  reason:
    | 'page-mismatch'
    | 'exact-range'
    | 'native-text-layer-rect-overlap'
    | 'native-text-layer-range-overlap'
    | 'range-mismatch'
  derivedRange: TextLayerMatchRange | null
  rectCoverage: {
    expectedCoverage: number
    nativeCoverage: number
  } | null
}

export class StaleEvidenceNavigationError extends Error {
  constructor() {
    super('Evidence navigation request became stale')
    this.name = 'StaleEvidenceNavigationError'
  }
}

export const isStaleEvidenceNavigationError = (error: unknown): error is StaleEvidenceNavigationError => {
  return error instanceof StaleEvidenceNavigationError
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

export const getPageContainer = (iframeDoc: Document, pageNumber: number): HTMLElement | null => {
  return iframeDoc.querySelector<HTMLElement>(`.page[data-page-number="${pageNumber}"]`)
}

export const getPageTextLayer = (iframeDoc: Document, pageNumber: number): HTMLElement | null => {
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

export const findPdfJsSelectedHighlightRects = (
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

const doesPdfJsOccurrenceMatchTarget = (
  occurrence: PdfJsQuoteMatchOccurrence | null,
  target: NativePdfJsQuoteTarget,
): boolean => {
  return occurrence?.pageNumber === target.pageNumber
    && occurrence.rawStart === target.expectedRange.rawStart
    && occurrence.rawEndExclusive === target.expectedRange.rawEndExclusive
}

const resolveNativePdfJsMatchCounts = (
  pdfApp: any,
  occurrence: PdfJsQuoteMatchOccurrence | null,
  fallback?: {
    currentMatch?: number
    matchesTotal?: number
  },
): {
  currentMatch: number
  matchesTotal: number
} => {
  if (!occurrence) {
    return {
      currentMatch: fallback?.currentMatch ?? 0,
      matchesTotal: fallback?.matchesTotal ?? 0,
    }
  }

  const pageMatchesByPage = Array.isArray(pdfApp?.findController?.pageMatches)
    ? pdfApp.findController.pageMatches
    : []
  const globalMatchesTotal = pageMatchesByPage.reduce((sum: number, pageMatches: unknown) => (
    sum + (Array.isArray(pageMatches) ? pageMatches.length : 0)
  ), 0)
  const matchesBeforeSelectedPage = pageMatchesByPage
    .slice(0, Math.max(0, occurrence.pageNumber - 1))
    .reduce((sum: number, pageMatches: unknown) => (
      sum + (Array.isArray(pageMatches) ? pageMatches.length : 0)
    ), 0)

  return {
    currentMatch: matchesBeforeSelectedPage + occurrence.pageMatchIndex + 1,
    matchesTotal: globalMatchesTotal > 0
      ? globalMatchesTotal
      : Math.max(fallback?.matchesTotal ?? 0, occurrence.pageMatchIndex + 1),
  }
}

const waitForVisibleNativePdfJsSelection = async (
  iframeDoc: Document,
  pageNumber: number,
  pdfApp: any,
  timeoutMs: number = PDF_NATIVE_SELECTION_TIMEOUT_MS,
): Promise<EvidenceTextLayerRect[]> => {
  const immediateRects = findPdfJsSelectedHighlightRects(iframeDoc, pageNumber)
  if (immediateRects.length > 0) {
    return immediateRects
  }

  const eventBus = pdfApp?.eventBus
  if (!eventBus?.on || !eventBus?.off) {
    return []
  }

  return new Promise((resolve) => {
    let settled = false
    let attemptTimeoutId: number | null = null

    const finish = (rects: EvidenceTextLayerRect[]) => {
      if (settled) {
        return
      }

      settled = true
      window.clearTimeout(timeoutId)
      if (attemptTimeoutId !== null) {
        window.clearTimeout(attemptTimeoutId)
      }
      eventBus.off('textlayerrendered', handleAttempt)
      eventBus.off('updatetextlayermatches', handleAttempt)
      eventBus.off('pagerendered', handleAttempt)
      resolve(rects)
    }

    const scheduleAttempt = () => {
      if (settled || attemptTimeoutId !== null) {
        return
      }

      attemptTimeoutId = window.setTimeout(() => {
        attemptTimeoutId = null
        const rects = findPdfJsSelectedHighlightRects(iframeDoc, pageNumber)
        if (rects.length > 0) {
          finish(rects)
        }
      }, 0)
    }

    const handleAttempt = (event: any) => {
      const eventPageNumber = typeof event?.pageNumber === 'number'
        ? event.pageNumber
        : (
            typeof event?.pageIndex === 'number' && event.pageIndex >= 0
              ? event.pageIndex + 1
              : null
          )
      if (eventPageNumber !== null && eventPageNumber !== pageNumber) {
        return
      }
      scheduleAttempt()
    }

    const timeoutId = window.setTimeout(() => finish([]), timeoutMs)
    eventBus.on('textlayerrendered', handleAttempt)
    eventBus.on('updatetextlayermatches', handleAttempt)
    eventBus.on('pagerendered', handleAttempt)
    scheduleAttempt()
  })
}

export const synchronizeNativePdfJsQuoteHighlight = async (
  iframeDoc: Document,
  pdfApp: any,
  target: NativePdfJsQuoteTarget,
  options?: {
    assertCurrentRequest?: () => void
    pageTextLookup?: (pageNumber: number) => string | null
    reason?: string
  },
): Promise<NativePdfJsQuoteSyncResult> => {
  const adapter = createPdfJsQuoteSearchAdapter(pdfApp, {
    getPageText: options?.pageTextLookup,
  })
  const selectedOccurrence = adapter.getSelectedOccurrence()
  const currentNativeRects = findPdfJsSelectedHighlightRects(iframeDoc, target.pageNumber)
  const currentVerification = verifyNativePdfJsOccurrenceMatchesTarget(
    iframeDoc,
    pdfApp,
    selectedOccurrence,
    target,
    {
      pageTextLookup: options?.pageTextLookup,
      nativeRects: currentNativeRects,
    },
  )
  const alreadySelected = currentVerification.matched && currentNativeRects.length > 0

  if (alreadySelected) {
    const matchCounts = resolveNativePdfJsMatchCounts(pdfApp, selectedOccurrence)
    logPdfEvidenceDebug('Reusing existing native PDF.js quote highlight', {
      query: target.query,
      pageNumber: target.pageNumber,
      reason: options?.reason ?? 'quote-match',
      occurrence: selectedOccurrence,
      verification: currentVerification,
      ...matchCounts,
    })
    return {
      success: true,
      matchedPage: target.pageNumber,
      currentMatch: matchCounts.currentMatch,
      matchesTotal: matchCounts.matchesTotal,
      pageMatchIndex: selectedOccurrence?.pageMatchIndex ?? null,
      occurrence: selectedOccurrence,
    }
  }

  options?.assertCurrentRequest?.()
  setEvidenceSpikePage(pdfApp, target.pageNumber)
  const outcome = await dispatchEvidenceSpikeFind(pdfApp, {
    query: target.query,
    reason: 'exact-quote',
  })
  options?.assertCurrentRequest?.()

  const selectedAfterFind = createPdfJsQuoteSearchAdapter(pdfApp, {
    getPageText: options?.pageTextLookup,
  }).getSelectedOccurrence()
  if (!outcome.found) {
    logPdfEvidenceDebug('Native PDF.js quote highlight did not produce a trusted match to verify', {
      query: target.query,
      pageNumber: target.pageNumber,
      reason: options?.reason ?? 'quote-match',
      expectedRange: target.expectedRange,
      selectedOccurrence: selectedAfterFind,
      outcome,
    })
    return {
      success: false,
      matchedPage: selectedAfterFind?.pageNumber ?? outcome.matchedPage,
      currentMatch: outcome.currentMatch,
      matchesTotal: outcome.matchesTotal,
      pageMatchIndex: selectedAfterFind?.pageMatchIndex ?? outcome.pageMatchIndex,
      occurrence: selectedAfterFind,
    }
  }

  const nativeRects = await waitForVisibleNativePdfJsSelection(
    iframeDoc,
    target.pageNumber,
    pdfApp,
  )
  options?.assertCurrentRequest?.()

  const verifiedOccurrence = createPdfJsQuoteSearchAdapter(pdfApp, {
    getPageText: options?.pageTextLookup,
  }).getSelectedOccurrence()
  const verification = verifyNativePdfJsOccurrenceMatchesTarget(
    iframeDoc,
    pdfApp,
    verifiedOccurrence,
    target,
    {
      pageTextLookup: options?.pageTextLookup,
      nativeRects,
    },
  )
  if (nativeRects.length === 0 || !verification.matched) {
    logPdfEvidenceDebug('Native PDF.js quote highlight did not become visibly selected on the intended occurrence', {
      query: target.query,
      pageNumber: target.pageNumber,
      reason: options?.reason ?? 'quote-match',
      expectedRange: target.expectedRange,
      selectedOccurrenceAfterFind: selectedAfterFind,
      selectedOccurrence: verifiedOccurrence,
      rectCount: nativeRects.length,
      verification,
    })
    return {
      success: false,
      matchedPage: verifiedOccurrence?.pageNumber ?? target.pageNumber,
      currentMatch: outcome.currentMatch,
      matchesTotal: outcome.matchesTotal,
      pageMatchIndex: verifiedOccurrence?.pageMatchIndex ?? outcome.pageMatchIndex,
      occurrence: verifiedOccurrence,
    }
  }

  const matchCounts = resolveNativePdfJsMatchCounts(pdfApp, verifiedOccurrence, outcome)
  logPdfEvidenceDebug('Verified native PDF.js quote highlight occurrence', {
    query: target.query,
    pageNumber: target.pageNumber,
    reason: options?.reason ?? 'quote-match',
    expectedRange: target.expectedRange,
    occurrence: verifiedOccurrence,
    rectCount: nativeRects.length,
    verification,
    ...matchCounts,
  })
  return {
    success: true,
    matchedPage: target.pageNumber,
    currentMatch: matchCounts.currentMatch,
    matchesTotal: matchCounts.matchesTotal,
    pageMatchIndex: verifiedOccurrence?.pageMatchIndex ?? null,
    occurrence: verifiedOccurrence,
  }
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

export const findTextLayerMatchRects = (
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

const getEvidenceTextLayerRectArea = (rect: EvidenceTextLayerRect): number => {
  return Math.max(0, rect.width) * Math.max(0, rect.height)
}

const getEvidenceTextLayerRectIntersectionArea = (
  left: EvidenceTextLayerRect,
  right: EvidenceTextLayerRect,
): number => {
  const overlapLeft = Math.max(left.left, right.left)
  const overlapTop = Math.max(left.top, right.top)
  const overlapRight = Math.min(left.left + left.width, right.left + right.width)
  const overlapBottom = Math.min(left.top + left.height, right.top + right.height)
  const overlapWidth = overlapRight - overlapLeft
  const overlapHeight = overlapBottom - overlapTop
  if (overlapWidth <= 0 || overlapHeight <= 0) {
    return 0
  }

  return overlapWidth * overlapHeight
}

const getEvidenceTextLayerRectSetCoverage = (
  sourceRects: EvidenceTextLayerRect[],
  targetRects: EvidenceTextLayerRect[],
): number => {
  if (sourceRects.length === 0 || targetRects.length === 0) {
    return 0
  }

  const totalSourceArea = sourceRects.reduce((sum, rect) => sum + getEvidenceTextLayerRectArea(rect), 0)
  if (totalSourceArea <= 0) {
    return 0
  }

  const coveredArea = sourceRects.reduce((sum, rect) => {
    const rectArea = getEvidenceTextLayerRectArea(rect)
    if (rectArea <= 0) {
      return sum
    }

    const bestOverlap = targetRects.reduce((best, candidate) => (
      Math.max(best, getEvidenceTextLayerRectIntersectionArea(rect, candidate))
    ), 0)
    return sum + Math.min(rectArea, bestOverlap)
  }, 0)

  return coveredArea / totalSourceArea
}

const getTextLayerRangeOverlapCoverage = (
  left: TextLayerMatchRange,
  right: TextLayerMatchRange,
): number => {
  const overlapStart = Math.max(left.rawStart, right.rawStart)
  const overlapEndExclusive = Math.min(left.rawEndExclusive, right.rawEndExclusive)
  const overlapLength = Math.max(0, overlapEndExclusive - overlapStart)
  const shorterLength = Math.min(
    left.rawEndExclusive - left.rawStart,
    right.rawEndExclusive - right.rawStart,
  )
  if (shorterLength <= 0) {
    return 0
  }

  return overlapLength / shorterLength
}

export function verifyNativePdfJsOccurrenceMatchesTarget(
  iframeDoc: Document,
  pdfApp: any,
  occurrence: PdfJsQuoteMatchOccurrence | null,
  target: NativePdfJsQuoteTarget,
  options?: {
    pageTextLookup?: (pageNumber: number) => string | null
    nativeRects?: EvidenceTextLayerRect[]
  },
): NativePdfJsOccurrenceVerification {
  if (!occurrence || occurrence.pageNumber !== target.pageNumber) {
    return {
      matched: false,
      reason: 'page-mismatch',
      derivedRange: null,
      rectCoverage: null,
    }
  }

  if (doesPdfJsOccurrenceMatchTarget(occurrence, target)) {
    return {
      matched: true,
      reason: 'exact-range',
      derivedRange: target.expectedRange,
      rectCoverage: null,
    }
  }

  const expectedRects = findTextLayerMatchRects(
    iframeDoc,
    target.pageNumber,
    target.query,
    occurrence.pageMatchIndex,
  )
  const nativeRects = options?.nativeRects ?? findPdfJsSelectedHighlightRects(iframeDoc, target.pageNumber)
  const expectedCoverage = getEvidenceTextLayerRectSetCoverage(expectedRects, nativeRects)
  const nativeCoverage = getEvidenceTextLayerRectSetCoverage(nativeRects, expectedRects)
  if (expectedCoverage >= 0.9 && nativeCoverage >= 0.6) {
    return {
      matched: true,
      reason: 'native-text-layer-rect-overlap',
      derivedRange: null,
      rectCoverage: {
        expectedCoverage,
        nativeCoverage,
      },
    }
  }

  const adapter = createPdfJsQuoteSearchAdapter(pdfApp, {
    getPageText: options?.pageTextLookup,
  })
  const pageText = adapter.getPageContents(target.pageNumber)
  const derivedRange = pageText
    ? findTextLayerMatchRange(pageText, target.query, occurrence.pageMatchIndex)
    : null
  if (
    (expectedRects.length === 0 || nativeRects.length === 0)
    && derivedRange
    && getTextLayerRangeOverlapCoverage(derivedRange, target.expectedRange) >= 0.9
  ) {
    return {
      matched: true,
      reason: 'native-text-layer-range-overlap',
      derivedRange,
      rectCoverage: {
        expectedCoverage,
        nativeCoverage,
      },
    }
  }

  return {
    matched: false,
    reason: 'range-mismatch',
    derivedRange,
    rectCoverage: {
      expectedCoverage,
      nativeCoverage,
    },
  }
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

export const waitForTextLayerMatch = async (
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

export const isDegradedLocatorQuality = (quality: EvidenceLocatorQuality): boolean => {
  return quality === 'section_only'
    || quality === 'page_only'
    || quality === 'document_only'
    || quality === 'unresolved'
}

export const resolveFuzzyQuoteMatchLocatorQuality = (
  anchorQuality: EvidenceLocatorQuality,
  requestedQuote: string,
  matchedQuery: string,
): EvidenceLocatorQuality => {
  if (anchorQuality === 'normalized_quote') {
    return 'normalized_quote'
  }

  return requestedQuote.trim() === matchedQuery.trim() ? 'exact_quote' : 'normalized_quote'
}

export const buildRapidFuzzQuoteMatchNavigationNote = (
  matchResult: PdfEvidenceFuzzyMatchResult,
  options?: {
    crossPage?: boolean
  },
): string => {
  if (options?.crossPage) {
    return 'Localized the quote with RapidFuzz and kept the best native anchor-page PDF.js highlight because the recovered span crosses pages.'
  }

  if (matchResult.strategy === 'rapidfuzz-stitched-page') {
    return 'Localized the quote with RapidFuzz across adjacent PDF.js page text and highlighted the verified native anchor-page span.'
  }

  return 'Localized the quote with RapidFuzz and highlighted the verified native PDF.js span.'
}

export const buildEvidenceSpikeAnchor = (input: PdfEvidenceSpikeInput): EvidenceAnchor => {
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

export const buildNavigationCommandKey = (command: EvidenceNavigationCommand): string => {
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

export const getNavigationBadgeColor = (
  result: PdfViewerNavigationResult,
): 'error' | 'warning' | 'success' => {
  if (result.locatorQuality === 'unresolved') {
    return 'error'
  }

  return result.degraded ? 'warning' : 'success'
}

export const formatLocatorQualityLabel = (quality: EvidenceLocatorQuality): string => {
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

export const getEvidenceHighlightRectStyles = (
  highlight: EvidenceTextLayerHighlight,
  theme: Theme,
): Record<string, string> => {
  const evidenceColor = theme.palette.success.main
  const sectionColor = theme.palette.text.secondary
  const overlayHaloColor = theme.palette.mode === 'dark'
    ? theme.palette.common.white
    : theme.palette.common.black

  if (highlight.kind === 'section') {
    return highlight.mode === 'hover'
      ? {
          background: alpha(sectionColor, 0.12),
          border: `1px dashed ${alpha(sectionColor, 0.55)}`,
          boxShadow: `0 0 0 1px ${alpha(overlayHaloColor, 0.2)}`,
        }
      : {
          background: alpha(sectionColor, 0.18),
          border: `2px solid ${alpha(sectionColor, 0.72)}`,
          boxShadow: `0 0 0 1px ${alpha(overlayHaloColor, 0.25)}`,
        }
  }

  return highlight.mode === 'hover'
    ? {
        background: alpha(evidenceColor, 0.16),
        border: `1px dashed ${alpha(evidenceColor, 0.7)}`,
        boxShadow: `0 0 0 1px ${alpha(evidenceColor, 0.15)}`,
      }
    : {
        background: alpha(evidenceColor, 0.28),
        border: `2px solid ${alpha(evidenceColor, 0.92)}`,
        boxShadow: `0 0 0 1px ${alpha(overlayHaloColor, 0.22)}`,
      }
}

export const getNavigationBannerSeverity = (
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

export const getNavigationBannerMessage = (
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

export interface PdfEvidenceSpikeFindOutcome extends Pick<PdfEvidenceSpikeResult, 'matchedPage' | 'matchesTotal' | 'currentMatch'> {
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

export const dispatchEvidenceSpikeFind = async (pdfApp: any, candidate: PdfEvidenceSpikeCandidate) => {
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
