import { safeRemoveItem, safeSetItem } from '@/lib/browserStorage'

export const DOCUMENT_LOADING_STORAGE_KEY = 'document-loading'
export const DOCUMENT_LOAD_START_EVENT = 'document-load-start'
export const DOCUMENT_LOAD_COMPLETE_EVENT = 'document-load-complete'
export const DOCUMENT_LOAD_ERROR_EVENT = 'document-load-error'

export interface DocumentLoadEventDetail {
  documentId?: string
  filename?: string | null
  message: string
}

export function startDocumentLoad(detail: DocumentLoadEventDetail): void {
  safeSetItem(() => window.sessionStorage, DOCUMENT_LOADING_STORAGE_KEY, 'true', {
    owner: 'workflow',
    workflowCritical: true,
  })
  window.dispatchEvent(new CustomEvent(DOCUMENT_LOAD_START_EVENT, { detail }))
}

export function completeDocumentLoad(detail: DocumentLoadEventDetail): void {
  safeRemoveItem(() => window.sessionStorage, DOCUMENT_LOADING_STORAGE_KEY, {
    owner: 'workflow',
    workflowCritical: true,
  })
  window.dispatchEvent(new CustomEvent(DOCUMENT_LOAD_COMPLETE_EVENT, { detail }))
}

export function failDocumentLoad(detail: DocumentLoadEventDetail): void {
  safeRemoveItem(() => window.sessionStorage, DOCUMENT_LOADING_STORAGE_KEY, {
    owner: 'workflow',
    workflowCritical: true,
  })
  window.dispatchEvent(new CustomEvent(DOCUMENT_LOAD_ERROR_EVENT, { detail }))
}
