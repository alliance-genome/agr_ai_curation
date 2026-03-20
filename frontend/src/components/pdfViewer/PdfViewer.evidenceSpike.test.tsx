import { afterEach, describe, expect, it, vi } from 'vitest'

import { fireEvent, render, screen, waitFor } from '../../test/test-utils'
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
    if (eventName === 'find') {
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
})
