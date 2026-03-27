import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { ThemeProvider } from '@mui/material/styles'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { dispatchPDFViewerEvidenceAnchorSelected } from '@/components/pdfViewer/pdfEvents'
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
}))

vi.mock('@/features/curation/services/curationWorkspaceService', () => ({
  autosaveCurationCandidateDraft: serviceMocks.autosaveCurationCandidateDraft,
  createManualCurationCandidate: serviceMocks.createManualCurationCandidate,
  fetchCurationWorkspace: serviceMocks.fetchCurationWorkspace,
  fetchSubmissionPreview: serviceMocks.fetchSubmissionPreview,
  submitCurationCandidateDecision: serviceMocks.submitCurationCandidateDecision,
  updateCurationSession: serviceMocks.updateCurationSession,
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

function buildReferenceWorkspace(): CurationWorkspace {
  return {
    session: {
      session_id: 'session-1',
      status: 'in_progress',
      adapter: {
        adapter_key: 'reference',
        display_label: 'Reference',
        profile_label: null,
        color_token: 'teal',
        metadata: {},
      },
      document: {
        document_id: 'document-1',
        title: 'Reference Workspace Document',
        pmid: '123456',
        pdf_url: '/api/documents/document-1.pdf',
        viewer_url: '/api/documents/document-1.pdf',
      },
      progress: {
        total_candidates: 1,
        reviewed_candidates: 0,
        pending_candidates: 1,
        accepted_candidates: 0,
        rejected_candidates: 0,
        manual_candidates: 0,
      },
      current_candidate_id: 'candidate-reference',
      prepared_at: '2026-03-20T12:00:00Z',
      warnings: [],
      tags: [],
      session_version: 1,
      extraction_results: [],
    },
    candidates: [
      {
        candidate_id: 'candidate-reference',
        session_id: 'session-1',
        source: 'extracted',
        status: 'pending',
        order: 0,
        adapter_key: 'reference',
        display_label: 'Adapter-owned reference scaffold in practice',
        conversation_summary: 'Reference adapter owns the editor pack and field layout.',
        unresolved_ambiguities: [],
        draft: {
          draft_id: 'draft-reference',
          candidate_id: 'candidate-reference',
          adapter_key: 'reference',
          version: 1,
          title: 'Reference draft',
          fields: [
            {
              field_key: 'citation.title',
              label: 'Title',
              value: 'Adapter-owned reference scaffold in practice',
              seed_value: 'Adapter-owned reference scaffold in practice',
              field_type: 'string',
              group_key: 'citation_details',
              group_label: 'Citation details',
              order: 0,
              required: true,
              read_only: false,
              dirty: false,
              stale_validation: false,
              evidence_anchor_ids: [],
              metadata: {},
            },
            {
              field_key: 'citation.authors',
              label: 'Authors',
              value: ['Ada Lovelace', 'Grace Hopper'],
              seed_value: ['Ada Lovelace', 'Grace Hopper'],
              field_type: 'json',
              group_key: 'citation_details',
              group_label: 'Citation details',
              order: 10,
              required: false,
              read_only: false,
              dirty: false,
              stale_validation: false,
              evidence_anchor_ids: [],
              metadata: {
                widget: 'reference_author_list',
                helper_text: 'One author per line.',
                placeholder: 'Ada Lovelace\nGrace Hopper',
              },
            },
            {
              field_key: 'citation.reference_type',
              label: 'Reference type',
              value: 'journal_article',
              seed_value: 'journal_article',
              field_type: 'string',
              group_key: 'citation_details',
              group_label: 'Citation details',
              order: 40,
              required: true,
              read_only: false,
              dirty: false,
              stale_validation: false,
              evidence_anchor_ids: [],
              metadata: {
                options: [
                  {
                    label: 'Journal article',
                    value: 'journal_article',
                  },
                  {
                    label: 'Review article',
                    value: 'review_article',
                  },
                ],
              },
            },
            {
              field_key: 'identifiers.doi',
              label: 'DOI',
              value: '10.1000/reference-1',
              seed_value: '10.1000/reference-1',
              field_type: 'string',
              group_key: 'identifiers',
              group_label: 'Identifiers',
              order: 100,
              required: false,
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
    ],
    active_candidate_id: 'candidate-reference',
    queue_context: null,
    action_log: [],
    submission_history: [],
    saved_view_context: null,
  }
}

function buildQueueWorkspace(): CurationWorkspace {
  const workspace = buildWorkspace()

  workspace.session.progress = {
    total_candidates: 3,
    reviewed_candidates: 1,
    pending_candidates: 2,
    accepted_candidates: 1,
    rejected_candidates: 0,
    manual_candidates: 1,
  }
  workspace.session.current_candidate_id = 'candidate-pending'
  workspace.active_candidate_id = 'candidate-pending'
  workspace.candidates.push({
    candidate_id: 'candidate-next',
    session_id: 'session-1',
    source: 'extracted',
    status: 'pending',
    order: 2,
    adapter_key: 'entity_adapter',
    display_label: 'Next candidate',
    unresolved_ambiguities: [],
    draft: {
      draft_id: 'draft-next',
      candidate_id: 'candidate-next',
      adapter_key: 'entity_adapter',
      version: 1,
      title: 'Next candidate draft',
      fields: [
        {
          field_key: 'field_b',
          label: 'Secondary term',
          value: 'CLU',
          seed_value: 'CLU',
          field_type: 'string',
          group_key: 'secondary',
          group_label: 'Secondary',
          order: 0,
          required: false,
          read_only: false,
          dirty: false,
          stale_validation: false,
          evidence_anchor_ids: [],
          metadata: {},
        },
      ],
      created_at: '2026-03-20T12:05:00Z',
      updated_at: '2026-03-20T12:06:00Z',
      metadata: {},
    },
    evidence_anchors: [],
    created_at: '2026-03-20T12:05:00Z',
    updated_at: '2026-03-20T12:06:00Z',
    metadata: {},
  })

  return workspace
}

function buildDecisionResponse({
  workspace,
  candidateId,
  nextCandidateId,
  actionType,
  newStatus,
  reason = null,
}: {
  workspace: CurationWorkspace
  candidateId: string
  nextCandidateId?: string | null
  actionType: 'candidate_accepted' | 'candidate_rejected' | 'candidate_reset'
  newStatus: 'accepted' | 'rejected' | 'pending'
  reason?: string | null
}) {
  const updatedCandidates = workspace.candidates.map((candidate) =>
    candidate.candidate_id === candidateId
      ? {
          ...candidate,
          status: newStatus,
          last_reviewed_at: '2026-03-21T09:00:00Z',
          updated_at: '2026-03-21T09:00:00Z',
        }
      : candidate,
  )
  const pendingCount = updatedCandidates.filter((candidate) => candidate.status === 'pending').length
  const acceptedCount = updatedCandidates.filter((candidate) => candidate.status === 'accepted').length
  const rejectedCount = updatedCandidates.filter((candidate) => candidate.status === 'rejected').length
  const updatedSession = {
    ...workspace.session,
    current_candidate_id: nextCandidateId ?? candidateId,
    session_version: workspace.session.session_version + 1,
    progress: {
      ...workspace.session.progress,
      reviewed_candidates: updatedCandidates.length - pendingCount,
      pending_candidates: pendingCount,
      accepted_candidates: acceptedCount,
      rejected_candidates: rejectedCount,
    },
  }

  return {
    candidate: updatedCandidates.find((candidate) => candidate.candidate_id === candidateId)!,
    session: updatedSession,
    next_candidate_id: nextCandidateId ?? null,
    action_log_entry: {
      action_id: `action-${actionType}-${candidateId}`,
      session_id: workspace.session.session_id,
      candidate_id: candidateId,
      action_type: actionType,
      actor_type: 'user',
      actor: {
        actor_id: 'user-1',
        display_name: 'Curator One',
      },
      occurred_at: '2026-03-21T09:00:00Z',
      previous_candidate_status: workspace.candidates.find(
        (candidate) => candidate.candidate_id === candidateId,
      )?.status ?? 'pending',
      new_candidate_status: newStatus,
      changed_field_keys: [],
      evidence_anchor_ids: [],
      reason,
      message: `Candidate marked as ${newStatus}`,
      metadata: {},
    },
  }
}

function buildSubmissionPreviewResponse() {
  return {
    submission: {
      submission_id: 'submission-preview-1',
      session_id: 'session-1',
      adapter_key: 'entity_adapter',
      mode: 'preview',
      target_key: 'review_export_bundle',
      status: 'preview_ready',
      readiness: [
        {
          candidate_id: 'candidate-accepted',
          ready: true,
          blocking_reasons: [],
          warnings: [],
        },
        {
          candidate_id: 'candidate-pending',
          ready: false,
          blocking_reasons: ['Candidate is still pending curator review.'],
          warnings: [],
        },
      ],
      payload: {
        mode: 'preview',
        target_key: 'review_export_bundle',
        adapter_key: 'entity_adapter',
        candidate_ids: ['candidate-accepted'],
        payload_json: {
          candidate_count: 1,
          candidates: ['candidate-accepted'],
        },
        warnings: [],
      },
      requested_at: '2026-03-21T09:00:00Z',
      completed_at: '2026-03-21T09:00:01Z',
      validation_errors: [],
      warnings: [],
    },
    session_validation: {
      snapshot_id: 'session-validation-1',
      scope: 'session',
      session_id: 'session-1',
      adapter_key: 'entity_adapter',
      state: 'completed',
      field_results: {},
      summary: {
        state: 'completed',
        counts: {
          validated: 0,
          ambiguous: 0,
          not_found: 0,
          invalid_format: 0,
          conflict: 0,
          skipped: 1,
          overridden: 0,
        },
        warnings: [],
        stale_field_keys: [],
        last_validated_at: '2026-03-21T09:00:00Z',
      },
      requested_at: '2026-03-21T09:00:00Z',
      completed_at: '2026-03-21T09:00:01Z',
      warnings: [],
    },
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
  const originalScrollIntoView = HTMLElement.prototype.scrollIntoView
  let scrollIntoViewMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    serviceMocks.autosaveCurationCandidateDraft.mockReset()
    serviceMocks.createManualCurationCandidate.mockReset()
    serviceMocks.fetchCurationWorkspace.mockReset()
    serviceMocks.fetchSubmissionPreview.mockReset()
    serviceMocks.dispatchPDFDocumentChanged.mockReset()
    serviceMocks.renderPdfViewer.mockReset()
    serviceMocks.submitCurationCandidateDecision.mockReset()
    serviceMocks.updateCurationSession.mockReset()
    scrollIntoViewMock = vi.fn()
    HTMLElement.prototype.scrollIntoView = scrollIntoViewMock
  })

  afterEach(() => {
    HTMLElement.prototype.scrollIntoView = originalScrollIntoView
    vi.useRealTimers()
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
    expect(screen.getByText('Decision toolbar')).toBeInTheDocument()
    expect(
      screen.getByText('Candidate 1 of 2 — Entity / Accepted candidate'),
    ).toBeInTheDocument()

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

  it('uses the reference adapter editor pack for adapter-owned author widgets', async () => {
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(buildReferenceWorkspace())

    renderPage('/curation/session-1/candidate-reference')

    const authorsInput = await screen.findByLabelText('Authors')

    expect(screen.getByText('CITATION DETAILS')).toBeInTheDocument()
    expect(screen.getByText('IDENTIFIERS')).toBeInTheDocument()
    expect(screen.getByText('One author per line.')).toBeInTheDocument()
    expect(authorsInput).toHaveValue('Ada Lovelace\nGrace Hopper')
  })

  it('submits accept decisions and advances to the next pending candidate', async () => {
    const user = userEvent.setup()
    const workspace = buildQueueWorkspace()

    serviceMocks.fetchCurationWorkspace.mockResolvedValue(workspace)
    serviceMocks.submitCurationCandidateDecision.mockResolvedValue(
      buildDecisionResponse({
        workspace,
        candidateId: 'candidate-pending',
        nextCandidateId: 'candidate-next',
        actionType: 'candidate_accepted',
        newStatus: 'accepted',
      }),
    )
    serviceMocks.updateCurationSession.mockResolvedValue({
      session: {
        ...workspace.session,
        current_candidate_id: 'candidate-next',
      },
      action_log_entry: null,
    })

    renderPage('/curation/session-1/candidate-pending')

    await waitFor(() => {
      expect(screen.getByText('Pending candidate draft')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: '✓ Accept' }))

    await waitFor(() => {
      expect(serviceMocks.submitCurationCandidateDecision).toHaveBeenCalledWith({
        session_id: 'session-1',
        candidate_id: 'candidate-pending',
        action: 'accept',
        reason: undefined,
        advance_queue: true,
      })
    })

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/curation/session-1/candidate-next',
      )
    })
    expect(screen.getByText('Next candidate draft')).toBeInTheDocument()
  })

  it('skips to the next candidate without submitting any decision', async () => {
    const user = userEvent.setup()
    const workspace = buildQueueWorkspace()

    serviceMocks.fetchCurationWorkspace.mockResolvedValue(workspace)
    serviceMocks.updateCurationSession.mockResolvedValue({
      session: {
        ...workspace.session,
        current_candidate_id: 'candidate-next',
      },
      action_log_entry: null,
    })

    renderPage('/curation/session-1/candidate-pending')

    await waitFor(() => {
      expect(screen.getByText('Pending candidate draft')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Skip →' }))

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/curation/session-1/candidate-next',
      )
    })

    expect(screen.getByText('Next candidate draft')).toBeInTheDocument()
    expect(serviceMocks.submitCurationCandidateDecision).not.toHaveBeenCalled()
  })

  it('collects an optional reject reason before submitting the decision', async () => {
    const user = userEvent.setup()
    const workspace = buildQueueWorkspace()

    serviceMocks.fetchCurationWorkspace.mockResolvedValue(workspace)
    serviceMocks.submitCurationCandidateDecision.mockResolvedValue(
      buildDecisionResponse({
        workspace,
        candidateId: 'candidate-pending',
        nextCandidateId: 'candidate-next',
        actionType: 'candidate_rejected',
        newStatus: 'rejected',
        reason: 'Not supported by evidence.',
      }),
    )
    serviceMocks.updateCurationSession.mockResolvedValue({
      session: {
        ...workspace.session,
        current_candidate_id: 'candidate-next',
      },
      action_log_entry: null,
    })

    renderPage('/curation/session-1/candidate-pending')

    await waitFor(() => {
      expect(screen.getByText('Pending candidate draft')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: '✕ Reject' }))

    expect(screen.getByText('Reject candidate?')).toBeInTheDocument()

    await user.type(
      screen.getByLabelText('Reason (optional)'),
      'Not supported by evidence.',
    )
    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: /^Reject$/ }),
    )

    await waitFor(() => {
      expect(serviceMocks.submitCurationCandidateDecision).toHaveBeenCalledWith({
        session_id: 'session-1',
        candidate_id: 'candidate-pending',
        action: 'reject',
        reason: 'Not supported by evidence.',
        advance_queue: true,
      })
    })
  })

  it('requires reset confirmation before submitting the reset action', async () => {
    const user = userEvent.setup()
    const workspace = buildWorkspace()

    serviceMocks.fetchCurationWorkspace.mockResolvedValue(workspace)
    serviceMocks.submitCurationCandidateDecision.mockResolvedValue(
      buildDecisionResponse({
        workspace,
        candidateId: 'candidate-accepted',
        actionType: 'candidate_reset',
        newStatus: 'pending',
      }),
    )

    renderPage('/curation/session-1/candidate-accepted')

    await waitFor(() => {
      expect(screen.getByText('Accepted candidate draft')).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Reset' }))

    expect(screen.getByText('Reset candidate?')).toBeInTheDocument()
    expect(serviceMocks.submitCurationCandidateDecision).not.toHaveBeenCalled()

    await user.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: /^Reset$/ }),
    )

    await waitFor(() => {
      expect(serviceMocks.submitCurationCandidateDecision).toHaveBeenCalledWith({
        session_id: 'session-1',
        candidate_id: 'candidate-accepted',
        action: 'reset',
        reason: undefined,
        advance_queue: false,
      })
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
  }, 10_000)

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

  it('renders validation badges and revert actions through the editor slots', async () => {
    const workspace = buildWorkspace()
    workspace.candidates[0].draft.fields = [
      {
        field_key: 'gene_symbol',
        label: 'Gene symbol',
        value: 'BRCA2',
        seed_value: 'BRCA1',
        field_type: 'string',
        group_key: 'primary_data',
        group_label: 'Primary data',
        order: 0,
        required: true,
        read_only: false,
        dirty: true,
        stale_validation: false,
        evidence_anchor_ids: [],
        validation_result: {
          status: 'overridden',
          resolver: 'curator_override',
          candidate_matches: [],
          warnings: [],
        },
        metadata: {},
      },
    ]
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(workspace)

    renderPage('/curation/session-1/candidate-accepted')

    await waitFor(() => {
      expect(screen.getByText('Overridden')).toBeInTheDocument()
      expect(screen.getByText('Edited')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /revert to ai/i })).toBeInTheDocument()
    })

    expect(screen.getByLabelText('Gene symbol')).toHaveValue('BRCA2')
  })

  it('switches candidates and scrolls the linked field row when the PDF viewer selects an anchor', async () => {
    const workspace = buildWorkspace()
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(workspace)
    serviceMocks.updateCurationSession.mockResolvedValue({
      session: {
        ...workspace.session,
        current_candidate_id: 'candidate-pending',
      },
      action_log_entry: null,
    })

    renderPage('/curation/session-1/candidate-accepted')

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/curation/session-1/candidate-accepted',
      )
    })

    await act(async () => {
      await Promise.resolve()
    })

    act(() => {
      dispatchPDFViewerEvidenceAnchorSelected('anchor-1')
    })

    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/curation/session-1/candidate-pending',
      )
      expect(screen.getByTestId('field-row-field_a')).toHaveClass(
        'pdf-to-form-linked-field',
      )
    })

    expect(scrollIntoViewMock).toHaveBeenCalledWith({
      behavior: 'smooth',
      block: 'center',
      inline: 'nearest',
    })
  })

  it('opens the submission preview dialog from the workspace header trigger', async () => {
    const user = userEvent.setup()

    serviceMocks.fetchCurationWorkspace.mockResolvedValue(buildWorkspace())
    serviceMocks.fetchSubmissionPreview.mockResolvedValue(buildSubmissionPreviewResponse())

    renderPage('/curation/session-1')

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Preview submission' })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Preview submission' }))

    expect(await screen.findByRole('dialog', { name: 'Submission preview' })).toBeInTheDocument()
    await waitFor(() => {
      expect(serviceMocks.fetchSubmissionPreview).toHaveBeenCalledWith({
        session_id: 'session-1',
        mode: 'preview',
        include_payload: true,
      })
    })
  })
})
