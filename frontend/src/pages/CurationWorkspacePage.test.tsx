import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { ThemeProvider, createTheme } from '@mui/material/styles'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { CurationWorkspace } from '@/features/curation/types'
import CurationWorkspacePage from './CurationWorkspacePage'

const serviceMocks = vi.hoisted(() => ({
  fetchCurationWorkspace: vi.fn(),
  dispatchPDFDocumentChanged: vi.fn(),
}))

vi.mock('@/features/curation/services/curationWorkspaceService', () => ({
  fetchCurationWorkspace: serviceMocks.fetchCurationWorkspace,
}))

vi.mock('@/components/pdfViewer/pdfEvents', () => ({
  dispatchPDFDocumentChanged: serviceMocks.dispatchPDFDocumentChanged,
}))

vi.mock('@/components/pdfViewer/PdfViewer', () => ({
  default: () => <div data-testid="pdf-viewer">PDF viewer</div>,
}))

function buildWorkspace(): CurationWorkspace {
  return {
    session: {
      session_id: 'session-1',
      status: 'in_progress',
      adapter: {
        adapter_key: 'gene',
        display_label: 'Gene',
        metadata: {},
      },
      document: {
        document_id: 'document-1',
        title: 'Workspace Document',
        pdf_url: '/api/documents/document-1.pdf',
        viewer_url: '/api/documents/document-1.pdf',
      },
      progress: {
        total_candidates: 2,
        reviewed_candidates: 1,
        pending_candidates: 1,
        accepted_candidates: 1,
        rejected_candidates: 0,
        manual_candidates: 0,
      },
      current_candidate_id: 'candidate-accepted',
      prepared_at: '2026-03-20T12:00:00Z',
      warnings: [],
      tags: [],
      session_version: 1,
      extraction_results: [],
    },
    candidates: [
      {
        candidate_id: 'candidate-accepted',
        session_id: 'session-1',
        source: 'extracted',
        status: 'accepted',
        order: 0,
        adapter_key: 'gene',
        display_label: 'Accepted candidate',
        unresolved_ambiguities: [],
        draft: {
          draft_id: 'draft-accepted',
          candidate_id: 'candidate-accepted',
          adapter_key: 'gene',
          version: 1,
          fields: [],
          created_at: '2026-03-20T12:01:00Z',
          updated_at: '2026-03-20T12:02:00Z',
          metadata: {},
        },
        evidence_anchors: [],
        created_at: '2026-03-20T12:01:00Z',
        updated_at: '2026-03-20T12:02:00Z',
        metadata: {},
      },
      {
        candidate_id: 'candidate-pending',
        session_id: 'session-1',
        source: 'manual',
        status: 'pending',
        order: 1,
        adapter_key: 'gene',
        display_label: 'Pending candidate',
        conversation_summary: 'Needs curator review',
        unresolved_ambiguities: [],
        draft: {
          draft_id: 'draft-pending',
          candidate_id: 'candidate-pending',
          adapter_key: 'gene',
          version: 1,
          fields: [],
          created_at: '2026-03-20T12:03:00Z',
          updated_at: '2026-03-20T12:04:00Z',
          metadata: {},
        },
        evidence_anchors: [
          {
            anchor_id: 'anchor-1',
            candidate_id: 'candidate-pending',
            source: 'manual',
            field_keys: ['gene_symbol'],
            field_group_keys: ['primary'],
            is_primary: true,
            anchor: {
              anchor_kind: 'snippet',
              locator_quality: 'exact_quote',
              supports_decision: 'supports',
            },
            created_at: '2026-03-20T12:03:00Z',
            updated_at: '2026-03-20T12:04:00Z',
            warnings: [],
          },
        ],
        created_at: '2026-03-20T12:03:00Z',
        updated_at: '2026-03-20T12:04:00Z',
        metadata: {},
      },
    ],
    active_candidate_id: 'candidate-accepted',
    queue_context: null,
    action_log: [],
    submission_history: [],
    saved_view_context: null,
  }
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname}</div>
}

function renderPage(initialEntry: string) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={createTheme()}>
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route
              path="/curation/:sessionId"
              element={(
                <>
                  <CurationWorkspacePage />
                  <LocationProbe />
                </>
              )}
            />
            <Route
              path="/curation/:sessionId/:candidateId"
              element={(
                <>
                  <CurationWorkspacePage />
                  <LocationProbe />
                </>
              )}
            />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  )
}

describe('CurationWorkspacePage', () => {
  beforeEach(() => {
    serviceMocks.fetchCurationWorkspace.mockReset()
    serviceMocks.dispatchPDFDocumentChanged.mockReset()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('defaults to the first pending candidate and rewrites the route', async () => {
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(buildWorkspace())

    renderPage('/curation/session-1')

    await waitFor(() => {
      expect(serviceMocks.fetchCurationWorkspace).toHaveBeenCalledWith('session-1')
    })

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/curation/session-1/candidate-pending',
      )
    })

    expect(screen.getByText('Pending candidate')).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: /back to inventory/i }),
    ).toHaveAttribute('href', '/curation')
  })

  it('honors an explicit candidate id and initializes the PDF viewer document', async () => {
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(buildWorkspace())

    renderPage('/curation/session-1/candidate-accepted')

    await waitFor(() => {
      expect(screen.getByText('Accepted candidate')).toBeInTheDocument()
    })

    expect(screen.getByTestId('location')).toHaveTextContent(
      '/curation/session-1/candidate-accepted',
    )
    expect(screen.getByTestId('pdf-viewer')).toBeInTheDocument()

    await waitFor(() => {
      expect(serviceMocks.dispatchPDFDocumentChanged).toHaveBeenCalledWith(
        'document-1',
        '/api/documents/document-1.pdf',
        'Workspace Document',
        0,
      )
    })
  })
})
