import userEvent from '@testing-library/user-event'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'

import type {
  CurationCandidate,
  CurationReviewSession,
  CurationWorkspace,
} from '../types'
import theme from '@/theme'
import { CurationWorkspaceProvider } from '../workspace/CurationWorkspaceContext'
import AnnotationEditor, { type AnnotationEditorProps } from './AnnotationEditor'

function buildWorkspace(
  overrides: {
    activeCandidateId?: string | null
    candidateFields?: CurationCandidate['draft']['fields']
    workspace?: CurationWorkspace
  } = {},
): CurationWorkspace {
  if (overrides.workspace) {
    return overrides.workspace
  }

  const activeCandidateId = Object.prototype.hasOwnProperty.call(overrides, 'activeCandidateId')
    ? overrides.activeCandidateId ?? null
    : 'candidate-1'
  const candidateFields = overrides.candidateFields ?? [
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
      evidence_anchor_ids: ['anchor-1'],
      validation_result: null,
      metadata: {
        placeholder: 'Enter symbol',
      },
    },
    {
      field_key: 'relationship_type',
      label: 'Relationship type',
      value: 'interacts_with',
      seed_value: 'interacts_with',
      field_type: 'string',
      group_key: 'relationship',
      group_label: 'Relationship',
      order: 1,
      required: false,
      read_only: false,
      dirty: false,
      stale_validation: false,
      evidence_anchor_ids: [],
      validation_result: null,
      metadata: {},
    },
    {
      field_key: 'context_note',
      label: 'Context note',
      value: 'Observed in human tissue',
      seed_value: 'Observed in human tissue',
      field_type: 'string',
      group_key: 'context',
      group_label: 'Context',
      order: 2,
      required: false,
      read_only: false,
      dirty: false,
      stale_validation: false,
      evidence_anchor_ids: [],
      validation_result: null,
      metadata: {},
    },
  ]

  return {
    session: {
      session_id: 'session-1',
      status: 'in_progress',
      adapter: {
        adapter_key: 'gene',
        display_label: 'Gene',
        metadata: {},
      },
      document: {
        document_id: 'document-1',
        title: 'Workspace document',
      },
      progress: {
        total_candidates: 1,
        reviewed_candidates: 0,
        pending_candidates: 1,
        accepted_candidates: 0,
        rejected_candidates: 0,
        manual_candidates: 0,
      },
      current_candidate_id: activeCandidateId,
      prepared_at: '2026-03-20T12:00:00Z',
      warnings: [],
      tags: [],
      session_version: 1,
      extraction_results: [],
    } satisfies CurationReviewSession,
    candidates: [
      {
        candidate_id: 'candidate-1',
        session_id: 'session-1',
        source: 'manual',
        status: 'pending',
        order: 0,
        adapter_key: 'gene',
        display_label: 'Candidate 1',
        conversation_summary: 'AI-seeded draft ready for curator review.',
        draft: {
          draft_id: 'draft-1',
          candidate_id: 'candidate-1',
          adapter_key: 'gene',
          version: 1,
          title: 'Candidate 1 draft',
          summary: 'Shared editor summary',
          fields: candidateFields,
          created_at: '2026-03-20T12:00:00Z',
          updated_at: '2026-03-20T12:00:00Z',
          metadata: {},
        },
        evidence_anchors: [],
        created_at: '2026-03-20T12:00:00Z',
        updated_at: '2026-03-20T12:00:00Z',
        metadata: {},
      },
    ],
    active_candidate_id: activeCandidateId,
    queue_context: null,
    action_log: [],
    submission_history: [],
    saved_view_context: null,
  }
}

function buildAnnotationEditorTree(
  workspace: CurationWorkspace,
  props: Partial<AnnotationEditorProps> = {},
) {
  const activeCandidateId = workspace.active_candidate_id ?? null
  const activeCandidate =
    workspace.candidates.find((candidate) => candidate.candidate_id === activeCandidateId) ?? null
  const resolvedProps: AnnotationEditorProps = {
    emptyState: props.emptyState,
    onFieldChange: props.onFieldChange,
    renderEvidence: props.renderEvidence,
    renderFieldInput: props.renderFieldInput,
    renderRevert: props.renderRevert,
    renderValidation: props.renderValidation,
  }

  return (
    <ThemeProvider theme={theme}>
      <CurationWorkspaceProvider
        value={{
          workspace,
          setWorkspace: vi.fn(),
          session: workspace.session,
          candidates: workspace.candidates,
          activeCandidateId,
          activeCandidate,
          setActiveCandidate: vi.fn(),
        }}
      >
        <AnnotationEditor {...resolvedProps} />
      </CurationWorkspaceProvider>
    </ThemeProvider>,
  )
}

function renderAnnotationEditor(
  props: Partial<AnnotationEditorProps> = {},
  options: {
    activeCandidateId?: string | null
    candidateFields?: CurationCandidate['draft']['fields']
    workspace?: CurationWorkspace
  } = {},
) {
  const workspace = buildWorkspace(options)

  return render(buildAnnotationEditorTree(workspace, props))
}

describe('AnnotationEditor', () => {
  it('renders grouped field sections and forwards slot content per field', () => {
    renderAnnotationEditor({
      renderValidation: (field) => <span>{`validate-${field.field_key}`}</span>,
      renderEvidence: (field) =>
        field.evidence_anchor_ids.length > 0
          ? <button type="button">p.3</button>
          : null,
    })

    expect(screen.getByText('Candidate 1 draft')).toBeInTheDocument()
    expect(screen.getByText('Shared editor summary')).toBeInTheDocument()
    expect(screen.getByText('PRIMARY DATA')).toBeInTheDocument()
    expect(screen.getByText('RELATIONSHIP')).toBeInTheDocument()
    expect(screen.getByText('CONTEXT')).toBeInTheDocument()

    const primarySection = screen.getByTestId('annotation-editor-section-primary_data')
    expect(within(primarySection).getByText('Gene symbol')).toBeInTheDocument()
    expect(within(primarySection).getByLabelText('Gene symbol')).toHaveValue('BRCA1')
    expect(within(primarySection).getByText('validate-gene_symbol')).toBeInTheDocument()
    expect(within(primarySection).getByRole('button', { name: 'p.3' })).toBeInTheDocument()
  })

  it('tracks local edits and exposes revert callbacks against the seed value', async () => {
    const user = userEvent.setup()
    const onFieldChange = vi.fn()

    renderAnnotationEditor({
      onFieldChange,
      renderRevert: (field, { canRevert, revert }) =>
        canRevert ? (
          <button onClick={revert} type="button">
            Revert {field.label}
          </button>
        ) : null,
    })

    const input = screen.getByLabelText('Gene symbol')

    fireEvent.change(input, {
      target: { value: 'BRCA2' },
    })

    expect(screen.getByLabelText('Gene symbol')).toHaveValue('BRCA2')
    expect(onFieldChange).toHaveBeenCalledWith(
      {
        field_key: 'gene_symbol',
        value: 'BRCA2',
      },
      expect.objectContaining({
        field_key: 'gene_symbol',
      }),
    )

    await user.click(screen.getByRole('button', { name: 'Revert Gene symbol' }))

    expect(screen.getByLabelText('Gene symbol')).toHaveValue('BRCA1')
    expect(onFieldChange).toHaveBeenLastCalledWith(
      {
        field_key: 'gene_symbol',
        revert_to_seed: true,
      },
      expect.objectContaining({
        field_key: 'gene_symbol',
      }),
    )
  })

  it('shows an empty-state prompt when no candidate is active', () => {
    renderAnnotationEditor({}, { activeCandidateId: null })

    expect(screen.getByText('Select a candidate to begin editing.')).toBeInTheDocument()
  })

  it('renders new candidate field slots immediately when the active candidate changes', () => {
    const slotSpy = vi.fn((field: CurationCandidate['draft']['fields'][number]) => (
      <span>{`slot-${field.field_key}`}</span>
    ))
    const firstWorkspace = buildWorkspace()
    const firstCandidate = firstWorkspace.candidates[0]
    const secondCandidate: CurationCandidate = {
      ...firstCandidate,
      candidate_id: 'candidate-2',
      display_label: 'Candidate 2',
      conversation_summary: 'Second candidate summary',
      draft: {
        ...firstCandidate.draft,
        draft_id: 'draft-2',
        candidate_id: 'candidate-2',
        title: 'Candidate 2 draft',
        summary: 'Second editor summary',
        fields: [
          {
            field_key: 'disease_term',
            label: 'Disease term',
            value: 'Alzheimer disease',
            seed_value: 'Alzheimer disease',
            field_type: 'string',
            group_key: 'context',
            group_label: 'Context',
            order: 0,
            required: true,
            read_only: false,
            dirty: false,
            stale_validation: false,
            evidence_anchor_ids: [],
            validation_result: null,
            metadata: {},
          },
        ],
        updated_at: '2026-03-20T12:05:00Z',
      },
    }
    const initialWorkspace: CurationWorkspace = {
      ...firstWorkspace,
      session: {
        ...firstWorkspace.session,
        current_candidate_id: 'candidate-1',
        progress: {
          ...firstWorkspace.session.progress,
          total_candidates: 2,
          pending_candidates: 2,
        },
      },
      candidates: [
        firstCandidate,
        secondCandidate,
      ],
      active_candidate_id: 'candidate-1',
    }
    const nextWorkspace: CurationWorkspace = {
      ...initialWorkspace,
      session: {
        ...initialWorkspace.session,
        current_candidate_id: 'candidate-2',
      },
      active_candidate_id: 'candidate-2',
    }
    const { rerender } = renderAnnotationEditor(
      {
        renderValidation: slotSpy,
      },
      {
        workspace: initialWorkspace,
      },
    )

    slotSpy.mockClear()

    rerender(
      buildAnnotationEditorTree(nextWorkspace, {
        renderValidation: slotSpy,
      }),
    )

    expect(screen.getByText('Candidate 2 draft')).toBeInTheDocument()
    expect(screen.getByText('Second editor summary')).toBeInTheDocument()
    expect(screen.getByLabelText('Disease term')).toHaveValue('Alzheimer disease')
    expect(screen.queryByLabelText('Gene symbol')).not.toBeInTheDocument()
    expect(slotSpy.mock.calls.map(([field]) => field.field_key)).not.toContain('gene_symbol')
    expect(slotSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        field_key: 'disease_term',
      }),
    )
  })
})
