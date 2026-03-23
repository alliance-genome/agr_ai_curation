import userEvent from '@testing-library/user-event'
import { render, screen } from '@testing-library/react'
import { ThemeProvider } from '@mui/material/styles'
import { describe, expect, it, vi } from 'vitest'

import type {
  CurationCandidate,
  CurationWorkspace,
} from '@/features/curation/types'
import theme from '@/theme'

import {
  CurationWorkspaceProvider,
  type CurationWorkspaceContextValue,
} from './CurationWorkspaceContext'
import CandidateQueue from './CandidateQueue'

function buildCandidate(
  candidateId: string,
  overrides: Partial<CurationCandidate> = {},
): CurationCandidate {
  return {
    candidate_id: candidateId,
    session_id: 'session-queue',
    source: 'extracted',
    status: 'pending',
    order: 0,
    adapter_key: 'entity_adapter',
    display_label: `Candidate ${candidateId}`,
    unresolved_ambiguities: [],
    draft: {
      draft_id: `draft-${candidateId}`,
      candidate_id: candidateId,
      adapter_key: 'entity_adapter',
      version: 1,
      fields: [],
      created_at: '2026-03-20T12:00:00Z',
      updated_at: '2026-03-20T12:00:00Z',
      metadata: {},
    },
    evidence_anchors: [],
    created_at: '2026-03-20T12:00:00Z',
    updated_at: '2026-03-20T12:00:00Z',
    metadata: {},
    ...overrides,
  }
}

function buildWorkspace(): CurationWorkspace {
  return {
    session: {
      session_id: 'session-queue',
      status: 'in_progress',
      adapter: {
        adapter_key: 'entity_adapter',
        display_label: 'Entity',
        metadata: {},
      },
      document: {
        document_id: 'document-queue',
        title: 'Queue Document',
      },
      progress: {
        total_candidates: 4,
        reviewed_candidates: 2,
        pending_candidates: 2,
        accepted_candidates: 1,
        rejected_candidates: 1,
        manual_candidates: 0,
      },
      current_candidate_id: 'candidate-active',
      prepared_at: '2026-03-20T12:00:00Z',
      warnings: [],
      tags: [],
      session_version: 1,
      extraction_results: [],
    },
    candidates: [
      buildCandidate('candidate-rejected', {
        display_label: 'Queue item rejected',
        status: 'rejected',
        order: 3,
      }),
      buildCandidate('candidate-todo', {
        display_label: 'Queue item pending',
        status: 'pending',
        order: 2,
      }),
      buildCandidate('candidate-active', {
        display_label: 'Queue item active',
        secondary_label: 'Secondary context',
        status: 'pending',
        order: 1,
        evidence_anchors: [
          {
            anchor_id: 'anchor-1',
            candidate_id: 'candidate-active',
            source: 'manual',
            field_keys: ['field_a'],
            field_group_keys: ['group_a'],
            is_primary: true,
            anchor: {
              anchor_kind: 'snippet',
              chunk_ids: [],
              locator_quality: 'exact_quote',
              supports_decision: 'supports',
            },
            created_at: '2026-03-20T12:00:00Z',
            updated_at: '2026-03-20T12:00:00Z',
            warnings: [],
          },
          {
            anchor_id: 'anchor-2',
            candidate_id: 'candidate-active',
            source: 'manual',
            field_keys: ['field_b'],
            field_group_keys: ['group_b'],
            is_primary: false,
            anchor: {
              anchor_kind: 'snippet',
              chunk_ids: [],
              locator_quality: 'normalized_quote',
              supports_decision: 'supports',
            },
            created_at: '2026-03-20T12:00:00Z',
            updated_at: '2026-03-20T12:00:00Z',
            warnings: [],
          },
        ],
        validation: {
          state: 'completed',
          counts: {
            validated: 3,
            ambiguous: 1,
            not_found: 0,
            invalid_format: 1,
            conflict: 0,
            skipped: 0,
            overridden: 0,
          },
          stale_field_keys: [],
          warnings: [],
        },
      }),
      buildCandidate('candidate-accepted', {
        display_label: 'Queue item accepted',
        secondary_label: 'Accepted details',
        status: 'accepted',
        order: 0,
      }),
    ],
    active_candidate_id: 'candidate-active',
    queue_context: null,
    action_log: [],
    submission_history: [],
    saved_view_context: null,
  }
}

function renderQueue(overrides: Partial<CurationWorkspaceContextValue> = {}) {
  const workspace = overrides.workspace ?? buildWorkspace()
  const activeCandidateId = overrides.activeCandidateId ?? 'candidate-active'
  const activeCandidate = workspace.candidates.find(
    (candidate) => candidate.candidate_id === activeCandidateId,
  ) ?? null

  const contextValue: CurationWorkspaceContextValue = {
    workspace,
    session: workspace.session,
    candidates: workspace.candidates,
    activeCandidateId,
    activeCandidate,
    setActiveCandidate: vi.fn(),
    ...overrides,
  }

  render(
    <ThemeProvider theme={theme}>
      <CurationWorkspaceProvider value={contextValue}>
        <CandidateQueue />
      </CurationWorkspaceProvider>
    </ThemeProvider>,
  )

  return contextValue
}

describe('CandidateQueue', () => {
  it('renders the queue header, progress segments, and compact card details', () => {
    renderQueue()

    expect(screen.getByText('Candidates (4)')).toBeInTheDocument()
    expect(screen.getByText('2/4 reviewed')).toBeInTheDocument()
    expect(screen.getByText('1✓ accepted')).toBeInTheDocument()
    expect(screen.getByText('1✎ editing')).toBeInTheDocument()
    expect(screen.getByText('1— pending')).toBeInTheDocument()

    expect(screen.getByText('Queue item active')).toBeInTheDocument()
    expect(screen.getByText('Secondary context')).toBeInTheDocument()
    expect(screen.getByText('3/5 ✓ 1⚠ 1✕')).toBeInTheDocument()
    expect(screen.getByTestId('candidate-queue-evidence-candidate-active')).toHaveTextContent(
      '📎 2',
    )

    expect(
      screen.getByTestId('candidate-queue-progress-segment-candidate-accepted'),
    ).toHaveAttribute('data-segment-state', 'done')
    expect(
      screen.getByTestId('candidate-queue-progress-segment-candidate-active'),
    ).toHaveAttribute('data-segment-state', 'active')
    expect(
      screen.getByTestId('candidate-queue-progress-segment-candidate-todo'),
    ).toHaveAttribute('data-segment-state', 'todo')
    expect(
      screen.getByTestId('candidate-queue-progress-segment-candidate-rejected'),
    ).toHaveAttribute('data-segment-state', 'done')
  })

  it('applies visual state attributes and selects a candidate on click', async () => {
    const user = userEvent.setup()
    const setActiveCandidate = vi.fn()

    renderQueue({ setActiveCandidate })

    expect(screen.getByTestId('candidate-queue-card-candidate-accepted')).toHaveAttribute(
      'data-status-appearance',
      'accepted',
    )
    expect(screen.getByTestId('candidate-queue-card-candidate-active')).toHaveAttribute(
      'data-status-appearance',
      'active',
    )
    expect(screen.getByTestId('candidate-queue-card-candidate-todo')).toHaveAttribute(
      'data-status-appearance',
      'pending',
    )
    expect(screen.getByTestId('candidate-queue-card-candidate-rejected')).toHaveAttribute(
      'data-status-appearance',
      'rejected',
    )
    expect(screen.getByTestId('candidate-queue-card-candidate-active')).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByTestId('candidate-queue-status-icon-candidate-active')).toHaveTextContent(
      '▸',
    )

    await user.click(screen.getByTestId('candidate-queue-card-candidate-accepted'))

    expect(setActiveCandidate).toHaveBeenCalledWith('candidate-accepted')
  })
})
