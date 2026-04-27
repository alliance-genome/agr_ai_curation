import type { HighlightSettings } from '@/components/pdfViewer/highlightSettings'
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
  try {
    localStorage.setItem(storageKey, JSON.stringify(session))
  } catch (error) {
    console.warn('Unable to persist viewer session', error)
  }
}

export const ensureMarkInjected = (iframeDoc: Document, settings: HighlightSettings) => {
  if (!iframeDoc.getElementById('mark-js-script')) {
    const script = iframeDoc.createElement('script')
    script.id = 'mark-js-script'
    script.src = 'https://cdn.jsdelivr.net/npm/mark.js@8.11.1/dist/mark.min.js'
    iframeDoc.head.appendChild(script)
  }

  let styleEl = iframeDoc.getElementById('pdf-highlight-styles') as HTMLStyleElement | null
  if (!styleEl) {
    styleEl = iframeDoc.createElement('style')
    styleEl.id = 'pdf-highlight-styles'
    iframeDoc.head.appendChild(styleEl)
  }

  styleEl.textContent = `
    mark.pdf-highlight {
      background-color: ${settings.highlightColor};
      color: inherit !important;
      padding: 0 !important;
      margin: 0 !important;
      border-radius: 2px;
      opacity: ${settings.highlightOpacity};
      mix-blend-mode: multiply;
    }
  `
}

export const getTextLayers = (iframeDoc: Document, specificLayer?: HTMLElement): HTMLElement[] => {
  if (specificLayer) {
    return [specificLayer]
  }
  return Array.from(iframeDoc.querySelectorAll<HTMLElement>('.textLayer'))
}
