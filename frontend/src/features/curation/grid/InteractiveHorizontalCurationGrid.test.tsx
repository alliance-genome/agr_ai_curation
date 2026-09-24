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
  DomainEnvelopeReviewResolvedValue,
  DomainEnvelopeValidationSummaryProjection,
} from '@/features/curation/types'
import {
  CurationWorkspaceProvider,
  type CurationWorkspaceContextValue,
} from '@/features/curation/workspace/CurationWorkspaceContext'
import type { UseAutosaveReturn } from '@/features/curation/workspace/useAutosave'
import theme, { createAppTheme, type ThemeMode } from '@/theme'
import InteractiveHorizontalCurationGrid from './InteractiveHorizontalCurationGrid'
import {
  HORIZONTAL_GRID_CONTEXT_COLUMN_KEY,
  type HorizontalGridModel,
  type HorizontalGridValidationProjection,
} from './horizontalGridModel'

const serviceMocks = vi.hoisted(() => ({
  fetchCurationWorkspace: vi.fn(),
  fetchCurationWorkspaceEnvelopeReviewRows: vi.fn(),
  patchCurationEnvelopeField: vi.fn(),
  validateCurationCandidate: vi.fn(),
}))

vi.mock('@/features/curation/services/curationWorkspaceService', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/features/curation/services/curationWorkspaceService')>(),
  fetchCurationWorkspace: serviceMocks.fetchCurationWorkspace,
  fetchCurationWorkspaceEnvelopeReviewRows:
    serviceMocks.fetchCurationWorkspaceEnvelopeReviewRows,
  patchCurationEnvelopeField: serviceMocks.patchCurationEnvelopeField,
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
            rationale: null,
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
            displayText: 'Ada Lovelace, Grace Hopper',
            resolution: null,
            resolutionDetails: [],
            resolutionLinesId: null,
            resolutionDescribedBy: [],
            required: true,
            readOnly: false,
            curatorOverride: false,
            overrideDisagreements: [],
            overrideTargets: [],
            staleValidation: false,
            state: authorsValidation.statuses.length > 0
              && authorsValidation.statuses.every((status) => status === 'resolved' || status === 'waived')
              ? 'resolved'
              : 'ai-unconfirmed',
            fieldValidation: null,
            evidence: authorsEvidence,
            validation: authorsValidation,
            extractorComparison: null,
          },
          {
            columnKey: 'field:locked',
            fieldKey: 'locked',
            fieldPath: 'identifiers.pmid',
            hasField: true,
            value: 'PMID:1',
            displayText: 'PMID:1',
            resolution: null,
            resolutionDetails: [],
            resolutionLinesId: null,
            resolutionDescribedBy: [],
            required: false,
            readOnly: true,
            curatorOverride: false,
            overrideDisagreements: [],
            overrideTargets: [],
            staleValidation: false,
            state: 'ai-unconfirmed',
            fieldValidation: null,
            evidence: [],
            validation: emptyValidation,
            extractorComparison: null,
          },
          {
            columnKey: 'field:missing',
            fieldKey: null,
            fieldPath: 'citation.title',
            hasField: false,
            value: null,
            displayText: null,
            resolution: null,
            resolutionDetails: [],
            resolutionLinesId: null,
            resolutionDescribedBy: [],
            required: null,
            readOnly: null,
            curatorOverride: false,
            overrideDisagreements: [],
            overrideTargets: [],
            staleValidation: null,
            state: null,
            fieldValidation: null,
            evidence: [],
            validation: emptyValidation,
            extractorComparison: null,
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
    submitEnvelopeEdit: vi.fn().mockResolvedValue(undefined),
    clearWarning: vi.fn(),
    ...overrides,
  }
}

function renderGrid({
  autosave = createAutosave(),
  model = buildModel(),
  mode,
  workspace: initialWorkspace = buildWorkspace(),
}: {
  autosave?: UseAutosaveReturn
  model?: HorizontalGridModel | ((workspace: CurationWorkspace) => HorizontalGridModel)
  mode?: ThemeMode
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
      <ThemeProvider theme={mode ? createAppTheme(mode) : theme}>
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
  it('shows the rationale read-only inside the selectable row context', () => {
    const model = buildModel()
    model.rows[0]!.contextCell.value.rationale = { value: null }
    renderGrid({ model })

    const context = screen.getByTestId('horizontal-grid-context-candidate-1')
    expect(within(context).getByText('Not recorded')).toBeInTheDocument()
    expect(context.querySelector('input, textarea, [contenteditable="true"]')).toBeNull()
    expect(screen.queryByRole('button', { name: /edit.*rationale/i })).toBeNull()
  })

  it('selects canonical candidates and dispatches exact field and context evidence commands', async () => {
    const user = userEvent.setup()
    const navigateEvidence = vi.fn()
    const unsubscribe = onPDFViewerNavigateEvidence(navigateEvidence)
    const { setActiveCandidate } = renderGrid()

    expect(screen.getByRole('group', {
      name: 'Actions for Authors: Ada Lovelace, Grace Hopper in Reference one',
    })).toBeInTheDocument()

    await user.click(screen.getByRole('button', {
      name: /Select Authors for citation\.authors/,
    }))
    expect(setActiveCandidate).toHaveBeenCalledWith('candidate-1')

    await user.click(screen.getByRole('button', { name: 'Select Reference one' }))
    expect(setActiveCandidate).toHaveBeenCalledWith('candidate-1')

    await user.click(screen.getByRole('button', {
      name: /^Show evidence and validation details for Authors:/,
    }))
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

    await user.click(screen.getByRole('button', {
      name: /^Show evidence and validation details for Authors:/,
    }))

    const details = screen.getByRole('dialog', { name: /Authors:/ })
    expect(details).toHaveStyle({
      display: 'flex',
      maxHeight: 'calc(100dvh - 24px)',
      overflow: 'hidden',
    })
    expect(within(details).getByTestId('horizontal-grid-evidence-scroll-region')).toHaveStyle({
      minHeight: '0',
      overflowY: 'auto',
      overscrollBehavior: 'contain',
      scrollbarGutter: 'stable',
    })
    expect(within(details).getByText('Evidence & validation details')).toBeInTheDocument()
    expect(within(details).getByText('Highlighted passage from the paper')).toBeInTheDocument()
    expect(within(details).getByText('Evidence for citation.authors')).toBeInTheDocument()
    expect(within(details).getByLabelText('Current status: Not validated')).toBeInTheDocument()
    expect(within(details).queryByText(/^Authors resolved to /)).not.toBeInTheDocument()
    expect(within(details).queryByRole('button', { name: /Validate/ })).not.toBeInTheDocument()
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

  it('keeps every field evidence anchor independently focusable from one details popup', async () => {
    const user = userEvent.setup()
    const navigateEvidence = vi.fn()
    const unsubscribe = onPDFViewerNavigateEvidence(navigateEvidence)
    renderGrid({
      model: buildModel({
        authorsEvidence: [
          evidenceProjection('field-evidence-1', 'citation.authors'),
          evidenceProjection('field-evidence-2', 'citation.authors'),
        ],
      }),
    })

    await user.click(screen.getByRole('button', {
      name: /^Show evidence and validation details for Authors:/,
    }))
    expect(navigateEvidence).toHaveBeenLastCalledWith(expect.objectContaining({
      detail: { command: expect.objectContaining({ anchorId: 'field-evidence-1' }) },
    }))

    await user.click(screen.getByRole('button', {
      name: 'Focus evidence 2 for Authors in paper',
    }))
    expect(navigateEvidence).toHaveBeenLastCalledWith(expect.objectContaining({
      detail: { command: expect.objectContaining({ anchorId: 'field-evidence-2' }) },
    }))
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

  it('opens unresolved field details without dispatching PDF navigation', async () => {
    const user = userEvent.setup()
    const navigateEvidence = vi.fn()
    const unsubscribe = onPDFViewerNavigateEvidence(navigateEvidence)
    const { setActiveCandidate } = renderGrid({
      model: buildModel({
        authorsEvidence: [unresolvedEvidenceProjection('field-unresolved', 'citation.authors')],
        objectEvidence: [unresolvedEvidenceProjection('object-unresolved', null)],
      }),
    })

    const fieldDetails = screen.getByRole('button', {
      name: /^Show evidence and validation details for Authors:/,
    })
    const objectEvidence = screen.getByRole('button', {
      name: 'Object evidence 1 for Reference one has no navigable PDF location',
    })
    expect(objectEvidence).toBeDisabled()

    await user.click(fieldDetails)
    fireEvent.click(objectEvidence)

    expect(screen.getByRole('dialog', { name: /Authors:/ })).toHaveTextContent(
      'No quoted passage is available for this evidence anchor.',
    )
    expect(navigateEvidence).not.toHaveBeenCalled()
    expect(setActiveCandidate).toHaveBeenCalledWith('candidate-1')
    unsubscribe()
  })

  it('uses the adapter FieldRow renderer and saves or restores only after confirmation', async () => {
    const user = userEvent.setup()
    const autosave = createAutosave({ warning: 'Draft version conflict; edits remain local.' })
    renderGrid({ autosave })

    await user.click(screen.getByRole('button', { name: /^Edit Authors:/ }))

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

    await user.click(screen.getByRole('button', { name: /^Edit Authors:/ }))
    await user.click(screen.getByRole('button', { name: 'Restore extracted value' }))
    await user.click(screen.getByRole('button', { name: 'Save value' }))
    expect(autosave.queueFieldChange).toHaveBeenLastCalledWith({
      field_key: 'authors',
      revert_to_seed: true,
    })
    expect(autosave.flush).toHaveBeenCalledTimes(2)
  })

  it('keeps preview validation available for read-only fields but not unavailable cells', () => {
    renderGrid()

    expect(screen.getByRole('button', {
      name: /^Edit unavailable for Locked identifier:/,
    })).toBeDisabled()
    expect(screen.getByRole('button', {
      name: /^Show evidence and validation details for Locked identifier:/,
    })).toBeEnabled()
    expect(screen.getByRole('button', { name: /^Validate Locked identifier:/ })).toBeEnabled()
    expect(screen.getByText('Not available')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Edit Missing field:/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Validate Missing field:/ })).not.toBeInTheDocument()
  })

  it('toggles preview validation locally while execution stays unmounted', async () => {
    const user = userEvent.setup()
    const autosave = createAutosave({ isSaving: true })
    renderGrid({ autosave })

    expect(screen.getByRole('button', { name: /^Edit Authors:/ })).toBeDisabled()
    expect(screen.getByRole('button', {
      name: /^Show evidence and validation details for Authors:/,
    })).toBeEnabled()
    const validateAuthors = screen.getByRole('button', { name: /^Validate Authors:/ })
    expect(validateAuthors).toHaveAttribute('aria-pressed', 'false')

    await user.click(validateAuthors)

    expect(screen.getByRole('button', { name: /^Mark as not validated Authors:/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByRole('status')).toHaveTextContent(
      'Authors marked curator validated for this preview only. No validation was run or saved.',
    )
    expect(serviceMocks.validateCurationCandidate).not.toHaveBeenCalled()
    expect(autosave.queueFieldChange).not.toHaveBeenCalled()
    expect(autosave.flush).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Comfortable' }))
    expect(screen.getByRole('status')).toHaveTextContent('Comfortable row density enabled')
  })

  it('mirrors the prototype row Validate control and keeps its progress local', async () => {
    const user = userEvent.setup()
    const autosave = createAutosave()
    renderGrid({ autosave, mode: 'light' })

    const validateRow = screen.getByRole('button', {
      name: 'Validate all fields for Reference one',
    })
    expect(screen.getByRole('columnheader', { name: 'Validate' })).toHaveAttribute(
      'data-sticky',
      'right',
    )
    expect(validateRow).toBeEnabled()
    expect(validateRow).toHaveStyle({
      borderRadius: '4px',
      color: '#076b65',
      fontSize: '9px',
      height: '22px',
      width: '58px',
    })
    expect(screen.getByLabelText(
      '0 of 2 fields curator validated for Reference one',
    )).toHaveTextContent('0/2')

    await user.click(validateRow)

    expect(screen.getByRole('button', {
      name: 'All fields validated for Reference one',
    })).toBeDisabled()
    expect(screen.getByLabelText(
      '2 of 2 fields curator validated for Reference one',
    )).toHaveTextContent('2/2')
    expect(screen.getByRole('status')).toHaveTextContent(
      'Reference one marked curator validated for this preview only. No validation was run or saved.',
    )

    await user.click(screen.getByRole('button', { name: /^Mark as not validated Authors:/ }))

    expect(screen.getByRole('button', {
      name: 'Validate all fields for Reference one',
    })).toBeEnabled()
    expect(screen.getByLabelText(
      '1 of 2 fields curator validated for Reference one',
    )).toHaveTextContent('1/2')
    expect(serviceMocks.validateCurationCandidate).not.toHaveBeenCalled()
    expect(autosave.queueFieldChange).not.toHaveBeenCalled()
    expect(autosave.flush).not.toHaveBeenCalled()
  })

  it('keeps resolved messages in details without showing a false error marker', async () => {
    const user = userEvent.setup()
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
    expect(screen.queryByRole('img', {
      name: /Authors were validated by the server/,
    })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Mark as not validated Authors:/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    await user.click(screen.getByRole('button', {
      name: /^Show evidence and validation details for Authors:/,
    }))
    const details = screen.getByRole('dialog', { name: /Authors:/ })
    expect(details).toHaveTextContent('Authors were validated by the server.')
    expect(details).toHaveTextContent('A second authoritative validation detail.')
    expect(within(details).getByText('Resolution')).toBeInTheDocument()
    expect(within(details).getByText(/^Authors resolved to /)).toBeInTheDocument()
    expect(within(details).getByText('Validator context')).toBeInTheDocument()
    expect(within(details).getByText('Current status')).toBeInTheDocument()
    expect(within(details).getByText('Curator validated')).toBeInTheDocument()
    expect(serviceMocks.validateCurationCandidate).not.toHaveBeenCalled()
  })

  it('distinguishes a field-specific taxon result from shared gene-validator context', async () => {
    const user = userEvent.setup()
    const candidate = buildCandidate()
    const taxonField = candidate.draft.fields[0]
    taxonField.label = 'Taxon'
    taxonField.value = 'NCBITaxon:7227'
    taxonField.metadata = { source_field_path: 'taxon' }

    const summary = {
      ...resolvedSummary(),
      field_path: 'taxon',
      messages: [
        'Resolved Example organism sample (sym) as ExampleDB gene MOD:GENE-123.',
      ],
    }
    const model = buildModel({
      authorsEvidence: [],
      authorsValidation: validationProjection([summary]),
    })
    const taxonColumn = model.columns[1]
    taxonColumn.fieldPath = 'taxon'
    taxonColumn.label = 'Taxon'
    const taxonCell = model.rows[0].cells[0]
    taxonCell.fieldPath = 'taxon'
    taxonCell.value = 'NCBITaxon:7227'
    taxonCell.displayText = 'NCBITaxon:7227'
    taxonCell.extractorComparison = {
      fieldKey: 'proposed_taxon',
      fieldPath: 'proposed_taxon',
      label: 'Proposed taxon',
      value: 'NCBITaxon:7227',
      outcome: 'confirmed',
    }

    renderGrid({ model, workspace: buildWorkspace(candidate) })
    await user.click(screen.getByRole('button', {
      name: /^Show evidence and validation details for Taxon:/,
    }))

    const details = screen.getByRole('dialog', { name: /Taxon:/ })
    expect(within(details).getByText(
      'Taxon resolved to NCBITaxon:7227.',
    )).toBeInTheDocument()
    expect(within(details).getByText('Validator context')).toBeInTheDocument()
    expect(within(details).getByLabelText('Extractor result confirmed')).toBeInTheDocument()
    expect(within(details).getByText('Extractor result')).toBeInTheDocument()
    expect(within(details).getByText('Validator result')).toBeInTheDocument()
    expect(within(details).getByText('The validator confirmed the extractor result.')).toBeInTheDocument()
    expect(within(details).getByText(
      'Resolved Example organism sample (sym) as ExampleDB gene MOD:GENE-123.',
    )).toBeInTheDocument()
  })

  it('flags extractor and validator disagreement for curator review without a proposal column', async () => {
    const user = userEvent.setup()
    const candidate = buildCandidate()
    const symbolField = candidate.draft.fields[0]
    symbolField.label = 'Symbol'
    symbolField.value = 'abc'
    symbolField.metadata = { source_field_path: 'symbol' }

    const model = buildModel({ authorsEvidence: [] })
    model.columns[1].fieldPath = 'symbol'
    model.columns[1].label = 'Symbol'
    const symbolCell = model.rows[0].cells[0]
    symbolCell.fieldPath = 'symbol'
    symbolCell.value = 'abc'
    symbolCell.displayText = 'abc'
    symbolCell.state = 'needs-review'
    symbolCell.extractorComparison = {
      fieldKey: 'proposed_symbol',
      fieldPath: 'proposed_symbol',
      label: 'Proposed symbol',
      value: 'abcd',
      outcome: 'different',
    }

    renderGrid({ model, workspace: buildWorkspace(candidate) })
    expect(model.columns.map((column) => column.fieldPath)).not.toContain('proposed_symbol')
    expect(screen.getByRole('img', {
      name: /Extractor proposed abcd; validator resolved abc\. Curator review is needed/,
    })).toBeInTheDocument()

    await user.click(screen.getByRole('button', {
      name: /^Show evidence and validation details for Symbol:/,
    }))

    const details = screen.getByRole('dialog', { name: /Symbol:/ })
    const comparison = within(details).getByTestId('horizontal-grid-extractor-comparison')
    expect(within(comparison).getByText(
      'Extractor and validator differ — curator review needed',
    )).toBeInTheDocument()
    expect(within(comparison).getByText('abcd')).toBeInTheDocument()
    expect(within(comparison).getByText('abc')).toBeInTheDocument()
    expect(within(details).getByLabelText('Current status: Needs review')).toBeInTheDocument()
  })

  it('shows UNRESOLVED, never the extractor proposal, in the validated slot', async () => {
    const user = userEvent.setup()
    const candidate = buildCandidate()
    const symbolField = candidate.draft.fields[0]
    symbolField.label = 'Symbol'
    symbolField.value = 'draft-seed'
    symbolField.metadata = { source_field_path: 'symbol' }

    const model = buildModel({ authorsEvidence: [] })
    model.columns[1].fieldPath = 'symbol'
    model.columns[1].label = 'Symbol'
    const symbolCell = model.rows[0].cells[0]
    symbolCell.fieldPath = 'symbol'
    symbolCell.value = null
    symbolCell.displayText = 'UNRESOLVED'
    symbolCell.state = 'needs-review'
    symbolCell.extractorComparison = {
      fieldKey: 'proposed_symbol',
      fieldPath: 'proposed_symbol',
      label: 'Proposed symbol',
      value: 'extracted-symbol',
      outcome: 'unresolved',
    }

    renderGrid({ model, workspace: buildWorkspace(candidate) })
    const symbolValue = within(screen.getByTestId(`horizontal-grid-field-${symbolField.field_key}`))
      .getByText('UNRESOLVED')
    expect(symbolValue).toHaveAttribute('data-slot', 'field-value')
    expect(screen.queryByText(/Extractor value/)).not.toBeInTheDocument()
    expect(screen.getByRole('img', {
      name: /Extractor proposed extracted-symbol, but the validator did not resolve a canonical value/,
    })).toBeInTheDocument()
    await user.click(screen.getByRole('button', {
      name: /^Show evidence and validation details for Symbol:/,
    }))

    const comparison = screen.getByTestId('horizontal-grid-extractor-comparison')
    expect(within(comparison).getByText('Not resolved')).toBeInTheDocument()
    expect(within(comparison).queryByText('draft-seed')).not.toBeInTheDocument()
  })

  it('shows paper wording and the lookup result apart from the validated value', async () => {
    const user = userEvent.setup()
    const candidate = buildCandidate()
    const symbolField = candidate.draft.fields[0]
    symbolField.label = 'Symbol'
    symbolField.value = null
    symbolField.metadata = { source_field_path: 'symbol' }

    const model = buildModel({ authorsEvidence: [] })
    model.columns[1].fieldPath = 'symbol'
    model.columns[1].label = 'Symbol'
    const symbolCell = model.rows[0].cells[0]
    symbolCell.fieldPath = 'symbol'
    symbolCell.value = null
    symbolCell.displayText = 'UNRESOLVED'
    symbolCell.resolution = {
      display_text: 'UNRESOLVED',
      values: [{
        value_path: '',
        display_text: 'UNRESOLVED',
        mention: 'abc-1',
        resolution_state: 'unresolved',
        lookup_outcome: 'rejected_candidates',
        lookup_result: 'Candidates rejected',
        validator_explanation: 'Every candidate was a different species.',
        validator_curator_message: null,
        override_disagreements: [],
        identity_field_paths: ['symbol'],
        id_key: 'curie',
        label_key: 'name',
        validated_keys: [],
        stored_identity: {},
        container_protected: false,
        overridable: true,
      }],
    }
    symbolCell.resolutionDetails = symbolCell.resolution.values
    symbolCell.resolutionLinesId = 'lines-symbol'
    symbolCell.resolutionDescribedBy = ['lines-symbol']

    renderGrid({ model, workspace: buildWorkspace(candidate) })
    const button = screen.getByTestId(`horizontal-grid-field-${symbolField.field_key}`)
    const cell = button.parentElement!
    expect(within(cell).getByText('UNRESOLVED')).toHaveAttribute('data-slot', 'field-value')
    expect(button).toHaveAccessibleName(/^Select Symbol for symbol: UNRESOLVED\./)
    expect(button).toHaveAccessibleDescription(
      /^Paper wording:\s*abc-1\s+Lookup result:\s*Candidates rejected\. Validator explanation: Every candidate was a different species\.$/,
    )
    expect(cell.querySelector('[data-slot="field-paper-wording"]')).toHaveTextContent('Paper wording: abc-1')
    expect(cell.querySelector('[data-slot="field-lookup-result"]')).not.toHaveAttribute('tabindex')

    await user.click(screen.getByRole('button', {
      name: /^Show evidence and validation details for Symbol: UNRESOLVED/,
    }))
    const details = screen.getByRole('dialog', { name: /Symbol:/ })
    expect(within(details).getByText('Symbol: UNRESOLVED')).toBeInTheDocument()
    const resolution = within(details).getByTestId('horizontal-grid-resolution-details')
    expect([...resolution.querySelectorAll('p')].map((line) => line.textContent)).toEqual([
      'Value: UNRESOLVED',
      'Paper wording: abc-1',
      'Lookup result: Candidates rejected',
      'Validator explanation: Every candidate was a different species.',
    ])
  })

  function overrideModel(
    candidate: CurationCandidate,
    values: DomainEnvelopeReviewResolvedValue[],
    displayText: string,
    fieldPath = 'subject.curie',
  ): HorizontalGridModel {
    const symbolField = candidate.draft.fields[0]
    symbolField.label = 'Subject ID'
    symbolField.value = null
    symbolField.metadata = { source_field_path: fieldPath }
    const model = buildModel({ authorsEvidence: [] })
    model.columns[1].fieldPath = fieldPath
    model.columns[1].label = 'Subject ID'
    const cell = model.rows[0].cells[0]
    cell.fieldPath = fieldPath
    cell.value = null
    cell.displayText = displayText
    cell.state = values.every((value) => value.curator_override) ? 'resolved' : 'ai-unconfirmed'
    cell.curatorOverride = values.some((value) => value.curator_override)
    cell.resolution = { display_text: displayText, values }
    cell.resolutionDetails = values
    cell.resolutionLinesId = 'lines-subject'
    cell.resolutionDescribedBy = ['lines-subject']
    cell.overrideTargets = values
    return model
  }

  const UNRESOLVED_SUBJECT: DomainEnvelopeReviewResolvedValue = {
    value_path: 'subject',
    display_text: 'UNRESOLVED',
    mention: 'abc-1',
    resolution_state: 'unresolved',
    lookup_outcome: 'not_found',
    lookup_result: 'Not found',
    validator_explanation: 'No gene matched the wording.',
    validator_curator_message: null,
    override_disagreements: [],
    identity_field_paths: ['subject.curie', 'subject.name', 'subject.taxon'],
    id_key: 'curie',
    label_key: 'name',
    validated_keys: ['taxon'],
    stored_identity: { curie: null, name: null, taxon: 'NCBITaxon:6239' },
    container_protected: false,
    overridable: true,
  }

  const OVERRIDDEN_SUBJECT: DomainEnvelopeReviewResolvedValue = {
    ...UNRESOLVED_SUBJECT,
    display_text: 'abc-2 (GENE:2)',
    resolution_state: 'resolved',
    lookup_outcome: 'curator_override',
    lookup_result: 'Curator override',
    curator_override: {
      actor_id: '6b1f0c2e-sub',
      actor_display_name: 'Pat Curator',
      at: '2026-09-23T20:00:00+00:00',
    },
    stored_identity: { curie: 'GENE:2', name: 'abc-2', taxon: 'NCBITaxon:6239' },
  }

  it('sets a first override with every identity key in one replace_identity edit', async () => {
    const user = userEvent.setup()
    const autosave = createAutosave()
    const candidate = buildCandidate()
    const model = overrideModel(candidate, [UNRESOLVED_SUBJECT], 'UNRESOLVED')

    renderGrid({ autosave, model, workspace: buildWorkspace(candidate) })
    await user.click(screen.getByRole('button', { name: /^Edit Subject ID: UNRESOLVED/ }))
    const editor = screen.getByRole('dialog', { name: 'Set Subject ID by curator override' })
    expect(within(editor).getByText('abc-1')).toBeInTheDocument()
    // The validated key is prefilled from the stored value and editable.
    expect(within(editor).getByRole('textbox', { name: /^Taxon/ })).toHaveValue('NCBITaxon:6239')

    // An incomplete identity is refused before anything is sent, in the
    // backend's words, and the editor stays open.
    await user.click(within(editor).getByRole('button', { name: 'Save override' }))
    expect(within(editor).getByTestId('horizontal-grid-override-error')).toHaveTextContent(
      'Enter both the identifier and the name for a curator override.',
    )
    await user.type(within(editor).getByRole('textbox', { name: /^Identifier/ }), 'GENE:2')
    await user.click(within(editor).getByRole('button', { name: 'Save override' }))
    expect(within(editor).getByTestId('horizontal-grid-override-error')).toHaveTextContent(
      'Enter both the identifier and the name for a curator override.',
    )
    expect(autosave.submitEnvelopeEdit).not.toHaveBeenCalled()
    await user.type(within(editor).getByRole('textbox', { name: /^Name/ }), 'abc-2')
    await user.clear(within(editor).getByRole('textbox', { name: /^Taxon/ }))
    await user.type(within(editor).getByRole('textbox', { name: /^Taxon/ }), 'NCBITaxon:7227')
    await user.click(within(editor).getByRole('button', { name: 'Save override' }))

    // Autosave sends it at the envelope's latest revision.
    expect(autosave.submitEnvelopeEdit).toHaveBeenCalledWith(candidate.candidate_id, {
      fieldPath: 'subject.curie',
      operation: 'replace_identity',
      // Only identity keys are sent: paper wording, state and the proposal stay as they are.
      before: { curie: null, name: null, taxon: 'NCBITaxon:6239' },
      value: { curie: 'GENE:2', name: 'abc-2', taxon: 'NCBITaxon:7227' },
    })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /curator override/ })).not.toBeInTheDocument())
  })

  it('keeps the editor open with the backend\'s words when it refuses the override', async () => {
    const user = userEvent.setup()
    const autosave = createAutosave({
      submitEnvelopeEdit: vi.fn().mockRejectedValue(new Error('Enter the name for a curator override.')),
    })
    const candidate = buildCandidate()
    const model = overrideModel(candidate, [UNRESOLVED_SUBJECT], 'UNRESOLVED')

    renderGrid({ autosave, model, workspace: buildWorkspace(candidate) })
    await user.click(screen.getByRole('button', { name: /^Edit Subject ID: UNRESOLVED/ }))
    const editor = screen.getByRole('dialog', { name: 'Set Subject ID by curator override' })
    await user.type(within(editor).getByRole('textbox', { name: /^Identifier/ }), 'GENE:2')
    await user.type(within(editor).getByRole('textbox', { name: /^Name/ }), 'abc-2')
    await user.click(within(editor).getByRole('button', { name: 'Save override' }))

    expect(await within(editor).findByTestId('horizontal-grid-override-error')).toHaveTextContent(
      'Enter the name for a curator override.',
    )
    expect(within(editor).getByRole('textbox', { name: /^Identifier/ })).toHaveValue('GENE:2')
    expect(within(editor).getByRole('textbox', { name: /^Name/ })).toHaveValue('abc-2')
  })

  it('overrides one element of a list cell, picked in the editor', async () => {
    const user = userEvent.setup()
    const autosave = createAutosave()
    const candidate = buildCandidate()
    const codes = ['IMP', 'IGI'].map((mention, index): DomainEnvelopeReviewResolvedValue => ({
      ...UNRESOLVED_SUBJECT,
      value_path: `evidence_codes[${index}]`,
      mention,
      identity_field_paths: [`evidence_codes[${index}].curie`],
      id_key: 'curie',
      label_key: null,
      validated_keys: [],
      stored_identity: { curie: null },
    }))
    const model = overrideModel(candidate, codes, 'UNRESOLVED | UNRESOLVED', 'evidence_codes')

    renderGrid({ autosave, model, workspace: buildWorkspace(candidate) })
    await user.click(screen.getByRole('button', { name: /^Edit Subject ID: UNRESOLVED/ }))
    const editor = screen.getByRole('dialog', { name: 'Set Subject ID by curator override' })
    await user.click(within(editor).getByRole('combobox', { name: 'Value to override' }))
    await user.click(screen.getByRole('option', { name: '2. IGI (Not found)' }))
    await user.type(within(editor).getByRole('textbox', { name: /^Identifier/ }), 'ECO:0000316')
    await user.click(within(editor).getByRole('button', { name: 'Save override' }))

    expect(autosave.submitEnvelopeEdit).toHaveBeenCalledWith(candidate.candidate_id, {
      fieldPath: 'evidence_codes[1].curie',
      operation: 'replace_identity',
      before: { curie: null },
      value: { curie: 'ECO:0000316' },
    })
  })

  it('removes one element of a list after the curator confirms it', async () => {
    const user = userEvent.setup()
    const autosave = createAutosave()
    const candidate = buildCandidate()
    const stored = { mention: 'IGI', curie: null, resolution_state: 'unresolved', lookup_outcome: 'not_found' }
    const element: DomainEnvelopeReviewResolvedValue = {
      ...UNRESOLVED_SUBJECT,
      value_path: 'evidence_codes[0]',
      mention: 'IGI',
      identity_field_paths: ['evidence_codes[0].curie'],
      label_key: null,
      validated_keys: [],
      stored_identity: { curie: null },
      stored_value: stored,
    }
    const model = overrideModel(candidate, [element], 'UNRESOLVED', 'evidence_codes')

    renderGrid({ autosave, model, workspace: buildWorkspace(candidate) })
    await user.click(screen.getByRole('button', { name: /^Edit Subject ID: UNRESOLVED/ }))
    const editor = screen.getByRole('dialog', { name: 'Set Subject ID by curator override' })
    await user.click(within(editor).getByRole('button', { name: 'Remove from the list' }))
    expect(autosave.submitEnvelopeEdit).not.toHaveBeenCalled()
    await user.click(within(editor).getByRole('button', { name: 'Confirm removal from the list' }))

    expect(autosave.submitEnvelopeEdit).toHaveBeenCalledWith(candidate.candidate_id, {
      fieldPath: 'evidence_codes[0]',
      operation: 'remove',
      before: stored,
      value: null,
    })
  })

  it('shows who made an override by name, and removes it with one clearing edit', async () => {
    const user = userEvent.setup()
    const autosave = createAutosave()
    const candidate = buildCandidate()
    const model = overrideModel(candidate, [OVERRIDDEN_SUBJECT], 'GENE:2')

    renderGrid({ autosave, model, workspace: buildWorkspace(candidate) })
    const button = screen.getByTestId(`horizontal-grid-field-${candidate.draft.fields[0].field_key}`)
    expect(within(button.parentElement!).getByText('Curator override', { selector: '[data-slot="field-override-badge"]' }))
      .toBeInTheDocument()
    expect(button).toHaveAccessibleName(/^Select Subject ID for subject\.curie: GENE:2, curator override\. Curator validated\.$/)
    expect(button).toHaveAccessibleDescription(/Curator override by Pat Curator on 2026-09-23 20:00 UTC/)
    expect(screen.queryByText(/6b1f0c2e-sub/)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', {
      name: /^Show evidence and validation details for Subject ID: GENE:2/,
    }))
    const details = screen.getByRole('dialog', { name: /Subject ID:/ })
    expect(within(details).getByTestId('horizontal-grid-resolution-details')).toHaveTextContent(
      'Curator override by Pat Curator on 2026-09-23 20:00 UTC',
    )

    await user.click(screen.getByRole('button', {
      name: /^Remove curator override for Subject ID: GENE:2 .*The value returns to unresolved\.$/,
    }))
    expect(autosave.submitEnvelopeEdit).toHaveBeenCalledWith(candidate.candidate_id, {
      fieldPath: 'subject.curie',
      operation: 'replace_identity',
      before: { curie: 'GENE:2', name: 'abc-2', taxon: 'NCBITaxon:6239' },
      // Every identity key cleared in one edit, validated keys included.
      value: { curie: null, name: null, taxon: null },
    })
  })

  it('overrides a saved profile attribute value with one whole-value replace', async () => {
    const user = userEvent.setup()
    const autosave = createAutosave({
      submitEnvelopeEdit: vi.fn().mockRejectedValue(new Error("field_path 'attributes.gene' is protected")),
    })
    const candidate = buildCandidate()
    const stored = { mention: 'abc-1', curie: null, name: null, resolution_state: 'unresolved', lookup_outcome: 'not_found' }
    const profileValue: DomainEnvelopeReviewResolvedValue = {
      ...UNRESOLVED_SUBJECT,
      value_path: 'attributes.gene',
      identity_field_paths: ['attributes.gene.curie', 'attributes.gene.name'],
      validated_keys: [],
      stored_identity: { curie: null, name: null },
      stored_value: stored,
    }
    const model = overrideModel(candidate, [profileValue], 'UNRESOLVED', 'attributes.gene.curie')

    renderGrid({ autosave, model, workspace: buildWorkspace(candidate) })
    await user.click(screen.getByRole('button', { name: /^Edit Subject ID: UNRESOLVED/ }))
    const editor = screen.getByRole('dialog', { name: 'Set Subject ID by curator override' })
    await user.type(within(editor).getByRole('textbox', { name: /^Identifier/ }), 'GENE:2')
    await user.type(within(editor).getByRole('textbox', { name: /^Name/ }), 'abc-2')
    await user.click(within(editor).getByRole('button', { name: 'Save override' }))

    expect(autosave.submitEnvelopeEdit).toHaveBeenCalledWith(candidate.candidate_id, {
      fieldPath: 'attributes.gene',
      operation: 'replace',
      before: stored,
      value: { ...stored, curie: 'GENE:2', name: 'abc-2' },
    })
    // A protected value field's rejection shows inline like the others.
    expect(await within(editor).findByTestId('horizontal-grid-override-error')).toHaveTextContent(
      "field_path 'attributes.gene' is protected",
    )
  })

  it('overrides a value that is the object itself with its bare identity field', async () => {
    const user = userEvent.setup()
    const autosave = createAutosave()
    const candidate = buildCandidate()
    const rootValue: DomainEnvelopeReviewResolvedValue = {
      ...UNRESOLVED_SUBJECT,
      value_path: '',
      identity_field_paths: ['primary_external_id', 'gene_symbol', 'taxon'],
      id_key: 'primary_external_id',
      label_key: 'gene_symbol',
      stored_identity: { primary_external_id: null, gene_symbol: null, taxon: 'NCBITaxon:6239' },
    }
    const model = overrideModel(candidate, [rootValue], 'UNRESOLVED', 'gene_symbol')

    renderGrid({ autosave, model, workspace: buildWorkspace(candidate) })
    await user.click(screen.getByRole('button', { name: /^Edit Subject ID: UNRESOLVED/ }))
    const editor = screen.getByRole('dialog', { name: 'Set Subject ID by curator override' })
    await user.type(within(editor).getByRole('textbox', { name: /^Identifier/ }), 'GENE:2')
    await user.type(within(editor).getByRole('textbox', { name: /^Name/ }), 'abc-2')
    await user.click(within(editor).getByRole('button', { name: 'Save override' }))

    expect(autosave.submitEnvelopeEdit).toHaveBeenCalledWith(candidate.candidate_id, {
      fieldPath: 'primary_external_id',
      operation: 'replace_identity',
      before: { primary_external_id: null, gene_symbol: null, taxon: 'NCBITaxon:6239' },
      value: { primary_external_id: 'GENE:2', gene_symbol: 'abc-2', taxon: 'NCBITaxon:6239' },
    })
  })

  it('keeps a value\'s own leaves read-only in the grid', () => {
    const candidate = buildCandidate()
    const leafField = candidate.draft.fields[0]
    leafField.label = 'Lookup result'
    leafField.read_only = false
    leafField.metadata = { source_field_path: 'site.lookup_outcome' }

    const model = buildModel({ authorsEvidence: [] })
    model.columns[1].fieldPath = 'site.lookup_outcome'
    model.columns[1].label = 'Lookup result'
    const leafCell = model.rows[0].cells[0]
    leafCell.fieldPath = 'site.lookup_outcome'
    leafCell.readOnly = true

    renderGrid({ model, workspace: buildWorkspace(candidate) })
    expect(screen.getByRole('button', { name: /^Edit unavailable for Lookup result: .*Read-only field\.$/ }))
      .toBeDisabled()
  })

  it('attributes a waived comparison to curator override rather than validator resolution', async () => {
    const user = userEvent.setup()
    const candidate = buildCandidate()
    const symbolField = candidate.draft.fields[0]
    symbolField.label = 'Symbol'
    symbolField.value = 'curator-symbol'
    symbolField.metadata = { source_field_path: 'symbol' }

    const waivedSummary = {
      ...resolvedSummary(),
      field_path: 'symbol',
      status: 'waived' as const,
      messages: ['Accepted by curator override.'],
    }
    const model = buildModel({
      authorsEvidence: [],
      authorsValidation: validationProjection([waivedSummary]),
    })
    model.columns[1].fieldPath = 'symbol'
    model.columns[1].label = 'Symbol'
    const symbolCell = model.rows[0].cells[0]
    symbolCell.fieldPath = 'symbol'
    symbolCell.value = 'curator-symbol'
    symbolCell.displayText = 'curator-symbol'
    symbolCell.state = 'resolved'
    symbolCell.extractorComparison = {
      fieldKey: 'proposed_symbol',
      fieldPath: 'proposed_symbol',
      label: 'Proposed symbol',
      value: 'extracted-symbol',
      outcome: 'overridden',
    }

    renderGrid({ model, workspace: buildWorkspace(candidate) })
    await user.click(screen.getByRole('button', {
      name: /^Show evidence and validation details for Symbol:/,
    }))

    const details = screen.getByRole('dialog', { name: /Symbol:/ })
    const comparison = within(details).getByTestId('horizontal-grid-extractor-comparison')
    expect(within(comparison).getAllByText('Curator override')).toHaveLength(2)
    expect(within(comparison).getByText('extracted-symbol')).toBeInTheDocument()
    expect(within(comparison).getByText('curator-symbol')).toBeInTheDocument()
    expect(within(comparison).getByText(
      'The canonical value was accepted by curator override; it was not resolved by the validator.',
    )).toBeInTheDocument()
    expect(within(details).queryByText('Symbol resolved to curator-symbol.')).not.toBeInTheDocument()
  })

  it('does not attribute a stale resolved projection to the validator', async () => {
    const user = userEvent.setup()
    const candidate = buildCandidate()
    const symbolField = candidate.draft.fields[0]
    symbolField.label = 'Symbol'
    symbolField.value = 'edited-symbol'
    symbolField.stale_validation = true
    symbolField.metadata = { source_field_path: 'symbol' }

    const resolved = {
      ...resolvedSummary(),
      field_path: 'symbol',
    }
    const model = buildModel({
      authorsEvidence: [],
      authorsValidation: validationProjection([resolved]),
    })
    model.columns[1].fieldPath = 'symbol'
    model.columns[1].label = 'Symbol'
    const symbolCell = model.rows[0].cells[0]
    symbolCell.fieldPath = 'symbol'
    symbolCell.value = 'edited-symbol'
    symbolCell.displayText = 'UNRESOLVED'
    symbolCell.staleValidation = true
    symbolCell.state = 'ai-unconfirmed'
    symbolCell.extractorComparison = {
      fieldKey: 'proposed_symbol',
      fieldPath: 'proposed_symbol',
      label: 'Proposed symbol',
      value: 'extracted-symbol',
      outcome: 'unresolved',
    }

    renderGrid({ model, workspace: buildWorkspace(candidate) })
    await user.click(screen.getByRole('button', {
      name: /^Show evidence and validation details for Symbol:/,
    }))

    const details = screen.getByRole('dialog', { name: /Symbol:/ })
    expect(within(details).getByLabelText('Canonical value not resolved')).toBeInTheDocument()
    expect(within(details).queryByText('Symbol resolved to edited-symbol.')).not.toBeInTheDocument()
    expect(within(details).getByText('Not resolved')).toBeInTheDocument()
  })

  it('shows validation-only details and reserves warning/error symbols for field state', async () => {
    const user = userEvent.setup()
    const model = buildModel({ authorsEvidence: [] })
    model.rows[0].cells[0].state = 'needs-review'
    renderGrid({ mode: 'light', model })

    expect(screen.getByRole('img', { name: 'Needs review' })).toHaveTextContent('!')
    expect(screen.getByRole('img', { name: 'Not validated' })).toHaveTextContent('×')

    await user.click(screen.getByRole('button', {
      name: /^Show evidence and validation details for Authors:/,
    }))
    const details = screen.getByRole('dialog', { name: /Authors:/ })
    expect(within(details).getByText('Field-specific evidence')).toBeInTheDocument()
    expect(within(details).getByText(
      'No field-specific evidence was recorded for this field.',
    )).toBeInTheDocument()
    expect(within(details).getByLabelText('Current status: Needs review')).toBeInTheDocument()
    expect(within(details).getByTestId('horizontal-grid-current-status')).toHaveStyle({
      color: '#8a5b0d',
    })
  })

  it('summarizes displayed preview states and restores focus when the drawer closes', async () => {
    const user = userEvent.setup()
    renderGrid({
      model: buildModel({
        authorsValidation: validationProjection([resolvedSummary()]),
      }),
    })

    const summaryTrigger = screen.getByRole('button', { name: /Validation summary/ })
    await user.click(summaryTrigger)

    const summary = screen.getByRole('dialog', { name: /1 record · 2 curated fields/ })
    expect(within(summary).getByText('Curator validated').nextSibling).toHaveTextContent('1')
    expect(within(summary).getByText('Needs review').nextSibling).toHaveTextContent('0')
    expect(within(summary).getByText('Not validated').nextSibling).toHaveTextContent('1')
    expect(within(summary).getByText(/Preview only/)).toBeInTheDocument()
    expect(within(summary).getByRole('button', { name: 'Close validation summary' })).toHaveFocus()

    await user.click(screen.getByRole('button', { name: /^Validate Locked identifier:/ }))
    expect(within(summary).getByText('Curator validated').nextSibling).toHaveTextContent('2')
    expect(within(summary).getByText('Not validated').nextSibling).toHaveTextContent('0')

    await user.keyboard('{Escape}')
    expect(summary).toHaveAttribute('aria-hidden', 'true')
    expect(summaryTrigger).toHaveFocus()
    expect(serviceMocks.validateCurationCandidate).not.toHaveBeenCalled()
  })
})
