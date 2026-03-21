import { afterEach, describe, expect, it, vi } from 'vitest'

import { fireEvent, render, screen, waitFor } from '../../test/test-utils'
import type { EvidenceNavigationCommand } from '@/features/curation/evidence'
import PdfViewer, {
  buildEvidenceSpikeQuoteCandidates,
  buildEvidenceSpikeSectionCandidates,
  normalizeEvidenceSpikePageHints,
  normalizeEvidenceSpikeText,
  type PdfEvidenceSpikeInput,
} from './PdfViewer'
import { dispatchPDFDocumentChanged } from './pdfEvents'

interface MockFindResponse {
  state: number
  total: number
  current: number
  pageIdx: number | null
  lateCount?: {
    total: number
    current: number
    delayMs?: number
  }
}

class MockPdfEventBus {
  listeners = new Map<string, Set<(event: any) => void>>()
  findQueries: string[] = []
  currentPage = 1
  findDispatches: Array<{ query: string; pageBeforeDispatch: number }> = []
  findbarCloseCount = 0

  constructor(
    private readonly onFind: (query: string) => MockFindResponse,
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
    if (eventName !== 'find') {
      return
    }

    this.findQueries.push(payload.query)
    const response = this.onFind(payload.query)
    this.currentPage = response.pageIdx !== null ? response.pageIdx + 1 : this.currentPage
    this.emit('updatefindmatchescount', {
      source: payload.source === 'pdf-evidence-spike' ? undefined : payload.source,
      matchesCount: {
        current: response.current,
        total: response.total,
      },
    })
    this.emit('updatefindcontrolstate', {
      source: undefined,
      state: response.state,
      matchesCount: {
        current: response.current,
        total: response.total,
      },
      rawQuery: payload.query,
    })
  }

  emit(eventName: string, payload: any) {
    for (const handler of this.listeners.get(eventName) ?? []) {
      handler(payload)
    }
  }
}

const installMockPdfViewer = (onFind: (query: string) => MockFindResponse) => {
  const iframe = screen.getByTitle('PDF Viewer') as HTMLIFrameElement
  const iframeDocument = document.implementation.createHTMLDocument('pdf-viewer-iframe')
  const eventBus = new MockPdfEventBus(onFind)
  const findController = {
    selected: {
      pageIdx: -1,
      matchIdx: -1,
    },
  }
  const pdfViewer = {
    currentPageNumber: 1,
    currentScaleValue: 'auto',
    pdfDocument: {},
  }
  const pdfApp = {
    eventBus,
    findController,
    pdfViewer,
    pdfDocument: {},
    appConfig: {
      viewerContainer: iframeDocument.createElement('div'),
    },
  }

  const originalDispatch = eventBus.dispatch.bind(eventBus)
  eventBus.dispatch = (eventName: string, payload: any) => {
    if (eventName === 'findbarclose') {
      eventBus.findbarCloseCount += 1
      findController.selected.pageIdx = -1
      findController.selected.matchIdx = -1
      return
    }

    if (eventName === 'find') {
      eventBus.findDispatches.push({
        query: payload.query,
        pageBeforeDispatch: pdfViewer.currentPageNumber,
      })
      const response = onFind(payload.query)
      findController.selected.pageIdx = response.pageIdx ?? -1
      findController.selected.matchIdx = response.pageIdx !== null ? Math.max(response.current - 1, 0) : -1
      if (response.pageIdx !== null) {
        pdfViewer.currentPageNumber = response.pageIdx + 1
      }

      const emitCount = (current: number, total: number) => {
        for (const handler of eventBus.listeners.get('updatefindmatchescount') ?? []) {
          handler({
            source: findController,
            matchesCount: {
              current,
              total,
            },
          })
        }
      }

      for (const handler of eventBus.listeners.get('updatefindcontrolstate') ?? []) {
        handler({
          source: findController,
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
      } else {
        emitCount(response.current, response.total)
      }

      eventBus.findQueries.push(payload.query)
      return
    }
    originalDispatch(eventName, payload)
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

  return { iframe, eventBus, findController, pdfViewer }
}

const buildNavigationCommand = (
  overrides: Partial<EvidenceNavigationCommand> = {},
): EvidenceNavigationCommand => ({
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

describe('PDF evidence spike helpers', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.useRealTimers()
    delete window.__pdfViewerEvidenceSpike
    delete window.__pdfViewerEvidenceSpikeLastResult
  })

  it('normalizes quote text and page hints for PDF.js search', () => {
    expect(normalizeEvidenceSpikeText('“Line one”\n  and — line two')).toBe('"Line one" and - line two')
    expect(normalizeEvidenceSpikePageHints({ pageNumbers: [4, 4, 0, 9], pageNumber: 2 })).toEqual([4, 9, 2])
  })

  it('builds quote and section candidates with deterministic fallbacks', () => {
    const reasons = buildEvidenceSpikeQuoteCandidates(
      '“Quoted” text with enough words to trigger fragment generation because the prototype needs a shorter excerpt for cross page fallback behavior,\n' +
        'and it keeps going with several extra words for a later fragment match after the page break simulation.  A second sentence makes the first-sentence fallback distinct.',
    ).map((candidate) => candidate.reason)

    expect(reasons).toEqual(expect.arrayContaining([
      'raw',
      'whitespace-normalized',
      'ascii-normalized',
      'first-sentence',
      'leading-fragment',
      'trailing-fragment',
    ]))

    expect(
      buildEvidenceSpikeSectionCandidates(' Results ', ['Paper', 'Results', 'Subheading']).map((candidate) => candidate.query),
    ).toEqual(['Results', 'Paper', 'Subheading'])

    expect(
      buildEvidenceSpikeSectionCandidates('Results', ['Quantification', 'Results', 'Quantification'])
        .map((candidate) => candidate.query),
    ).toEqual(['Results', 'Quantification'])
  })

  it('uses the dev-only harness to drive normalized PDF.js find queries', async () => {
    vi.stubEnv('VITE_DEV_MODE', 'true')
    vi.mocked(global.fetch).mockResolvedValue(
      new Response(null, {
        status: 200,
      }),
    )

    render(<PdfViewer />)

    dispatchPDFDocumentChanged('doc-1', '/fixtures/sample.pdf', 'sample.pdf', 12)
    await waitFor(() => {
      expect(screen.getByText('sample.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus, pdfViewer } = installMockPdfViewer((query) => {
      if (query === 'Raw quote with "smart" punctuation') {
        return {
          state: 0,
          total: 0,
          current: 0,
          pageIdx: 4,
          lateCount: {
            current: 1,
            total: 1,
            delayMs: 15,
          },
        }
      }

      return {
        state: 1,
        total: 0,
        current: 0,
        pageIdx: null,
      }
    })

    await waitFor(() => {
      expect(iframe.src).toContain('/pdfjs/web/viewer.html?file=%2Ffixtures%2Fsample.pdf')
    })
    fireEvent.load(iframe)
    await new Promise((resolve) => setTimeout(resolve, 250))
    expect(window.__pdfViewerEvidenceSpike).toBeTypeOf('function')

    const result = await window.__pdfViewerEvidenceSpike?.({
      quote: 'Raw   quote\nwith “smart” punctuation',
      pageNumber: 4,
      sectionTitle: 'Results',
    } satisfies PdfEvidenceSpikeInput)

    expect(result).toMatchObject({
      status: 'matched',
      strategy: 'ascii-normalized',
      matchedQuery: 'Raw quote with "smart" punctuation',
      matchedPage: 5,
      matchesTotal: 1,
      currentMatch: 1,
    })
    expect(pdfViewer.currentPageNumber).toBe(5)
    expect(eventBus.findQueries).toEqual([
      'Raw   quote\nwith “smart” punctuation',
      'Raw quote with “smart” punctuation',
      'Raw quote with "smart" punctuation',
    ])
    expect(window.__pdfViewerEvidenceSpikeLastResult).toMatchObject({
      status: 'matched',
      matchedPage: 5,
    })
  })

  it('handles typed navigation props and acknowledges successful quote localization', async () => {
    vi.mocked(global.fetch).mockResolvedValue(
      new Response(null, {
        status: 200,
      }),
    )

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

    const { iframe, eventBus } = installMockPdfViewer((query) => {
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
    expect(screen.getByText('Exact quote')).toBeInTheDocument()
  })

  it('downgrades exact quote anchors when only a normalized retry resolves', async () => {
    vi.mocked(global.fetch).mockResolvedValue(
      new Response(null, {
        status: 200,
      }),
    )

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'exact_quote',
        supports_decision: 'supports',
        snippet_text: 'Repeated   quote\nwith “smart” punctuation',
        normalized_text: 'Repeated quote with "smart" punctuation',
        viewer_search_text: 'Repeated   quote\nwith “smart” punctuation',
        page_number: 3,
        section_title: 'Results',
        subsection_title: 'Quantification',
        chunk_ids: ['chunk-normalized-fallback'],
      },
      searchText: 'Repeated   quote\nwith “smart” punctuation',
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

    dispatchPDFDocumentChanged('doc-5', '/fixtures/sample.pdf', 'normalized-fallback.pdf', 9)
    await waitFor(() => {
      expect(screen.getByText('normalized-fallback.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer((query) => {
      if (query === 'Repeated quote with "smart" punctuation') {
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
    })

    await waitFor(() => {
      expect(iframe.src).toContain('/pdfjs/web/viewer.html?file=%2Ffixtures%2Fsample.pdf')
    })
    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(eventBus.findQueries).toEqual([
      'Repeated   quote\nwith “smart” punctuation',
      'Repeated quote with “smart” punctuation',
      'Repeated quote with "smart" punctuation',
    ])
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      locatorQuality: 'normalized_quote',
      degraded: false,
      matchedQuery: 'Repeated quote with "smart" punctuation',
      matchedPage: 3,
    }))
    expect(screen.getByText('Approximate quote')).toBeInTheDocument()
  })

  it('does not upgrade normalized quote anchors when the first query resolves', async () => {
    vi.mocked(global.fetch).mockResolvedValue(
      new Response(null, {
        status: 200,
      }),
    )

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'normalized_quote',
        supports_decision: 'supports',
        snippet_text: 'Already normalized quote text',
        normalized_text: 'Already normalized quote text',
        viewer_search_text: 'Already normalized quote text',
        page_number: 2,
        section_title: 'Discussion',
        subsection_title: 'Summary',
        chunk_ids: ['chunk-normalized-stays-normalized'],
      },
      searchText: 'Already normalized quote text',
      pageNumber: 2,
      sectionTitle: 'Discussion',
    })

    render(
      <PdfViewer
        pendingNavigation={command}
        onNavigationComplete={onNavigationComplete}
        onNavigationStateChange={onNavigationStateChange}
      />,
    )

    dispatchPDFDocumentChanged('doc-6', '/fixtures/sample.pdf', 'normalized-stable.pdf', 7)
    await waitFor(() => {
      expect(screen.getByText('normalized-stable.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer((query) => {
      if (query === 'Already normalized quote text') {
        return {
          state: 0,
          total: 1,
          current: 1,
          pageIdx: 1,
        }
      }

      return {
        state: 1,
        total: 0,
        current: 0,
        pageIdx: null,
      }
    })

    await waitFor(() => {
      expect(iframe.src).toContain('/pdfjs/web/viewer.html?file=%2Ffixtures%2Fsample.pdf')
    })
    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(eventBus.findQueries).toEqual(['Already normalized quote text'])
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'matched',
      locatorQuality: 'normalized_quote',
      degraded: false,
      matchedQuery: 'Already normalized quote text',
      matchedPage: 2,
    }))
    expect(screen.getByText('Approximate quote')).toBeInTheDocument()
  })

  it('skips quote search for degraded anchors when typed searchText is absent', async () => {
    vi.mocked(global.fetch).mockResolvedValue(
      new Response(null, {
        status: 200,
      }),
    )

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'section',
        locator_quality: 'section_only',
        supports_decision: 'supports',
        snippet_text: 'Snippet text exists but should not be used as a typed quote-search fallback.',
        normalized_text: 'Snippet text exists but should not be used as a typed quote-search fallback.',
        viewer_search_text: null,
        page_number: 4,
        section_title: 'Results',
        subsection_title: 'Quantification',
        chunk_ids: ['chunk-3'],
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

    dispatchPDFDocumentChanged('doc-4', '/fixtures/sample.pdf', 'degraded.pdf', 12)
    await waitFor(() => {
      expect(screen.getByText('degraded.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer((query) => {
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
    expect(screen.queryByText('Approximate quote')).not.toBeInTheDocument()
    expect(screen.getByText('Section fallback')).toBeInTheDocument()
  })

  it('re-biases each retry to the hinted page and reports section fallback when quote search fails', async () => {
    vi.mocked(global.fetch).mockResolvedValue(
      new Response(null, {
        status: 200,
      }),
    )

    const onNavigationComplete = vi.fn()
    const onNavigationStateChange = vi.fn()
    const command = buildNavigationCommand({
      anchor: {
        anchor_kind: 'snippet',
        locator_quality: 'normalized_quote',
        supports_decision: 'supports',
        snippet_text: 'Repeated   quote\nwith “smart” punctuation and enough extra words to keep the retry chain moving before section search.',
        normalized_text: 'Repeated quote with "smart" punctuation and enough extra words to keep the retry chain moving before section search.',
        viewer_search_text: 'Repeated quote with "smart" punctuation and enough extra words to keep the retry chain moving before section search.',
        page_number: 3,
        section_title: 'Results',
        subsection_title: 'Quantification',
        chunk_ids: ['chunk-2'],
      },
      searchText: 'Repeated quote with "smart" punctuation and enough extra words to keep the retry chain moving before section search.',
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

    dispatchPDFDocumentChanged('doc-3', '/fixtures/sample.pdf', 'fallback.pdf', 10)
    await waitFor(() => {
      expect(screen.getByText('fallback.pdf')).toBeInTheDocument()
    })

    const { iframe, eventBus } = installMockPdfViewer((query) => {
      if (query === 'Results') {
        return {
          state: 0,
          total: 1,
          current: 1,
          pageIdx: 5,
        }
      }

      return {
        state: 1,
        total: 0,
        current: 0,
        pageIdx: 7,
      }
    })

    await waitFor(() => {
      expect(iframe.src).toContain('/pdfjs/web/viewer.html?file=%2Ffixtures%2Fsample.pdf')
    })
    fireEvent.load(iframe)

    await waitFor(() => {
      expect(onNavigationComplete).toHaveBeenCalledTimes(1)
    })

    expect(eventBus.findQueries).toEqual([
      'Repeated quote with "smart" punctuation and enough extra words to keep the retry chain moving before section search.',
      'Results',
    ])
    expect(eventBus.findDispatches).toHaveLength(2)
    expect(eventBus.findDispatches.map((entry) => entry.pageBeforeDispatch)).toEqual(
      eventBus.findDispatches.map(() => 3),
    )
    expect(eventBus.findbarCloseCount).toBeGreaterThanOrEqual(1)
    expect(onNavigationStateChange).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'section-fallback',
      locatorQuality: 'section_only',
      degraded: true,
      matchedPage: 6,
      matchedQuery: 'Results',
    }))
    expect(screen.getByText('Section fallback')).toBeInTheDocument()
  })
})
