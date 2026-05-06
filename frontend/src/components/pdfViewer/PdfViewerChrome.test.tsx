import { createRef } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '../../test/test-utils'

import { PdfViewerChrome } from './PdfViewerChrome'
import type { PdfViewerNavigationResult } from './pdfEvidenceNavigation'

const defaultUploadDialog = {
  open: false,
  dismissedToBackground: false,
  fileName: '',
  stage: 'uploading',
  progress: 0,
  message: '',
}

const renderChrome = (overrides: Partial<Parameters<typeof PdfViewerChrome>[0]> = {}) => {
  const props: Parameters<typeof PdfViewerChrome>[0] = {
    activeDocument: null,
    status: 'idle',
    error: null,
    retryKey: 0,
    viewerSrc: '/pdfjs/web/viewer.html',
    iframeRef: createRef<HTMLIFrameElement>(),
    highlightTerms: [],
    navigationResult: null,
    navigationBannerMessage: null,
    dragActive: false,
    uploadInFlight: false,
    dropError: null,
    uploadDialog: defaultUploadDialog,
    currentPage: 1,
    zoomLevel: 100,
    searchQuery: '',
    searchCurrent: null,
    searchTotal: null,
    searchNotFound: false,
    onDragEnter: vi.fn(),
    onDragOver: vi.fn(),
    onDragLeave: vi.fn(),
    onDrop: vi.fn(),
    onRetry: vi.fn(),
    onCloseUploadDialog: vi.fn(),
    onPreviousPage: vi.fn(),
    onNextPage: vi.fn(),
    onZoomOut: vi.fn(),
    onZoomIn: vi.fn(),
    onZoomAuto: vi.fn(),
    onSearchQueryChange: vi.fn(),
    onSearchNext: vi.fn(),
    onSearchPrevious: vi.fn(),
    ...overrides,
  }

  return render(<PdfViewerChrome {...props} />)
}

describe('PdfViewerChrome', () => {
  it('renders the empty drop zone chrome without an active document', () => {
    renderChrome()

    expect(screen.getByText('No document loaded')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'PDF drop zone' })).toBeInTheDocument()
    expect(screen.getByText('Drag and drop a PDF here to upload')).toBeInTheDocument()
    expect(screen.getByTitle('PDF Viewer')).toHaveAttribute('src', '/pdfjs/web/viewer.html')
  })

  it('renders document, navigation, and highlight chrome for an active document', () => {
    const navigationResult: PdfViewerNavigationResult = {
      status: 'matched',
      strategy: 'exact-quote',
      locatorQuality: 'exact_quote',
      degraded: false,
      mode: 'select',
      documentId: 'doc-1',
      quote: 'evidence quote',
      pageHints: [3],
      sectionTitle: null,
      matchedQuery: 'evidence quote',
      matchedPage: 3,
      matchesTotal: 1,
      currentMatch: 1,
      attemptedQueries: ['evidence quote'],
      note: 'Matched exact quote.',
    }

    renderChrome({
      activeDocument: {
        documentId: 'doc-1',
        viewerUrl: '/documents/doc-1/viewer',
        filename: 'paper.pdf',
        pageCount: 12,
        loadedAt: '2026-04-27T00:00:00.000Z',
      },
      status: 'ready',
      viewerSrc: '/pdfjs/web/viewer.html?file=/documents/doc-1/viewer',
      highlightTerms: ['gene', 'variant'],
      navigationResult,
      navigationBannerMessage: 'Exact quote matched.',
    })

    expect(screen.getByText('paper.pdf')).toBeInTheDocument()
    expect(screen.getByText(/12 pages/)).toBeInTheDocument()
    expect(screen.getByText(/Serving from/)).toBeInTheDocument()
    expect(screen.getByText('Exact quote')).toBeInTheDocument()
    expect(screen.getByText('Selection sync')).toBeInTheDocument()
    expect(screen.getByText('Page 3')).toBeInTheDocument()
    expect(screen.getByText('gene')).toBeInTheDocument()
    expect(screen.getByText('variant')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous PDF page' })).toBeInTheDocument()
    expect(screen.getByText('1 / 12')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Zoom out' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Zoom in' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Automatic zoom' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Find in PDF' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'PDF drop zone' })).not.toBeInTheDocument()
  })

  it('wires curator PDF toolbar controls to callbacks', () => {
    const onPreviousPage = vi.fn()
    const onNextPage = vi.fn()
    const onZoomOut = vi.fn()
    const onZoomIn = vi.fn()
    const onZoomAuto = vi.fn()
    const onSearchQueryChange = vi.fn()
    const onSearchNext = vi.fn()
    const onSearchPrevious = vi.fn()

    renderChrome({
      activeDocument: {
        documentId: 'doc-1',
        viewerUrl: '/documents/doc-1/viewer',
        filename: 'paper.pdf',
        pageCount: 12,
        loadedAt: '2026-04-27T00:00:00.000Z',
      },
      status: 'ready',
      currentPage: 3,
      searchQuery: 'kinase',
      searchCurrent: 1,
      searchTotal: 4,
      onPreviousPage,
      onNextPage,
      onZoomOut,
      onZoomIn,
      onZoomAuto,
      onSearchQueryChange,
      onSearchNext,
      onSearchPrevious,
    })

    fireEvent.click(screen.getByRole('button', { name: 'Previous PDF page' }))
    fireEvent.click(screen.getByRole('button', { name: 'Next PDF page' }))
    fireEvent.click(screen.getByRole('button', { name: 'Zoom out' }))
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }))
    fireEvent.click(screen.getByRole('button', { name: 'Automatic zoom' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Find in PDF' }), {
      target: { value: 'variant' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Next PDF search match' }))
    fireEvent.click(screen.getByRole('button', { name: 'Previous PDF search match' }))

    expect(onPreviousPage).toHaveBeenCalledTimes(1)
    expect(onNextPage).toHaveBeenCalledTimes(1)
    expect(onZoomOut).toHaveBeenCalledTimes(1)
    expect(onZoomIn).toHaveBeenCalledTimes(1)
    expect(onZoomAuto).toHaveBeenCalledTimes(1)
    expect(onSearchQueryChange).toHaveBeenCalledWith('variant')
    expect(onSearchNext).toHaveBeenCalledTimes(1)
    expect(onSearchPrevious).toHaveBeenCalledTimes(1)
  })
})
