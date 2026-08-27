import { safeSetJson } from '@/lib/browserStorage'
import type { ViewerDocument, ViewerSession, ViewerState } from './pdfViewerTypes'

export const uniqueTerms = (terms: string[]): string[] => {
  const seen = new Set<string>()
  return terms.filter((term) => {
    const normalized = term.trim()
    if (!normalized) return false
    const key = normalized.toLowerCase()
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export const persistSession = (storageKey: string | null, doc: ViewerDocument, state: ViewerState) => {
  if (!storageKey) {
    return
  }

  const session: ViewerSession = {
    ...doc,
    ...state,
    lastInteraction: new Date().toISOString(),
  }
  safeSetJson(() => window.localStorage, storageKey, session, {
    owner: 'pdf-viewer',
    workflowCritical: true,
  })
}
