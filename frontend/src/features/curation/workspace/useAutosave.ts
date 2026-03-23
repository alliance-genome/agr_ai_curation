import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { CurationDraftFieldChange } from '@/features/curation/types'
import {
  autosaveCurationCandidateDraft,
  updateCurationSession,
} from '@/features/curation/services/curationWorkspaceService'
import { useCurationWorkspaceContext } from './CurationWorkspaceContext'
import {
  applyDraftFieldChangesToWorkspace,
  mergeSavedDraftIntoWorkspace,
  replaceWorkspaceSession,
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
  const draftVersionsRef = useRef<Map<string, number | null>>(new Map())
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

      const nextSave = saveSequenceRef.current.then(() =>
        flushPendingCandidate(targetCandidateId, options),
      )
      saveSequenceRef.current = nextSave.catch(() => false)
      return nextSave
    },
    [activeCandidateId, flushPendingCandidate],
  )

  const flushAllPendingChanges = useCallback(
    async (options?: FlushOptions): Promise<boolean> => {
      clearTimer()

      let allSucceeded = true
      for (const candidateId of pendingDraftsRef.current.keys()) {
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
      pendingDraftsRef.current.has(previousActiveCandidateId)
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
    clearWarning: () => setWarning(null),
  }
}
