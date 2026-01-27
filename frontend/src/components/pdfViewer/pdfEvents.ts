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
