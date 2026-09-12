import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { getProfileMappingOptions, type GenericProfileContract, type ProfileValidatorOptions } from '@/services/genericProfileService'
import { canonicalAuthoringJson } from '../authoringContext'

const Context = createContext<{ capabilities: ProfileValidatorOptions[]; loaded: boolean; error: boolean }>({ capabilities: [], loaded: false, error: false })
export const useProfileValidatorCatalog = () => useContext(Context)

/** Resolve labels for saved attachments against the current caller and exact pins. */
export default function ProfileValidatorCatalog({ value, children }: { value: GenericProfileContract; children: ReactNode }) {
  const key = canonicalAuthoringJson(value)
  const [catalog, setCatalog] = useState<{key: string; capabilities: ProfileValidatorOptions[]; error: boolean} | null>(null)
  useEffect(() => {
    let cancelled = false
    if (!value.validator_mappings?.length) return
    async function load() {
      const capabilities: ProfileValidatorOptions[] = []
      let after: string | undefined
      do {
        const page = await getProfileMappingOptions(value, after)
        if (cancelled) return
        capabilities.push(...page.capabilities)
        after = page.next_cursor ?? undefined
      } while (after)
      setCatalog({ key, capabilities, error: false })
    }
    void load().catch(() => { if (!cancelled) setCatalog({ key, capabilities: [], error: true }) })
    return () => { cancelled = true }
    // Canonical contract captures all inputs and exact pins, avoiding identity churn.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return <Context.Provider value={catalog?.key === key ? { capabilities: catalog.capabilities, loaded: !catalog.error, error: catalog.error } : { capabilities: [], loaded: false, error: false }}>{children}</Context.Provider>
}
