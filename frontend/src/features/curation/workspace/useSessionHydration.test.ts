import { createElement, type ReactNode, useCallback, useMemo, useState } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { CurationWorkspace } from '@/features/curation/types'
import {
  CurationWorkspaceProvider,
  useCurationWorkspaceContext,
} from './CurationWorkspaceContext'
import { useSessionHydration } from './useSessionHydration'

const serviceMocks = vi.hoisted(() => ({
  updateCurationSession: vi.fn(),
}))

vi.mock('@/features/curation/services/curationWorkspaceService', () => ({
  updateCurationSession: serviceMocks.updateCurationSession,
}))

function buildWorkspace(): CurationWorkspace {
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
        total_candidates: 2,
        reviewed_candidates: 0,
        pending_candidates: 2,
        accepted_candidates: 0,
        rejected_candidates: 0,
        manual_candidates: 0,
      },
      current_candidate_id: 'candidate-2',
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
        source: 'manual',
        status: 'pending',
        order: 0,
        adapter_key: 'gene',
        display_label: 'Candidate 1',
        draft: {
          draft_id: 'draft-1',
          candidate_id: 'candidate-1',
          adapter_key: 'gene',
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
      },
      {
        candidate_id: 'candidate-2',
        session_id: 'session-1',
        source: 'manual',
        status: 'pending',
        order: 1,
        adapter_key: 'gene',
        display_label: 'Candidate 2',
        draft: {
          draft_id: 'draft-2',
          candidate_id: 'candidate-2',
          adapter_key: 'gene',
          version: 1,
          fields: [
            {
              field_key: 'gene_symbol',
              label: 'Gene symbol',
              value: 'BRCA2',
              seed_value: 'BRCA2',
              order: 0,
              required: true,
              read_only: false,
              dirty: true,
              stale_validation: false,
              evidence_anchor_ids: [],
              metadata: {},
            },
          ],
          created_at: '2026-03-20T12:00:00Z',
          updated_at: '2026-03-20T12:00:00Z',
          metadata: {
            session_state: {
              scroll_position: 128,
              cursor_field_key: 'gene_symbol',
            },
          },
        },
        evidence_anchors: [],
        created_at: '2026-03-20T12:00:00Z',
        updated_at: '2026-03-20T12:00:00Z',
        metadata: {},
      },
    ],
    active_candidate_id: 'candidate-2',
    queue_context: null,
    action_log: [],
    submission_history: [],
    saved_view_context: null,
  }
}

function createWrapper(
  initialWorkspace: CurationWorkspace,
  initialActiveCandidateId: string | null = null,
) {
  return function Wrapper({ children }: { children: ReactNode }) {
    const [workspace, setWorkspaceState] = useState(initialWorkspace)
    const [activeCandidateId, setActiveCandidateId] = useState<string | null>(
      initialActiveCandidateId,
    )
    const setWorkspace = useCallback(
      (
        nextWorkspace:
          | CurationWorkspace
          | ((currentWorkspace: CurationWorkspace) => CurationWorkspace),
      ) => {
        setWorkspaceState((currentWorkspace) =>
          typeof nextWorkspace === 'function'
            ? nextWorkspace(currentWorkspace)
            : nextWorkspace,
        )
      },
      [],
    )
    const setActiveCandidate = useCallback((candidateId: string | null) => {
      setActiveCandidateId(candidateId)
    }, [])

    const activeCandidate = useMemo(
      () =>
        workspace.candidates.find((candidate) => candidate.candidate_id === activeCandidateId) ??
        null,
      [activeCandidateId, workspace.candidates],
    )
    const contextValue = useMemo(
      () => ({
        workspace,
        setWorkspace,
        session: workspace.session,
        candidates: workspace.candidates,
        activeCandidateId,
        activeCandidate,
        setActiveCandidate,
      }),
      [
        activeCandidate,
        activeCandidateId,
        setActiveCandidate,
        setWorkspace,
        workspace,
      ],
    )

    return createElement(
      CurationWorkspaceProvider,
      {
        value: contextValue,
        children,
      },
    )
  }
}

describe('useSessionHydration', () => {
  beforeEach(() => {
    serviceMocks.updateCurationSession.mockReset()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('hydrates the active candidate and resume metadata from the workspace response', async () => {
    const { result } = renderHook(
      () => ({
        hydration: useSessionHydration(),
        context: useCurationWorkspaceContext(),
      }),
      {
        wrapper: createWrapper(buildWorkspace()),
      },
    )

    await waitFor(() => {
      expect(result.current.hydration.isHydrated).toBe(true)
    })
    expect(result.current.context.activeCandidateId).toBe('candidate-2')
    expect(result.current.hydration.restoredCandidateId).toBe('candidate-2')
    expect(result.current.hydration.restoredScrollPosition).toBe(128)
    expect(result.current.hydration.restoredCursorFieldKey).toBe('gene_symbol')
    expect(result.current.hydration.dirtyFieldKeys).toEqual(['gene_symbol'])
    expect(serviceMocks.updateCurationSession).not.toHaveBeenCalled()
  })

  it('falls back to the first pending candidate when no prior session state exists', async () => {
    const workspace = buildWorkspace()
    workspace.active_candidate_id = null
    workspace.session.current_candidate_id = null
    workspace.candidates[1].draft.metadata = {}
    workspace.candidates[1].draft.fields[0].dirty = false

    serviceMocks.updateCurationSession.mockResolvedValue({
      session: {
        ...workspace.session,
        current_candidate_id: 'candidate-1',
      },
      action_log_entry: null,
    })

    const { result } = renderHook(
      () => ({
        hydration: useSessionHydration(),
        context: useCurationWorkspaceContext(),
      }),
      {
        wrapper: createWrapper(workspace),
      },
    )

    await waitFor(() => {
      expect(result.current.context.activeCandidateId).toBe('candidate-1')
    })
    await waitFor(() => {
      expect(serviceMocks.updateCurationSession).toHaveBeenCalledWith({
        session_id: 'session-1',
        current_candidate_id: 'candidate-1',
      })
    })

    expect(result.current.hydration.restoredCandidateId).toBe('candidate-1')
    expect(result.current.hydration.restoredScrollPosition).toBeNull()
    expect(result.current.hydration.restoredCursorFieldKey).toBeNull()
    expect(result.current.hydration.dirtyFieldKeys).toEqual([])
  })
})
