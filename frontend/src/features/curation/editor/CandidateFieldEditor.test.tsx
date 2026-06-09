import userEvent from '@testing-library/user-event'
import { fireEvent, render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'

import { onPDFViewerNavigateEvidence } from '@/components/pdfViewer/pdfEvents'
import type { CurationWorkspace } from '@/features/curation/types'
import {
  CurationWorkspaceProvider,
  type CurationWorkspaceContextValue,
} from '@/features/curation/workspace/CurationWorkspaceContext'
import theme from '@/theme'
import CandidateFieldEditor from './CandidateFieldEditor'

const FIELD_EVIDENCE_QUOTE =
  'ABC appears in the result sentence with enough surrounding source context for curators to review why the selected field value is grounded in the paper and should open as a full quote.'
const FIELD_EVIDENCE_PREVIEW =
  'ABC appears in the result sentence with enough surrounding source context for curators to review why the selected field value is...'

function buildWorkspace(): CurationWorkspace {
  return {
    session: {
      session_id: 'session-1',
      status: 'in_progress',
      adapter: {
        adapter_key: 'generic',
        display_label: 'Generic',
        metadata: {},
      },
      document: {
        document_id: 'document-1',
        title: 'Envelope document',
      },
      progress: {
        total_candidates: 1,
        reviewed_candidates: 0,
        pending_candidates: 1,
        accepted_candidates: 0,
        rejected_candidates: 0,
        manual_candidates: 0,
      },
      current_candidate_id: 'candidate-1',
      prepared_at: '2026-03-20T12:00:00Z',
      warnings: [],
      tags: [],
      session_version: 1,
      extraction_results: [],
    },
    candidates: [
      {
        candidate_id: 'candidate-1',
        session_id: 'session-1',
        source: 'extracted',
        status: 'pending',
        order: 0,
        adapter_key: 'generic',
        display_label: 'Envelope object',
        projection_ref: {
          envelope_id: 'envelope-1',
          object_id: 'object-1',
          envelope_revision: 4,
        },
        draft: {
          draft_id: 'draft-1',
          candidate_id: 'candidate-1',
          adapter_key: 'generic',
          version: 1,
          fields: [
            {
              field_key: 'field_symbol',
              label: 'Gene symbol',
              value: 'abc',
              seed_value: 'abc',
              field_type: 'string',
              group_key: 'details',
              group_label: 'Details',
              order: 0,
              required: true,
              read_only: false,
              dirty: false,
              stale_validation: false,
              evidence_anchor_ids: [],
              validation_result: null,
              metadata: {
                source_field_path: 'gene.symbol',
                helper_text: 'Curator-facing symbol.',
              },
            },
            {
              field_key: 'field_curie',
              label: 'Gene CURIE',
              value: null,
              seed_value: null,
              field_type: 'string',
              group_key: 'details',
              group_label: 'Details',
              order: 1,
              required: false,
              read_only: true,
              dirty: false,
              stale_validation: false,
              evidence_anchor_ids: [],
              validation_result: null,
              metadata: {
                source_field_path: 'gene.curie',
                field_metadata: {
                  term_helper: {
                    field_path: 'gene.curie',
                    term_source: { kind: 'ontology' },
                  },
                },
              },
            },
            {
              field_key: 'field_score',
              label: 'Evidence score',
              value: 0.4,
              seed_value: 0.4,
              field_type: 'number',
              group_key: 'details',
              group_label: 'Details',
              order: 2,
              required: false,
              read_only: false,
              dirty: false,
              stale_validation: false,
              evidence_anchor_ids: [],
              validation_result: null,
              metadata: {
                source_field_path: 'evidence.score',
              },
            },
            {
              field_key: 'field_label',
              label: 'Gene label',
              value: 'ABC',
              seed_value: 'ABC',
              field_type: 'string',
              group_key: 'details',
              group_label: 'Details',
              order: 3,
              required: false,
              read_only: false,
              dirty: false,
              stale_validation: false,
              evidence_anchor_ids: [],
              validation_result: null,
              metadata: {
                source_field_path: 'gene.label',
              },
            },
            {
              field_key: 'field_override',
              label: 'Override reason',
              value: 'Reviewed',
              seed_value: 'Reviewed',
              field_type: 'string',
              group_key: 'details',
              group_label: 'Details',
              order: 4,
              required: false,
              read_only: false,
              dirty: false,
              stale_validation: false,
              evidence_anchor_ids: [],
              validation_result: null,
              metadata: {
                source_field_path: 'curator.override',
              },
            },
          ],
          created_at: '2026-03-20T12:00:00Z',
          updated_at: '2026-03-20T12:00:00Z',
          metadata: {},
        },
        evidence_anchors: [],
        evidence_anchor_projections: [
          {
            anchor_id: 'evidence-1',
            evidence_record_id: 'record-1',
            envelope_id: 'envelope-1',
            object_id: 'object-1',
            object_type: 'gene',
            field_path: 'gene.symbol',
            envelope_revision: 4,
            document_id: 'document-1',
            quote: FIELD_EVIDENCE_QUOTE,
            page_number: 2,
            page_label: null,
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
              snippet_text: FIELD_EVIDENCE_QUOTE,
              sentence_text: FIELD_EVIDENCE_QUOTE,
              viewer_search_text: FIELD_EVIDENCE_QUOTE,
              page_number: 2,
              section_title: 'Results',
              chunk_ids: ['chunk-1'],
            },
            metadata: {},
          },
          {
            anchor_id: 'evidence-object-1',
            evidence_record_id: 'record-object-1',
            envelope_id: 'envelope-1',
            object_id: 'object-1',
            object_type: 'gene',
            field_path: null,
            envelope_revision: 4,
            document_id: 'document-1',
            quote: 'Object-level evidence quote from the loaded paper.',
            page_number: 4,
            page_label: null,
            chunk_id: 'chunk-object-1',
            chunk_ids: ['chunk-object-1'],
            section_title: 'Results',
            subsection_title: 'Expression findings',
            figure_reference: null,
            table_reference: null,
            source_id: null,
            source_title: null,
            source_url: null,
            anchor: {
              anchor_kind: 'snippet',
              locator_quality: 'exact_quote',
              supports_decision: 'supports',
              snippet_text: 'Object-level evidence quote from the loaded paper.',
              sentence_text: 'Object-level evidence quote from the loaded paper.',
              viewer_search_text: 'Object-level evidence quote from the loaded paper.',
              page_number: 4,
              section_title: 'Results',
              subsection_title: 'Expression findings',
              chunk_ids: ['chunk-object-1'],
            },
            metadata: {},
          },
        ],
        validation_summary_projections: [
          {
            summary_id: 'summary-unresolved',
            envelope_id: 'envelope-1',
            object_id: 'object-1',
            object_type: 'gene',
            field_path: 'gene.symbol',
            envelope_revision: 4,
            status: 'unresolved',
            highest_severity: 'warning',
            finding_count: 1,
            open_finding_count: 1,
            finding_ids: ['finding-unresolved'],
            codes: ['symbol.unresolved'],
            messages: ['Symbol needs curator review.'],
            findings: [],
          },
          {
            summary_id: 'summary-planned',
            envelope_id: 'envelope-1',
            object_id: 'object-1',
            object_type: 'gene',
            field_path: 'gene.curie',
            envelope_revision: 4,
            status: 'planned',
            highest_severity: 'info',
            finding_count: 1,
            open_finding_count: 1,
            finding_ids: ['finding-planned'],
            codes: ['curie.planned'],
            messages: ['The identifier validator is planned.'],
            findings: [],
          },
          {
            summary_id: 'summary-blocked',
            envelope_id: 'envelope-1',
            object_id: 'object-1',
            object_type: 'gene',
            field_path: 'evidence.score',
            envelope_revision: 4,
            status: 'blocked',
            highest_severity: 'error',
            finding_count: 1,
            open_finding_count: 1,
            finding_ids: ['finding-blocked'],
            codes: ['score.blocked'],
            messages: ['Evidence score blocks export.'],
            findings: [],
          },
          {
            summary_id: 'summary-resolved',
            envelope_id: 'envelope-1',
            object_id: 'object-1',
            object_type: 'gene',
            field_path: 'gene.label',
            envelope_revision: 4,
            status: 'resolved',
            highest_severity: null,
            finding_count: 1,
            open_finding_count: 0,
            finding_ids: ['finding-resolved'],
            codes: ['label.resolved'],
            messages: ['Label has been validated.'],
            findings: [],
          },
          {
            summary_id: 'summary-waived',
            envelope_id: 'envelope-1',
            object_id: 'object-1',
            object_type: 'gene',
            field_path: 'curator.override',
            envelope_revision: 4,
            status: 'waived',
            highest_severity: null,
            finding_count: 1,
            open_finding_count: 0,
            finding_ids: ['finding-waived'],
            codes: ['override.waived'],
            messages: ['Curator opted out of this finding.'],
            findings: [],
          },
          {
            summary_id: 'summary-under-development',
            envelope_id: 'envelope-1',
            object_id: 'object-1',
            object_type: 'gene',
            field_path: null,
            envelope_revision: 4,
            status: 'under_development',
            highest_severity: 'info',
            finding_count: 1,
            open_finding_count: 1,
            finding_ids: ['finding-under-development'],
            codes: ['object.under_development'],
            messages: ['The object-level validator is under development.'],
            findings: [],
          },
        ],
        validation: null,
        evidence_summary: null,
        created_at: '2026-03-20T12:00:00Z',
        updated_at: '2026-03-20T12:00:00Z',
        metadata: {},
      },
    ],
    active_candidate_id: 'candidate-1',
    queue_context: null,
    action_log: [
      {
        action_id: 'action-1',
        session_id: 'session-1',
        candidate_id: 'candidate-1',
        draft_id: 'draft-1',
        action_type: 'envelope_field_patched',
        actor_type: 'user',
        occurred_at: '2026-03-20T13:00:00Z',
        changed_field_keys: ['gene.symbol'],
        evidence_anchor_ids: [],
        metadata: {
          envelope_id: 'envelope-1',
          object_id: 'object-1',
          field_path: 'gene.symbol',
          before: 'old',
          after: 'abc',
          accepted: true,
          history_event_ids: ['history-1'],
        },
      },
    ],
    submission_history: [],
    saved_view_context: null,
  }
}

function renderEditor(workspace = buildWorkspace()) {
  const activeCandidate = workspace.candidates[0]
  const autosave = {
    debounceMs: 10,
    dirtyFieldKeys: [],
    isDirty: false,
    isSaving: false,
    warning: null,
    queueFieldChange: vi.fn(),
    queueFieldChanges: vi.fn(),
    flush: vi.fn().mockResolvedValue(true),
    clearWarning: vi.fn(),
  }
  const contextValue: CurationWorkspaceContextValue = {
    workspace,
    setWorkspace: vi.fn(),
    session: workspace.session,
    candidates: workspace.candidates,
    activeCandidateId: activeCandidate.candidate_id,
    activeCandidate,
    setActiveCandidate: vi.fn(),
    autosave,
  }

  render(
    <ThemeProvider theme={theme}>
      <CurationWorkspaceProvider value={contextValue}>
        <CandidateFieldEditor />
      </CurationWorkspaceProvider>
    </ThemeProvider>,
  )

  return autosave
}

describe('CandidateFieldEditor', () => {
  it('displays envelope validation, evidence, metadata, and curator edit history by field', () => {
    renderEditor()

    expect(screen.getByTestId('field-state-indicator-field_symbol')).toHaveAccessibleName(
      'Needs review',
    )
    expect(screen.getByText('Symbol needs curator review.')).toBeInTheDocument()
    expect(screen.getByTestId('field-state-indicator-field_curie')).toHaveAccessibleName(
      'AI unconfirmed',
    )
    expect(screen.getByText('The identifier validator is planned.')).toBeInTheDocument()
    expect(screen.getByTestId('field-state-indicator-field_score')).toHaveAccessibleName(
      'Needs review',
    )
    expect(screen.getByText('Evidence score blocks export.')).toBeInTheDocument()
    expect(screen.queryByText('Validated')).not.toBeInTheDocument()
    expect(screen.getByTestId('field-state-indicator-field_label')).toHaveAccessibleName(
      'Resolved',
    )
    expect(screen.getByTestId('field-state-indicator-field_override')).toHaveAccessibleName(
      'Resolved',
    )
    expect(screen.getByTestId('object-validation-state-under_development')).toHaveTextContent(
      "Gene curation isn't ready on this screen yet — validation and submission are still being built.",
    )
    expect(
      screen.queryByText('The object-level validator is under development.'),
    ).not.toBeInTheDocument()
    expect(screen.queryByText('Editable fields')).not.toBeInTheDocument()
    expect(screen.getByTestId('object-evidence-panel')).toHaveTextContent('Evidence')
    expect(
      screen.getByRole('button', {
        name: 'Highlight object evidence 1: Object-level evidence quote from the loaded paper.',
      }),
    ).toBeInTheDocument()
    expect(screen.getByTestId('field-evidence-projection-evidence-1')).toHaveTextContent(
      'p. 2',
    )
    expect(screen.getByTestId('field-evidence-projection-evidence-1')).toHaveTextContent(
      'Evidence quote',
    )
    expect(screen.getByTestId('field-evidence-projection-evidence-1')).toHaveTextContent(
      FIELD_EVIDENCE_PREVIEW,
    )
    expect(screen.getByTestId('field-support-details-field_symbol')).toHaveTextContent(
      'Path: gene.symbol',
    )
    expect(screen.getByTestId('field-support-details-field_symbol')).toHaveTextContent(
      'Last curator edit: old -> abc',
    )
  })

  it('does not use legacy field validation results when envelope summaries are absent', () => {
    const workspace = buildWorkspace()
    const candidate = workspace.candidates[0]!
    candidate.validation_summary_projections = []
    candidate.draft.fields[0]!.validation_result = {
      status: 'conflict',
      candidate_matches: [],
      warnings: ['Legacy validation warning.'],
    }

    renderEditor(workspace)

    expect(screen.queryByText('Conflict')).not.toBeInTheDocument()
    expect(screen.queryByText('Legacy validation warning.')).not.toBeInTheDocument()
  })

  it('shows compact validation explanations from envelope finding details', () => {
    const workspace = buildWorkspace()
    const candidate = workspace.candidates[0]!
    candidate.validation_summary_projections = [
      {
        summary_id: 'summary-gene-symbol-resolved',
        envelope_id: 'envelope-1',
        object_id: 'object-1',
        object_type: 'gene',
        field_path: 'gene.symbol',
        envelope_revision: 4,
        status: 'resolved',
        highest_severity: null,
        finding_count: 1,
        open_finding_count: 0,
        finding_ids: ['finding-gene-symbol-resolved'],
        codes: ['domain_pack.validator_resolved'],
        messages: ['Fallback summary message.'],
        findings: [
          {
            finding_id: 'finding-gene-symbol-resolved',
            envelope_id: 'envelope-1',
            object_id: 'object-1',
            object_type: 'gene',
            field_path: 'gene.symbol',
            envelope_revision: 4,
            severity: 'info',
            finding_status: 'resolved',
            summary_status: 'resolved',
            code: 'domain_pack.validator_resolved',
            message: 'Resolved gene symbol.',
            details: {
              curator_message: 'Resolved abc from the gene lookup.',
              validation_metadata: {
                parent_request_id: 'request-gene-1',
                materialized_result_field: 'symbol',
                generated_from_expected_result_field: true,
              },
              validation_result: {
                request_id: 'request-gene-1',
                validator_agent: {
                  package_id: 'agr.alliance',
                  agent_id: 'gene_validation',
                },
                curator_message: 'Resolved abc from the gene lookup.',
                explanation: 'The lookup matched the current accepted gene symbol.',
                resolved_values: {
                  symbol: 'abc',
                  curie: 'WB:WBGene00000001',
                },
              },
              lookup_attempts: [
                {
                  provider: 'agr_curation_query',
                  method: 'search_genes',
                  lookup_status: 'success',
                  candidate_count: 1,
                },
              ],
            },
          },
          {
            finding_id: 'finding-gene-symbol-resolved-retry',
            envelope_id: 'envelope-1',
            object_id: 'object-1',
            object_type: 'gene',
            field_path: 'gene.symbol',
            envelope_revision: 4,
            severity: 'info',
            finding_status: 'resolved',
            summary_status: 'resolved',
            code: 'domain_pack.validator_resolved',
            message: 'Resolved gene symbol.',
            details: {
              validation_metadata: {
                parent_request_id: 'request-gene-1',
                materialized_result_field: 'symbol',
                generated_from_expected_result_field: true,
              },
              validation_result: {
                request_id: 'request-gene-1',
                validator_agent: {
                  package_id: 'agr.alliance',
                  agent_id: 'gene_validation',
                },
                explanation: 'A retry produced redundant shared reasoning.',
                resolved_values: {
                  symbol: 'abc',
                },
              },
            },
          },
        ],
      },
    ]

    renderEditor(workspace)

    const validationState = screen.getByTestId('field-validation-state-field_symbol')
    expect(validationState).not.toHaveTextContent('Validated')
    expect(validationState).toHaveTextContent('Validation details')
    expect(validationState).not.toHaveTextContent('Symbol validated: abc')
    expect(validationState).toHaveTextContent('Validator: agr.alliance/gene_validation')
    expect(validationState).toHaveTextContent('Resolved Symbol: abc')
    expect(validationState).not.toHaveTextContent('Result field: Symbol')
    expect(validationState).not.toHaveTextContent('Curie: WB:WBGene00000001')
    expect(validationState).toHaveTextContent(
      'Lookup: agr_curation_query · search_genes · success · 1 candidate',
    )
    expect(validationState).toHaveTextContent(
      'Shared explanation: The lookup matched the current accepted gene symbol.',
    )
    expect(validationState).not.toHaveTextContent('A retry produced redundant shared reasoning.')
  })

  it('floats needs-review fields and counts them from envelope summaries', () => {
    renderEditor()

    expect(screen.getByTestId('field-section-needs-review-details')).toHaveTextContent(
      '2 need review',
    )
    expect(screen.getByTestId('field-state-indicator-field_symbol')).toHaveAccessibleName(
      'Needs review',
    )
    expect(screen.getByTestId('field-state-indicator-field_score')).toHaveAccessibleName(
      'Needs review',
    )
    expect(screen.getByTestId('field-state-indicator-field_label')).toHaveAccessibleName(
      'Resolved',
    )

    const fieldRows = screen.getAllByTestId(/^field-row-/)
    expect(fieldRows.map((row) => row.getAttribute('data-field-key'))).toEqual([
      'field_symbol',
      'field_score',
      'field_curie',
      'field_label',
      'field_override',
    ])
  })

  it('resolves selector leaf fields from parent validation summaries', () => {
    const workspace = buildWorkspace()
    const candidate = workspace.candidates[0]!
    candidate.validation_summary_projections = [
      {
        summary_id: 'summary-gene-selector',
        envelope_id: 'envelope-1',
        object_id: 'object-1',
        object_type: 'gene',
        field_path: 'gene',
        envelope_revision: 4,
        status: 'resolved',
        highest_severity: null,
        finding_count: 1,
        open_finding_count: 0,
        finding_ids: ['finding-gene-selector'],
        codes: ['gene.resolved'],
        messages: ['Gene selector has been validated.'],
        findings: [],
      },
    ]

    renderEditor(workspace)

    expect(screen.getByTestId('field-state-indicator-field_curie')).toHaveAccessibleName(
      'Resolved',
    )
    expect(screen.getByTestId('field-validation-state-field_curie')).not.toHaveTextContent(
      'Validated',
    )
    expect(screen.getByTestId('field-validation-state-field_curie')).toHaveTextContent(
      'Gene selector has been validated.',
    )
    expect(screen.queryByText('Validation details')).not.toBeInTheDocument()
  })

  it('displays under-development validator metadata without validation findings', () => {
    const workspace = buildWorkspace()
    const candidate = workspace.candidates[0]!
    candidate.validation_summary_projections = []
    candidate.metadata = {
      unavailable_validator_capabilities: [
        {
          validator_binding_id: 'fixture.object_lookup',
          label: 'Object lookup',
          state: 'under_development',
          state_explanation: 'Object lookup is being wired.',
        },
      ],
    }
    candidate.draft.fields[1]!.metadata.field_metadata = {
      unavailable_validator_capabilities: [
        {
          validator_binding_id: 'fixture.curie_lookup',
          label: 'CURIE lookup',
          state: 'under_development',
          state_explanation: 'CURIE lookup is being wired.',
        },
      ],
    }

    renderEditor(workspace)

    expect(screen.getByTestId('object-validation-state-under_development')).toHaveTextContent(
      "Gene curation isn't ready on this screen yet — validation and submission are still being built.",
    )
    expect(
      screen.getByTestId('object-validation-state-under_development'),
    ).not.toHaveTextContent('Object lookup is being wired.')
    expect(screen.queryByText('Object lookup is being wired.')).not.toBeInTheDocument()
    expect(screen.getByTestId('field-validation-state-field_curie')).toHaveTextContent(
      'CURIE lookup is being wired.',
    )
  })

  it('falls back to a generic under-development banner when no clean domain name is available', () => {
    const workspace = buildWorkspace()
    const candidate = workspace.candidates[0]!
    candidate.validation_summary_projections = []
    candidate.evidence_anchor_projections = []
    candidate.display_label = 'allele-paper-evidence-association-1'
    candidate.metadata = {
      unavailable_validator_capabilities: [
        {
          validator_binding_id: 'fixture.object_lookup',
          label: 'Object lookup',
          state: 'under_development',
          state_explanation: 'Object lookup is being wired.',
        },
      ],
    }

    renderEditor(workspace)

    expect(screen.getByTestId('object-validation-state-under_development')).toHaveTextContent(
      "This curation isn't ready on this screen yet — validation and submission are still being built.",
    )
  })

  it('surfaces required, read-only, and controlled field states from layout metadata', () => {
    renderEditor()

    expect(screen.getByTestId('field-support-details-field_symbol')).toHaveTextContent(
      'Required',
    )
    expect(screen.getByTestId('field-support-details-field_curie')).toHaveTextContent(
      'Read only',
    )
    expect(screen.getByTestId('field-support-details-field_curie')).toHaveTextContent(
      'Controlled',
    )
    expect(screen.getByLabelText('Gene CURIE')).toBeDisabled()
  })

  it('marks missing evidence text explicitly', () => {
    const workspace = buildWorkspace()
    const projection = workspace.candidates[0]!.evidence_anchor_projections![0]!
    projection.quote = null
    projection.anchor.sentence_text = null
    projection.anchor.snippet_text = null
    projection.anchor.normalized_text = null

    renderEditor(workspace)

    expect(
      screen.getByRole('button', {
        name: 'Highlight field evidence 1: [missing evidence text]',
      }),
    ).toBeInTheDocument()
  })

  it('dispatches pdf navigation from the section evidence chip', async () => {
    const user = userEvent.setup()
    const onNavigateEvidence = vi.fn()
    const unsubscribe = onPDFViewerNavigateEvidence(onNavigateEvidence)

    renderEditor()

    await user.click(screen.getByTestId('field-section-evidence-details'))

    expect(onNavigateEvidence).toHaveBeenCalledTimes(1)
    const command = onNavigateEvidence.mock.calls[0][0].detail.command
    expect(command.anchorId).toBe('evidence-1')
    expect(command.searchText).toBe(FIELD_EVIDENCE_QUOTE)
    expect(command.mode).toBe('select')

    unsubscribe()
  })

  it('previews, expands, and dispatches pdf navigation from the field evidence quote details', async () => {
    const user = userEvent.setup()
    const onNavigateEvidence = vi.fn()
    const unsubscribe = onPDFViewerNavigateEvidence(onNavigateEvidence)

    renderEditor()

    const summary = screen.getByTestId('field-evidence-projection-evidence-1')
    const details = summary.closest('details')
    expect(details).not.toBeNull()
    expect(details).not.toHaveAttribute('open')
    expect(summary).toHaveTextContent(FIELD_EVIDENCE_PREVIEW)
    expect(summary).not.toHaveTextContent(FIELD_EVIDENCE_QUOTE)

    await user.click(summary)

    expect(onNavigateEvidence).toHaveBeenCalledTimes(1)
    const command = onNavigateEvidence.mock.calls[0][0].detail.command
    expect(command.anchorId).toBe('evidence-1')
    expect(command.searchText).toBe(FIELD_EVIDENCE_QUOTE)
    expect(command.mode).toBe('select')
    expect(details).toHaveAttribute('open')
    expect(details).toHaveTextContent(`“${FIELD_EVIDENCE_QUOTE}”`)

    unsubscribe()
  })

  it('dispatches pdf navigation from the object evidence quote', async () => {
    const user = userEvent.setup()
    const onNavigateEvidence = vi.fn()
    const unsubscribe = onPDFViewerNavigateEvidence(onNavigateEvidence)

    renderEditor()

    await user.click(
      screen.getByRole('button', {
        name: 'Highlight object evidence 1: Object-level evidence quote from the loaded paper.',
      }),
    )

    expect(onNavigateEvidence).toHaveBeenCalledTimes(1)
    const command = onNavigateEvidence.mock.calls[0][0].detail.command
    expect(command.anchorId).toBe('evidence-object-1')
    expect(command.searchText).toBe('Object-level evidence quote from the loaded paper.')
    expect(command.pageNumber).toBe(4)
    expect(command.sectionTitle).toBe('Results')
    expect(command.mode).toBe('select')

    unsubscribe()
  })

  it('marks unserializable curator edit values explicitly', () => {
    const workspace = buildWorkspace()
    const circular: Record<string, unknown> = {}
    circular.self = circular
    workspace.action_log[0]!.metadata.before = circular

    renderEditor(workspace)

    expect(screen.getByTestId('field-support-details-field_symbol')).toHaveTextContent(
      'Last curator edit: [unserializable value] -> abc',
    )
  })

  it('labels missing field groups with curator-facing review language', () => {
    const workspace = buildWorkspace()
    workspace.candidates[0]!.draft.fields[0]!.group_key = null
    workspace.candidates[0]!.draft.fields[0]!.group_label = null

    renderEditor(workspace)

    expect(screen.getByText('Fields to review')).toBeInTheDocument()
  })

  it('flushes queued field edits when saving the draft', async () => {
    const user = userEvent.setup()
    const autosave = renderEditor()

    fireEvent.change(screen.getByLabelText('Gene symbol'), {
      target: { value: 'tmem67' },
    })
    await user.click(screen.getByRole('button', { name: 'Save draft' }))

    expect(autosave.queueFieldChange).toHaveBeenLastCalledWith({
      field_key: 'field_symbol',
      value: 'tmem67',
    })
    expect(autosave.flush).toHaveBeenCalledTimes(1)
    expect(autosave.queueFieldChange.mock.invocationCallOrder.at(-1)).toBeLessThan(
      autosave.flush.mock.invocationCallOrder[0],
    )
  })
})
