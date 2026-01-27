import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { debug } from '@/utils/env'
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

const VIEWER_BASE_PATH = '/pdfjs/web/viewer.html'
const SESSION_STORAGE_KEY = 'pdf-viewer-session'
const SETTINGS_STORAGE_KEY = 'pdf-viewer-settings'

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

interface OverlayDocItem {
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

interface OverlayPayload {
  chunkId: string
  documentId?: string | null
  docItems: OverlayDocItem[]
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

export function PdfViewer() {
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const pdfAppRef = useRef<any>(null)
  const cleanupRefs = useRef<(() => void)[]>([])
  const highlightTermsRef = useRef<string[]>([])
  const settingsRef = useRef<HighlightSettings>(DEFAULT_SETTINGS)
  const viewerStateRef = useRef<ViewerState>({ ...DEFAULT_STATE })
  const loadStartRef = useRef<number | null>(null)

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
  const [overlays, setOverlays] = useState<OverlayPayload[]>([])
  const [overlayRenderKey, setOverlayRenderKey] = useState(0)

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

      if (typeof detail.chunkId !== 'string' || detail.chunkId.trim().length === 0) {
        debug.log('🔍 [PDF VIEWER DEBUG] Invalid chunk ID, skipping:', detail.chunkId)
        return
      }

      debug.log('🔍 [PDF VIEWER DEBUG] Processing doc items for normalization:', {
        rawCount: detail.docItems?.length || 0,
        firstThreeItems: detail.docItems?.slice(0, 3)
      })

      const normalizedDocItems: OverlayDocItem[] = Array.isArray(detail.docItems)
        ? (detail.docItems
            .map((item, idx) => {
              const pageValue = typeof item.page === 'number' ? item.page : typeof item.page_no === 'number' ? item.page_no : undefined
              const hasBbox = !!item.bbox

              if (!hasBbox || typeof pageValue !== 'number') {
                debug.log(`🔍 [PDF VIEWER DEBUG] Skipping item ${idx}:`, {
                  hasBbox,
                  pageValue,
                  item
                })
                return null
              }

              debug.log(`🔍 [PDF VIEWER DEBUG] Normalized item ${idx}:`, {
                page: pageValue,
                bbox: item.bbox,
                label: item.doc_item_label
              })

              return {
                ...item,
                page: pageValue,
              } as OverlayDocItem
            })
            .filter((item): item is OverlayDocItem => item !== null))
        : []

      debug.log('🔍 [PDF VIEWER DEBUG] Normalization complete:', {
        inputCount: detail.docItems?.length || 0,
        outputCount: normalizedDocItems.length,
        normalizedItems: normalizedDocItems.slice(0, 3) // First 3 for brevity
      })

      if (normalizedDocItems.length === 0) {
        debug.log('🔍 [PDF VIEWER DEBUG] No valid doc items after normalization, skipping')
        return
      }

      setOverlays((prev) => {
        const filtered = prev.filter((entry) => entry.chunkId !== detail.chunkId)
        const next = [
          ...filtered,
          {
            chunkId: detail.chunkId,
            documentId: detail.documentId ?? activeDocument?.documentId ?? null,
            docItems: normalizedDocItems,
          },
        ]
        const finalOverlays = next.slice(-5)

        debug.log('🔍 [PDF VIEWER DEBUG] Updated overlays state:', {
          previousCount: prev.length,
          newCount: finalOverlays.length,
          chunkIds: finalOverlays.map(o => o.chunkId),
          totalDocItems: finalOverlays.reduce((sum, o) => sum + o.docItems.length, 0)
        })

        return finalOverlays
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
  }, [activeDocument?.documentId])

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
    setOverlayRenderKey((prev) => prev + 1)
    persistSession(document, viewerStateRef.current)
  }, [])

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
        setActiveDocument(null)
        setStatus('idle')
        setError(null)
        highlightTermsRef.current = []
        setHighlightTerms([])
        setOverlays([])
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
  }, [applyHighlights, beginDocumentLoad, clearAllHighlights])

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
    }

    iframe.addEventListener('load', handleLoad)
    iframe.addEventListener('error', handleError)
    return () => {
      iframe.removeEventListener('load', handleLoad)
      iframe.removeEventListener('error', handleError)
    }
  }, [initialisePdfApplication, viewerSrc])

  useEffect(() => {
    if (activeDocument) {
      debug.log('🔍 [PDF VIEWER DEBUG] Active document exists, persisting session:', activeDocument.documentId)
      persistSession(activeDocument, viewerStateRef.current)
    } else {
      debug.log('🔍 [PDF VIEWER DEBUG] No active document, resetting to idle')
      localStorage.removeItem(SESSION_STORAGE_KEY)
      setStatus('idle')
      setError(null)
      setTelemetry({
        lastLoadMs: null,
        lastHighlightMs: null,
        slowLoad: false,
        slowHighlight: false,
      })
    }
  }, [activeDocument?.documentId])

  useEffect(() => {
    if (status === 'ready' && highlightTermsRef.current.length) {
      applyHighlights()
    }
  }, [status, applyHighlights])

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
          debug.log(`🔍 [PDF OVERLAY RENDER] Skipping item ${itemIdx} - missing data:`, {
            hasPageNumber: !!pageNumber,
            hasBbox: !!bbox,
            item
          })
          skippedItemCount++
          return
        }

        const left = Number(bbox.left)
        const top = Number(bbox.top)
        const right = Number(bbox.right)
        const bottom = Number(bbox.bottom)

        if (!Number.isFinite(left) || !Number.isFinite(top) || !Number.isFinite(right) || !Number.isFinite(bottom)) {
          debug.log(`🔍 [PDF OVERLAY RENDER] Skipping item ${itemIdx} - invalid bbox coordinates:`, {
            left, top, right, bottom
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
  }, [overlays, status, overlayRenderKey])

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
  }, [activeDocument])

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
            {highlightTerms.length > 0 && (
              <Stack direction="row" spacing={1} flexWrap="wrap">
                {highlightTerms.map((term) => (
                  <Chip key={term} size="small" label={term} color="secondary" sx={{ marginTop: 0.5 }} />
                ))}
              </Stack>
            )}
          </Stack>
        ) : (
          <Typography variant="h6">Select a document to load the PDF viewer</Typography>
        )}
      </Box>

      <Box sx={{ flex: 1, position: 'relative' }}>
        {!activeDocument && (
          <Box
            sx={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'text.secondary',
            }}
          >
            <Typography variant="body1">PDF preview will appear here.</Typography>
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
    </Paper>
  )
}

export default PdfViewer
