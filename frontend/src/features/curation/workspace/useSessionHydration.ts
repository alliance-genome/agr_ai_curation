import { useEffect, useMemo, useRef, useState } from 'react'

import type { CurationCandidate, CurationWorkspace } from '@/features/curation/types'
import { updateCurationSession } from '@/features/curation/services/curationWorkspaceService'
import { useCurationWorkspaceContext } from './CurationWorkspaceContext'
import {
  findWorkspaceCandidate,
  replaceWorkspaceSession,
  updateWorkspaceActiveCandidate,
} from './workspaceState'

const HYDRATION_METADATA_CONTAINERS = [
  'session_state',
  'sessionState',
  'resume_state',
  'resumeState',
  'hydration',
  'hydration_state',
  'viewer_state',
  'viewerState',
] as const

const SCROLL_POSITION_KEYS = [
  'scroll_position',
  'scrollPosition',
  'viewer_scroll_position',
  'viewerScrollPosition',
] as const

const CURSOR_FIELD_KEYS = [
  'cursor_field_key',
  'cursorFieldKey',
  'active_field_key',
  'activeFieldKey',
] as const

export interface UseSessionHydrationOptions {
  routeCandidateId?: string | null
}

export interface UseSessionHydrationReturn {
  isHydrated: boolean
  restoredCandidateId: string | null
  restoredScrollPosition: number | null
  restoredCursorFieldKey: string | null
  dirtyFieldKeys: string[]
  warning: string | null
}

function readMetadataContainer(metadata: Record<string, unknown>): Array<Record<string, unknown>> {
  const containers = [metadata]

  for (const key of HYDRATION_METADATA_CONTAINERS) {
    const value = metadata[key]
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      containers.push(value as Record<string, unknown>)
    }
  }

  return containers
}

function readMetadataNumber(
  metadata: Record<string, unknown>,
  keys: readonly string[],
): number | null {
  for (const container of readMetadataContainer(metadata)) {
    for (const key of keys) {
      const value = container[key]
      if (typeof value === 'number' && Number.isFinite(value)) {
        return value
      }
    }
  }

  return null
}

function readMetadataString(
  metadata: Record<string, unknown>,
  keys: readonly string[],
): string | null {
  for (const container of readMetadataContainer(metadata)) {
    for (const key of keys) {
      const value = container[key]
      if (typeof value === 'string' && value.length > 0) {
        return value
      }
    }
  }

  return null
}

function readCandidateHydrationState(candidate: CurationCandidate | null) {
  const draftMetadata = candidate?.draft.metadata ?? {}
  const candidateMetadata = candidate?.metadata ?? {}

  const restoredScrollPosition =
    readMetadataNumber(draftMetadata, SCROLL_POSITION_KEYS) ??
    readMetadataNumber(candidateMetadata, SCROLL_POSITION_KEYS)

  const restoredCursorFieldKey =
    readMetadataString(draftMetadata, CURSOR_FIELD_KEYS) ??
    readMetadataString(candidateMetadata, CURSOR_FIELD_KEYS)

  return {
    restoredScrollPosition,
    restoredCursorFieldKey,
    dirtyFieldKeys: candidate?.draft.fields
      .filter((field) => field.dirty)
      .map((field) => field.field_key) ?? [],
  }
}

export function resolveHydratedCandidateId(
  workspace: CurationWorkspace,
  routeCandidateId?: string | null,
): string | null {
  const routeCandidate = findWorkspaceCandidate(workspace, routeCandidateId)
  if (routeCandidate) {
    return routeCandidate.candidate_id
  }

  const workspaceActiveCandidate = findWorkspaceCandidate(
    workspace,
    workspace.active_candidate_id,
  )
  if (workspaceActiveCandidate) {
    return workspaceActiveCandidate.candidate_id
  }

  const sessionActiveCandidate = findWorkspaceCandidate(
    workspace,
    workspace.session.current_candidate_id,
  )
  if (sessionActiveCandidate) {
    return sessionActiveCandidate.candidate_id
  }

  const firstPendingCandidate = workspace.candidates.find(
    (candidate) => candidate.status === 'pending',
  )
  if (firstPendingCandidate) {
    return firstPendingCandidate.candidate_id
  }

  return workspace.candidates[0]?.candidate_id ?? null
}

export function useSessionHydration(
  options: UseSessionHydrationOptions = {},
): UseSessionHydrationReturn {
  const { workspace, activeCandidateId, setActiveCandidate, setWorkspace } =
    useCurationWorkspaceContext()
  const [hydratedSessionId, setHydratedSessionId] = useState<string | null>(null)
  const [warning, setWarning] = useState<string | null>(null)
  const resolvedCandidateId = useMemo(
    () => resolveHydratedCandidateId(workspace, options.routeCandidateId),
    [options.routeCandidateId, workspace],
  )
  const restoredCandidate = useMemo(
    () => findWorkspaceCandidate(workspace, resolvedCandidateId),
    [resolvedCandidateId, workspace],
  )
  const { restoredCursorFieldKey, restoredScrollPosition, dirtyFieldKeys } = useMemo(
    () => readCandidateHydrationState(restoredCandidate),
    [restoredCandidate],
  )
  const lastPersistedCandidateIdRef = useRef<string | null>(
    workspace.session.current_candidate_id ?? workspace.active_candidate_id ?? null,
  )

  useEffect(() => {
    setHydratedSessionId(null)
    lastPersistedCandidateIdRef.current =
      workspace.session.current_candidate_id ?? workspace.active_candidate_id ?? null
    setWarning(null)
  }, [workspace.session.session_id])

  useEffect(() => {
    if (
      hydratedSessionId === workspace.session.session_id &&
      activeCandidateId === resolvedCandidateId
    ) {
      return
    }

    const persistedCandidateId =
      workspace.session.current_candidate_id ?? workspace.active_candidate_id ?? null
    const shouldReplaceRoute =
      options.routeCandidateId !== undefined && options.routeCandidateId !== resolvedCandidateId

    setActiveCandidate(resolvedCandidateId, { replace: shouldReplaceRoute })
    setHydratedSessionId(workspace.session.session_id)

    if (persistedCandidateId === resolvedCandidateId) {
      lastPersistedCandidateIdRef.current = persistedCandidateId
    }
  }, [
    activeCandidateId,
    hydratedSessionId,
    options.routeCandidateId,
    resolvedCandidateId,
    setActiveCandidate,
    workspace.active_candidate_id,
    workspace.session.current_candidate_id,
    workspace.session.session_id,
  ])

  useEffect(() => {
    if (hydratedSessionId !== workspace.session.session_id) {
      return
    }

    if (activeCandidateId === lastPersistedCandidateIdRef.current) {
      return
    }

    let cancelled = false
    const nextCandidateId = activeCandidateId

    setWorkspace((currentWorkspace) =>
      updateWorkspaceActiveCandidate(currentWorkspace, nextCandidateId),
    )

    void updateCurationSession({
      session_id: workspace.session.session_id,
      current_candidate_id: nextCandidateId,
    })
      .then((response) => {
        if (cancelled) {
          return
        }

        lastPersistedCandidateIdRef.current =
          response.session.current_candidate_id ?? nextCandidateId
        setWorkspace((currentWorkspace) =>
          updateWorkspaceActiveCandidate(
            replaceWorkspaceSession(currentWorkspace, response.session),
            nextCandidateId,
          ),
        )
      })
      .catch(() => {
        if (cancelled) {
          return
        }

        setWarning('Unable to persist the current candidate selection for resume.')
      })

    return () => {
      cancelled = true
    }
  }, [
    activeCandidateId,
    hydratedSessionId,
    setWorkspace,
    workspace.session.session_id,
  ])

  return {
    isHydrated: hydratedSessionId === workspace.session.session_id,
    restoredCandidateId: resolvedCandidateId,
    restoredScrollPosition,
    restoredCursorFieldKey,
    dirtyFieldKeys,
    warning,
  }
}
