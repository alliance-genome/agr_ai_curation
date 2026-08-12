import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from '@mui/material/styles'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { onPDFViewerNavigateEvidence } from '@/components/pdfViewer/pdfEvents'
import type {
  CurationCandidate,
  CurationCandidateValidationResponse,
  CurationWorkspace,
  DomainEnvelopeEvidenceAnchorProjection,
  DomainEnvelopeReviewRowsResponse,
  DomainEnvelopeValidationSummaryProjection,
} from '@/features/curation/types'
import {
  CurationWorkspaceProvider,
  type CurationWorkspaceContextValue,
} from '@/features/curation/workspace/CurationWorkspaceContext'
import type { UseAutosaveReturn } from '@/features/curation/workspace/useAutosave'
import theme from '@/theme'
import InteractiveHorizontalCurationGrid from './InteractiveHorizontalCurationGrid'
import {
  HORIZONTAL_GRID_CONTEXT_COLUMN_KEY,
  type HorizontalGridModel,
  type HorizontalGridValidationProjection,
} from './horizontalGridModel'

const serviceMocks = vi.hoisted(() => ({
  fetchCurationWorkspace: vi.fn(),
  fetchCurationWorkspaceEnvelopeReviewRows: vi.fn(),
  validateCurationCandidate: vi.fn(),
}))

vi.mock('@/features/curation/services/curationWorkspaceService', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/features/curation/services/curationWorkspaceService')>(),
  fetchCurationWorkspace: serviceMocks.fetchCurationWorkspace,
  fetchCurationWorkspaceEnvelopeReviewRows:
    serviceMocks.fetchCurationWorkspaceEnvelopeReviewRows,
  validateCurationCandidate: serviceMocks.validateCurationCandidate,
}))

const emptyValidation: HorizontalGridValidationProjection = {
  summaries: [],
  statuses: [],
  summaryCount: 0,
  findingCount: 0,
  openFindingCount: 0,
}

function evidenceProjection(
  anchorId: string,
  fieldPath: string | null,
  envelopeRevision = 3,
): DomainEnvelopeEvidenceAnchorProjection {
  return {
    anchor_id: anchorId,
    evidence_record_id: `record-${anchorId}`,
    envelope_id: 'envelope-1',
    object_id: 'object-1',
    object_type: 'Reference',
    field_path: fieldPath,
    envelope_revision: envelopeRevision,
    document_id: 'document-1',
    quote: `Evidence for ${fieldPath ?? 'object'}`,
    page_number: 7,
    page_label: null,
    chunk_id: `chunk-${anchorId}`,
    chunk_ids: [`chunk-${anchorId}`],
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
      snippet_text: `Evidence for ${fieldPath ?? 'object'}`,
      sentence_text: `Evidence for ${fieldPath ?? 'object'}`,
      viewer_search_text: `Evidence for ${fieldPath ?? 'object'}`,
      page_number: 7,
      section_title: 'Results',
      chunk_ids: [`chunk-${anchorId}`],
    },
    metadata: {},
  }
}

function resolvedSummary(envelopeRevision = 3): DomainEnvelopeValidationSummaryProjection {
  return {
    summary_id: 'summary-authors',
    envelope_id: 'envelope-1',
    object_id: 'object-1',
    object_type: 'Reference',
    field_path: 'citation.authors',
    envelope_revision: envelopeRevision,
    status: 'resolved',
    highest_severity: null,
    finding_count: 1,
    open_finding_count: 0,
    finding_ids: ['finding-authors'],
    codes: ['authors.valid'],
    messages: ['Authors were validated by the server.'],
    findings: [],
  }
}

function validationProjection(
  summaries: DomainEnvelopeValidationSummaryProjection[],
): HorizontalGridValidationProjection {
  return {
    summaries,
    statuses: summaries.map((summary) => summary.status),
    summaryCount: summaries.length,
    findingCount: summaries.reduce((count, summary) => count + summary.finding_count, 0),
    openFindingCount: summaries.reduce(
      (count, summary) => count + summary.open_finding_count,
      0,
    ),
  }
}

function buildCandidate(overrides: Partial<CurationCandidate> = {}): CurationCandidate {
  return {
    candidate_id: 'candidate-1',
    session_id: 'session-1',
    source: 'extracted',
    status: 'pending',
    order: 0,
    adapter_key: 'reference',
    display_label: 'Reference one',
    projection_ref: {
      envelope_id: 'envelope-1',
      object_id: 'object-1',
      envelope_revision: 3,
    },
    draft: {
      draft_id: 'draft-1',
      candidate_id: 'candidate-1',
      adapter_key: 'reference',
      version: 2,
      fields: [
        {
          field_key: 'authors',
          label: 'Authors',
          value: ['Ada Lovelace', 'Grace Hopper'],
          seed_value: ['Ada Lovelace'],
          field_type: 'json',
          group_key: 'citation_details',
          group_label: 'Citation details',
          order: 0,
          required: true,
          read_only: false,
          dirty: true,
          stale_validation: false,
          evidence_anchor_ids: ['field-evidence'],
          validation_result: null,
          metadata: {
            source_field_path: 'citation.authors',
            widget: 'reference_author_list',
            helper_text: 'One author per line.',
          },
        },
        {
          field_key: 'locked',
          label: 'Locked identifier',
          value: 'PMID:1',
          seed_value: 'PMID:1',
          field_type: 'string',
          group_key: 'identifiers',
          group_label: 'Identifiers',
          order: 1,
          required: false,
          read_only: true,
          dirty: false,
          stale_validation: false,
          evidence_anchor_ids: [],
          validation_result: null,
          metadata: { source_field_path: 'identifiers.pmid' },
        },
      ],
      notes: null,
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-01T00:00:00Z',
      metadata: {},
    },
    evidence_anchors: [],
    evidence_anchor_projections: [],
    validation_summary_projections: [],
    validation: null,
    evidence_summary: null,
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    metadata: {},
    ...overrides,
  }
}

function buildWorkspace(candidate = buildCandidate()): CurationWorkspace {
  return {
    session: {
      session_id: 'session-1',
      status: 'in_progress',
      adapter: { adapter_key: 'reference', display_label: 'Reference', metadata: {} },
      document: { document_id: 'document-1', title: 'Reference paper' },
      progress: {
        total_candidates: 1,
        reviewed_candidates: 0,
        pending_candidates: 1,
        accepted_candidates: 0,
        rejected_candidates: 0,
        manual_candidates: 0,
      },
      current_candidate_id: null,
      prepared_at: '2026-08-01T00:00:00Z',
      warnings: [],
      tags: [],
      session_version: 1,
      extraction_results: [],
    },
    candidates: [candidate],
    active_candidate_id: null,
    queue_context: null,
    action_log: [],
    submission_history: [],
    saved_view_context: null,
  }
}

function buildModel({
  authorsEvidence = [evidenceProjection('field-evidence', 'citation.authors')],
  authorsValidation = emptyValidation,
  objectEvidence = [evidenceProjection('object-evidence', null)],
}: {
  authorsEvidence?: DomainEnvelopeEvidenceAnchorProjection[]
  authorsValidation?: HorizontalGridValidationProjection
  objectEvidence?: DomainEnvelopeEvidenceAnchorProjection[]
} = {}): HorizontalGridModel {
  return {
    columns: [
      {
        key: HORIZONTAL_GRID_CONTEXT_COLUMN_KEY,
        kind: 'context',
        fieldPath: null,
        label: 'Object',
        order: -1,
        required: false,
        readOnly: true,
        groupKey: null,
        groupLabel: null,
      },
      {
        key: 'field:authors',
        kind: 'field',
        fieldPath: 'citation.authors',
        label: 'Authors',
        order: 0,
        required: true,
        readOnly: false,
        groupKey: 'citation_details',
        groupLabel: 'Citation details',
      },
      {
        key: 'field:locked',
        kind: 'field',
        fieldPath: 'identifiers.pmid',
        label: 'Locked identifier',
        order: 1,
        required: false,
        readOnly: true,
        groupKey: 'identifiers',
        groupLabel: 'Identifiers',
      },
      {
        key: 'field:missing',
        kind: 'field',
        fieldPath: 'citation.title',
        label: 'Missing field',
        order: 2,
        required: false,
        readOnly: false,
        groupKey: 'citation_details',
        groupLabel: 'Citation details',
      },
    ],
    rows: [
      {
        candidateId: 'candidate-1',
        contextCell: {
          columnKey: HORIZONTAL_GRID_CONTEXT_COLUMN_KEY,
          value: {
            candidateId: 'candidate-1',
            objectId: 'object-1',
            envelopeId: 'envelope-1',
            envelopeRevision: 3,
            objectType: 'Reference',
            objectRole: 'curatable_unit',
            identityLabel: 'Reference one',
            secondaryLabel: 'Paper citation',
            candidateStatus: 'pending',
            candidateSource: 'extracted',
            candidateMetadata: {},
            summaryFields: [],
            reviewRowMetadata: {},
          },
          evidence: objectEvidence,
          validation: emptyValidation,
        },
        cells: [
          {
            columnKey: 'field:authors',
            fieldKey: 'authors',
            fieldPath: 'citation.authors',
            hasField: true,
            value: ['Ada Lovelace', 'Grace Hopper'],
            required: true,
            readOnly: false,
            staleValidation: false,
            fieldValidation: null,
            evidence: authorsEvidence,
            validation: authorsValidation,
          },
          {
            columnKey: 'field:locked',
            fieldKey: 'locked',
            fieldPath: 'identifiers.pmid',
            hasField: true,
            value: 'PMID:1',
            required: false,
            readOnly: true,
            staleValidation: false,
            fieldValidation: null,
            evidence: [],
            validation: emptyValidation,
          },
          {
            columnKey: 'field:missing',
            fieldKey: null,
            fieldPath: 'citation.title',
            hasField: false,
            value: null,
            required: null,
            readOnly: null,
            staleValidation: null,
            fieldValidation: null,
            evidence: [],
            validation: emptyValidation,
          },
        ],
        evidence: [...objectEvidence, ...authorsEvidence],
        validation: authorsValidation,
        unmappedEvidence: [],
        unmappedValidation: emptyValidation,
      },
    ],
  }
}

function validationResponse(candidate: CurationCandidate): CurationCandidateValidationResponse {
  return {
    candidate,
    validation_snapshot: {
      snapshot_id: 'snapshot-1',
      scope: 'candidate',
      session_id: 'session-1',
      candidate_id: candidate.candidate_id,
      adapter_key: candidate.adapter_key,
      state: 'completed',
      field_results: {},
      summary: {
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
      warnings: [],
    },
  }
}

function createAutosave(overrides: Partial<UseAutosaveReturn> = {}): UseAutosaveReturn {
  return {
    debounceMs: 10,
    dirtyFieldKeys: [],
    isDirty: false,
    isSaving: false,
    warning: null,
    queueFieldChange: vi.fn(),
    queueFieldChanges: vi.fn(),
    flush: vi.fn().mockResolvedValue(true),
    clearWarning: vi.fn(),
    ...overrides,
  }
}

function renderGrid({
  autosave = createAutosave(),
  model = buildModel(),
  workspace: initialWorkspace = buildWorkspace(),
}: {
  autosave?: UseAutosaveReturn
  model?: HorizontalGridModel | ((workspace: CurationWorkspace) => HorizontalGridModel)
  workspace?: CurationWorkspace
} = {}) {
  const setActiveCandidate = vi.fn()
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })

  function Harness() {
    const [workspace, setWorkspace] = useState(initialWorkspace)
    const [activeCandidateId, setActiveCandidateId] = useState<string | null>(null)
    const activeCandidate = workspace.candidates.find(
      (candidate) => candidate.candidate_id === activeCandidateId,
    ) ?? null
    const contextValue: CurationWorkspaceContextValue = {
      workspace,
      setWorkspace,
      session: workspace.session,
      candidates: workspace.candidates,
      activeCandidateId,
      activeCandidate,
      setActiveCandidate: (candidateId) => {
        setActiveCandidate(candidateId)
        setActiveCandidateId(candidateId)
      },
      autosave,
    }
    const renderedModel = typeof model === 'function' ? model(workspace) : model

    return (
      <CurationWorkspaceProvider value={contextValue}>
        <InteractiveHorizontalCurationGrid model={renderedModel} />
      </CurationWorkspaceProvider>
    )
  }

  render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={theme}>
        <Harness />
      </ThemeProvider>
    </QueryClientProvider>,
  )

  return { autosave, queryClient, setActiveCandidate }
}

function createDeferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, reject, resolve }
}

beforeEach(() => {
  serviceMocks.fetchCurationWorkspace.mockResolvedValue(buildWorkspace())
  serviceMocks.fetchCurationWorkspaceEnvelopeReviewRows.mockResolvedValue([])
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('InteractiveHorizontalCurationGrid', () => {
  it('selects canonical candidates and dispatches exact field and context evidence commands', async () => {
    const user = userEvent.setup()
    const navigateEvidence = vi.fn()
    const unsubscribe = onPDFViewerNavigateEvidence(navigateEvidence)
    const { setActiveCandidate } = renderGrid()

    await user.click(screen.getByRole('button', {
      name: 'Select Authors for citation.authors',
    }))
    expect(setActiveCandidate).toHaveBeenCalledWith('candidate-1')

    await user.click(screen.getByRole('button', { name: 'Select Reference one' }))
    expect(setActiveCandidate).toHaveBeenCalledWith('candidate-1')

    await user.click(screen.getByRole('button', { name: 'Show evidence 1 for Authors' }))
    expect(navigateEvidence).toHaveBeenLastCalledWith(expect.objectContaining({
      detail: {
        command: expect.objectContaining({
          anchorId: 'field-evidence',
          searchText: 'Evidence for citation.authors',
          pageNumber: 7,
          mode: 'select',
        }),
      },
    }))

    await user.click(screen.getByRole('button', {
      name: 'Show object evidence 1 for Reference one',
    }))
    expect(navigateEvidence).toHaveBeenLastCalledWith(expect.objectContaining({
      detail: {
        command: expect.objectContaining({ anchorId: 'object-evidence' }),
      },
    }))
    unsubscribe()
  })

  it('uses the adapter FieldRow renderer and delegates edits and reverts to autosave', async () => {
    const user = userEvent.setup()
    const autosave = createAutosave({ warning: 'Draft version conflict; edits remain local.' })
    renderGrid({ autosave })

    await user.click(screen.getByRole('button', { name: 'Edit Authors' }))

    expect(screen.getByRole('dialog', { name: 'Edit Authors' })).toBeInTheDocument()
    expect(screen.getByText('Draft version conflict; edits remain local.')).toBeInTheDocument()
    expect(screen.getByText('Required')).toBeInTheDocument()
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument()
    const authors = screen.getByLabelText('Authors')
    expect(authors).toHaveValue('Ada Lovelace\nGrace Hopper')

    fireEvent.change(authors, { target: { value: 'Ada Lovelace\n\nKatherine Johnson' } })
    expect(autosave.queueFieldChange).toHaveBeenCalledWith({
      field_key: 'authors',
      value: ['Ada Lovelace', '', 'Katherine Johnson'],
    })

    await user.click(screen.getByRole('button', { name: 'Revert' }))
    expect(autosave.queueFieldChange).toHaveBeenLastCalledWith({
      field_key: 'authors',
      revert_to_seed: true,
    })
  })

  it('does not expose edit or validation actions for read-only or unavailable cells', () => {
    renderGrid()

    expect(screen.queryByRole('button', { name: 'Edit Locked identifier' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Validate Locked identifier' })).not.toBeInTheDocument()
    expect(screen.getByText('Not available')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit Missing field' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Validate Missing field' })).not.toBeInTheDocument()
  })

  it('disables field mutations while autosave is in progress', () => {
    renderGrid({ autosave: createAutosave({ isSaving: true }) })

    expect(screen.getByRole('button', { name: 'Edit Authors' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Validate Authors' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Show evidence 1 for Authors' })).toBeEnabled()
  })

  it('keeps validation loading until the authoritative workspace and review rows are refreshed', async () => {
    const user = userEvent.setup()
    const navigateEvidence = vi.fn()
    const unsubscribe = onPDFViewerNavigateEvidence(navigateEvidence)
    const deferred = createDeferred<CurationCandidateValidationResponse>()
    const reviewRowsDeferred = createDeferred<DomainEnvelopeReviewRowsResponse[]>()
    serviceMocks.validateCurationCandidate.mockReturnValue(deferred.promise)
    const serverEvidence = evidenceProjection(
      'server-field-evidence',
      'citation.authors',
      4,
    )
    const serverSummary = resolvedSummary(4)
    const serverCandidate = buildCandidate({
      projection_ref: {
        envelope_id: 'envelope-1',
        object_id: 'object-1',
        envelope_revision: 4,
      },
      evidence_anchor_projections: [serverEvidence],
      validation_summary_projections: [serverSummary],
    })
    serverCandidate.draft = {
      ...serverCandidate.draft,
      fields: serverCandidate.draft.fields.map((field) =>
        field.field_key === 'authors'
          ? { ...field, value: ['Server Author'], dirty: false, stale_validation: false }
          : field,
      ),
    }
    const serverWorkspace = {
      ...buildWorkspace(serverCandidate),
      evidence_anchor_projections: [serverEvidence],
      validation_summary_projections: [serverSummary],
    }
    const serverReviewRows: DomainEnvelopeReviewRowsResponse[] = [{
      envelope_id: 'envelope-1',
      envelope_revision: 4,
      row_count: 0,
      rows: [],
    }]
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(serverWorkspace)
    serviceMocks.fetchCurationWorkspaceEnvelopeReviewRows.mockReturnValue(
      reviewRowsDeferred.promise,
    )
    const { autosave, queryClient } = renderGrid({
      model: (workspace) => buildModel({
        authorsEvidence: (workspace.evidence_anchor_projections ?? []).filter(
          (projection) => projection.field_path === 'citation.authors',
        ),
        authorsValidation: validationProjection(
          workspace.validation_summary_projections ?? [],
        ),
        objectEvidence: (workspace.evidence_anchor_projections ?? []).filter(
          (projection) => projection.field_path === null,
        ),
      }),
    })
    const authorsCell = screen.getByTestId('horizontal-grid-field-authors')
    expect(within(authorsCell).getByRole('img', { name: 'AI unconfirmed' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Validate Authors' }))

    await waitFor(() => {
      expect(autosave.flush).toHaveBeenCalledTimes(1)
      expect(serviceMocks.validateCurationCandidate).toHaveBeenCalledWith({
        session_id: 'session-1',
        candidate_id: 'candidate-1',
        field_keys: ['authors'],
      })
    })
    expect(screen.getByRole('button', { name: 'Validate Authors' })).toBeDisabled()
    expect(screen.getByLabelText('Validating Authors')).toBeInTheDocument()
    expect(within(authorsCell).getByRole('img', { name: 'AI unconfirmed' })).toBeInTheDocument()

    await act(async () => {
      deferred.resolve(validationResponse(serverCandidate))
      await deferred.promise
    })

    await waitFor(() => {
      expect(serviceMocks.fetchCurationWorkspace).toHaveBeenCalledWith('session-1')
      expect(serviceMocks.fetchCurationWorkspaceEnvelopeReviewRows).toHaveBeenCalledWith(
        serverWorkspace,
      )
    })
    expect(screen.getByRole('button', { name: 'Validate Authors' })).toBeDisabled()
    expect(screen.getByLabelText('Validating Authors')).toBeInTheDocument()
    expect(screen.queryByText('Server Author')).not.toBeInTheDocument()

    await act(async () => {
      reviewRowsDeferred.resolve(serverReviewRows)
      await reviewRowsDeferred.promise
    })

    expect(await screen.findByText('Server Author')).toBeInTheDocument()
    expect(within(screen.getByTestId('horizontal-grid-field-authors')).getByRole(
      'img',
      { name: 'Resolved' },
    )).toBeInTheDocument()
    expect(screen.getByText('Authors were validated by the server.')).toBeInTheDocument()
    expect(queryClient.getQueryData([
      'curation-workspace-envelope-review-rows',
      'session-1',
      [{ envelope_id: 'envelope-1', envelope_revision: 4 }],
    ])).toEqual(serverReviewRows)

    await user.click(screen.getByRole('button', { name: 'Show evidence 1 for Authors' }))
    expect(navigateEvidence).toHaveBeenLastCalledWith(expect.objectContaining({
      detail: {
        command: expect.objectContaining({
          anchorId: 'server-field-evidence',
          searchText: 'Evidence for citation.authors',
        }),
      },
    }))
    unsubscribe()
  })

  it('derives resolved status and validation messages only from authoritative projections', () => {
    const summary = resolvedSummary()
    renderGrid({ model: buildModel({ authorsValidation: validationProjection([summary]) }) })

    const authorsCell = screen.getByTestId('horizontal-grid-field-authors')
    expect(within(authorsCell).getByRole('img', { name: 'Resolved' })).toBeInTheDocument()
    expect(within(authorsCell).getByText('Authors were validated by the server.')).toBeInTheDocument()
  })

  it('surfaces a failed pre-validation save and does not call validation early', async () => {
    const user = userEvent.setup()
    const autosave = createAutosave({ flush: vi.fn().mockResolvedValue(false) })
    renderGrid({ autosave })

    await user.click(screen.getByRole('button', { name: 'Validate Authors' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Unable to save pending field changes before validation.',
    )
    expect(serviceMocks.validateCurationCandidate).not.toHaveBeenCalled()
  })

  it('surfaces version conflicts returned while pending edits are flushed', async () => {
    const user = userEvent.setup()
    const autosave = createAutosave({
      flush: vi.fn().mockRejectedValue(new Error('Draft version conflict. Refresh and retry.')),
    })
    renderGrid({ autosave })

    await user.click(screen.getByRole('button', { name: 'Validate Authors' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Draft version conflict. Refresh and retry.',
    )
    expect(serviceMocks.validateCurationCandidate).not.toHaveBeenCalled()
  })

  it('surfaces server validation errors without changing the projected field status', async () => {
    const user = userEvent.setup()
    serviceMocks.validateCurationCandidate.mockRejectedValue(
      new Error('Server validation failed for Authors.'),
    )
    renderGrid()

    await user.click(screen.getByRole('button', { name: 'Validate Authors' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Server validation failed for Authors.',
    )
    expect(within(screen.getByTestId('horizontal-grid-field-authors')).getByRole(
      'img',
      { name: 'AI unconfirmed' },
    )).toBeInTheDocument()
  })

  it('surfaces a failed authoritative validation snapshot after merging the candidate', async () => {
    const user = userEvent.setup()
    const serverCandidate = buildCandidate()
    serverCandidate.draft = {
      ...serverCandidate.draft,
      fields: serverCandidate.draft.fields.map((field) =>
        field.field_key === 'authors'
          ? { ...field, value: ['Author from failed validation'] }
          : field,
      ),
    }
    const response = validationResponse(serverCandidate)
    response.validation_snapshot = {
      ...response.validation_snapshot,
      state: 'failed',
      warnings: ['The configured Authors validator is unavailable.'],
    }
    serviceMocks.validateCurationCandidate.mockResolvedValue(response)
    serviceMocks.fetchCurationWorkspace.mockResolvedValue(buildWorkspace(serverCandidate))
    renderGrid()

    await user.click(screen.getByRole('button', { name: 'Validate Authors' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The configured Authors validator is unavailable.',
    )
    expect(screen.getByText('Author from failed validation')).toBeInTheDocument()
  })
})
