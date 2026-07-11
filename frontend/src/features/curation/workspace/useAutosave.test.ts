import { createElement, type ReactNode, useCallback, useMemo, useState } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
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
  fetchCurationWorkspace: vi.fn(),
  patchCurationEnvelopeField: vi.fn(),
  updateCurationSession: vi.fn(),
}))

vi.mock('@/features/curation/services/curationWorkspaceService', () => ({
  autosaveCurationCandidateDraft: serviceMocks.autosaveCurationCandidateDraft,
  fetchCurationWorkspace: serviceMocks.fetchCurationWorkspace,
  patchCurationEnvelopeField: serviceMocks.patchCurationEnvelopeField,
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

function buildSavedWorkspaceResponse({
  status = 'in_progress',
  value = 'BRCA2',
  version = 2,
}: {
  status?: CurationReviewSession['status']
  value?: string
  version?: number
} = {}) {
  const lastSavedMinute = String(version).padStart(2, '0')
  const workspace = buildWorkspace('in_progress')
  const savedCandidate = {
    ...workspace.candidates[0],
    draft: {
      ...workspace.candidates[0].draft,
      version,
      last_saved_at: `2026-03-20T12:${lastSavedMinute}:00Z`,
      fields: [
        {
          ...workspace.candidates[0].draft.fields[0],
          value,
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
    session: {
      ...workspace.session,
      status,
    },
  }
}

function buildEnvelopeWorkspace({
  includeSecondaryField = false,
}: {
  includeSecondaryField?: boolean
} = {}): CurationWorkspace {
  const workspace = buildWorkspace()
  const baseField = workspace.candidates[0].draft.fields[0]
  const envelopeCandidate = {
    ...workspace.candidates[0],
    projection_ref: {
      envelope_id: 'envelope-1',
      object_id: 'object-1',
      envelope_revision: 7,
    },
    draft: {
      ...workspace.candidates[0].draft,
      fields: [
        {
          ...baseField,
          metadata: {
            source_field_path: 'gene.symbol',
          },
        },
        ...(includeSecondaryField
          ? [
              {
                ...baseField,
                field_key: 'gene_name',
                label: 'Gene name',
                value: 'breast cancer 1',
                seed_value: 'breast cancer 1',
                order: 1,
                metadata: {
                  source_field_path: 'gene.name',
                },
              },
            ]
          : []),
      ],
    },
  }

  return {
    ...workspace,
    candidates: [envelopeCandidate],
  }
}

function buildEnvelopePatchResponse({
  workspace,
  value,
  before,
  previousRevision,
  envelopeRevision,
  fieldKey = 'gene_symbol',
  fieldPath = 'gene.symbol',
}: {
  workspace: CurationWorkspace
  value: string
  before: string
  previousRevision: number
  envelopeRevision: number
  fieldKey?: string
  fieldPath?: string
}) {
  const candidate = workspace.candidates[0]
  const projectionRef = candidate.projection_ref ?? {
    envelope_id: 'envelope-1',
    object_id: 'object-1',
    envelope_revision: previousRevision,
  }
  const patchedCandidate = {
    ...candidate,
    projection_ref: {
      ...projectionRef,
      envelope_revision: envelopeRevision,
    },
    draft: {
      ...candidate.draft,
      version: envelopeRevision,
      fields: candidate.draft.fields.map((field) => {
        const sourceFieldPath = field.metadata.source_field_path
        const resolvedFieldPath =
          typeof sourceFieldPath === 'string' && sourceFieldPath.trim().length > 0
            ? sourceFieldPath.trim()
            : field.field_key

        return field.field_key === fieldKey || resolvedFieldPath === fieldPath
          ? {
              ...field,
              value,
              seed_value: value,
              dirty: false,
              stale_validation: true,
            }
          : field
      }),
    },
  }

  return {
    accepted: true,
    envelope_id: projectionRef.envelope_id,
    previous_revision: previousRevision,
    envelope_revision: envelopeRevision,
    object_id: projectionRef.object_id,
    object_type: 'gene',
    field_path: fieldPath,
    operation: 'replace',
    before,
    value,
    projection_ref: patchedCandidate.projection_ref,
    candidate: patchedCandidate,
    session: workspace.session,
    action_log_entry: null,
    history_event_ids: [`history-${envelopeRevision}`],
    projection_candidate_ids: [candidate.candidate_id],
  }
}

function createDeferred<T>() {
  let resolvePromise!: (value: T) => void
  let rejectPromise!: (reason?: unknown) => void

  const promise = new Promise<T>((resolve, reject) => {
    resolvePromise = resolve
    rejectPromise = reject
  })

  return {
    promise,
    resolve: resolvePromise,
    reject: rejectPromise,
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
    serviceMocks.fetchCurationWorkspace.mockReset()
    serviceMocks.patchCurationEnvelopeField.mockReset()
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

  it('patches envelope-backed fields by object id, field path, revision, and before value', async () => {
    const envelopeWorkspace = buildEnvelopeWorkspace()

    serviceMocks.patchCurationEnvelopeField.mockResolvedValue(
      buildEnvelopePatchResponse({
        workspace: envelopeWorkspace,
        value: 'BRCA2',
        before: 'BRCA1',
        previousRevision: 7,
        envelopeRevision: 8,
      }),
    )

    const { result } = renderHook(
      () => ({
        autosave: useAutosave({ debounceMs: 10 }),
        context: useCurationWorkspaceContext(),
      }),
      {
        wrapper: createWrapper(envelopeWorkspace),
      },
    )

    act(() => {
      result.current.autosave.queueFieldChange({
        field_key: 'gene_symbol',
        value: 'BRCA2',
      })
    })

    expect(result.current.context.activeCandidate?.draft.fields[0].value).toBe('BRCA2')

    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, AUTOSAVE_SETTLE_MS))
    })

    expect(serviceMocks.autosaveCurationCandidateDraft).not.toHaveBeenCalled()
    expect(serviceMocks.patchCurationEnvelopeField).toHaveBeenCalledWith({
      session_id: 'session-1',
      envelope_id: 'envelope-1',
      expected_revision: 7,
      object_id: 'object-1',
      field_path: 'gene.symbol',
      operation: 'replace',
      before: 'BRCA1',
      value: 'BRCA2',
    }, {
      keepalive: undefined,
    })

    await waitFor(() => {
      expect(result.current.context.activeCandidate?.projection_ref?.envelope_revision).toBe(8)
      expect(result.current.context.activeCandidate?.draft.fields[0]).toMatchObject({
        value: 'BRCA2',
        dirty: false,
        stale_validation: true,
      })
    })
  })

  it('keeps unsent envelope batch fields visible and dirty after an earlier field saves', async () => {
    const envelopeWorkspace = buildEnvelopeWorkspace({ includeSecondaryField: true })
    const firstEnvelopePatch = createDeferred<ReturnType<typeof buildEnvelopePatchResponse>>()
    const secondEnvelopePatch = createDeferred<ReturnType<typeof buildEnvelopePatchResponse>>()

    serviceMocks.patchCurationEnvelopeField
      .mockImplementationOnce(() => firstEnvelopePatch.promise)
      .mockImplementationOnce(() => secondEnvelopePatch.promise)

    const { result } = renderHook(
      () => ({
        autosave: useAutosave({ debounceMs: 10 }),
        context: useCurationWorkspaceContext(),
      }),
      {
        wrapper: createWrapper(envelopeWorkspace),
      },
    )

    act(() => {
      result.current.autosave.queueFieldChanges([
        {
          field_key: 'gene_symbol',
          value: 'BRCA2',
        },
        {
          field_key: 'gene_name',
          value: 'BRCA1 related',
        },
      ])
    })

    expect(result.current.context.activeCandidate?.draft.fields[0].value).toBe('BRCA2')
    expect(result.current.context.activeCandidate?.draft.fields[1].value).toBe('BRCA1 related')

    await waitFor(() => {
      expect(serviceMocks.patchCurationEnvelopeField).toHaveBeenCalledTimes(1)
    })
    expect(serviceMocks.patchCurationEnvelopeField.mock.calls[0]?.[0]).toMatchObject({
      expected_revision: 7,
      object_id: 'object-1',
      field_path: 'gene.symbol',
      before: 'BRCA1',
      value: 'BRCA2',
    })

    const firstPatchResponse = buildEnvelopePatchResponse({
      workspace: envelopeWorkspace,
      value: 'BRCA2',
      before: 'BRCA1',
      previousRevision: 7,
      envelopeRevision: 8,
    })

    await act(async () => {
      firstEnvelopePatch.resolve(firstPatchResponse)
      await firstEnvelopePatch.promise
    })

    await waitFor(() => {
      expect(result.current.context.activeCandidate?.projection_ref?.envelope_revision).toBe(8)
      expect(result.current.context.activeCandidate?.draft.fields[0]).toMatchObject({
        value: 'BRCA2',
        dirty: false,
      })
      expect(result.current.context.activeCandidate?.draft.fields[1]).toMatchObject({
        value: 'BRCA1 related',
        dirty: true,
      })
      expect(result.current.autosave.isDirty).toBe(true)
    })

    await waitFor(() => {
      expect(serviceMocks.patchCurationEnvelopeField).toHaveBeenCalledTimes(2)
    })
    expect(serviceMocks.patchCurationEnvelopeField.mock.calls[1]?.[0]).toMatchObject({
      expected_revision: 8,
      object_id: 'object-1',
      field_path: 'gene.name',
      before: 'breast cancer 1',
      value: 'BRCA1 related',
    })

    await act(async () => {
      secondEnvelopePatch.reject(new Error('validation conflict'))
      await secondEnvelopePatch.promise.catch(() => undefined)
    })

    await waitFor(() => {
      expect(result.current.context.activeCandidate?.draft.fields[1]).toMatchObject({
        value: 'BRCA1 related',
        dirty: true,
      })
      expect(result.current.autosave.warning).toBe(
        'Autosave could not patch the envelope field. Your edits remain local and can be retried.',
      )
    })

    serviceMocks.patchCurationEnvelopeField.mockResolvedValueOnce(
      buildEnvelopePatchResponse({
        workspace: {
          ...envelopeWorkspace,
          candidates: [firstPatchResponse.candidate],
        },
        fieldKey: 'gene_name',
        fieldPath: 'gene.name',
        value: 'BRCA1 related',
        before: 'breast cancer 1',
        previousRevision: 8,
        envelopeRevision: 9,
      }),
    )

    await act(async () => {
      await result.current.autosave.flush()
    })
  })

  it('keeps newer queued envelope edits dirty after an in-flight patch completes', async () => {
    const envelopeWorkspace = buildEnvelopeWorkspace()
    const firstEnvelopePatch = createDeferred<ReturnType<typeof buildEnvelopePatchResponse>>()
    const secondEnvelopePatch = createDeferred<ReturnType<typeof buildEnvelopePatchResponse>>()

    serviceMocks.patchCurationEnvelopeField
      .mockImplementationOnce(() => firstEnvelopePatch.promise)
      .mockImplementationOnce(() => secondEnvelopePatch.promise)

    const { result } = renderHook(
      () => ({
        autosave: useAutosave({ debounceMs: 10 }),
        context: useCurationWorkspaceContext(),
      }),
      {
        wrapper: createWrapper(envelopeWorkspace),
      },
    )

    act(() => {
      result.current.autosave.queueFieldChange({
        field_key: 'gene_symbol',
        value: 'BRCA2',
      })
    })

    await waitFor(() => {
      expect(serviceMocks.patchCurationEnvelopeField).toHaveBeenCalledTimes(1)
    })
    expect(serviceMocks.patchCurationEnvelopeField.mock.calls[0]?.[0]).toMatchObject({
      expected_revision: 7,
      object_id: 'object-1',
      field_path: 'gene.symbol',
      before: 'BRCA1',
      value: 'BRCA2',
    })

    act(() => {
      result.current.autosave.queueFieldChange({
        field_key: 'gene_symbol',
        value: 'BRCA3',
      })
    })

    expect(result.current.context.activeCandidate?.draft.fields[0].value).toBe('BRCA3')
    expect(result.current.autosave.isDirty).toBe(true)

    const firstPatchResponse = buildEnvelopePatchResponse({
      workspace: envelopeWorkspace,
      value: 'BRCA2',
      before: 'BRCA1',
      previousRevision: 7,
      envelopeRevision: 8,
    })

    await act(async () => {
      firstEnvelopePatch.resolve(firstPatchResponse)
      await firstEnvelopePatch.promise
    })

    await waitFor(() => {
      expect(result.current.context.activeCandidate?.projection_ref?.envelope_revision).toBe(8)
      expect(result.current.context.activeCandidate?.draft.fields[0].value).toBe('BRCA3')
      expect(result.current.autosave.isDirty).toBe(true)
    })

    await waitFor(() => {
      expect(serviceMocks.patchCurationEnvelopeField).toHaveBeenCalledTimes(2)
    })
    expect(serviceMocks.patchCurationEnvelopeField.mock.calls[1]?.[0]).toMatchObject({
      expected_revision: 8,
      object_id: 'object-1',
      field_path: 'gene.symbol',
      before: 'BRCA2',
      value: 'BRCA3',
    })

    await act(async () => {
      secondEnvelopePatch.resolve(
        buildEnvelopePatchResponse({
          workspace: {
            ...envelopeWorkspace,
            candidates: [firstPatchResponse.candidate],
          },
          value: 'BRCA3',
          before: 'BRCA2',
          previousRevision: 8,
          envelopeRevision: 9,
        }),
      )
      await secondEnvelopePatch.promise
    })

    await waitFor(() => {
      expect(result.current.context.activeCandidate?.projection_ref?.envelope_revision).toBe(9)
      expect(result.current.context.activeCandidate?.draft.fields[0]).toMatchObject({
        value: 'BRCA3',
        dirty: false,
        stale_validation: true,
      })
      expect(result.current.autosave.isDirty).toBe(false)
    })
  })

  it('keeps newer queued edits dirty and advances expected_version after an in-flight save completes', async () => {
    const firstAutosave = createDeferred<ReturnType<typeof buildSavedWorkspaceResponse>>()

    serviceMocks.autosaveCurationCandidateDraft
      .mockImplementationOnce(() => firstAutosave.promise)
      .mockResolvedValueOnce(buildSavedWorkspaceResponse({ value: 'BRCA3', version: 3 }))

    const { result } = renderHook(
      () => ({
        autosave: useAutosave({ debounceMs: 10 }),
        context: useCurationWorkspaceContext(),
      }),
      {
        wrapper: createWrapper(buildWorkspace()),
      },
    )

    act(() => {
      result.current.autosave.queueFieldChange({
        field_key: 'gene_symbol',
        value: 'BRCA2',
      })
    })

    await waitFor(() => {
      expect(serviceMocks.autosaveCurationCandidateDraft).toHaveBeenCalledTimes(1)
    })
    expect(serviceMocks.autosaveCurationCandidateDraft.mock.calls[0]?.[0]).toMatchObject({
      expected_version: 1,
      field_changes: [
        {
          field_key: 'gene_symbol',
          value: 'BRCA2',
        },
      ],
    })

    act(() => {
      result.current.autosave.queueFieldChange({
        field_key: 'gene_symbol',
        value: 'BRCA3',
      })
    })

    await act(async () => {
      firstAutosave.resolve(buildSavedWorkspaceResponse({ value: 'BRCA2', version: 2 }))
      await firstAutosave.promise
    })

    await waitFor(() => {
      expect(result.current.context.activeCandidate?.draft.fields[0].value).toBe('BRCA3')
      expect(result.current.autosave.isDirty).toBe(true)
    })

    await waitFor(() => {
      expect(serviceMocks.autosaveCurationCandidateDraft).toHaveBeenCalledTimes(2)
    })
    expect(serviceMocks.autosaveCurationCandidateDraft.mock.calls[1]?.[0]).toMatchObject({
      expected_version: 2,
      field_changes: [
        {
          field_key: 'gene_symbol',
          value: 'BRCA3',
        },
      ],
    })

    await waitFor(() => {
      expect(result.current.context.activeCandidate?.draft.version).toBe(3)
      expect(result.current.context.activeCandidate?.draft.fields[0].value).toBe('BRCA3')
      expect(result.current.autosave.isDirty).toBe(false)
    })
  })

  it('bounds an explicit draft flush and preserves failed work for the next pass', async () => {
    serviceMocks.autosaveCurationCandidateDraft.mockRejectedValue(
      new Error('persistent outage'),
    )

    const { result } = renderHook(
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

    let firstFlushSucceeded = true
    await act(async () => {
      firstFlushSucceeded = await result.current.flush()
    })

    expect(firstFlushSucceeded).toBe(false)
    expect(serviceMocks.autosaveCurationCandidateDraft).toHaveBeenCalledTimes(2)
    expect(result.current.isDirty).toBe(true)

    serviceMocks.autosaveCurationCandidateDraft.mockResolvedValueOnce(
      buildSavedWorkspaceResponse(),
    )
    await act(async () => {
      expect(await result.current.flush()).toBe(true)
    })
    expect(serviceMocks.autosaveCurationCandidateDraft).toHaveBeenCalledTimes(3)
  })

  it('bounds an explicit envelope flush and preserves failed work for the next pass', async () => {
    const envelopeWorkspace = buildEnvelopeWorkspace()
    serviceMocks.patchCurationEnvelopeField.mockRejectedValue(
      new Error('persistent outage'),
    )

    const { result } = renderHook(
      () => useAutosave({ debounceMs: 60_000 }),
      {
        wrapper: createWrapper(envelopeWorkspace),
      },
    )

    act(() => {
      result.current.queueFieldChange({
        field_key: 'gene_symbol',
        value: 'BRCA2',
      })
    })

    let firstFlushSucceeded = true
    await act(async () => {
      firstFlushSucceeded = await result.current.flush()
    })

    expect(firstFlushSucceeded).toBe(false)
    expect(serviceMocks.patchCurationEnvelopeField).toHaveBeenCalledTimes(1)
    expect(result.current.isDirty).toBe(true)

    serviceMocks.patchCurationEnvelopeField.mockResolvedValueOnce(
      buildEnvelopePatchResponse({
        workspace: envelopeWorkspace,
        value: 'BRCA2',
        before: 'BRCA1',
        previousRevision: 7,
        envelopeRevision: 8,
      }),
    )
    await act(async () => {
      expect(await result.current.flush()).toBe(true)
    })
    expect(serviceMocks.patchCurationEnvelopeField).toHaveBeenCalledTimes(2)
  })

  it('attempts mixed draft and envelope maps once per candidate in one flush pass', async () => {
    const draftWorkspace = buildWorkspace()
    const envelopeWorkspace = buildEnvelopeWorkspace()
    serviceMocks.autosaveCurationCandidateDraft.mockRejectedValue(
      new Error('persistent draft outage'),
    )
    serviceMocks.patchCurationEnvelopeField.mockRejectedValue(
      new Error('persistent envelope outage'),
    )

    const { result } = renderHook(
      () => ({
        autosave: useAutosave({ debounceMs: 60_000 }),
        context: useCurationWorkspaceContext(),
      }),
      {
        wrapper: createWrapper(draftWorkspace),
      },
    )

    act(() => {
      result.current.autosave.queueFieldChange({
        field_key: 'gene_symbol',
        value: 'BRCA2',
      })
      result.current.context.setWorkspace(envelopeWorkspace)
    })
    act(() => {
      result.current.autosave.queueFieldChange({
        field_key: 'gene_symbol',
        value: 'BRCA3',
      })
    })

    let firstFlushSucceeded = true
    await act(async () => {
      firstFlushSucceeded = await result.current.autosave.flush()
    })

    expect(firstFlushSucceeded).toBe(false)
    expect(serviceMocks.autosaveCurationCandidateDraft).toHaveBeenCalledTimes(2)
    expect(serviceMocks.patchCurationEnvelopeField).toHaveBeenCalledTimes(1)

    serviceMocks.autosaveCurationCandidateDraft.mockResolvedValueOnce(
      buildSavedWorkspaceResponse(),
    )
    serviceMocks.patchCurationEnvelopeField.mockResolvedValueOnce(
      buildEnvelopePatchResponse({
        workspace: envelopeWorkspace,
        value: 'BRCA3',
        before: 'BRCA1',
        previousRevision: 7,
        envelopeRevision: 8,
      }),
    )
    await act(async () => {
      expect(await result.current.autosave.flush()).toBe(true)
    })
    expect(serviceMocks.autosaveCurationCandidateDraft).toHaveBeenCalledTimes(3)
    expect(serviceMocks.patchCurationEnvelopeField).toHaveBeenCalledTimes(2)
  })

  it('leaves an edit queued during an explicit flush for a later pass', async () => {
    const firstAutosave = createDeferred<ReturnType<typeof buildSavedWorkspaceResponse>>()
    serviceMocks.autosaveCurationCandidateDraft
      .mockImplementationOnce(() => firstAutosave.promise)
      .mockResolvedValueOnce(buildSavedWorkspaceResponse({ value: 'BRCA3', version: 3 }))

    const { result } = renderHook(
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

    let firstFlush!: Promise<boolean>
    act(() => {
      firstFlush = result.current.flush()
    })
    await waitFor(() => {
      expect(serviceMocks.autosaveCurationCandidateDraft).toHaveBeenCalledTimes(1)
    })

    act(() => {
      result.current.queueFieldChange({
        field_key: 'gene_symbol',
        value: 'BRCA3',
      })
    })

    await act(async () => {
      firstAutosave.resolve(buildSavedWorkspaceResponse({ value: 'BRCA2', version: 2 }))
      expect(await firstFlush).toBe(true)
    })

    expect(serviceMocks.autosaveCurationCandidateDraft).toHaveBeenCalledTimes(1)
    expect(result.current.isDirty).toBe(true)
    expect(result.current.dirtyFieldKeys).toEqual(['gene_symbol'])

    await act(async () => {
      expect(await result.current.flush()).toBe(true)
    })
    expect(serviceMocks.autosaveCurationCandidateDraft).toHaveBeenCalledTimes(2)
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

  it('bounds a persistently failing unmount flush', async () => {
    serviceMocks.autosaveCurationCandidateDraft.mockRejectedValue(
      new Error('persistent outage'),
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

    act(() => {
      unmount()
    })
    await waitFor(() => {
      expect(serviceMocks.autosaveCurationCandidateDraft).toHaveBeenCalledTimes(2)
    })
    await new Promise((resolve) => window.setTimeout(resolve, AUTOSAVE_SETTLE_MS))
    expect(serviceMocks.autosaveCurationCandidateDraft).toHaveBeenCalledTimes(2)
  })

  it('pauses a new session on unmount after the in-progress transition settles', async () => {
    const inProgressRequest = createDeferred<{
      session: CurationReviewSession
      action_log_entry: null
    }>()

    serviceMocks.updateCurationSession
      .mockImplementationOnce(() => inProgressRequest.promise)
      .mockResolvedValueOnce({
        session: {
          ...buildWorkspace('paused').session,
          current_candidate_id: 'candidate-1',
        },
        action_log_entry: null,
      })
    serviceMocks.autosaveCurationCandidateDraft.mockResolvedValue(
      buildSavedWorkspaceResponse(),
    )

    const { result, unmount } = renderHook(
      () => useAutosave({ debounceMs: 60_000 }),
      {
        wrapper: createWrapper(buildWorkspace('new')),
      },
    )

    act(() => {
      result.current.queueFieldChange({
        field_key: 'gene_symbol',
        value: 'BRCA2',
      })
    })
    expect(serviceMocks.updateCurationSession).toHaveBeenCalledWith({
      session_id: 'session-1',
      status: 'in_progress',
      current_candidate_id: 'candidate-1',
    })

    act(() => {
      unmount()
    })
    expect(serviceMocks.updateCurationSession).toHaveBeenCalledTimes(1)

    await act(async () => {
      inProgressRequest.resolve({
        session: {
          ...buildWorkspace('in_progress').session,
          current_candidate_id: 'candidate-1',
        },
        action_log_entry: null,
      })
      await inProgressRequest.promise
    })

    await waitFor(() => {
      expect(serviceMocks.updateCurationSession).toHaveBeenCalledTimes(2)
    })
    expect(serviceMocks.updateCurationSession.mock.calls[1]).toEqual([
      {
        session_id: 'session-1',
        status: 'paused',
        current_candidate_id: 'candidate-1',
      },
      {
        keepalive: true,
      },
    ])
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

  it('refreshes a draft conflict and preserves a newer local edit for a fresh version', async () => {
    const authoritativeRefresh = createDeferred<CurationWorkspace>()
    const authoritativeWorkspace = buildWorkspace()
    authoritativeWorkspace.candidates[0] = {
      ...authoritativeWorkspace.candidates[0],
      draft: {
        ...authoritativeWorkspace.candidates[0].draft,
        version: 4,
        fields: authoritativeWorkspace.candidates[0].draft.fields.map((field) => ({
          ...field,
          value: 'TP53',
          seed_value: 'TP53',
        })),
      },
    }
    serviceMocks.autosaveCurationCandidateDraft.mockRejectedValueOnce({ status: 409 })
    serviceMocks.fetchCurationWorkspace.mockImplementationOnce(
      () => authoritativeRefresh.promise,
    )

    const { result } = renderHook(
      () => ({
        autosave: useAutosave({ debounceMs: 10 }),
        context: useCurationWorkspaceContext(),
      }),
      { wrapper: createWrapper(buildWorkspace()) },
    )

    act(() => {
      result.current.autosave.queueFieldChange({
        field_key: 'gene_symbol',
        value: 'BRCA2',
      })
    })
    await waitFor(() => {
      expect(serviceMocks.fetchCurationWorkspace).toHaveBeenCalledWith('session-1')
    })

    act(() => {
      result.current.autosave.queueFieldChange({
        field_key: 'gene_symbol',
        value: 'BRCA3',
      })
    })
    await act(async () => {
      authoritativeRefresh.resolve(authoritativeWorkspace)
      await authoritativeRefresh.promise
    })

    await waitFor(() => {
      expect(result.current.context.activeCandidate?.draft.version).toBe(4)
      expect(result.current.context.activeCandidate?.draft.fields[0]).toMatchObject({
        value: 'BRCA3',
        dirty: true,
      })
    })
    expect(serviceMocks.autosaveCurationCandidateDraft).toHaveBeenCalledTimes(1)
    expect(result.current.autosave.warning).toContain('rebased onto the latest version')

    serviceMocks.autosaveCurationCandidateDraft.mockResolvedValueOnce(
      buildSavedWorkspaceResponse({ value: 'BRCA3', version: 5 }),
    )
    await act(async () => {
      expect(await result.current.autosave.flush()).toBe(true)
    })
    expect(serviceMocks.autosaveCurationCandidateDraft).toHaveBeenCalledTimes(2)
    expect(serviceMocks.autosaveCurationCandidateDraft.mock.calls[1]?.[0]).toMatchObject({
      expected_version: 4,
      field_changes: [{ field_key: 'gene_symbol', value: 'BRCA3' }],
    })
  })

  it('blocks a stale draft resend while authoritative conflict refresh fails', async () => {
    serviceMocks.autosaveCurationCandidateDraft
      .mockRejectedValueOnce({ status: 409 })
      .mockResolvedValue(buildSavedWorkspaceResponse())
    serviceMocks.fetchCurationWorkspace.mockRejectedValue(new Error('refresh unavailable'))

    const { result } = renderHook(
      () => useAutosave({ debounceMs: 10 }),
      { wrapper: createWrapper(buildWorkspace()) },
    )

    act(() => {
      result.current.queueFieldChange({
        field_key: 'gene_symbol',
        value: 'BRCA2',
      })
    })
    await waitFor(() => {
      expect(result.current.warning).toContain('latest version could not be loaded')
    })

    await act(async () => {
      expect(await result.current.flush()).toBe(false)
    })
    expect(serviceMocks.fetchCurationWorkspace).toHaveBeenCalledTimes(2)
    expect(serviceMocks.autosaveCurationCandidateDraft).toHaveBeenCalledTimes(1)
    expect(result.current.isDirty).toBe(true)
  })

  it('refreshes an envelope conflict and preserves a newer local edit for a fresh revision', async () => {
    const initialWorkspace = buildEnvelopeWorkspace()
    const authoritativeRefresh = createDeferred<CurationWorkspace>()
    const authoritativeWorkspace = buildEnvelopeWorkspace()
    authoritativeWorkspace.candidates[0] = {
      ...authoritativeWorkspace.candidates[0],
      projection_ref: {
        ...authoritativeWorkspace.candidates[0].projection_ref!,
        envelope_revision: 9,
      },
      draft: {
        ...authoritativeWorkspace.candidates[0].draft,
        version: 9,
        fields: authoritativeWorkspace.candidates[0].draft.fields.map((field) => ({
          ...field,
          value: 'TP53',
          seed_value: 'TP53',
        })),
      },
    }
    serviceMocks.patchCurationEnvelopeField.mockRejectedValueOnce({ status: 409 })
    serviceMocks.fetchCurationWorkspace.mockImplementationOnce(
      () => authoritativeRefresh.promise,
    )

    const { result } = renderHook(
      () => ({
        autosave: useAutosave({ debounceMs: 10 }),
        context: useCurationWorkspaceContext(),
      }),
      { wrapper: createWrapper(initialWorkspace) },
    )

    act(() => {
      result.current.autosave.queueFieldChange({
        field_key: 'gene_symbol',
        value: 'BRCA2',
      })
    })
    await waitFor(() => {
      expect(serviceMocks.fetchCurationWorkspace).toHaveBeenCalledWith('session-1')
    })

    act(() => {
      result.current.autosave.queueFieldChange({
        field_key: 'gene_symbol',
        value: 'BRCA3',
      })
    })
    await act(async () => {
      authoritativeRefresh.resolve(authoritativeWorkspace)
      await authoritativeRefresh.promise
    })

    await waitFor(() => {
      expect(result.current.context.activeCandidate?.projection_ref?.envelope_revision).toBe(9)
      expect(result.current.context.activeCandidate?.draft.fields[0]).toMatchObject({
        value: 'BRCA3',
        dirty: true,
      })
    })
    expect(serviceMocks.patchCurationEnvelopeField).toHaveBeenCalledTimes(1)
    expect(result.current.autosave.warning).toContain('rebased onto the latest revision')

    serviceMocks.patchCurationEnvelopeField.mockResolvedValueOnce(
      buildEnvelopePatchResponse({
        workspace: authoritativeWorkspace,
        value: 'BRCA3',
        before: 'TP53',
        previousRevision: 9,
        envelopeRevision: 10,
      }),
    )
    await act(async () => {
      expect(await result.current.autosave.flush()).toBe(true)
    })
    expect(serviceMocks.patchCurationEnvelopeField).toHaveBeenCalledTimes(2)
    expect(serviceMocks.patchCurationEnvelopeField.mock.calls[1]?.[0]).toMatchObject({
      expected_revision: 9,
      before: 'TP53',
      value: 'BRCA3',
    })
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
    expect(serviceMocks.autosaveCurationCandidateDraft).toHaveBeenCalledTimes(2)
    expect(result.current.warning).toBe(
      'Autosave could not reach the server. Your draft changes remain local and can be retried.',
    )
    expect(result.current.isDirty).toBe(true)
  })
})
