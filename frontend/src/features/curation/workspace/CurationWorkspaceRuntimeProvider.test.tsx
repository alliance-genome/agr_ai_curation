import { createElement, type ReactNode, useCallback, useMemo, useState } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  CurationReviewSession,
  CurationWorkspace,
} from '@/features/curation/types'
import {
  CurationWorkspaceProvider,
  useCurationWorkspaceAutosave,
  useCurationWorkspaceContext,
  useCurationWorkspaceHydration,
} from './CurationWorkspaceContext'
import { CurationWorkspaceRuntimeProvider } from './CurationWorkspaceRuntimeProvider'

const serviceMocks = vi.hoisted(() => ({
  autosaveCurationCandidateDraft: vi.fn(),
  updateCurationSession: vi.fn(),
}))

vi.mock('@/features/curation/services/curationWorkspaceService', () => ({
  autosaveCurationCandidateDraft: serviceMocks.autosaveCurationCandidateDraft,
  updateCurationSession: serviceMocks.updateCurationSession,
}))

function buildWorkspace(
  status: CurationReviewSession['status'] = 'in_progress',
): CurationWorkspace {
  return {
    session: {
      session_id: 'session-1',
      status,
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
          fields: [
            {
              field_key: 'gene_symbol',
              label: 'Gene symbol',
              value: 'BRCA1',
              seed_value: 'BRCA1',
              order: 0,
              required: true,
              read_only: false,
              dirty: false,
              stale_validation: false,
              evidence_anchor_ids: [],
              metadata: {
                session_state: {
                  scroll_position: 128,
                },
              },
            },
          ],
          created_at: '2026-03-20T12:00:00Z',
          updated_at: '2026-03-20T12:00:00Z',
          metadata: {
            session_state: {
              scroll_position: 128,
            },
          },
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

function buildSavedWorkspaceResponse() {
  const workspace = buildWorkspace()
  const savedCandidate = {
    ...workspace.candidates[0],
    draft: {
      ...workspace.candidates[0].draft,
      version: 2,
      fields: [
        {
          ...workspace.candidates[0].draft.fields[0],
          value: 'BRCA2',
          dirty: false,
        },
      ],
    },
  }

  return {
    candidate: savedCandidate,
    draft: savedCandidate.draft,
    validation_snapshot: null,
    action_log_entry: null,
  }
}

function createWrapper(initialWorkspace: CurationWorkspace) {
  return function Wrapper({ children }: { children: ReactNode }) {
    const [workspace, setWorkspaceState] = useState(initialWorkspace)
    const [activeCandidateId, setActiveCandidateId] = useState<string | null>(
      initialWorkspace.active_candidate_id ?? initialWorkspace.session.current_candidate_id ?? null,
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
        children: createElement(
          CurationWorkspaceRuntimeProvider,
          {
            routeCandidateId: initialWorkspace.active_candidate_id,
            children,
          },
        ),
      },
    )
  }
}

describe('CurationWorkspaceRuntimeProvider', () => {
  beforeEach(() => {
    serviceMocks.autosaveCurationCandidateDraft.mockReset()
    serviceMocks.updateCurationSession.mockReset()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('exposes one shared autosave and hydration controller through the workspace context', async () => {
    serviceMocks.autosaveCurationCandidateDraft.mockResolvedValue(buildSavedWorkspaceResponse())

    const { result } = renderHook(
      () => ({
        context: useCurationWorkspaceContext(),
        autosave: useCurationWorkspaceAutosave(),
        hydration: useCurationWorkspaceHydration(),
      }),
      {
        wrapper: createWrapper(buildWorkspace()),
      },
    )

    await waitFor(() => {
      expect(result.current.hydration.isHydrated).toBe(true)
    })
    expect(result.current.context.autosave).toBe(result.current.autosave)
    expect(result.current.context.hydration).toBe(result.current.hydration)
    expect(result.current.hydration.restoredScrollPosition).toBe(128)

    act(() => {
      result.current.autosave.queueFieldChange({
        field_key: 'gene_symbol',
        value: 'BRCA2',
      })
    })

    await act(async () => {
      await result.current.autosave.flush()
    })

    await waitFor(() => {
      expect(serviceMocks.autosaveCurationCandidateDraft).toHaveBeenCalledTimes(1)
    })
    expect(result.current.context.activeCandidate?.draft.fields[0].value).toBe('BRCA2')
  })
})
