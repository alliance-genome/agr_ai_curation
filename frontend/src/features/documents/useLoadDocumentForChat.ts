import { useCallback } from 'react'
import { useNavigate } from 'react-router-dom'

import { startDocumentLoad } from '@/features/documents/documentLoadEvents'

export interface ChatDocumentTarget {
  id: string
  filename?: string | null
}

/**
 * Opens chat with a document loaded. HomePage consumes the
 * `loadForChatDocument` route state; every "Load for chat" entry point uses
 * this hook so they behave identically.
 */
export function useLoadDocumentForChat(): (document: ChatDocumentTarget) => void {
  const navigate = useNavigate()

  return useCallback((document: ChatDocumentTarget) => {
    startDocumentLoad({
      documentId: document.id,
      filename: document.filename,
      message: `Loading ${document.filename || 'document'} for chat...`,
    })

    navigate('/', {
      state: {
        loadForChatDocument: {
          id: document.id,
          filename: document.filename,
        },
      },
    })
  }, [navigate])
}
