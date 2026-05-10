import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'

import type { CurationWorkspace } from '@/features/curation/types'
import {
  CurationWorkspaceProvider,
  type CurationWorkspaceContextValue,
} from '@/features/curation/workspace/CurationWorkspaceContext'
import theme from '@/theme'
import CandidateFieldEditor from './CandidateFieldEditor'

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
    entity_tags: [],
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
              read_only: false,
              dirty: false,
              stale_validation: false,
              evidence_anchor_ids: [],
              validation_result: null,
              metadata: {
                source_field_path: 'gene.curie',
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
            quote: 'ABC appears in the result sentence.',
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
              snippet_text: 'ABC appears in the result sentence.',
              sentence_text: 'ABC appears in the result sentence.',
              viewer_search_text: 'ABC appears in the result sentence.',
              page_number: 2,
              section_title: 'Results',
              chunk_ids: ['chunk-1'],
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
  it('displays envelope validation, evidence, metadata, and repair history by field', () => {
    renderEditor()

    expect(screen.getByText('Unresolved')).toBeInTheDocument()
    expect(screen.getByText('Symbol needs curator review.')).toBeInTheDocument()
    expect(screen.getByText('Planned')).toBeInTheDocument()
    expect(screen.getByText('The identifier validator is planned.')).toBeInTheDocument()
    expect(screen.getByText('Blocked')).toBeInTheDocument()
    expect(screen.getByText('Evidence score blocks export.')).toBeInTheDocument()
    expect(screen.getByText('Validated')).toBeInTheDocument()
    expect(screen.getByText('Opt out')).toBeInTheDocument()
    expect(screen.getByTestId('object-validation-state-under_development')).toHaveTextContent(
      'The object-level validator is under development.',
    )
    expect(screen.getByTestId('field-evidence-projection-evidence-1')).toHaveTextContent(
      'p. 2',
    )
    expect(screen.getByTestId('field-support-details-field_symbol')).toHaveTextContent(
      'Path: gene.symbol',
    )
    expect(screen.getByTestId('field-support-details-field_symbol')).toHaveTextContent(
      'Last repair: old -> abc',
    )
  })
})
