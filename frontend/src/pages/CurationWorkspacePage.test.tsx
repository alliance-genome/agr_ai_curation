import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { ThemeProvider } from '@mui/material/styles'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { CurationWorkspace } from '@/features/curation/types'
import theme from '@/theme'
import CurationWorkspacePage from './CurationWorkspacePage'

const serviceMocks = vi.hoisted(() => ({
  autosaveCurationCandidateDraft: vi.fn(),
  fetchCurationWorkspace: vi.fn(),
  dispatchPDFDocumentChanged: vi.fn(),
  renderPdfViewer: vi.fn(),
  updateCurationSession: vi.fn(),
}))

vi.mock('@/features/curation/services/curationWorkspaceService', () => ({
  autosaveCurationCandidateDraft: serviceMocks.autosaveCurationCandidateDraft,
  fetchCurationWorkspace: serviceMocks.fetchCurationWorkspace,
  updateCurationSession: serviceMocks.updateCurationSession,
}))

vi.mock('@/components/pdfViewer/pdfEvents', () => ({
  dispatchPDFDocumentChanged: serviceMocks.dispatchPDFDocumentChanged,
}))

vi.mock('@/components/pdfViewer/PdfViewer', () => ({
  default: (props: MockPdfViewerProps) => {
    serviceMocks.renderPdfViewer(props)
    return <div data-testid="pdf-viewer">PDF viewer</div>
  },
}))

type MockPdfViewerProps = {
  pendingNavigation?: {
    mode?: string
    pageNumber?: number | null
    sectionTitle?: string | null
    searchText?: string | null
  } | null
  onNavigationComplete?: () => void
}

function getLatestPdfViewerProps(): MockPdfViewerProps {
  const latestCall = serviceMocks.renderPdfViewer.mock.lastCall

  return (latestCall?.[0] as MockPdfViewerProps) ?? {}
}

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
        adapter_key: 'entity_adapter',
        display_label: 'Accepted candidate',
        unresolved_ambiguities: [],
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
              metadata: {},
            },
          ],
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
        adapter_key: 'entity_adapter',
        display_label: 'Pending candidate',
        conversation_summary: 'Needs curator review',
        unresolved_ambiguities: [],
        draft: {
          draft_id: 'draft-pending',
          candidate_id: 'candidate-pending',
          adapter_key: 'entity_adapter',
          version: 1,
          title: 'Pending candidate draft',
          fields: [
            {
              field_key: 'field_a',
              label: 'Primary term',
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
              metadata: {},
            },
          ],
          created_at: '2026-03-20T12:03:00Z',
          updated_at: '2026-03-20T12:04:00Z',
          metadata: {},
        },
        evidence_anchors: [
          {
            anchor_id: 'anchor-1',
            candidate_id: 'candidate-pending',
            source: 'manual',
            field_keys: ['field_a'],
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
    serviceMocks.fetchCurationWorkspace.mockReset()
    serviceMocks.dispatchPDFDocumentChanged.mockReset()
    serviceMocks.renderPdfViewer.mockReset()
    serviceMocks.updateCurationSession.mockReset()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('restores the session-selected candidate by default and renders the queue', async () => {
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(buildWorkspace())

    renderPage('/curation/session-1')

    await waitFor(() => {
      expect(serviceMocks.fetchCurationWorkspace).toHaveBeenCalledWith('session-1')
    })

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/curation/session-1/candidate-accepted',
      )
    })

    expect(screen.getByText('Candidates (2)')).toBeInTheDocument()
    expect(screen.getByText('Accepted candidate draft')).toBeInTheDocument()
    expect(screen.getByText('PRIMARY DATA')).toBeInTheDocument()
    expect(screen.getByLabelText('Gene symbol')).toHaveValue('BRCA1')
    expect(screen.getByTestId('workspace-shell-editor-panel')).toBeInTheDocument()
    expect(screen.getByText('Evidence Anchors (0)')).toBeInTheDocument()
    expect(
      screen.getByText('No evidence anchors are available for this candidate.'),
    ).toBeInTheDocument()
    expect(screen.getAllByText('1/2 reviewed')).toHaveLength(2)
    expect(
      screen.getByRole('link', { name: /back to inventory/i }),
    ).toHaveAttribute('href', '/curation')
    expect(
      screen.getByText(
        'Queue navigation is available when you open a session from the inventory queue.',
      ),
    ).toBeInTheDocument()
  })

  it('honors an explicit candidate id and initializes the PDF viewer document', async () => {
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(buildWorkspace())

    renderPage('/curation/session-1/candidate-accepted')

    await waitFor(() => {
      expect(screen.getAllByText('Accepted candidate')).toHaveLength(1)
    })

    expect(screen.getByTestId('location')).toHaveTextContent(
      '/curation/session-1/candidate-accepted',
    )
    expect(screen.getByTestId('location-state')).toHaveTextContent('null')
    expect(screen.getByTestId('pdf-viewer')).toBeInTheDocument()
    expect(screen.getByText('Workspace Document')).toBeInTheDocument()
    expect(screen.getByText('PMID 123456')).toBeInTheDocument()
    expect(screen.getByText('Decision Toolbar')).toBeInTheDocument()

    await waitFor(() => {
      expect(serviceMocks.dispatchPDFDocumentChanged).toHaveBeenCalledWith(
        'document-1',
        '/api/documents/document-1.pdf',
        'Workspace Document',
        0,
        undefined,
      )
    })
  })

  it('updates the route when a queue card is selected and forwards hover/select navigation to the PDF viewer', async () => {
    const user = userEvent.setup()

    serviceMocks.fetchCurationWorkspace.mockResolvedValue(buildWorkspace())
    serviceMocks.updateCurationSession.mockResolvedValue({
      session: {
        ...buildWorkspace().session,
        current_candidate_id: 'candidate-pending',
      },
      action_log_entry: null,
    })

    renderPage('/curation/session-1')

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/curation/session-1/candidate-accepted',
      )
    })

    await user.click(screen.getByTestId('candidate-queue-card-candidate-pending'))

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/curation/session-1/candidate-pending',
      )
    })

    expect(screen.getByText('Evidence Anchors (1)')).toBeInTheDocument()
    expect(screen.getByText('APOE evidence sentence')).toBeInTheDocument()

    const evidenceCard = screen.getByTestId('evidence-card-anchor-1')

    await user.hover(evidenceCard)
    fireEvent.focus(evidenceCard)

    expect(getLatestPdfViewerProps().pendingNavigation).toMatchObject({
      mode: 'hover',
      pageNumber: 3,
      sectionTitle: 'Results',
      searchText: 'APOE evidence sentence',
    })

    await user.click(evidenceCard)

    await waitFor(() => {
      expect(getLatestPdfViewerProps().pendingNavigation).toMatchObject({
        mode: 'select',
        pageNumber: 3,
        sectionTitle: 'Results',
        searchText: 'APOE evidence sentence',
      })
      expect(getLatestPdfViewerProps().onNavigationComplete).toEqual(expect.any(Function))
    })

    await act(async () => {
      getLatestPdfViewerProps().onNavigationComplete?.()
    })

    await waitFor(() => {
      expect(getLatestPdfViewerProps().pendingNavigation).toBeNull()
    })
  })

  it('forwards chip hover and click events to PDF navigation', async () => {
    const user = userEvent.setup()
    const workspace = buildWorkspace()
    const candidate = workspace.candidates[1]
    candidate.draft.fields = [
      {
        field_key: 'field-a',
        label: 'Field A',
        order: 0,
        required: false,
        read_only: false,
        dirty: false,
        stale_validation: false,
        evidence_anchor_ids: ['anchor-1'],
        validation_result: null,
        metadata: {},
      },
    ]

    serviceMocks.fetchCurationWorkspace.mockResolvedValue(workspace)
    serviceMocks.updateCurationSession.mockResolvedValue({
      session: {
        ...workspace.session,
        current_candidate_id: 'candidate-pending',
      },
      action_log_entry: null,
    })

    renderPage('/curation/session-1')

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/curation/session-1/candidate-accepted',
      )
    })

    await user.click(screen.getByTestId('candidate-queue-card-candidate-pending'))

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/curation/session-1/candidate-pending',
      )
    })

    await waitFor(() => {
      expect(screen.getByText('Evidence Anchors (1)')).toBeInTheDocument()
      expect(screen.getByText('APOE evidence sentence')).toBeInTheDocument()
      expect(screen.getByTestId('evidence-chip-anchor-1')).toBeInTheDocument()
    })

    const chip = screen.getByTestId('evidence-chip-anchor-1')

    await user.hover(chip)

    expect(getLatestPdfViewerProps().pendingNavigation).toMatchObject({
      mode: 'hover',
      pageNumber: 3,
      sectionTitle: 'Results',
      searchText: 'APOE evidence sentence',
    })

    await user.unhover(chip)

    expect(getLatestPdfViewerProps().pendingNavigation).toBeNull()

    await user.click(chip)

    await waitFor(() => {
      expect(getLatestPdfViewerProps().pendingNavigation).toMatchObject({
        mode: 'select',
        pageNumber: 3,
        sectionTitle: 'Results',
        searchText: 'APOE evidence sentence',
      })
      expect(getLatestPdfViewerProps().onNavigationComplete).toEqual(expect.any(Function))
    })

    await act(async () => {
      getLatestPdfViewerProps().onNavigationComplete?.()
    })

    await waitFor(() => {
      expect(getLatestPdfViewerProps().pendingNavigation).toBeNull()
    })
  })

  it('preserves location state when it normalizes the candidate route', async () => {
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
    await waitFor(() => {
      expect(serviceMocks.updateCurationSession).toHaveBeenCalledWith({
        session_id: 'session-1',
        current_candidate_id: 'candidate-pending',
      })
    })

    expect(screen.getByTestId('location-state')).toHaveTextContent('"launchedFromInventory":true')
    expect(screen.getByTestId('location-state')).toHaveTextContent('"note":"preserve-this-state"')
  })
})
