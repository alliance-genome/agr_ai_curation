import userEvent from '@testing-library/user-event'
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { useMemo, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  CurationManualCandidateCreateResponse,
  CurationWorkspace,
} from '@/features/curation/types'
import theme from '@/theme'
import { CurationWorkspaceProvider, useCurationWorkspaceContext } from '@/features/curation/workspace/CurationWorkspaceContext'

import ManualAnnotationDialog from './ManualAnnotationDialog'

const serviceMocks = vi.hoisted(() => ({
  createManualCurationCandidate: vi.fn(),
}))

vi.mock('@/features/curation/services/curationWorkspaceService', () => ({
  createManualCurationCandidate: serviceMocks.createManualCurationCandidate,
}))

function buildTemplateFields() {
  return [
    {
      field_key: 'field_a',
      label: 'Field A',
      value: 'seed a',
      seed_value: 'seed a',
      field_type: 'string',
      group_key: 'group_one',
      group_label: 'Group One',
      order: 0,
      required: true,
      read_only: false,
      dirty: false,
      stale_validation: false,
      evidence_anchor_ids: [],
      validation_result: null,
      metadata: {
        placeholder: 'Value for field A',
      },
    },
    {
      field_key: 'field_b',
      label: 'Field B',
      value: 'seed b',
      seed_value: 'seed b',
      field_type: 'string',
      group_key: 'group_two',
      group_label: 'Group Two',
      order: 1,
      required: false,
      read_only: false,
      dirty: false,
      stale_validation: false,
      evidence_anchor_ids: [],
      validation_result: null,
      metadata: {},
    },
  ]
}

function buildWorkspace(): CurationWorkspace {
  return {
    session: {
      session_id: 'session-1',
      status: 'in_progress',
      adapter: {
        adapter_key: 'test_adapter',
        profile_key: 'profile_a',
        display_label: 'Test adapter',
        profile_label: 'Profile A',
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
        adapter_key: 'test_adapter',
        profile_key: 'profile_a',
        display_label: 'Candidate one',
        unresolved_ambiguities: [],
        draft: {
          draft_id: 'draft-1',
          candidate_id: 'candidate-1',
          adapter_key: 'test_adapter',
          version: 1,
          title: 'Candidate one draft',
          fields: buildTemplateFields(),
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
    active_candidate_id: 'candidate-1',
    queue_context: null,
    action_log: [],
    submission_history: [],
    saved_view_context: null,
  }
}

function buildZeroCandidateWorkspace(): CurationWorkspace {
  return {
    ...buildWorkspace(),
    session: {
      ...buildWorkspace().session,
      progress: {
        total_candidates: 0,
        reviewed_candidates: 0,
        pending_candidates: 0,
        accepted_candidates: 0,
        rejected_candidates: 0,
        manual_candidates: 0,
      },
      current_candidate_id: null,
      adapter: {
        ...buildWorkspace().session.adapter,
        metadata: {
          manual_draft_fields: buildTemplateFields().map((field) => ({
            ...field,
            value: null,
            seed_value: null,
          })),
        },
      },
    },
    candidates: [],
    active_candidate_id: null,
  }
}

function WorkspaceProbe() {
  const {
    activeCandidate,
    activeCandidateId,
    workspace,
  } = useCurationWorkspaceContext()

  return (
    <>
      <div data-testid="probe-candidate-count">{workspace.candidates.length}</div>
      <div data-testid="probe-candidate-labels">
        {workspace.candidates.map((candidate) => candidate.display_label).join('|')}
      </div>
      <div data-testid="probe-active-candidate-id">{activeCandidateId ?? 'none'}</div>
      <div data-testid="probe-active-candidate-label">
        {activeCandidate?.display_label ?? 'none'}
      </div>
      <div data-testid="probe-action-log-count">{workspace.action_log.length}</div>
      <div data-testid="probe-action-log-types">
        {workspace.action_log.map((entry) => entry.action_type).join('|')}
      </div>
    </>
  )
}

function Harness({
  onClose,
  initialWorkspace = buildWorkspace(),
}: {
  onClose: () => void
  initialWorkspace?: CurationWorkspace
}) {
  const [workspace, setWorkspace] = useState(initialWorkspace)
  const [open, setOpen] = useState(true)
  const [activeCandidateId, setActiveCandidateId] = useState<string | null>(
    initialWorkspace.active_candidate_id ?? null,
  )
  const activeCandidate = useMemo(
    () =>
      workspace.candidates.find((candidate) => candidate.candidate_id === activeCandidateId)
      ?? null,
    [activeCandidateId, workspace.candidates],
  )

  return (
    <ThemeProvider theme={theme}>
      <CurationWorkspaceProvider
        value={{
          workspace,
          setWorkspace,
          session: workspace.session,
          candidates: workspace.candidates,
          activeCandidateId,
          activeCandidate,
          setActiveCandidate: (candidateId) => {
            setActiveCandidateId(candidateId)
          },
        }}
      >
        <ManualAnnotationDialog
          onClose={() => {
            setOpen(false)
            onClose()
          }}
          open={open}
        />
        <WorkspaceProbe />
      </CurationWorkspaceProvider>
    </ThemeProvider>
  )
}

function renderDialog(initialWorkspace = buildWorkspace()) {
  const onClose = vi.fn()

  render(<Harness initialWorkspace={initialWorkspace} onClose={onClose} />)

  return {
    onClose,
  }
}

describe('ManualAnnotationDialog', () => {
  beforeEach(() => {
    serviceMocks.createManualCurationCandidate.mockReset()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('renders the shared draft-field template and evidence controls', async () => {
    renderDialog()

    const dialog = await screen.findByRole('dialog')

    expect(within(dialog).getByText('Add Manual Annotation')).toBeInTheDocument()
    expect(
      within(dialog).getByRole('combobox', { name: 'Adapter / profile' }),
    ).toHaveTextContent(
      'Test adapter / Profile A',
    )
    expect(within(dialog).getByLabelText('Annotation label')).toHaveValue('')
    expect(within(dialog).getByText('GROUP ONE')).toBeInTheDocument()
    expect(within(dialog).getByText('GROUP TWO')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Field A')).toHaveValue('')
    expect(within(dialog).getByLabelText('Field B')).toHaveValue('')
    expect(within(dialog).getByText('Evidence links')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Add evidence' })).toBeInTheDocument()
  })

  it('creates a manual candidate optimistically and appends the action log entry on success', async () => {
    const user = userEvent.setup()
    let resolveCreate: ((value: unknown) => void) | null = null
    const createPromise = new Promise((resolve) => {
      resolveCreate = resolve
    })
    serviceMocks.createManualCurationCandidate.mockReturnValue(createPromise)

    const { onClose } = renderDialog()
    const dialog = await screen.findByRole('dialog')

    fireEvent.change(within(dialog).getByLabelText('Annotation label'), {
      target: { value: 'Manual candidate' },
    })
    fireEvent.change(within(dialog).getByLabelText('Field A'), {
      target: { value: 'value alpha' },
    })

    await user.click(within(dialog).getByRole('button', { name: 'Add evidence' }))

    const evidenceRow = within(dialog).getByTestId(/manual-annotation-evidence-row-/)
    fireEvent.change(within(evidenceRow).getByLabelText('Snippet text'), {
      target: { value: 'Quoted support text' },
    })
    fireEvent.change(within(evidenceRow).getByLabelText('Page'), {
      target: { value: '4' },
    })

    await user.click(within(dialog).getByTestId('manual-annotation-create-button'))

    await waitFor(() => {
      expect(serviceMocks.createManualCurationCandidate).toHaveBeenCalledTimes(1)
    })

    await waitFor(() => {
      expect(screen.getByTestId('probe-candidate-count')).toHaveTextContent('2')
      expect(screen.getByTestId('probe-candidate-labels')).toHaveTextContent('Manual candidate')
      expect(screen.getByTestId('probe-active-candidate-label')).toHaveTextContent(
        'Manual candidate',
      )
    })

    expect(serviceMocks.createManualCurationCandidate).toHaveBeenCalledTimes(1)
    expect(serviceMocks.createManualCurationCandidate).toHaveBeenCalledWith(
      expect.objectContaining({
        session_id: 'session-1',
        adapter_key: 'test_adapter',
        profile_key: 'profile_a',
        source: 'manual',
        display_label: expect.any(String),
        draft: expect.objectContaining({
          title: 'Manual candidate',
          fields: expect.arrayContaining([
            expect.objectContaining({
              field_key: 'field_a',
              value: 'value alpha',
              dirty: false,
            }),
          ]),
        }),
        evidence_anchors: expect.arrayContaining([
          expect.objectContaining({
            source: 'manual',
            field_keys: ['field_a'],
            anchor: expect.objectContaining({
              snippet_text: 'Quoted support text',
              page_number: 4,
            }),
          }),
        ]),
      }),
    )

    resolveCreate?.({
      candidate: {
        candidate_id: 'candidate-manual-1',
        session_id: 'session-1',
        source: 'manual',
        status: 'pending',
        order: 1,
        adapter_key: 'test_adapter',
        profile_key: 'profile_a',
        display_label: 'Manual candidate',
        unresolved_ambiguities: [],
        draft: {
          draft_id: 'draft-manual-1',
          candidate_id: 'candidate-manual-1',
          adapter_key: 'test_adapter',
          version: 1,
          title: 'Manual candidate',
          fields: [
            {
              field_key: 'field_a',
              label: 'Field A',
              value: 'value alpha',
              seed_value: 'value alpha',
              field_type: 'string',
              group_key: 'group_one',
              group_label: 'Group One',
              order: 0,
              required: true,
              read_only: false,
              dirty: false,
              stale_validation: false,
              evidence_anchor_ids: ['anchor-manual-1'],
              validation_result: null,
              metadata: {
                placeholder: 'Value for field A',
              },
            },
            {
              field_key: 'field_b',
              label: 'Field B',
              value: null,
              seed_value: null,
              field_type: 'string',
              group_key: 'group_two',
              group_label: 'Group Two',
              order: 1,
              required: false,
              read_only: false,
              dirty: false,
              stale_validation: false,
              evidence_anchor_ids: [],
              validation_result: null,
              metadata: {},
            },
          ],
          created_at: '2026-03-21T10:00:00Z',
          updated_at: '2026-03-21T10:00:00Z',
          last_saved_at: '2026-03-21T10:00:00Z',
          metadata: {},
        },
        evidence_anchors: [
          {
            anchor_id: 'anchor-manual-1',
            candidate_id: 'candidate-manual-1',
            source: 'manual',
            field_keys: ['field_a'],
            field_group_keys: ['group_one'],
            is_primary: false,
            anchor: {
              anchor_kind: 'snippet',
              locator_quality: 'exact_quote',
              supports_decision: 'supports',
              snippet_text: 'Quoted support text',
              sentence_text: 'Quoted support text',
              normalized_text: null,
              viewer_search_text: 'Quoted support text',
              viewer_highlightable: true,
              pdfx_markdown_offset_start: null,
              pdfx_markdown_offset_end: null,
              page_number: 4,
              page_label: null,
              section_title: null,
              subsection_title: null,
              figure_reference: null,
              table_reference: null,
              chunk_ids: [],
            },
            created_at: '2026-03-21T10:00:00Z',
            updated_at: '2026-03-21T10:00:00Z',
            warnings: [],
          },
        ],
        validation: null,
        evidence_summary: null,
        created_at: '2026-03-21T10:00:00Z',
        updated_at: '2026-03-21T10:00:00Z',
        metadata: {},
      },
      session: {
        ...buildWorkspace().session,
        session_version: 2,
        current_candidate_id: 'candidate-manual-1',
        last_worked_at: '2026-03-21T10:00:00Z',
        progress: {
          total_candidates: 2,
          reviewed_candidates: 0,
          pending_candidates: 2,
          accepted_candidates: 0,
          rejected_candidates: 0,
          manual_candidates: 1,
        },
      },
      action_log_entry: {
        action_id: 'action-1',
        session_id: 'session-1',
        candidate_id: 'candidate-manual-1',
        draft_id: 'draft-manual-1',
        action_type: 'candidate_created',
        actor_type: 'user',
        actor: {
          actor_id: 'user-1',
        },
        occurred_at: '2026-03-21T10:00:00Z',
        previous_session_status: null,
        new_session_status: null,
        previous_candidate_status: null,
        new_candidate_status: 'pending',
        changed_field_keys: ['field_a', 'field_b'],
        evidence_anchor_ids: ['anchor-manual-1'],
        reason: null,
        message: 'Manual candidate created',
        metadata: {
          adapter_key: 'test_adapter',
        },
      },
    } satisfies CurationManualCandidateCreateResponse)

    await waitFor(() => {
      expect(onClose).toHaveBeenCalledTimes(1)
      expect(screen.getByTestId('probe-active-candidate-id')).toHaveTextContent(
        'candidate-manual-1',
      )
      expect(screen.getByTestId('probe-action-log-count')).toHaveTextContent('1')
      expect(screen.getByTestId('probe-action-log-types')).toHaveTextContent(
        'candidate_created',
      )
    })
  })

  it('uses the session adapter template when the workspace has no existing candidates', async () => {
    const user = userEvent.setup()
    serviceMocks.createManualCurationCandidate.mockReturnValue(new Promise(() => {}))

    renderDialog(buildZeroCandidateWorkspace())
    const dialog = await screen.findByRole('dialog')

    expect(within(dialog).queryByText(/No shared draft-field template/i)).not.toBeInTheDocument()
    expect(within(dialog).getByLabelText('Field A')).toHaveValue('')
    expect(within(dialog).getByLabelText('Field B')).toHaveValue('')

    await user.type(
      within(dialog).getByLabelText('Annotation label'),
      'Zero candidate manual annotation',
    )
    await user.type(within(dialog).getByLabelText('Field A'), 'value alpha')
    await user.click(within(dialog).getByTestId('manual-annotation-create-button'))

    await waitFor(() => {
      expect(serviceMocks.createManualCurationCandidate).toHaveBeenCalledTimes(1)
    })

    expect(serviceMocks.createManualCurationCandidate).toHaveBeenCalledWith(
      expect.objectContaining({
        session_id: 'session-1',
        adapter_key: 'test_adapter',
        profile_key: 'profile_a',
        source: 'manual',
        display_label: expect.any(String),
        draft: expect.objectContaining({
          fields: expect.arrayContaining([
            expect.objectContaining({
              field_key: 'field_a',
              value: 'value alpha',
            }),
          ]),
        }),
      }),
    )
  })
})
