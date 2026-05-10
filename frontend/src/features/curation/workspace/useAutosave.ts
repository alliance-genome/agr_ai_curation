import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type {
  CurationCandidate,
  CurationDraftField,
  CurationDraftFieldChange,
  CurationEnvelopeFieldPatchRequest,
} from '@/features/curation/types'
import {
  autosaveCurationCandidateDraft,
  patchCurationEnvelopeField,
  updateCurationSession,
} from '@/features/curation/services/curationWorkspaceService'
import { useCurationWorkspaceContext } from './CurationWorkspaceContext'
import {
  applyDraftFieldChangesToWorkspace,
  mergeEnvelopeFieldPatchIntoWorkspace,
  mergeSavedDraftIntoWorkspace,
  replaceWorkspaceSession,
  resolveEnvelopeFieldPath,
  updateWorkspaceActiveCandidate,
} from './workspaceState'

const DEFAULT_AUTOSAVE_DEBOUNCE_MS = 2_500

interface PendingDraftAutosave {
  sessionId: string
  candidateId: string
  draftId: string
  expectedVersion: number | null
  fieldChanges: Map<string, CurationDraftFieldChange>
}

interface PendingEnvelopeFieldPatch {
  candidateId: string
  envelopeId: string
  objectId: string
  fieldKey: string
  fieldPath: string
  expectedRevision: number
  before: unknown
  value: unknown
}

interface PendingEnvelopeAutosave {
  sessionId: string
  candidateId: string
  envelopeId: string
  objectId: string
  expectedRevision: number
  fieldPatches: Map<string, PendingEnvelopeFieldPatch>
}

interface FlushOptions {
  keepalive?: boolean
  updateState?: boolean
}

export interface UseAutosaveOptions {
  debounceMs?: number
}

export interface UseAutosaveReturn {
  debounceMs: number
  dirtyFieldKeys: string[]
  isDirty: boolean
  isSaving: boolean
  warning: string | null
  queueFieldChange: (fieldChange: CurationDraftFieldChange) => void
  queueFieldChanges: (fieldChanges: CurationDraftFieldChange[]) => void
  flush: () => Promise<boolean>
  clearWarning: () => void
}

function mergePendingFieldChanges(
  pendingDraft: PendingDraftAutosave,
  fieldChanges: CurationDraftFieldChange[],
): PendingDraftAutosave {
  const nextFieldChanges = new Map(pendingDraft.fieldChanges)
  for (const fieldChange of fieldChanges) {
    nextFieldChanges.set(fieldChange.field_key, fieldChange)
  }

  return {
    ...pendingDraft,
    fieldChanges: nextFieldChanges,
  }
}

function upsertPendingDraft(
  pendingDrafts: Map<string, PendingDraftAutosave>,
  pendingDraft: PendingDraftAutosave,
): void {
  pendingDrafts.set(pendingDraft.candidateId, pendingDraft)
}

function hasEnvelopeProjection(candidate: CurationCandidate | null): boolean {
  return Boolean(candidate?.projection_ref)
}

function findCandidateField(
  candidate: CurationCandidate,
  fieldKey: string,
): CurationDraftField | null {
  return candidate.draft.fields.find((field) => field.field_key === fieldKey) ?? null
}

function mergePendingEnvelopePatches(
  pendingEnvelope: PendingEnvelopeAutosave,
  fieldPatches: PendingEnvelopeFieldPatch[],
): PendingEnvelopeAutosave {
  const nextFieldPatches = new Map(pendingEnvelope.fieldPatches)

  for (const fieldPatch of fieldPatches) {
    const existingPatch = nextFieldPatches.get(fieldPatch.fieldPath)
    nextFieldPatches.set(
      fieldPatch.fieldPath,
      existingPatch
        ? {
            ...fieldPatch,
            expectedRevision: existingPatch.expectedRevision,
            before: existingPatch.before,
          }
        : fieldPatch,
    )
  }

  return {
    ...pendingEnvelope,
    fieldPatches: nextFieldPatches,
  }
}

function upsertPendingEnvelope(
  pendingEnvelopes: Map<string, PendingEnvelopeAutosave>,
  pendingEnvelope: PendingEnvelopeAutosave,
): void {
  pendingEnvelopes.set(pendingEnvelope.candidateId, pendingEnvelope)
}

function updatePendingEnvelopeRevision(
  pendingEnvelope: PendingEnvelopeAutosave,
  expectedRevision: number,
): PendingEnvelopeAutosave {
  return {
    ...pendingEnvelope,
    expectedRevision,
    fieldPatches: new Map(
      Array.from(pendingEnvelope.fieldPatches.entries()).map(([fieldPath, fieldPatch]) => [
        fieldPath,
        {
          ...fieldPatch,
          expectedRevision,
        },
      ]),
    ),
  }
}

function buildEnvelopeFieldPatch(args: {
  candidate: CurationCandidate
  fieldChange: CurationDraftFieldChange
  expectedRevision: number
}): PendingEnvelopeFieldPatch | null {
  const projectionRef = args.candidate.projection_ref
  if (!projectionRef) {
    return null
  }

  const field = findCandidateField(args.candidate, args.fieldChange.field_key)
  if (!field) {
    return null
  }

  const fieldPath = resolveEnvelopeFieldPath(field)
  const value = args.fieldChange.revert_to_seed
    ? field.seed_value ?? null
    : args.fieldChange.value ?? null

  return {
    candidateId: args.candidate.candidate_id,
    envelopeId: projectionRef.envelope_id,
    objectId: projectionRef.object_id,
    fieldKey: field.field_key,
    fieldPath,
    expectedRevision: args.expectedRevision,
    before: field.value ?? null,
    value,
  }
}

export function useAutosave(
  options: UseAutosaveOptions = {},
): UseAutosaveReturn {
  const debounceMs = options.debounceMs ?? DEFAULT_AUTOSAVE_DEBOUNCE_MS
  const { activeCandidate, activeCandidateId, session, setWorkspace, workspace } =
    useCurationWorkspaceContext()
  const [isSaving, setIsSaving] = useState(false)
  const [warning, setWarning] = useState<string | null>(null)
  const timerRef = useRef<number | null>(null)
  const scheduledCandidateIdRef = useRef<string | null>(null)
  const pendingDraftsRef = useRef<Map<string, PendingDraftAutosave>>(new Map())
  const inFlightDraftsRef = useRef<Map<string, PendingDraftAutosave>>(new Map())
  const pendingEnvelopesRef = useRef<Map<string, PendingEnvelopeAutosave>>(new Map())
  const inFlightEnvelopesRef = useRef<Map<string, PendingEnvelopeAutosave>>(new Map())
  const draftVersionsRef = useRef<Map<string, number | null>>(new Map())
  const envelopeRevisionsRef = useRef<Map<string, number>>(new Map())
  const previousActiveCandidateIdRef = useRef<string | null>(activeCandidateId)
  const mountedRef = useRef(true)
  const saveSequenceRef = useRef<Promise<boolean>>(Promise.resolve(true))
  const inProgressRequestRef = useRef<Promise<boolean> | null>(null)
  const pauseInFlightRef = useRef(false)
  const sessionStatusRef = useRef(session.status)
  const flushAllPendingChangesRef = useRef<
    ((options?: FlushOptions) => Promise<boolean>) | null
  >(null)
  const pauseSessionRef = useRef<((options?: FlushOptions) => Promise<boolean>) | null>(null)

  useEffect(() => {
    draftVersionsRef.current = new Map(
      workspace.candidates.map((candidate) => [candidate.candidate_id, candidate.draft.version]),
    )
    envelopeRevisionsRef.current = new Map(
      workspace.candidates
        .map((candidate) => candidate.projection_ref)
        .filter((projectionRef): projectionRef is NonNullable<typeof projectionRef> =>
          projectionRef !== null && projectionRef !== undefined)
        .map((projectionRef) => [
          projectionRef.envelope_id,
          projectionRef.envelope_revision,
        ]),
    )
  }, [workspace.candidates])

  useEffect(() => {
    sessionStatusRef.current = session.status
  }, [session.status])

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
      scheduledCandidateIdRef.current = null
    }
  }, [])

  const dirtyFieldKeys = useMemo(
    () =>
      activeCandidate?.draft.fields
        .filter((field) => field.dirty)
        .map((field) => field.field_key) ?? [],
    [activeCandidate],
  )

  const flushPendingCandidate = useCallback(
    async (
      candidateId: string,
      options?: FlushOptions,
    ): Promise<boolean> => {
      const pendingDraft = pendingDraftsRef.current.get(candidateId)
      if (!pendingDraft || pendingDraft.fieldChanges.size === 0) {
        return true
      }

      pendingDraftsRef.current.delete(candidateId)
      inFlightDraftsRef.current.set(candidateId, pendingDraft)
      if (scheduledCandidateIdRef.current === candidateId) {
        clearTimer()
      }

      if (options?.updateState !== false && mountedRef.current) {
        setIsSaving(true)
      }

      const request = {
        session_id: pendingDraft.sessionId,
        candidate_id: pendingDraft.candidateId,
        draft_id: pendingDraft.draftId,
        expected_version: pendingDraft.expectedVersion,
        field_changes: Array.from(pendingDraft.fieldChanges.values()),
        autosave: true,
      }

      try {
        let response

        try {
          response = await autosaveCurationCandidateDraft(request, {
            keepalive: options?.keepalive,
          })
        } catch {
          response = await autosaveCurationCandidateDraft(request, {
            keepalive: options?.keepalive,
          })
        }

        const nextPendingDraft = pendingDraftsRef.current.get(candidateId)
        if (options?.updateState !== false && mountedRef.current) {
          setWorkspace((currentWorkspace) => {
            const mergedWorkspace = mergeSavedDraftIntoWorkspace(currentWorkspace, response)
            if (!nextPendingDraft) {
              return mergedWorkspace
            }

            return applyDraftFieldChangesToWorkspace(
              mergedWorkspace,
              candidateId,
              Array.from(nextPendingDraft.fieldChanges.values()),
            )
          })
          setWarning(null)
        }

        draftVersionsRef.current.set(candidateId, response.draft.version)
        if (nextPendingDraft) {
          upsertPendingDraft(pendingDraftsRef.current, {
            ...nextPendingDraft,
            draftId: response.draft.draft_id,
            expectedVersion: response.draft.version,
          })
        }

        return true
      } catch {
        const nextPendingDraft = pendingDraftsRef.current.get(candidateId)
        if (nextPendingDraft) {
          upsertPendingDraft(
            pendingDraftsRef.current,
            mergePendingFieldChanges(
              {
                ...pendingDraft,
                draftId: nextPendingDraft.draftId,
              },
              Array.from(nextPendingDraft.fieldChanges.values()),
            ),
          )
        } else {
          upsertPendingDraft(pendingDraftsRef.current, pendingDraft)
        }

        if (options?.updateState !== false && mountedRef.current) {
          setWarning(
            'Autosave could not reach the server. Your draft changes remain local and can be retried.',
          )
        }

        return false
      } finally {
        inFlightDraftsRef.current.delete(candidateId)
        if (options?.updateState !== false && mountedRef.current) {
          setIsSaving(false)
        }
      }
    },
    [clearTimer, setWorkspace],
  )

  const flushPendingEnvelopeCandidate = useCallback(
    async (
      candidateId: string,
      options?: FlushOptions,
    ): Promise<boolean> => {
      const pendingEnvelope = pendingEnvelopesRef.current.get(candidateId)
      if (!pendingEnvelope || pendingEnvelope.fieldPatches.size === 0) {
        return true
      }

      pendingEnvelopesRef.current.delete(candidateId)
      inFlightEnvelopesRef.current.set(candidateId, pendingEnvelope)
      if (scheduledCandidateIdRef.current === candidateId) {
        clearTimer()
      }

      if (options?.updateState !== false && mountedRef.current) {
        setIsSaving(true)
      }

      const fieldPatches = Array.from(pendingEnvelope.fieldPatches.values())
      let remainingFieldPatches = fieldPatches
      let latestRevision =
        envelopeRevisionsRef.current.get(pendingEnvelope.envelopeId)
        ?? pendingEnvelope.expectedRevision

      try {
        for (let index = 0; index < fieldPatches.length; index += 1) {
          const fieldPatch = fieldPatches[index]
          if (!fieldPatch) {
            continue
          }
          remainingFieldPatches = fieldPatches.slice(index)

          const request: CurationEnvelopeFieldPatchRequest = {
            session_id: pendingEnvelope.sessionId,
            envelope_id: fieldPatch.envelopeId,
            expected_revision: latestRevision,
            object_id: fieldPatch.objectId,
            field_path: fieldPatch.fieldPath,
            operation: 'replace',
            before: fieldPatch.before ?? null,
            value: fieldPatch.value ?? null,
          }

          const response = await patchCurationEnvelopeField(request, {
            keepalive: options?.keepalive,
          })
          latestRevision = response.envelope_revision
          remainingFieldPatches = fieldPatches.slice(index + 1)
          envelopeRevisionsRef.current.set(response.envelope_id, response.envelope_revision)

          if (options?.updateState !== false && mountedRef.current) {
            setWorkspace((currentWorkspace) =>
              mergeEnvelopeFieldPatchIntoWorkspace(currentWorkspace, response),
            )
          }
        }

        const nextPendingEnvelope = pendingEnvelopesRef.current.get(candidateId)
        if (nextPendingEnvelope) {
          upsertPendingEnvelope(
            pendingEnvelopesRef.current,
            updatePendingEnvelopeRevision(nextPendingEnvelope, latestRevision),
          )
        }

        if (options?.updateState !== false && mountedRef.current) {
          setWarning(null)
        }

        return true
      } catch {
        const nextPendingEnvelope = pendingEnvelopesRef.current.get(candidateId)
        const envelopeToRequeue = updatePendingEnvelopeRevision(
          {
            ...pendingEnvelope,
            fieldPatches: new Map(
              remainingFieldPatches.map((fieldPatch) => [fieldPatch.fieldPath, fieldPatch]),
            ),
          },
          latestRevision,
        )

        if (nextPendingEnvelope) {
          upsertPendingEnvelope(
            pendingEnvelopesRef.current,
            mergePendingEnvelopePatches(
              envelopeToRequeue,
              Array.from(nextPendingEnvelope.fieldPatches.values()),
            ),
          )
        } else {
          upsertPendingEnvelope(pendingEnvelopesRef.current, envelopeToRequeue)
        }

        if (options?.updateState !== false && mountedRef.current) {
          setWarning(
            'Autosave could not patch the envelope field. Your edits remain local and can be retried.',
          )
        }

        return false
      } finally {
        inFlightEnvelopesRef.current.delete(candidateId)
        if (options?.updateState !== false && mountedRef.current) {
          setIsSaving(false)
        }
      }
    },
    [clearTimer, setWorkspace],
  )

  const flushPendingChanges = useCallback(
    async (
      candidateId?: string,
      options?: FlushOptions,
    ): Promise<boolean> => {
      const targetCandidateId =
        candidateId ?? scheduledCandidateIdRef.current ?? activeCandidateId

      if (!targetCandidateId) {
        return true
      }

      const nextSave = saveSequenceRef.current.then(async () => {
        const draftSaved = await flushPendingCandidate(targetCandidateId, options)
        const envelopeSaved = await flushPendingEnvelopeCandidate(targetCandidateId, options)
        return draftSaved && envelopeSaved
      })
      saveSequenceRef.current = nextSave.catch(() => false)
      return nextSave
    },
    [activeCandidateId, flushPendingCandidate, flushPendingEnvelopeCandidate],
  )

  const flushAllPendingChanges = useCallback(
    async (options?: FlushOptions): Promise<boolean> => {
      clearTimer()

      let allSucceeded = true
      for (const candidateId of pendingDraftsRef.current.keys()) {
        const candidateSaved = await flushPendingChanges(candidateId, options)
        allSucceeded = candidateSaved && allSucceeded
      }
      for (const candidateId of pendingEnvelopesRef.current.keys()) {
        const candidateSaved = await flushPendingChanges(candidateId, options)
        allSucceeded = candidateSaved && allSucceeded
      }

      return allSucceeded
    },
    [clearTimer, flushPendingChanges],
  )

  const ensureSessionInProgress = useCallback(async (): Promise<boolean> => {
    if (session.status !== 'new') {
      return true
    }

    if (inProgressRequestRef.current) {
      return inProgressRequestRef.current
    }

    inProgressRequestRef.current = updateCurationSession({
      session_id: session.session_id,
      status: 'in_progress',
      current_candidate_id: activeCandidateId,
    })
      .then((response) => {
        sessionStatusRef.current = response.session.status
        if (mountedRef.current) {
          setWorkspace((currentWorkspace) =>
            updateWorkspaceActiveCandidate(
              replaceWorkspaceSession(currentWorkspace, response.session),
              response.session.current_candidate_id ?? activeCandidateId ?? null,
            ),
          )
        }

        return true
      })
      .catch(() => {
        if (mountedRef.current) {
          setWarning('Unable to mark this session as in progress yet.')
        }

        return false
      })
      .finally(() => {
        inProgressRequestRef.current = null
      })

    return inProgressRequestRef.current
  }, [activeCandidateId, session.session_id, session.status, setWorkspace])

  const pauseSession = useCallback(
    async (options?: FlushOptions): Promise<boolean> => {
      if (pauseInFlightRef.current) {
        return true
      }

      pauseInFlightRef.current = true

      try {
        if (sessionStatusRef.current !== 'in_progress' && inProgressRequestRef.current) {
          const advancedToInProgress = await inProgressRequestRef.current
          if (!advancedToInProgress) {
            return true
          }
        }

        if (sessionStatusRef.current !== 'in_progress') {
          return true
        }

        const response = await updateCurationSession(
          {
            session_id: workspace.session.session_id,
            status: 'paused',
            current_candidate_id: activeCandidateId,
          },
          { keepalive: options?.keepalive },
        )

        if (options?.updateState !== false && mountedRef.current) {
          setWorkspace((currentWorkspace) =>
            updateWorkspaceActiveCandidate(
              replaceWorkspaceSession(currentWorkspace, response.session),
              response.session.current_candidate_id ?? activeCandidateId ?? null,
            ),
          )
        }

        sessionStatusRef.current = response.session.status

        return true
      } catch {
        if (options?.updateState !== false && mountedRef.current) {
          setWarning('Unable to pause this session before leaving.')
        }

        return false
      } finally {
        pauseInFlightRef.current = false
      }
    },
    [activeCandidateId, setWorkspace, workspace.session.session_id],
  )

  const scheduleAutosave = useCallback(
    (candidateId: string) => {
      clearTimer()
      scheduledCandidateIdRef.current = candidateId
      timerRef.current = window.setTimeout(() => {
        void flushPendingChanges(candidateId)
      }, debounceMs)
    },
    [clearTimer, debounceMs, flushPendingChanges],
  )

  const queueFieldChanges = useCallback(
    (fieldChanges: CurationDraftFieldChange[]) => {
      if (!activeCandidate || fieldChanges.length === 0) {
        return
      }

      void ensureSessionInProgress()
      setWarning(null)
      setWorkspace((currentWorkspace) =>
        applyDraftFieldChangesToWorkspace(
          currentWorkspace,
          activeCandidate.candidate_id,
          fieldChanges,
        ),
      )

      const candidateId = activeCandidate.candidate_id
      const projectionRef = activeCandidate.projection_ref

      if (hasEnvelopeProjection(activeCandidate) && projectionRef) {
        const existingPendingEnvelope = pendingEnvelopesRef.current.get(candidateId)
        const inFlightEnvelope = inFlightEnvelopesRef.current.get(candidateId)
        const knownEnvelopeRevision =
          envelopeRevisionsRef.current.get(projectionRef.envelope_id)
          ?? projectionRef.envelope_revision
        const expectedRevision =
          existingPendingEnvelope?.expectedRevision
          ?? inFlightEnvelope?.expectedRevision
          ?? knownEnvelopeRevision
        const fieldPatches = fieldChanges
          .map((fieldChange) =>
            buildEnvelopeFieldPatch({
              candidate: activeCandidate,
              fieldChange,
              expectedRevision,
            }))
          .filter((fieldPatch): fieldPatch is PendingEnvelopeFieldPatch =>
            fieldPatch !== null)

        if (fieldPatches.length > 0) {
          const nextPendingEnvelope = existingPendingEnvelope ?? {
            sessionId: session.session_id,
            candidateId,
            envelopeId: projectionRef.envelope_id,
            objectId: projectionRef.object_id,
            expectedRevision,
            fieldPatches: new Map<string, PendingEnvelopeFieldPatch>(),
          }

          upsertPendingEnvelope(
            pendingEnvelopesRef.current,
            mergePendingEnvelopePatches(nextPendingEnvelope, fieldPatches),
          )
        }

        scheduleAutosave(candidateId)
        return
      }

      const existingPendingDraft = pendingDraftsRef.current.get(candidateId)
      const inFlightDraft = inFlightDraftsRef.current.get(candidateId)
      const knownDraftVersion =
        draftVersionsRef.current.get(candidateId) ?? activeCandidate.draft.version

      const nextPendingDraft = existingPendingDraft ?? {
        sessionId: session.session_id,
        candidateId,
        draftId: activeCandidate.draft.draft_id,
        expectedVersion: inFlightDraft?.expectedVersion ?? knownDraftVersion,
        fieldChanges: new Map<string, CurationDraftFieldChange>(),
      }

      upsertPendingDraft(
        pendingDraftsRef.current,
        mergePendingFieldChanges(nextPendingDraft, fieldChanges),
      )

      scheduleAutosave(candidateId)
    },
    [
      activeCandidate,
      ensureSessionInProgress,
      scheduleAutosave,
      session.session_id,
      setWorkspace,
    ],
  )

  const queueFieldChange = useCallback(
    (fieldChange: CurationDraftFieldChange) => {
      queueFieldChanges([fieldChange])
    },
    [queueFieldChanges],
  )

  const flush = useCallback(async () => {
    return flushAllPendingChanges()
  }, [flushAllPendingChanges])

  const clearWarning = useCallback(() => {
    setWarning(null)
  }, [])

  useEffect(() => {
    flushAllPendingChangesRef.current = flushAllPendingChanges
  }, [flushAllPendingChanges])

  useEffect(() => {
    pauseSessionRef.current = pauseSession
  }, [pauseSession])

  useEffect(() => {
    const previousActiveCandidateId = previousActiveCandidateIdRef.current
    previousActiveCandidateIdRef.current = activeCandidateId

    if (
      previousActiveCandidateId &&
      previousActiveCandidateId !== activeCandidateId &&
      (
        pendingDraftsRef.current.has(previousActiveCandidateId) ||
        pendingEnvelopesRef.current.has(previousActiveCandidateId)
      )
    ) {
      void flushPendingChanges(previousActiveCandidateId)
    }
  }, [activeCandidateId, flushPendingChanges])

  useEffect(() => {
    const handleBeforeUnload = () => {
      void flushAllPendingChangesRef.current?.({ keepalive: true, updateState: false })
      void pauseSessionRef.current?.({ keepalive: true, updateState: false })
    }

    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [])

  useEffect(() => {
    mountedRef.current = true

    return () => {
      mountedRef.current = false
      void flushAllPendingChangesRef.current?.({ keepalive: true, updateState: false })
      void pauseSessionRef.current?.({ keepalive: true, updateState: false })
    }
  }, [])

  return {
    debounceMs,
    dirtyFieldKeys,
    isDirty: dirtyFieldKeys.length > 0,
    isSaving,
    warning,
    queueFieldChange,
    queueFieldChanges,
    flush,
    clearWarning,
  }
}
