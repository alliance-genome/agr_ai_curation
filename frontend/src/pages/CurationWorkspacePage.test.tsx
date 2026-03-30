import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { ThemeProvider } from '@mui/material/styles'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { CurationWorkspace } from '@/features/curation/types'
import theme from '@/theme'
import CurationWorkspacePage from './CurationWorkspacePage'

const serviceMocks = vi.hoisted(() => ({
  autosaveCurationCandidateDraft: vi.fn(),
  createManualCurationCandidate: vi.fn(),
  fetchCurationWorkspace: vi.fn(),
  fetchSubmissionPreview: vi.fn(),
  dispatchPDFDocumentChanged: vi.fn(),
  renderPdfViewer: vi.fn(),
  submitCurationCandidateDecision: vi.fn(),
  updateCurationSession: vi.fn(),
  validateCurationCandidate: vi.fn(),
}))

vi.mock('@/features/curation/services/curationWorkspaceService', () => ({
  autosaveCurationCandidateDraft: serviceMocks.autosaveCurationCandidateDraft,
  createManualCurationCandidate: serviceMocks.createManualCurationCandidate,
  fetchCurationWorkspace: serviceMocks.fetchCurationWorkspace,
  fetchSubmissionPreview: serviceMocks.fetchSubmissionPreview,
  submitCurationCandidateDecision: serviceMocks.submitCurationCandidateDecision,
  updateCurationSession: serviceMocks.updateCurationSession,
  validateCurationCandidate: serviceMocks.validateCurationCandidate,
}))

vi.mock('@/components/pdfViewer/pdfEvents', async () => {
  const actual = await vi.importActual<typeof import('@/components/pdfViewer/pdfEvents')>(
    '@/components/pdfViewer/pdfEvents',
  )

  return {
    ...actual,
    dispatchPDFDocumentChanged: serviceMocks.dispatchPDFDocumentChanged,
  }
})

vi.mock('@/components/pdfViewer/PdfViewer', () => ({
  default: () => {
    return <div data-testid="pdf-viewer">PDF viewer</div>
  },
}))

function buildWorkspace(): CurationWorkspace {
  return {
    session: {
      session_id: 'session-1',
      status: 'in_progress',
      adapter: {
        adapter_key: 'entity_adapter',
        display_label: 'Entity',
        profile_label: 'Default',
        color_token: 'green',
        metadata: {},
      },
      document: {
        document_id: 'document-1',
        title: 'Workspace Document',
        pmid: '123456',
        pdf_url: '/api/documents/document-1.pdf',
        viewer_url: '/api/documents/document-1.pdf',
        page_count: 5,
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
    entity_tags: [
      {
        tag_id: 'candidate-accepted',
        entity_name: 'BRCA1',
        entity_type: 'ATP:0000005',
        species: '',
        topic: '',
        db_status: 'validated',
        db_entity_id: 'HGNC:1100',
        source: 'ai',
        decision: 'accepted',
        evidence: null,
        notes: null,
      },
      {
        tag_id: 'candidate-pending',
        entity_name: 'APOE',
        entity_type: 'ATP:0000005',
        species: '',
        topic: '',
        db_status: 'ambiguous',
        db_entity_id: 'HGNC:613',
        source: 'manual',
        decision: 'pending',
        evidence: {
          sentence_text: 'APOE evidence sentence',
          page_number: 3,
          section_title: 'Results',
          chunk_ids: ['chunk-1'],
        },
        notes: null,
      },
    ],
    candidates: [
      {
        candidate_id: 'candidate-accepted',
        session_id: 'session-1',
        source: 'extracted',
        status: 'accepted',
        order: 0,
        adapter_key: 'entity_adapter',
        display_label: 'Accepted candidate',
        draft: {
          draft_id: 'draft-accepted',
          candidate_id: 'candidate-accepted',
          adapter_key: 'entity_adapter',
          version: 1,
          title: 'Accepted candidate draft',
          fields: [
            {
              field_key: 'gene_symbol',
              label: 'Gene symbol',
              value: 'BRCA1',
              seed_value: 'BRCA1',
              field_type: 'string',
              group_key: 'primary_data',
              group_label: 'Primary data',
              order: 0,
              required: true,
              read_only: false,
              dirty: false,
              stale_validation: false,
              evidence_anchor_ids: [],
              validation_result: {
                status: 'validated',
                resolver: 'agr_db',
                candidate_matches: [
                  {
                    label: 'BRCA1',
                    identifier: 'HGNC:1100',
                  },
                ],
                warnings: [],
              },
              metadata: {},
            },
          ],
          created_at: '2026-03-20T12:01:00Z',
          updated_at: '2026-03-20T12:02:00Z',
          metadata: {},
        },
        validation: {
          state: 'completed',
          counts: {
            validated: 1,
            ambiguous: 0,
            not_found: 0,
            invalid_format: 0,
            conflict: 0,
            skipped: 0,
            overridden: 0,
          },
          stale_field_keys: [],
          warnings: [],
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
        adapter_key: 'entity_adapter',
        display_label: 'Pending candidate',
        conversation_summary: 'Needs curator review',
        draft: {
          draft_id: 'draft-pending',
          candidate_id: 'candidate-pending',
          adapter_key: 'entity_adapter',
          version: 1,
          title: 'Pending candidate draft',
          fields: [
            {
              field_key: 'gene_symbol',
              label: 'Gene symbol',
              value: 'APOE',
              seed_value: 'APOE',
              field_type: 'string',
              group_key: 'primary',
              group_label: 'Primary',
              order: 0,
              required: true,
              read_only: false,
              dirty: false,
              stale_validation: false,
              evidence_anchor_ids: ['anchor-1'],
              validation_result: {
                status: 'ambiguous',
                resolver: 'agr_db',
                candidate_matches: [
                  {
                    label: 'APOE',
                    identifier: 'HGNC:613',
                  },
                ],
                warnings: ['Multiple matches'],
              },
              metadata: {},
            },
          ],
          created_at: '2026-03-20T12:03:00Z',
          updated_at: '2026-03-20T12:04:00Z',
          metadata: {},
        },
        validation: {
          state: 'completed',
          counts: {
            validated: 0,
            ambiguous: 1,
            not_found: 0,
            invalid_format: 0,
            conflict: 0,
            skipped: 0,
            overridden: 0,
          },
          stale_field_keys: [],
          warnings: [],
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
              snippet_text: 'APOE evidence sentence',
              viewer_search_text: 'APOE evidence sentence',
              page_number: 3,
              section_title: 'Results',
              chunk_ids: ['chunk-1'],
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
  return (
    <>
      <div data-testid="location">{location.pathname}</div>
      <div data-testid="location-state">{JSON.stringify(location.state)}</div>
    </>
  )
}

function renderPage(initialEntry: string | { pathname: string; state?: unknown }) {
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
      <ThemeProvider theme={theme}>
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
    serviceMocks.autosaveCurationCandidateDraft.mockReset()
    serviceMocks.createManualCurationCandidate.mockReset()
    serviceMocks.fetchCurationWorkspace.mockReset()
    serviceMocks.fetchSubmissionPreview.mockReset()
    serviceMocks.dispatchPDFDocumentChanged.mockReset()
    serviceMocks.renderPdfViewer.mockReset()
    serviceMocks.submitCurationCandidateDecision.mockReset()
    serviceMocks.updateCurationSession.mockReset()
    serviceMocks.validateCurationCandidate.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('renders backend-provided entity tag rows from the workspace payload', async () => {
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(buildWorkspace())

    renderPage('/curation/session-1')

    await waitFor(() => {
      expect(serviceMocks.fetchCurationWorkspace).toHaveBeenCalledWith('session-1')
    })

    await waitFor(() => {
      expect(screen.getByTestId('workspace-shell')).toBeInTheDocument()
    })

    expect(
      screen.getByRole('region', { name: /entity table panel/i }),
    ).toBeInTheDocument()
    expect(screen.getByTestId('pdf-viewer')).toBeInTheDocument()

    expect(screen.getAllByText('BRCA1').length).toBeGreaterThan(0)
    expect(screen.getAllByText('APOE').length).toBeGreaterThan(0)
    expect(screen.getByText('validated')).toBeInTheDocument()
    expect(screen.getByText('ambiguous')).toBeInTheDocument()
  })

  it('renders the workspace header with document info', async () => {
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(buildWorkspace())

    renderPage('/curation/session-1')

    await waitFor(() => {
      expect(screen.getByText('Workspace Document')).toBeInTheDocument()
    })

    expect(screen.getByText('PMID 123456')).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: /back to inventory/i }),
    ).toHaveAttribute('href', '/curation')
  })

  it('initializes the PDF viewer document after hydration', async () => {
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(buildWorkspace())

    renderPage('/curation/session-1/candidate-accepted')

    await waitFor(() => {
      expect(serviceMocks.dispatchPDFDocumentChanged).toHaveBeenCalledWith(
        'document-1',
        '/api/documents/document-1.pdf',
        'Workspace Document',
        5,
        undefined,
      )
    })
  })

  it('restores the route-selected entity row into the evidence pane', async () => {
    const workspace = buildWorkspace()
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(workspace)
    serviceMocks.updateCurationSession.mockResolvedValue({
      session: {
        ...workspace.session,
        current_candidate_id: 'candidate-pending',
      },
      action_log_entry: null,
    })

    renderPage('/curation/session-1/candidate-pending')

    await waitFor(() => {
      expect(
        screen.getByText((_, element) =>
          element?.tagName.toLowerCase() === 'p' &&
          (element.textContent?.includes('APOE evidence sentence') ?? false),
        ),
      ).toBeInTheDocument()
    })

    expect(screen.getByText(/Evidence for/i)).toBeInTheDocument()
    expect(screen.getByText('Show in PDF')).toBeInTheDocument()
  })

  it('submits inline accept actions through the workspace decision service', async () => {
    const workspace = buildWorkspace()
    const refreshedWorkspace: CurationWorkspace = {
      ...workspace,
      candidates: workspace.candidates.map((candidate) =>
        candidate.candidate_id === 'candidate-pending'
          ? {
              ...candidate,
              status: 'accepted',
            }
          : candidate,
      ),
      entity_tags: workspace.entity_tags.map((tag) =>
        tag.tag_id === 'candidate-pending'
          ? {
              ...tag,
              decision: 'accepted',
            }
          : tag,
      ),
    }
    serviceMocks.fetchCurationWorkspace
      .mockResolvedValueOnce(workspace)
      .mockResolvedValueOnce(refreshedWorkspace)
    serviceMocks.submitCurationCandidateDecision.mockResolvedValue({
      candidate: {
        ...workspace.candidates[1],
        status: 'accepted',
      },
      session: {
        ...workspace.session,
        current_candidate_id: 'candidate-pending',
      },
      next_candidate_id: null,
      action_log_entry: {
        action_id: 'action-1',
        session_id: workspace.session.session_id,
        candidate_id: 'candidate-pending',
        action_type: 'candidate_accepted',
        actor_type: 'user',
        occurred_at: '2026-03-30T12:00:00Z',
        changed_field_keys: [],
        evidence_anchor_ids: [],
        metadata: {},
      },
    })

    renderPage('/curation/session-1')

    await waitFor(() => {
      expect(screen.getByText('APOE')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Accept' }))

    await waitFor(() => {
      expect(serviceMocks.submitCurationCandidateDecision).toHaveBeenCalledWith({
        session_id: 'session-1',
        candidate_id: 'candidate-pending',
        action: 'accept',
        advance_queue: false,
      })
      expect(screen.getAllByText('Accepted').length).toBeGreaterThan(0)
    })
  })

  it('shows loading state while workspace is being fetched', () => {
    serviceMocks.fetchCurationWorkspace.mockReturnValue(new Promise(() => {}))

    renderPage('/curation/session-1')

    expect(screen.getByText('Loading curation workspace...')).toBeInTheDocument()
  })

  it('shows error state when workspace fetch fails', async () => {
    serviceMocks.fetchCurationWorkspace.mockRejectedValue(
      new Error('Network timeout'),
    )

    renderPage('/curation/session-1')

    await waitFor(() => {
      expect(screen.getByText('Network timeout')).toBeInTheDocument()
    })

    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('preserves location state across route normalization', async () => {
    const workspace = buildWorkspace()
    workspace.active_candidate_id = null
    workspace.session.current_candidate_id = null

    serviceMocks.fetchCurationWorkspace.mockResolvedValue(workspace)
    serviceMocks.updateCurationSession.mockResolvedValue({
      session: {
        ...workspace.session,
        current_candidate_id: 'candidate-pending',
      },
      action_log_entry: null,
    })

    renderPage({
      pathname: '/curation/session-1',
      state: {
        launchedFromInventory: true,
        note: 'preserve-this-state',
      },
    })

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/curation/session-1/candidate-pending',
      )
    })

    expect(screen.getByTestId('location-state')).toHaveTextContent('"launchedFromInventory":true')
    expect(screen.getByTestId('location-state')).toHaveTextContent('"note":"preserve-this-state"')
  })
})
