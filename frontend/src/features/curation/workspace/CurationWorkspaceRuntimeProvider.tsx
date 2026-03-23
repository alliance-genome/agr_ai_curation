import { useMemo } from 'react'
import type { ReactNode } from 'react'

import {
  CurationWorkspaceProvider,
  type CurationWorkspaceContextValue,
  useCurationWorkspaceContext,
} from './CurationWorkspaceContext'
import { useAutosave } from './useAutosave'
import { useSessionHydration } from './useSessionHydration'

export function CurationWorkspaceRuntimeProvider({
  children,
  routeCandidateId,
}: {
  children: ReactNode
  routeCandidateId?: string | null
}) {
  const workspaceContext = useCurationWorkspaceContext()
  const autosave = useAutosave()
  const hydration = useSessionHydration({ routeCandidateId })
  const value = useMemo<CurationWorkspaceContextValue>(
    () => ({
      ...workspaceContext,
      autosave,
      hydration,
    }),
    [autosave, hydration, workspaceContext],
  )

  return (
    <CurationWorkspaceProvider value={value}>
      {children}
    </CurationWorkspaceProvider>
  )
}
