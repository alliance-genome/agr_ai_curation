import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { ThemeProvider } from '@mui/material/styles'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  CurationSubmissionPreviewResponse,
  CurationWorkspace,
  DomainEnvelopeReviewRowsResponse,
} from '@/features/curation/types'
import theme from '@/theme'
import CurationWorkspacePage from './CurationWorkspacePage'

const serviceMocks = vi.hoisted(() => ({
  autosaveCurationCandidateDraft: vi.fn(),
  createManualCurationCandidate: vi.fn(),
  deleteCurationCandidate: vi.fn(),
  executeCurationSubmission: vi.fn(),
  fetchCurationWorkspace: vi.fn(),
  fetchCurationWorkspaceEnvelopeReviewRows: vi.fn(),
  fetchSubmissionPreview: vi.fn(),
  patchCurationEnvelopeField: vi.fn(),
  dispatchPDFDocumentChanged: vi.fn(),
  renderPdfViewer: vi.fn(),
  submitCurationCandidateDecision: vi.fn(),
  updateCurationSession: vi.fn(),
  validateCurationCandidate: vi.fn(),
}))

vi.mock('@/features/curation/services/curationWorkspaceService', () => ({
  autosaveCurationCandidateDraft: serviceMocks.autosaveCurationCandidateDraft,
  buildCurationWorkspaceEnvelopeReviewRowsRequests: (workspace: CurationWorkspace) => {
    const requestsByKey = new Map<string, { envelope_id: string; envelope_revision: number }>()
    for (const candidate of workspace.candidates) {
      const projectionRef = candidate.projection_ref
      if (!projectionRef) {
        continue
      }
      requestsByKey.set(
        `${projectionRef.envelope_id}:${projectionRef.envelope_revision}`,
        {
          envelope_id: projectionRef.envelope_id,
          envelope_revision: projectionRef.envelope_revision,
        },
      )
    }

    return Array.from(requestsByKey.values())
  },
  createManualCurationCandidate: serviceMocks.createManualCurationCandidate,
  deleteCurationCandidate: serviceMocks.deleteCurationCandidate,
  executeCurationSubmission: serviceMocks.executeCurationSubmission,
  fetchCurationWorkspace: serviceMocks.fetchCurationWorkspace,
  fetchCurationWorkspaceEnvelopeReviewRows: serviceMocks.fetchCurationWorkspaceEnvelopeReviewRows,
  fetchSubmissionPreview: serviceMocks.fetchSubmissionPreview,
  patchCurationEnvelopeField: serviceMocks.patchCurationEnvelopeField,
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

function createDeferredPromise<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve
    reject = nextReject
  })

  return { promise, resolve, reject }
}

function buildWorkspace(): CurationWorkspace {
  return {
    session: {
      session_id: 'session-1',
      status: 'in_progress',
      adapter: {
        adapter_key: 'entity_adapter',
        display_label: 'Entity',
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
          page_number: 1,
          section_title: 'Results and Discussion',
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
          {
            anchor_id: 'anchor-2',
            candidate_id: 'candidate-pending',
            source: 'manual',
            field_keys: ['gene_symbol'],
            field_group_keys: ['primary'],
            is_primary: false,
            anchor: {
              anchor_kind: 'snippet',
              locator_quality: 'exact_quote',
              supports_decision: 'supports',
              snippet_text: 'APOE follow-up evidence sentence',
              sentence_text: 'APOE follow-up evidence sentence',
              viewer_search_text: 'APOE follow-up evidence sentence',
              page_number: 5,
              section_title: 'Discussion',
              chunk_ids: ['chunk-2'],
            },
            created_at: '2026-03-20T12:03:30Z',
            updated_at: '2026-03-20T12:04:30Z',
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

function buildEnvelopeWorkspace(): CurationWorkspace {
  const baseWorkspace = buildWorkspace()
  const candidate = {
    ...baseWorkspace.candidates[1],
    candidate_id: 'candidate-tmem67',
    source: 'extracted' as const,
    status: 'pending' as const,
    display_label: 'Legacy candidate label',
    projection_ref: {
      envelope_id: 'tmem67-envelope',
      object_id: 'tmem67-gene-object',
      envelope_revision: 4,
    },
    draft: {
      ...baseWorkspace.candidates[1].draft,
      draft_id: 'draft-tmem67',
      candidate_id: 'candidate-tmem67',
      fields: [
        {
          ...baseWorkspace.candidates[1].draft.fields[0],
          field_key: 'legacy_gene_symbol',
          label: 'Legacy should not render',
          value: 'LEGACY',
          seed_value: 'LEGACY',
        },
      ],
    },
    evidence_anchors: [],
  }

  return {
    ...baseWorkspace,
    entity_tags: [],
    candidates: [candidate],
    evidence_anchor_projections: [
      {
        anchor_id: 'projection-anchor-1',
        evidence_record_id: 'evidence-record-1',
        envelope_id: 'tmem67-envelope',
        object_id: 'tmem67-gene-object',
        object_type: 'GeneAssertion',
        field_path: 'gene.symbol',
        envelope_revision: 4,
        document_id: 'document-1',
        quote: 'Projected evidence sentence for TMEM67.',
        page_number: 3,
        page_label: '3',
        chunk_id: 'chunk-1',
        chunk_ids: ['chunk-1'],
        section_title: 'Results',
        subsection_title: null,
        figure_reference: null,
        table_reference: null,
        source_id: null,
        source_title: null,
        source_url: null,
        anchor: {
          anchor_kind: 'snippet',
          locator_quality: 'exact_quote',
          supports_decision: 'supports',
          snippet_text: 'Projected evidence sentence for TMEM67.',
          chunk_ids: ['chunk-1'],
        },
        metadata: {},
      },
    ],
    validation_summary_projections: [
      {
        summary_id: 'validation-summary-1',
        envelope_id: 'tmem67-envelope',
        object_id: 'tmem67-gene-object',
        object_type: 'GeneAssertion',
        field_path: 'gene.symbol',
        envelope_revision: 4,
        status: 'unresolved',
        highest_severity: 'warning',
        finding_count: 1,
        open_finding_count: 1,
        finding_ids: ['finding-1'],
        codes: ['fixture.warning'],
        messages: ['Needs curator review'],
        findings: [],
      },
    ],
    active_candidate_id: 'candidate-tmem67',
    session: {
      ...baseWorkspace.session,
      current_candidate_id: 'candidate-tmem67',
      progress: {
        total_candidates: 1,
        reviewed_candidates: 0,
        pending_candidates: 1,
        accepted_candidates: 0,
        rejected_candidates: 0,
        manual_candidates: 0,
      },
    },
  }
}

function buildEnvelopeReviewRows(): DomainEnvelopeReviewRowsResponse {
  return {
    envelope_id: 'tmem67-envelope',
    envelope_revision: 4,
    row_count: 1,
    rows: [
      {
        envelope_id: 'tmem67-envelope',
        object_id: 'tmem67-gene-object',
        envelope_revision: 4,
        domain_pack_id: 'fixture.alliance.gene',
        domain_pack_version: '0.7.0',
        object_type: 'GeneAssertion',
        object_role: 'curatable_unit',
        status: 'draft',
        validation_state: 'unresolved',
        projection_type: 'workspace_review_row',
        projection_key: 'tmem67-gene-object',
        display_label: 'TMEM67',
        secondary_label: 'Gene assertion',
        summary_fields: [
          {
            field_path: 'gene.symbol',
            label: 'Symbol',
            value: 'TMEM67',
            field_type: 'string',
            metadata: {},
          },
          {
            field_path: 'evidence.count',
            label: 'Evidence count',
            value: 1,
            field_type: 'integer',
            metadata: {},
          },
        ],
        schema_provider: 'fixture-schema',
        schema_ref: {},
        object_model_ref: {},
        model_field_ref: {},
        metadata: {
          semantic_source: 'domain_envelope.objects',
        },
      },
    ],
  }
}

function buildSubmissionPreviewResponse(
  mode: 'preview' | 'direct_submit' = 'preview',
): CurationSubmissionPreviewResponse {
  return {
    submission: {
      submission_id: `submission-${mode}`,
      session_id: 'session-1',
      adapter_key: 'entity_adapter',
      mode,
      target_key: 'review_export_bundle',
      status: 'preview_ready',
      readiness: [
        {
          candidate_id: 'candidate-accepted',
          ready: true,
          blocking_reasons: [],
          warnings: [],
          blockers: [],
        },
        {
          candidate_id: 'candidate-pending',
          ready: true,
          blocking_reasons: [],
          warnings: [],
          blockers: [],
        },
      ],
      payload: {
        mode,
        target_key: 'review_export_bundle',
        adapter_key: 'entity_adapter',
        candidate_ids: ['candidate-accepted', 'candidate-pending'],
        payload_json: {
          candidate_count: 2,
        },
        warnings: [],
      },
      requested_at: '2026-03-20T13:00:00Z',
      validation_errors: [],
      warnings: [],
      submission_state: {},
      target_result_history: [],
    },
    session_validation: null,
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
    serviceMocks.deleteCurationCandidate.mockReset()
    serviceMocks.executeCurationSubmission.mockReset()
    serviceMocks.fetchCurationWorkspace.mockReset()
    serviceMocks.fetchCurationWorkspaceEnvelopeReviewRows.mockReset()
    serviceMocks.fetchSubmissionPreview.mockReset()
    serviceMocks.patchCurationEnvelopeField.mockReset()
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

    expect(screen.getAllByText('BRCA1').length).toBeGreaterThan(0)
    expect(screen.getAllByText('APOE').length).toBeGreaterThan(0)
    expect(screen.getByText('validated')).toBeInTheDocument()
    expect(screen.getByText('ambiguous')).toBeInTheDocument()
  })

  it('renders domain-envelope object rows from persisted review-row projections', async () => {
    const workspace = buildEnvelopeWorkspace()
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(workspace)
    serviceMocks.fetchCurationWorkspaceEnvelopeReviewRows.mockResolvedValue([
      buildEnvelopeReviewRows(),
    ])
    serviceMocks.submitCurationCandidateDecision.mockResolvedValue({
      candidate: {
        ...workspace.candidates[0],
        status: 'accepted',
      },
      session: {
        ...workspace.session,
        current_candidate_id: 'candidate-tmem67',
      },
      next_candidate_id: null,
      action_log_entry: {
        action_id: 'action-envelope-accept',
        session_id: workspace.session.session_id,
        candidate_id: 'candidate-tmem67',
        action_type: 'candidate_accepted',
        actor_type: 'user',
        occurred_at: '2026-05-10T12:15:00Z',
        changed_field_keys: [],
        evidence_anchor_ids: [],
        metadata: {},
      },
    })

    renderPage('/curation/session-1')

    expect(await screen.findByText('Objects to review')).toBeInTheDocument()
    const envelopeObjectTablePanel = screen.getByRole('region', {
      name: /envelope object table panel/i,
    })
    expect(envelopeObjectTablePanel).toBeInTheDocument()

    await waitFor(() => {
      expect(serviceMocks.fetchCurationWorkspaceEnvelopeReviewRows).toHaveBeenCalledTimes(1)
    })

    expect(within(envelopeObjectTablePanel).getAllByText('TMEM67').length).toBeGreaterThan(0)
    expect(within(envelopeObjectTablePanel).getByText('Gene Assertion · Curatable unit')).toBeInTheDocument()
    expect(within(envelopeObjectTablePanel).getByText('Gene assertion')).toBeInTheDocument()
    expect(within(envelopeObjectTablePanel).getByText('Symbol')).toBeInTheDocument()
    expect(within(envelopeObjectTablePanel).getByText('Evidence count')).toBeInTheDocument()
    expect(within(envelopeObjectTablePanel).getByText('1 open / 1 findings')).toBeInTheDocument()
    expect(
      screen.getByRole('button', {
        name: /Projected evidence sentence for TMEM67\./,
      }),
    ).toBeInTheDocument()
    expect(
      within(envelopeObjectTablePanel).queryByText('Legacy should not render'),
    ).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Accept TMEM67' }))

    await waitFor(() => {
      expect(serviceMocks.submitCurationCandidateDecision).toHaveBeenCalledWith({
        session_id: 'session-1',
        candidate_id: 'candidate-tmem67',
        action: 'accept',
        advance_queue: false,
      })
    })
  })

  it('surfaces non-Error domain-envelope review row query failures', async () => {
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(buildEnvelopeWorkspace())
    serviceMocks.fetchCurationWorkspaceEnvelopeReviewRows.mockRejectedValue(
      'review rows unavailable',
    )

    renderPage('/curation/session-1')

    expect(await screen.findByText('review rows unavailable')).toBeInTheDocument()
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

  it('passes envelope revisions into previews and submits with the preview-resolved target', async () => {
    const workspace = buildWorkspace()
    workspace.candidates = workspace.candidates.map((candidate) => ({
      ...candidate,
      status: 'accepted',
      projection_ref: {
        envelope_id: 'envelope-1',
        object_id: candidate.candidate_id,
        envelope_revision: 5,
      },
    }))
    const directSubmitPreview = buildSubmissionPreviewResponse('direct_submit')
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(workspace)
    serviceMocks.fetchSubmissionPreview
      .mockResolvedValueOnce(buildSubmissionPreviewResponse('preview'))
      .mockResolvedValueOnce(directSubmitPreview)
    serviceMocks.executeCurationSubmission.mockResolvedValue({
      submission: {
        ...directSubmitPreview.submission,
        status: 'accepted',
        external_reference: 'noop:review_export_bundle:2',
        completed_at: '2026-03-20T13:01:00Z',
      },
      session: {
        ...workspace.session,
        status: 'submitted',
        submitted_at: '2026-03-20T13:01:00Z',
      },
      action_log_entry: {
        action_id: 'action-submit-1',
        session_id: 'session-1',
        action_type: 'submission_executed',
        actor_type: 'user',
        occurred_at: '2026-03-20T13:01:00Z',
        changed_field_keys: [],
        evidence_anchor_ids: [],
        metadata: {},
      },
    })

    renderPage('/curation/session-1')

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Preview submission' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Preview submission' }))

    await waitFor(() => {
      expect(serviceMocks.fetchSubmissionPreview).toHaveBeenCalledWith({
        session_id: 'session-1',
        mode: 'preview',
        include_payload: true,
        expected_envelope_revisions: {
          'envelope-1': 5,
        },
      })
    })

    fireEvent.click(await screen.findByRole('button', { name: 'Submit mode' }))

    await waitFor(() => {
      expect(serviceMocks.fetchSubmissionPreview).toHaveBeenLastCalledWith({
        session_id: 'session-1',
        mode: 'direct_submit',
        include_payload: true,
        expected_envelope_revisions: {
          'envelope-1': 5,
        },
      })
    })

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Submit' })).toBeEnabled()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Submit' }))

    await waitFor(() => {
      expect(serviceMocks.executeCurationSubmission).toHaveBeenCalledWith({
        session_id: 'session-1',
        target_key: 'review_export_bundle',
        candidate_ids: ['candidate-accepted', 'candidate-pending'],
        mode: 'direct_submit',
        expected_envelope_revisions: {
          'envelope-1': 5,
        },
      })
    })
  })

  it('surfaces missing direct-submit preview payloads instead of deriving candidate IDs', async () => {
    const workspace = buildWorkspace()
    workspace.candidates = workspace.candidates.map((candidate) => ({
      ...candidate,
      status: 'accepted',
      projection_ref: {
        envelope_id: 'envelope-1',
        object_id: candidate.candidate_id,
        envelope_revision: 5,
      },
    }))
    const directSubmitPreview = buildSubmissionPreviewResponse('direct_submit')
    directSubmitPreview.submission.payload = null

    serviceMocks.fetchCurationWorkspace.mockResolvedValue(workspace)
    serviceMocks.fetchSubmissionPreview
      .mockResolvedValueOnce(buildSubmissionPreviewResponse('preview'))
      .mockResolvedValueOnce(directSubmitPreview)

    renderPage('/curation/session-1')

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Preview submission' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Preview submission' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Submit mode' }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Submit' })).toBeEnabled()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Submit' }))

    expect(await screen.findByText(
      'Direct submission requires a preview payload. Refresh the submission preview and try again.',
    )).toBeInTheDocument()
    expect(serviceMocks.executeCurationSubmission).not.toHaveBeenCalled()
  }, 10000)

  it('initializes the PDF viewer document after hydration', async () => {
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(buildWorkspace())

    renderPage('/curation/session-1/candidate-accepted')

    await waitFor(() => {
      expect(serviceMocks.dispatchPDFDocumentChanged).toHaveBeenCalledWith(
        'document-1',
        '/api/documents/document-1.pdf',
        'Workspace Document',
        5,
        { ownerToken: 'curation:session-1' },
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
        screen.getByRole('button', {
          name: /Highlight evidence on PDF: APOE evidence sentence/i,
        }),
      ).toBeInTheDocument()
    })

    expect(screen.getByText(/Evidence for/i)).toBeInTheDocument()
    expect(
      screen.getByRole('button', {
        name: /Highlight evidence on PDF: APOE follow-up evidence sentence/i,
      }),
    ).toBeInTheDocument()
    expect(screen.getByText(/2 evidence quotes/)).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /Highlight evidence on PDF:/i })).toHaveLength(2)
  })

  it('patches active envelope fields from the field editor with revision and before value', async () => {
    const workspace = buildWorkspace()
    const envelopeCandidate = {
      ...workspace.candidates[0],
      projection_ref: {
        envelope_id: 'envelope-1',
        object_id: 'object-1',
        envelope_revision: 5,
      },
      draft: {
        ...workspace.candidates[0].draft,
        fields: [
          {
            ...workspace.candidates[0].draft.fields[0],
            metadata: {
              source_field_path: 'gene.symbol',
            },
          },
        ],
      },
    }
    const patchedCandidate = {
      ...envelopeCandidate,
      projection_ref: {
        envelope_id: 'envelope-1',
        object_id: 'object-1',
        envelope_revision: 6,
      },
      draft: {
        ...envelopeCandidate.draft,
        version: 2,
        fields: [
          {
            ...envelopeCandidate.draft.fields[0],
            value: 'BRCA2',
            seed_value: 'BRCA2',
            dirty: false,
            stale_validation: true,
          },
        ],
      },
    }
    const envelopeWorkspace: CurationWorkspace = {
      ...workspace,
      candidates: [patchedCandidate, workspace.candidates[1]],
      active_candidate_id: 'candidate-accepted',
    }

    serviceMocks.fetchCurationWorkspace.mockResolvedValue({
      ...workspace,
      candidates: [envelopeCandidate, workspace.candidates[1]],
      active_candidate_id: 'candidate-accepted',
    })
    serviceMocks.patchCurationEnvelopeField.mockResolvedValue({
      accepted: true,
      envelope_id: 'envelope-1',
      previous_revision: 5,
      envelope_revision: 6,
      object_id: 'object-1',
      object_type: 'gene',
      field_path: 'gene.symbol',
      operation: 'replace',
      before: 'BRCA1',
      value: 'BRCA2',
      projection_ref: patchedCandidate.projection_ref,
      candidate: patchedCandidate,
      session: envelopeWorkspace.session,
      action_log_entry: null,
      history_event_ids: ['history-1'],
      projection_candidate_ids: ['candidate-accepted'],
    })

    renderPage('/curation/session-1/candidate-accepted')

    await waitFor(() => {
      expect(screen.getByTestId('candidate-field-editor')).toBeInTheDocument()
    })

    vi.useFakeTimers()
    fireEvent.change(screen.getByLabelText('Gene symbol'), {
      target: { value: 'BRCA2' },
    })

    await act(async () => {
      vi.advanceTimersByTime(2600)
      await Promise.resolve()
    })

    expect(serviceMocks.patchCurationEnvelopeField).toHaveBeenCalledWith({
      session_id: 'session-1',
      envelope_id: 'envelope-1',
      expected_revision: 5,
      object_id: 'object-1',
      field_path: 'gene.symbol',
      operation: 'replace',
      before: 'BRCA1',
      value: 'BRCA2',
    }, {
      keepalive: undefined,
    })
    expect(serviceMocks.autosaveCurationCandidateDraft).not.toHaveBeenCalled()
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

    const entityTablePanel = screen.getByRole('region', {
      name: /entity table panel/i,
    })
    fireEvent.click(within(entityTablePanel).getByRole('button', { name: 'Accept' }))

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

  it('confirms and deletes a curation row through the workspace delete service', async () => {
    const workspace = buildWorkspace()
    const refreshedWorkspace: CurationWorkspace = {
      ...workspace,
      entity_tags: workspace.entity_tags.filter((tag) => tag.tag_id !== 'candidate-pending'),
      candidates: workspace.candidates.filter((candidate) => candidate.candidate_id !== 'candidate-pending'),
      active_candidate_id: 'candidate-accepted',
      session: {
        ...workspace.session,
        current_candidate_id: 'candidate-accepted',
        progress: {
          total_candidates: 1,
          reviewed_candidates: 1,
          pending_candidates: 0,
          accepted_candidates: 1,
          rejected_candidates: 0,
          manual_candidates: 0,
        },
      },
    }
    serviceMocks.fetchCurationWorkspace
      .mockResolvedValueOnce(workspace)
      .mockResolvedValueOnce(refreshedWorkspace)
    serviceMocks.updateCurationSession.mockResolvedValue({
      session: {
        ...workspace.session,
        current_candidate_id: 'candidate-pending',
      },
      action_log_entry: null,
    })
    serviceMocks.deleteCurationCandidate.mockResolvedValue({
      deleted_candidate_id: 'candidate-pending',
      session: refreshedWorkspace.session,
      action_log_entry: {
        action_id: 'action-delete-1',
        session_id: workspace.session.session_id,
        action_type: 'candidate_deleted',
        actor_type: 'user',
        occurred_at: '2026-03-30T12:10:00Z',
        changed_field_keys: [],
        evidence_anchor_ids: ['anchor-1', 'anchor-2'],
        metadata: {
          deleted_candidate_id: 'candidate-pending',
        },
      },
    })

    renderPage('/curation/session-1/candidate-pending')

    await waitFor(() => {
      expect(screen.getByText('APOE')).toBeInTheDocument()
    })

    await waitFor(() => {
      expect(screen.getByLabelText('Delete APOE')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByLabelText('Delete APOE'))

    expect(screen.getByText('Delete curation row?')).toBeInTheDocument()
    expect(serviceMocks.deleteCurationCandidate).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Delete row' }))

    await waitFor(() => {
      expect(serviceMocks.deleteCurationCandidate).toHaveBeenCalledWith({
        session_id: 'session-1',
        candidate_id: 'candidate-pending',
      })
    })

    await waitFor(() => {
      expect(screen.queryByText('APOE')).not.toBeInTheDocument()
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/curation/session-1/candidate-accepted',
      )
    })
  })

  it('submits accept-all-validated decisions without waiting for each prior request to resolve', async () => {
    const workspace = buildWorkspace()
    const workspaceWithValidatedPending: CurationWorkspace = {
      ...workspace,
      entity_tags: workspace.entity_tags.map((tag) => ({
        ...tag,
        decision: 'pending',
        db_status: 'validated',
      })),
      candidates: workspace.candidates.map((candidate) => ({
        ...candidate,
        status: 'pending',
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
      })),
    }
    const refreshedWorkspace: CurationWorkspace = {
      ...workspaceWithValidatedPending,
      entity_tags: workspaceWithValidatedPending.entity_tags.map((tag) => ({
        ...tag,
        decision: 'accepted',
      })),
      candidates: workspaceWithValidatedPending.candidates.map((candidate) => ({
        ...candidate,
        status: 'accepted',
      })),
    }
    const firstDecision = createDeferredPromise<unknown>()
    const secondDecision = createDeferredPromise<unknown>()

    serviceMocks.fetchCurationWorkspace
      .mockResolvedValueOnce(workspaceWithValidatedPending)
      .mockResolvedValueOnce(refreshedWorkspace)
    serviceMocks.submitCurationCandidateDecision.mockImplementation(({ candidate_id }) => {
      if (candidate_id === 'candidate-accepted') {
        return firstDecision.promise
      }

      return secondDecision.promise
    })

    renderPage('/curation/session-1')

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Accept All Validated' })).toBeEnabled()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Accept All Validated' }))

    await waitFor(() => {
      expect(serviceMocks.submitCurationCandidateDecision).toHaveBeenCalledTimes(2)
    })

    firstDecision.resolve({
      candidate: refreshedWorkspace.candidates[0],
      session: refreshedWorkspace.session,
      next_candidate_id: null,
      action_log_entry: null,
    })
    secondDecision.resolve({
      candidate: refreshedWorkspace.candidates[1],
      session: refreshedWorkspace.session,
      next_candidate_id: null,
      action_log_entry: null,
    })

    await waitFor(() => {
      expect(serviceMocks.fetchCurationWorkspace).toHaveBeenCalledTimes(2)
      expect(screen.getAllByText('Accepted').length).toBeGreaterThan(0)
    })
  }, 15000) // The deferred decision flow is intentionally async and can overrun 5s during suite-wide contention.

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
