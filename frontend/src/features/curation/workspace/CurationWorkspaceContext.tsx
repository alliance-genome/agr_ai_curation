import { createContext, useContext } from 'react'
import type { ReactNode } from 'react'

import type {
  CurationCandidate,
  CurationReviewSession,
  CurationWorkspace,
} from '@/features/curation/types'
import type { UseAutosaveReturn } from './useAutosave'
import type { UseSessionHydrationReturn } from './useSessionHydration'

export interface CurationWorkspaceBaseContextValue {
  workspace: CurationWorkspace
  setWorkspace: (
    nextWorkspace:
      | CurationWorkspace
      | ((currentWorkspace: CurationWorkspace) => CurationWorkspace),
  ) => void
  session: CurationReviewSession
  candidates: CurationCandidate[]
  activeCandidateId: string | null
  activeCandidate: CurationCandidate | null
  setActiveCandidate: (
    candidateId: string | null,
    options?: { replace?: boolean },
  ) => void
}

export interface CurationWorkspaceRuntimeContextValue {
  autosave: UseAutosaveReturn
  hydration: UseSessionHydrationReturn
}

export type CurationWorkspaceContextValue =
  CurationWorkspaceBaseContextValue & Partial<CurationWorkspaceRuntimeContextValue>

const CurationWorkspaceContext = createContext<CurationWorkspaceContextValue | null>(null)

export function CurationWorkspaceProvider({
  children,
  value,
}: {
  children: ReactNode
  value: CurationWorkspaceContextValue
}) {
  return (
    <CurationWorkspaceContext.Provider value={value}>
      {children}
    </CurationWorkspaceContext.Provider>
  )
}

export function useCurationWorkspaceContext(): CurationWorkspaceContextValue {
  const context = useContext(CurationWorkspaceContext)
  if (context === null) {
    throw new Error('useCurationWorkspaceContext must be used within CurationWorkspaceProvider')
  }

  return context
}

export function useCurationWorkspaceAutosave(): UseAutosaveReturn {
  const context = useCurationWorkspaceContext()
  if (!context.autosave) {
    throw new Error(
      'useCurationWorkspaceAutosave must be used within a CurationWorkspaceRuntimeProvider',
    )
  }

  return context.autosave
}

export function useCurationWorkspaceHydration(): UseSessionHydrationReturn {
  const context = useCurationWorkspaceContext()
  if (!context.hydration) {
    throw new Error(
      'useCurationWorkspaceHydration must be used within a CurationWorkspaceRuntimeProvider',
    )
  }

  return context.hydration
}
