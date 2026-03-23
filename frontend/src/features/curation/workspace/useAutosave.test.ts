import { createElement, type ReactNode, useCallback, useMemo, useState } from 'react'
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  CurationReviewSession,
  CurationWorkspace,
} from '@/features/curation/types'
import {
  CurationWorkspaceProvider,
  useCurationWorkspaceContext,
} from './CurationWorkspaceContext'
import { useAutosave } from './useAutosave'

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
    candidates: [
      {
        candidate_id: 'candidate-1',
        session_id: 'session-1',
        source: 'manual',
        status: 'pending',
        order: 0,
        adapter_key: 'gene',
        display_label: 'Candidate 1',
        unresolved_ambiguities: [],
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
              metadata: {},
            },
          ],
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

function buildSavedWorkspaceResponse() {
  const workspace = buildWorkspace('in_progress')
  const savedCandidate = {
    ...workspace.candidates[0],
    draft: {
      ...workspace.candidates[0].draft,
      version: 2,
      last_saved_at: '2026-03-20T12:05:00Z',
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

const AUTOSAVE_SETTLE_MS = 50

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
        children,
      },
    )
  }
}

describe('useAutosave', () => {
  beforeEach(() => {
    serviceMocks.autosaveCurationCandidateDraft.mockReset()
    serviceMocks.updateCurationSession.mockReset()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('debounces autosave writes and marks a new session in progress on first edit', async () => {
    serviceMocks.updateCurationSession.mockResolvedValue({
      session: {
        ...buildWorkspace('in_progress').session,
        current_candidate_id: 'candidate-1',
      },
      action_log_entry: null,
    })
    serviceMocks.autosaveCurationCandidateDraft.mockResolvedValue(
      buildSavedWorkspaceResponse(),
    )

    const { result } = renderHook(
      () => ({
        autosave: useAutosave({ debounceMs: 10 }),
        context: useCurationWorkspaceContext(),
      }),
      {
        wrapper: createWrapper(buildWorkspace('new')),
      },
    )

    act(() => {
      result.current.autosave.queueFieldChange({
        field_key: 'gene_symbol',
        value: 'BRCA2',
      })
    })

    expect(result.current.autosave.dirtyFieldKeys).toEqual(['gene_symbol'])
    expect(result.current.context.activeCandidate?.draft.fields[0].value).toBe('BRCA2')
    expect(serviceMocks.updateCurationSession).toHaveBeenCalledWith({
      session_id: 'session-1',
      status: 'in_progress',
      current_candidate_id: 'candidate-1',
    })

    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, AUTOSAVE_SETTLE_MS))
    })
    expect(serviceMocks.autosaveCurationCandidateDraft).toHaveBeenCalledTimes(1)

    const [request] = serviceMocks.autosaveCurationCandidateDraft.mock.calls[0]
    expect(request).toMatchObject({
      session_id: 'session-1',
      candidate_id: 'candidate-1',
      draft_id: 'draft-1',
      expected_version: 1,
      autosave: true,
      field_changes: [
        {
          field_key: 'gene_symbol',
          value: 'BRCA2',
        },
      ],
    })

    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, AUTOSAVE_SETTLE_MS))
    })
    expect(result.current.context.activeCandidate?.draft.version).toBe(2)
    expect(result.current.autosave.isDirty).toBe(false)
    expect(result.current.autosave.warning).toBeNull()
  })

  it('flushes pending autosave work during unmount', async () => {
    serviceMocks.autosaveCurationCandidateDraft.mockResolvedValue(
      buildSavedWorkspaceResponse(),
    )

    const { result, unmount } = renderHook(
      () => useAutosave({ debounceMs: 60_000 }),
      {
        wrapper: createWrapper(buildWorkspace()),
      },
    )

    act(() => {
      result.current.queueFieldChange({
        field_key: 'gene_symbol',
        value: 'BRCA2',
      })
    })

    await act(async () => {
      unmount()
      await new Promise((resolve) => window.setTimeout(resolve, AUTOSAVE_SETTLE_MS))
    })
    expect(serviceMocks.autosaveCurationCandidateDraft).toHaveBeenCalledTimes(1)
  })

  it('retries a failed autosave request once before succeeding', async () => {
    serviceMocks.autosaveCurationCandidateDraft
      .mockRejectedValueOnce(new Error('temporary outage'))
      .mockResolvedValueOnce(buildSavedWorkspaceResponse())

    const { result } = renderHook(
      () => useAutosave({ debounceMs: 10 }),
      {
        wrapper: createWrapper(buildWorkspace()),
      },
    )

    act(() => {
      result.current.queueFieldChange({
        field_key: 'gene_symbol',
        value: 'BRCA2',
      })
    })

    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, AUTOSAVE_SETTLE_MS))
    })
    expect(serviceMocks.autosaveCurationCandidateDraft).toHaveBeenCalledTimes(2)
    expect(result.current.warning).toBeNull()
  })

  it('surfaces a non-blocking warning after the retry also fails', async () => {
    serviceMocks.autosaveCurationCandidateDraft
      .mockRejectedValueOnce(new Error('still unavailable'))
      .mockRejectedValueOnce(new Error('still unavailable'))
      .mockResolvedValue(buildSavedWorkspaceResponse())

    const { result } = renderHook(
      () => useAutosave({ debounceMs: 10 }),
      {
        wrapper: createWrapper(buildWorkspace()),
      },
    )

    act(() => {
      result.current.queueFieldChange({
        field_key: 'gene_symbol',
        value: 'BRCA2',
      })
    })

    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, AUTOSAVE_SETTLE_MS))
    })
    expect(serviceMocks.autosaveCurationCandidateDraft.mock.calls.length).toBeGreaterThanOrEqual(2)
    expect(result.current.warning).toBe(
      'Autosave could not reach the server. Your draft changes remain local and can be retried.',
    )
    expect(result.current.isDirty).toBe(true)
  })
})
