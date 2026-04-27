import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTheme } from '@mui/material/styles'
import { debug, getEnvFlag } from '@/utils/env'
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
  buildDefaultHighlightSettings,
  loadStoredHighlightSettings,
  type HighlightSettings,
} from '@/components/pdfViewer/highlightSettings'
import { normalizePdfViewerDocumentUrl } from '@/components/pdfViewer/viewerDocumentUrl'
import type { EvidenceNavigationCommand } from '@/features/curation/evidence'
import {
  fuzzyMatchPdfEvidenceQuote,
  type PdfEvidenceFuzzyMatchPage,
  type PdfEvidenceFuzzyMatchResult,
} from '@/features/curation/services/pdfEvidenceMatcherService'
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
import {
  ensureMarkInjected,
  getTextLayers,
  persistSession,
  uniqueTerms,
} from './pdfViewerHighlighting'
import {
  DEFAULT_STATE,
  VIEWER_BASE_PATH,
  type PdfViewerProps,
  type ViewerDocument,
  type ViewerState,
  type ViewerStatus,
  type ViewerTelemetry,
} from './pdfViewerTypes'
import { usePdfViewerUpload } from './usePdfViewerUpload'

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

export function PdfViewer({
  activeDocumentOwnerToken,
  storageUserId = null,
  pendingNavigation = null,
  onNavigationComplete,
  onNavigationStateChange,
}: PdfViewerProps) {
  const theme = useTheme()
  const defaultHighlightColor = theme.palette.success.main
  const defaultHighlightSettings = useMemo(
    () => buildDefaultHighlightSettings(defaultHighlightColor),
    [defaultHighlightColor],
  )
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const pdfAppRef = useRef<any>(null)
  const cleanupRefs = useRef<(() => void)[]>([])
  const highlightTermsRef = useRef<string[]>([])
  const settingsRef = useRef<HighlightSettings>(defaultHighlightSettings)
  const viewerStateRef = useRef<ViewerState>({ ...DEFAULT_STATE })
  const loadStartRef = useRef<number | null>(null)
  const handledNavigationKeyRef = useRef<string | null>(null)
  const navigationRequestIdRef = useRef(0)
  const evidencePageTextCorpusRef = useRef<PdfEvidencePageTextCorpusCache>({
    cacheKey: null,
    pages: null,
    promise: null,
  })

  const [status, setStatus] = useState<ViewerStatus>('idle')
  const [activeDocument, setActiveDocument] = useState<ViewerDocument | null>(null)
  const [highlightTerms, setHighlightTerms] = useState<string[]>([])
  const [_highlightSettings, setHighlightSettings] = useState<HighlightSettings>(defaultHighlightSettings)
  const [error, setError] = useState<string | null>(null)
  const [retryKey, setRetryKey] = useState(0)
  const [_telemetry, setTelemetry] = useState<ViewerTelemetry>({
    lastLoadMs: null,
    lastHighlightMs: null,
    slowLoad: false,
    slowHighlight: false,
  })
  const [overlayRenderKey, setOverlayRenderKey] = useState(0)
  const [navigationResult, setNavigationResult] = useState<PdfViewerNavigationResult | null>(null)
  const [evidenceHighlight, setEvidenceHighlight] = useState<EvidenceTextLayerHighlight | null>(null)
  const [eventPendingNavigation, setEventPendingNavigation] =
    useState<EvidenceNavigationCommand | null>(null)
  const effectivePendingNavigation = pendingNavigation ?? eventPendingNavigation
  const viewerSessionStorageKey = useMemo(
    () => (storageUserId ? getChatLocalStorageKeys(storageUserId).pdfViewerSession : null),
    [storageUserId],
  )
  const evidencePageTextCacheKey = activeDocument
    ? `${activeDocument.documentId}:${activeDocument.loadedAt}`
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
    viewerStateRef.current = {
      ...DEFAULT_STATE,
      lastInteraction: new Date().toISOString(),
    }
    highlightTermsRef.current = []
    idleResetErrorRef.current = nextError
    setActiveDocument(null)
    setStatus(nextError ? 'error' : 'idle')
    setError(nextError)
    setHighlightTerms([])
    setEvidenceHighlight(null)
    commitNavigationResult(null)
    if (viewerSessionStorageKey) {
      localStorage.removeItem(viewerSessionStorageKey)
    }
  }, [commitNavigationResult, viewerSessionStorageKey])

  /**
   * Signal that document loading is complete (whether success or failure).
   * Clears the sessionStorage flag and dispatches the event to dismiss the loading overlay.
   */
  const signalLoadComplete = useCallback(() => {
    sessionStorage.removeItem('document-loading')
    window.dispatchEvent(new CustomEvent('document-load-complete'))
  }, [])

  useEffect(() => {
    evidencePageTextCorpusRef.current = {
      cacheKey: evidencePageTextCacheKey,
      pages: null,
      promise: null,
    }
  }, [evidencePageTextCacheKey])

  const getNativeEvidencePageText = useCallback((pdfApp: any, pageNumber: number): string | null => {
    const pageContents = pdfApp?.findController?._pageContents?.[pageNumber - 1]
    return typeof pageContents === 'string' ? pageContents : null
  }, [])

  const getNativeEvidencePageCorpus = useCallback((pdfApp: any): PdfEvidenceFuzzyMatchPage[] | null => {
    const pageCount = pdfApp?.pdfDocument?.numPages
      ?? pdfApp?.pdfViewer?.pdfDocument?.numPages
      ?? activeDocument?.pageCount
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
  }, [activeDocument?.pageCount, getNativeEvidencePageText])

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
    const cache = evidencePageTextCorpusRef.current
    if (cache.cacheKey !== cacheKey) {
      evidencePageTextCorpusRef.current = {
        cacheKey,
        pages: null,
        promise: null,
      }
    }

    const currentCache = evidencePageTextCorpusRef.current
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
          ?? activeDocument?.pageCount
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
        if (evidencePageTextCorpusRef.current.cacheKey === cacheKey) {
          evidencePageTextCorpusRef.current.pages = pages
          evidencePageTextCorpusRef.current.promise = null
        }
        return pages
      })().catch((error) => {
        if (evidencePageTextCorpusRef.current.cacheKey === cacheKey) {
          evidencePageTextCorpusRef.current.promise = null
        }
        throw error
      })
    }

    return currentCache.promise
  }, [activeDocument?.pageCount, evidencePageTextCacheKey, extractEvidencePageText, getNativeEvidencePageCorpus])

  const getCachedEvidencePageText = useCallback((pageNumber: number): string | null => {
    const cache = evidencePageTextCorpusRef.current
    if (cache.cacheKey !== evidencePageTextCacheKey || !cache.pages) {
      return null
    }

    return cache.pages.find((page) => page.pageNumber === pageNumber)?.text ?? null
  }, [evidencePageTextCacheKey])

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
    }
  }, [
    activeDocument?.documentId,
    activeDocument?.pageCount,
    ensureEvidencePageTextCorpus,
    getCachedEvidencePageText,
  ])

  const executeEvidenceSpike = useCallback(async (input: PdfEvidenceSpikeInput): Promise<PdfEvidenceSpikeResult> => {
    const result = await executeEvidenceNavigation(
      ({
        anchor: buildEvidenceSpikeAnchor(input),
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
        void ensureEvidencePageTextCorpus(pdfApp).catch((corpusError) => {
          console.warn('Unable to warm raw PDF.js evidence page text corpus', corpusError)
        })
        setOverlayRenderKey((prev) => prev + 1)
      }

      const onPageChanging = (event: any) => {
        if (typeof event.pageNumber === 'number') {
          updateViewerState({ currentPage: event.pageNumber })
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
          setOverlayRenderKey((prev) => prev + 1)
        }
      }

      eventBus.on('textlayerrendered', onTextLayerRendered)
      eventBus.on('documentloaded', onDocumentLoaded)
      eventBus.on('pagechanging', onPageChanging)
      eventBus.on('updatetextlayermatches', onTextLayerMatchesUpdated)
      eventBus.on('scalechanging', onScaleChanging)

      cleanupRefs.current.push(() => {
        eventBus.off('textlayerrendered', onTextLayerRendered)
        eventBus.off('documentloaded', onDocumentLoaded)
        eventBus.off('pagechanging', onPageChanging)
        eventBus.off('updatetextlayermatches', onTextLayerMatchesUpdated)
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
    [applyHighlights, ensureEvidencePageTextCorpus, updateViewerState, setOverlayRenderKey],
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
    navigationRequestIdRef.current += 1
    setStatus('loading')
    setError(null)
    setTelemetry((prev) => ({
      ...prev,
      lastLoadMs: null,
      slowLoad: false,
      lastHighlightMs: null,
      slowHighlight: false,
    }))
    viewerSessionStorageUserIdRef.current = storageUserId
    setActiveDocument(document)
    setEvidenceHighlight(null)
    commitNavigationResult(null)
    setOverlayRenderKey((prev) => prev + 1)
    persistViewerSession(document, viewerStateRef.current)
  }, [commitNavigationResult, persistViewerSession, storageUserId])

  useEffect(() => {
    const storedSettings = loadStoredHighlightSettings(defaultHighlightSettings)
    settingsRef.current = storedSettings
    setHighlightSettings(storedSettings)
    if (iframeRef.current?.contentWindow?.document) {
      ensureMarkInjected(iframeRef.current.contentWindow.document, storedSettings)
      if (highlightTermsRef.current.length) {
        applyHighlights()
      }
    }

    // DO NOT auto-load stored session on mount
    // The PDF viewer is passive and only loads when it receives a 'pdf-viewer-document-changed' event
    // This event is dispatched by:
    // 1. DocumentsPage when user selects a document
    // 2. Chat component on mount if backend has an active document (preserves doc across refreshes)
  }, [applyHighlights, defaultHighlightSettings])

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
    highlightTermsRef.current = []
    setHighlightTerms([])
    setActiveDocument(null)
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

      let normalizedViewerUrl: string
      try {
        normalizedViewerUrl = normalizePdfViewerDocumentUrl(
          event.detail.viewerUrl,
          window.location.origin,
        )
      } catch (error) {
        console.warn('Rejected unsupported PDF document URL', {
          viewerUrl: event.detail.viewerUrl,
          error,
        })
        resetViewerToIdle(error instanceof Error ? error.message : String(error))
        loadStartRef.current = null
        signalLoadComplete()
        return
      }

      const sameLoadedDocument = activeDocument
        && activeDocument.documentId === event.detail.documentId
        && activeDocument.viewerUrl === normalizedViewerUrl
        && activeDocument.pageCount === event.detail.pageCount
        && activeDocument.filename === event.detail.filename

      if (sameLoadedDocument && status !== 'error') {
        console.debug('[PDF DEBUG] Ignoring redundant document change for already loaded document', {
          documentId: event.detail.documentId,
          ownerToken: event.detail.ownerToken ?? null,
        })
        return
      }

      const nextDoc: ViewerDocument = {
        documentId: event.detail.documentId,
        viewerUrl: normalizedViewerUrl,
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

    return () => {
      unregisterDocument()
      unregisterHighlights()
      unregisterClear()
      unregisterSettings()
      window.removeEventListener('chat-document-changed', handleChatDocumentChange)
    }
  }, [
    activeDocument,
    activeDocumentOwnerToken,
    applyHighlights,
    beginDocumentLoad,
    clearAllHighlights,
    resetViewerToIdle,
    signalLoadComplete,
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
      clearDropState()
      debug.log('🔍 [PDF VIEWER DEBUG] Active document exists, persisting session:', activeDocument.documentId)
      persistViewerSession(activeDocument, viewerStateRef.current)
    } else {
      debug.log('🔍 [PDF VIEWER DEBUG] No active document, resetting to idle')
      handledNavigationKeyRef.current = null
      navigationRequestIdRef.current += 1
      if (viewerSessionStorageKey && viewerSessionStorageUserIdRef.current === storageUserId) {
        localStorage.removeItem(viewerSessionStorageKey)
      }
      const nextIdleError = idleResetErrorRef.current
      idleResetErrorRef.current = null
      setStatus(nextIdleError ? 'error' : 'idle')
      setError(nextIdleError)
      setTelemetry({
        lastLoadMs: null,
        lastHighlightMs: null,
        slowLoad: false,
        slowHighlight: false,
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
      dispatchPDFViewerEvidenceAnchorSelected(evidenceHighlight.anchorId)
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
  }, [activeDocument?.documentId, evidenceHighlight, overlayRenderKey, status])

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
  }, [activeDocument?.documentId, evidenceHighlight, overlayRenderKey, status, theme])

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

  const handleRetry = useCallback(() => {
    if (!activeDocument) return
    const refreshedDocument: ViewerDocument = {
      ...activeDocument,
      loadedAt: new Date().toISOString(),
    }
    setRetryKey((key) => key + 1)
    beginDocumentLoad(refreshedDocument)
  }, [activeDocument, beginDocumentLoad])

  const navigationBannerMessage = navigationResult
    ? getNavigationBannerMessage(navigationResult, evidenceHighlight)
    : null

  return (
    <PdfViewerChrome
      activeDocument={activeDocument}
      status={status}
      error={error}
      retryKey={retryKey}
      viewerSrc={viewerSrc}
      iframeRef={iframeRef}
      highlightTerms={highlightTerms}
      navigationResult={navigationResult}
      navigationBannerMessage={navigationBannerMessage}
      dragActive={dragActive}
      uploadInFlight={uploadInFlight}
      dropError={dropError}
      uploadDialog={uploadDialog}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      onRetry={handleRetry}
      onCloseUploadDialog={handleCloseUploadDialog}
    />
  )
}

export default PdfViewer
