import { createContext, useContext, useState } from 'react'

export const StudioNavigationContext = createContext<{
  params: URLSearchParams
  navigate: (changes: Record<string, string | null>) => void
} | null>(null)

/** Router-backed in Studio; isolated editors retain ordinary local navigation. */
export function useStudioLocation(key: string, initial: string) {
  const navigation = useContext(StudioNavigationContext)
  const [local, setLocal] = useState(initial)
  const value = navigation ? navigation.params.get(key) || initial : local
  const set = (next: string) => {
    if (navigation) navigation.navigate({ [key]: next || null,
      ...(key === 'workshop' ? { detail: null, stage: null } : {}) })
    else setLocal(next)
  }
  return [value, set] as const
}
