import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from '@mui/material/styles'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { onPDFViewerNavigateEvidence } from '@/components/pdfViewer/pdfEvents'
import type {
  CurationCandidate,
  CurationWorkspace,
  DomainEnvelopeEvidenceAnchorProjection,
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

function unresolvedEvidenceProjection(
  anchorId: string,
  fieldPath: string | null,
): DomainEnvelopeEvidenceAnchorProjection {
  const projection = evidenceProjection(anchorId, fieldPath)

  return {
    ...projection,
    quote: null,
    page_number: null,
    page_label: null,
    chunk_id: null,
    chunk_ids: [],
    section_title: null,
    subsection_title: null,
    figure_reference: null,
    table_reference: null,
    anchor: {
      anchor_kind: 'snippet',
      locator_quality: 'unresolved',
      supports_decision: 'neutral',
      chunk_ids: [],
    },
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
            state: authorsValidation.statuses.length > 0
              && authorsValidation.statuses.every((status) => status === 'resolved' || status === 'waived')
              ? 'resolved'
              : 'ai-unconfirmed',
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
            state: 'ai-unconfirmed',
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
            state: null,
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

beforeEach(() => {
  serviceMocks.fetchCurationWorkspace.mockResolvedValue(buildWorkspace())
  serviceMocks.fetchCurationWorkspaceEnvelopeReviewRows.mockResolvedValue([])
})

afterEach(() => {
  delete window.__pdfViewerEvidenceDebug
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

describe('InteractiveHorizontalCurationGrid', () => {
  it('selects canonical candidates and dispatches exact field and context evidence commands', async () => {
    const user = userEvent.setup()
    const navigateEvidence = vi.fn()
    const unsubscribe = onPDFViewerNavigateEvidence(navigateEvidence)
    const { setActiveCandidate } = renderGrid()

    await user.click(screen.getByRole('button', {
      name: /Select Authors for citation\.authors/,
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

  it('opens prototype-fidelity read-only evidence details while focusing the PDF', async () => {
    const user = userEvent.setup()
    const navigateEvidence = vi.fn()
    const unsubscribe = onPDFViewerNavigateEvidence(navigateEvidence)
    renderGrid()

    await user.click(screen.getByRole('button', { name: 'Show evidence 1 for Authors' }))

    const details = screen.getByRole('dialog', { name: /Authors:/ })
    expect(within(details).getByText('Evidence & validation details')).toBeInTheDocument()
    expect(within(details).getByText('Highlighted passage from the paper')).toBeInTheDocument()
    expect(within(details).getByText('Evidence for citation.authors')).toBeInTheDocument()
    expect(within(details).getByText(/Current status:/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Validate/ })).not.toBeInTheDocument()
    expect(navigateEvidence).toHaveBeenCalledTimes(1)
    expect(within(details).getByRole('button', { name: 'Close evidence details' })).toHaveFocus()

    await user.click(screen.getByRole('button', {
      name: 'Show object evidence 1 for Reference one',
    }))
    expect(screen.getByRole('dialog', { name: /Object evidence:/ })).toBeInTheDocument()

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('button', {
      name: 'Show object evidence 1 for Reference one',
    })).toHaveFocus()

    unsubscribe()
  })

  it('preserves unmatched field provenance on the context evidence trigger and popup', async () => {
    const user = userEvent.setup()
    const navigateEvidence = vi.fn()
    const consoleInfo = vi.spyOn(console, 'info').mockImplementation(() => undefined)
    window.__pdfViewerEvidenceDebug = {
      enabled: true,
      storageKey: 'test-evidence-debug',
      setEnabled: vi.fn((enabled: boolean) => enabled),
      getEntries: vi.fn(() => []),
      clearEntries: vi.fn(),
      getLastResult: vi.fn(() => null),
    }
    const unsubscribe = onPDFViewerNavigateEvidence(navigateEvidence)
    renderGrid({
      model: buildModel({
        objectEvidence: [evidenceProjection('mention-evidence', 'Gene.Symbol')],
      }),
    })

    await user.click(screen.getByRole('button', {
      name: 'Show field evidence (Gene.Symbol) 1 for Reference one',
    }))

    expect(screen.getByRole('dialog', {
      name: 'Field evidence (Gene.Symbol): Reference one',
    })).toBeInTheDocument()
    expect(consoleInfo).toHaveBeenCalledWith(
      '[PDF EVIDENCE DEBUG] Dispatching shared evidence navigation',
      expect.objectContaining({ fieldPath: 'Gene.Symbol' }),
    )
    expect(navigateEvidence).toHaveBeenCalledTimes(1)

    unsubscribe()
  })

  it('disables unresolved field and context evidence without dispatching navigation', () => {
    const navigateEvidence = vi.fn()
    const unsubscribe = onPDFViewerNavigateEvidence(navigateEvidence)
    const { setActiveCandidate } = renderGrid({
      model: buildModel({
        authorsEvidence: [unresolvedEvidenceProjection('field-unresolved', 'citation.authors')],
        objectEvidence: [unresolvedEvidenceProjection('object-unresolved', null)],
      }),
    })

    const fieldEvidence = screen.getByRole('button', {
      name: 'Evidence 1 for Authors has no navigable PDF location',
    })
    const objectEvidence = screen.getByRole('button', {
      name: 'Object evidence 1 for Reference one has no navigable PDF location',
    })
    expect(fieldEvidence).toBeDisabled()
    expect(objectEvidence).toBeDisabled()

    fireEvent.click(fieldEvidence)
    fireEvent.click(objectEvidence)

    expect(navigateEvidence).not.toHaveBeenCalled()
    expect(setActiveCandidate).not.toHaveBeenCalled()
    unsubscribe()
  })

  it('uses the adapter FieldRow renderer and saves or restores only after confirmation', async () => {
    const user = userEvent.setup()
    const autosave = createAutosave({ warning: 'Draft version conflict; edits remain local.' })
    renderGrid({ autosave })

    await user.click(screen.getByRole('button', { name: 'Edit Authors' }))

    expect(screen.getByRole('dialog', { name: 'Edit Authors' })).toBeInTheDocument()
    expect(screen.getByText('Draft version conflict; edits remain local.')).toBeInTheDocument()
    const authors = screen.getByLabelText('Authors')
    expect(authors).toHaveValue('Ada Lovelace\nGrace Hopper')

    fireEvent.change(authors, { target: { value: 'Ada Lovelace\n\nKatherine Johnson' } })
    expect(autosave.queueFieldChange).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Save value' }))
    expect(autosave.queueFieldChange).toHaveBeenCalledWith({
      field_key: 'authors',
      value: ['Ada Lovelace', '', 'Katherine Johnson'],
    })
    expect(autosave.flush).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: 'Edit Authors' }))
    await user.click(screen.getByRole('button', { name: 'Restore extracted value' }))
    await user.click(screen.getByRole('button', { name: 'Save value' }))
    expect(autosave.queueFieldChange).toHaveBeenLastCalledWith({
      field_key: 'authors',
      revert_to_seed: true,
    })
    expect(autosave.flush).toHaveBeenCalledTimes(2)
  })

  it('does not expose edit or validation actions for read-only or unavailable cells', () => {
    renderGrid()

    expect(screen.queryByRole('button', { name: 'Edit Locked identifier' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Validate Locked identifier' })).not.toBeInTheDocument()
    expect(screen.getByText('Not available')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit Missing field' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Validate Missing field' })).not.toBeInTheDocument()
  })

  it('keeps editing and evidence available while validation execution stays unmounted', () => {
    const autosave = createAutosave({ isSaving: true })
    renderGrid({ autosave })

    expect(screen.getByRole('button', { name: 'Edit Authors' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Show evidence 1 for Authors' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: /Validate/ })).not.toBeInTheDocument()
    expect(serviceMocks.validateCurationCandidate).not.toHaveBeenCalled()
  })

  it('shows authoritative validation details compactly without mounting an action', () => {
    const summary = {
      ...resolvedSummary(),
      messages: [
        'Authors were validated by the server.',
        'A second authoritative validation detail.',
      ],
    }
    renderGrid({ model: buildModel({ authorsValidation: validationProjection([summary]) }) })

    const authorsCell = screen.getByTestId('horizontal-grid-field-authors')
    expect(authorsCell).toHaveAccessibleName(/Curator validated/)
    expect(screen.getByRole('img', { name: /Authors were validated by the server/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Validate/ })).not.toBeInTheDocument()
    expect(serviceMocks.validateCurationCandidate).not.toHaveBeenCalled()
  })
})
