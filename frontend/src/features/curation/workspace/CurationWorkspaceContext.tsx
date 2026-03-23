import { createContext, useContext } from 'react'
import type { ReactNode } from 'react'

import type {
  CurationCandidate,
  CurationReviewSession,
  CurationWorkspace,
} from '@/features/curation/types'

export interface CurationWorkspaceContextValue {
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
