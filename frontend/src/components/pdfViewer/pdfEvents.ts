export interface PDFViewerDocumentChangedDetail {
  documentId: string
  viewerUrl: string
  filename: string
  pageCount: number
}

export type PDFViewerDocumentChangedEvent = CustomEvent<PDFViewerDocumentChangedDetail>

export interface ApplyHighlightsDetail {
  messageId: string
  terms: string[]
  pages?: number[]
}

export type ApplyHighlightsEvent = CustomEvent<ApplyHighlightsDetail>

export interface ClearHighlightsDetail {
  reason: 'new-query' | 'user-action' | 'document-change'
}

export type ClearHighlightsEvent = CustomEvent<ClearHighlightsDetail>

export interface HighlightSettingsDetail {
  color?: string
  opacity?: number
  clearOnNewQuery?: boolean
}

export type HighlightSettingsChangedEvent = CustomEvent<HighlightSettingsDetail>

export interface LocateSnippetDetail {
  matchIndex?: number
  requestId: string
  snippet: string
}

export type LocateSnippetEvent = CustomEvent<LocateSnippetDetail>

export interface SnippetLocalizationMatchSummary {
  crossPage: boolean
  excerpt: string
  index: number
  pages: number[]
  rectCount: number
}

export interface SnippetLocalizationResultDetail {
  durationMs: number
  matchCount: number
  matches: SnippetLocalizationMatchSummary[]
  reason?: string
  renderedPageCount: number
  renderedPages: number[]
  requestId: string
  selectedMatch: SnippetLocalizationMatchSummary | null
  selectedMatchIndex: number | null
  snippet: string
  status: 'empty-query' | 'not-found' | 'not-ready' | 'success'
  totalPageCount: number
}

export type SnippetLocalizationResultEvent = CustomEvent<SnippetLocalizationResultDetail>

export interface ClearSnippetLocalizationDetail {
  reason: 'document-change' | 'new-query' | 'user-action' | 'viewer-refresh'
}

export type ClearSnippetLocalizationEvent = CustomEvent<ClearSnippetLocalizationDetail>

export function dispatchPDFDocumentChanged(
  documentId: string,
  viewerUrl: string,
  filename: string,
  pageCount: number,
): void {
  window.dispatchEvent(
    new CustomEvent<PDFViewerDocumentChangedDetail>('pdf-viewer-document-changed', {
      detail: { documentId, viewerUrl, filename, pageCount },
    }),
  )
}

export function onPDFDocumentChanged(
  handler: (event: PDFViewerDocumentChangedEvent) => void,
): () => void {
  const listener = (event: Event) => handler(event as PDFViewerDocumentChangedEvent)
  window.addEventListener('pdf-viewer-document-changed', listener)
  return () => window.removeEventListener('pdf-viewer-document-changed', listener)
}

export function dispatchApplyHighlights(
  messageId: string,
  terms: string[],
  pages?: number[],
): void {
  window.dispatchEvent(
    new CustomEvent<ApplyHighlightsDetail>('apply-highlights', {
      detail: { messageId, terms, pages },
    }),
  )
}

export function onApplyHighlights(
  handler: (event: ApplyHighlightsEvent) => void,
): () => void {
  const listener = (event: Event) => handler(event as ApplyHighlightsEvent)
  window.addEventListener('apply-highlights', listener)
  return () => window.removeEventListener('apply-highlights', listener)
}

export function dispatchClearHighlights(reason: ClearHighlightsDetail['reason']): void {
  window.dispatchEvent(
    new CustomEvent<ClearHighlightsDetail>('clear-highlights', {
      detail: { reason },
    }),
  )
}

export function onClearHighlights(
  handler: (event: ClearHighlightsEvent) => void,
): () => void {
  const listener = (event: Event) => handler(event as ClearHighlightsEvent)
  window.addEventListener('clear-highlights', listener)
  return () => window.removeEventListener('clear-highlights', listener)
}

export function dispatchHighlightSettingsChanged(detail: HighlightSettingsDetail): void {
  window.dispatchEvent(
    new CustomEvent<HighlightSettingsDetail>('highlight-settings-changed', {
      detail,
    }),
  )
}

export function onHighlightSettingsChanged(
  handler: (event: HighlightSettingsChangedEvent) => void,
): () => void {
  const listener = (event: Event) => handler(event as HighlightSettingsChangedEvent)
  window.addEventListener('highlight-settings-changed', listener)
  return () => window.removeEventListener('highlight-settings-changed', listener)
}

export function dispatchLocateSnippet(requestId: string, snippet: string, matchIndex?: number): void {
  window.dispatchEvent(
    new CustomEvent<LocateSnippetDetail>('locate-snippet', {
      detail: { requestId, snippet, matchIndex },
    }),
  )
}

export function onLocateSnippet(
  handler: (event: LocateSnippetEvent) => void,
): () => void {
  const listener = (event: Event) => handler(event as LocateSnippetEvent)
  window.addEventListener('locate-snippet', listener)
  return () => window.removeEventListener('locate-snippet', listener)
}

export function dispatchSnippetLocalizationResult(detail: SnippetLocalizationResultDetail): void {
  window.dispatchEvent(
    new CustomEvent<SnippetLocalizationResultDetail>('snippet-localization-result', {
      detail,
    }),
  )
}

export function onSnippetLocalizationResult(
  handler: (event: SnippetLocalizationResultEvent) => void,
): () => void {
  const listener = (event: Event) => handler(event as SnippetLocalizationResultEvent)
  window.addEventListener('snippet-localization-result', listener)
  return () => window.removeEventListener('snippet-localization-result', listener)
}

export function dispatchClearSnippetLocalization(reason: ClearSnippetLocalizationDetail['reason']): void {
  window.dispatchEvent(
    new CustomEvent<ClearSnippetLocalizationDetail>('clear-snippet-localization', {
      detail: { reason },
    }),
  )
}

export function onClearSnippetLocalization(
  handler: (event: ClearSnippetLocalizationEvent) => void,
): () => void {
  const listener = (event: Event) => handler(event as ClearSnippetLocalizationEvent)
  window.addEventListener('clear-snippet-localization', listener)
  return () => window.removeEventListener('clear-snippet-localization', listener)
}
