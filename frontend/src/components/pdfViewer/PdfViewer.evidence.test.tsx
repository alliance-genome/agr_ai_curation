import { afterEach, describe, expect, it, vi } from 'vitest'

import { fireEvent, render, screen, waitFor } from '../../test/test-utils'
import type { EvidenceNavigationCommand } from '@/features/curation/evidence'
import PdfViewer, {
  buildEvidenceSpikeQuoteCandidates,
  buildEvidenceSpikeSectionCandidates,
  findExpandedEvidenceQueryFromPageText,
  normalizeEvidenceSpikePageHints,
  type PdfEvidenceSpikeInput,
} from './PdfViewer'
import {
  dispatchPDFViewerNavigateEvidence,
  dispatchPDFDocumentChanged,
  onPDFViewerEvidenceAnchorSelected,
} from './pdfEvents'

interface MockFindResponse {
  state: number
  total: number
  current: number
  pageIdx: number | null
  viewerPageNumber?: number | null
  delayedSelection?: {
    pageIdx?: number | null
    viewerPageNumber?: number | null
    delayMs?: number
  }
  lateCount?: {
    total: number
    current: number
    delayMs?: number
  }
}

interface MockPageSpec {
  pageNumber: number
  textSegments: string[]
}

class MockPdfEventBus {
  listeners = new Map<string, Set<(event: any) => void>>()
  findQueries: string[] = []
  findDispatches: Array<{ query: string; pageBeforeDispatch: number }> = []
  findbarCloseCount = 0

  constructor(
    private readonly onFind: (query: string) => MockFindResponse,
    private readonly getCurrentPage: () => number,
    private readonly setCurrentPage: (pageNumber: number) => void,
    private readonly findController: { selected: { pageIdx: number; matchIdx: number } },
  ) {}

  on(eventName: string, handler: (event: any) => void) {
    const handlers = this.listeners.get(eventName) ?? new Set()
    handlers.add(handler)
    this.listeners.set(eventName, handlers)
  }

  off(eventName: string, handler: (event: any) => void) {
    this.listeners.get(eventName)?.delete(handler)
  }

  dispatch(eventName: string, payload: any) {
    if (eventName === 'findbarclose') {
      this.findbarCloseCount += 1
      this.findController.selected.pageIdx = -1
      this.findController.selected.matchIdx = -1
      return
    }

    if (eventName !== 'find') {
      return
    }

    this.findDispatches.push({
      query: payload.query,
      pageBeforeDispatch: this.getCurrentPage(),
    })

    const response = this.onFind(payload.query)
    this.findQueries.push(payload.query)

    const applySelection = (selection: {
      pageIdx?: number | null
      viewerPageNumber?: number | null
    }) => {
      const pageIdx = selection.pageIdx ?? null
      this.findController.selected.pageIdx = pageIdx ?? -1
      this.findController.selected.matchIdx = pageIdx !== null ? Math.max(response.current - 1, 0) : -1
      if (pageIdx !== null) {
        this.setCurrentPage(pageIdx + 1)
      } else if (typeof selection.viewerPageNumber === 'number' && selection.viewerPageNumber >= 1) {
        this.setCurrentPage(selection.viewerPageNumber)
      }
    }
    applySelection({
      pageIdx: response.pageIdx,
      viewerPageNumber: response.viewerPageNumber,
    })

    if (response.delayedSelection) {
      window.setTimeout(() => {
        applySelection(response.delayedSelection ?? {})
      }, response.delayedSelection.delayMs ?? 25)
    }

    const emitCount = (current: number, total: number) => {
      for (const handler of this.listeners.get('updatefindmatchescount') ?? []) {
        handler({
          source: this.findController,
          matchesCount: {
            current,
            total,
          },
        })
      }
    }

    for (const handler of this.listeners.get('updatefindcontrolstate') ?? []) {
      handler({
        source: this.findController,
        state: response.state,
        matchesCount: {
          current: response.current,
          total: response.total,
        },
        rawQuery: payload.query,
      })
    }

    if (response.lateCount) {
      window.setTimeout(() => {
        emitCount(response.lateCount?.current ?? response.current, response.lateCount?.total ?? response.total)
      }, response.lateCount.delayMs ?? 25)
      return
    }

    emitCount(response.current, response.total)
  }
}

const createMockRect = (left: number, top: number, width: number, height: number): DOMRect => {
  return {
    x: left,
    y: top,
    left,
    top,
    width,
    height,
    right: left + width,
    bottom: top + height,
    toJSON: () => ({}),
  } as DOMRect
}

const installMockPdfViewer = ({
  onFind,
  pages,
}: {
  onFind: (query: string) => MockFindResponse
  pages: MockPageSpec[]
}) => {
  const iframe = screen.getByTitle('PDF Viewer') as HTMLIFrameElement
  const iframeDocument = document.implementation.createHTMLDocument('pdf-viewer-iframe')
  const findController = {
    selected: {
      pageIdx: -1,
      matchIdx: -1,
    },
  }

  let currentPageNumber = 1
  const pageViews = new Map<number, { div: HTMLElement; viewport: { convertToViewportRectangle: (coords: number[]) => number[] } }>()
  const textLayers = new Map<number, HTMLElement>()
  const textNodeLayouts = new WeakMap<Text, { rect: DOMRect; charWidth: number }>()

  const appendTextSegment = (pageNumber: number, segment: string) => {
    const textLayer = textLayers.get(pageNumber)
    if (!textLayer) {
      throw new Error(`Missing text layer for page ${pageNumber}`)
    }

    const pageIndex = pageNumber - 1
    const segmentIndex = textLayer.querySelectorAll('span').length
    const span = iframeDocument.createElement('span')
    span.textContent = segment
    const spanRect = createMockRect(
      32,
      (pageIndex * 900) + 48 + (segmentIndex * 28),
      Math.max(60, segment.length * 5),
      20,
    )
    span.getBoundingClientRect = () => spanRect
    const textNode = span.firstChild as Text | null
    if (textNode) {
      textNodeLayouts.set(textNode, {
        rect: spanRect,
        charWidth: segment.length > 0 ? spanRect.width / segment.length : spanRect.width,
      })
    }
    textLayer.appendChild(span)
  }

  pages.forEach((page, pageIndex) => {
    const pageTop = pageIndex * 900
    const pageDiv = iframeDocument.createElement('div')
    pageDiv.className = 'page'
    pageDiv.dataset.pageNumber = String(page.pageNumber)
    pageDiv.getBoundingClientRect = () => createMockRect(0, pageTop, 640, 840)

    const textLayer = iframeDocument.createElement('div')
    textLayer.className = 'textLayer'
    textLayers.set(page.pageNumber, textLayer)

    page.textSegments.forEach((segment) => {
      appendTextSegment(page.pageNumber, segment)
    })

    pageDiv.appendChild(textLayer)
    iframeDocument.body.appendChild(pageDiv)

    pageViews.set(page.pageNumber, {
      div: pageDiv,
      viewport: {
        convertToViewportRectangle: (coords: number[]) => coords,
      },
    })
  })

  iframeDocument.createRange = (() => {
    let startNode: Text | null = null
    let endNode: Text | null = null
    let startOffset = 0
    let endOffset = 0

    const buildRangeRect = (): DOMRect | null => {
      if (!startNode || !endNode || startNode !== endNode || endOffset <= startOffset) {
        return null
      }

      const layout = textNodeLayouts.get(startNode)
      if (!layout) {
        return null
      }

      return createMockRect(
        layout.rect.left + (startOffset * layout.charWidth),
        layout.rect.top,
        (endOffset - startOffset) * layout.charWidth,
        layout.rect.height,
      )
    }

    return () => ({
      setStart(node: Node, offset: number) {
        startNode = node as Text
        startOffset = offset
      },
      setEnd(node: Node, offset: number) {
        endNode = node as Text
        endOffset = offset
      },
      getClientRects() {
        const rect = buildRangeRect()
        return rect ? [rect] : []
      },
      getBoundingClientRect() {
        return buildRangeRect() ?? createMockRect(0, 0, 0, 0)
      },
    } as unknown as Range)
  })()

  const pdfViewer = {
    get currentPageNumber() {
      return currentPageNumber
    },
    set currentPageNumber(value: number) {
      currentPageNumber = value
    },
    currentScaleValue: 'auto',
    pdfDocument: {},
    getPageView(pageIndex: number) {
      return pageViews.get(pageIndex + 1)
    },
  }

  const eventBus = new MockPdfEventBus(
    onFind,
    () => currentPageNumber,
    (nextPage) => {
      currentPageNumber = nextPage
    },
    findController,
  )

  const pdfApp = {
    eventBus,
    findController,
    pdfViewer,
    pdfDocument: {},
    appConfig: {
      viewerContainer: iframeDocument.createElement('div'),
    },
  }

  Object.defineProperty(iframe, 'contentWindow', {
    configurable: true,
    value: {
      document: iframeDocument,
      Mark: class {
        unmark(): void {}
        mark(): void {}
      },
      PDFViewerApplication: pdfApp,
    },
  })

  return { iframe, eventBus, pdfViewer, appendTextSegment }
}

const getEvidenceHighlightRects = (iframe: HTMLIFrameElement): HTMLElement[] => {
  return Array.from(
    iframe.contentWindow?.document.querySelectorAll<HTMLElement>('.pdf-evidence-highlight-rect') ?? [],
  )
}

const buildNavigationCommand = (
  overrides: Partial<EvidenceNavigationCommand> = {},
): EvidenceNavigationCommand => ({
  anchorId: 'anchor-1',
  anchor: {
    anchor_kind: 'snippet',
    locator_quality: 'exact_quote',
    supports_decision: 'supports',
    snippet_text: 'Exact quote from PDFX markdown',
    normalized_text: 'Exact quote from PDFX markdown',
    viewer_search_text: 'Exact quote from PDFX markdown',
    page_number: 3,
    section_title: 'Results',
    subsection_title: 'Quantification',
    chunk_ids: ['chunk-1'],
    ...overrides.anchor,
  },
  searchText: 'Exact quote from PDFX markdown',
  pageNumber: 3,
  sectionTitle: 'Results',
  mode: 'select',
  ...overrides,
})

const buildDefaultPages = (): MockPageSpec[] => [
  { pageNumber: 1, textSegments: ['Introduction'] },
  { pageNumber: 2, textSegments: ['Already normalized quote text'] },
  { pageNumber: 3, textSegments: ['Exact quote from PDFX markdown', 'Results'] },
  { pageNumber: 4, textSegments: ['Results'] },
  { pageNumber: 5, textSegments: ['Raw quote with “smart” punctuation'] },
  { pageNumber: 6, textSegments: ['Discussion'] },
]

describe('PdfViewer evidence navigation', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.useRealTimers()
    vi.mocked(global.fetch).mockReset()
    delete window.__pdfViewerEvidenceSpike
    delete window.__pdfViewerEvidenceSpikeLastResult
  })

  it('builds quote and section candidates with deterministic fallbacks', () => {
    const reasons = buildEvidenceSpikeQuoteCandidates(
      '“Quoted” text with enough words to trigger fragment generation because the production chain needs a shorter excerpt for page break fallback behavior,\n' +
        'and it keeps going with several extra words for a later fragment match after the page break simulation. A second sentence makes the sentence fallback distinct.',
    ).map((candidate) => candidate.reason)

    expect(reasons).toEqual(expect.arrayContaining([
      'exact-quote',
      'normalized-quote',
      'first-sentence-fragment',
      'window-fragment',
      'leading-fragment',
      'trailing-fragment',
    ]))

    expect(
      buildEvidenceSpikeSectionCandidates('Results', 'Quantification').map((candidate) => candidate.query),
    ).toEqual(['Results', 'Quantification'])

    expect(normalizeEvidenceSpikePageHints({ pageNumbers: [4, 4, 0, 9], pageNumber: 2 })).toEqual([4, 9, 2])
  })

  it('prefers a sanitized quote candidate before raw markdown-formatted evidence text', () => {
    const query = 'all proteins changed in the allele lacking the *crb_C* isoform constitute interesting candidates.'
    const candidates = buildEvidenceSpikeQuoteCandidates(query)

    expect(candidates[0]).toEqual({
      query: 'all proteins changed in the allele lacking the crb_C isoform constitute interesting candidates.',
      reason: 'sanitized-quote',
    })
    expect(candidates).toContainEqual({
      query,
      reason: 'exact-quote',
    })
  })

  it('expands a matched fragment back to the longest contiguous quote available on the page', () => {
    const pageText = [
      'all proteins changed in the allele lacking the crb_C isoform',
      'constitute interesting candidates in the connection of the Crumbs',
      'function in organizing the cytoskeleton.',
    ].join(' ')
    const desiredQuote = [
      'In summary, all proteins changed in the allele lacking the *crb_C* isoform',
      'constitute interesting candidates in the connection of the Crumbs',
      'function in organizing the cytoskeleton and should be prioritized for follow-up experiments.',
    ].join(' ')
    const fragmentCandidate = buildEvidenceSpikeQuoteCandidates(desiredQuote)
      .find((candidate) => candidate.reason === 'window-fragment' && pageText.includes(candidate.query))
      ?.query

    expect(fragmentCandidate).toBeTruthy()
    expect(
      findExpandedEvidenceQueryFromPageText(pageText, desiredQuote, fragmentCandidate ?? ''),
    ).toEqual(expect.objectContaining({
      query: 'all proteins changed in the allele lacking the crb_C isoform constitute interesting candidates in the connection of the Crumbs function in organizing the cytoskeleton',
      startWordIndex: 2,
    }))
  })

  it('uses the dev harness to localize a normalized quote and render text-layer highlights', async () => {
    vi.stubEnv('VITE_DEV_MODE', 'true')
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    render(<PdfViewer />)

    dispatchPDFDocumentChanged('doc-1', '/fixtures/sample.pdf', 'sample.pdf', 12)
    await waitFor(() => {
      expect(screen.getByText('sample.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (query) => {
        if (query === 'Raw quote with "smart" punctuation') {
          return {
            state: 0,
            total: 1,
            current: 1,
            pageIdx: 4,
          }
        }

        return {
          state: 1,
          total: 0,
          current: 0,
          pageIdx: null,
        }
      },
      pages: buildDefaultPages(),
    })

    fireEvent.load(iframe)
    await waitFor(() => {
      expect(window.__pdfViewerEvidenceSpike).toBeTypeOf('function')
    })

    const result = await window.__pdfViewerEvidenceSpike?.({
      quote: 'Raw   quote\nwith “smart” punctuation',
      pageNumber: 4,
      sectionTitle: 'Results',
    } satisfies PdfEvidenceSpikeInput)

    expect(result).toMatchObject({
      status: 'matched',
      strategy: 'normalized-quote',
      matchedQuery: 'Raw quote with "smart" punctuation',
      matchedPage: 5,
      matchesTotal: 1,
      currentMatch: 1,
    })
    expect(eventBus.findQueries).toEqual([
      'Raw   quote\nwith “smart” punctuation',
      'Raw quote with "smart" punctuation',
    ])
    await waitFor(() => {
      expect(getEvidenceHighlightRects(iframe)).toHaveLength(1)
    })
    expect(getEvidenceHighlightRects(iframe)[0].getAttribute('data-kind')).toBe('quote')
  })

  it('acknowledges select navigation and renders quote highlights on the matched page', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const command = buildNavigationCommand()

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-2', '/fixtures/sample.pdf', 'typed.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('typed.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (query) => {
        if (query === 'Exact quote from PDFX markdown') {
          return {
            state: 0,
            total: 1,
            current: 1,
            pageIdx: 2,
          }
        }

        return {
          state: 1,
          total: 0,
          current: 0,
          pageIdx: null,
        }
      },
      pages: buildDefaultPages(),
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(eventBus.findQueries).toEqual(['Exact quote from PDFX markdown'])
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      locatorQuality: 'exact_quote',
      degraded: false,
      matchedPage: 3,
    }))
    await waitFor(() => {
      expect(getEvidenceHighlightRects(iframe)).toHaveLength(1)
    })
    expect(getEvidenceHighlightRects(iframe)[0].getAttribute('data-mode')).toBe('select')
    expect(getEvidenceHighlightRects(iframe)[0].style.border).toContain('solid')
    expect(screen.getByText('Exact quote')).toBeInTheDocument()
  })

  it('accepts chat-dispatched evidence navigation without rendering custom overlay boxes', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()

    render(
      <PdfViewer
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-chat', '/fixtures/sample.pdf', 'chat-evidence.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('chat-evidence.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (query) => ({
        state: query === 'Exact quote from PDFX markdown' ? 0 : 1,
        total: query === 'Exact quote from PDFX markdown' ? 1 : 0,
        current: query === 'Exact quote from PDFX markdown' ? 1 : 0,
        pageIdx: query === 'Exact quote from PDFX markdown' ? 2 : null,
      }),
      pages: buildDefaultPages(),
    })

    fireEvent.load(iframe)
    dispatchPDFViewerNavigateEvidence(
      buildNavigationCommand({ anchorId: 'chat-anchor-1' }),
    )

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(eventBus.findQueries).toEqual(['Exact quote from PDFX markdown'])
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      locatorQuality: 'exact_quote',
      degraded: false,
      matchedPage: 3,
    }))
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    expect(eventBus.findbarCloseCount).toBe(1)
  })

  it('keeps quote-triggered chat navigation on the matched page instead of falling through to section search', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()

    render(
      <PdfViewer
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-chat-page-context', '/fixtures/sample.pdf', 'chat-page-context.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('chat-page-context.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (query) => {
        if (query === 'Exact quote from PDFX markdown') {
          return {
            state: 0,
            total: 1,
            current: 1,
            pageIdx: 2,
          }
        }

        if (query === 'Results') {
          return {
            state: 0,
            total: 1,
            current: 1,
            pageIdx: 3,
          }
        }

        return {
          state: 1,
          total: 0,
          current: 0,
          pageIdx: null,
        }
      },
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Already normalized quote text'] },
        { pageNumber: 3, textSegments: ['Completely different visible text'] },
        { pageNumber: 4, textSegments: ['Results'] },
      ],
    })

    fireEvent.load(iframe)
    dispatchPDFViewerNavigateEvidence(
      buildNavigationCommand({ anchorId: 'chat-anchor-page-context' }),
    )

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    }, { timeout: 3000 })

    expect(eventBus.findQueries).toEqual([
      'Exact quote from PDFX markdown',
    ])
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'page-fallback',
      locatorQuality: 'page_only',
      degraded: true,
      matchedQuery: 'Exact quote from PDFX markdown',
      matchedPage: 3,
    }))
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    expect(
      screen.getAllByText(
        'Evidence on this page. Quote text was not matched reliably enough to highlight.',
      ),
    ).toHaveLength(2)
  })

  it('waits for a late-selected native PDF.js quote match during chat navigation', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const query = 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants'

    render(
      <PdfViewer
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-chat-native', '/fixtures/sample.pdf', 'chat-native.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('chat-native.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (candidate) => ({
        state: candidate === query ? 1 : 1,
        total: candidate === query ? 1 : 0,
        current: candidate === query ? 1 : 0,
        pageIdx: null,
        delayedSelection: candidate === query
          ? {
              pageIdx: 5,
              viewerPageNumber: 6,
              delayMs: 300,
            }
          : undefined,
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Already normalized quote text'] },
        { pageNumber: 3, textSegments: ['Completely different visible text', 'Results'] },
        { pageNumber: 4, textSegments: ['More text'] },
        { pageNumber: 5, textSegments: ['Discussion'] },
        { pageNumber: 6, textSegments: ['Completely different visible text on the eventual matched page'] },
      ],
    })

    fireEvent.load(iframe)
    window.setTimeout(() => {
      const iframeDocument = iframe.contentWindow?.document
      const textLayer = iframeDocument?.querySelector<HTMLElement>(
        '.page[data-page-number="6"] .textLayer',
      )
      if (!iframeDocument || !textLayer) {
        return
      }

      const nativeHighlight = iframeDocument.createElement('span')
      nativeHighlight.className = 'highlight selected'
      nativeHighlight.textContent = query
      nativeHighlight.getBoundingClientRect = () => createMockRect(48, (5 * 900) + 72, 190, 20)
      textLayer.appendChild(nativeHighlight)
    }, 350)

    dispatchPDFViewerNavigateEvidence(
      buildNavigationCommand({
        anchorId: 'chat-anchor-native',
        anchor: {
          anchor_kind: 'snippet',
          locator_quality: 'exact_quote',
          supports_decision: 'supports',
          snippet_text: query,
          normalized_text: query,
          viewer_search_text: query,
          page_number: 1,
          section_title: 'Results and Discussion',
          subsection_title: query,
          chunk_ids: ['chunk-chat-native'],
        },
        searchText: query,
        pageNumber: 1,
        sectionTitle: 'Results and Discussion',
      }),
    )

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    }, { timeout: 1200 })

    expect(eventBus.findQueries).toEqual([query])
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      locatorQuality: 'exact_quote',
      degraded: false,
      matchedPage: 6,
      matchedQuery: query,
    }))
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    expect(eventBus.findbarCloseCount).toBe(1)
    expect(
      screen.queryByText('Evidence on this page. Quote text was not matched reliably enough to highlight.'),
    ).not.toBeInTheDocument()
    expect(screen.getByText('Page 6')).toBeInTheDocument()
  })

  it('dispatches the selected anchor id when a highlight rect is clicked', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onAnchorSelected = vi.fn()
    const unsubscribe = onPDFViewerEvidenceAnchorSelected(onAnchorSelected)

    render(<PdfViewer pendingNavigation={buildNavigationCommand({ anchorId: 'anchor-click' })} />)

    dispatchPDFDocumentChanged('doc-click', '/fixtures/sample.pdf', 'clickable.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('clickable.pdf')).toBeInTheDocument()
    })

    const { iframe } = installMockPdfViewer({
      onFind: (query) => ({
        state: query === 'Exact quote from PDFX markdown' ? 0 : 1,
        total: query === 'Exact quote from PDFX markdown' ? 1 : 0,
        current: query === 'Exact quote from PDFX markdown' ? 1 : 0,
        pageIdx: query === 'Exact quote from PDFX markdown' ? 2 : null,
      }),
      pages: buildDefaultPages(),
    })

    fireEvent.load(iframe)

    const highlightRect = await waitFor(() => {
      const [rect] = getEvidenceHighlightRects(iframe)
      expect(rect).toBeDefined()
      return rect
    })

    highlightRect.dispatchEvent(new MouseEvent('click', { bubbles: true }))

    expect(onAnchorSelected).toHaveBeenCalledTimes(1)
    expect(onAnchorSelected.mock.calls[0][0].detail.anchorId).toBe('anchor-click')

    unsubscribe()
  })

  it('derives highlight rects from the matched substring instead of the whole text span', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const query = 'Exact quote from PDFX markdown'
    const prefix = 'Prefix '
    const suffix = ' suffix'

    render(<PdfViewer pendingNavigation={buildNavigationCommand({ searchText: query })} />)

    dispatchPDFDocumentChanged('doc-2b', '/fixtures/sample.pdf', 'substring.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('substring.pdf')).toBeInTheDocument()
    })

    const { iframe } = installMockPdfViewer({
      onFind: (candidate) => ({
        state: candidate === query ? 0 : 1,
        total: candidate === query ? 1 : 0,
        current: candidate === query ? 1 : 0,
        pageIdx: candidate === query ? 2 : null,
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Already normalized quote text'] },
        { pageNumber: 3, textSegments: [`${prefix}${query}${suffix}`, 'Results'] },
      ],
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(getEvidenceHighlightRects(iframe)).toHaveLength(1)
    })

    const highlightRect = getEvidenceHighlightRects(iframe)[0]
    expect(highlightRect.style.left).toBe(`${32 + (prefix.length * 5)}px`)
    expect(highlightRect.style.width).toBe(`${query.length * 5}px`)
  })

  it('uses the selected repeated same-page match index for text-layer highlighting', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const query = 'Exact quote from PDFX markdown'

    render(<PdfViewer pendingNavigation={buildNavigationCommand({ searchText: query })} />)

    dispatchPDFDocumentChanged('doc-2c', '/fixtures/sample.pdf', 'repeated.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('repeated.pdf')).toBeInTheDocument()
    })

    const { iframe } = installMockPdfViewer({
      onFind: (candidate) => ({
        state: candidate === query ? 0 : 1,
        total: candidate === query ? 2 : 0,
        current: candidate === query ? 2 : 0,
        pageIdx: candidate === query ? 2 : null,
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Already normalized quote text'] },
        { pageNumber: 3, textSegments: ['Header', query, 'gap', query, 'Results'] },
      ],
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(getEvidenceHighlightRects(iframe)).toHaveLength(1)
    })

    expect(getEvidenceHighlightRects(iframe)[0].style.top).toBe(`${48 + (3 * 28)}px`)
  })

  it('waits for delayed text-layer rendering before degrading a successful quote match', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const query = 'Exact quote from PDFX markdown'

    render(<PdfViewer pendingNavigation={buildNavigationCommand({ searchText: query })} />)

    dispatchPDFDocumentChanged('doc-2d', '/fixtures/sample.pdf', 'delayed-text-layer.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('delayed-text-layer.pdf')).toBeInTheDocument()
    })

    const { iframe, appendTextSegment } = installMockPdfViewer({
      onFind: (candidate) => ({
        state: candidate === query ? 0 : 1,
        total: candidate === query ? 1 : 0,
        current: candidate === query ? 1 : 0,
        pageIdx: candidate === query ? 2 : null,
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Already normalized quote text'] },
        { pageNumber: 3, textSegments: [] },
      ],
    })

    fireEvent.load(iframe)

    window.setTimeout(() => {
      appendTextSegment(3, query)
    }, 150)

    await waitFor(() => {
      expect(getEvidenceHighlightRects(iframe)).toHaveLength(1)
    })

    expect(getEvidenceHighlightRects(iframe)[0].getAttribute('data-kind')).toBe('quote')
  })

  it('reuses native PDF.js match rects when raw text reconstruction cannot localize the quote', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const query = 'crumbs ( crb ) mutant eyes'
    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'exact_quote',
        supports_decision: 'supports',
        snippet_text: query,
        normalized_text: query,
        viewer_search_text: query,
        page_number: 3,
        section_title: 'Results',
        subsection_title: null,
        chunk_ids: ['chunk-native-match'],
      },
      searchText: query,
      pageNumber: 3,
      sectionTitle: 'Results',
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-native-match', '/fixtures/sample.pdf', 'native-match.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('native-match.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (candidate) => ({
        state: candidate === query ? 0 : 1,
        total: candidate === query ? 1 : 0,
        current: candidate === query ? 1 : 0,
        pageIdx: candidate === query ? 2 : null,
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Already normalized quote text'] },
        { pageNumber: 3, textSegments: ['Completely different visible text'] },
      ],
    })

    fireEvent.load(iframe)

    window.setTimeout(() => {
      const iframeDocument = iframe.contentWindow?.document
      const textLayer = iframeDocument?.querySelector<HTMLElement>(
        '.page[data-page-number="3"] .textLayer',
      )
      if (!iframeDocument || !textLayer) {
        return
      }

      const nativeHighlight = iframeDocument.createElement('span')
      nativeHighlight.className = 'highlight selected'
      nativeHighlight.textContent = query
      nativeHighlight.getBoundingClientRect = () => createMockRect(48, (2 * 900) + 72, 190, 20)
      textLayer.appendChild(nativeHighlight)
    }, 150)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(eventBus.findQueries).toEqual([query])
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      locatorQuality: 'exact_quote',
      degraded: false,
      matchedPage: 3,
    }))
    await waitFor(() => {
      expect(getEvidenceHighlightRects(iframe)).toHaveLength(1)
    })
    expect(getEvidenceHighlightRects(iframe)[0].style.left).toBe('48px')
    expect(getEvidenceHighlightRects(iframe)[0].style.top).toBe('72px')
  })

  it('matches a contiguous quote fragment before degrading to page context', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const query = [
      'Nevertheless, all proteins changed in the allele lacking',
      'the crb_C isoform constitute interesting candidates in the con-',
      'nection of the Crumbs function in organizing the cytoskeleton.',
    ].join('\n')
    const fragmentCandidate = buildEvidenceSpikeQuoteCandidates(query)
      .find((candidate) => candidate.reason === 'window-fragment')
      ?.query

    expect(fragmentCandidate).toBeTruthy()

    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'exact_quote',
        supports_decision: 'supports',
        snippet_text: query,
        normalized_text: query,
        viewer_search_text: query,
        page_number: 3,
        section_title: 'Results',
        subsection_title: null,
        chunk_ids: ['chunk-window-fragment'],
      },
      searchText: query,
      pageNumber: 3,
      sectionTitle: 'Results',
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-window-fragment', '/fixtures/sample.pdf', 'window-fragment.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('window-fragment.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (candidate) => ({
        state: candidate === fragmentCandidate ? 0 : 1,
        total: candidate === fragmentCandidate ? 1 : 0,
        current: candidate === fragmentCandidate ? 1 : 0,
        pageIdx: candidate === fragmentCandidate ? 2 : null,
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Already normalized quote text'] },
        { pageNumber: 3, textSegments: [fragmentCandidate ?? '', 'Results'] },
      ],
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(eventBus.findQueries).toContain(fragmentCandidate)
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      strategy: 'window-fragment',
      locatorQuality: 'normalized_quote',
      degraded: false,
      matchedPage: 3,
      matchedQuery: fragmentCandidate,
    }))
    await waitFor(() => {
      expect(getEvidenceHighlightRects(iframe)).toHaveLength(1)
    })
    expect(screen.getByText('Approximate quote')).toBeInTheDocument()
  })

  it('recovers a longer quote span after a fragment match on the resolved page', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const pageText = [
      'all proteins changed in the allele lacking the crb_C isoform',
      'constitute interesting candidates in the connection of the Crumbs',
      'function in organizing the cytoskeleton.',
    ].join(' ')
    const query = [
      'In summary, all proteins changed in the allele lacking the *crb_C* isoform',
      'constitute interesting candidates in the connection of the Crumbs',
      'function in organizing the cytoskeleton and should be prioritized for follow-up experiments.',
    ].join(' ')
    const fragmentCandidate = buildEvidenceSpikeQuoteCandidates(query)
      .find((candidate) => candidate.reason === 'window-fragment' && pageText.includes(candidate.query))
      ?.query
    const expandedQuery = findExpandedEvidenceQueryFromPageText(pageText, query, fragmentCandidate ?? '')?.query

    expect(fragmentCandidate).toBeTruthy()
    expect(expandedQuery).toBe(
      'all proteins changed in the allele lacking the crb_C isoform constitute interesting candidates in the connection of the Crumbs function in organizing the cytoskeleton',
    )

    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'exact_quote',
        supports_decision: 'supports',
        snippet_text: query,
        normalized_text: query,
        viewer_search_text: query,
        page_number: 1,
        section_title: 'Results and Discussion',
        subsection_title: null,
        chunk_ids: ['chunk-expanded-fragment'],
      },
      searchText: query,
      pageNumber: 1,
      sectionTitle: 'Results and Discussion',
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-expanded-fragment', '/fixtures/sample.pdf', 'expanded-fragment.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('expanded-fragment.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (candidate) => ({
        state: candidate === fragmentCandidate || candidate === expandedQuery ? 0 : 1,
        total: candidate === fragmentCandidate || candidate === expandedQuery ? 1 : 0,
        current: candidate === fragmentCandidate || candidate === expandedQuery ? 1 : 0,
        pageIdx: candidate === fragmentCandidate || candidate === expandedQuery ? 2 : null,
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Already normalized quote text'] },
        { pageNumber: 3, textSegments: [pageText, 'Results and Discussion'] },
      ],
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(eventBus.findQueries).toContain(fragmentCandidate)
    expect(eventBus.findQueries).toContain(expandedQuery)
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      strategy: 'window-fragment',
      locatorQuality: 'normalized_quote',
      degraded: false,
      matchedPage: 3,
      matchedQuery: expandedQuery,
      note: 'Recovered a longer contiguous quote around the matched fragment on the PDF text layer.',
    }))
    await waitFor(() => {
      expect(getEvidenceHighlightRects(iframe)).toHaveLength(1)
    })
  })

  it('renders hover previews differently from selected evidence highlights', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const hoverCommand = buildNavigationCommand({ mode: 'hover' })
    const selectCommand = buildNavigationCommand({ mode: 'select' })

    const { rerender } = render(<PdfViewer pendingNavigation={hoverCommand} />)

    dispatchPDFDocumentChanged('doc-3', '/fixtures/sample.pdf', 'hover-select.pdf', 8)
    await waitFor(() => {
      expect(screen.getByText('hover-select.pdf')).toBeInTheDocument()
    })

    const { iframe } = installMockPdfViewer({
      onFind: (query) => ({
        state: query === 'Exact quote from PDFX markdown' ? 0 : 1,
        total: query === 'Exact quote from PDFX markdown' ? 1 : 0,
        current: query === 'Exact quote from PDFX markdown' ? 1 : 0,
        pageIdx: query === 'Exact quote from PDFX markdown' ? 2 : null,
      }),
      pages: buildDefaultPages(),
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(getEvidenceHighlightRects(iframe)).toHaveLength(1)
    })
    expect(getEvidenceHighlightRects(iframe)[0].getAttribute('data-mode')).toBe('hover')
    expect(getEvidenceHighlightRects(iframe)[0].style.border).toContain('dashed')

    rerender(<PdfViewer pendingNavigation={selectCommand} />)

    await waitFor(() => {
      expect(getEvidenceHighlightRects(iframe)[0].getAttribute('data-mode')).toBe('select')
    })
    expect(getEvidenceHighlightRects(iframe)[0].style.border).toContain('solid')
  })

  it('highlights a section heading when quote matching fails but section metadata resolves', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'section',
        locator_quality: 'section_only',
        supports_decision: 'supports',
        snippet_text: 'Snippet text exists but should not drive quote search.',
        normalized_text: 'Snippet text exists but should not drive quote search.',
        viewer_search_text: null,
        page_number: 4,
        section_title: 'Results',
        subsection_title: 'Quantification',
        chunk_ids: ['chunk-section'],
      },
      searchText: null,
      pageNumber: 4,
      sectionTitle: 'Results',
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-4', '/fixtures/sample.pdf', 'section.pdf', 12)
    await waitFor(() => {
      expect(screen.getByText('section.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (query) => ({
        state: query === 'Results' ? 0 : 1,
        total: query === 'Results' ? 1 : 0,
        current: query === 'Results' ? 1 : 0,
        pageIdx: query === 'Results' ? 3 : null,
      }),
      pages: buildDefaultPages(),
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(eventBus.findQueries).toEqual(['Results'])
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'section-fallback',
      locatorQuality: 'section_only',
      degraded: true,
      matchedQuery: 'Results',
      matchedPage: 4,
    }))
    await waitFor(() => {
      expect(getEvidenceHighlightRects(iframe)).toHaveLength(1)
    })
    expect(getEvidenceHighlightRects(iframe)[0].getAttribute('data-kind')).toBe('section')
    expect(screen.getByText('Section fallback')).toBeInTheDocument()
  })

  it('re-biases retries to the hinted page and degrades to a page banner when text-layer matching fails', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'normalized_quote',
        supports_decision: 'supports',
        snippet_text: 'Repeated   quote\nwith “smart” punctuation and enough extra words to keep the retry chain moving before page fallback.',
        normalized_text: 'Repeated quote with "smart" punctuation and enough extra words to keep the retry chain moving before page fallback.',
        viewer_search_text: 'Repeated   quote\nwith “smart” punctuation and enough extra words to keep the retry chain moving before page fallback.',
        page_number: 3,
        section_title: null,
        subsection_title: null,
        chunk_ids: ['chunk-page-fallback'],
      },
      searchText: 'Repeated   quote\nwith “smart” punctuation and enough extra words to keep the retry chain moving before page fallback.',
      pageNumber: 3,
      sectionTitle: null,
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-5', '/fixtures/sample.pdf', 'page-fallback.pdf', 10)
    await waitFor(() => {
      expect(screen.getByText('page-fallback.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: () => ({
        state: 1,
        total: 0,
        current: 0,
        pageIdx: null,
      }),
      pages: buildDefaultPages(),
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(eventBus.findDispatches.length).toBeGreaterThan(1)
    expect(eventBus.findDispatches.map((entry) => entry.pageBeforeDispatch)).toEqual(
      eventBus.findDispatches.map(() => 3),
    )
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'page-fallback',
      locatorQuality: 'page_only',
      degraded: true,
      matchedPage: 3,
    }))
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    expect(
      screen.getAllByText('Evidence on this page. Quote text was not matched reliably enough to highlight.'),
    ).toHaveLength(2)
  })

  it('degrades to page fallback when PDF.js finds a quote but no reliable text-layer rects can be derived', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'exact_quote',
        supports_decision: 'supports',
        snippet_text: 'Raw quote with “smart” punctuation',
        normalized_text: 'Raw quote with "smart" punctuation',
        viewer_search_text: 'Raw quote with “smart” punctuation',
        page_number: 3,
        section_title: null,
        subsection_title: null,
        chunk_ids: ['chunk-strict-gate'],
      },
      searchText: 'Raw quote with “smart” punctuation',
      pageNumber: 3,
      sectionTitle: null,
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-5b', '/fixtures/sample.pdf', 'strict-gate.pdf', 10)
    await waitFor(() => {
      expect(screen.getByText('strict-gate.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer({
      onFind: (query) => ({
        state: query.includes('quote') ? 0 : 1,
        total: query.includes('quote') ? 1 : 0,
        current: query.includes('quote') ? 1 : 0,
        pageIdx: query.includes('quote') ? 2 : null,
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Already normalized quote text'] },
        { pageNumber: 3, textSegments: ['Completely different visible text', 'Results'] },
      ],
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    }, { timeout: 4000 })

    expect(onNavigationStateChange).toHaveBeenCalledWith(expect.objectContaining({
      status: 'page-fallback',
      locatorQuality: 'page_only',
      degraded: true,
      matchedPage: 3,
    }))

    expect(eventBus.findQueries).toEqual([
      'Raw quote with “smart” punctuation',
      'Raw quote with "smart" punctuation',
    ])
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    expect(
      screen.getAllByText('Evidence on this page. Quote text was not matched reliably enough to highlight.'),
    ).toHaveLength(2)
  })

  it('preserves the viewer page when PDF.js jumps to a match without populating selected.pageIdx', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'exact_quote',
        supports_decision: 'supports',
        snippet_text: 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
        normalized_text: 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
        viewer_search_text: 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
        page_number: 1,
        section_title: 'Results and Discussion',
        subsection_title: 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
        chunk_ids: ['chunk-live-repro'],
      },
      searchText: 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
      pageNumber: 1,
      sectionTitle: 'Results and Discussion',
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-live-repro', '/fixtures/sample.pdf', 'live-repro.pdf', 10)
    await waitFor(() => {
      expect(screen.getByText('live-repro.pdf')).toBeInTheDocument()
    })

    const { iframe } = installMockPdfViewer({
      onFind: (query) => ({
        state: query === 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants' ? 1 : 1,
        total: query === 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants' ? 1 : 0,
        current: query === 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants' ? 1 : 0,
        pageIdx: null,
        viewerPageNumber: query === 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants' ? 6 : null,
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Background'] },
        { pageNumber: 3, textSegments: ['Methods'] },
        { pageNumber: 4, textSegments: ['Figure legends'] },
        { pageNumber: 5, textSegments: ['Discussion'] },
        { pageNumber: 6, textSegments: ['Nearby text that does not include the requested quote contiguously'] },
      ],
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    }, { timeout: 3000 })

    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'page-fallback',
      locatorQuality: 'page_only',
      degraded: true,
      matchedPage: 6,
    }))
    expect(screen.getByText('Page 6')).toBeInTheDocument()
    expect(
      screen.getAllByText('Evidence on this page. Quote text was not matched reliably enough to highlight.'),
    ).toHaveLength(2)
  })

  it('retries quote localization on a late-selected PDF.js match page before degrading', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'exact_quote',
        supports_decision: 'supports',
        snippet_text: 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
        normalized_text: 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
        viewer_search_text: 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
        page_number: 1,
        section_title: 'Results and Discussion',
        subsection_title: 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
        chunk_ids: ['chunk-late-selected-page'],
      },
      searchText: 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
      pageNumber: 1,
      sectionTitle: 'Results and Discussion',
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-late-selected-page', '/fixtures/sample.pdf', 'late-selected-page.pdf', 10)
    await waitFor(() => {
      expect(screen.getByText('late-selected-page.pdf')).toBeInTheDocument()
    })

    const { iframe } = installMockPdfViewer({
      onFind: () => ({
        state: 1,
        total: 1,
        current: 1,
        pageIdx: null,
        delayedSelection: {
          pageIdx: 5,
          viewerPageNumber: 6,
          delayMs: 300,
        },
      }),
      pages: [
        { pageNumber: 1, textSegments: ['Introduction'] },
        { pageNumber: 2, textSegments: ['Background'] },
        { pageNumber: 3, textSegments: ['Methods'] },
        { pageNumber: 4, textSegments: ['Figure legends'] },
        { pageNumber: 5, textSegments: ['Discussion'] },
        { pageNumber: 6, textSegments: ['2.6. Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants'] },
      ],
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    }, { timeout: 4000 })

    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      locatorQuality: 'exact_quote',
      degraded: false,
      matchedPage: 6,
      matchedQuery: 'Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants',
    }))
    expect(screen.getByText('Page 6')).toBeInTheDocument()
  })

  it('shows an unresolved marker when no reliable quote, section, or page target exists', async () => {
    vi.mocked(global.fetch).mockResolvedValue(new Response(null, { status: 200 }))

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'document',
        locator_quality: 'unresolved',
        supports_decision: 'supports',
        snippet_text: null,
        sentence_text: null,
        normalized_text: null,
        viewer_search_text: null,
        page_number: null,
        section_title: null,
        subsection_title: null,
        chunk_ids: ['chunk-unresolved'],
      },
      searchText: null,
      pageNumber: null,
      sectionTitle: null,
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-6', '/fixtures/sample.pdf', 'unresolved.pdf', 6)
    await waitFor(() => {
      expect(screen.getByText('unresolved.pdf')).toBeInTheDocument()
    })

    const { iframe } = installMockPdfViewer({
      onFind: () => ({
        state: 1,
        total: 0,
        current: 0,
        pageIdx: null,
      }),
      pages: buildDefaultPages(),
    })

    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'not-found',
      locatorQuality: 'unresolved',
      degraded: true,
      matchedPage: null,
    }))
    expect(getEvidenceHighlightRects(iframe)).toHaveLength(0)
    expect(
      screen.getAllByText('Evidence localization is unresolved. No trusted page or text highlight could be produced.'),
    ).toHaveLength(2)
  })
})
