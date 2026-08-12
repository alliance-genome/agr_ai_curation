import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { ThemeProvider } from '@mui/material/styles'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  CurationWorkspace,
  DomainEnvelopeReviewRowsResponse,
} from '@/features/curation/types'
import { dispatchPDFViewerEvidenceAnchorSelected } from '@/components/pdfViewer/pdfEvents'
import { PDF_TO_FORM_HIGHLIGHT_CLASSNAME } from '@/features/curation/workspace/usePdfToFormLinking'
import theme from '@/theme'
import CurationWorkspacePage from './CurationWorkspacePage'

const serviceMocks = vi.hoisted(() => ({
  autosaveCurationCandidateDraft: vi.fn(),
  createManualCurationCandidate: vi.fn(),
  executeCurationSubmission: vi.fn(),
  fetchCurationWorkspace: vi.fn(),
  fetchCurationWorkspaceEnvelopeReviewRows: vi.fn(),
  fetchSubmissionPreview: vi.fn(),
  patchCurationEnvelopeField: vi.fn(),
  dispatchPDFDocumentChanged: vi.fn(),
  renderPdfViewer: vi.fn(),
  submitCurationCandidateDecision: vi.fn(),
  updateCurationSession: vi.fn(),
  validateAllCurationSessionCandidates: vi.fn(),
  validateCurationCandidate: vi.fn(),
  focusGrid: vi.fn(),
  showPdf: vi.fn(),
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
  executeCurationSubmission: serviceMocks.executeCurationSubmission,
  fetchCurationWorkspace: serviceMocks.fetchCurationWorkspace,
  fetchCurationWorkspaceEnvelopeReviewRows: serviceMocks.fetchCurationWorkspaceEnvelopeReviewRows,
  fetchSubmissionPreview: serviceMocks.fetchSubmissionPreview,
  patchCurationEnvelopeField: serviceMocks.patchCurationEnvelopeField,
  submitCurationCandidateDecision: serviceMocks.submitCurationCandidateDecision,
  updateCurationSession: serviceMocks.updateCurationSession,
  validateAllCurationSessionCandidates: serviceMocks.validateAllCurationSessionCandidates,
  validateCurationCandidate: serviceMocks.validateCurationCandidate,
}))

vi.mock('@/components/pdfViewer/PersistentPdfWorkspaceLayout', () => ({
  usePersistentPdfWorkspaceLayout: () => ({
    focusGrid: serviceMocks.focusGrid,
    isPdfVisible: true,
    showPdf: serviceMocks.showPdf,
  }),
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
  const evidenceProjection = {
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
      anchor_kind: 'snippet' as const,
      locator_quality: 'exact_quote' as const,
      supports_decision: 'supports' as const,
      snippet_text: 'Projected evidence sentence for TMEM67.',
      chunk_ids: ['chunk-1'],
    },
    metadata: {},
  }
  const validationSummaryProjection = {
    summary_id: 'validation-summary-1',
    envelope_id: 'tmem67-envelope',
    object_id: 'tmem67-gene-object',
    object_type: 'GeneAssertion',
    field_path: 'gene.symbol',
    envelope_revision: 4,
    status: 'unresolved' as const,
    highest_severity: 'warning' as const,
    finding_count: 1,
    open_finding_count: 1,
    finding_ids: ['finding-1'],
    codes: ['fixture.warning'],
    messages: ['Needs curator review'],
    findings: [],
  }
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
          field_key: 'gene.symbol',
          label: 'Gene symbol',
          value: 'TMEM67',
          seed_value: 'TMEM67',
        },
      ],
    },
    evidence_anchors: [],
    evidence_anchor_projections: [evidenceProjection],
    validation_summary_projections: [validationSummaryProjection],
  }

  return {
    ...baseWorkspace,
    candidates: [candidate],
    evidence_anchor_projections: [evidenceProjection],
    validation_summary_projections: [validationSummaryProjection],
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
          semantic_source: 'domain_envelope.extracted_objects',
        },
      },
    ],
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

  beforeEach(() => {
    HTMLElement.prototype.scrollIntoView = vi.fn()
    serviceMocks.autosaveCurationCandidateDraft.mockReset()
    serviceMocks.createManualCurationCandidate.mockReset()
    serviceMocks.executeCurationSubmission.mockReset()
    serviceMocks.fetchCurationWorkspace.mockReset()
    serviceMocks.fetchCurationWorkspaceEnvelopeReviewRows.mockReset()
    serviceMocks.fetchSubmissionPreview.mockReset()
    serviceMocks.patchCurationEnvelopeField.mockReset()
    serviceMocks.dispatchPDFDocumentChanged.mockReset()
    serviceMocks.renderPdfViewer.mockReset()
    serviceMocks.submitCurationCandidateDecision.mockReset()
    serviceMocks.updateCurationSession.mockReset()
    serviceMocks.validateAllCurationSessionCandidates.mockReset()
    serviceMocks.validateCurationCandidate.mockReset()
    serviceMocks.focusGrid.mockReset()
    serviceMocks.showPdf.mockReset()
  })

  afterEach(() => {
    HTMLElement.prototype.scrollIntoView = originalScrollIntoView
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('renders workspace candidates on the production horizontal-grid review surface', async () => {
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(buildWorkspace())

    renderPage('/curation/session-1')

    await waitFor(() => {
      expect(serviceMocks.fetchCurationWorkspace).toHaveBeenCalledWith('session-1')
    })

    await waitFor(() => {
      expect(screen.getByTestId('workspace-shell')).toBeInTheDocument()
    })

    expect(screen.getByRole('region', { name: /review work pane/i })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: /envelope object table panel/i })).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Horizontally scrollable curation grid' })).toBeInTheDocument()
    expect(screen.getByTestId('workspace-shell-work-pane-content')).toBeInTheDocument()
    expect(screen.queryByTestId('object-selector-strip')).not.toBeInTheDocument()
    expect(screen.queryByTestId('candidate-field-editor')).not.toBeInTheDocument()
    expect(screen.getByText('Review objects')).toBeInTheDocument()
    expect(screen.getByLabelText('Authoritative validation summary')).toHaveTextContent(
      '1 validated · 1 blocking · 0 stale · 0 open findings',
    )

    expect(screen.getAllByText('Accepted candidate').length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: 'Focus grid' }))
    expect(serviceMocks.focusGrid).toHaveBeenCalledTimes(1)
  })

  it('uses persisted review-row projections in the horizontal grid', async () => {
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

    expect(await screen.findByRole('region', {
      name: 'Horizontally scrollable curation grid',
    })).toBeInTheDocument()

    await waitFor(() => {
      expect(serviceMocks.fetchCurationWorkspaceEnvelopeReviewRows).toHaveBeenCalledTimes(1)
    })

    expect(screen.getAllByText('TMEM67').length).toBeGreaterThan(0)
    expect(screen.getByText('Needs curator review')).toBeInTheDocument()
    expect(screen.getByRole('button', {
      name: 'Select Gene symbol for gene.symbol',
    })).toBeInTheDocument()
    expect(screen.getByRole('button', {
      name: 'Show evidence 1 for Gene symbol',
    })).toBeInTheDocument()

    act(() => {
      dispatchPDFViewerEvidenceAnchorSelected(
        'projection-anchor-1',
        'document-1',
        'curation:session-1',
      )
    })
    await waitFor(() => {
      expect(screen.getByRole('button', {
        name: 'Select Gene symbol for gene.symbol',
      })).toHaveFocus()
      expect(screen.getByTestId('horizontal-grid-field-gene.symbol')).toHaveClass(
        PDF_TO_FORM_HIGHLIGHT_CLASSNAME,
      )
    })

    expect(screen.getByRole('button', { name: 'Accept Legacy candidate label' })).toBeDisabled()
    expect(serviceMocks.submitCurationCandidateDecision).not.toHaveBeenCalled()
  })

  it('creates a manual object from the envelope work pane toolbar', async () => {
    const user = userEvent.setup()
    const workspace = buildEnvelopeWorkspace()
    const templateField = workspace.candidates[0].draft.fields[0]
    workspace.candidates[0].draft.fields = [
      {
        ...templateField,
        field_key: 'expression_annotation_subject.gene_symbol',
        label: 'Gene symbol',
        value: 'TMEM67',
        seed_value: 'TMEM67',
      },
      {
        ...templateField,
        field_key: 'single_reference.reference_id',
        label: 'Reference',
        value: 'PMID:123456',
        seed_value: 'PMID:123456',
      },
    ]
    const manualCandidate = {
      ...workspace.candidates[0],
      candidate_id: 'candidate-manual-1',
      source: 'manual' as const,
      status: 'pending' as const,
      display_label: 'manual gene',
      projection_ref: null,
      draft: {
        ...workspace.candidates[0].draft,
        draft_id: 'draft-manual-1',
        candidate_id: 'candidate-manual-1',
      },
    }
    const refreshedWorkspace = {
      ...workspace,
      candidates: [...workspace.candidates, manualCandidate],
      active_candidate_id: 'candidate-manual-1',
      session: {
        ...workspace.session,
        current_candidate_id: 'candidate-manual-1',
      },
    }
    const refreshDeferred = createDeferredPromise<CurationWorkspace>()

    serviceMocks.fetchCurationWorkspace
      .mockResolvedValueOnce(workspace)
      .mockReturnValue(refreshDeferred.promise)
    serviceMocks.fetchCurationWorkspaceEnvelopeReviewRows.mockResolvedValue([
      buildEnvelopeReviewRows(),
    ])
    serviceMocks.createManualCurationCandidate.mockResolvedValue({
      candidate: manualCandidate,
      session: refreshedWorkspace.session,
      action_log_entry: {
        action_id: 'action-manual-1',
        session_id: 'session-1',
        candidate_id: 'candidate-manual-1',
        draft_id: 'draft-manual-1',
        action_type: 'candidate_created',
        actor_type: 'user',
        occurred_at: '2026-05-10T12:20:00Z',
        changed_field_keys: ['entity_name', 'entity_type', 'species', 'topic'],
        evidence_anchor_ids: [],
        metadata: {},
      },
    })
    serviceMocks.updateCurationSession.mockResolvedValue({
      session: refreshedWorkspace.session,
      action_log_entry: null,
    })

    renderPage('/curation/session-1')

    await user.click(await screen.findByRole('button', { name: 'Add object' }))

    const dialog = await screen.findByRole('dialog', { name: 'Add object' })
    await user.type(within(dialog).getByLabelText('Name'), 'manual gene')
    await user.click(within(dialog).getByRole('combobox', { name: 'Type' }))
    await user.click(screen.getByRole('option', { name: 'gene' }))
    await user.type(within(dialog).getByLabelText('Species'), 'NCBITaxon:7955')
    await user.type(within(dialog).getByLabelText('Topic'), 'gene expression')
    await user.click(within(dialog).getByRole('button', { name: 'Add object' }))

    await waitFor(() => {
      expect(serviceMocks.createManualCurationCandidate).toHaveBeenCalledWith({
        session_id: 'session-1',
        adapter_key: 'entity_adapter',
        source: 'manual',
        display_label: 'manual gene',
        draft: expect.objectContaining({
          candidate_id: expect.stringContaining('manual-candidate-'),
          metadata: expect.objectContaining({
            manual_object: {
              entity_name: 'manual gene',
              entity_type: 'ATP:0000005',
              species: 'NCBITaxon:7955',
              topic: 'gene expression',
            },
          }),
          fields: expect.arrayContaining([
            expect.objectContaining({
              field_key: 'expression_annotation_subject.gene_symbol',
              value: 'manual gene',
            }),
            expect.objectContaining({
              field_key: 'single_reference.reference_id',
              value: null,
            }),
          ]),
        }),
        evidence_anchors: [],
      })
    })
    await waitFor(() => {
      expect(serviceMocks.fetchCurationWorkspace).toHaveBeenCalledTimes(2)
    })
    expect(serviceMocks.updateCurationSession).not.toHaveBeenCalledWith({
      session_id: 'session-1',
      current_candidate_id: 'candidate-manual-1',
    })

    await act(async () => {
      refreshDeferred.resolve(refreshedWorkspace)
      await refreshDeferred.promise
    })

    await waitFor(() => {
      expect(serviceMocks.fetchCurationWorkspace).toHaveBeenLastCalledWith('session-1')
    })
    await waitFor(() => {
      expect(serviceMocks.updateCurationSession).toHaveBeenCalledWith({
        session_id: 'session-1',
        expected_session_version: 1,
        intent_owner: expect.any(String),
        intent_generation: expect.any(Number),
        current_candidate_id: 'candidate-manual-1',
      }, { signal: expect.any(AbortSignal) })
    })
    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/curation/session-1/candidate-manual-1',
      )
    })
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Add object' })).not.toBeInTheDocument()
    })
    expect(screen.getByTestId('horizontal-grid-context-candidate-manual-1')).toHaveTextContent(
      'manual gene',
    )
  })

  it('surfaces non-Error domain-envelope review row query failures', async () => {
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(buildEnvelopeWorkspace())
    serviceMocks.fetchCurationWorkspaceEnvelopeReviewRows.mockRejectedValue(
      'review rows unavailable',
    )

    renderPage('/curation/session-1')

    expect(await screen.findByText('review rows unavailable')).toBeInTheDocument()
  })

  it('keeps a successful decision when the following review-row refresh fails', async () => {
    const workspace = buildEnvelopeWorkspace()
    const refreshedWorkspace: CurationWorkspace = {
      ...workspace,
      candidates: workspace.candidates.map((candidate) => ({
        ...candidate,
        status: 'rejected',
        projection_ref: candidate.projection_ref
          ? { ...candidate.projection_ref, envelope_revision: 5 }
          : null,
      })),
    }
    serviceMocks.fetchCurationWorkspace
      .mockResolvedValueOnce(workspace)
      .mockResolvedValueOnce(refreshedWorkspace)
    serviceMocks.fetchCurationWorkspaceEnvelopeReviewRows
      .mockResolvedValueOnce([buildEnvelopeReviewRows()])
      .mockRejectedValue(new Error('review row refresh failed'))
    serviceMocks.submitCurationCandidateDecision.mockResolvedValue({
      candidate: refreshedWorkspace.candidates[0],
      session: refreshedWorkspace.session,
      next_candidate_id: null,
      action_log_entry: null,
    })

    renderPage('/curation/session-1/candidate-tmem67')

    fireEvent.click(await screen.findByRole('button', {
      name: 'Reject Legacy candidate label',
    }))

    expect(await screen.findByText('review row refresh failed')).toBeInTheDocument()
    await waitFor(() => {
      const contextCell = screen.getByTestId('horizontal-grid-context-candidate-tmem67')
      expect(contextCell).toHaveTextContent('Legacy candidate label')
      expect(contextCell).not.toHaveTextContent('Gene assertion')
      expect(screen.getByText('rejected')).toBeInTheDocument()
      expect(screen.queryByRole('button', {
        name: 'Reject Legacy candidate label',
      })).not.toBeInTheDocument()
    })
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

  it('shows a work-in-progress message instead of loading submission preview', async () => {
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(buildWorkspace())

    renderPage('/curation/session-1')

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Preview submission' })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Preview submission' }))

    expect(screen.getByRole('dialog', { name: 'Submission preview is in progress' })).toBeInTheDocument()
    expect(screen.getByText(/Submission preview and submission actions are a work in progress/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Submit mode' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Refresh preview' })).not.toBeInTheDocument()
    expect(serviceMocks.fetchSubmissionPreview).not.toHaveBeenCalled()
    expect(serviceMocks.executeCurationSubmission).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'OK' }))

    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Submission preview is in progress' })).not.toBeInTheDocument()
    })
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
        { ownerToken: 'curation:session-1' },
      )
    })
  })

  it('restores the route-selected candidate into the horizontal grid', async () => {
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

    await waitFor(() => expect(
      screen.getByTestId('horizontal-grid-context-candidate-pending'),
    ).toHaveAttribute('aria-pressed', 'true'))
    expect(screen.getAllByText('Pending candidate').length).toBeGreaterThan(0)
    expect(screen.getByTestId('location')).toHaveTextContent(
      '/curation/session-1/candidate-pending',
    )
  })

  it('links a scoped PDF highlight action to candidate selection, field scrolling, focus, and highlight', async () => {
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

    expect(await screen.findByTestId('horizontal-grid-context-candidate-accepted')).toHaveAttribute(
      'aria-pressed',
      'true',
    )

    act(() => {
      dispatchPDFViewerEvidenceAnchorSelected(
        'anchor-1',
        'document-1',
        'curation:session-1',
      )
    })

    const pendingField = document.querySelector<HTMLElement>(
      'tr[data-candidate-id="candidate-pending"] [data-field-key="gene_symbol"]',
    )
    expect(pendingField).not.toBeNull()
    await waitFor(() => {
      expect(pendingField).toHaveFocus()
      expect(pendingField).toHaveClass(PDF_TO_FORM_HIGHLIGHT_CLASSNAME)
      expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalledWith({
        behavior: 'smooth',
        block: 'center',
        inline: 'nearest',
      })
    })
    expect(screen.getByTestId('location')).toHaveTextContent(
      '/curation/session-1/candidate-pending',
    )
  })

  it('ignores stale workspace events and anchors without an authoritative field mapping', async () => {
    const workspace = buildWorkspace()
    workspace.candidates[1].evidence_anchors[0].field_keys = ['removed_field']
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(workspace)

    renderPage('/curation/session-1/candidate-accepted')

    expect(await screen.findByTestId('horizontal-grid-context-candidate-accepted')).toHaveAttribute(
      'aria-pressed',
      'true',
    )

    act(() => {
      dispatchPDFViewerEvidenceAnchorSelected(
        'anchor-2',
        'document-from-previous-session',
        'curation:previous-session',
      )
      dispatchPDFViewerEvidenceAnchorSelected(
        'anchor-1',
        'document-1',
        'curation:session-1',
      )
    })

    expect(screen.getByTestId('horizontal-grid-context-candidate-accepted')).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByTestId('location')).toHaveTextContent(
      '/curation/session-1/candidate-accepted',
    )
    expect(HTMLElement.prototype.scrollIntoView).not.toHaveBeenCalled()
  })

  it('keeps one evidence listener across candidate route changes and removes it on unmount', async () => {
    const workspace = buildWorkspace()
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(workspace)
    serviceMocks.updateCurationSession.mockResolvedValue({
      session: {
        ...workspace.session,
        current_candidate_id: 'candidate-pending',
      },
      action_log_entry: null,
    })
    const addEventListenerSpy = vi.spyOn(window, 'addEventListener')
    const removeEventListenerSpy = vi.spyOn(window, 'removeEventListener')
    const rendered = renderPage('/curation/session-1/candidate-accepted')

    expect(await screen.findByTestId('horizontal-grid-context-candidate-accepted')).toBeInTheDocument()
    const evidenceListenerAdds = () => addEventListenerSpy.mock.calls.filter(
      ([eventName]) => eventName === 'pdf-viewer-evidence-anchor-selected',
    )
    const evidenceListenerRemovals = () => removeEventListenerSpy.mock.calls.filter(
      ([eventName]) => eventName === 'pdf-viewer-evidence-anchor-selected',
    )
    expect(evidenceListenerAdds()).toHaveLength(1)

    act(() => {
      dispatchPDFViewerEvidenceAnchorSelected(
        'anchor-1',
        'document-1',
        'curation:session-1',
      )
    })
    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/curation/session-1/candidate-pending',
      )
    })
    expect(evidenceListenerAdds()).toHaveLength(1)
    expect(evidenceListenerRemovals()).toHaveLength(0)

    rendered.unmount()

    expect(evidenceListenerRemovals()).toHaveLength(1)
    expect(evidenceListenerRemovals()[0][1]).toBe(evidenceListenerAdds()[0][1])
  })

  it('patches active envelope fields from the horizontal grid editor with revision and before value', async () => {
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

    const activeRow = await screen.findByRole('row', { name: /Accepted candidate/i })
    fireEvent.click(within(activeRow).getByRole('button', { name: 'Edit Gene symbol' }))
    expect(await screen.findByRole('dialog', { name: 'Edit Gene symbol' })).toBeInTheDocument()

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
    workspace.candidates[1] = {
      ...workspace.candidates[1],
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
    }
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
    const savedPendingCandidate = {
      ...workspace.candidates[1],
      draft: {
        ...workspace.candidates[1].draft,
        version: 2,
        fields: workspace.candidates[1].draft.fields.map((field) => ({
          ...field,
          value: 'APOE2',
          dirty: false,
        })),
      },
    }
    serviceMocks.autosaveCurationCandidateDraft.mockResolvedValue({
      candidate: savedPendingCandidate,
      draft: savedPendingCandidate.draft,
      validation_snapshot: null,
      action_log_entry: null,
    })

    renderPage('/curation/session-1/candidate-pending')

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Accept Pending candidate' })).toBeEnabled()
    })

    const pendingRow = screen.getByRole('row', { name: /Pending candidate/i })
    fireEvent.click(within(pendingRow).getByRole('button', { name: 'Edit Gene symbol' }))
    fireEvent.change(await screen.findByLabelText('Gene symbol'), {
      target: { value: 'APOE2' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: /Edit/ })).not.toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: 'Accept Pending candidate' }))

    await waitFor(() => {
      expect(serviceMocks.autosaveCurationCandidateDraft).toHaveBeenCalled()
      expect(serviceMocks.submitCurationCandidateDecision).toHaveBeenCalledWith({
        session_id: 'session-1',
        candidate_id: 'candidate-pending',
        action: 'accept',
        advance_queue: false,
      })
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/curation/session-1/candidate-pending',
      )
    })
    expect(serviceMocks.submitCurationCandidateDecision.mock.invocationCallOrder[0]).toBeGreaterThan(
      serviceMocks.autosaveCurationCandidateDraft.mock.invocationCallOrder[0],
    )
  })

  it('flushes autosave before authoritative row validation and refreshes the grid', async () => {
    const workspace = buildWorkspace()
    const refreshedWorkspace: CurationWorkspace = {
      ...workspace,
      candidates: workspace.candidates.map((candidate) => candidate.candidate_id === 'candidate-pending'
        ? {
            ...candidate,
            validation: {
              state: 'completed' as const,
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
          }
        : candidate),
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
    serviceMocks.validateCurationCandidate.mockResolvedValue({
      candidate: refreshedWorkspace.candidates[1],
      validation_snapshot: {
        snapshot_id: 'snapshot-row-1',
        scope: 'candidate',
        session_id: 'session-1',
        candidate_id: 'candidate-pending',
        state: 'completed',
        field_results: {},
        summary: refreshedWorkspace.candidates[1].validation,
        warnings: [],
      },
    })

    renderPage('/curation/session-1/candidate-pending')

    const validateButton = await screen.findByRole('button', { name: 'Validate Pending candidate' })
    fireEvent.click(validateButton)

    await waitFor(() => {
      expect(serviceMocks.validateCurationCandidate).toHaveBeenCalledWith({
        session_id: 'session-1',
        candidate_id: 'candidate-pending',
      })
    })
    expect(serviceMocks.fetchCurationWorkspace.mock.invocationCallOrder[1]).toBeGreaterThan(
      serviceMocks.validateCurationCandidate.mock.invocationCallOrder[0],
    )
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Accept Pending candidate' })).toBeEnabled()
    })
  })

  it('shows session validation progress and refreshes authoritative aggregate counts', async () => {
    const workspace = buildWorkspace()
    const refreshedWorkspace: CurationWorkspace = {
      ...workspace,
      candidates: workspace.candidates.map((candidate) => ({
        ...candidate,
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
    const validationDeferred = createDeferredPromise<unknown>()
    serviceMocks.fetchCurationWorkspace
      .mockResolvedValueOnce(workspace)
      .mockResolvedValueOnce(refreshedWorkspace)
    serviceMocks.validateAllCurationSessionCandidates.mockReturnValue(validationDeferred.promise)
    serviceMocks.updateCurationSession.mockResolvedValue({
      session: workspace.session,
      action_log_entry: null,
    })

    renderPage('/curation/session-1')

    fireEvent.click(await screen.findByRole('button', { name: 'Validate all' }))
    expect(screen.getByRole('button', { name: 'Validating all…' })).toBeDisabled()
    await waitFor(() => {
      expect(serviceMocks.validateAllCurationSessionCandidates).toHaveBeenCalledWith({
        session_id: 'session-1',
      })
    })

    validationDeferred.resolve({
      session: refreshedWorkspace.session,
      session_validation: {
        snapshot_id: 'snapshot-session-1',
        scope: 'session',
        session_id: 'session-1',
        state: 'completed',
        field_results: {},
        summary: refreshedWorkspace.candidates[0].validation,
        warnings: [],
      },
      candidate_validations: [],
    })

    await waitFor(() => {
      expect(screen.getByLabelText('Authoritative validation summary')).toHaveTextContent(
        '2 validated · 0 blocking · 0 stale · 0 open findings',
      )
    })
  })

  it('submits row rejection through the existing decision owner', async () => {
    const workspace = buildWorkspace()
    const refreshedWorkspace: CurationWorkspace = {
      ...workspace,
      candidates: workspace.candidates.map((candidate) =>
        candidate.candidate_id === 'candidate-pending'
          ? { ...candidate, status: 'rejected' as const }
          : candidate),
    }
    serviceMocks.fetchCurationWorkspace
      .mockResolvedValueOnce(workspace)
      .mockResolvedValueOnce(refreshedWorkspace)
    serviceMocks.submitCurationCandidateDecision.mockResolvedValue({
      candidate: refreshedWorkspace.candidates[1],
      session: refreshedWorkspace.session,
      next_candidate_id: null,
      action_log_entry: null,
    })
    serviceMocks.updateCurationSession.mockResolvedValue({
      session: {
        ...workspace.session,
        current_candidate_id: 'candidate-pending',
      },
      action_log_entry: null,
    })

    renderPage('/curation/session-1/candidate-pending')
    fireEvent.click(await screen.findByRole('button', { name: 'Reject Pending candidate' }))

    await waitFor(() => {
      expect(serviceMocks.submitCurationCandidateDecision).toHaveBeenCalledWith({
        session_id: 'session-1',
        candidate_id: 'candidate-pending',
        action: 'reject',
        advance_queue: false,
      })
    })
  })

  it('submits accept-all-validated decisions without waiting for each prior request to resolve', async () => {
    const workspace = buildWorkspace()
    const workspaceWithValidatedPending: CurationWorkspace = {
      ...workspace,
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
      expect(screen.getByRole('button', { name: 'Accept all validated' })).toBeEnabled()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Accept all validated' }))

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
