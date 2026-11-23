import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTheme } from '@mui/material/styles'
import { debug, getEnvFlag } from '@/utils/env'
import {
  PDFViewerDocumentChangedEvent,
  dispatchPDFViewerEvidenceAnchorSelected,
  onPDFDocumentChanged,
  onPDFViewerDocumentCleared,
  onPDFViewerNavigateEvidence,
} from '@/components/pdfViewer/pdfEvents'
import { normalizePdfViewerDocumentUrl } from '@/components/pdfViewer/viewerDocumentUrl'
import type { EvidenceNavigationCommand } from '@/features/curation/evidence'
import {
  fuzzyMatchPdfEvidenceQuote,
  type PdfEvidenceFuzzyMatchPage,
  type PdfEvidenceFuzzyMatchResult,
} from '@/features/curation/services/pdfEvidenceMatcherService'
import {
  completeDocumentLoad,
  failDocumentLoad,
} from '@/features/documents/documentLoadEvents'
import { safeRemoveItem } from '@/lib/browserStorage'
import { getChatLocalStorageKeys } from '@/lib/chatCacheKeys'
import { PdfViewerChrome } from './PdfViewerChrome'
import {
  EVIDENCE_SPIKE_EVENT_NAME,
  PDF_EVIDENCE_FUZZY_MATCH_MIN_SCORE,
  PDF_TEXT_LAYER_MATCH_TIMEOUT_MS,
  StaleEvidenceNavigationError,
  buildEvidenceSpikeAnchor,
  buildEvidenceSpikeSectionCandidates,
  buildNavigationCommandKey,
  buildRapidFuzzQuoteMatchNavigationNote,
  clearPdfJsFindHighlights,
  createPdfJsQuoteSearchAdapter,
  dispatchEvidenceSpikeFind,
  findPdfJsSelectedHighlightRects,
  findEvidenceOccurrenceIndexForRange,
  findTextLayerMatchRects,
  getEvidenceHighlightRectStyles,
  getNavigationBannerMessage,
  getPageContainer,
  getPageTextLayer,
  getSelectedEvidenceSpikePage,
  isDegradedLocatorQuality,
  isStaleEvidenceNavigationError,
  joinPdfJsTextContentItems,
  maybeClearPdfJsFindHighlights,
  normalizeEvidenceSpikePageHints,
  normalizeEvidenceSpikeText,
  publishEvidenceSpikeResult,
  resolveFuzzyQuoteMatchLocatorQuality,
  setEvidenceSpikePage,
  synchronizeNativePdfJsQuoteHighlight,
  verifyNativePdfJsOccurrenceMatchesTarget,
  waitForTextLayerMatch,
  type EvidenceTextLayerHighlight,
  type NativePdfJsQuoteTarget,
  type PdfEvidencePageTextCorpusCache,
  type PdfEvidenceSpikeInput,
  type PdfEvidenceSpikeResult,
  type PdfViewerNavigationResult,
} from './pdfEvidenceNavigation'
import {
  installPdfEvidenceDebugWindow,
  logPdfEvidenceDebug,
  setLastPdfEvidenceNavigationResult,
} from './pdfViewerDebug'
import { persistSession } from './pdfViewerHighlighting'
import {
  DEFAULT_STATE,
  VIEWER_BASE_PATH,
  activeViewerFile,
  type PdfViewerProps,
  type ViewerDocument,
  type ViewerState,
  type ViewerStatus,
  type ViewerTelemetry,
} from './pdfViewerTypes'
import { usePdfViewerUpload } from './usePdfViewerUpload'
import { fileDisplayLabel, isPdfFile, type ViewerFile } from './viewerFiles'
import {
  buildEvidenceFileResult,
  openedFileAnnouncement,
  pageLessQuoteNotLocatedMessage,
  type TextNavigationRequest,
} from './viewerFileNavigation'

export {
  buildEvidenceSpikeQuoteCandidates,
  buildEvidenceSpikeSectionCandidates,
  findExpandedEvidenceQueryFromPageText,
  normalizeEvidenceSpikePageHints,
  normalizeEvidenceSpikeText,
} from './pdfEvidenceNavigation'
export type {
  ExpandedEvidenceQuery,
  PdfEvidenceSpikeCandidate,
  PdfEvidenceSpikeInput,
  PdfEvidenceSpikeResult,
  PdfViewerNavigationResult,
} from './pdfEvidenceNavigation'
export type { PdfViewerProps } from './pdfViewerTypes'

const PDFJS_FIND_STATE_NOT_FOUND = 1

/** Whether two tab lists show the same files the same way (a refreshed list from the server). */
function sameViewerFiles(left: readonly ViewerFile[], right: readonly ViewerFile[]): boolean {
  return left.length === right.length && left.every((file, index) => {
    const other = right[index]!
    return file.file_id === other.file_id
      && file.role === other.role
      && file.display_name === other.display_name
      && file.display_mode === other.display_mode
      && file.status === other.status
      && file.tab_order === other.tab_order
      && file.content_url === other.content_url
      && file.text_url === other.text_url
  })
}

function sameIds(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((id, index) => id === right[index])
}

const MAIN_FILE_UNREACHABLE_MESSAGE = 'Unable to reach the PDF document. Please retry or re-upload.'
/** Approved by Chris (23 Nov 2026): a supplement's PDF that cannot be reached (no re-upload offered). */
export const SUPPLEMENT_UNREACHABLE_MESSAGE = 'This supplement could not be loaded.'

export function PdfViewer({
  activeDocumentOwnerToken,
  storageUserId = null,
  variant = 'default',
  pendingNavigation = null,
  onNavigationComplete,
  onNavigationStateChange,
}: PdfViewerProps) {
  const theme = useTheme()
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const pdfAppRef = useRef<any>(null)
  const cleanupRefs = useRef<(() => void)[]>([])
  const toolbarSearchQueryRef = useRef('')
  const viewerStateRef = useRef<ViewerState>({ ...DEFAULT_STATE })
  const loadStartRef = useRef<number | null>(null)
  const handledNavigationKeyRef = useRef<string | null>(null)
  const navigationRequestIdRef = useRef(0)
  // The pdf.js page-text corpus of each PDF file, keyed by file; never shared across files.
  const evidencePageTextCorporaRef = useRef<Map<string, PdfEvidencePageTextCorpusCache>>(new Map())
  // Page and scroll of each file, so a tab switch returns to where the curator was.
  const fileViewerStatesRef = useRef<Map<string, ViewerState>>(new Map())
  const textNavigationRequestIdRef = useRef(0)

  const [status, setStatus] = useState<ViewerStatus>('idle')
  const [activeDocument, setActiveDocument] = useState<ViewerDocument | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [retryKey, setRetryKey] = useState(0)
  const [_telemetry, setTelemetry] = useState<ViewerTelemetry>({
    lastLoadMs: null,
    slowLoad: false,
  })
  const [overlayRenderKey, setOverlayRenderKey] = useState(0)
  const [navigationResult, setNavigationResult] = useState<PdfViewerNavigationResult | null>(null)
  const [evidenceHighlight, setEvidenceHighlight] = useState<EvidenceTextLayerHighlight | null>(null)
  const [viewerUiState, setViewerUiState] = useState({
    currentPage: DEFAULT_STATE.currentPage,
    zoomLevel: DEFAULT_STATE.zoomLevel,
    searchQuery: '',
    searchCurrent: null as number | null,
    searchTotal: null as number | null,
    searchNotFound: false,
  })
  const [eventPendingNavigation, setEventPendingNavigation] =
    useState<EvidenceNavigationCommand | null>(null)
  const [textNavigation, setTextNavigation] = useState<TextNavigationRequest | null>(null)
  const [fileAnnouncement, setFileAnnouncement] = useState('')
  // Shown instead of the empty viewer when the loaded paper has nothing to show yet.
  const [idleNotice, setIdleNotice] = useState<string | null>(null)
  const effectivePendingNavigation = pendingNavigation ?? eventPendingNavigation
  const viewerSessionStorageKey = useMemo(
    () => (storageUserId ? getChatLocalStorageKeys(storageUserId).pdfViewerSession : null),
    [storageUserId],
  )
  const activeFile: ViewerFile | null = activeDocument ? activeViewerFile(activeDocument) : null
  const activeFileIsPdf = activeFile !== null && isPdfFile(activeFile)
  const activeFilePageCount = activeFile?.page_count ?? null
  const activeFileName = activeFile ? fileDisplayLabel(activeFile) : null
  const evidencePageTextCacheKey = activeDocument && activeFileIsPdf
    ? `${activeDocument.documentId}:${activeDocument.activeFileId}`
    : null
  const activeDocumentOwnerRef = useRef(activeDocumentOwnerToken)
  const viewerSessionStorageUserIdRef = useRef<string | null>(storageUserId)
  const storageUserIdRef = useRef<string | null>(storageUserId)
  const idleResetErrorRef = useRef<string | null>(null)
  const {
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
  } = usePdfViewerUpload({ disabled: Boolean(activeDocument) })

  const commitNavigationResult = useCallback((result: PdfViewerNavigationResult | null) => {
    setLastPdfEvidenceNavigationResult(result)
    logPdfEvidenceDebug('Committed evidence navigation result to viewer state', {
      result,
    })
    setNavigationResult(result)
    onNavigationStateChange?.(result)
  }, [onNavigationStateChange])

  const persistViewerSession = useCallback((document: ViewerDocument, state: ViewerState) => {
    if (viewerSessionStorageUserIdRef.current !== storageUserId) {
      return
    }

    persistSession(viewerSessionStorageKey, document, state)
  }, [storageUserId, viewerSessionStorageKey])

  const resetViewerToIdle = useCallback((nextError: string | null = null) => {
    handledNavigationKeyRef.current = null
    navigationRequestIdRef.current += 1
    // A queued evidence navigation names no document; it must not run against
    // whichever PDF loads next.
    setEventPendingNavigation(null)
    viewerStateRef.current = {
      ...DEFAULT_STATE,
      lastInteraction: new Date().toISOString(),
    }
    toolbarSearchQueryRef.current = ''
    idleResetErrorRef.current = nextError
    documentLoadSignalPendingRef.current = false
    evidencePageTextCorporaRef.current = new Map()
    fileViewerStatesRef.current = new Map()
    textNavigationRequestIdRef.current += 1
    setTextNavigation(null)
    setFileAnnouncement('')
    setIdleNotice(null)
    setActiveDocument(null)
    setStatus(nextError ? 'error' : 'idle')
    setError(nextError)
    setEvidenceHighlight(null)
    setViewerUiState({
      currentPage: DEFAULT_STATE.currentPage,
      zoomLevel: DEFAULT_STATE.zoomLevel,
      searchQuery: '',
      searchCurrent: null,
      searchTotal: null,
      searchNotFound: false,
    })
    commitNavigationResult(null)
    if (viewerSessionStorageKey) {
      safeRemoveItem(() => window.localStorage, viewerSessionStorageKey, {
        owner: 'pdf-viewer',
        workflowCritical: true,
      })
    }
  }, [commitNavigationResult, viewerSessionStorageKey])

  /**
   * Signal terminal document load states to the Home overlay.
   */
  // Home's load overlay follows the paper's first file only: a later tab (a PDF
  // supplement) that fails shows its error in its own pane, never Home's backdrop.
  const documentLoadSignalPendingRef = useRef(false)
  const signalLoadComplete = useCallback(() => {
    if (!documentLoadSignalPendingRef.current) {
      return
    }
    documentLoadSignalPendingRef.current = false
    completeDocumentLoad({
      documentId: activeDocument?.documentId,
      filename: activeFileName ?? undefined,
      message: `${activeFileName ?? 'Document'} loaded in the PDF viewer.`,
    })
  }, [activeDocument?.documentId, activeFileName])

  const signalLoadError = useCallback((message: string) => {
    if (!documentLoadSignalPendingRef.current) {
      return
    }
    documentLoadSignalPendingRef.current = false
    failDocumentLoad({
      documentId: activeDocument?.documentId,
      filename: activeFileName ?? undefined,
      message,
    })
  }, [activeDocument?.documentId, activeFileName])

  /** The active PDF file's corpus cache; created on first use, kept for that file only. */
  const getEvidencePageTextCorpusCache = useCallback((cacheKey: string): PdfEvidencePageTextCorpusCache => {
    const existing = evidencePageTextCorporaRef.current.get(cacheKey)
    if (existing) {
      return existing
    }
    const created: PdfEvidencePageTextCorpusCache = { cacheKey, pages: null, promise: null }
    evidencePageTextCorporaRef.current.set(cacheKey, created)
    return created
  }, [])

  const getNativeEvidencePageText = useCallback((pdfApp: any, pageNumber: number): string | null => {
    const pageContents = pdfApp?.findController?._pageContents?.[pageNumber - 1]
    return typeof pageContents === 'string' ? pageContents : null
  }, [])

  const getNativeEvidencePageCorpus = useCallback((pdfApp: any): PdfEvidenceFuzzyMatchPage[] | null => {
    const pageCount = pdfApp?.pdfDocument?.numPages
      ?? pdfApp?.pdfViewer?.pdfDocument?.numPages
      ?? activeFilePageCount
      ?? 0

    if (!Number.isInteger(pageCount) || pageCount <= 0) {
      return null
    }

    const pages: PdfEvidenceFuzzyMatchPage[] = []
    for (let pageNumber = 1; pageNumber <= pageCount; pageNumber += 1) {
      const pageText = getNativeEvidencePageText(pdfApp, pageNumber)
      if (pageText === null) {
        return null
      }

      pages.push({
        pageNumber,
        text: pageText,
      })
    }

    return pages
  }, [activeFilePageCount, getNativeEvidencePageText])

  const extractEvidencePageText = useCallback(async (
    pdfApp: any,
    pageNumber: number,
  ): Promise<PdfEvidenceFuzzyMatchPage> => {
    const nativePageText = getNativeEvidencePageText(pdfApp, pageNumber)
    if (nativePageText !== null) {
      return {
        pageNumber,
        text: nativePageText,
      }
    }

    const pdfDocument = pdfApp?.pdfDocument ?? pdfApp?.pdfViewer?.pdfDocument ?? null
    if (!pdfDocument?.getPage) {
      throw new Error('PDF.js document is not ready for raw evidence page text extraction.')
    }

    const page = await pdfDocument.getPage(pageNumber)
    const textContent = await page.getTextContent({ disableNormalization: true })
    return {
      pageNumber,
      text: joinPdfJsTextContentItems(textContent),
    }
  }, [])

  const ensureEvidencePageTextCorpus = useCallback(async (pdfApp: any): Promise<PdfEvidenceFuzzyMatchPage[]> => {
    const cacheKey = evidencePageTextCacheKey
    if (cacheKey === null) {
      throw new Error('No PDF file is active for evidence page text extraction.')
    }
    const currentCache = getEvidencePageTextCorpusCache(cacheKey)
    if (currentCache.pages) {
      return currentCache.pages
    }

    const nativeCorpus = getNativeEvidencePageCorpus(pdfApp)
    if (nativeCorpus) {
      currentCache.pages = nativeCorpus
      return nativeCorpus
    }

    if (!currentCache.promise) {
      currentCache.promise = (async () => {
        const pageCount = pdfApp?.pdfDocument?.numPages
          ?? pdfApp?.pdfViewer?.pdfDocument?.numPages
          ?? activeFilePageCount
          ?? 0
        if (!Number.isInteger(pageCount) || pageCount <= 0) {
          throw new Error('PDF.js page count is unavailable for evidence page text extraction.')
        }

        try {
          await dispatchEvidenceSpikeFind(pdfApp, {
            query: '__pdf_evidence_warmup__',
            reason: 'warm-search-corpus',
          })
        } catch (warmupError) {
          logPdfEvidenceDebug('Unable to warm the native PDF.js search corpus before RapidFuzz localization', {
            error: warmupError instanceof Error ? warmupError.message : String(warmupError),
          })
        } finally {
          clearPdfJsFindHighlights(pdfApp)
        }

        const warmedNativeCorpus = getNativeEvidencePageCorpus(pdfApp)
        const pages = warmedNativeCorpus ?? await Promise.all(
          Array.from({ length: pageCount }, (_, pageIndex) => extractEvidencePageText(pdfApp, pageIndex + 1)),
        )
        // The cache object belongs to this file; a later document load replaces the map.
        currentCache.pages = pages
        currentCache.promise = null
        return pages
      })().catch((error) => {
        currentCache.promise = null
        throw error
      })
    }

    return currentCache.promise
  }, [activeFilePageCount, evidencePageTextCacheKey, extractEvidencePageText, getEvidencePageTextCorpusCache, getNativeEvidencePageCorpus])

  const getCachedEvidencePageText = useCallback((pageNumber: number): string | null => {
    const cache = evidencePageTextCacheKey === null
      ? undefined
      : evidencePageTextCorporaRef.current.get(evidencePageTextCacheKey)
    if (!cache?.pages) {
      return null
    }

    return cache.pages.find((page) => page.pageNumber === pageNumber)?.text ?? null
  }, [evidencePageTextCacheKey])

  const activeContentUrl = activeFileIsPdf ? activeFile?.content_url ?? null : null
  const viewerSrc = useMemo(() => {
    if (!activeDocument || !activeContentUrl) return 'about:blank'
    const url = new URL(VIEWER_BASE_PATH, window.location.origin)
    url.searchParams.set('file', activeContentUrl)
    // Append a cache-busting token so repeated loads of the same URL still trigger iframe reloads
    url.searchParams.set('ts', activeDocument.loadedAt)
    return url.toString()
  }, [activeContentUrl, activeDocument])

  const executeEvidenceNavigation = useCallback(async (
    command: EvidenceNavigationCommand,
    options?: {
      pageHints?: number[]
      renderOverlay?: boolean
    },
  ): Promise<PdfViewerNavigationResult> => {
    const requestId = ++navigationRequestIdRef.current
    const assertCurrentRequest = () => {
      if (navigationRequestIdRef.current !== requestId) {
        logPdfEvidenceDebug('Ignoring stale evidence navigation result', {
          anchorId: command.anchorId,
          requestId,
          latestRequestId: navigationRequestIdRef.current,
        })
        throw new StaleEvidenceNavigationError()
      }
    }
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
      fileId: activeDocument?.activeFileId ?? null,
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

    toolbarSearchQueryRef.current = ''
    setViewerUiState((current) => ({
      ...current,
      searchQuery: '',
      searchCurrent: null,
      searchTotal: null,
      searchNotFound: false,
    }))
    setEvidenceHighlight(null)
    clearPdfJsFindHighlights(pdfApp)

    const quoteSearchText = (searchText || quote).trim()
    const preferredPage = pageHints[0] ?? null
    let quoteMatchedPageContext: {
      currentMatch: number
      matchedPage: number | null
      matchedQuery: string
      matchesTotal: number
    } | null = null

    logPdfEvidenceDebug('Starting evidence navigation', {
      anchorId: command.anchorId,
      requestId,
      mode: command.mode,
      quote,
      searchText,
      quoteSearchText,
      pageHints,
      preferredPage,
      sectionTitle,
      renderOverlay,
    })
    if (quoteSearchText) {
      assertCurrentRequest()
      attemptedQueries.push(quoteSearchText)
      let fuzzyMatch: PdfEvidenceFuzzyMatchResult | null = null

      try {
        const pageCorpus = await ensureEvidencePageTextCorpus(pdfApp)
        assertCurrentRequest()
        fuzzyMatch = await fuzzyMatchPdfEvidenceQuote({
          quote: quoteSearchText,
          pageHints,
          minScore: PDF_EVIDENCE_FUZZY_MATCH_MIN_SCORE,
          pages: pageCorpus,
        })
        assertCurrentRequest()
      } catch (quoteMatchError) {
        logPdfEvidenceDebug('RapidFuzz quote localization request failed', {
          anchorId: command.anchorId,
          quoteSearchText,
          pageHints,
          error: quoteMatchError instanceof Error ? quoteMatchError.message : String(quoteMatchError),
        })
      }

      logPdfEvidenceDebug('RapidFuzz quote localization result', {
        anchorId: command.anchorId,
        quoteSearchText,
        pageHints,
        result: fuzzyMatch,
      })

      if (fuzzyMatch && fuzzyMatch.matchedPage !== null) {
        quoteMatchedPageContext = {
          currentMatch: 0,
          matchedPage: fuzzyMatch.matchedPage,
          matchedQuery: fuzzyMatch.matchedQuery ?? quoteSearchText,
          matchesTotal: 0,
        }
      }

      if (
        fuzzyMatch?.found
        && fuzzyMatch.matchedPage !== null
        && fuzzyMatch.matchedRange
        && fuzzyMatch.matchedQuery
        && iframeDoc
      ) {
        const matchedTarget: NativePdfJsQuoteTarget = {
          query: fuzzyMatch.matchedQuery,
          pageNumber: fuzzyMatch.matchedPage,
          expectedRange: {
            rawStart: fuzzyMatch.matchedRange.rawStart,
            rawEndExclusive: fuzzyMatch.matchedRange.rawEndExclusive,
          },
        }
        if (matchedTarget.query.trim() !== quoteSearchText) {
          attemptedQueries.push(matchedTarget.query)
        }

        const locatorQuality = resolveFuzzyQuoteMatchLocatorQuality(
          anchor.locator_quality,
          quoteSearchText,
          matchedTarget.query,
        )
        const matchedSync = await synchronizeNativePdfJsQuoteHighlight(
          iframeDoc,
          pdfApp,
          matchedTarget,
          {
            assertCurrentRequest,
            pageTextLookup: getCachedEvidencePageText,
            reason: fuzzyMatch.crossPage ? 'rapidfuzz-cross-page' : 'rapidfuzz-quote-match',
          },
        )
        assertCurrentRequest()

        if (matchedSync.success) {
          const matchedPage = matchedSync.matchedPage ?? matchedTarget.pageNumber
          const degradedQuoteMatch = fuzzyMatch.crossPage
          setEvidenceHighlight({
            anchorId: command.anchorId,
            kind: 'quote',
            mode: command.mode,
            pageNumber: matchedPage,
            query: matchedTarget.query,
            pageMatchIndex: matchedSync.pageMatchIndex,
            rects: null,
            renderOverlay: false,
            nativeTarget: matchedTarget,
          })
          maybeClearPdfJsFindHighlights(pdfApp, {
            preserveNativeHighlight: true,
            reason: 'rapidfuzz-quote-match',
          })
          logPdfEvidenceDebug('Evidence navigation matched successfully with RapidFuzz localization and native PDF.js highlighting', {
            anchorId: command.anchorId,
            quoteSearchText,
            matchedTarget,
            locatorQuality,
            degradedQuoteMatch,
            strategy: fuzzyMatch.strategy,
            score: fuzzyMatch.score,
            pageRanges: fuzzyMatch.pageRanges,
            occurrence: matchedSync.occurrence,
          })
          return {
            ...baseResult,
            status: 'matched',
            strategy: fuzzyMatch.strategy,
            locatorQuality,
            degraded: degradedQuoteMatch,
            matchedQuery: matchedTarget.query,
            matchedPage,
            matchesTotal: matchedSync.matchesTotal,
            currentMatch: matchedSync.currentMatch,
            note: buildRapidFuzzQuoteMatchNavigationNote(fuzzyMatch, {
              crossPage: degradedQuoteMatch,
            }),
          }
        }

        logPdfEvidenceDebug('RapidFuzz localized quote could not be verified as a visible native PDF.js occurrence', {
          anchorId: command.anchorId,
          quoteSearchText,
          matchedTarget,
          result: fuzzyMatch,
          matchedSync,
        })
        clearPdfJsFindHighlights(pdfApp)

        const pageCorpusText = getCachedEvidencePageText(matchedTarget.pageNumber)
        const trustedOccurrenceIndex = pageCorpusText
          ? findEvidenceOccurrenceIndexForRange(
            pageCorpusText,
            matchedTarget.query,
            matchedTarget.expectedRange,
          )
          : null
        const textLayerRects = matchedSync.occurrence && trustedOccurrenceIndex !== null
          ? findTextLayerMatchRects(
            iframeDoc,
            matchedTarget.pageNumber,
            matchedTarget.query,
            trustedOccurrenceIndex,
          )
          : []
        if (textLayerRects.length > 0) {
          setEvidenceHighlight({
            anchorId: command.anchorId,
            kind: 'quote',
            mode: command.mode,
            pageNumber: matchedTarget.pageNumber,
            query: matchedTarget.query,
            pageMatchIndex: trustedOccurrenceIndex,
            rects: textLayerRects,
            renderOverlay: true,
            nativeTarget: null,
          })
          logPdfEvidenceDebug('Used the RapidFuzz-localized text-layer range after native PDF.js occurrence verification failed', {
            anchorId: command.anchorId,
            quoteSearchText,
            matchedTarget,
            rectCount: textLayerRects.length,
          })
          return {
            ...baseResult,
            status: 'matched',
            strategy: fuzzyMatch.strategy,
            locatorQuality,
            degraded: fuzzyMatch.crossPage,
            matchedQuery: matchedTarget.query,
            matchedPage: matchedTarget.pageNumber,
            matchesTotal: 1,
            currentMatch: 1,
            note: 'Localized the quote with RapidFuzz and highlighted the independently matched PDF text layer span.',
          }
        }
      }
    }

    const sectionCandidates = buildEvidenceSpikeSectionCandidates(sectionTitle, anchor.subsection_title)
    const quoteContextPage = quoteMatchedPageContext?.matchedPage
      ?? getSelectedEvidenceSpikePage(pdfApp)
      ?? preferredPage
    const sectionPreferredPage = quoteContextPage ?? preferredPage
    for (const candidate of sectionCandidates) {
      assertCurrentRequest()
      if (sectionPreferredPage !== null) {
        setEvidenceSpikePage(pdfApp, sectionPreferredPage)
      }
      attemptedQueries.push(candidate.query)
      const outcome = await dispatchEvidenceSpikeFind(pdfApp, candidate)
      assertCurrentRequest()
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
        assertCurrentRequest()
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
            nativeTarget: null,
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

    assertCurrentRequest()
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

    assertCurrentRequest()
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

    assertCurrentRequest()
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

    assertCurrentRequest()
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
      // Page-less evidence (PDF supplements, ABC main papers) opened its file but has
      // no page to fall back to; say so instead of pretending a page.
      curatorMessage: pageHints.length === 0 && activeFileName
        ? pageLessQuoteNotLocatedMessage(activeFileName)
        : null,
    }
  }, [
    activeDocument?.activeFileId,
    activeDocument?.documentId,
    activeFileName,
    ensureEvidencePageTextCorpus,
    getCachedEvidencePageText,
  ])

  const executeEvidenceSpike = useCallback(async (input: PdfEvidenceSpikeInput): Promise<PdfEvidenceSpikeResult> => {
    const result = await executeEvidenceNavigation(
      ({
        anchor: buildEvidenceSpikeAnchor(input),
        fileId: activeDocument?.activeFileId ?? null,
        textEvidence: { fragments: null, section: input.sectionTitle ?? null, subsection: null },
        searchText: input.quote?.trim() || null,
        pageNumber: input.pageNumber ?? null,
        sectionTitle: input.sectionTitle ?? null,
        mode: 'select',
      } as EvidenceNavigationCommand),
      {
        pageHints: input.pageNumbers,
      },
    )

    commitNavigationResult(result)
    publishEvidenceSpikeResult(result)
    return result
  }, [activeDocument?.activeFileId, commitNavigationResult, executeEvidenceNavigation])

  const updateViewerState = useCallback(
    (updates: Partial<ViewerState>) => {
      if (!activeDocument) return
      viewerStateRef.current = {
        ...viewerStateRef.current,
        ...updates,
        lastInteraction: new Date().toISOString(),
      }
      persistViewerSession(activeDocument, viewerStateRef.current)
    },
    [activeDocument, persistViewerSession],
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
          // pdf.js may emit the event before glyphs settle; delay the evidence overlay refresh.
          window.setTimeout(() => {
            setOverlayRenderKey((prev) => prev + 1)
          }, 50)
        }
      }

      const onDocumentLoaded = () => {
        debug.log('🔍 [PDF VIEWER DEBUG] Document loaded event fired - setting status to ready')
        setStatus('ready')
        const resolvedPageCount =
          pdfApp?.pdfDocument?.numPages ?? pdfApp?.pdfViewer?.pdfDocument?.numPages ?? null
        const resolvedCurrentPage = pdfApp?.pdfViewer?.currentPageNumber ?? pdfApp?.page ?? DEFAULT_STATE.currentPage
        const resolvedScale = pdfApp?.pdfViewer?.currentScale
        setViewerUiState((current) => ({
          ...current,
          currentPage: typeof resolvedCurrentPage === 'number'
            ? resolvedCurrentPage
            : current.currentPage,
          zoomLevel: typeof resolvedScale === 'number'
            ? Math.round(Math.max(10, Math.min(500, resolvedScale * 100)))
            : current.zoomLevel,
          searchCurrent: null,
          searchTotal: null,
          searchNotFound: false,
        }))
        if (typeof resolvedPageCount === 'number' && resolvedPageCount > 0) {
          // pdf.js knows the real page count (a stored count can be a placeholder).
          setActiveDocument((current) => {
            if (!current) {
              return current
            }
            const shown = activeViewerFile(current)
            return shown.page_count === resolvedPageCount
              ? current
              : {
                  ...current,
                  files: current.files.map((file) => (
                    file.file_id === shown.file_id ? { ...file, page_count: resolvedPageCount } : file
                  )),
                }
          })
        }
        signalLoadComplete()
        if (loadStartRef.current !== null) {
          const duration = performance.now() - loadStartRef.current
          setTelemetry({
            lastLoadMs: duration,
            slowLoad: duration > 3000,
          })
          loadStartRef.current = null
        }
        void ensureEvidencePageTextCorpus(pdfApp).catch((corpusError) => {
          console.warn('Unable to warm raw PDF.js evidence page text corpus', corpusError)
        })
        setOverlayRenderKey((prev) => prev + 1)
      }

      const onPageChanging = (event: any) => {
        if (typeof event.pageNumber === 'number') {
          updateViewerState({ currentPage: event.pageNumber })
          setViewerUiState((current) => ({
            ...current,
            currentPage: event.pageNumber,
          }))
          setOverlayRenderKey((prev) => prev + 1)
        }
      }

      const onTextLayerMatchesUpdated = (event: any) => {
        if (typeof event?.pageIndex === 'number' || typeof event?.pageNumber === 'number') {
          setOverlayRenderKey((prev) => prev + 1)
        }
      }

      const onScaleChanging = (event: any) => {
        if (typeof event.scale === 'number') {
          // event.scale is a decimal like 1.0 for 100%, 1.5 for 150%, etc.
          // Clamp to reasonable values (10% to 500%) to prevent extreme zoom bugs
          const newZoomLevel = Math.round(Math.max(10, Math.min(500, event.scale * 100)))
          updateViewerState({ zoomLevel: newZoomLevel })
          setViewerUiState((current) => ({
            ...current,
            zoomLevel: newZoomLevel,
          }))
          setOverlayRenderKey((prev) => prev + 1)
        }
      }

      const onFindMatchesCount = (event: any) => {
        if (event?.source !== pdfApp?.findController) {
          return
        }
        const activeSearchQuery = toolbarSearchQueryRef.current.trim()
        const controllerQuery = typeof event?.source?.state?.query === 'string'
          ? event.source.state.query.trim()
          : ''
        if (!activeSearchQuery || controllerQuery !== activeSearchQuery) {
          return
        }
        setViewerUiState((current) => ({
          ...current,
          searchCurrent: event?.matchesCount?.current ?? current.searchCurrent,
          searchTotal: event?.matchesCount?.total ?? current.searchTotal,
        }))
      }

      const onFindControlState = (event: any) => {
        if (event?.source !== pdfApp?.findController) {
          return
        }
        const rawQuery = typeof event?.rawQuery === 'string' ? event.rawQuery : ''
        if (!toolbarSearchQueryRef.current.trim() || rawQuery.trim() !== toolbarSearchQueryRef.current.trim()) {
          return
        }
        setViewerUiState((current) => ({
          ...current,
          searchCurrent: event?.matchesCount?.current ?? current.searchCurrent,
          searchTotal: event?.matchesCount?.total ?? current.searchTotal,
          searchNotFound: event?.state === PDFJS_FIND_STATE_NOT_FOUND,
        }))
      }

      eventBus.on('textlayerrendered', onTextLayerRendered)
      eventBus.on('documentloaded', onDocumentLoaded)
      eventBus.on('pagechanging', onPageChanging)
      eventBus.on('updatetextlayermatches', onTextLayerMatchesUpdated)
      eventBus.on('scalechanging', onScaleChanging)
      eventBus.on('updatefindmatchescount', onFindMatchesCount)
      eventBus.on('updatefindcontrolstate', onFindControlState)

      cleanupRefs.current.push(() => {
        eventBus.off('textlayerrendered', onTextLayerRendered)
        eventBus.off('documentloaded', onDocumentLoaded)
        eventBus.off('pagechanging', onPageChanging)
        eventBus.off('updatetextlayermatches', onTextLayerMatchesUpdated)
        eventBus.off('scalechanging', onScaleChanging)
        eventBus.off('updatefindmatchescount', onFindMatchesCount)
        eventBus.off('updatefindcontrolstate', onFindControlState)
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
    [ensureEvidencePageTextCorpus, updateViewerState, setOverlayRenderKey],
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

    const handshakeTimeout = window.setTimeout(() => {
      if (loadStartRef.current !== null) {
        setStatus('error')
        setError('Timed out waiting for the PDF viewer to initialise.')
        loadStartRef.current = null
        signalLoadError('Timed out waiting for the PDF viewer to initialise.')
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
      }
    }, 150)

    cleanupRefs.current.push(() => window.clearInterval(intervalId))
    cleanupRefs.current.push(() => window.clearTimeout(handshakeTimeout))
  }, [attachPdfEventListeners, signalLoadError])

  const beginDocumentLoad = useCallback((document: ViewerDocument) => {
    console.debug('[PDF DEBUG] beginDocumentLoad invoked', document)
    const showsPdf = isPdfFile(activeViewerFile(document))
    // A text file has no PDF to load: its pane loads its own text.
    loadStartRef.current = showsPdf ? performance.now() : null
    handledNavigationKeyRef.current = null
    navigationRequestIdRef.current += 1
    textNavigationRequestIdRef.current += 1
    setTextNavigation(null)
    // The previous file's PDF application must never answer for this one.
    pdfAppRef.current = null
    setStatus(showsPdf ? 'loading' : 'ready')
    setError(null)
    toolbarSearchQueryRef.current = ''
    setViewerUiState((current) => ({
      ...current,
      currentPage: viewerStateRef.current.currentPage,
      zoomLevel: viewerStateRef.current.zoomLevel,
      searchQuery: '',
      searchCurrent: null,
      searchTotal: null,
      searchNotFound: false,
    }))
    setTelemetry({
      lastLoadMs: null,
      slowLoad: false,
    })
    viewerSessionStorageUserIdRef.current = storageUserId
    setActiveDocument(document)
    setEvidenceHighlight(null)
    commitNavigationResult(null)
    setOverlayRenderKey((prev) => prev + 1)
    persistViewerSession(document, viewerStateRef.current)
  }, [commitNavigationResult, persistViewerSession, storageUserId])

  /**
   * Show another file of the paper. Page, scroll, corpus and highlight are per file.
   * A curator's own switch drops any evidence navigation still waiting for another
   * file; a switch made for evidence keeps it and is announced, without moving focus.
   */
  const switchActiveFile = useCallback((fileId: string, reason: 'curator' | 'evidence') => {
    if (!activeDocument || activeDocument.activeFileId === fileId) {
      return
    }
    const nextFile = activeDocument.files.find((file) => file.file_id === fileId)
    if (!nextFile) {
      throw new Error(`File ${fileId} is not one of the viewer's tabs`)
    }
    fileViewerStatesRef.current.set(activeDocument.activeFileId, { ...viewerStateRef.current })
    viewerStateRef.current = fileViewerStatesRef.current.get(fileId)
      ?? { ...DEFAULT_STATE, lastInteraction: new Date().toISOString() }
    cleanupRefs.current.forEach((cleanup) => cleanup())
    cleanupRefs.current = []
    toolbarSearchQueryRef.current = ''
    if (reason === 'curator') {
      if (effectivePendingNavigation) {
        handledNavigationKeyRef.current = buildNavigationCommandKey(effectivePendingNavigation)
        setEventPendingNavigation(null)
        if (pendingNavigation) {
          onNavigationComplete?.()
        }
      }
      setFileAnnouncement('')
    } else {
      setFileAnnouncement(openedFileAnnouncement(fileDisplayLabel(nextFile)))
    }
    const pendingKeyToKeep = reason === 'curator' ? handledNavigationKeyRef.current : null
    beginDocumentLoad({
      ...activeDocument,
      activeFileId: fileId,
      loadedAt: new Date().toISOString(),
    })
    if (pendingKeyToKeep !== null) {
      // beginDocumentLoad clears the handled key; a dropped command must stay dropped.
      handledNavigationKeyRef.current = pendingKeyToKeep
    }
  }, [activeDocument, beginDocumentLoad, effectivePendingNavigation, onNavigationComplete, pendingNavigation])

  const handleSelectFile = useCallback((fileId: string) => {
    switchActiveFile(fileId, 'curator')
  }, [switchActiveFile])

  useEffect(() => {
    if (activeDocumentOwnerRef.current === activeDocumentOwnerToken) {
      return
    }

    // Owner changes redefine who may drive the shared host next, but they should
    // not wipe the live PDF.js session on their own.
    activeDocumentOwnerRef.current = activeDocumentOwnerToken
  }, [activeDocumentOwnerToken])

  useEffect(() => {
    if (storageUserIdRef.current === storageUserId) {
      return
    }

    storageUserIdRef.current = storageUserId
    viewerSessionStorageUserIdRef.current = null
    handledNavigationKeyRef.current = null
    navigationRequestIdRef.current += 1
    viewerStateRef.current = {
      ...DEFAULT_STATE,
      lastInteraction: new Date().toISOString(),
    }
    toolbarSearchQueryRef.current = ''
    setActiveDocument(null)
    setStatus('idle')
    setError(null)
    setViewerUiState({
      currentPage: DEFAULT_STATE.currentPage,
      zoomLevel: DEFAULT_STATE.zoomLevel,
      searchQuery: '',
      searchCurrent: null,
      searchTotal: null,
      searchNotFound: false,
    })
    setTelemetry({
      lastLoadMs: null,
      slowLoad: false,
    })
    setEvidenceHighlight(null)
    commitNavigationResult(null)
  }, [commitNavigationResult, storageUserId])

  useEffect(() => {
    const unregisterDocument = onPDFDocumentChanged((event: PDFViewerDocumentChangedEvent) => {
      console.debug('[PDF DEBUG] pdf-viewer-document-changed event received', event.detail)
      if (
        activeDocumentOwnerToken
        && event.detail.ownerToken !== activeDocumentOwnerToken
      ) {
        console.debug('[PDF DEBUG] Ignoring document change for inactive owner', {
          activeDocumentOwnerToken,
          eventOwnerToken: event.detail.ownerToken ?? null,
        })
        return
      }

      let files: ViewerFile[]
      try {
        // Both addresses must be same-origin paths before the viewer uses them.
        files = event.detail.files.map((file) => ({
          ...file,
          content_url: file.content_url === null
            ? null
            : normalizePdfViewerDocumentUrl(file.content_url, window.location.origin),
          text_url: file.text_url === null
            ? null
            : normalizePdfViewerDocumentUrl(file.text_url, window.location.origin),
        }))
        if (!files.some((file) => file.file_id === event.detail.activeFileId)) {
          throw new Error('The document change names an active file that is not one of its files.')
        }
      } catch (error) {
        console.warn('Rejected an unsupported viewer document change', {
          documentId: event.detail.documentId,
          error,
        })
        resetViewerToIdle(error instanceof Error ? error.message : String(error))
        loadStartRef.current = null
        // A refused new paper is a failed document load Home is waiting on.
        documentLoadSignalPendingRef.current = true
        signalLoadError(error instanceof Error ? error.message : String(error))
        return
      }

      if (activeDocument && activeDocument.documentId === event.detail.documentId && status !== 'error') {
        // The same paper again (a re-render, or a file joined or left): keep the file
        // the curator is on when it is still there, and reload nothing.
        const current = activeViewerFile(activeDocument)
        const stillThere = files.find((file) => file.file_id === current.file_id)
        if (stillThere && stillThere.content_url === current.content_url && stillThere.text_url === current.text_url) {
          const unchanged = sameViewerFiles(activeDocument.files, files)
            && activeDocument.mainFileUnavailable === event.detail.mainFileUnavailable
            && sameIds(activeDocument.notReadyFileIds, event.detail.notReadyFileIds)
          if (!unchanged) {
            setActiveDocument({
              ...activeDocument,
              // Keep the page count pdf.js found for the file on screen.
              files: files.map((file) => (file.file_id === current.file_id ? { ...file, page_count: current.page_count } : file)),
              mainFileUnavailable: event.detail.mainFileUnavailable,
              notReadyFileIds: event.detail.notReadyFileIds,
            })
          }
          if (!isPdfFile(stillThere) && status === 'loading') {
            // A chat load marked the viewer loading; a text file has nothing to wait for.
            setStatus('ready')
          }
          return
        }
      }

      const nextDoc: ViewerDocument = {
        documentId: event.detail.documentId,
        files,
        activeFileId: event.detail.activeFileId,
        mainFileUnavailable: event.detail.mainFileUnavailable,
        notReadyFileIds: event.detail.notReadyFileIds,
        loadedAt: new Date().toISOString(),
      }
      console.debug('[PDF DEBUG] beginDocumentLoad called with', nextDoc)

      evidencePageTextCorporaRef.current = new Map()
      fileViewerStatesRef.current = new Map()
      setFileAnnouncement('')
      setIdleNotice(null)
      viewerStateRef.current = {
        ...DEFAULT_STATE,
        currentPage: event.detail.viewerState?.currentPage ?? DEFAULT_STATE.currentPage,
        scrollPosition: event.detail.viewerState?.scrollPosition ?? DEFAULT_STATE.scrollPosition,
        lastInteraction: new Date().toISOString(),
      }
      // Home's load overlay waits on a real new load whose first file is a PDF; a text
      // first file has no PDF load to signal, and later tabs never signal.
      documentLoadSignalPendingRef.current = isPdfFile(activeViewerFile(nextDoc))
      beginDocumentLoad(nextDoc)
    })

    // Listen for chat document changes (including unload)
    const handleChatDocumentChange = (event: Event) => {
      const customEvent = event as CustomEvent
      const detail = customEvent.detail || {}

      if (
        activeDocumentOwnerToken
        && detail.ownerToken
        && detail.ownerToken !== activeDocumentOwnerToken
      ) {
        console.debug('[PDF DEBUG] Ignoring chat document change for inactive owner', {
          activeDocumentOwnerToken,
          eventOwnerToken: detail.ownerToken,
        })
        return
      }

      // If document is being unloaded (active=false), clear the viewer
      if (!detail?.active || !detail.document) {
        console.debug('[PDF DEBUG] Document unloaded via chat-document-changed event')
        resetViewerToIdle()
      } else {
        // Document is being loaded - show loading state immediately
        console.debug('[PDF DEBUG] Document loading started via chat-document-changed event')
        setStatus('loading')
      }
    }
    window.addEventListener('chat-document-changed', handleChatDocumentChange)

    // The new chat document has nothing to show here (a text paper): end the load
    // started above and stop showing the previous PDF.
    const unregisterCleared = onPDFViewerDocumentCleared((event) => {
      if (
        activeDocumentOwnerToken
        && event.detail.ownerToken
        && event.detail.ownerToken !== activeDocumentOwnerToken
      ) {
        return
      }
      resetViewerToIdle()
      setIdleNotice(event.detail.notice ?? null)
    })

    return () => {
      unregisterDocument()
      unregisterCleared()
      window.removeEventListener('chat-document-changed', handleChatDocumentChange)
    }
  }, [
    activeDocument,
    activeDocumentOwnerToken,
    beginDocumentLoad,
    resetViewerToIdle,
    signalLoadComplete,
    signalLoadError,
    status,
  ])

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
    }
  }, [])

  // The latest handlers, read by the frame's listeners: a refreshed file list must
  // not re-run them (that would re-initialise pdf.js, reset the zoom and signal a
  // second load).
  const initialisePdfApplicationRef = useRef(initialisePdfApplication)
  initialisePdfApplicationRef.current = initialisePdfApplication
  const signalLoadErrorRef = useRef(signalLoadError)
  signalLoadErrorRef.current = signalLoadError
  const activeFileIsMainRef = useRef(false)
  activeFileIsMainRef.current = activeFile?.role === 'main'
  const frameKey = activeDocument
    ? `${activeDocument.documentId}::${activeDocument.activeFileId}::${activeDocument.loadedAt}`
    : null

  useEffect(() => {
    if (!iframeRef.current) {
      console.debug('[PDF DEBUG] iframeRef null in load listener effect')
      return
    }
    const iframe = iframeRef.current

    const handleLoad = () => {
      console.debug('[PDF DEBUG] iframe load event triggered', {
        iframeSrc: iframe?.src,
      })
      initialisePdfApplicationRef.current()
    }

    const handleError = () => {
      console.error('[PDF DEBUG] iframe error event triggered', {
        iframeSrc: iframe?.src,
      })
      setStatus('error')
      setError('Failed to load the PDF viewer frame.')
      loadStartRef.current = null
      signalLoadErrorRef.current('Failed to load the PDF viewer frame.')
    }

    iframe.addEventListener('load', handleLoad)
    iframe.addEventListener('error', handleError)

    if (frameKey && (iframe.contentWindow as any)?.PDFViewerApplication?.eventBus) {
      // Recover when the iframe finished loading before this effect reattached listeners.
      initialisePdfApplicationRef.current()
    }

    return () => {
      iframe.removeEventListener('load', handleLoad)
      iframe.removeEventListener('error', handleError)
    }
  }, [frameKey, viewerSrc])

  useEffect(() => {
    if (activeDocument) {
      clearDropState()
      debug.log('🔍 [PDF VIEWER DEBUG] Active document exists, persisting session:', activeDocument.documentId)
      persistViewerSession(activeDocument, viewerStateRef.current)
    } else {
      debug.log('🔍 [PDF VIEWER DEBUG] No active document, resetting to idle')
      handledNavigationKeyRef.current = null
      navigationRequestIdRef.current += 1
      if (viewerSessionStorageKey && viewerSessionStorageUserIdRef.current === storageUserId) {
        safeRemoveItem(() => window.localStorage, viewerSessionStorageKey, {
          owner: 'pdf-viewer',
          workflowCritical: true,
        })
      }
      const nextIdleError = idleResetErrorRef.current
      idleResetErrorRef.current = null
      setStatus(nextIdleError ? 'error' : 'idle')
      setError(nextIdleError)
      setTelemetry({
        lastLoadMs: null,
        slowLoad: false,
      })
      setEvidenceHighlight(null)
      commitNavigationResult(null)
    }
  }, [activeDocument?.documentId, clearDropState, commitNavigationResult, persistViewerSession, storageUserId, viewerSessionStorageKey])

  useEffect(() => {
    if (!activeDocument) {
      viewerSessionStorageUserIdRef.current = storageUserId
    }
  }, [activeDocument, storageUserId])

  useEffect(() => {
    if (!effectivePendingNavigation) {
      handledNavigationKeyRef.current = null
      return
    }

    if (!activeDocument) {
      return
    }

    const navigationKey = buildNavigationCommandKey(effectivePendingNavigation)
    if (handledNavigationKeyRef.current === navigationKey) {
      return
    }

    const usesEventNavigation =
      pendingNavigation === null && eventPendingNavigation === effectivePendingNavigation
    // Evidence the viewer cannot show changes nothing on screen but the banner:
    // no tab switch, no scroll, and the current highlight stays.
    const finishWithoutSearch = (result: PdfViewerNavigationResult | null) => {
      handledNavigationKeyRef.current = navigationKey
      if (result) {
        commitNavigationResult(result)
      }
      if (usesEventNavigation) {
        setEventPendingNavigation(null)
      }
      onNavigationComplete?.()
    }

    // The evidence's file decides first. A hover never opens or explains anything.
    const targetFileId = effectivePendingNavigation.fileId
    const isHover = effectivePendingNavigation.mode === 'hover'
    if (targetFileId === null) {
      finishWithoutSearch(isHover ? null : buildEvidenceFileResult(
        effectivePendingNavigation,
        activeDocument.documentId,
        'evidence-without-file',
      ))
      return
    }
    if (!activeDocument.files.some((file) => file.file_id === targetFileId)) {
      finishWithoutSearch(isHover ? null : buildEvidenceFileResult(
        effectivePendingNavigation,
        activeDocument.documentId,
        activeDocument.notReadyFileIds.includes(targetFileId) ? 'file-not-ready' : 'file-removed',
      ))
      return
    }
    if (targetFileId !== activeDocument.activeFileId) {
      if (isHover) {
        finishWithoutSearch(null)
        return
      }
      // Show the evidence's file first; this effect runs again once it is shown.
      switchActiveFile(targetFileId, 'evidence')
      return
    }
    if (!activeFileIsPdf) {
      // A text file: its pane locates the copied quote; never pdf.js or the fuzzy matcher.
      handledNavigationKeyRef.current = navigationKey
      textNavigationRequestIdRef.current += 1
      setTextNavigation({
        requestId: textNavigationRequestIdRef.current,
        fileId: targetFileId,
        command: effectivePendingNavigation,
      })
      return
    }
    if (status !== 'ready') {
      return
    }

    handledNavigationKeyRef.current = navigationKey
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
        if (isStaleEvidenceNavigationError(error)) {
          return
        }
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
    activeFileIsPdf,
    commitNavigationResult,
    effectivePendingNavigation,
    eventPendingNavigation,
    executeEvidenceNavigation,
    onNavigationComplete,
    pendingNavigation,
    status,
    switchActiveFile,
  ])

  /** The text pane located (or did not locate) the quote of the current text navigation. */
  const handleTextNavigationResult = useCallback((
    request: TextNavigationRequest,
    result: PdfViewerNavigationResult | null,
  ) => {
    if (request.requestId !== textNavigationRequestIdRef.current) {
      return
    }
    // A hover changes nothing in the header: it neither writes nor clears. The last
    // click's header stays until the next click or tab change.
    if (result !== null) {
      commitNavigationResult(result)
    }
    if (pendingNavigation === null && eventPendingNavigation === request.command) {
      setEventPendingNavigation(null)
    }
    onNavigationComplete?.()
  }, [commitNavigationResult, eventPendingNavigation, onNavigationComplete, pendingNavigation])

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

  useEffect(() => installPdfEvidenceDebugWindow(), [])

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
    const pdfApp = pdfAppRef.current ?? (iframeRef.current?.contentWindow as any)?.PDFViewerApplication ?? null

    if (
      !iframeDoc
      || !pdfApp?.eventBus
      || !pdfApp?.findController
      || !activeDocument
      || status !== 'ready'
      || !evidenceHighlight
      || evidenceHighlight.kind !== 'quote'
      || evidenceHighlight.renderOverlay
      || !evidenceHighlight.nativeTarget
    ) {
      return
    }

    const currentOccurrence = createPdfJsQuoteSearchAdapter(pdfApp, {
      getPageText: getCachedEvidencePageText,
    }).getSelectedOccurrence()
    const nativeRects = findPdfJsSelectedHighlightRects(iframeDoc, evidenceHighlight.pageNumber)
    const currentVerification = verifyNativePdfJsOccurrenceMatchesTarget(
      iframeDoc,
      pdfApp,
      currentOccurrence,
      evidenceHighlight.nativeTarget,
      {
        pageTextLookup: getCachedEvidencePageText,
        nativeRects,
      },
    )
    if (currentVerification.matched && nativeRects.length > 0) {
      return
    }

    let cancelled = false

    void synchronizeNativePdfJsQuoteHighlight(
      iframeDoc,
      pdfApp,
      evidenceHighlight.nativeTarget,
      {
        pageTextLookup: getCachedEvidencePageText,
        reason: 'quote-highlight-reconcile',
      },
    ).then((result) => {
      if (cancelled || !result.success) {
        return
      }
      setOverlayRenderKey((prev) => prev + 1)
    }).catch((error) => {
      if (cancelled) {
        return
      }
      console.warn('Unable to reconcile native PDF.js evidence highlight', error)
    })

    return () => {
      cancelled = true
    }
  }, [activeDocument?.documentId, evidenceHighlight, getCachedEvidencePageText, overlayRenderKey, status])

  useEffect(() => {
    const iframeDoc = iframeRef.current?.contentWindow?.document
    const cleanupNativeHighlights = () => {
      iframeDoc?.querySelectorAll<HTMLElement>('[data-pdf-evidence-native-highlight="true"]')
        .forEach((node) => {
          delete node.dataset.pdfEvidenceNativeHighlight
          delete node.dataset.anchorId
          delete node.dataset.mode
          delete node.dataset.kind
          node.removeAttribute('aria-label')
          node.removeAttribute('role')
          node.removeAttribute('tabindex')
          node.style.cursor = ''
          const clickHandler = (node as any).__pdfEvidenceClickHandler as EventListener | undefined
          const keydownHandler = (node as any).__pdfEvidenceKeydownHandler as EventListener | undefined
          if (clickHandler) {
            node.removeEventListener('click', clickHandler)
            delete (node as any).__pdfEvidenceClickHandler
          }
          if (keydownHandler) {
            node.removeEventListener('keydown', keydownHandler)
            delete (node as any).__pdfEvidenceKeydownHandler
          }
        })
    }

    cleanupNativeHighlights()

    if (
      !iframeDoc
      || !activeDocument
      || status !== 'ready'
      || !evidenceHighlight
      || evidenceHighlight.kind !== 'quote'
      || evidenceHighlight.renderOverlay
    ) {
      return
    }

    const textLayer = getPageTextLayer(iframeDoc, evidenceHighlight.pageNumber)
    const selectedHighlights = Array.from(
      textLayer?.querySelectorAll<HTMLElement>('.highlight.selected') ?? [],
    )
    if (selectedHighlights.length === 0) {
      return cleanupNativeHighlights
    }

    const handleAnchorSelection = () => {
      dispatchPDFViewerEvidenceAnchorSelected(
        evidenceHighlight.anchorId,
        activeDocument.documentId,
        activeDocumentOwnerToken ?? null,
      )
    }
    const primaryHighlightNode = selectedHighlights[0] ?? null

    selectedHighlights.forEach((node) => {
      node.dataset.pdfEvidenceNativeHighlight = 'true'
      node.dataset.anchorId = evidenceHighlight.anchorId
      node.dataset.mode = evidenceHighlight.mode
      node.dataset.kind = evidenceHighlight.kind
      node.style.cursor = 'pointer'

      node.addEventListener('click', handleAnchorSelection)
      ;(node as any).__pdfEvidenceClickHandler = handleAnchorSelection

      if (node !== primaryHighlightNode) {
        return
      }

      node.setAttribute('role', 'button')
      node.setAttribute('tabindex', '0')
      node.setAttribute('aria-label', 'Jump to linked annotation field')

      const handleKeyDown = (event: Event) => {
        const keyboardEvent = event as KeyboardEvent
        if (keyboardEvent.key !== 'Enter' && keyboardEvent.key !== ' ') {
          return
        }

        keyboardEvent.preventDefault()
        handleAnchorSelection()
      }

      node.addEventListener('keydown', handleKeyDown)
      ;(node as any).__pdfEvidenceKeydownHandler = handleKeyDown
    })

    return cleanupNativeHighlights
  }, [
    activeDocument?.documentId,
    activeDocumentOwnerToken,
    evidenceHighlight,
    overlayRenderKey,
    status,
  ])

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

    const rectStyles = getEvidenceHighlightRectStyles(evidenceHighlight, theme)
    const rectCleanupFns: Array<() => void> = []
    const handleAnchorSelection = () => {
      dispatchPDFViewerEvidenceAnchorSelected(
        evidenceHighlight.anchorId,
        activeDocument.documentId,
        activeDocumentOwnerToken ?? null,
      )
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
  }, [
    activeDocument?.documentId,
    activeDocumentOwnerToken,
    evidenceHighlight,
    overlayRenderKey,
    status,
    theme,
  ])

  useEffect(() => {
    if (!frameKey || !activeContentUrl) return

    let cancelled = false
    const controller = new AbortController()
    const timeoutId = window.setTimeout(() => controller.abort(), 8000)

    const verifyDocument = async () => {
      try {
        const response = await fetch(activeContentUrl, {
          method: 'HEAD',
          signal: controller.signal,
          credentials: 'same-origin',
        })
        if (!response.ok) {
          throw new Error(`Unexpected status ${response.status}`)
        }
      } catch (fetchError) {
        if (!cancelled) {
          // A supplement cannot be re-uploaded by the curator; its pane says only that it could not be reached.
          const message = activeFileIsMainRef.current ? MAIN_FILE_UNREACHABLE_MESSAGE : SUPPLEMENT_UNREACHABLE_MESSAGE
          setStatus('error')
          setError(message)
          loadStartRef.current = null
          signalLoadErrorRef.current(message)
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
  }, [activeContentUrl, frameKey])

  const handleRetry = useCallback(() => {
    if (!activeDocument) return
    const refreshedDocument: ViewerDocument = {
      ...activeDocument,
      loadedAt: new Date().toISOString(),
    }
    setRetryKey((key) => key + 1)
    beginDocumentLoad(refreshedDocument)
  }, [activeDocument, beginDocumentLoad])

  const getPdfApplication = useCallback(() => {
    return pdfAppRef.current ?? (iframeRef.current?.contentWindow as any)?.PDFViewerApplication ?? null
  }, [])

  const handlePreviousPage = useCallback(() => {
    const pdfViewer = getPdfApplication()?.pdfViewer
    if (!pdfViewer) return
    if (typeof pdfViewer.previousPage === 'function') {
      pdfViewer.previousPage()
      return
    }
    if (typeof pdfViewer.currentPageNumber === 'number') {
      pdfViewer.currentPageNumber = Math.max(1, pdfViewer.currentPageNumber - 1)
    }
  }, [getPdfApplication])

  const handleNextPage = useCallback(() => {
    const pdfViewer = getPdfApplication()?.pdfViewer
    if (!pdfViewer) return
    if (typeof pdfViewer.nextPage === 'function') {
      pdfViewer.nextPage()
      return
    }
    if (typeof pdfViewer.currentPageNumber === 'number') {
      const pageCount = activeFilePageCount ?? pdfViewer.pagesCount ?? 1
      pdfViewer.currentPageNumber = Math.min(pageCount, pdfViewer.currentPageNumber + 1)
    }
  }, [activeFilePageCount, getPdfApplication])

  const handleZoomOut = useCallback(() => {
    const pdfViewer = getPdfApplication()?.pdfViewer
    if (!pdfViewer) return
    if (typeof pdfViewer.decreaseScale === 'function') {
      pdfViewer.decreaseScale()
      return
    }
    if (typeof pdfViewer.currentScale === 'number') {
      pdfViewer.currentScale = Math.max(0.1, pdfViewer.currentScale * 0.9)
    }
  }, [getPdfApplication])

  const handleZoomIn = useCallback(() => {
    const pdfViewer = getPdfApplication()?.pdfViewer
    if (!pdfViewer) return
    if (typeof pdfViewer.increaseScale === 'function') {
      pdfViewer.increaseScale()
      return
    }
    if (typeof pdfViewer.currentScale === 'number') {
      pdfViewer.currentScale = Math.min(5, pdfViewer.currentScale * 1.1)
    }
  }, [getPdfApplication])

  const handleZoomAuto = useCallback(() => {
    const pdfViewer = getPdfApplication()?.pdfViewer
    if (!pdfViewer) return
    pdfViewer.currentScaleValue = 'auto'
  }, [getPdfApplication])

  const dispatchPdfSearch = useCallback((query: string, options?: { findPrevious?: boolean; type?: string }) => {
    const pdfApp = getPdfApplication()
    const trimmedQuery = query.trim()
    if (!pdfApp?.eventBus) return

    if (!trimmedQuery) {
      toolbarSearchQueryRef.current = ''
      pdfApp.eventBus.dispatch('findbarclose', {
        source: 'pdf-viewer-toolbar',
      })
      setViewerUiState((current) => ({
        ...current,
        searchCurrent: null,
        searchTotal: null,
        searchNotFound: false,
      }))
      return
    }

    toolbarSearchQueryRef.current = trimmedQuery
    navigationRequestIdRef.current += 1
    setEvidenceHighlight(null)
    commitNavigationResult(null)
    pdfApp.eventBus.dispatch('find', {
      source: 'pdf-viewer-toolbar',
      type: options?.type ?? '',
      query: trimmedQuery,
      caseSensitive: false,
      entireWord: false,
      highlightAll: true,
      findPrevious: options?.findPrevious ?? false,
      matchDiacritics: false,
    })
  }, [commitNavigationResult, getPdfApplication])

  const handleSearchQueryChange = useCallback((query: string) => {
    setViewerUiState((current) => ({
      ...current,
      searchQuery: query,
      searchCurrent: null,
      searchTotal: null,
      searchNotFound: false,
    }))
    dispatchPdfSearch(query)
  }, [dispatchPdfSearch])

  const handleSearchNext = useCallback(() => {
    dispatchPdfSearch(viewerUiState.searchQuery, { type: 'again', findPrevious: false })
  }, [dispatchPdfSearch, viewerUiState.searchQuery])

  const handleSearchPrevious = useCallback(() => {
    dispatchPdfSearch(viewerUiState.searchQuery, { type: 'again', findPrevious: true })
  }, [dispatchPdfSearch, viewerUiState.searchQuery])

  const handleDismissNavigationResult = useCallback(() => {
    commitNavigationResult(null)
  }, [commitNavigationResult])

  const navigationBannerMessage = navigationResult
    ? getNavigationBannerMessage(navigationResult, evidenceHighlight)
    : null

  return (
    <PdfViewerChrome
      activeDocument={activeDocument}
      activeFile={activeFile}
      fileAnnouncement={fileAnnouncement}
      idleNotice={idleNotice}
      onSelectFile={handleSelectFile}
      textNavigation={textNavigation}
      onTextNavigationResult={handleTextNavigationResult}
      onDismissNavigationResult={handleDismissNavigationResult}
      status={status}
      error={error}
      retryKey={retryKey}
      viewerSrc={viewerSrc}
      iframeRef={iframeRef}
      navigationResult={navigationResult}
      navigationBannerMessage={navigationBannerMessage}
      dragActive={dragActive}
      uploadInFlight={uploadInFlight}
      dropError={dropError}
      uploadDialog={uploadDialog}
      currentPage={viewerUiState.currentPage}
      zoomLevel={viewerUiState.zoomLevel}
      searchQuery={viewerUiState.searchQuery}
      searchCurrent={viewerUiState.searchCurrent}
      searchTotal={viewerUiState.searchTotal}
      searchNotFound={viewerUiState.searchNotFound}
      variant={variant}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      onRetry={handleRetry}
      onCloseUploadDialog={handleCloseUploadDialog}
      onPreviousPage={handlePreviousPage}
      onNextPage={handleNextPage}
      onZoomOut={handleZoomOut}
      onZoomIn={handleZoomIn}
      onZoomAuto={handleZoomAuto}
      onSearchQueryChange={handleSearchQueryChange}
      onSearchNext={handleSearchNext}
      onSearchPrevious={handleSearchPrevious}
    />
  )
}

export default PdfViewer
