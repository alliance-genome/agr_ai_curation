import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '../../test/test-utils'
import DocumentDownloadDialog from './DocumentDownloadDialog'

describe('DocumentDownloadDialog', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      pdf_available: false,
      pdf_size: null,
      pdfx_json_available: false,
      pdfx_json_size: null,
      processed_json_available: true,
      processed_json_size: 128,
      source_markdown_available: true,
      source_markdown_size: 256,
      viewer_mode: 'text_only',
      filename: 'provider-paper.pdf',
    }), { status: 200 })))
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('shows provider source Markdown as downloadable for text-only documents', async () => {
    render(
      <DocumentDownloadDialog
        open
        documentId="doc-provider"
        onClose={vi.fn()}
      />,
    )

    await waitFor(() => {
      expect(screen.getByText('Source Markdown')).toBeInTheDocument()
    })

    expect(screen.getByText('No local PDF is stored for this provider text import')).toBeInTheDocument()
    expect(screen.getByText('Provider-converted Markdown used for ingestion')).toBeInTheDocument()
    expect(screen.getByText('(256 Bytes)')).toBeInTheDocument()
  })

  it('shows both original PDF and provider source Markdown for PDF-backed provider imports', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      pdf_available: true,
      pdf_size: 1024,
      pdfx_json_available: false,
      pdfx_json_size: null,
      processed_json_available: true,
      processed_json_size: 128,
      source_markdown_available: true,
      source_markdown_size: 256,
      viewer_mode: 'local_pdf',
      filename: 'abc-ready-paper.pdf',
    }), { status: 200 })))

    render(
      <DocumentDownloadDialog
        open
        documentId="doc-provider-pdf"
        onClose={vi.fn()}
      />,
    )

    await waitFor(() => {
      expect(screen.getByText('Original PDF')).toBeInTheDocument()
    })

    expect(screen.getByText('The original uploaded PDF document')).toBeInTheDocument()
    expect(screen.getByText('Source Markdown')).toBeInTheDocument()
    expect(screen.getByText('Provider-converted Markdown used for ingestion')).toBeInTheDocument()
    expect(screen.getByText('(1 KB)')).toBeInTheDocument()
    expect(screen.getByText('(256 Bytes)')).toBeInTheDocument()
    expect(screen.queryByText('No local PDF is stored for this provider text import')).not.toBeInTheDocument()
  })
})
